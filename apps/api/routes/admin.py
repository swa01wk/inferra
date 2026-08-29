import json
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.schemas import (
    ApiKeyCreateResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
    UsageQueryResponse,
)
from apps.api.services.auth.keys import (
    AuthenticatedContext,
    generate_api_key,
    require_admin_key,
    require_inference_key,
)
from db.models import APIKey, Deployment, RequestRecord, UsageMetric, Worker
from db.session import get_db

router = APIRouter()
logger = logging.getLogger("inferra.admin")


@router.post("/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    body: CreateApiKeyRequest,
    auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreateResponse:
    secret, key_hash, key_prefix = generate_api_key()
    org_id = body.organization_id or auth.organization.id
    api_key = APIKey(
        organization_id=org_id,
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        is_admin=False,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    logger.info(
        json.dumps(
            {
                "event": "api_key_created",
                "api_key_id": str(api_key.id),
                "organization_id": str(org_id),
            }
        )
    )
    return ApiKeyCreateResponse(
        id=api_key.id,
        key_prefix=api_key.key_prefix,
        name=api_key.name,
        organization_id=api_key.organization_id,
        status=api_key.status,
        expires_at=api_key.expires_at,
        secret=secret,
    )


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: UUID,
    auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    api_key = await db.get(APIKey, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.status = "revoked"
    await db.commit()
    logger.info(
        json.dumps(
            {
                "event": "api_key_revoked",
                "api_key_id": str(api_key.id),
                "organization_id": str(api_key.organization_id),
            }
        )
    )
    return {"status": "revoked", "id": str(key_id)}


@router.get("/workers")
async def list_workers(
    auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Worker))
    workers = result.scalars().all()
    return {
        "workers": [
            {
                "id": str(w.id),
                "hostname": w.hostname,
                "gpu_type": w.gpu_type,
                "gpu_vram_mb": w.gpu_vram_mb,
                "endpoint": w.endpoint,
                "status": w.status,
            }
            for w in workers
        ]
    }


@router.get("/deployments")
async def list_deployments(
    auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Deployment))
    deployments = result.scalars().all()
    return {
        "deployments": [
            {
                "id": str(d.id),
                "model_id": str(d.model_id),
                "worker_id": str(d.worker_id),
                "endpoint": d.endpoint,
                "status": d.status,
                "config_json": d.config_json,
            }
            for d in deployments
        ]
    }


@router.get("/usage", response_model=UsageQueryResponse)
async def get_usage(
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> UsageQueryResponse:
    result = await db.execute(
        select(RequestRecord, UsageMetric)
        .join(UsageMetric, UsageMetric.request_id == RequestRecord.id, isouter=True)
        .where(RequestRecord.organization_id == auth.organization.id)
        .order_by(RequestRecord.received_at.desc())
        .limit(100)
    )
    rows = result.all()
    requests = []
    total_prompt = 0
    total_completion = 0
    for req, metric in rows:
        if metric:
            total_prompt += metric.prompt_tokens
            total_completion += metric.completion_tokens
        requests.append(
            {
                "request_id": str(req.id),
                "logical_model": req.logical_model,
                "status": req.status,
                "received_at": req.received_at.isoformat() if req.received_at else None,
                "prompt_tokens": metric.prompt_tokens if metric else 0,
                "completion_tokens": metric.completion_tokens if metric else 0,
                "ttft_ms": metric.ttft_ms if metric else None,
                "total_ms": metric.total_ms if metric else None,
            }
        )
    return UsageQueryResponse(
        total_requests=len(requests),
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        requests=requests,
    )
