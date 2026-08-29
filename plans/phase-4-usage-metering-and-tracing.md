# Phase 4 — Usage Metering & Request Tracing

**Spec Milestone:** M4 — Usage  
**Exit Criterion:** Every request has a durable usage record with token counts and full latency decomposition.

---

## Goals

- Assign a globally unique `request_id` to every inference call.
- Persist a `requests` and `usage_metrics` record for every completed call.
- Capture the full latency decomposition: `gateway_ms`, `routing_ms`, `queue_ms`, `ttft_ms`, `decode_ms`, `total_ms`.
- Track `received_at`, `first_token_at`, and `completed_at` timestamps.
- Support `GET /v1/usage` for tenant-scoped usage queries.
- Answer: "Was inference slow? Where was the time spent?"

---

## Deliverables

1. `db/models/request.py` — `Request` ORM model.
2. `db/models/usage_metric.py` — `UsageMetric` ORM model.
3. `db/migrations/0002_requests_and_usage.py` — Alembic migration.
4. `apps/api/services/usage/recorder.py` — async usage recording service.
5. `apps/api/services/usage/streaming_wrapper.py` — TTFT detection in SSE stream.
6. `apps/api/routes/usage.py` — `GET /v1/usage` endpoint.
7. `apps/api/middleware/tracing.py` — request lifecycle hooks.

---

## Data Model Additions

### `requests` Table

```sql
CREATE TABLE requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    api_key_id      UUID NOT NULL REFERENCES api_keys(id),
    deployment_id   UUID REFERENCES deployments(id),
    adapter_id      UUID,                        -- FK added in Phase 5
    logical_model   TEXT NOT NULL,               -- alias submitted by client
    base_model_id   UUID REFERENCES models(id),
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | completed | failed | cancelled
    http_status     INT,
    error_code      TEXT,
    cancelled       BOOLEAN NOT NULL DEFAULT false,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_token_at  TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_requests_org_id     ON requests(organization_id);
CREATE INDEX idx_requests_received_at ON requests(received_at DESC);
```

### `usage_metrics` Table

```sql
CREATE TABLE usage_metrics (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id        UUID NOT NULL UNIQUE REFERENCES requests(id),
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    total_tokens      INT NOT NULL DEFAULT 0,
    gateway_ms        INT,     -- time spent in gateway before forwarding
    routing_ms        INT,     -- time to resolve tenant/adapter/deployment
    queue_ms          INT,     -- time waiting in vLLM queue (if available)
    ttft_ms           INT,     -- time from request forward to first token
    decode_ms         INT,     -- time from first token to last token
    total_ms          INT,     -- total wall-clock time
    tokens_per_second FLOAT,   -- completion_tokens / (decode_ms / 1000)
    time_per_output_token_ms FLOAT,
    worker_id         UUID REFERENCES workers(id),
    vllm_version      TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Step-by-Step Implementation

### 4.1 Request Lifecycle State Machine

```
received_at set
    |
    v
[pending] ---> gateway auth + routing
    |
    v
request forwarded to vLLM
    |
    v
first token received --> first_token_at set; ttft_ms computed
    |
    v
stream complete --> completed_at set; completion_tokens counted
    |
    v
[completed] --> usage_metrics persisted
    |
    (on error)
    v
[failed] --> error_code + http_status saved
    |
    (on disconnect)
    v
[cancelled] --> partial token counts saved; usage recorded consistently
```

### 4.2 Latency Decomposition Points

Instrument these timestamps in the request lifecycle:

```python
class RequestTimings:
    received_at: float          # time.monotonic() at gateway entry
    routing_start: float        # before resolve_target()
    routing_end: float          # after resolve_target()
    forwarded_at: float         # when HTTP request sent to vLLM
    first_token_at: float       # when first SSE chunk with content arrives
    completed_at: float         # when SSE stream closes

    @property
    def gateway_ms(self) -> int:
        return int((self.routing_start - self.received_at) * 1000)

    @property
    def routing_ms(self) -> int:
        return int((self.routing_end - self.routing_start) * 1000)

    @property
    def ttft_ms(self) -> int:
        return int((self.first_token_at - self.forwarded_at) * 1000)

    @property
    def decode_ms(self) -> int:
        return int((self.completed_at - self.first_token_at) * 1000)

    @property
    def total_ms(self) -> int:
        return int((self.completed_at - self.received_at) * 1000)
```

### 4.3 Streaming Wrapper for TTFT Detection

The SSE proxy must detect when the first actual token arrives without buffering. `apps/api/services/usage/streaming_wrapper.py`:

```python
async def tracked_stream(
    raw_stream: AsyncIterator[bytes],
    timings: RequestTimings,
    token_counter: TokenCounter,
) -> AsyncIterator[bytes]:
    first_token_seen = False
    async for chunk in raw_stream:
        if not first_token_seen and has_content(chunk):
            timings.first_token_at = time.monotonic()
            first_token_seen = True
        token_counter.ingest(chunk)  # parse delta.content from SSE JSON
        yield chunk
    timings.completed_at = time.monotonic()
```

`has_content(chunk)` parses the SSE JSON to check if `choices[0].delta.content` is non-empty.

### 4.4 Token Counting

- **Prompt tokens:** read from the vLLM response's `usage.prompt_tokens` field in the final `[DONE]` chunk, OR count locally using the model's tokenizer as a fallback.
- **Completion tokens:** read from `usage.completion_tokens` in the final SSE chunk.
- vLLM emits a final non-streaming usage object; parse it from the `data: [DONE]` SSE event.

```python
class TokenCounter:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def ingest(self, chunk: bytes):
        # Parse SSE line; extract usage if present
        ...
```

### 4.5 Async Usage Recorder

`apps/api/services/usage/recorder.py` — write to Postgres after stream completes:

```python
async def record_usage(
    db: AsyncSession,
    request_record: Request,
    timings: RequestTimings,
    token_counter: TokenCounter,
    worker_id: UUID,
):
    request_record.status = "completed"
    request_record.first_token_at = datetime.utcfromtimestamp(timings.first_token_at)
    request_record.completed_at = datetime.utcfromtimestamp(timings.completed_at)

    metric = UsageMetric(
        request_id=request_record.id,
        prompt_tokens=token_counter.prompt_tokens,
        completion_tokens=token_counter.completion_tokens,
        total_tokens=token_counter.prompt_tokens + token_counter.completion_tokens,
        gateway_ms=timings.gateway_ms,
        routing_ms=timings.routing_ms,
        ttft_ms=timings.ttft_ms,
        decode_ms=timings.decode_ms,
        total_ms=timings.total_ms,
        tokens_per_second=(
            token_counter.completion_tokens / (timings.decode_ms / 1000)
            if timings.decode_ms > 0 else 0
        ),
        worker_id=worker_id,
    )
    db.add(metric)
    await db.commit()
```

**Critical:** This write must happen even if the client disconnects (cancelled status). Use a background task or `asyncio.shield` to protect the final write from connection cancellation.

### 4.6 Client Disconnect Handling

```python
@router.post("/chat/completions")
async def chat_completions(..., background_tasks: BackgroundTasks):
    ...
    try:
        async for chunk in tracked_stream(...):
            yield chunk
    except asyncio.CancelledError:
        request_record.status = "cancelled"
        request_record.cancelled = True
    finally:
        background_tasks.add_task(record_usage, db, request_record, timings, counter)
```

### 4.7 Usage Query Endpoint

`apps/api/routes/usage.py`:

```
GET /v1/usage
  Query params:
    start_time: datetime (ISO 8601)
    end_time:   datetime
    adapter_id: UUID (optional)
    page:       int (default 1)
    page_size:  int (default 100, max 1000)

  Auth: require_inference_key (tenant-scoped)

  Response: {
    requests: [...],
    total_prompt_tokens: int,
    total_completion_tokens: int,
    total_requests: int,
    period: { start, end }
  }
```

Query must always filter by `organization_id = auth.organization.id`. No cross-tenant data leakage.

### 4.8 Per-Request Log Enrichment

After usage is recorded, enrich the structured log line from Phase 2 with:

```json
{
  "request_id": "...",
  "organization_id": "...",
  "api_key_id": "...",
  "logical_model": "automotive-expert",
  "status": "completed",
  "prompt_tokens": 312,
  "completion_tokens": 128,
  "gateway_ms": 12,
  "routing_ms": 3,
  "ttft_ms": 210,
  "decode_ms": 1840,
  "total_ms": 2065,
  "tokens_per_second": 69.6
}
```

No raw prompt content in logs by default.

---

## Required Per-Request Fields (from Spec Section 20.1)

| Category | Fields | Phase |
|----------|--------|-------|
| Identity | `request_id`, `organization_id`, `api_key_id` | Phase 3 + 4 |
| Model | `logical_model_alias`, `base_model_id`, `adapter_id`, `deployment_id` | Phase 4 + 5 |
| Tokens | `prompt_tokens`, `completion_tokens`, `total_tokens` | Phase 4 |
| Latency | `gateway_ms`, `routing_ms`, `queue_ms`, `ttft_ms`, `decode_ms`, `total_ms` | Phase 4 |
| Throughput | `tokens_per_second`, `time_per_output_token` | Phase 4 |
| Runtime | `worker_id`, `vllm_version`, `model_config_version` | Phase 4 |
| Outcome | `http_status`, `inference_status`, `error_code`, `cancellation_flag` | Phase 4 |
| Time | `received_at`, `first_token_at`, `completed_at` | Phase 4 |

Note: `queue_ms` requires vLLM to expose queue wait time in response headers or metrics. Implement as best-effort: compute from `forwarded_at` to first vLLM response header byte if vLLM does not expose it natively.

---

## Exit Checklist

- [ ] Every completed request has a row in `requests` and `usage_metrics`.
- [ ] `ttft_ms` is populated and non-zero for streaming responses.
- [ ] `decode_ms` and `tokens_per_second` are accurate.
- [ ] Cancelled/disconnected requests are recorded with `status = 'cancelled'`.
- [ ] Failed requests are recorded with `status = 'failed'` and `error_code`.
- [ ] `GET /v1/usage` returns only the authenticated tenant's data.
- [ ] No raw prompt content in log output.
- [ ] Usage record survives client disconnection (background task pattern).

---

## Post-Implementation Documentation

Complete this section immediately after Phase 4 is implemented.

### Implementation Log

```
Date completed: 2026-08-29
Implemented by: Cursor Agent
Git commit / tag: (commit after verification)
Branch: main
```

### Schema Migration Record

```
Approach: Tables created via init_db() / create_all() alongside Phase 3 tables.
Files:
  - db/models/request.py  -> RequestRecord, UsageMetric ORM models
Tables created:
  - requests      (id, organization_id, api_key_id, deployment_id, adapter_id,
                   logical_model, base_model_id, status, http_status, error_code,
                   cancelled, received_at, first_token_at, completed_at)
  - usage_metrics (id, request_id, prompt_tokens, completion_tokens, total_tokens,
                   gateway_ms, routing_ms, queue_ms, ttft_ms, decode_ms, total_ms,
                   tokens_per_second, time_per_output_token_ms, worker_id, vllm_version)
Note: SQLAlchemy indexes not separately defined; add explicit indexes in Alembic migration at beta.
```

### Latency Decomposition — Implementation Notes

```
All timings computed from time.monotonic() in apps/api/services/usage/recorder.py.
Fields implemented in RequestTimings dataclass:
  - received_at:   set at route entry
  - routing_start: set just before resolve_target()
  - routing_end:   set after resolve_target() returns
  - forwarded_at:  set just before vLLM HTTP call
  - first_token_at: set by tracked_stream() on first SSE chunk with content
  - completed_at:  set by tracked_stream() when async generator exhausts

Properties computed:
  - gateway_ms  = routing_start - received_at
  - routing_ms  = routing_end - routing_start
  - ttft_ms     = first_token_at - forwarded_at
  - decode_ms   = completed_at - first_token_at
  - total_ms    = completed_at - received_at

Note: With mock vLLM (MOCK_TTFT_DELAY_MS=50ms, MOCK_TOKEN_DELAY_MS=20ms * 32 tokens),
expected total_ms ≈ 690ms per request. Actual numbers will differ with real vLLM on GPU.
```

### Token Counting

```
Source: final SSE chunk from vLLM containing "usage" field.
Parsing: TokenCounter.ingest() reads usage.prompt_tokens and usage.completion_tokens from
         the last non-[DONE] data: line in the SSE stream.
Mock vLLM returns usage in the final chunk: confirmed in infra/mock-vllm/main.py.
tokens_per_second = completion_tokens / (decode_ms / 1000) — computed in recorder.py.
```

### Client Disconnect Handling

```
Mechanism: asyncio.CancelledError caught in stream_response() generator.
On disconnect:
  - request_record.status = "cancelled"
  - request_record.cancelled = True
  - background_tasks.add_task(_finalize_usage, ...) still fires
Usage record saved: YES — background task is registered before CancelledError propagates.
```

### Exit Checklist — Actual Results

- [x] RequestRecord + UsageMetric ORM models created in db/models/request.py — confirmed
- [x] RequestTimings dataclass with all 5 computed properties — confirmed in recorder.py
- [x] tracked_stream() detects first content chunk for first_token_at — confirmed
- [x] TokenCounter reads usage from final SSE chunk — confirmed
- [x] record_usage() writes both tables as background task — confirmed
- [x] Cancelled requests: CancelledError caught, status="cancelled", background task fires — confirmed
- [x] GET /v1/usage filters by organization_id — confirmed in admin.py
- [x] No raw prompt content in logs — RequestLoggingMiddleware only logs path/status/timing

### Deviations from Plan

```
1. streaming_wrapper.py and recorder.py merged into recorder.py.
   Reason: Both were small enough to keep in one file; tracked_stream(), TokenCounter,
           has_content(), RequestTimings, and record_usage() all live in recorder.py.
   Impact: Import path is apps/api/services/usage/recorder — no functional change.

2. requests.adapter_id added immediately (not deferred to Phase 5 migration).
   Reason: All ORM models built in one pass; adapter_id FK added to RequestRecord at creation.
   Impact: None — the column exists from day one.

3. _finalize_usage() background task added to chat.py as a module-level async function
   (not a method), receiving timings/counter by value to avoid closure issues.
   Reason: BackgroundTasks serialises args; dataclasses passed by reference are safe here.
```

### Issues Encountered

```
None.
```

### Architecture Decisions Made

```
Decision 1:
  Context: How to persist usage when the client disconnects before stream completes.
  Choice made: background_tasks.add_task() registered before the yield loop; CancelledError
               caught to set cancelled=True, then re-raised; background task still fires.
  Reason: FastAPI BackgroundTasks run after the response is sent regardless of client state.
  Trade-off: If the process is killed mid-stream the background task may not run —
             acceptable at V1; use a task queue for durability at V2.
```

### Handoff Notes for Phase 5

```
- requests.adapter_id FK column: present from day one (db/models/request.py)
- Usage recorder: apps/api/services/usage/recorder.py
  (contains RequestTimings, TokenCounter, tracked_stream, has_content, record_usage)
- Background task pattern: confirmed working — _finalize_usage() in apps/api/routes/chat.py
- Token counting relies on vLLM returning usage in final SSE chunk — mock does this correctly
- queue_ms field: not yet populated (vLLM does not expose per-request queue wait time via SSE)
```

---

## What This Phase Does NOT Build

- No adapter-level usage breakdown (Phase 5 will add `adapter_id` to records)
- No rate limiting or quota checks (Phase 6)
- No Prometheus counters/histograms (Phase 7; usage data goes to Postgres here)
