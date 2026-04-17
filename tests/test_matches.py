from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.match import Match


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
        self.executed = []
        self.added = []

    def begin(self):
        return _FakeBegin()

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)

    def last_stmt_sql(self) -> str:
        if not self.executed:
            return ""
        return str(self.executed[-1])


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


def _build_match(
    match_id: int = 1,
    user_a_id: int = 1,
    user_b_id: int = 2,
    status: str = "active",
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> Match:
    match = Match(
        id=match_id,
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        status=status,
    )
    if created_at is not None:
        match.created_at = created_at
    if closed_at is not None:
        match.closed_at = closed_at
    return match


def test_matches_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/matches")
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_matches_invalid_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/matches", headers={"X-User-ID": "oops"})
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID must be integer"


def test_list_matches_empty(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalars=[])])
    with client_with_session(session) as client:
        response = client.get("/matches", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"] == []


def test_list_matches_serialization(client_with_session):
    created_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    closed_at = datetime(2024, 2, 1, 12, 0, tzinfo=timezone.utc)
    matches = [
        _build_match(match_id=10, user_a_id=1, user_b_id=2, created_at=created_at),
        _build_match(
            match_id=11,
            user_a_id=3,
            user_b_id=1,
            status="closed",
            created_at=created_at,
            closed_at=closed_at,
        ),
    ]
    session = _FakeSession(results=[_FakeResult(scalars=matches)])
    with client_with_session(session) as client:
        response = client.get("/matches", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["user_id"] == 2
    assert payload["data"][1]["user_id"] == 3
    assert payload["data"][0]["created_at"] == created_at.isoformat()
    assert payload["data"][1]["closed_at"] == closed_at.isoformat()


def test_list_matches_unseen_builds_queue_query(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalars=[])])
    with client_with_session(session) as client:
        response = client.get("/matches?unseen=true", headers={"X-User-ID": "1"})
    assert response.status_code == 200
    stmt_sql = session.last_stmt_sql()
    assert "match_views" in stmt_sql
    assert "ORDER BY matches.created_at ASC" in stmt_sql


def test_list_matches_default_order(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalars=[])])
    with client_with_session(session) as client:
        response = client.get("/matches", headers={"X-User-ID": "1"})
    assert response.status_code == 200
    stmt_sql = session.last_stmt_sql()
    assert "match_views" not in stmt_sql
    assert "ORDER BY matches.created_at DESC" in stmt_sql


def test_list_matches_invalid_unseen_param(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/matches?unseen=wat", headers={"X-User-ID": "1"})
    assert response.status_code == 422


def test_close_match_not_found(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalar=None)])
    with client_with_session(session) as client:
        response = client.patch("/matches/10/close", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "MATCH_NOT_FOUND"


def test_close_match_forbidden(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    session = _FakeSession(results=[_FakeResult(scalar=match)])
    with client_with_session(session) as client:
        response = client.patch("/matches/10/close", headers={"X-User-ID": "3"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "MATCH_FORBIDDEN"


def test_close_match_already_closed(client_with_session):
    closed_at = datetime(2024, 2, 1, 12, 0, tzinfo=timezone.utc)
    match = _build_match(
        match_id=10,
        user_a_id=1,
        user_b_id=2,
        status="closed",
        closed_at=closed_at,
    )
    session = _FakeSession(results=[_FakeResult(scalar=match)])
    with client_with_session(session) as client:
        response = client.patch("/matches/10/close", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["status"] == "closed"
    assert payload["data"]["closed_at"] == closed_at.isoformat()
    assert match.closed_at == closed_at


def test_close_match_success(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2, status="active")
    session = _FakeSession(results=[_FakeResult(scalar=match)])
    with client_with_session(session) as client:
        response = client.patch("/matches/10/close", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["status"] == "closed"
    assert match.status == "closed"
    assert match.closed_at is not None
    assert any(isinstance(item, AuditLog) for item in session.added)


def test_close_match_invalid_id(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.patch("/matches/abc/close", headers={"X-User-ID": "1"})
    assert response.status_code == 422


def test_mark_seen_not_found(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalar=None)])
    with client_with_session(session) as client:
        response = client.patch("/matches/10/seen", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "MATCH_NOT_FOUND"


def test_mark_seen_forbidden(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    session = _FakeSession(results=[_FakeResult(scalar=match)])
    with client_with_session(session) as client:
        response = client.patch("/matches/10/seen", headers={"X-User-ID": "3"})
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "MATCH_FORBIDDEN"


def test_mark_seen_success(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    session = _FakeSession(
        results=[
            _FakeResult(scalar=match),
            _FakeResult(),
        ]
    )
    with client_with_session(session) as client:
        response = client.patch("/matches/10/seen", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == 10
    assert len(session.executed) == 2


def test_mark_seen_invalid_id(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.patch("/matches/abc/seen", headers={"X-User-ID": "1"})
    assert response.status_code == 422
