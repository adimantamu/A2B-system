"""robots.txt, from the fetching side.

The interesting cases are not "does RobotFileParser work" — it does — but the
decisions this module makes around it: which agent token a site writes to
address us, what an unreachable file means, and whether a refusal is reported as
a refusal or buried as a server error.
"""
import pytest

from a2b import robots


@pytest.fixture(autouse=True)
def _clear_cache():
    robots.reset()
    yield
    robots.reset()


def _serves(body):
    """A robots.txt fetcher that answers every origin with the same file."""
    return lambda url: body


def test_a_disallowed_path_is_refused():
    rules = _serves("User-agent: *\nDisallow: /private\n")
    assert robots.allowed("https://bank.test/personal", fetch_text=rules)
    assert not robots.allowed("https://bank.test/private/rates", fetch_text=rules)


def test_a_site_can_address_us_by_name_without_knowing_our_version():
    """Our User-Agent carries `/0.1`. A site writes `LenniA2B` and means us."""
    rules = _serves("User-agent: LenniA2B\nDisallow: /\n")
    assert not robots.allowed("https://bank.test/", fetch_text=rules)


def test_a_rule_for_someone_else_does_not_apply_to_us():
    rules = _serves("User-agent: SomeOtherBot\nDisallow: /\n")
    assert robots.allowed("https://bank.test/business/lending", fetch_text=rules)


def test_a_missing_robots_txt_is_permission():
    """404 is permission under RFC 9309, and a bank without the file is the
    common case rather than the interesting one."""
    assert robots.allowed("https://bank.test/loans", fetch_text=lambda url: None)


def test_the_file_is_fetched_once_per_host():
    """Twelve pages on one host must not cost twelve robots.txt requests."""
    calls = []

    def counting(url):
        calls.append(url)
        return "User-agent: *\nDisallow: /admin\n"

    for path in ("/", "/business", "/business/lending", "/personal"):
        robots.allowed("https://bank.test" + path, fetch_text=counting)
    assert calls == ["https://bank.test/robots.txt"]


def test_each_host_is_asked_separately():
    calls = []

    def counting(url):
        calls.append(url)
        return "User-agent: *\n"

    robots.allowed("https://one.test/a", fetch_text=counting)
    robots.allowed("https://two.test/a", fetch_text=counting)
    assert calls == ["https://one.test/robots.txt", "https://two.test/robots.txt"]


def test_a_bare_hostname_still_resolves_to_an_origin():
    """The scanner accepts `bank.com` typed without a scheme."""
    assert robots.origin("bank.test") == "https://bank.test"
    assert robots.origin("https://bank.test/business/lending") == "https://bank.test"


def test_a_refusal_reaches_the_crawler_rather_than_being_swallowed(monkeypatch):
    """`prospect.crawl` must raise on a disallowed landing page, not return an
    empty scan that looks like a bank with no products."""
    from a2b import prospect

    monkeypatch.setattr(robots, "allowed", lambda url, **kw: False)
    with pytest.raises(PermissionError):
        prospect.crawl("https://bank.test")


def test_a_refusal_is_a_refusal_and_not_a_bad_gateway(monkeypatch):
    """403 says they answered. 502 would say their server broke, which is a
    different thing to put in front of someone."""
    from fastapi.testclient import TestClient

    from a2b import prospect
    from a2b.api.main import app

    def refuse(*args, **kwargs):
        raise PermissionError("https://bank.test is disallowed by its robots.txt")

    monkeypatch.setattr(prospect, "preview", refuse)
    client = TestClient(app)
    resp = client.post("/api/ingest/scan", json={"url": "https://bank.test"})
    assert resp.status_code == 403
    assert "robots.txt" in resp.json()["detail"]
