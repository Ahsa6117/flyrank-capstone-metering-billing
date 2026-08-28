"""Test fixtures.

Each test module gets a fresh file-backed SQLite database. File-backed rather
than ``:memory:`` on purpose: the concurrency test opens real connections from
several threads, and an in-memory database would give each thread its own empty
copy -- which would make the race test silently pass without testing anything.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

# Point the app at a throwaway database BEFORE app.db is imported, since the
# engine is created at import time from these settings.
_TMP_DIR = Path(tempfile.gettempdir()) / f"billing_tests_{uuid.uuid4().hex}"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["INTERNAL_JOB_TOKEN"] = "test-internal-token"
os.environ.setdefault("STRIPE_SECRET_KEY", "")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, run_migrations  # noqa: E402
from app.models import Subscription, Tenant  # noqa: E402
from app.repositories.tenants import hash_api_key  # noqa: E402

def _unique_key(prefix: str) -> str:
    """API keys are globally unique in the schema, so each fixture mints its own.

    Sharing one constant across tests would collide on the unique index and make
    failures look like application bugs rather than fixture reuse.
    """
    return f"{prefix}_{uuid.uuid4().hex}"


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    run_migrations()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _make_tenant(
    session, tenant_id: str, api_key: str, plan: str, sub_status: str | None
) -> Tenant:
    tenant = Tenant(
        id=tenant_id, name=tenant_id, api_key_hash=hash_api_key(api_key), plan_code=plan
    )
    session.add(tenant)
    if sub_status:
        session.add(
            Subscription(
                id=f"sub_{uuid.uuid4().hex}",
                tenant_id=tenant_id,
                plan_code=plan,
                status=sub_status,
            )
        )
    session.commit()
    return tenant


@pytest.fixture
def free_tenant(session) -> Tenant:
    """A Free-plan tenant with a unique id, so tests never share usage."""
    return _make_tenant(
        session, f"tnt_free_{uuid.uuid4().hex[:8]}", _unique_key("free"), "free", None
    )


@pytest.fixture
def pro_tenant(session) -> Tenant:
    return _make_tenant(
        session, f"tnt_pro_{uuid.uuid4().hex[:8]}", _unique_key("pro"), "pro", "active"
    )


@pytest.fixture
def past_due_tenant(session) -> Tenant:
    return _make_tenant(
        session,
        f"tnt_pastdue_{uuid.uuid4().hex[:8]}",
        _unique_key("pastdue"),
        "pro",
        "past_due",
    )


@pytest.fixture
def api_tenant(session):
    """A tenant reachable over HTTP, returned with its API key."""
    key = _unique_key("http")
    tenant = _make_tenant(session, f"tnt_http_{uuid.uuid4().hex[:8]}", key, "free", None)
    return tenant, key


@pytest.fixture
def client():
    """TestClient without the lifespan, so no scheduler thread runs in tests."""
    from app.main import app

    with TestClient(app) as c:
        yield c
