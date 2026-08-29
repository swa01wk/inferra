from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.services.vllm.client import VLLMClient
from db.models import Adapter

logger = logging.getLogger("inferra.adapters")


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
    )


def parse_storage_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme in {"s3", "minio"}:
        return parsed.netloc, parsed.path.lstrip("/")
    raise ValueError(f"Unsupported storage URI: {uri}")


async def validate_adapter_artifact(local_path: Path, expected_rank: int) -> None:
    config_path = local_path / "adapter_config.json"
    if not config_path.exists():
        raise ValueError("adapter_config.json not found")
    config = json.loads(config_path.read_text())
    rank = config.get("r") or config.get("rank")
    if rank and int(rank) > expected_rank:
        raise ValueError(f"Adapter rank {rank} exceeds policy maximum")


async def download_adapter(adapter: Adapter, db: AsyncSession) -> None:
    adapter.status = "downloading"
    await db.commit()
    try:
        bucket, key_prefix = parse_storage_uri(adapter.storage_uri)
        local_path = Path(settings.adapter_cache_dir) / str(adapter.id)
        local_path.mkdir(parents=True, exist_ok=True)

        s3 = get_s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        found = False
        for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                found = True
                rel = obj["Key"][len(key_prefix) :].lstrip("/")
                dest = local_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, obj["Key"], str(dest))

        if not found:
            raise ValueError("No adapter artifacts found at storage URI")

        await validate_adapter_artifact(local_path, adapter.rank)
        adapter.local_path = str(local_path)
        adapter.status = "available"
        adapter.error_message = None
        await db.commit()
    except Exception as exc:
        adapter.status = "failed"
        adapter.error_message = str(exc)
        await db.commit()
        logger.exception("Adapter download failed for %s", adapter.id)
        raise


async def load_adapter_into_vllm(adapter: Adapter, worker_endpoint: str) -> None:
    if not adapter.local_path:
        raise ValueError("Adapter has no local path")
    client = VLLMClient(worker_endpoint)
    await client.load_lora_adapter(str(adapter.id), adapter.local_path)
    adapter.status = "loaded"


async def ensure_bucket_exists() -> None:
    s3 = get_s3_client()
    try:
        s3.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        s3.create_bucket(Bucket=settings.s3_bucket)
