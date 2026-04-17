import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.user import User


class _FakeResult:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, results, users=None):
        self._results = list(results)
        self._users = users or {}

    def begin(self):
        return _FakeBegin()

    async def execute(self, _stmt):
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


def _build_user(user_id: int, is_admin: bool, is_active: bool = True) -> User:
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


def test_grant_admin_bootstrap(client_with_session):
    actor = _build_user(1, is_admin=False)
    target = _build_user(2, is_admin=False)
    session = _FakeSession(
        results=[
            _FakeResult(scalar=actor),  # require_active_user
            _FakeResult(scalar=None),  # admin_exists
        ],
        users={(User, 2): target},
    )
    with client_with_session(session) as client:
        response = client.put("/users/admin/2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["is_admin"] is True
    assert target.is_admin is True


def test_grant_admin_requires_admin_when_exists(client_with_session):
    actor = _build_user(1, is_admin=False)
    session = _FakeSession(
        results=[
            _FakeResult(scalar=actor),  # require_active_user
            _FakeResult(scalar=1),  # admin_exists -> requires admin
            _FakeResult(scalar=actor),  # require_admin
        ],
    )
    with client_with_session(session) as client:
        response = client.put("/users/admin/2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "ADMIN_REQUIRED"
