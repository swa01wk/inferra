# API Reference

**Base URL:** `http://localhost:9100` (local development)

All inference and adapter endpoints require a `Bearer` token in the `Authorization` header. See [Authentication](authentication.md) for key management.

---

## Table of Contents

- [Health](#health)
- [Metrics](#metrics)
- [Inference](#inference)
  - [POST /v1/chat/completions](#post-v1chatcompletions)
  - [GET /v1/models](#get-v1models)
- [Usage](#usage)
  - [GET /v1/usage](#get-v1usage)
- [LoRA Adapters](#lora-adapters)
  - [POST /v1/adapters](#post-v1adapters)
  - [GET /v1/adapters](#get-v1adapters)
  - [GET /v1/adapters/{adapter_id}](#get-v1adaptersadapter_id)
  - [DELETE /v1/adapters/{adapter_id}](#delete-v1adaptersadapter_id)
  - [POST /v1/aliases](#post-v1aliases)
- [Admin](#admin)
  - [POST /v1/api-keys](#post-v1api-keys)
  - [DELETE /v1/api-keys/{key_id}](#delete-v1api-keyskey_id)
  - [GET /v1/workers](#get-v1workers)
  - [GET /v1/deployments](#get-v1deployments)

---

## Health

### `GET /health`

Returns the gateway's own status and whether vLLM is reachable.

**Authentication:** None required.

**Response `200 OK`** — vLLM is reachable:
```json
{
  "status": "ok",
  "vllm": "ready"
}
```

**Response `503 Service Unavailable`** — vLLM is not reachable:
```json
{
  "status": "degraded",
  "vllm": "unavailable"
}
```

> The HTTP status code is `200` when vLLM is healthy and `503` when it is not. The `status` field in the body mirrors this.

---

## Metrics

### `GET /metrics`

Exports all Prometheus metrics in text exposition format. Scraped by Prometheus; not intended for direct client use.

**Authentication:** None required.

**Response:** `text/plain; version=0.0.4` (Prometheus text format)

---

## Inference

### `POST /v1/chat/completions`

The core inference endpoint. OpenAI-compatible. Supports both streaming and non-streaming modes.

**Authentication:** Inference key required (`is_admin=false`).

#### Request Body

```json
{
  "model": "my-assistant",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Explain KV-cache in simple terms."}
  ],
  "stream": false,
  "max_tokens": 512,
  "temperature": 0.7,
  "top_p": 1.0
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | YES | — | Model alias, HuggingFace repo ID, or public alias |
| `messages` | array | YES | — | Conversation turns. Each has `role` and `content` |
| `stream` | boolean | NO | `false` | Return SSE stream if `true` |
| `max_tokens` | integer | NO | `512` | Maximum output tokens. Capped at org policy limit |
| `temperature` | float | NO | `1.0` | Sampling temperature |
| `top_p` | float | NO | `1.0` | Nucleus sampling parameter |

**`messages[].role`** — Allowed values: `system`, `user`, `assistant`

#### Admission Checks (in order)

Before the request reaches vLLM, the gateway enforces:

1. **Input token ceiling** — word-count estimate of messages must be ≤ `max_input_tokens` (policy, default 8192)
2. **Context ceiling** — `estimated_prompt + max_tokens` must be ≤ 8192 (hard global limit)
3. **RPM limit** — requests per minute per organization (default 60)
4. **Concurrency limit** — max in-flight requests per organization (default 5)
5. **Daily token quota** — cumulative daily token usage per organization (default 1,000,000)
6. **Global queue** — system-wide in-flight gate (default 50)

#### Non-Streaming Response `200 OK`

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1722345678,
  "model": "Qwen/Qwen3-4B",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "KV-cache stores the key and value tensors from the attention mechanism..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 48,
    "completion_tokens": 156,
    "total_tokens": 204
  }
}
```

#### Streaming Response `200 OK`

When `stream: true`, the response is `text/event-stream`. Each event:

```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1722345678,"model":"Qwen/Qwen3-4B","choices":[{"index":0,"delta":{"role":"assistant","content":"KV"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1722345678,"model":"Qwen/Qwen3-4B","choices":[{"index":0,"delta":{"content":"-cache "},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1722345678,"model":"Qwen/Qwen3-4B","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":48,"completion_tokens":156,"total_tokens":204}}

data: [DONE]
```

#### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `400` | Context too long | `{"detail": "Request context too long: estimated N prompt tokens + M max_tokens = X exceeds maximum 8192"}` |
| `400` | Prompt exceeds input ceiling | `{"detail": "Prompt exceeds maximum input token limit of N"}` |
| `401` | Invalid / missing API key | `{"detail": "Invalid or expired API key"}` |
| `403` | Admin key used for inference | `{"detail": "Admin keys cannot be used for inference"}` |
| `403` | Organization suspended | `{"detail": "Organization suspended"}` |
| `429` | RPM limit exceeded | `{"detail": "Rate limit exceeded"}` + `Retry-After: 1` |
| `429` | Concurrency limit | `{"detail": "Concurrent request limit reached"}` |
| `429` | Daily quota exceeded | `{"detail": "Daily token quota exceeded"}` + `Retry-After: 86400` |
| `503` | No active deployment | `{"detail": "No active deployment available"}` |
| `503` | No healthy worker | `{"detail": "No healthy worker available"}` |
| `503` | Global queue saturated | `{"detail": "Service temporarily at capacity. Please retry shortly."}` + `Retry-After: 5` |

#### curl Examples

**Non-streaming:**
```bash
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "max_tokens": 64
  }'
```

**Streaming:**
```bash
curl -N http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [{"role": "user", "content": "Explain attention in transformers."}],
    "stream": true,
    "max_tokens": 256
  }'
```

**OpenAI Python SDK:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:9100/v1",
    api_key="inf_your_inference_key_here",
)

response = client.chat.completions.create(
    model="test-assistant",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=128,
)
print(response.choices[0].message.content)
```

---

### `GET /v1/models`

Lists available models (proxied from vLLM).

**Authentication:** Inference key required.

**Response `200 OK`:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen/Qwen3-4B",
      "object": "model",
      "created": 1722345678,
      "owned_by": "inferra"
    }
  ]
}
```

---

## Usage

### `GET /v1/usage`

Returns the calling organization's last 100 inference requests with token and latency data.

**Authentication:** Inference key required.

**Response `200 OK`:**
```json
{
  "total_requests": 42,
  "total_prompt_tokens": 15032,
  "total_completion_tokens": 8744,
  "requests": [
    {
      "request_id": "550e8400-e29b-41d4-a716-446655440000",
      "logical_model": "test-assistant",
      "status": "completed",
      "received_at": "2026-08-29T14:01:23.456789+00:00",
      "prompt_tokens": 48,
      "completion_tokens": 156,
      "ttft_ms": 342,
      "total_ms": 2891
    }
  ]
}
```

**Field descriptions:**

| Field | Description |
|-------|-------------|
| `total_requests` | Count of rows returned (up to 100, most recent) |
| `total_prompt_tokens` | Sum of prompt tokens across all returned rows |
| `total_completion_tokens` | Sum of completion tokens across all returned rows |
| `requests[].ttft_ms` | Time-to-first-token in milliseconds (null for non-streaming) |
| `requests[].total_ms` | End-to-end wall clock in milliseconds |

---

## LoRA Adapters

### `POST /v1/adapters`

Register and begin loading a LoRA adapter from S3/MinIO storage.

**Authentication:** Inference key required.

#### Request Body

```json
{
  "name": "my-fine-tuned-adapter",
  "storage_uri": "s3://inferra-adapters/my-org/adapter-v1/",
  "base_model": "Qwen/Qwen3-4B",
  "rank": 16,
  "alias": "my-fine-tuned-assistant"
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | YES | — | Unique name within the organization |
| `storage_uri` | string | YES | — | `s3://` or `minio://` URI pointing to adapter artifacts |
| `base_model` | string | NO | `"Qwen/Qwen3-4B"` | HuggingFace repo ID of the base model |
| `rank` | integer | NO | `16` | LoRA rank. Must be ≤ 16 (platform limit) |
| `alias` | string | NO | `null` | If provided, immediately creates a model alias |

**Processing:** Registration returns immediately. Artifact download and vLLM loading happen asynchronously in the background. Poll `GET /v1/adapters/{id}` to check `status`.

**Response `200 OK`:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440001",
  "organization_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "my-fine-tuned-adapter",
  "base_model_id": "550e8400-e29b-41d4-a716-446655440002",
  "rank": 16,
  "status": "registered",
  "error_message": null,
  "created_at": "2026-08-29T14:01:23.456789+00:00"
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| `400` | Base model not found in platform catalog |
| `409` | Adapter name already exists for this organization |
| `422` | `rank` exceeds platform maximum (16) |

---

### `GET /v1/adapters`

List all non-deleted adapters for the calling organization.

**Authentication:** Inference key required.

**Response `200 OK`:**
```json
{
  "adapters": [
    {
      "id": "550e8400-...",
      "organization_id": "...",
      "name": "my-fine-tuned-adapter",
      "base_model_id": "...",
      "rank": 16,
      "status": "active",
      "error_message": null,
      "created_at": "2026-08-29T14:01:23.456789+00:00"
    }
  ]
}
```

**`status` values:**

| Value | Description |
|-------|-------------|
| `registered` | Newly created, background processing not yet started |
| `downloading` | Downloading artifacts from S3/MinIO |
| `available` | Downloaded and validated, waiting to load into vLLM |
| `loaded` | Loaded into vLLM via `load_lora_adapter` API |
| `active` | Confirmed active and ready to serve |
| `failed` | Error during download or load (see `error_message`) |
| `deleted` | Soft-deleted, no longer shown by default |

---

### `GET /v1/adapters/{adapter_id}`

Get details for a specific adapter.

**Authentication:** Inference key required (must belong to caller's organization).

**Path Parameters:**
- `adapter_id` — UUID of the adapter

**Response `200 OK`:** Same schema as adapter list items.

**Response `404 Not Found`:** Adapter not found or belongs to another organization.

---

### `DELETE /v1/adapters/{adapter_id}`

Soft-delete an adapter (sets `status='deleted'`). The adapter is no longer visible in list responses or usable for inference.

**Authentication:** Inference key required.

**Response `200 OK`:**
```json
{
  "status": "deleted",
  "id": "550e8400-e29b-41d4-a716-446655440001"
}
```

---

### `POST /v1/aliases`

Create a model alias that maps a friendly name to a (base model, optional adapter) pair.

**Authentication:** Inference key required.

#### Request Body

```json
{
  "alias": "my-assistant-v2",
  "base_model": "Qwen/Qwen3-4B",
  "adapter_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `alias` | string | YES | — | The name to use as `model` in chat completions |
| `base_model` | string | NO | `"Qwen/Qwen3-4B"` | HuggingFace repo ID |
| `adapter_id` | UUID | NO | `null` | Adapter to attach. If null, alias resolves to base model |

**Response `200 OK`:**
```json
{
  "alias": "my-assistant-v2",
  "adapter_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| `400` | Base model not found |
| `403` | `adapter_id` belongs to another organization |

---

## Admin

Admin endpoints require an **admin key** (`is_admin=true`). See [Authentication](authentication.md).

### `POST /v1/api-keys`

Create a new inference API key for an organization.

**Authentication:** Admin key required.

#### Request Body

```json
{
  "name": "prod-inference-key",
  "organization_id": "550e8400-e29b-41d4-a716-446655440000",
  "expires_at": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | YES | Human-readable label |
| `organization_id` | UUID | NO | Defaults to admin's own organization |
| `expires_at` | ISO 8601 datetime | NO | Optional expiry. `null` = never expires |

**Response `200 OK`:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440003",
  "key_prefix": "inf_aBcD",
  "name": "prod-inference-key",
  "organization_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "expires_at": null,
  "secret": "inf_aBcDefGhIjKlMnOpQrStUvWxYz1234567890ab"
}
```

> **Important:** The `secret` field is only returned once at creation time. Store it securely — it cannot be retrieved again.

---

### `DELETE /v1/api-keys/{key_id}`

Revoke an API key immediately. Active in-flight requests using this key are not affected, but future requests will receive `401`.

**Authentication:** Admin key required.

**Response `200 OK`:**
```json
{
  "status": "revoked",
  "id": "550e8400-e29b-41d4-a716-446655440003"
}
```

---

### `GET /v1/workers`

List all registered GPU workers.

**Authentication:** Admin key required.

**Response `200 OK`:**
```json
{
  "workers": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440004",
      "hostname": "runpod-l4-v1",
      "gpu_type": "NVIDIA L4",
      "gpu_vram_mb": 24576,
      "endpoint": "http://host.docker.internal:8001",
      "status": "healthy"
    }
  ]
}
```

---

### `GET /v1/deployments`

List all deployments (model-to-worker pairings).

**Authentication:** Admin key required.

**Response `200 OK`:**
```json
{
  "deployments": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440005",
      "model_id": "550e8400-e29b-41d4-a716-446655440002",
      "worker_id": "550e8400-e29b-41d4-a716-446655440004",
      "endpoint": "http://host.docker.internal:8001",
      "status": "running",
      "config_json": {
        "max_model_len": 8192,
        "dtype": "bfloat16"
      }
    }
  ]
}
```

---

## Common Headers

### Request Headers

| Header | Value | Required |
|--------|-------|----------|
| `Authorization` | `Bearer <key>` | YES (most endpoints) |
| `Content-Type` | `application/json` | YES (POST requests) |

### Response Headers

| Header | Description |
|--------|-------------|
| `X-Request-ID` | UUID identifying this request (injected by gateway middleware) |
| `Retry-After` | Seconds to wait before retrying (on 429/503 responses) |

---

## Rate Limit Response Headers

When a request is rejected with `429`:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 1

{"detail": "Rate limit exceeded"}
```

| Rejection reason | HTTP status | `Retry-After` |
|-----------------|-------------|---------------|
| RPM limit | 429 | 1 |
| Concurrency limit | 429 | (none) |
| Daily quota | 429 | 86400 |
| Global queue | 503 | 5 |
