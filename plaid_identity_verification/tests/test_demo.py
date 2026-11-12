from __future__ import annotations

import html
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["PIV_DATA_DIR"] = tempfile.mkdtemp(prefix="piv-test-")
os.environ["PIV_PUBLIC_BASE"] = "https://testserver"
os.environ["PIV_BASE_PATH"] = ""
os.environ["PIV_COOKIE_SECURE"] = "1"
os.environ["PIV_PROVIDER"] = "demo"
os.environ["PIV_EMAIL_BACKEND"] = "console"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import app as application  # noqa: E402
import db  # noqa: E402
from security import password_hash  # noqa: E402


def value_for(html_text: str, element_id: str) -> str:
    match = re.search(
        rf'id="{re.escape(element_id)}"[^>]*value="([^"]+)"',
        html_text,
    )
    assert match, f"missing #{element_id}"
    return html.unescape(match.group(1))


def test_organization_employee_links_and_verification_flow():
    with TestClient(application.app, base_url="https://testserver") as admin:
        response = admin.post(
            "/organizations",
            data={
                "organization_name": "Demo Credit Union",
                "name": "Admin Example",
                "email": "admin@example.invalid",
                "password": "Password1234",
            },
        )
        assert response.status_code == 200
        admin_link = value_for(response.text, "verification-link")
        invite_link = value_for(response.text, "invite-link")
        assert "/v/" in admin_link
        assert "/join?invite=" in invite_link

        invite = parse_qs(urlparse(invite_link).query)["invite"][0]

        with TestClient(
            application.app,
            base_url="https://testserver",
        ) as employee:
            response = employee.post(
                "/join",
                data={
                    "invite": invite,
                    "name": "Employee Example",
                    "email": "employee@example.invalid",
                    "password": "Password5678",
                },
            )
            assert response.status_code == 200
            employee_link = value_for(response.text, "verification-link")
            assert employee_link != admin_link

            link_path = urlparse(employee_link).path
            with TestClient(
                application.app,
                base_url="https://testserver",
            ) as subject:
                response = subject.post(
                    link_path,
                    data={
                        "name": "Sample Customer",
                        "email": "customer@example.invalid",
                        "consent": "yes",
                        "website": "",
                    },
                    follow_redirects=False,
                )
                assert response.status_code == 303
                simulator_url = response.headers["location"]
                assert "/demo/" in simulator_url

                parsed = urlparse(simulator_url)
                completion_token = parse_qs(parsed.query)["token"][0]
                verification_id = parsed.path.split("/")[-1]
                response = subject.post(
                    f"/demo/{verification_id}/complete",
                    data={
                        "token": completion_token,
                        "scenario": "success",
                    },
                )
                assert response.status_code == 200
                assert "Submission complete" in response.text

            response = employee.get("/dashboard")
            assert response.status_code == 200
            assert "Sample Customer" in response.text
            assert "Passed" in response.text

            verification = db.get_verification(verification_id)
            assert verification
            assert verification["status"] == "success"
            assert verification["result"]["trust_index_score"] == 92
            assert verification["employee_notified_at"]


def test_pages_consent_and_email_carry_the_prooflink_name():
    with TestClient(application.app, base_url="https://testserver") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Proof<strong>Link</strong>" in response.text
        assert "· ProofLink</title>" in response.text
        assert "Lenni Verify" not in response.text

        _, owner, _ = db.create_organization(
            organization_name="Name Check Bank",
            member_name="Name Check Officer",
            email="name-check@example.invalid",
            encoded_password=password_hash("Password3333"),
            request_ip="127.0.0.1",
        )
        link_path = f"/v/{owner['link_slug']}"
        response = client.get(link_path)
        assert response.status_code == 200
        assert "using ProofLink and its" in response.text
        assert f"Consent notice version {application.CONSENT_VERSION}" in response.text
        assert "Lenni Verify" not in response.text

        response = client.post(
            link_path,
            data={"name": "Name Check Customer", "consent": "yes", "website": ""},
            follow_redirects=False,
        )
        assert response.status_code == 303
        parsed = urlparse(response.headers["location"])
        verification_id = parsed.path.split("/")[-1]
        verification = db.get_verification(verification_id)
        assert verification["consent_version"] == application.CONSENT_VERSION

        response = client.post(
            f"/demo/{verification_id}/complete",
            data={"token": parse_qs(parsed.query)["token"][0], "scenario": "success"},
        )
        assert response.status_code == 200
        outbox = Path(os.environ["PIV_DATA_DIR"]) / "outbox"
        sent = [p.read_text(encoding="utf-8") for p in outbox.glob("*-name-check_at_example.invalid.txt")]
        assert sent
        assert all("ProofLink" in s and "Lenni Verify" not in s for s in sent)


def test_invalid_invite_is_rejected():
    with TestClient(application.app, base_url="https://testserver") as client:
        response = client.post(
            "/join",
            data={
                "invite": "not-a-real-invite",
                "name": "Unknown Employee",
                "email": "unknown@example.invalid",
                "password": "Password9999",
            },
        )
        assert response.status_code == 400
        assert "invalid or closed" in response.text


def test_result_is_not_visible_across_organizations():
    with TestClient(application.app, base_url="https://testserver") as outsider:
        _, owner, _ = db.create_organization(
            organization_name="Owner Organization",
            member_name="Owner Admin",
            email="owner-isolation@example.invalid",
            encoded_password=password_hash("Password1111"),
            request_ip="127.0.0.1",
        )
        verification, _ = db.create_verification(
            member=owner,
            provider="demo",
            subject_name="Private Subject",
            subject_email=None,
            consent_version="test",
            request_ip="127.0.0.1",
        )
        db.complete_verification(
            verification["id"],
            status="success",
            result={"status": "success"},
        )

        _, other_admin, _ = db.create_organization(
            organization_name="Other Organization",
            member_name="Other Admin",
            email="other-isolation@example.invalid",
            encoded_password=password_hash("Password2222"),
            request_ip="127.0.0.1",
        )
        raw_session, _ = db.create_auth_session(
            other_admin["id"],
            ttl_seconds=3600,
        )
        outsider.cookies.set(
            application.COOKIE_NAME,
            raw_session,
            path="/",
        )
        response = outsider.get(f"/results/{verification['id']}")
        assert response.status_code == 404
        assert "Private Subject" not in response.text
