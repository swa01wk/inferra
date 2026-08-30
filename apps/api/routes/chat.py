from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.schemas import ChatCompletionRequest
from apps.api.services.auth.keys import AuthenticatedContext, require_inference_key
from apps.api.services.limits.admission import check_admission, release_admission
from apps.api.services.limits.rate_limiter import increment_token_usage
from apps.api.services.observability.metrics import (
    completion_tokens_total,
    inference_errors_total,
    inference_requests_total,
    output_tokens_per_second,
    prompt_tokens_total,
    total_latency_seconds,
    ttft_seconds,
)
from apps.api.services.routing.resolver import resolve_target
from apps.api.services.usage.recorder import (
    RequestTimings,
    TokenCounter,
    record_usage,
    tracked_stream,
)
from apps.api.services.vllm.client import VLLMClient
from db.models import RequestRecord
from db.session import AsyncSessionLocal, get_db

router = APIRouter()
logger = logging.getLogger("inferra.chat")


async def _persist_request_record(
    request_record_id: UUID,
    status: str,
    http_status: int | None,
    error_code: str | None,
    cancelled: bool,
) -> None:
    async with AsyncSessionLocal() as db:
        record = await db.get(RequestRecord, request_record_id)
        if not record:
            return
        record.status = status
        record.http_status = http_status
        record.error_code = error_code
        record.cancelled = cancelled
        await db.commit()


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
):
    timings = RequestTimings(received_at=time.monotonic(), routing_start=time.monotonic())
    admission = await check_admission(request, auth, db)

    try:
        try:
            request_id = UUID(http_request.state.request_id)
        except (ValueError, AttributeError):
            from uuid import uuid4

            request_id = uuid4()

        target = await resolve_target(
            request,
            auth,
            db,
            request_id=request_id,
        )
        timings.routing_end = time.monotonic()

        request_record = RequestRecord(
            id=target.request_id,
            organization_id=auth.organization.id,
            api_key_id=auth.api_key.id,
            deployment_id=target.deployment_id,
            adapter_id=target.adapter_id,
            logical_model=target.logical_model,
            status="pending",
        )
        db.add(request_record)
        await db.commit()

        payload = request.model_dump(exclude={"enable_thinking"})
        payload["model"] = target.adapter_runtime_name or target.base_model
        if request.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": request.enable_thinking}

        client = VLLMClient(target.worker_endpoint)
        token_counter = TokenCounter()

        if request.stream:
            timings.forwarded_at = time.monotonic()

            async def stream_response():
                try:
                    async for chunk in tracked_stream(
                        client.chat_completions_stream(payload),
                        timings,
                        token_counter,
                    ):
                        yield chunk
                    request_record.status = "completed"
                    request_record.http_status = 200
                except asyncio.CancelledError:
                    request_record.status = "cancelled"
                    request_record.cancelled = True
                    raise
                except Exception as exc:
                    request_record.status = "failed"
                    request_record.http_status = 500
                    request_record.error_code = str(exc)
                    raise
                finally:
                    background_tasks.add_task(
                        _finalize_usage,
                        request_record.id,
                        timings,
                        token_counter,
                        target.deployment_id,
                        request_record.status,
                        request_record.http_status or 200,
                        request_record.cancelled,
                        request_record.error_code,
                    )
                    await release_admission(auth, admission)
                    inference_requests_total.labels(
                        status=request_record.status,
                        tenant_id=str(auth.organization.id),
                        logical_model=target.logical_model,
                    ).inc()
                    if timings.ttft_ms:
                        ttft_seconds.labels(logical_model=target.logical_model).observe(
                            timings.ttft_ms / 1000
                        )
                    if timings.total_ms:
                        total_latency_seconds.labels(logical_model=target.logical_model).observe(
                            timings.total_ms / 1000
                        )
                    if timings.decode_ms and timings.decode_ms > 0 and token_counter.completion_tokens > 0:
                        tps = token_counter.completion_tokens / (timings.decode_ms / 1000)
                        output_tokens_per_second.labels(logical_model=target.logical_model).observe(tps)
                    if request_record.status == "failed" and request_record.error_code:
                        inference_errors_total.labels(
                            error_code=request_record.error_code[:64],
                            tenant_id=str(auth.organization.id),
                        ).inc()
                    prompt_tokens_total.labels(tenant_id=str(auth.organization.id)).inc(
                        token_counter.prompt_tokens
                    )
                    completion_tokens_total.labels(tenant_id=str(auth.organization.id)).inc(
                        token_counter.completion_tokens
                    )
                    await increment_token_usage(
                        str(auth.organization.id),
                        token_counter.prompt_tokens + token_counter.completion_tokens,
                    )

            return StreamingResponse(stream_response(), media_type="text/event-stream")

        timings.forwarded_at = time.monotonic()
        result = await client.chat_completions(payload)
        timings.first_token_at = time.monotonic()
        timings.completed_at = time.monotonic()
        usage = result.get("usage") or {}
        token_counter.prompt_tokens = usage.get("prompt_tokens", 0)
        token_counter.completion_tokens = usage.get("completion_tokens", 0)
        request_record.status = "completed"
        request_record.http_status = 200
        background_tasks.add_task(
            _finalize_usage,
            request_record.id,
            timings,
            token_counter,
            target.deployment_id,
        )
        await release_admission(auth, admission)
        return result
    except HTTPException:
        await release_admission(auth, admission)
        raise
    except Exception as exc:
        await release_admission(auth, admission)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _finalize_usage(
    request_record_id: UUID,
    timings: RequestTimings,
    token_counter: TokenCounter,
    deployment_id: UUID,
    status: str = "completed",
    http_status: int = 200,
    cancelled: bool = False,
    error_code: str | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        record = await db.get(RequestRecord, request_record_id)
        if not record:
            return
        record.status = status
        record.http_status = http_status
        record.cancelled = cancelled
        record.error_code = error_code
        from db.models import Deployment

        deployment = await db.get(Deployment, deployment_id)
        worker_id = deployment.worker_id if deployment else None
        await record_usage(db, record, timings, token_counter, worker_id)
