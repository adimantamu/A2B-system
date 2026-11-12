"""Test setup.

The data directory is redirected before anything imports `a2b.config`, which
resolves paths at import time. Every test then gets a freshly created database,
so ordering between tests can never matter.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="a2b-tests-"))
os.environ["A2B_DATA_DIR"] = str(_TMP)
os.environ["A2B_DB_PATH"] = str(_TMP / "a2b.db")
os.environ["A2B_PACKAGE_DIR"] = str(_TMP / "packages")
# Pinned, not inferred. `mailer.backend()` would pick SES off an AWS_REGION that
# happens to be in a developer's shell, and the suite exercises password reset —
# so the default would be to send real email to made-up addresses from anyone's
# laptop. The outbox writes into the temp directory above instead.
os.environ["A2B_EMAIL_BACKEND"] = "outbox"


@pytest.fixture(autouse=True)
def fresh_db():
    from a2b.config import DB_PATH
    from a2b.db import init_db

    for suffix in ("", "-wal", "-shm"):
        p = DB_PATH.with_name(DB_PATH.name + suffix)
        if p.exists():
            p.unlink()
    init_db()
    yield


@pytest.fixture
def vendor_id():
    from a2b import skills

    return skills.create_vendor(
        name="Bluebonnet Community Bank",
        industry_key="banking",
        website="bluebonnetbank.com",
        description="Community bank in Boerne, TX.",
    )


@pytest.fixture
def published_skill(vendor_id):
    """A publishable skill with a working destination."""
    from a2b import skills

    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(sid)
    return sid


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
