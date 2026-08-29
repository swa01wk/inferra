# LoRA Adapters

Inferra supports serving fine-tuned LoRA adapters alongside the base model without restarting vLLM. Each adapter is scoped to an organization and accessed via a user-defined alias.

---

## What Are LoRA Adapters?

LoRA (Low-Rank Adaptation) is a parameter-efficient fine-tuning technique that trains small rank-decomposed weight matrices on top of a frozen base model. The adapter weights are ~10–100 MB instead of the full model's ~8 GB.

In Inferra, adapters:
- Are stored as files in MinIO (S3-compatible object storage)
- Are downloaded to the gateway host's local cache at registration time
- Are loaded into the running vLLM process via the vLLM LoRA API
- Are accessible via a named alias in chat completions

**V1 Limits:**
- Maximum 4 adapters loaded concurrently in vLLM
- Maximum LoRA rank: 16
- Base model: Qwen/Qwen3-4B only

---

## Adapter Lifecycle

```
POST /v1/adapters
        │
        ▼
   registered        ← DB row created, background task queued
        │
        ▼
   downloading       ← S3/MinIO download in progress
        │
     ┌──┴──────────────────┐
     │                     │
     ▼                     ▼
  available           failed (download/validation error)
     │
     ▼
   loaded             ← vLLM load_lora_adapter() succeeded
     │
     ▼
   active             ← confirmed ready for traffic
     │
     ▼
   deleted            ← soft-deleted via DELETE /v1/adapters/{id}
```

Use `GET /v1/adapters/{id}` to poll the `status` field.

---

## Step 1: Upload Adapter Artifacts to MinIO

Your LoRA adapter must be uploaded to MinIO (or any S3-compatible bucket) before registering it with Inferra.

### Using the MinIO Console

1. Open `http://localhost:9001` in your browser
2. Login: `minioadmin` / `minioadmin`
3. Create or use the bucket `inferra-adapters`
4. Upload your adapter files under a prefix, e.g., `my-org/adapter-v1/`

Required files in the adapter directory:
```
adapter_config.json     ← must contain "r" or "rank" field
adapter_model.safetensors  (or .bin equivalent)
```

`adapter_config.json` example:
```json
{
  "base_model_name_or_path": "Qwen/Qwen3-4B",
  "r": 16,
  "lora_alpha": 32,
  "target_modules": ["q_proj", "v_proj"],
  "task_type": "CAUSAL_LM"
}
```

### Using the AWS CLI (pointed at MinIO)

```bash
# Configure CLI to point at MinIO
aws configure set aws_access_key_id minioadmin
aws configure set aws_secret_access_key minioadmin

# Upload adapter files
aws s3 cp ./my-adapter/ s3://inferra-adapters/my-org/adapter-v1/ \
  --recursive \
  --endpoint-url http://localhost:9000
```

### Using boto3

```python
import boto3

s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
)

s3.upload_file("adapter_config.json", "inferra-adapters", "my-org/adapter-v1/adapter_config.json")
s3.upload_file("adapter_model.safetensors", "inferra-adapters", "my-org/adapter-v1/adapter_model.safetensors")
```

---

## Step 2: Register the Adapter

```bash
curl -X POST http://localhost:9100/v1/adapters \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-fine-tuned-v1",
    "storage_uri": "s3://inferra-adapters/my-org/adapter-v1/",
    "base_model": "Qwen/Qwen3-4B",
    "rank": 16,
    "alias": "my-assistant"
  }'
```

Response (immediate — loading is async):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440001",
  "organization_id": "...",
  "name": "my-fine-tuned-v1",
  "base_model_id": "...",
  "rank": 16,
  "status": "registered",
  "error_message": null,
  "created_at": "2026-08-29T14:01:23.456789+00:00"
}
```

The `alias` field automatically creates a `ModelAlias` pointing to this adapter — so once the adapter reaches `active` status, you can use `"model": "my-assistant"` in chat completions.

---

## Step 3: Poll Until Active

```bash
ADAPTER_ID="550e8400-e29b-41d4-a716-446655440001"

curl http://localhost:9100/v1/adapters/$ADAPTER_ID \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY"
```

Wait until `status` is `active`. Typical timing on a real GPU:
- `registered → downloading`: < 1s (background task start)
- `downloading → available`: 10–60s (depends on adapter size and S3 speed)
- `available → active`: 5–30s (vLLM load time)

If `status` becomes `failed`, check `error_message` for the reason.

---

## Step 4: Use the Adapter in Inference

Once `status=active`, use the alias you specified (or the adapter's UUID directly):

```bash
# Via alias
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "my-assistant",
    "messages": [{"role": "user", "content": "Hello, what can you do?"}],
    "max_tokens": 256
  }'
```

The routing resolver will:
1. Look up `ModelAlias` where `alias='my-assistant'`
2. Find the associated `adapter_id`
3. Pass `lora_request={"lora_name": "<adapter_id>"}` to vLLM

---

## Managing Aliases Separately

You can create aliases without registering a new adapter (e.g., to point an alias at an existing adapter or at the base model):

```bash
# Alias pointing to base model (no adapter)
curl -X POST http://localhost:9100/v1/aliases \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "alias": "qwen-base",
    "base_model": "Qwen/Qwen3-4B"
  }'

# Alias pointing to an existing adapter
curl -X POST http://localhost:9100/v1/aliases \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "alias": "my-assistant-v2",
    "base_model": "Qwen/Qwen3-4B",
    "adapter_id": "550e8400-e29b-41d4-a716-446655440001"
  }'
```

---

## Listing Adapters

```bash
curl http://localhost:9100/v1/adapters \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY"
```

Returns all non-deleted adapters for your organization.

---

## Deleting an Adapter

Soft-delete an adapter (the record remains in the database but `status='deleted'`):

```bash
curl -X DELETE http://localhost:9100/v1/adapters/$ADAPTER_ID \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY"
```

**Note:** Deleting an adapter does not automatically unload it from vLLM's memory. In V1, vLLM must be restarted to fully free the VRAM occupied by a deleted adapter. The adapter will simply stop receiving traffic via its alias.

---

## Adapter Validation

When downloading, Inferra validates the adapter artifact:

1. **`adapter_config.json` must exist** — if missing, the adapter moves to `failed`
2. **Rank check** — the `r` (or `rank`) field in `adapter_config.json` must be ≤ `MAX_LORA_RANK` (16)

If validation fails, `error_message` will contain the reason.

---

## Storage URI Format

Inferra supports two URI schemes:

| Scheme | Example | Description |
|--------|---------|-------------|
| `s3://` | `s3://my-bucket/path/to/adapter/` | Standard S3 |
| `minio://` | `minio://inferra-adapters/my-org/adapter-v1/` | MinIO (treated identically to s3://) |

The path after the bucket name is used as a prefix to list and download all objects.

---

## Error Reference

### `400 Base model not found`

The `base_model` field must match a model in the platform catalog (seeded by `seed_dev_data.py`). Currently only `Qwen/Qwen3-4B` is supported in V1.

### `409 Adapter name already exists`

Adapter names are unique per organization. Use a different `name` or delete the existing adapter first.

### `422 Adapter rank N exceeds deployment maximum 16`

Reduce the `rank` field to 16 or below.

### `status: "failed"` after registration

Check `error_message` for the root cause. Common issues:
- `"No adapter artifacts found at storage URI"` — the `storage_uri` prefix returned no objects; verify the path in MinIO
- `"adapter_config.json not found"` — the uploaded directory is missing this required file
- `"Adapter rank N exceeds policy maximum"` — the rank in `adapter_config.json` is > 16
- `"No active deployment"` — no running vLLM deployment; check worker status
- `"No worker available"` — worker not found; verify `seed_real_worker.py` was run

---

## Python Helper: Full Registration Flow

```python
import time
import httpx

BASE_URL = "http://localhost:9100"
INFERENCE_KEY = "inf_your_key_here"

headers = {
    "Authorization": f"Bearer {INFERENCE_KEY}",
    "Content-Type": "application/json",
}

def register_adapter(name: str, storage_uri: str, alias: str, rank: int = 16) -> str:
    resp = httpx.post(
        f"{BASE_URL}/v1/adapters",
        headers=headers,
        json={
            "name": name,
            "storage_uri": storage_uri,
            "base_model": "Qwen/Qwen3-4B",
            "rank": rank,
            "alias": alias,
        },
    )
    resp.raise_for_status()
    adapter_id = resp.json()["id"]
    print(f"Registered adapter {adapter_id}, polling...")

    for _ in range(60):  # poll up to 5 minutes
        time.sleep(5)
        status_resp = httpx.get(
            f"{BASE_URL}/v1/adapters/{adapter_id}",
            headers=headers,
        )
        data = status_resp.json()
        status = data["status"]
        print(f"  status: {status}")
        if status == "active":
            print(f"Adapter {name} is ready!")
            return adapter_id
        if status == "failed":
            raise RuntimeError(f"Adapter failed: {data.get('error_message')}")

    raise TimeoutError("Adapter did not become active within 5 minutes")


adapter_id = register_adapter(
    name="my-fine-tuned-v1",
    storage_uri="s3://inferra-adapters/my-org/adapter-v1/",
    alias="my-assistant",
)

# Use it
resp = httpx.post(
    f"{BASE_URL}/v1/chat/completions",
    headers=headers,
    json={
        "model": "my-assistant",
        "messages": [{"role": "user", "content": "Hello!"}],
        "max_tokens": 128,
    },
)
print(resp.json()["choices"][0]["message"]["content"])
```
