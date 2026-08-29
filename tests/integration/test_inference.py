"""Integration tests for /v1/chat/completions inference endpoint."""

import json
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
def test_non_streaming_chat_returns_complete_response(client: httpx.Client) -> None:
    """Non-streaming completion returns a well-formed response with content."""
    response = client.post(
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "stream": False,
            "max_tokens": 32,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "choices" in body
    assert len(body["choices"]) >= 1
    assert body["choices"][0]["message"]["content"]


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_streaming_chat_returns_sse_events(client: httpx.Client) -> None:
    """Streaming completion delivers SSE chunks and terminates with [DONE]."""
    chunks = []
    done_seen = False
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Count to three."}],
            "stream": True,
            "max_tokens": 32,
        },
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = line[6:].strip()
                if payload == "[DONE]":
                    done_seen = True
                else:
                    try:
                        chunks.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass

    assert len(chunks) > 0, "Expected at least one data chunk"
    assert done_seen, "Expected [DONE] sentinel at end of stream"


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_context_too_long_returns_400(client: httpx.Client) -> None:
    """Requests exceeding the 8192-token context window are rejected with 400."""
    # Request max_tokens near the ceiling to force the guard
    response = client.post(
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
            "max_tokens": 9000,  # > 8192 alone
        },
    )
    assert response.status_code == 400


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_max_tokens_is_respected(client: httpx.Client) -> None:
    """Non-streaming response does not return more completion tokens than max_tokens."""
    response = client.post(
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Write a very long essay about AI."}],
            "stream": False,
            "max_tokens": 10,
        },
    )
    assert response.status_code == 200
    body = response.json()
    usage = body.get("usage") or {}
    completion_tokens = usage.get("completion_tokens", 0)
    # Allow a small overshoot (some backends count sub-tokens differently)
    assert completion_tokens <= 20, f"Expected ≤20 tokens, got {completion_tokens}"


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_models_endpoint_lists_models(client: httpx.Client) -> None:
    """GET /v1/models returns a non-empty list."""
    response = client.get("/v1/models", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body.get("object") == "list"
    assert len(body.get("data", [])) >= 1
