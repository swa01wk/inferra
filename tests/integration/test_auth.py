"""Integration tests for authentication and authorization."""

import os

import httpx
import pytest

BASE_URL = os.getenv("INFERRA_BASE_URL", "http://localhost:9100")
INFERENCE_KEY = os.getenv("INFERRA_INFERENCE_KEY", "")
ADMIN_KEY = os.getenv("INFERRA_ADMIN_KEY", "")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def _inference_headers() -> dict:
    return {"Authorization": f"Bearer {INFERENCE_KEY}"}


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_valid_inference_key_accepted(client: httpx.Client) -> None:
    """A valid inference key passes authentication on an inference endpoint."""
    response = client.get("/v1/models", headers=_inference_headers())
    assert response.status_code == 200


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_missing_auth_header_returns_403(client: httpx.Client) -> None:
    """Requests without Authorization header are rejected."""
    response = client.get("/v1/models")
    assert response.status_code in {401, 403, 422}


def test_invalid_key_returns_401(client: httpx.Client) -> None:
    """A totally invalid key returns 401."""
    response = client.get(
        "/v1/models",
        headers={"Authorization": "Bearer inf_totallywrongkey"},
    )
    assert response.status_code == 401


def test_random_token_returns_401(client: httpx.Client) -> None:
    """A random bearer token is rejected with 401."""
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer notavalidkeyatall"},
        json={"model": "test-assistant", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


@pytest.mark.skipif(not ADMIN_KEY or not INFERENCE_KEY, reason="Both keys required")
def test_admin_key_rejected_on_inference_endpoint(client: httpx.Client) -> None:
    """Admin keys are not accepted on inference endpoints."""
    response = client.post(
        "/v1/chat/completions",
        headers=_admin_headers(),
        json={"model": "test-assistant", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403


@pytest.mark.skipif(not INFERENCE_KEY or not ADMIN_KEY, reason="Both keys required")
def test_inference_key_rejected_on_admin_endpoint(client: httpx.Client) -> None:
    """Inference keys cannot access admin-only endpoints."""
    response = client.get("/v1/workers", headers=_inference_headers())
    assert response.status_code == 403


@pytest.mark.skipif(not ADMIN_KEY, reason="INFERRA_ADMIN_KEY not set")
def test_valid_admin_key_accepted_on_admin_endpoint(client: httpx.Client) -> None:
    """A valid admin key can reach admin endpoints."""
    response = client.get("/v1/workers", headers=_admin_headers())
    assert response.status_code == 200
