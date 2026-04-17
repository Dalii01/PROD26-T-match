from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routers import recommendations as rec_router
from app.db.database import get_session
from app.main import app


class _FakeResult:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._rows)


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


def test_recommendations_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/recommendations")
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_recommendations_invalid_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/recommendations", headers={"X-User-ID": "oops"})
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID must be integer"


def test_recommendations_user_not_found(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalar=None)])
    with client_with_session(session) as client:
        response = client.get("/recommendations", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_recommendations_daily_limit_reached(client_with_session, monkeypatch):
    async def _count(*_args, **_kwargs):
        return 20

    monkeypatch.setattr(rec_router, "get_daily_count", _count)

    target_user = SimpleNamespace(id=1, external_party_rk=999)
    session = _FakeSession(results=[_FakeResult(scalar=target_user)])
    with client_with_session(session) as client:
        response = client.get("/recommendations", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "DAILY_LIMIT_REACHED"


def test_recommendations_maps_and_filters(client_with_session, monkeypatch):
    async def _fake_scored(*_args, **_kwargs):
        return [("10", 0.9), ("20", 0.8), ("30", 0.7)]

    async def _fake_explain(*_a, **_kw):
        return "тест"

    monkeypatch.setattr(
        rec_router._recommender, "get_recommendations_scored", _fake_scored
    )
    monkeypatch.setattr(rec_router._recommender, "explain_match_async", _fake_explain)
    monkeypatch.setattr(rec_router, "strict_conditions", lambda *_: [])
    monkeypatch.setattr(rec_router, "city_conditions", lambda *_: [])

    target_user = SimpleNamespace(
        id=1, external_party_rk=999, gender=None, birth_date=None, city=None
    )
    seen_rows = [(5,), (7,)]
    candidates = [
        SimpleNamespace(id=2, external_party_rk=10),
        SimpleNamespace(id=5, external_party_rk=20),
        SimpleNamespace(id=7, external_party_rk=30),
    ]
    session = _FakeSession(
        results=[
            _FakeResult(scalar=target_user),
            _FakeResult(rows=seen_rows),
            _FakeResult(rows=candidates),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/recommendations?top_k=2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert [item["user_id"] for item in payload["data"]] == [2]
    assert "match_reason" in payload["data"][0]


def test_recommendations_fallback_when_no_matches(client_with_session, monkeypatch):
    async def _fake_scored(*_args, **_kwargs):
        return [("10", 0.5)]

    monkeypatch.setattr(
        rec_router._recommender, "get_recommendations_scored", _fake_scored
    )
    monkeypatch.setattr(rec_router, "strict_conditions", lambda *_: [])
    monkeypatch.setattr(rec_router, "city_conditions", lambda *_: [])

    target_user = SimpleNamespace(
        id=1, external_party_rk=999, gender=None, birth_date=None, city=None
    )
    session = _FakeSession(
        results=[
            _FakeResult(scalar=target_user),
            _FakeResult(rows=[]),
            _FakeResult(rows=[]),
            _FakeResult(rows=[]),
            _FakeResult(rows=[(9,), (8,)]),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/recommendations?top_k=2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert [item["user_id"] for item in payload["data"]] == [9, 8]
    assert all("match_reason" in item for item in payload["data"])


def test_recommendations_fallback_when_ml_fails(client_with_session, monkeypatch):
    async def _fail_scored(*_args, **_kwargs):
        raise RuntimeError("ml down")

    monkeypatch.setattr(
        rec_router._recommender, "get_recommendations_scored", _fail_scored
    )
    monkeypatch.setattr(rec_router, "strict_conditions", lambda *_: [])
    monkeypatch.setattr(rec_router, "city_conditions", lambda *_: [])

    target_user = SimpleNamespace(
        id=1, external_party_rk=999, gender=None, birth_date=None, city=None
    )
    session = _FakeSession(
        results=[
            _FakeResult(scalar=target_user),
            _FakeResult(rows=[]),
            _FakeResult(rows=[(9,), (8,)]),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/recommendations?top_k=2", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert [item["user_id"] for item in payload["data"]] == [9, 8]
