from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.match import Match
from app.models.message import Message


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeResult:
    def __init__(self, scalar=None, scalars=None, first_row=None, rows=None):
        self._scalar = scalar
        self._scalars = scalars
        self._first_row = first_row
        self._rows = rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _FakeScalars(self._scalars or [])

    def first(self):
        return self._first_row

    def all(self):
        return list(self._rows or [])


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, results, get_map=None):
        self._results = list(results)
        self._get_map = get_map or {}
        self.executed = []
        self.added = []

    def begin(self):
        return _FakeBegin()

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self._results:
            raise AssertionError("No prepared results for execute call")
        return self._results.pop(0)

    async def get(self, model, key):
        return self._get_map.get((model, key))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for idx, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", idx)


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
) -> Match:
    return Match(
        id=match_id,
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        status=status,
    )


def _build_conversation(
    conversation_id: int = 10,
    match_id: int = 1,
    status: str = "active",
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> Conversation:
    conversation = Conversation(id=conversation_id, match_id=match_id, status=status)
    if created_at is not None:
        conversation.created_at = created_at
    if closed_at is not None:
        conversation.closed_at = closed_at
    return conversation


def _build_message(
    message_id: int = 5,
    conversation_id: int = 10,
    sender_id: int = 1,
    body: str = "hello",
    created_at: datetime | None = None,
) -> Message:
    message = Message(
        id=message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        body=body,
    )
    if created_at is not None:
        message.created_at = created_at
    return message


def test_conversations_requires_header(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.get("/conversations")
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


def test_list_conversations_empty(client_with_session):
    session = _FakeSession(results=[_FakeResult(rows=[])])
    with client_with_session(session) as client:
        response = client.get("/conversations", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"] == []


def test_create_conversation_match_not_found(client_with_session):
    session = _FakeSession(results=[_FakeResult(scalar=None)])
    with client_with_session(session) as client:
        response = client.post(
            "/conversations", headers={"X-User-ID": "1"}, json={"match_id": 10}
        )
    assert response.status_code == 403
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "MATCH_NOT_FOUND"


def test_create_conversation_forbidden_user(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    session = _FakeSession(results=[_FakeResult(scalar=match)])
    with client_with_session(session) as client:
        response = client.post(
            "/conversations", headers={"X-User-ID": "3"}, json={"match_id": 10}
        )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "MATCH_FORBIDDEN"


def test_create_conversation_closed_match(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2, status="closed")
    session = _FakeSession(results=[_FakeResult(scalar=match)])
    with client_with_session(session) as client:
        response = client.post(
            "/conversations", headers={"X-User-ID": "1"}, json={"match_id": 10}
        )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "MATCH_INACTIVE"


def test_create_conversation_blocked_match(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    session = _FakeSession(results=[_FakeResult(scalar=match), _FakeResult(scalar=1)])
    with client_with_session(session) as client:
        response = client.post(
            "/conversations", headers={"X-User-ID": "1"}, json={"match_id": 10}
        )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "MATCH_BLOCKED"


def test_create_conversation_existing(client_with_session):
    created_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(
        conversation_id=99, match_id=10, created_at=created_at
    )
    session = _FakeSession(
        results=[
            _FakeResult(scalar=match),
            _FakeResult(scalar=None),
            _FakeResult(scalar=None),
            _FakeResult(scalar=conversation),
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/conversations", headers={"X-User-ID": "1"}, json={"match_id": 10}
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == 99
    assert payload["data"]["created_at"] == created_at.isoformat()


def test_create_conversation_new_adds_audit_log(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(conversation_id=99, match_id=10)
    session = _FakeSession(
        results=[
            _FakeResult(scalar=match),
            _FakeResult(scalar=None),
            _FakeResult(scalar=99),
        ],
        get_map={(Conversation, 99): conversation},
    )
    with client_with_session(session) as client:
        response = client.post(
            "/conversations", headers={"X-User-ID": "1"}, json={"match_id": 10}
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == 99
    assert any(isinstance(item, AuditLog) for item in session.added)


def test_list_messages_forbidden_user(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(conversation_id=55, match_id=10)
    session = _FakeSession(results=[_FakeResult(first_row=(conversation, match))])
    with client_with_session(session) as client:
        response = client.get("/conversations/55/messages", headers={"X-User-ID": "3"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "CONVERSATION_FORBIDDEN"


def test_list_messages_closed_match(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2, status="closed")
    conversation = _build_conversation(conversation_id=55, match_id=10)
    session = _FakeSession(results=[_FakeResult(first_row=(conversation, match))])
    with client_with_session(session) as client:
        response = client.get("/conversations/55/messages", headers={"X-User-ID": "1"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "CONVERSATION_CLOSED"


def test_list_messages_blocked_match(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(conversation_id=55, match_id=10)
    session = _FakeSession(
        results=[
            _FakeResult(first_row=(conversation, match)),
            _FakeResult(scalar=1),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/conversations/55/messages", headers={"X-User-ID": "1"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "CONVERSATION_BLOCKED"


def test_list_messages_success(client_with_session):
    created_at = datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc)
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(conversation_id=55, match_id=10)
    message = _build_message(
        message_id=7, conversation_id=55, sender_id=2, created_at=created_at
    )
    session = _FakeSession(
        results=[
            _FakeResult(first_row=(conversation, match)),
            _FakeResult(scalar=None),
            _FakeResult(scalars=[message]),
        ]
    )
    with client_with_session(session) as client:
        response = client.get("/conversations/55/messages", headers={"X-User-ID": "1"})
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["id"] == 7
    assert payload["data"][0]["created_at"] == created_at.isoformat()


def test_create_message_validation(client_with_session):
    session = _FakeSession(results=[])
    with client_with_session(session) as client:
        response = client.post(
            "/conversations/55/messages",
            headers={"X-User-ID": "1"},
            json={"body": ""},
        )
    assert response.status_code == 422


def test_create_message_blocked_match(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(conversation_id=55, match_id=10)
    session = _FakeSession(
        results=[
            _FakeResult(first_row=(conversation, match)),
            _FakeResult(scalar=1),
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/conversations/55/messages",
            headers={"X-User-ID": "1"},
            json={"body": "hi"},
        )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "CONVERSATION_BLOCKED"


def test_create_message_success(client_with_session):
    match = _build_match(match_id=10, user_a_id=1, user_b_id=2)
    conversation = _build_conversation(conversation_id=55, match_id=10)
    session = _FakeSession(
        results=[
            _FakeResult(first_row=(conversation, match)),
            _FakeResult(scalar=None),
        ]
    )
    with client_with_session(session) as client:
        response = client.post(
            "/conversations/55/messages",
            headers={"X-User-ID": "1"},
            json={"body": "hi"},
        )
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["sender_id"] == 1
    assert payload["data"]["body"] == "hi"
