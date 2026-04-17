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


def _two_user_ids(base_url: str) -> tuple[int, int]:
    response = httpx.get(f"{base_url}/users", params={"limit": 2}, timeout=5.0)
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    users = payload["data"]
    if len(users) < 2:
        pytest.skip("Need at least two users for interactions tests")
    return users[0]["id"], users[1]["id"]


@pytest.mark.integration
def test_user_me_requires_header(base_url: str) -> None:
    response = httpx.get(f"{base_url}/users/me", timeout=5.0)
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_ID_REQUIRED"


@pytest.mark.integration
def test_user_me_success(base_url: str) -> None:
    user_id = _first_user_id(base_url)
    response = httpx.get(
        f"{base_url}/users/me",
        headers={"X-User-ID": str(user_id)},
        timeout=5.0,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == user_id


@pytest.mark.integration
def test_user_by_id_success(base_url: str) -> None:
    user_id = _first_user_id(base_url)
    response = httpx.get(
        f"{base_url}/users/{user_id}",
        timeout=5.0,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["id"] == user_id


@pytest.mark.integration
def test_interactions_skip(base_url: str) -> None:
    actor_id, target_id = _two_user_ids(base_url)
    response = httpx.post(
        f"{base_url}/interactions",
        headers={"X-User-ID": str(actor_id)},
        json={"target_id": target_id, "action": "skip"},
        timeout=5.0,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["action"] == "skip"


@pytest.mark.integration
def test_interactions_invalid_action(base_url: str) -> None:
    actor_id, target_id = _two_user_ids(base_url)
    response = httpx.post(
        f"{base_url}/interactions",
        headers={"X-User-ID": str(actor_id)},
        json={"target_id": target_id, "action": "superlike"},
        timeout=5.0,
    )
    assert response.status_code == 422


@pytest.mark.integration
def test_interactions_target_not_found(base_url: str) -> None:
    actor_id, _ = _two_user_ids(base_url)
    response = httpx.post(
        f"{base_url}/interactions",
        headers={"X-User-ID": str(actor_id)},
        json={"target_id": 999999999, "action": "like"},
        timeout=5.0,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "USER_NOT_FOUND"
