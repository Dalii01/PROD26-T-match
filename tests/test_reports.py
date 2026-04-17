from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.report import Report
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


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, results, users=None):
        self._results = list(results)
        self._users = users or {}
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def begin(self):
        return _FakeBegin()

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)

    async def get(self, model, key, **_kwargs):
        return self._users.get((model, key))

    async def flush(self):
        for obj in self.added:
            if isinstance(obj, Report) and obj.id is None:
                obj.id = 1


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


def _build_user(user_id: int, is_admin: bool = False, is_active: bool = True) -> User:
    return User(
        id=user_id,
        external_party_rk=10_000_000 + user_id,
        first_name="User",
        last_name="Test",
        nickname=f"user{user_id}",
        bio=None,
        gender=None,
        birth_date=None,
        is_active=is_active,
        is_admin=is_admin,
    )


def test_create_report_invalid_reason(client_with_session):
    reporter = _build_user(1, is_active=True)
    session = _FakeSession(results=[_FakeResult(scalar=reporter)])
    with client_with_session(session) as client:
        response = client.post(
            "/reports",
            json={"reported_id": 2, "reason": "unknown"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "INVALID_REASON"


def test_create_report_self_invalid_target(client_with_session):
    reporter = _build_user(1, is_active=True)
    session = _FakeSession(results=[_FakeResult(scalar=reporter)])
    with client_with_session(session) as client:
        response = client.post(
            "/reports",
            json={"reported_id": 1, "reason": "spam"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "INVALID_TARGET"


def test_create_report_user_not_found(client_with_session):
    reporter = _build_user(1, is_active=True)
    session = _FakeSession(
        results=[_FakeResult(scalar=reporter)],
        users={(User, 2): None},
    )
    with client_with_session(session) as client:
        response = client.post(
            "/reports",
            json={"reported_id": 2, "reason": "spam"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_create_report_success(client_with_session):
    reporter = _build_user(1, is_active=True)
    reported = _build_user(2, is_active=True)
    session = _FakeSession(
        results=[_FakeResult(scalar=reporter)],
        users={(User, 2): reported},
    )
    with client_with_session(session) as client:
        response = client.post(
            "/reports",
            json={"reported_id": 2, "reason": "spam", "comment": "links"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["reported_id"] == 2
    assert payload["data"]["reason"] == "spam"
    assert any(isinstance(item, AuditLog) for item in session.added)


def test_list_reports_requires_admin(client_with_session):
    user = _build_user(1, is_admin=False)
    session = _FakeSession(results=[_FakeResult(scalar=user)])
    with client_with_session(session) as client:
        response = client.get("/reports", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "ADMIN_REQUIRED"


def test_list_reports_success(client_with_session):
    admin = _build_user(1, is_admin=True)
    ts = datetime(2026, 3, 16, tzinfo=timezone.utc)
    reports = [
        Report(
            id=10,
            reporter_id=1,
            reported_id=2,
            reason="spam",
            comment="links",
            created_at=ts,
        )
    ]
    session = _FakeSession(
        results=[_FakeResult(scalar=admin), _FakeResult(scalars=reports)]
    )
    with client_with_session(session) as client:
        response = client.get("/reports", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["id"] == 10
    assert payload["data"][0]["reason"] == "spam"


def test_reject_report_not_found(client_with_session):
    admin = _build_user(1, is_admin=True)
    session = _FakeSession(
        results=[_FakeResult(scalar=admin), _FakeResult(scalar=None)]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/reports/reject", json={"report_id": 10}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "REPORT_NOT_FOUND"


def test_reject_report_success(client_with_session):
    admin = _build_user(1, is_admin=True)
    session = _FakeSession(results=[_FakeResult(scalar=admin), _FakeResult(scalar=10)])
    with client_with_session(session) as client:
        response = client.post(
            "/reports/reject", json={"report_id": 10}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["report_id"] == 10


def test_reject_report_alias(client_with_session):
    admin = _build_user(1, is_admin=True)
    session = _FakeSession(results=[_FakeResult(scalar=admin), _FakeResult(scalar=10)])
    with client_with_session(session) as client:
        response = client.post(
            "/reject", json={"report_id": 10}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["report_id"] == 10
