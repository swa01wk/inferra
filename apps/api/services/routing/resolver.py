from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.schemas import ChatCompletionRequest
from apps.api.services.auth.keys import AuthenticatedContext
from db.models import Deployment, Model, ModelAlias, Worker


@dataclass
class ResolvedInferenceTarget:
    request_id: UUID
    organization_id: UUID
    logical_model: str
    base_model: str
    adapter_id: UUID | None
    adapter_runtime_name: str | None
    deployment_id: UUID
    worker_endpoint: str
    max_model_len: int
    max_output_tokens: int
    policy_version: str = "v1"


def estimate_tokens(messages) -> int:
    text = " ".join(m.content for m in messages)
    return max(1, len(text.split()))


async def resolve_target(
    request: ChatCompletionRequest,
    auth: AuthenticatedContext,
    db: AsyncSession,
    request_id: UUID | None = None,
) -> ResolvedInferenceTarget:
    # Enforce total context ceiling: estimated prompt tokens + max_tokens must not exceed 8192.
    estimated_prompt = estimate_tokens(request.messages)
    requested_output = request.max_tokens or settings.default_max_tokens
    if estimated_prompt + requested_output > settings.max_context_tokens:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Request context too long: estimated {estimated_prompt} prompt tokens + "
                f"{requested_output} max_tokens = {estimated_prompt + requested_output} "
                f"exceeds maximum {settings.max_context_tokens}"
            ),
        )

    alias_result = await db.execute(
        select(ModelAlias).where(
            ModelAlias.organization_id == auth.organization.id,
            ModelAlias.alias == request.model,
        )
    )
    alias = alias_result.scalar_one_or_none()

    if not alias:
        alias_result = await db.execute(
            select(ModelAlias).where(
                ModelAlias.alias == request.model,
                ModelAlias.is_public.is_(True),
            )
        )
        alias = alias_result.scalar_one_or_none()

    if alias and alias.organization_id != auth.organization.id and not alias.is_public:
        raise HTTPException(status_code=403, detail="Access denied to private model alias")

    if alias:
        base_model = await db.get(Model, alias.base_model_id)
        deployment = await db.get(Deployment, alias.deployment_id) if alias.deployment_id else None
        adapter_id = alias.adapter_id
        adapter_runtime_name = str(alias.adapter_id) if alias.adapter_id else None
    else:
        model_result = await db.execute(select(Model).where(Model.hf_repo == request.model))
        base_model = model_result.scalar_one_or_none()
        if not base_model:
            model_result = await db.execute(select(Model).limit(1))
            base_model = model_result.scalar_one_or_none()
        deployment_result = await db.execute(
            select(Deployment).where(Deployment.status == "running").limit(1)
        )
        deployment = deployment_result.scalar_one_or_none()
        adapter_id = None
        adapter_runtime_name = None

    if not base_model or not deployment:
        raise HTTPException(status_code=503, detail="No active deployment available")

    worker = await db.get(Worker, deployment.worker_id)
    if not worker or worker.status != "healthy":
        raise HTTPException(status_code=503, detail="No healthy worker available")

    max_output = min(request.max_tokens or settings.default_max_tokens, settings.default_max_tokens)
    return ResolvedInferenceTarget(
        request_id=request_id or uuid4(),
        organization_id=auth.organization.id,
        logical_model=request.model,
        base_model=base_model.hf_repo,
        adapter_id=adapter_id,
        adapter_runtime_name=adapter_runtime_name,
        deployment_id=deployment.id,
        worker_endpoint=worker.endpoint,
        max_model_len=settings.max_context_tokens,
        max_output_tokens=max_output,
    )
