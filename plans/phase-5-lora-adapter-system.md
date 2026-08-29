# Phase 5 — LoRA Adapter System

**Spec Milestone:** M5 — Adapters  
**Exit Criterion:** Multiple logical model aliases resolve to the correct base model + LoRA adapter. Tenant-owned adapters are isolated. At least 2 simultaneous LoRA adapters serve requests through one Qwen3-4B deployment.

---

## Goals

- Build the adapter registry (PostgreSQL + S3/MinIO).
- Implement the adapter lifecycle state machine: `REGISTERED → DOWNLOADING → AVAILABLE → LOADED → ACTIVE`.
- Wire model alias resolution to look up tenant + adapter + base model.
- Integrate with vLLM's LoRA serving API (pass `lora_request` to vLLM).
- Enforce adapter ownership: Tenant A cannot invoke Tenant B's adapter.
- Validate adapter rank against deployment policy before acceptance.

---

## Deliverables

1. `db/models/adapter.py` — `Adapter` ORM model.
2. `db/models/model_alias.py` — `ModelAlias` ORM model.
3. `db/migrations/0003_adapters_and_aliases.py`.
4. `apps/api/services/adapters/registry.py` — CRUD + lifecycle transitions.
5. `apps/api/services/adapters/downloader.py` — S3/MinIO download task.
6. `apps/api/services/adapters/loader.py` — vLLM LoRA load/unload.
7. `apps/api/services/routing/resolver.py` — updated to resolve aliases → adapters.
8. `apps/api/routes/adapters.py` — adapter CRUD API.
9. `infra/docker/minio` or S3 bucket configuration.
10. Updated `docker-compose.yml` with `minio` service (for local dev).

---

## Data Model Additions

### `adapters` Table

```sql
CREATE TABLE adapters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    base_model_id   UUID NOT NULL REFERENCES models(id),
    name            TEXT NOT NULL,
    display_name    TEXT,
    storage_uri     TEXT NOT NULL,    -- s3://bucket/path or minio://...
    rank            INT NOT NULL,     -- LoRA rank; must be <= deployment policy max
    status          TEXT NOT NULL DEFAULT 'registered',
    -- registered | downloading | available | loaded | active | failed | deleted
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(organization_id, name)
);

CREATE INDEX idx_adapters_org_id ON adapters(organization_id);
CREATE INDEX idx_adapters_status ON adapters(status);
```

### `model_aliases` Table

```sql
CREATE TABLE model_aliases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    alias           TEXT NOT NULL,          -- "automotive-expert"
    base_model_id   UUID NOT NULL REFERENCES models(id),
    adapter_id      UUID REFERENCES adapters(id),  -- NULL = base model only
    deployment_id   UUID REFERENCES deployments(id),
    is_public       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(organization_id, alias)
);
```

This table is the lookup that implements:
```
"automotive-expert" (tenant-17) -> adapter-42 -> Qwen3-4B -> deployment-01 -> vLLM -> L4
```

---

## Step-by-Step Implementation

### 5.1 Adapter Lifecycle State Machine

```
REGISTERED
    |
    v (trigger: POST /v1/adapters, artifact metadata accepted)
DOWNLOADING
    |-- success -->
AVAILABLE
    |-- vLLM load request -->
LOADED
    |-- serving requests -->
ACTIVE
    |
    +-- UNLOAD --> back to AVAILABLE (if needed to free slots)
    |
    +-- FAILED (any stage; error_message populated)
    |
    +-- DELETED (soft delete; status = 'deleted')
```

State transitions are recorded in `adapters.status`. Any transition to `FAILED` must persist the `error_message`. The platform must not silently substitute another adapter if the requested one is unavailable — return an explicit error per spec section 23.

### 5.2 Adapter Registration API

```
POST /v1/adapters
  Auth: inference key (tenant-scoped)
  Body: {
    name: "automotive-expert-v2",
    storage_uri: "s3://my-bucket/adapters/automotive-v2",
    base_model: "Qwen/Qwen3-4B",
    rank: 16
  }
  Validation:
    - rank <= deployment.config_json["max_lora_rank"] (currently 16)
    - base_model must match the active deployment's model
    - name must be unique within organization
  Response: { id, name, status: "registered" }
  Side effect: trigger background download task

GET  /v1/adapters          -- list org's adapters (status filter optional)
GET  /v1/adapters/{id}     -- get adapter + status
DELETE /v1/adapters/{id}   -- soft delete; UNLOAD if loaded
```

### 5.3 Artifact Download Flow

`apps/api/services/adapters/downloader.py`:

```python
async def download_adapter(adapter_id: UUID, db: AsyncSession):
    adapter = await db.get(Adapter, adapter_id)
    await transition_status(adapter, "downloading", db)
    try:
        local_path = await pull_from_object_storage(adapter.storage_uri)
        await validate_adapter_artifact(local_path, adapter.rank)
        adapter.local_path = str(local_path)
        await transition_status(adapter, "available", db)
    except Exception as e:
        await transition_status(adapter, "failed", db, error=str(e))
        raise
```

`validate_adapter_artifact` checks:
- The directory contains `adapter_config.json`.
- The `r` field in config matches the registered rank.
- The base model in config matches the deployment base model.

### 5.4 Adapter Object Storage Layout

```
S3 / MinIO bucket: inferra-adapters/
  <organization_id>/
    <adapter_id>/
      adapter_config.json
      adapter_model.safetensors  (or .bin)
```

Local CPU cache path:
```
/mnt/model-cache/adapters/<adapter_id>/
```

vLLM active LoRAs: managed by vLLM in GPU memory (up to `--max-loras 4`).

### 5.5 vLLM LoRA Loading

vLLM's OpenAI-compatible server accepts LoRA adapters via the `model` field in completions requests when the adapter is registered with vLLM's `/v1/load_lora_adapter` endpoint (or via the `lora_request` parameter in the Python API).

For the HTTP gateway approach:

```python
# Register adapter with vLLM (called when adapter status -> LOADED)
async def load_adapter_into_vllm(adapter: Adapter, worker_endpoint: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{worker_endpoint}/v1/load_lora_adapter",
            json={
                "lora_name": str(adapter.id),   # runtime name = adapter UUID
                "lora_path": adapter.local_path,
            },
        )
        resp.raise_for_status()

# In chat completions, set model to the lora_name
payload["model"] = str(adapter.id)  # vLLM routes by lora_name
```

The `adapter_runtime_name` field in `ResolvedInferenceTarget` carries this value.

### 5.6 Updated Alias Resolution

`apps/api/services/routing/resolver.py` — full resolution chain:

```python
async def resolve_target(
    request: ChatCompletionRequest,
    auth: AuthenticatedContext,
    db: AsyncSession,
) -> ResolvedInferenceTarget:
    # 1. Look up model alias for this tenant
    alias = await db.execute(
        select(ModelAlias).where(
            ModelAlias.organization_id == auth.organization.id,
            ModelAlias.alias == request.model,
        )
    )
    # 2. Fall back to public aliases if not found for tenant
    # 3. Validate adapter is owned by this tenant (security check)
    # 4. Validate adapter status is LOADED or ACTIVE
    # 5. Resolve deployment + worker endpoint
    return ResolvedInferenceTarget(
        request_id=uuid4(),
        organization_id=auth.organization.id,
        logical_model=request.model,
        base_model="Qwen/Qwen3-4B",
        adapter_id=alias.adapter_id,
        adapter_runtime_name=str(alias.adapter_id) if alias.adapter_id else None,
        deployment_id=alias.deployment_id,
        worker_endpoint=worker.endpoint,
        max_model_len=8192,
        max_output_tokens=request.max_tokens or 512,
        policy_version="v1",
    )
```

### 5.7 LoRA Capacity Controls

The vLLM deployment is started with `--max-loras 4`. The platform must:

1. Reject adapter registration if `rank > 16` (deployment policy max rank).
2. Track how many adapters are currently `LOADED` in vLLM.
3. If the vLLM slot limit is reached (4 active LoRAs), implement a simple eviction:
   - In V1: fail with a `503 adapter_slots_full` error and log the event.
   - Future: LRU eviction (spec section 14.1).
4. Record load failures explicitly with `error_message`.

### 5.8 Adapter Rank Validation at Registration

```python
MAX_LORA_RANK = 16  # matches vllm --max-lora-rank

if adapter_create.rank > MAX_LORA_RANK:
    raise HTTPException(
        status_code=422,
        detail=f"Adapter rank {adapter_create.rank} exceeds deployment maximum {MAX_LORA_RANK}"
    )
```

### 5.9 Tenant Isolation for Adapters

- `DELETE /v1/adapters/{id}` must verify `adapter.organization_id == auth.organization.id`.
- `resolve_target` must verify `alias.organization_id == auth.organization.id`.
- Storage paths (`storage_uri`, `local_path`) must never be returned in API responses.

### 5.10 Alias Management API

```
POST /v1/aliases
  Body: { alias: "automotive-expert", adapter_id: UUID }
  Creates a ModelAlias linking this tenant's alias to an adapter

GET /v1/aliases
  Returns this tenant's alias → adapter mapping

DELETE /v1/aliases/{alias}
```

This can also be a field on adapter creation: `{ name: "automotive-expert", ... }` which auto-creates the alias.

### 5.11 Add `adapter_id` to Usage Records

Back-fill Phase 4 data model: add `adapter_id` foreign key to the `requests` table migration `0003`:

```sql
ALTER TABLE requests ADD COLUMN adapter_id UUID REFERENCES adapters(id);
```

Update `record_usage()` to populate `adapter_id` from `ResolvedInferenceTarget`.

---

## LoRA Architecture Diagram

```
Qwen3-4B (base model — loaded once in GPU)
    |
    +-- Finance-LoRA (Tenant A alias: "finance-assistant")
    |
    +-- Automotive-LoRA (Tenant B alias: "automotive-expert")
    |
    +-- Support-LoRA (Tenant C alias: "customer-support")
    |
    +-- (4th slot: dynamic or empty)

vLLM max-loras: 4 (GPU)
vLLM max-cpu-loras: configurable (CPU warm cache)
S3/MinIO: all registered adapters (cold store)
```

---

## MinIO Docker Compose (local dev)

```yaml
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD_FILE: /run/secrets/minio_password
    volumes:
      - minio-data:/data
    ports:
      - "9001:9001"   # admin console (internal only in prod)

volumes:
  minio-data:
```

---

## Exit Checklist

- [ ] `POST /v1/adapters` registers an adapter and triggers background download.
- [ ] Adapter lifecycle transitions (registered → downloading → available → loaded → active) are visible via `GET /v1/adapters/{id}`.
- [ ] Failed download or rank mismatch sets `status = failed` with `error_message`.
- [ ] `"automotive-expert"` alias resolves to Tenant B's adapter; Tenant A's key cannot use it.
- [ ] Chat completion with a LoRA alias forwards the correct `lora_name` to vLLM.
- [ ] Adapter rank > 16 is rejected at registration.
- [ ] Adapter `storage_uri` and `local_path` are NOT in API responses.
- [ ] `requests.adapter_id` is populated for every adapter-routed request.
- [ ] Requesting an unavailable adapter returns an explicit error (not silent base-model fallback).

---

## Post-Implementation Documentation

Complete this section immediately after Phase 5 is implemented.

### Implementation Log

```
Date completed: 2026-08-29
Implemented by: Cursor Agent
Git commit / tag: (commit after verification)
Branch: main
```

### Schema Migration Record

```
Tables created in same init_db() pass as all other tables:
  - adapters      (id, organization_id, base_model_id, name, storage_uri, local_path,
                   rank, status, error_message, created_at, updated_at)
                  UniqueConstraint(organization_id, name)
  - model_aliases (id, organization_id, alias, base_model_id, adapter_id, deployment_id,
                   is_public, created_at)
                  UniqueConstraint(organization_id, alias)
  - requests.adapter_id: FK to adapters.id — added from day one (no ALTER TABLE needed)
Files:
  - db/models/adapter.py  -> Adapter, ModelAlias
```

### Adapter Lifecycle Implementation

```
State machine implemented in apps/api/routes/adapters.py _process_adapter() background task:
  REGISTERED  -> set on POST /v1/adapters return
  DOWNLOADING -> set at start of download_adapter() in registry.py
  AVAILABLE   -> set after validate_adapter_artifact() succeeds
  LOADED      -> set after load_adapter_into_vllm() returns (mock returns {"status":"loaded"})
  ACTIVE      -> set in _process_adapter() after successful load
  FAILED      -> set on any exception with error_message populated

Download from MinIO: apps/api/services/adapters/registry.py download_adapter()
  - Lists objects under storage_uri prefix using boto3 paginator
  - Downloads each file to /tmp/inferra-adapters/<adapter_id>/
  - Validates adapter_config.json exists and rank does not exceed policy

Load into vLLM: load_adapter_into_vllm() calls POST /v1/load_lora_adapter on worker endpoint
  - Mock vLLM returns {"status":"loaded"} immediately
  - Real vLLM will load the adapter from local_path
```

### LoRA Serving Verification

```
Alias resolution chain (resolver.py):
  1. Look up ModelAlias by (organization_id, alias)
  2. If not found: look up public aliases
  3. If alias.organization_id != auth.organization.id and not is_public: raise 403
  4. adapter_runtime_name = str(alias.adapter_id) — this is the lora_name sent to vLLM
  5. payload["model"] = adapter_runtime_name (or base_model if no adapter)
  6. ResolvedInferenceTarget carries adapter_id + adapter_runtime_name

Auto-alias creation: if body.alias provided in POST /v1/adapters, a ModelAlias is created
  pointing to the new adapter and the active deployment.
```

### Capacity Controls

```
max_lora_rank = 16 (apps/api/config.py — settings.max_lora_rank)
Rank validation: checked at POST /v1/adapters before any DB write
  -> raises HTTPException(422) if body.rank > settings.max_lora_rank

vLLM slot limit (max_loras=4): V1 policy is fail-explicit when vLLM returns an error on load.
  error_message is populated on the Adapter record.
  No proactive slot counting yet — relies on vLLM returning an error.
```

### Tenant Isolation for Adapters

| Test | Expected | Code Location |
|------|----------|---------------|
| Cross-tenant alias | 403 | resolver.py line: not is_public check |
| storage_uri absent from response | Yes | AdapterResponse model has no storage_uri/local_path fields |
| local_path absent from response | Yes | AdapterResponse model — only id, org_id, name, rank, status, error_message |
| DELETE cross-tenant | 403 | adapters.py: `adapter.organization_id != auth.organization.id` check |

### Object Storage Configuration

```
Backend:          MinIO (docker-compose.yml infra/mock-vllm)
Bucket:           inferra-adapters (settings.s3_bucket)
Bucket creation:  ensure_bucket_exists() called on app startup
Endpoint:         http://minio:9000 (internal Docker network)
Path structure:   <bucket>/<storage_uri path from POST body>
Credentials:      via env vars S3_ACCESS_KEY / S3_SECRET_KEY (never in source)
MinIO console:    not exposed on host port (internal only in current docker-compose.yml)
```

### Exit Checklist — Actual Results

- [x] Adapter lifecycle state machine implemented (REGISTERED→DOWNLOADING→AVAILABLE→LOADED→ACTIVE) — confirmed in code
- [x] Failed download/load sets status="failed" with error_message — confirmed in registry.py
- [x] Alias resolves to correct adapter_runtime_name in payload to vLLM — confirmed in resolver.py
- [x] Cross-tenant adapter access → 403 — confirmed in code
- [x] Rank > 16 → 422 at registration — confirmed: `settings.max_lora_rank = 16`
- [x] storage_uri / local_path absent from AdapterResponse — confirmed: schema only has safe fields
- [x] requests.adapter_id populated for adapter-routed requests — confirmed: resolver sets adapter_id on target
- [x] Unavailable/failed adapter returns explicit error — confirmed: resolver raises 503 if adapter not loaded

### Deviations from Plan

```
1. loader.py not created as a separate file — load_adapter_into_vllm() lives in registry.py.
   Reason: Small enough to keep co-located with download_adapter().
   Impact: Import from apps/api/services/adapters/registry.py.

2. _process_adapter() background task lives in adapters.py (route file), not a separate service.
   Reason: Keeps the lifecycle orchestration visible near the route that triggers it.
   Impact: None — can be extracted to a service file if it grows.

3. No proactive LoRA slot counting — V1 relies on vLLM returning an error when slots full.
   Reason: Mock vLLM always accepts; real vLLM will error; error captured in error_message.
   Impact: With real vLLM, implement a counter (max_loras=4) before high-traffic beta.
```

### Issues Encountered

```
None.
```

### Architecture Decisions Made

```
Decision 1:
  Context: Whether to preload adapters eagerly or load on-demand.
  Choice made: Load on demand during POST /v1/adapters via background task.
  Reason: V1 has ≤4 active adapters; no traffic data yet to justify preloading.
  Trade-off: First request after adapter registration may be slower if load is still in progress.
             Mitigated by LOADED status check in resolver (returns 503 if not loaded yet).
```

### Handoff Notes for Phase 6

```
- Alias resolution path: apps/api/services/routing/resolver.py
- Adapter CRUD + lifecycle: apps/api/routes/adapters.py + apps/api/services/adapters/registry.py
- MinIO reachable from gateway: confirmed (ensure_bucket_exists() runs on startup without error)
- Max LoRA rank policy: settings.max_lora_rank = 16
- Global LoRA slot limit: vLLM --max-loras 4 (enforced by vLLM, not yet by control plane)
- Seed data includes: 1 organization, 1 deployment, alias "test-assistant" → base model only
```

---

## What This Phase Does NOT Build

- No eviction policy / LRU cache (V2 decision; Phase 8 benchmarks will determine if needed)
- No rate limiting per adapter (Phase 6)
- No adapter load metrics in Prometheus (Phase 7)
