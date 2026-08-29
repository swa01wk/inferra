"""Integration tests for /health endpoint."""

import os

import httpx
import pytest

BASE_URL = os.getenv("INFERRA_BASE_URL", "http://localhost:9100")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def test_health_returns_200_when_vllm_ready(client: httpx.Client) -> None:
    """Health endpoint returns 200 when vLLM is reachable."""
    response = client.get("/health")
    # When stack is running with mock or real vLLM, expect 200 ok
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["vllm"] == "ready"


def test_health_response_shape(client: httpx.Client) -> None:
    """Health response always contains status and vllm fields."""
    response = client.get("/health")
    body = response.json()
    assert "status" in body
    assert "vllm" in body
    assert body["status"] in {"ok", "degraded"}
    assert body["vllm"] in {"ready", "unavailable"}
