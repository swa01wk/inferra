import json
import logging
import time
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.schemas import AdapterResponse, CreateAdapterRequest, CreateAliasRequest
from apps.api.services.adapters.registry import download_adapter, load_adapter_into_vllm
from apps.api.services.auth.keys import AuthenticatedContext, require_inference_key
from apps.api.services.observability.metrics import (
    active_adapters_loaded,
    adapter_load_latency_seconds,
    adapter_load_total,
)
from db.models import Adapter, Deployment, Model, ModelAlias, Worker
from db.session import get_db

router = APIRouter()
logger = logging.getLogger("inferra.adapters")


@router.post("/adapters", response_model=AdapterResponse)
async def create_adapter(
    body: CreateAdapterRequest,
    background_tasks: BackgroundTasks,
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> AdapterResponse:
    if body.rank > settings.max_lora_rank:
        raise HTTPException(
            status_code=422,
            detail=f"Adapter rank {body.rank} exceeds deployment maximum {settings.max_lora_rank}",
        )

    model_result = await db.execute(select(Model).where(Model.hf_repo == body.base_model))
    base_model = model_result.scalar_one_or_none()
    if not base_model:
        raise HTTPException(status_code=400, detail="Base model not found")

    existing = await db.execute(
        select(Adapter).where(
            Adapter.organization_id == auth.organization.id,
            Adapter.name == body.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Adapter name already exists")

    adapter = Adapter(
        organization_id=auth.organization.id,
        base_model_id=base_model.id,
        name=body.name,
        storage_uri=body.storage_uri,
        rank=body.rank,
        status="registered",
    )
    db.add(adapter)
    await db.commit()
    await db.refresh(adapter)

    if body.alias:
        deployment_result = await db.execute(
            select(Deployment).where(Deployment.status == "running").limit(1)
        )
        deployment = deployment_result.scalar_one_or_none()
        alias = ModelAlias(
            organization_id=auth.organization.id,
            alias=body.alias,
            base_model_id=base_model.id,
            adapter_id=adapter.id,
            deployment_id=deployment.id if deployment else None,
        )
        db.add(alias)
        await db.commit()

    background_tasks.add_task(_process_adapter, adapter.id)
    return _to_response(adapter)


@router.get("/adapters")
async def list_adapters(
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Adapter).where(
            Adapter.organization_id == auth.organization.id,
            Adapter.status != "deleted",
        )
    )
    adapters = result.scalars().all()
    return {"adapters": [_to_response(a).model_dump() for a in adapters]}


@router.get("/adapters/{adapter_id}", response_model=AdapterResponse)
async def get_adapter(
    adapter_id: UUID,
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> AdapterResponse:
    adapter = await db.get(Adapter, adapter_id)
    if not adapter or adapter.organization_id != auth.organization.id:
        raise HTTPException(status_code=404, detail="Adapter not found")
    return _to_response(adapter)


@router.delete("/adapters/{adapter_id}")
async def delete_adapter(
    adapter_id: UUID,
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    adapter = await db.get(Adapter, adapter_id)
    if not adapter or adapter.organization_id != auth.organization.id:
        raise HTTPException(status_code=404, detail="Adapter not found")
    adapter.status = "deleted"
    await db.commit()
    return {"status": "deleted", "id": str(adapter_id)}


@router.post("/aliases")
async def create_alias(
    body: CreateAliasRequest,
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    model_result = await db.execute(select(Model).where(Model.hf_repo == body.base_model))
    base_model = model_result.scalar_one_or_none()
    if not base_model:
        raise HTTPException(status_code=400, detail="Base model not found")

    if body.adapter_id:
        adapter = await db.get(Adapter, body.adapter_id)
        if not adapter or adapter.organization_id != auth.organization.id:
            raise HTTPException(status_code=403, detail="Adapter not found or access denied")

    deployment_result = await db.execute(
        select(Deployment).where(Deployment.status == "running").limit(1)
    )
    deployment = deployment_result.scalar_one_or_none()

    alias = ModelAlias(
        organization_id=auth.organization.id,
        alias=body.alias,
        base_model_id=base_model.id,
        adapter_id=body.adapter_id,
        deployment_id=deployment.id if deployment else None,
    )
    db.add(alias)
    await db.commit()
    return {"alias": body.alias, "adapter_id": str(body.adapter_id) if body.adapter_id else None}


def _to_response(adapter: Adapter) -> AdapterResponse:
    return AdapterResponse(
        id=adapter.id,
        organization_id=adapter.organization_id,
        name=adapter.name,
        base_model_id=adapter.base_model_id,
        rank=adapter.rank,
        status=adapter.status,
        error_message=adapter.error_message,
        created_at=adapter.created_at,
    )


async def _process_adapter(adapter_id: UUID) -> None:
    from db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        adapter = await db.get(Adapter, adapter_id)
        if not adapter:
            return
        try:
            await download_adapter(adapter, db)
            deployment_result = await db.execute(
                select(Deployment).where(Deployment.status == "running").limit(1)
            )
            deployment = deployment_result.scalar_one_or_none()
            if not deployment:
                adapter.status = "failed"
                adapter.error_message = "No active deployment"
                await db.commit()
                return
            worker = await db.get(Worker, deployment.worker_id)
            if not worker:
                adapter.status = "failed"
                adapter.error_message = "No worker available"
                await db.commit()
                return
            load_start = time.monotonic()
            await load_adapter_into_vllm(adapter, worker.endpoint)
            load_elapsed = time.monotonic() - load_start
            adapter_load_latency_seconds.labels(adapter_id=str(adapter_id)).observe(load_elapsed)
            adapter_load_total.labels(adapter_id=str(adapter_id), status="success").inc()
            adapter.status = "active"
            await db.commit()

            # Update active adapters gauge
            count_result = await db.execute(
                select(func.count()).where(Adapter.status.in_(["loaded", "active"]))
            )
            active_count = count_result.scalar() or 0
            active_adapters_loaded.set(active_count)

            logger.info(
                json.dumps(
                    {
                        "event": "adapter_status_changed",
                        "adapter_id": str(adapter.id),
                        "to_status": "active",
                    }
                )
            )
        except Exception as exc:
            adapter.status = "failed"
            adapter.error_message = str(exc)
            adapter_load_total.labels(adapter_id=str(adapter_id), status="failed").inc()
            await db.commit()
