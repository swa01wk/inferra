"""Integration tests for rate limiting and admission control."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pytest

BASE_URL = os.getenv("INFERRA_BASE_URL", "http://localhost:9100")
INFERENCE_KEY = os.getenv("INFERRA_INFERENCE_KEY", "")
MODEL = os.getenv("INFERRA_MODEL", "test-assistant")

# These tests can be slow/disruptive; mark them so they can be excluded in CI
pytestmark = pytest.mark.rate_limits


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def _headers() -> dict:
    return {"Authorization": f"Bearer {INFERENCE_KEY}"}


def _fire_request(base_url: str, headers: dict, model: str) -> int:
    """Send a single chat request and return the HTTP status code."""
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "max_tokens": 4,
            },
        )
        return r.status_code


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_concurrent_limit_enforced(client: httpx.Client) -> None:
    """Exceeding the concurrent request cap (5 by default) triggers a 429."""
    # Fire 8 concurrent requests; at least some should be rate-limited
    futures_statuses = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_fire_request, BASE_URL, _headers(), MODEL) for _ in range(8)]
        for f in as_completed(futs):
            futures_statuses.append(f.result())

    rate_limited = [s for s in futures_statuses if s in {429, 503}]
    # Not all must be limited, but at least 1 should hit the ceiling
    assert len(rate_limited) >= 1, (
        f"Expected at least 1 rate-limited response; got statuses: {futures_statuses}"
    )


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_429_has_retry_after_header(client: httpx.Client) -> None:
    """Rate-limited responses include a Retry-After header."""
    statuses_and_headers = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        def _fire_full(base_url, headers, model):
            with httpx.Client(base_url=base_url, timeout=30.0) as c:
                r = c.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                        "max_tokens": 4,
                    },
                )
                return r.status_code, dict(r.headers)

        futs = [pool.submit(_fire_full, BASE_URL, _headers(), MODEL) for _ in range(8)]
        for f in as_completed(futs):
            statuses_and_headers.append(f.result())

    for status, headers in statuses_and_headers:
        if status in {429, 503}:
            assert "retry-after" in {k.lower() for k in headers}, (
                f"429/503 response missing Retry-After header; got headers: {list(headers.keys())}"
            )
            break


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_context_length_ceiling_returns_400(client: httpx.Client) -> None:
    """Requests that exceed the context ceiling return 400, not a silent error."""
    response = client.post(
        "/v1/chat/completions",
        headers=_headers(),
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "max_tokens": 9000,
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert "detail" in body or "error" in body
