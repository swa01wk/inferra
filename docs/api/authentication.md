# Authentication

Inferra uses **Bearer token authentication** for all API calls. There are two categories of API key with different permissions.

---

## Key Types

| Type | `is_admin` | Can call | Cannot call |
|------|-----------|----------|-------------|
| **Inference key** | `false` | `/v1/chat/completions`, `/v1/models`, `/v1/adapters`, `/v1/aliases`, `/v1/usage` | `/v1/api-keys`, `/v1/workers`, `/v1/deployments` |
| **Admin key** | `true` | `/v1/api-keys`, `/v1/workers`, `/v1/deployments` | `/v1/chat/completions` (explicitly blocked) |

Using an admin key on an inference endpoint returns `403 Forbidden`:
```json
{"detail": "Admin keys cannot be used for inference"}
```

Using an inference key on an admin endpoint returns `403 Forbidden`:
```json
{"detail": "Admin authorization required"}
```

---

## Key Format

All keys follow the format:

```
inf_<32-byte-urlsafe-base64-token>
```

Example: `inf_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890ab`

The `inf_` prefix aids secret scanning tools (e.g., GitHub Advanced Security, truffleHog). The first 8 characters (e.g., `inf_aBcD`) form the `key_prefix`, which is safe to display in logs and UIs without exposing the secret.

---

## How Auth Works Internally

1. Client sends `Authorization: Bearer inf_<token>`
2. Gateway computes `SHA-256(token)`
3. Database query: `SELECT * FROM api_keys WHERE key_hash = ? AND status = 'active'`
4. If found, checks `expires_at` (in Python, not SQL, to handle timezone correctly)
5. Loads the associated `Organization` row
6. Checks `org.status == 'active'` — returns `403` if suspended
7. Returns `AuthenticatedContext(api_key, organization)` to the route handler

**The plaintext key is never stored.** Even if the database is compromised, raw keys cannot be recovered.

---

## Creating Keys

Keys are created via the admin API. You need an existing admin key to create more keys.

### Bootstrap Admin Key

The first admin key is seeded by `scripts/seed_dev_data.py`:

```bash
docker compose exec api-gateway python scripts/seed_dev_data.py
```

Output:
```
Organization: Dev Org (id: xxxxxxxx-...)
ADMIN_KEY:     inf_aBcDeFgHiJkLmN...  ← admin key (is_admin=True)
INFERENCE_KEY: inf_zYxWvUtSrQpOnMl...  ← inference key (is_admin=False)
```

Export both for use in subsequent commands:
```bash
export INFERRA_ADMIN_KEY=inf_aBcDeFg...
export INFERRA_INFERENCE_KEY=inf_zYxWvU...
```

### Create an Inference Key

```bash
curl -X POST http://localhost:9100/v1/api-keys \
  -H "Authorization: Bearer $INFERRA_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-key",
    "organization_id": "optional-uuid-if-different-org"
  }'
```

Response:
```json
{
  "id": "550e8400-...",
  "key_prefix": "inf_aBcD",
  "name": "prod-key",
  "organization_id": "...",
  "status": "active",
  "expires_at": null,
  "secret": "inf_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890ab"
}
```

> **Store the `secret` immediately.** It is shown exactly once and cannot be retrieved again.

### Create a Key with Expiry

```bash
curl -X POST http://localhost:9100/v1/api-keys \
  -H "Authorization: Bearer $INFERRA_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "temp-key-30-days",
    "expires_at": "2026-09-30T00:00:00Z"
  }'
```

---

## Revoking Keys

```bash
curl -X DELETE http://localhost:9100/v1/api-keys/<key_id> \
  -H "Authorization: Bearer $INFERRA_ADMIN_KEY"
```

Revocation is immediate. The key's `status` is set to `revoked`. Existing in-flight requests using the key are not terminated, but no new requests can authenticate with it.

---

## Using Keys in Client Code

### curl

```bash
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "test-assistant", "messages": [{"role": "user", "content": "Hello"}]}'
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:9100/v1",
    api_key="inf_your_key_here",
)

response = client.chat.completions.create(
    model="test-assistant",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### OpenAI Node.js SDK

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://localhost:9100/v1',
  apiKey: 'inf_your_key_here',
});

const response = await client.chat.completions.create({
  model: 'test-assistant',
  messages: [{ role: 'user', content: 'Hello' }],
});
```

### httpx (Python)

```python
import httpx

response = httpx.post(
    "http://localhost:9100/v1/chat/completions",
    headers={"Authorization": "Bearer inf_your_key_here"},
    json={
        "model": "test-assistant",
        "messages": [{"role": "user", "content": "Hello"}],
    },
)
```

---

## Error Reference

| Status | `detail` | Cause |
|--------|---------|-------|
| `401` | `"Invalid or expired API key"` | Key not found, revoked, or expired |
| `403` | `"Admin keys cannot be used for inference"` | Admin key on `/v1/chat/completions` |
| `403` | `"Admin authorization required"` | Inference key on admin endpoint |
| `403` | `"Organization suspended"` | Org `status='suspended'` |

---

## Security Best Practices

1. **Rotate keys regularly** — use `expires_at` for time-limited keys; revoke and reissue on suspected compromise.
2. **One key per service** — give each downstream service its own key so individual revocation is scoped.
3. **Never log raw keys** — Inferra logs only `key_prefix` (the first 8 chars), never the full secret.
4. **Use environment variables** — never hardcode keys in source code; use `.env` files or secret managers.
5. **Keep admin keys separate** — admin keys should not be deployed to application servers; use them only from secure management contexts.
6. **Check `key_prefix` in logs** — when debugging, use `key_prefix` to correlate log entries to a key without exposing the secret.

---

## Multi-Tenancy

Inferra is fully multi-tenant at the **Organization** level. Each organization:
- Has its own set of API keys
- Has its own quota policy (RPM, concurrency, daily tokens)
- Sees only its own adapters and usage records
- Cannot access another organization's private model aliases

Public aliases (`is_public=true`) are readable by any authenticated organization and are used for platform-provided base model aliases.
