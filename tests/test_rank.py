from collections.abc import Generator
from datetime import date
from typing import cast

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.testclient import TestClient

from app.api.routers import rank as rank_router
from app.db.database import get_session
from app.ml_main import app
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


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)


def _build_user(user_id: int, external_party_rk: int | None = None) -> User:
    user = User(
        id=user_id,
        external_party_rk=external_party_rk,
        first_name="Ivan",
        last_name="Petrov",
        nickname=f"ivan{user_id}",
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


def test_rank_requires_header(client: TestClient) -> None:
    response = client.get("/rank")
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_rank_invalid_header(client: TestClient) -> None:
    response = client.get("/rank", headers={"X-User-ID": "oops"})
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID must be integer"


def test_rank_limit_validation(client: TestClient) -> None:
    response = client.get("/rank?limit=0", headers={"X-User-ID": "1"})
    assert response.status_code == 422


def test_rank_uses_hybrid_when_available(
    client_with_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_scored_recs(*_args, **_kwargs):
        return [("party_rk_2", 0.9)]

    # _build_rank_payload вызывается дважды: сначала с city-фильтром,
    # затем без (т.к. MIN_CITY_RESULTS=3 и mock возвращает 1 запись).
    async def _fake_payload(*_args, **_kwargs):
        return [{"user_id": 2, "score": 0.9, "name": "Ivan Petrov"}]

    monkeypatch.setattr(
        "app.api.routers.rank.MLRecommender.get_recommendations_scored",
        _fake_scored_recs,
    )

    async def _fake_explain(*_a, **_kw):
        return "вам может понравиться этот человек"

    monkeypatch.setattr(
        "app.api.routers.rank.MLRecommender.explain_match_async",
        _fake_explain,
    )
    monkeypatch.setattr("app.api.routers.rank._build_rank_payload", _fake_payload)

    target_user = _build_user(1, external_party_rk=111)
    session = _FakeSession(results=[_FakeResult(scalar=target_user)])
    with client_with_session(session) as client:
        response = client.get("/rank?limit=10", headers={"X-User-ID": "1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["user_id"] == 2


def test_rank_fallback_user_not_found(
    client_with_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_payload(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.api.routers.rank._build_rank_payload", _fake_payload)

    session = _FakeSession(results=[_FakeResult(scalar=None)])
    with client_with_session(session) as client:
        response = client.get("/rank", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_build_rank_payload_filters_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_2 = _build_user(2)
    user_3 = _build_user(3)

    async def _fake_exclusions(*_args, **_kwargs):
        return {3}

    async def _fake_fetch(*_args, **_kwargs):
        return {2: user_2, 3: user_3}

    monkeypatch.setattr("app.api.routers.rank._load_exclusions", _fake_exclusions)
    monkeypatch.setattr("app.api.routers.rank._fetch_users_map", _fake_fetch)

    payload = await rank_router._build_rank_payload(
        session=cast(AsyncSession, _FakeSession(results=[])),
        user_id=1,
        recs=[{"user_id": 2, "score": 0.9}, {"user_id": 3, "score": 0.8}],
        limit=10,
        cooldown_days=30,
    )
    assert len(payload) == 1
    assert payload[0]["user_id"] == 2


@pytest.mark.asyncio
async def test_build_rank_payload_maps_party_rk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _build_user(5, external_party_rk=555)

    async def _fake_exclusions(*_args, **_kwargs):
        return set()

    async def _fake_fetch(*_args, **_kwargs):
        return {555: user}

    monkeypatch.setattr("app.api.routers.rank._load_exclusions", _fake_exclusions)
    monkeypatch.setattr("app.api.routers.rank._fetch_users_map", _fake_fetch)

    payload = await rank_router._build_rank_payload(
        session=cast(AsyncSession, _FakeSession(results=[])),
        user_id=1,
        recs=[{"party_rk": "555", "score": 0.7}],
        limit=10,
        cooldown_days=30,
    )
    assert len(payload) == 1
    assert payload[0]["user_id"] == 5


@pytest.mark.asyncio
async def test_build_rank_payload_respects_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_2 = _build_user(2)
    user_3 = _build_user(3)

    async def _fake_exclusions(*_args, **_kwargs):
        return set()

    async def _fake_fetch(*_args, **_kwargs):
        return {2: user_2, 3: user_3}

    monkeypatch.setattr("app.api.routers.rank._load_exclusions", _fake_exclusions)
    monkeypatch.setattr("app.api.routers.rank._fetch_users_map", _fake_fetch)

    payload = await rank_router._build_rank_payload(
        session=cast(AsyncSession, _FakeSession(results=[])),
        user_id=1,
        recs=[{"user_id": 2, "score": 0.9}, {"user_id": 3, "score": 0.8}],
        limit=1,
        cooldown_days=30,
    )
    assert len(payload) == 1
