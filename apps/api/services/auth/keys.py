from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import APIKey, Organization
from db.session import get_db

bearer_scheme = HTTPBearer()


@dataclass
class AuthenticatedContext:
    api_key: APIKey
    organization: Organization


def generate_api_key() -> tuple[str, str, str]:
    secret = "inf_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(secret.encode()).hexdigest()
    key_prefix = secret[:8]
    return secret, key_hash, key_prefix


async def validate_api_key(raw_key: str, db: AsyncSession) -> APIKey | None:
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash, APIKey.status == "active")
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        return None
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        return None
    return api_key


async def require_inference_key(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedContext:
    api_key = await validate_api_key(credentials.credentials, db)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")
    if api_key.is_admin:
        raise HTTPException(status_code=403, detail="Admin keys cannot be used for inference")
    org = await db.get(Organization, api_key.organization_id)
    if not org or org.status != "active":
        raise HTTPException(status_code=403, detail="Organization suspended")
    return AuthenticatedContext(api_key=api_key, organization=org)


async def require_admin_key(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedContext:
    api_key = await validate_api_key(credentials.credentials, db)
    if not api_key or not api_key.is_admin:
        raise HTTPException(status_code=403, detail="Admin authorization required")
    org = await db.get(Organization, api_key.organization_id)
    if not org:
        raise HTTPException(status_code=403, detail="Organization not found")
    return AuthenticatedContext(api_key=api_key, organization=org)
