from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.user import User


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeResult:
    def __init__(self, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _FakeScalars(self._scalars or [])


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)


@pytest.fixture()
def client_with_session():
    fake_session = None

    async def _override_get_session():
        yield fake_session

    def _make_client(session: _FakeSession):
        nonlocal fake_session
        fake_session = session
        app.dependency_overrides[get_session] = _override_get_session
        return TestClient(app)

    yield _make_client
    app.dependency_overrides.clear()


def test_audit_log_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/audit-log")
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_audit_log_list_success(client_with_session):
    ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    admin_user = User(
        id=1,
        external_party_rk=10_000_001,
        first_name="Admin",
        last_name="User",
        nickname="admin1",
        bio=None,
        gender=None,
        birth_date=None,
        is_active=True,
        is_admin=True,
    )
    rows = [
        AuditLog(
            id=10,
            event_type="like",
            actor_id=1,
            target_id=2,
            metadata_={"source": "test"},
            created_at=ts,
        ),
        AuditLog(
            id=11,
            event_type="match",
            actor_id=1,
            target_id=2,
            metadata_=None,
            created_at=ts,
        ),
    ]
    session = _FakeSession(
        results=[_FakeResult(scalar=admin_user), _FakeResult(scalars=rows)]
    )
    with client_with_session(session) as client:
        response = client.get("/audit-log", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert len(payload["data"]) == 2
    assert payload["data"][0]["event_type"] == "like"
    assert payload["data"][0]["metadata"] == {"source": "test"}
    assert payload["data"][1]["event_type"] == "match"
    assert payload["data"][1]["metadata"] == {}
    assert payload["data"][0]["created_at"] == ts.isoformat()


def test_audit_log_requires_admin(client_with_session):
    user = User(
        id=2,
        external_party_rk=10_000_002,
        first_name="User",
        last_name="NoAdmin",
        nickname="user2",
        bio=None,
        gender=None,
        birth_date=None,
        is_active=True,
        is_admin=False,
    )
    session = _FakeSession(results=[_FakeResult(scalar=user)])
    with client_with_session(session) as client:
        response = client.get("/audit-log", headers={"X-User-ID": "2"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "ADMIN_REQUIRED"
