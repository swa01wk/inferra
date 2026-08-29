import os

import httpx
import pytest

BASE_URL = os.getenv("INFERRA_BASE_URL", "http://localhost:9100")
INFERENCE_KEY = os.getenv("INFERRA_INFERENCE_KEY", "")
ADMIN_KEY = os.getenv("INFERRA_ADMIN_KEY", "")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=60.0)


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_health(client: httpx.Client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_streaming_chat(client: httpx.Client) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {INFERENCE_KEY}"},
        json={
            "model": "test-assistant",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
            "max_tokens": 32,
        },
    ) as response:
        assert response.status_code == 200
        chunks = list(response.iter_bytes())
        assert chunks


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_usage_endpoint(client: httpx.Client) -> None:
    client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {INFERENCE_KEY}"},
        json={
            "model": "test-assistant",
            "messages": [{"role": "user", "content": "Usage test"}],
            "stream": False,
            "max_tokens": 32,
        },
    )
    response = client.get("/v1/usage", headers={"Authorization": f"Bearer {INFERENCE_KEY}"})
    assert response.status_code == 200
    data = response.json()
    assert data["total_requests"] >= 1


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_invalid_key(client: httpx.Client) -> None:
    response = client.get(
        "/v1/models",
        headers={"Authorization": "Bearer invalid-key"},
    )
    assert response.status_code == 401


@pytest.mark.skipif(not ADMIN_KEY or not INFERENCE_KEY, reason="Admin/inference keys not set")
def test_admin_endpoint_rejects_inference_key(client: httpx.Client) -> None:
    response = client.get(
        "/v1/workers",
        headers={"Authorization": f"Bearer {INFERENCE_KEY}"},
    )
    assert response.status_code == 403
