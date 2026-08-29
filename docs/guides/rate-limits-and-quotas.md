# Rate Limits & Quotas

Inferra enforces four layered controls to prevent any single tenant from overwhelming the system. Each control is checked in order — if any check fails, the request is rejected before reaching vLLM.

---

## Overview

| Control | Scope | Default | Redis Key | Error |
|---------|-------|---------|-----------|-------|
| Input token ceiling | Per request | 8,192 tokens | (no Redis) | `400` |
| Requests per minute (RPM) | Per organization | 60 req/min | `rl:rpm:<org_id>` | `429` |
| Concurrent requests | Per organization | 5 simultaneous | `rl:concurrent:<org_id>` | `429` |
| Daily token quota | Per organization | 1,000,000 tokens | `rl:daily_tokens:<org_id>:<date>` | `429` |
| Global queue depth | System-wide | 50 in-flight | `rl:queue_depth` | `503` |

---

## Admission Control Flow

Every request to `POST /v1/chat/completions` passes through these gates in order:

```
Request arrives
      │
      ▼
1. Input token ceiling
   estimate = word_count(messages)
   if estimate > policy.max_input_tokens → 400
   if max_tokens > policy.max_output_tokens → cap max_tokens silently
      │
      ▼
2. Context ceiling (routing resolver)
   if estimate + max_tokens > 8192 → 400
      │
      ▼
3. RPM limit (Redis Lua, atomic)
   INCR rl:rpm:<org_id>  (TTL=60s on first write)
   if count > rpm_limit → 429 + Retry-After: 1
      │
      ▼
4. Concurrency limit (Redis INCR)
   INCR rl:concurrent:<org_id>  (TTL=300s)
   if count > max_concurrent → DECR + 429
      │
      ▼
5. Daily token quota (Redis GET)
   GET rl:daily_tokens:<org_id>:<YYYY-MM-DD>
   if current + estimated > daily_hard_limit → DECR concurrent + 429 + Retry-After: 86400
      │
      ▼
6. Global queue depth
   INCR rl:queue_depth
   if depth > 50 → DECR concurrent + 503 + Retry-After: 5
      │
      ▼
   Request forwarded to vLLM
      │
   [Response complete or error]
      │
      ▼
   release_admission()
   DECR rl:concurrent:<org_id>
   DECR rl:queue_depth
   INCRBY rl:daily_tokens:<org_id>:<date>  (actual tokens used)
```

---

## Default Quota Policy

If no `quota_policies` row exists for an organization, these defaults apply (in-memory, not stored):

```python
rpm_limit               = 60
max_concurrent_requests = 5
max_input_tokens        = 8192   # gateway soft estimate
max_output_tokens       = 2048   # clamps request's max_tokens
daily_token_hard_limit  = 1_000_000
```

---

## Control 1: Input Token Ceiling

**What it checks:** Word count of all message `content` fields combined (whitespace split, minimum 1). This is an approximation — actual tokenizer counts can differ by ~20–30%.

**What happens if exceeded:**
- Prompt estimate > `max_input_tokens`: request rejected immediately with `400`
- `max_tokens` > `max_output_tokens`: `max_tokens` is silently capped (no error)

**Note:** The gateway also enforces a hard context ceiling in `resolve_target()`:
```
if estimated_prompt + max_tokens > MAX_CONTEXT_TOKENS (8192):
    raise 400
```
This is redundant with the quota policy check but acts as a second gate.

---

## Control 2: Requests Per Minute (RPM)

**How it works:** Redis Lua script — atomic INCR with TTL set only on the first increment of each 60-second window. This is a sliding-window token bucket.

```lua
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])  -- ARGV[2] = 60
end
if current > tonumber(ARGV[1]) then  -- ARGV[1] = rpm_limit
    return 0  -- denied
end
return 1  -- allowed
```

**Response when exceeded:**
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 1
{"detail": "Rate limit exceeded"}
```

**Retry guidance:** Wait 1 second and retry. The window resets every 60 seconds.

---

## Control 3: Concurrent Requests

**How it works:** Redis INCR/DECR pair. The counter is incremented when a request passes all earlier gates and decremented in the `finally` block when the stream ends (or errors).

```
acquire:  INCR rl:concurrent:<org_id>  → if > limit: DECR + deny
release:  DECR rl:concurrent:<org_id>  (always, even on error/cancel)
```

A 300-second TTL prevents stuck counters if the process crashes without releasing.

**Response when exceeded:**
```http
HTTP/1.1 429 Too Many Requests
{"detail": "Concurrent request limit reached"}
```

**No `Retry-After` header** — retry when another request completes. For streaming requests this is indeterminate.

---

## Control 4: Daily Token Quota

**How it works:** A Redis key per organization per UTC day. The key accumulates tokens used (actual `prompt_tokens + completion_tokens` from the vLLM response). Before forwarding, a pessimistic estimate (word count + max_tokens) is checked against the hard limit.

**Key pattern:** `rl:daily_tokens:<org_id>:<YYYY-MM-DD>`  
**TTL:** 90,000 seconds (~25 hours) — survives day boundaries safely

**Important:** The check uses a pessimistic estimate. Actual usage may be lower (e.g., model stops early). The running total is updated with real counts after the response completes.

**Response when exceeded:**
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 86400
{"detail": "Daily token quota exceeded"}
```

**Retry guidance:** The quota resets at UTC midnight. `Retry-After: 86400` is a conservative worst-case.

---

## Control 5: Global Queue Depth

**How it works:** A single system-wide Redis counter tracking the total number of in-flight requests across all tenants. This is a hard capacity gate — if the system is saturated (e.g., vLLM is queue-saturated), all new requests are rejected with `503` regardless of per-tenant limits.

**Default limit:** 50 concurrent in-flight requests (controlled by `GLOBAL_QUEUE_LIMIT` env var).

**Response when exceeded:**
```http
HTTP/1.1 503 Service Unavailable
Retry-After: 5
{"detail": "Service temporarily at capacity. Please retry shortly."}
```

---

## Fail-Open on Redis Failures

All Redis calls are wrapped in try/except. If Redis becomes unreachable:
- All limit checks return `True` (allow)
- Requests proceed without rate limiting
- A warning is logged: `"Redis RPM check failed; failing open"`

**Rationale:** An inference service should degrade gracefully during Redis outages. The alternative (hard fail on Redis down) would cause cascading failures that are worse than temporary limit bypass. In practice, Redis is local and highly available.

The daily quota daily limit check is the **exception** — it fails closed (returns `False`, denying the request) because quota miscounting is a financial concern. This behavior is intentional.

---

## Configuring Per-Organization Quotas

Quota policies are stored in the `quota_policies` table. In V1, there is no REST API to modify them — use SQL directly or add to `seed_dev_data.py`:

```sql
INSERT INTO quota_policies (
    id,
    organization_id,
    rpm_limit,
    max_concurrent_requests,
    max_input_tokens,
    max_output_tokens,
    daily_token_hard_limit
) VALUES (
    gen_random_uuid(),
    '<org_uuid>',
    120,    -- 2x default RPM for premium tier
    10,     -- 2x concurrent
    8192,
    2048,
    5000000 -- 5M tokens/day
);
```

Or in Python:
```python
from sqlalchemy import insert
from db.models import QuotaPolicy

await db.execute(
    insert(QuotaPolicy).values(
        organization_id=org_id,
        rpm_limit=120,
        max_concurrent_requests=10,
        daily_token_hard_limit=5_000_000,
    )
)
await db.commit()
```

---

## Prometheus Metrics for Rate Limits

The gateway emits a counter for every rejection:

```
rate_limit_rejections_total{reason="<reason>", tenant_id="<org_id>"}
```

| `reason` label | Trigger |
|----------------|---------|
| `input_token_ceiling` | Prompt exceeded `max_input_tokens` |
| `rpm` | RPM limit hit |
| `concurrent` | Concurrent limit hit |
| `quota` | Daily token quota exceeded |
| `queue_depth` | Global queue depth exceeded |

Query in Prometheus / Grafana:
```promql
# Rate limit rejections by reason (last 5m)
increase(rate_limit_rejections_total[5m])

# RPM rejections per tenant
increase(rate_limit_rejections_total{reason="rpm"}[1m])
```

---

## Client-Side Retry Strategy

Recommended retry logic for production clients:

```python
import time
import httpx

def call_with_retry(client, **kwargs) -> dict:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.post(**kwargs)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 1))
                time.sleep(retry_after)
                continue
            if response.status_code == 503:
                retry_after = int(response.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
        except httpx.TimeoutException:
            time.sleep(2 ** attempt)  # exponential backoff
    raise RuntimeError("Max retries exceeded")
```

**Summary of Retry-After values:**
| Error | `Retry-After` | Strategy |
|-------|--------------|---------|
| RPM exceeded | 1s | Always retry after 1s |
| Daily quota | 86400s | Stop until tomorrow |
| Global capacity | 5s | Brief exponential backoff |
| Concurrency | (none) | Poll with jitter |
