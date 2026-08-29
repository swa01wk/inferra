# Getting Started

This guide walks you from zero to your first inference call in under 5 minutes using the local development stack with a mock vLLM engine.

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker + Docker Compose v2)
- `curl` or the OpenAI Python SDK

---

## Step 1: Start the Stack

```bash
cd /path/to/inferra

docker compose up --build -d
```

This starts:
| Service | Port | Description |
|---------|------|-------------|
| `api-gateway` | `9100` | FastAPI inference gateway |
| `vllm` (mock) | internal | Mock vLLM (instant, synthetic responses) |
| `postgres` | internal | Metadata database |
| `redis` | internal | Rate limiting |
| `minio` | internal | Adapter artifact storage |
| `prometheus` | internal | Metrics collection |
| `grafana` | `3000` | Dashboards |

Wait for all services to be healthy:
```bash
docker compose ps
```

All services should show `healthy` or `running`.

---

## Step 2: Seed Development Data

```bash
docker compose exec api-gateway python scripts/seed_dev_data.py
```

Output:
```
Organization: Dev Org  (id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)

ADMIN_KEY:      inf_aBcDeFgHiJkLmNoPqRsTuVwXyZ12345  ← admin operations
INFERENCE_KEY:  inf_zYxWvUtSrQpOnMlKjIhGfEdCbA09876  ← inference calls

Seeded model: Qwen/Qwen3-4B  (id: xxxxxxxx-...)
Seeded worker: mock-vllm
Seeded deployment: running
Seeded alias: test-assistant  →  Qwen/Qwen3-4B
```

Export the keys:
```bash
export INFERRA_ADMIN_KEY=inf_aBcDeFgH...
export INFERRA_INFERENCE_KEY=inf_zYxWvUtS...
```

---

## Step 3: Verify the Gateway

```bash
curl http://localhost:9100/health
```

Expected response:
```json
{"status": "ok", "vllm": "ready"}
```

---

## Step 4: Your First Inference Call

### Non-streaming (simple)

```bash
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [
      {"role": "user", "content": "Explain KV-cache in simple terms."}
    ],
    "max_tokens": 128
  }'
```

Response:
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
        "content": "KV-cache stores previously computed key-value pairs in attention layers..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 18,
    "completion_tokens": 32,
    "total_tokens": 50
  }
}
```

### Streaming

```bash
curl -N http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [
      {"role": "user", "content": "Write a haiku about GPUs."}
    ],
    "stream": true,
    "max_tokens": 64
  }'
```

You'll see tokens arriving as Server-Sent Events:
```
data: {"choices":[{"delta":{"role":"assistant","content":""},...}]}
data: {"choices":[{"delta":{"content":"Silicon"},...}]}
data: {"choices":[{"delta":{"content":" cores"},...}]}
...
data: [DONE]
```

---

## Step 5: Use the OpenAI Python SDK

Install the SDK if you don't have it:
```bash
pip install openai
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:9100/v1",
    api_key="inf_zYxWvUtSrQpOnMlKjIhGfEdCbA09876",  # your inference key
)

# Non-streaming
response = client.chat.completions.create(
    model="test-assistant",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ],
    max_tokens=64,
)
print(response.choices[0].message.content)

# Streaming
stream = client.chat.completions.create(
    model="test-assistant",
    messages=[{"role": "user", "content": "Count to 5 slowly."}],
    max_tokens=32,
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
```

---

## Step 6: Check Your Usage

```bash
curl http://localhost:9100/v1/usage \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY"
```

Response includes your last 100 requests with token counts and latency:
```json
{
  "total_requests": 3,
  "total_prompt_tokens": 54,
  "total_completion_tokens": 96,
  "requests": [
    {
      "request_id": "...",
      "logical_model": "test-assistant",
      "status": "completed",
      "received_at": "2026-08-29T14:01:23.456789+00:00",
      "prompt_tokens": 18,
      "completion_tokens": 32,
      "ttft_ms": 52,
      "total_ms": 694
    }
  ]
}
```

---

## Step 7: View Grafana Dashboards

Open `http://localhost:3000` in your browser.

- Username: `admin`
- Password: `admin`

The pre-built **Inferra V1** dashboard shows:
- Request rate (req/min)
- Time-to-first-token (TTFT) latency histogram
- Token throughput (tokens/s)
- Active request queue depth
- Rate limit rejection counts

---

## What to Do Next

| Goal | Where to go |
|------|-------------|
| Use a real GPU instead of mock vLLM | [RunPod GPU Deployment](../deployment/runpod-gpu.md) |
| Bring your own fine-tuned adapter | [LoRA Adapters Guide](lora-adapters.md) |
| Understand rate limits | [Rate Limits & Quotas](rate-limits-and-quotas.md) |
| Set up monitoring | [Observability Guide](observability.md) |
| Run integration tests | [Contributing & Testing](../development/contributing.md) |
| Full API reference | [API Reference](../api/api-reference.md) |

---

## Troubleshooting

### Health check returns `503`

The mock vLLM container may still be starting. Wait 10–15 seconds and retry:
```bash
docker compose ps
docker compose logs vllm
```

### `401 Unauthorized`

Ensure your key is exported correctly and has not expired:
```bash
echo $INFERRA_INFERENCE_KEY
```

If blank, re-run `seed_dev_data.py` and export the printed keys.

### `No active deployment available`

The seed data was not applied. Run:
```bash
docker compose exec api-gateway python scripts/seed_dev_data.py
```

### Container not starting

Check logs for the failing service:
```bash
docker compose logs api-gateway
docker compose logs postgres
```

A common issue is PostgreSQL not being ready before the gateway starts. The `depends_on: condition: service_healthy` should handle this, but you can manually restart:
```bash
docker compose restart api-gateway
```
