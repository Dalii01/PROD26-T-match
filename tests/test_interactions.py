from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.match import Match
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
    def __init__(self, results):
        self._results = list(results)
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def begin(self):
        return _FakeBegin()

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)


def _build_user(user_id: int, first_name: str, last_name: str, photo_url: str) -> User:
    user = User(
        id=user_id,
        external_party_rk=10_000_000 + user_id,
        first_name=first_name,
        last_name=last_name,
        nickname=f"user{user_id}",
        bio=None,
        gender=None,
        birth_date=date(1994, 5, 5),
        is_active=True,
    )
    user.photos = [
        UserPhoto(
            id=user_id,
            user_id=user_id,
            url=photo_url,
            is_primary=True,
        )
    ]
    return user


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


def test_interactions_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post("/interactions", json={"target_id": 2, "action": "like"})
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_interactions_invalid_target(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 1, "action": "like"},
            headers={"X-User-ID": "1"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "INVALID_TARGET"


def test_interactions_invalid_action(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 2, "action": "superlike"},
            headers={"X-User-ID": "1"},
        )
    assert response.status_code == 422


def test_interactions_target_not_found(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalars=[1])])
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 999, "action": "like"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_like_without_reciprocal(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
            _FakeResult(scalar=None),  # reciprocal like
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 2, "action": "like"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["is_match"] is False
    assert payload["data"]["match_created"] is False
    assert payload["data"]["match_id"] is None
    assert sum(isinstance(item, AuditLog) for item in session.added) == 1


def test_like_with_new_match(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
            _FakeResult(scalar=10),  # reciprocal like exists
            _FakeResult(scalar=55),  # insert match returns id
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 2, "action": "like"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["is_match"] is True
    assert payload["data"]["match_created"] is True
    assert payload["data"]["match_id"] == 55
    assert sum(isinstance(item, AuditLog) for item in session.added) == 2


def test_like_with_existing_match(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
            _FakeResult(scalar=10),  # reciprocal like exists
            _FakeResult(scalar=None),  # insert match returns none
            _FakeResult(scalar=Match(id=7, user_a_id=1, user_b_id=2, status="active")),
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 2, "action": "like"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["is_match"] is True
    assert payload["data"]["match_created"] is False
    assert payload["data"]["match_id"] == 7


def test_skip_action(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 2, "action": "skip"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["action"] == "skip"
    assert payload["data"]["is_match"] is False


def test_interactions_daily_limit_reached(client_with_session, monkeypatch):
    async def _deny(*_args, **_kwargs):
        return False, 20

    monkeypatch.setattr("app.api.routers.interactions.check_and_incr_daily", _deny)

    session = _FakeSession(results=[_FakeResult(scalars=[1, 2])])
    with client_with_session(session) as client:
        response = client.post(
            "/interactions",
            json={"target_id": 2, "action": "like"},
            headers={"X-User-ID": "1"},
        )
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "DAILY_LIMIT_REACHED"


def test_list_liked_by_user_not_found(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalar=None)])
    with client_with_session(session) as client:
        response = client.get("/interactions/liked-by/999", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_list_liked_by_success(client_with_session):
    users = [
        _build_user(3, "Anna", "Lee", "https://example.com/a.jpg"),
        _build_user(4, "Boris", "Kim", "https://example.com/b.jpg"),
    ]
    session = _FakeSession(
        results=[
            _FakeResult(scalar=3),  # ensure user exists
            _FakeResult(scalars=users),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/interactions/liked-by/3", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert [item["id"] for item in payload["data"]] == [3, 4]
    assert payload["data"][0]["name"] == "Anna Lee"
    assert payload["data"][0]["photo_url"] == "https://example.com/a.jpg"


def test_list_liked_success(client_with_session):
    users = [
        _build_user(8, "Mila", "Fox", "https://example.com/m.jpg"),
    ]
    session = _FakeSession(
        results=[
            _FakeResult(scalar=8),  # ensure user exists
            _FakeResult(scalars=users),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/interactions/liked/8", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert len(payload["data"]) == 1
    assert payload["data"][0]["id"] == 8
    assert payload["data"][0]["name"] == "Mila Fox"


def test_remove_like_success(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
            _FakeResult(scalar=None),  # no active match
            _FakeResult(scalars=[1]),  # delete like returning ids
        ]
    )
    with client_with_session(session) as client:
        response = client.delete("/interactions/like/2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["removed"] is True
    assert payload["data"]["removed_count"] == 1
    assert sum(isinstance(item, AuditLog) for item in session.added) == 1


def test_remove_like_match_exists(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
            _FakeResult(scalar=9),  # active match exists
        ]
    )
    with client_with_session(session) as client:
        response = client.delete("/interactions/like/2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "MATCH_EXISTS"


def test_remove_like_not_found(client_with_session):
    session = _FakeSession(
        results=[
            _FakeResult(scalars=[1, 2]),  # lock users
            _FakeResult(scalar=None),  # no active match
            _FakeResult(scalars=[]),  # nothing deleted
        ]
    )
    with client_with_session(session) as client:
        response = client.delete("/interactions/like/2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "LIKE_NOT_FOUND"
