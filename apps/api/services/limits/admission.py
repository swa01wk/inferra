from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.schemas import ChatCompletionRequest
from apps.api.services.auth.keys import AuthenticatedContext
from apps.api.services.limits.rate_limiter import (
    check_daily_quota,
    check_global_queue,
    check_rpm_limit,
    concurrency_tracker,
    release_global_queue,
)
from apps.api.services.observability.metrics import rate_limit_rejections_total
from apps.api.services.routing.resolver import estimate_tokens
from db.models import QuotaPolicy


@dataclass
class AdmissionResult:
    policy: QuotaPolicy
    concurrency_acquired: bool = False
    queue_acquired: bool = False


async def get_or_default_policy(org_id, db: AsyncSession) -> QuotaPolicy:
    result = await db.execute(select(QuotaPolicy).where(QuotaPolicy.organization_id == org_id))
    policy = result.scalar_one_or_none()
    if policy:
        return policy
    return QuotaPolicy(
        organization_id=org_id,
        rpm_limit=60,
        max_concurrent_requests=5,
        max_input_tokens=settings.max_context_tokens,
        max_output_tokens=2048,
        daily_token_hard_limit=1_000_000,
    )


def check_input_token_ceiling(request: ChatCompletionRequest, policy: QuotaPolicy, org_id: str | None = None) -> None:
    estimated = estimate_tokens(request.messages)
    if estimated > policy.max_input_tokens:
        if org_id:
            rate_limit_rejections_total.labels(reason="input_token_ceiling", tenant_id=org_id).inc()
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds maximum input token limit of {policy.max_input_tokens}",
        )
    if request.max_tokens and request.max_tokens > policy.max_output_tokens:
        request.max_tokens = policy.max_output_tokens


async def check_admission(
    request: ChatCompletionRequest,
    auth: AuthenticatedContext,
    db: AsyncSession,
) -> AdmissionResult:
    policy = await get_or_default_policy(auth.organization.id, db)
    org_id = str(auth.organization.id)
    check_input_token_ceiling(request, policy, org_id)
    if not await check_rpm_limit(org_id, policy.rpm_limit):
        rate_limit_rejections_total.labels(reason="rpm", tenant_id=org_id).inc()
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "1"},
        )

    if not await concurrency_tracker.acquire(org_id, policy.max_concurrent_requests):
        rate_limit_rejections_total.labels(reason="concurrent", tenant_id=org_id).inc()
        raise HTTPException(status_code=429, detail="Concurrent request limit reached")

    estimated_tokens = estimate_tokens(request.messages) + (request.max_tokens or settings.default_max_tokens)
    if not await check_daily_quota(org_id, estimated_tokens, policy.daily_token_hard_limit):
        await concurrency_tracker.release(org_id)
        rate_limit_rejections_total.labels(reason="quota", tenant_id=org_id).inc()
        raise HTTPException(
            status_code=429,
            detail="Daily token quota exceeded",
            headers={"Retry-After": "86400"},
        )

    if not await check_global_queue(settings.global_queue_limit):
        await concurrency_tracker.release(org_id)
        rate_limit_rejections_total.labels(reason="queue_depth", tenant_id=org_id).inc()
        raise HTTPException(
            status_code=503,
            detail="Service temporarily at capacity. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    return AdmissionResult(policy=policy, concurrency_acquired=True, queue_acquired=True)


async def release_admission(auth: AuthenticatedContext, admission: AdmissionResult) -> None:
    org_id = str(auth.organization.id)
    if admission.concurrency_acquired:
        await concurrency_tracker.release(org_id)
    if admission.queue_acquired:
        await release_global_queue()
