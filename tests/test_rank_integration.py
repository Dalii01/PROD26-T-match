import os

import httpx
import pytest


def _base_url() -> str:
    return os.getenv("BASE_URL", "http://ml:8001").rstrip("/")


def _app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://app:8000").rstrip("/")


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
        pytest.skip(f"ML API not reachable at {base_url}")
    return base_url


@pytest.fixture(scope="session")
def app_base_url() -> str:
    app_base_url = _app_base_url()
    if not _health_ok(app_base_url):
        pytest.skip(f"App API not reachable at {app_base_url}")
    return app_base_url


def _first_user_id(app_base_url: str) -> int:
    response = httpx.get(f"{app_base_url}/users", params={"limit": 1}, timeout=5.0)
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    users = payload["data"]
    if not users:
        pytest.skip("No users available for rank tests")
    return users[0]["id"]


@pytest.mark.integration
def test_rank_requires_header(base_url: str) -> None:
    response = httpx.get(f"{base_url}/rank", timeout=5.0)
    assert response.status_code == 400


@pytest.mark.integration
def test_rank_success(base_url: str, app_base_url: str) -> None:
    user_id = _first_user_id(app_base_url)
    response = httpx.get(
        f"{base_url}/rank",
        headers={"X-User-ID": str(user_id)},
        timeout=5.0,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert isinstance(payload["data"], list)
