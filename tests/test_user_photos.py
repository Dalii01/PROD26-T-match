import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.user import User, UserPhoto


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
        self.deleted = []
        self.executed = []

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        return None

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


def _build_user(user_id: int, is_active: bool = True) -> User:
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
    )


def test_add_photo_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post("/users/me/photos", json={"url": "https://x"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_ID_REQUIRED"


def test_add_photo_invalid_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post(
            "/users/me/photos",
            json={"url": "https://x"},
            headers={"X-User-ID": "oops"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_ID_INVALID"


def test_add_photo_limit_reached(client_with_session):
    user = _build_user(1)
    photos = [
        UserPhoto(id=idx, user_id=1, url=f"https://p{idx}", is_primary=idx == 1)
        for idx in range(1, 6)
    ]
    session = _FakeSession(
        results=[_FakeResult(scalar=user), _FakeResult(scalars=photos)],
        users={(User, 1): user},
    )
    with client_with_session(session) as client:
        response = client.post(
            "/users/me/photos",
            json={"url": "https://new"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "PHOTO_LIMIT_REACHED"


def test_add_photo_success_sets_primary(client_with_session):
    user = _build_user(1)
    session = _FakeSession(
        results=[_FakeResult(scalar=user), _FakeResult(scalars=[])],
        users={(User, 1): user},
    )
    with client_with_session(session) as client:
        response = client.post(
            "/users/me/photos",
            json={"url": "https://new"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["is_primary"] is True
    assert any(
        isinstance(item, UserPhoto) and item.url == "https://new" and item.is_primary
        for item in session.added
    )
    assert any(isinstance(item, AuditLog) for item in session.added)


def test_delete_photo_not_found(client_with_session):
    user = _build_user(1)
    session = _FakeSession(
        results=[_FakeResult(scalar=user), _FakeResult(scalar=None)],
        users={(User, 1): user},
    )
    with client_with_session(session) as client:
        response = client.delete(
            "/users/me/photos?url=https://missing",
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "PHOTO_NOT_FOUND"


def test_delete_photo_primary_promotes_next(client_with_session):
    user = _build_user(1)
    photo = UserPhoto(id=1, user_id=1, url="https://a", is_primary=True)
    next_photo = UserPhoto(id=2, user_id=1, url="https://b", is_primary=False)
    session = _FakeSession(
        results=[
            _FakeResult(scalar=user),
            _FakeResult(scalar=photo),
            _FakeResult(scalar=next_photo),
        ],
        users={(User, 1): user},
    )
    with client_with_session(session) as client:
        response = client.delete(
            "/users/me/photos?url=https://a",
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["url"] == "https://a"
    assert next_photo.is_primary is True
    assert photo in session.deleted
    assert any(isinstance(item, AuditLog) for item in session.added)
