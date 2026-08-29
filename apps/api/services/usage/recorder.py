from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UsageMetric


@dataclass
class RequestTimings:
    received_at: float = 0.0
    routing_start: float = 0.0
    routing_end: float = 0.0
    forwarded_at: float = 0.0
    first_token_at: float = 0.0
    completed_at: float = 0.0

    @property
    def gateway_ms(self) -> int:
        return int((self.routing_start - self.received_at) * 1000) if self.routing_start else 0

    @property
    def routing_ms(self) -> int:
        return int((self.routing_end - self.routing_start) * 1000) if self.routing_end else 0

    @property
    def ttft_ms(self) -> int:
        if self.first_token_at and self.forwarded_at:
            return int((self.first_token_at - self.forwarded_at) * 1000)
        return 0

    @property
    def decode_ms(self) -> int:
        if self.completed_at and self.first_token_at:
            return int((self.completed_at - self.first_token_at) * 1000)
        return 0

    @property
    def total_ms(self) -> int:
        if self.completed_at and self.received_at:
            return int((self.completed_at - self.received_at) * 1000)
        return 0


class TokenCounter:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def ingest(self, chunk: bytes) -> None:
        for line in chunk.decode("utf-8", errors="ignore").splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = data.get("usage")
            if usage:
                self.prompt_tokens = usage.get("prompt_tokens", self.prompt_tokens)
                self.completion_tokens = usage.get("completion_tokens", self.completion_tokens)


def has_content(chunk: bytes) -> bool:
    for line in chunk.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            if delta.get("content"):
                return True
    return False


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
        token_counter.ingest(chunk)
        yield chunk
    timings.completed_at = time.monotonic()


async def record_usage(
    db: AsyncSession,
    request_record,
    timings: RequestTimings,
    token_counter: TokenCounter,
    worker_id: UUID | None,
) -> None:
    request_record.first_token_at = (
        datetime.fromtimestamp(timings.first_token_at, tz=timezone.utc)
        if timings.first_token_at
        else None
    )
    request_record.completed_at = (
        datetime.fromtimestamp(timings.completed_at, tz=timezone.utc)
        if timings.completed_at
        else None
    )

    decode_ms = timings.decode_ms
    tps = (
        token_counter.completion_tokens / (decode_ms / 1000)
        if decode_ms > 0 and token_counter.completion_tokens
        else 0.0
    )

    metric = UsageMetric(
        request_id=request_record.id,
        prompt_tokens=token_counter.prompt_tokens,
        completion_tokens=token_counter.completion_tokens,
        total_tokens=token_counter.prompt_tokens + token_counter.completion_tokens,
        gateway_ms=timings.gateway_ms,
        routing_ms=timings.routing_ms,
        ttft_ms=timings.ttft_ms,
        decode_ms=decode_ms,
        total_ms=timings.total_ms,
        tokens_per_second=tps,
        time_per_output_token_ms=(decode_ms / token_counter.completion_tokens)
        if token_counter.completion_tokens
        else None,
        worker_id=worker_id,
    )
    db.add(metric)
    await db.commit()
