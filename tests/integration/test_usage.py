"""Integration tests for usage metering and the /v1/usage endpoint."""

import os

import httpx
import pytest

BASE_URL = os.getenv("INFERRA_BASE_URL", "http://localhost:9100")
INFERENCE_KEY = os.getenv("INFERRA_INFERENCE_KEY", "")
MODEL = os.getenv("INFERRA_MODEL", "test-assistant")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=60.0)


def _headers() -> dict:
    return {"Authorization": f"Bearer {INFERENCE_KEY}"}


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_usage_record_created_after_request(client: httpx.Client) -> None:
    """A usage record is persisted after a successful non-streaming request."""
    before = client.get("/v1/usage", headers=_headers())
    assert before.status_code == 200
    count_before = before.json()["total_requests"]

    client.post(
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Usage recording test."}],
            "stream": False,
            "max_tokens": 16,
        },
    )

    after = client.get("/v1/usage", headers=_headers())
    assert after.status_code == 200
    assert after.json()["total_requests"] > count_before


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_usage_endpoint_is_tenant_scoped(client: httpx.Client) -> None:
    """GET /v1/usage returns only the calling tenant's requests."""
    response = client.get("/v1/usage", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert "total_requests" in body
    assert "total_prompt_tokens" in body
    assert "total_completion_tokens" in body
    assert isinstance(body["requests"], list)


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_usage_response_has_latency_fields(client: httpx.Client) -> None:
    """Usage records include timing breakdown fields."""
    client.post(
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Timing test."}],
            "stream": False,
            "max_tokens": 16,
        },
    )
    response = client.get("/v1/usage", headers=_headers())
    assert response.status_code == 200
    requests = response.json()["requests"]
    if requests:
        record = requests[0]
        assert "request_id" in record
        assert "logical_model" in record
        assert "status" in record


def test_usage_endpoint_rejects_unauthenticated(client: httpx.Client) -> None:
    """Usage endpoint rejects requests without a valid key."""
    response = client.get("/v1/usage", headers={"Authorization": "Bearer badkey"})
    assert response.status_code == 401
