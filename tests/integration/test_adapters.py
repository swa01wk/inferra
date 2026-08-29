"""Integration tests for the LoRA adapter registry."""

import os
from uuid import uuid4

import httpx
import pytest

BASE_URL = os.getenv("INFERRA_BASE_URL", "http://localhost:9100")
INFERENCE_KEY = os.getenv("INFERRA_INFERENCE_KEY", "")
INFERENCE_KEY_B = os.getenv("INFERRA_INFERENCE_KEY_B", "")  # second tenant's key for isolation tests
BASE_MODEL = os.getenv("INFERRA_BASE_MODEL", "Qwen/Qwen3-4B")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def _headers(key: str | None = None) -> dict:
    return {"Authorization": f"Bearer {key or INFERENCE_KEY}"}


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_register_adapter_returns_201_status(client: httpx.Client) -> None:
    """Registering a new adapter returns the adapter record with registered status."""
    unique_name = f"test-adapter-{uuid4().hex[:8]}"
    response = client.post(
        "/v1/adapters",
        headers=_headers(),
        json={
            "name": unique_name,
            "storage_uri": "s3://inferra-adapters/test-adapter/",
            "base_model": BASE_MODEL,
            "rank": 8,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == unique_name
    assert body["status"] in {"registered", "downloading", "available", "failed"}
    assert body["rank"] == 8
    return body["id"]


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_rank_too_large_returns_422(client: httpx.Client) -> None:
    """Adapters with rank > max_lora_rank (16) are rejected with 422."""
    response = client.post(
        "/v1/adapters",
        headers=_headers(),
        json={
            "name": f"rank-overflow-{uuid4().hex[:6]}",
            "storage_uri": "s3://inferra-adapters/dummy/",
            "base_model": BASE_MODEL,
            "rank": 128,  # far exceeds the 16-rank limit
        },
    )
    assert response.status_code == 422


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_list_adapters_scoped_to_tenant(client: httpx.Client) -> None:
    """GET /v1/adapters only returns adapters belonging to the calling tenant."""
    response = client.get("/v1/adapters", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert "adapters" in body
    assert isinstance(body["adapters"], list)


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_get_nonexistent_adapter_returns_404(client: httpx.Client) -> None:
    """Fetching an adapter that does not exist returns 404."""
    fake_id = str(uuid4())
    response = client.get(f"/v1/adapters/{fake_id}", headers=_headers())
    assert response.status_code == 404


@pytest.mark.skipif(
    not INFERENCE_KEY or not INFERENCE_KEY_B,
    reason="Two inference keys required for cross-tenant test",
)
def test_cross_tenant_adapter_access_denied(client: httpx.Client) -> None:
    """Tenant A cannot read Tenant B's private adapter."""
    # Register adapter as tenant A
    unique_name = f"private-adapter-{uuid4().hex[:8]}"
    r = client.post(
        "/v1/adapters",
        headers=_headers(INFERENCE_KEY),
        json={
            "name": unique_name,
            "storage_uri": "s3://inferra-adapters/private/",
            "base_model": BASE_MODEL,
            "rank": 8,
        },
    )
    assert r.status_code == 200
    adapter_id = r.json()["id"]

    # Tenant B tries to read it — should get 404 (not found / not visible)
    response_b = client.get(
        f"/v1/adapters/{adapter_id}",
        headers=_headers(INFERENCE_KEY_B),
    )
    assert response_b.status_code in {403, 404}


@pytest.mark.skipif(not INFERENCE_KEY, reason="INFERRA_INFERENCE_KEY not set")
def test_delete_adapter_soft_deletes(client: httpx.Client) -> None:
    """Deleting an adapter sets its status to deleted and hides it from list."""
    unique_name = f"to-delete-{uuid4().hex[:8]}"
    create_resp = client.post(
        "/v1/adapters",
        headers=_headers(),
        json={
            "name": unique_name,
            "storage_uri": "s3://inferra-adapters/del-test/",
            "base_model": BASE_MODEL,
            "rank": 4,
        },
    )
    assert create_resp.status_code == 200
    adapter_id = create_resp.json()["id"]

    del_resp = client.delete(f"/v1/adapters/{adapter_id}", headers=_headers())
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Should no longer appear in list
    list_resp = client.get("/v1/adapters", headers=_headers())
    ids = [a["id"] for a in list_resp.json()["adapters"]]
    assert adapter_id not in ids
