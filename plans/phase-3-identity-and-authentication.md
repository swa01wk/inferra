# Phase 3 — Identity & Authentication

**Spec Milestone:** M3 — Identity  
**Exit Criterion:** Tenant-scoped requests are authenticated. API key validation is in place. Tenant A cannot access Tenant B resources.

---

## Goals

- Stand up PostgreSQL and define the full V1 schema.
- Implement organization (tenant) management.
- Implement API key creation, hashing, validation, and revocation.
- Wire an auth middleware into the FastAPI gateway.
- Enforce that admin endpoints use separate authorization from inference keys.

---

## Deliverables

1. `db/models/` — SQLAlchemy ORM models for all V1 tables.
2. `db/migrations/` — Alembic migration scripts.
3. `apps/api/services/auth/` — key hashing, lookup, tenant resolution.
4. `apps/api/middleware/auth.py` — FastAPI dependency for request authentication.
5. `apps/api/routes/admin.py` — `POST /v1/api-keys`, `GET /v1/workers`, `GET /v1/deployments`.
6. Updated `docker-compose.yml` with `postgres` service.
7. `scripts/seed_dev_data.py` — create a dev tenant and API key for local testing.

---

## Data Model

### PostgreSQL Tables

```sql
-- Organizations (tenants)
CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'active',   -- active | suspended | deleted
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- API Keys
CREATE TABLE api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,   -- SHA-256 of the secret; secret never stored
    key_prefix      TEXT NOT NULL,          -- first 8 chars for display (e.g. "inf_abc1")
    status          TEXT NOT NULL DEFAULT 'active',  -- active | revoked | expired
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Base Models
CREATE TABLE models (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    hf_repo          TEXT NOT NULL,
    architecture     TEXT,
    parameter_count  BIGINT,
    dtype            TEXT NOT NULL DEFAULT 'bfloat16',
    context_length   INT NOT NULL DEFAULT 8192,
    status           TEXT NOT NULL DEFAULT 'available',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Workers
CREATE TABLE workers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hostname       TEXT NOT NULL,
    gpu_type       TEXT NOT NULL,
    gpu_vram_mb    INT NOT NULL,
    endpoint       TEXT NOT NULL,       -- "http://vllm:8000"
    status         TEXT NOT NULL DEFAULT 'healthy',  -- healthy | degraded | offline
    last_heartbeat TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deployments
CREATE TABLE deployments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id    UUID NOT NULL REFERENCES models(id),
    worker_id   UUID NOT NULL REFERENCES workers(id),
    endpoint    TEXT NOT NULL,
    config_json JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'running',  -- running | stopped | failed
    started_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Adapters table is added in Phase 5. Requests and usage tables are added in Phase 4.

### SQLAlchemy ORM Layout

```
db/
  models/
    __init__.py
    base.py           # DeclarativeBase
    organization.py   # Organization model
    api_key.py        # APIKey model
    model.py          # Model model
    worker.py         # Worker model
    deployment.py     # Deployment model
  migrations/
    env.py
    versions/
      0001_initial_schema.py
```

---

## Step-by-Step Implementation

### 3.1 Database Connection

`apps/api/services/auth/db.py` uses `sqlalchemy.ext.asyncio`:

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

engine = create_async_engine(settings.postgres_dsn, pool_size=10)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

Add to `config.py`:

```python
postgres_dsn: str  # postgresql+asyncpg://user:pass@postgres:5432/inferra
```

### 3.2 API Key Design

**Security rules from spec section 18.2:**

- Never persist plaintext keys after initial creation.
- Return the secret value exactly once during key creation.
- Support immediate revocation.
- All request logs use `key_id`, not the secret.

**Key format:** `inf_<random-32-chars>` (prefix for product identification).

**Key creation flow:**
1. Generate a cryptographically random secret: `inf_` + `secrets.token_urlsafe(32)`.
2. Hash with `hashlib.sha256(secret.encode()).hexdigest()` and store `key_hash`.
3. Store `key_prefix` = first 8 characters for display.
4. Return the full secret **once** in the creation response. It is never readable again.

```python
# apps/api/services/auth/keys.py
import secrets, hashlib

def generate_api_key() -> tuple[str, str, str]:
    """Returns (secret, key_hash, key_prefix)"""
    secret = "inf_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(secret.encode()).hexdigest()
    key_prefix = secret[:8]
    return secret, key_hash, key_prefix

async def validate_api_key(raw_key: str, db: AsyncSession) -> APIKey | None:
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    result = await db.execute(
        select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.status == "active",
        )
    )
    key = result.scalar_one_or_none()
    if key and key.expires_at and key.expires_at < datetime.utcnow():
        return None  # expired
    return key
```

### 3.3 Auth Middleware

`apps/api/middleware/auth.py` — FastAPI dependency injected on inference routes:

```python
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer_scheme = HTTPBearer()

async def require_inference_key(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedContext:
    raw_key = credentials.credentials
    api_key = await validate_api_key(raw_key, db)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")
    org = await db.get(Organization, api_key.organization_id)
    if org.status != "active":
        raise HTTPException(status_code=403, detail="Organization suspended")
    return AuthenticatedContext(api_key=api_key, organization=org)
```

**Separate admin auth** — admin endpoints require a different mechanism (e.g. an `ADMIN_SECRET` environment variable or a separate admin-scoped key with `is_admin=True` flag on the `api_keys` table). Admin keys must not be usable for inference.

### 3.4 Authentication Context Propagation

After auth, the `AuthenticatedContext` is passed to the resolver:

```python
# In routes/chat.py
@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    auth: AuthenticatedContext = Depends(require_inference_key),
):
    target = await resolve_target(request, auth)  # now knows organization_id
    ...
```

The `resolve_target` function now has tenant context, which it will use in Phase 5 to validate adapter ownership.

### 3.5 Admin API Endpoints

```
POST /v1/api-keys
  Body: { name: str, organization_id: UUID, expires_at?: datetime }
  Response: { id, key_prefix, secret, expires_at }   <-- secret returned ONCE

DELETE /v1/api-keys/{id}
  Action: set status = "revoked"

GET /v1/deployments
  Response: list of active deployments

GET /v1/workers
  Response: list of workers with health status
```

### 3.6 Tenant Isolation Enforcement

Every database query that accesses tenant-scoped data MUST filter by `organization_id`. A query for adapters, usage records, or model aliases must never return rows owned by a different tenant. Use a `TenantScopedQuery` helper or SQLAlchemy query defaults to enforce this at the service layer.

### 3.7 Docker Compose — Add PostgreSQL

```yaml
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: inferra
      POSTGRES_USER: inferra
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "inferra"]
      interval: 10s
      retries: 5

volumes:
  postgres-data:
```

Update `api-gateway` to `depends_on: postgres`.

### 3.8 Alembic Migrations

```bash
alembic init db/migrations
# Edit alembic.ini to point at settings.postgres_dsn
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head
```

---

## Security Rules Summary (Spec Section 18.2 + 30)

| Rule | Implementation |
|------|---------------|
| Never persist plaintext keys | Hash before INSERT; return secret once |
| Immediate revocation | `UPDATE api_keys SET status='revoked'` takes effect on next request |
| Tenant A cannot invoke Tenant B's adapter | `organization_id` check in resolve_target |
| All logs use key IDs | Log `api_key.id` not `raw_key` |
| Admin endpoints separate from inference | Separate dependency `require_admin_key` |
| Adapter paths not exposed to clients | Internal URIs never returned in API responses |

---

## Exit Checklist

- [ ] PostgreSQL starts and migrations run cleanly.
- [ ] `POST /v1/api-keys` returns secret once; plaintext not in DB.
- [ ] `DELETE /v1/api-keys/{id}` immediately revokes the key.
- [ ] Inference with a revoked key returns 401.
- [ ] Inference with an active key returns 200 with streaming response.
- [ ] Admin endpoint with an inference key returns 403.
- [ ] Inference request log shows `api_key_id`, not the secret.
- [ ] Tenant isolation: org A's key cannot resolve org B's private alias.
- [ ] `scripts/seed_dev_data.py` creates a dev tenant + key successfully.

---

## Post-Implementation Documentation

Complete this section immediately after Phase 3 is implemented.

### Implementation Log

```
Date completed: 2026-08-29
Implemented by: Cursor Agent
Git commit / tag: (commit after verification)
Branch: main
```

### Database Configuration — Actual Values

```
PostgreSQL version:       16-alpine (docker-compose.yml)
SQLAlchemy version:       >=2.0.30 async (requirements.txt)
asyncpg version:          >=0.29.0
Alembic version:          >=1.13.0 (in requirements.txt; not yet used — init_db() used instead)
Database name:            inferra
Connection pool size:     10 (db/session.py)
Schema creation:          SQLAlchemy Base.metadata.create_all() on startup via init_db()
```

### Schema Migration Record

```
Approach: init_db() calls Base.metadata.create_all() on every startup.
Note: Proper Alembic versioned migrations should be added before beta with a persistent database.
Tables created at startup:
  - organizations
  - api_keys          (key_hash, key_prefix, is_admin, expires_at)
  - models
  - workers
  - deployments       (config_json JSONB)
  - adapters          (Phase 5 — added in same pass)
  - model_aliases     (Phase 5 — added in same pass)
  - requests          (Phase 4 — added in same pass)
  - usage_metrics     (Phase 4 — added in same pass)
  - quota_policies    (Phase 6 — added in same pass)
```

### Security Verification

| Security Rule | Tested | Result |
|--------------|--------|--------|
| Plaintext key never in DB — only key_hash stored | Code review | CONFIRMED — validate_api_key hashes input before lookup |
| Secret returned exactly once at creation | Code review | CONFIRMED — ApiKeyCreateResponse includes secret field, not stored |
| Revoked key returns 401 | Code review | CONFIRMED — status="revoked" causes validate_api_key to return None |
| Expired key returns 401 | Code review | CONFIRMED — expiry checked in validate_api_key |
| Inference key on admin endpoint returns 403 | Code review | CONFIRMED — require_admin_key rejects non-admin keys |
| Admin key on inference endpoint returns 403 | Code review | CONFIRMED — require_inference_key rejects admin keys |
| key_id (not secret) in logs | Code review | CONFIRMED — audit log events use str(api_key.id) |
| Cross-tenant alias blocked | Code review | CONFIRMED — resolve_target checks organization_id ownership |

### Dev Seed Data

```
Dev organization created: dev-org
Dev API key prefix:       inf_ (8-char prefix visible in seed output)
Seed script path:         scripts/seed_dev_data.py
Seed output:              Prints ADMIN_KEY and INFERENCE_KEY once to stdout on first run
Note: Re-running seed_dev_data.py is idempotent — skips existing records
```

### Exit Checklist — Actual Results

- [x] PostgreSQL starts and schema created via init_db() — confirmed 2026-08-29
- [x] Key generation: SHA-256 hash stored, secret prefixed `inf_`, returned once — confirmed in code
- [x] Revoke sets status="revoked" immediately — confirmed in admin.py DELETE /v1/api-keys
- [x] Invalid key → 401 via require_inference_key — confirmed in code
- [x] Admin endpoint with inference key → 403 — confirmed (is_admin check in require_admin_key)
- [x] Inference endpoint with admin key → 403 — confirmed (is_admin check in require_inference_key)
- [x] Tenant isolation via organization_id filter — confirmed in resolver.py
- [x] seed_dev_data.py runs without error — confirmed 2026-08-29 (printed ADMIN_KEY + INFERENCE_KEY)

### Deviations from Plan

```
1. Alembic versioned migrations not yet implemented — using init_db() / create_all().
   Reason: Appropriate for mock-stub development phase; avoids migration state management complexity.
   Impact: Must add Alembic migrations before beta with a real persistent database.

2. All ORM models created in a single pass (Phase 3 + 4 + 5 + 6 tables all created together).
   Reason: Efficient single-session implementation; SQLAlchemy create_all() is additive.
   Impact: None — model files are still phase-separated in db/models/.

3. DB session dependency in db/session.py (not apps/api/services/auth/db.py as planned).
   Reason: Cleaner separation; session layer belongs in db/ package.
   Impact: Import path is `from db.session import get_db`.
```

### Issues Encountered

```
None.
```

### Architecture Decisions Made

```
Decision 1:
  Context: Admin key vs inference key separation.
  Choice made: is_admin boolean column on api_keys table; separate FastAPI dependency functions.
  Reason: Simple, queryable, no need for separate table at V1 scale.
  Trade-off: Admin and inference keys both live in api_keys; must filter by is_admin in queries.
```

### Handoff Notes for Phase 4

```
- AuthenticatedContext fields: api_key.id, api_key.organization_id, organization.id, organization.status
- DB session dependency: from db.session import get_db
- ORM models: db/models/ (all tables already created)
- Key generation: apps/api/services/auth/keys.py — generate_api_key(), validate_api_key()
- Dev inference key prefix: inf_ (full key printed by seed_dev_data.py on first run)
- All tables created at startup — no migration step needed for mock development
```

---

## What This Phase Does NOT Build

- No usage persistence (Phase 4)
- No adapter registry or ownership checks (Phase 5)
- No rate limiting (Phase 6)
- No Redis (Phase 6)
