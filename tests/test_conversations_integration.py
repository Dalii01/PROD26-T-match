import os

import httpx
import pytest


def _base_url() -> str:
    return os.getenv("APP_BASE_URL", os.getenv("BASE_URL", "http://localhost")).rstrip(
        "/"
    )


def _health_ok(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url}/health", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


@pytest.fixture(scope="session")
def base_url() -> str:
    base_url = _base_url()
    if not _health_ok(base_url):
        pytest.skip(f"API not reachable at {base_url}")
    return base_url


def _first_user_id(base_url: str) -> int:
    response = httpx.get(f"{base_url}/users", params={"limit": 1}, timeout=5.0)
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    users = payload["data"]
    if not users:
        pytest.skip("No users available for integration tests")
    return users[0]["id"]


@pytest.mark.integration
def test_conversations_requires_header(base_url: str) -> None:
    response = httpx.get(f"{base_url}/conversations", timeout=5.0)
    assert response.status_code == 400
    assert response.json()["detail"] == "X-User-ID header required"


@pytest.mark.integration
def test_conversations_create_validation(base_url: str) -> None:
    user_id = _first_user_id(base_url)
    response = httpx.post(
        f"{base_url}/conversations",
        headers={"X-User-ID": str(user_id)},
        json={},
        timeout=5.0,
    )
    assert response.status_code == 422


@pytest.mark.integration
def test_messages_validation(base_url: str) -> None:
    user_id = _first_user_id(base_url)
    response = httpx.post(
        f"{base_url}/conversations/1/messages",
        headers={"X-User-ID": str(user_id)},
        json={"body": ""},
        timeout=5.0,
    )
    assert response.status_code == 422
