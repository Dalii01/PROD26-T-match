from collections.abc import Generator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.user import User, UserFeatures, UserPhoto


def _build_user(user_id: int = 1) -> User:
    user = User(
        id=user_id,
        external_party_rk=10_000_000 + user_id,
        first_name="Ivan",
        last_name="Petrov",
        nickname=f"ivanpetrov{user_id}",
        bio="Hello",
        gender="male",
        birth_date=date(1995, 1, 10),
        is_active=True,
    )
    user.photos = [
        UserPhoto(
            id=1,
            user_id=user_id,
            url="https://example.com/photo.jpg",
            is_primary=True,
        )
    ]
    user.features = UserFeatures(
        id=1,
        user_id=user_id,
        features={"vector": [0.1, 0.2], "tags": ["coffee", "music"]},
    )
    return user


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    async def _override_get_session():
        class _DummySession:
            pass

        yield _DummySession()

    app.dependency_overrides[get_session] = _override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_get_user_me_requires_header(client: TestClient) -> None:
    response = client.get("/users/me")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_ID_REQUIRED"


def test_get_user_by_id_daily_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    async def _deny(*_args, **_kwargs):
        return False, 20

    monkeypatch.setattr("app.api.routers.users.check_and_incr_daily", _deny)

    response = client.get("/users/2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "DAILY_LIMIT_REACHED"


def test_get_user_by_id_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_get_user_by_id(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.routers.users._get_user_by_id", _fake_get_user_by_id)

    response = client.get("/users/999", headers={"X-User-ID": "1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_get_user_by_id_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_get_user_by_id(*_args, **_kwargs):
        return _build_user(user_id=2)

    monkeypatch.setattr("app.api.routers.users._get_user_by_id", _fake_get_user_by_id)

    response = client.get("/users/2", headers={"X-User-ID": "1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == 2
    assert payload["data"]["primary_photo_url"] == "https://example.com/photo.jpg"
    assert payload["data"]["tags"] == ["coffee", "music"]


def test_get_user_me_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_get_user_by_id(*_args, **_kwargs):
        return _build_user(user_id=1)

    monkeypatch.setattr("app.api.routers.users._get_user_by_id", _fake_get_user_by_id)

    response = client.get("/users/me", headers={"X-User-ID": "1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == 1
