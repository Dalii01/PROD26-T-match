from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.match import Match
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
        self.executed = []

    def add(self, obj):
        self.added.append(obj)

    def begin(self):
        return _FakeBegin()

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)

    async def get(self, model, key, **_kwargs):
        return self._users.get((model, key))


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


def test_blocks_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post("/blocks", json={"target_id": 2})
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_blocks_requires_admin(client_with_session):
    actor = _build_user(1, is_admin=False)
    session = _FakeSession(results=[_FakeResult(scalar=actor)])
    with client_with_session(session) as client:
        response = client.post(
            "/blocks", json={"target_id": 2}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "ADMIN_REQUIRED"


def test_block_self_invalid_target(client_with_session):
    actor = _build_user(1, is_admin=True)
    session = _FakeSession(results=[_FakeResult(scalar=actor)])
    with client_with_session(session) as client:
        response = client.post(
            "/blocks", json={"target_id": 1}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "INVALID_TARGET"


def test_block_user_not_found(client_with_session):
    actor = _build_user(1, is_admin=True)
    session = _FakeSession(results=[_FakeResult(scalar=actor)], users={(User, 2): None})
    with client_with_session(session) as client:
        response = client.post(
            "/blocks", json={"target_id": 2}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_block_user_success_closes_matches(client_with_session):
    actor = _build_user(1, is_admin=True)
    target = _build_user(2, is_admin=False, is_active=True)
    match = Match(
        id=10,
        user_a_id=2,
        user_b_id=3,
        status="active",
        created_at=datetime(2026, 3, 15, tzinfo=timezone.utc),
    )
    session = _FakeSession(
        results=[
            _FakeResult(scalar=actor),  # require_admin
            _FakeResult(),  # insert block
            _FakeResult(scalars=[match]),  # match select
            _FakeResult(),  # update conversations
        ],
        users={(User, 2): target},
    )
    with client_with_session(session) as client:
        response = client.post(
            "/blocks", json={"target_id": 2}, headers={"X-User-ID": "1"}
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["blocked_id"] == 2
    assert target.is_active is False
    assert match.status == "closed"
    assert match.closed_at is not None
    assert any(isinstance(item, AuditLog) for item in session.added)
