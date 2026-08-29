"""Seed development organization, admin key, model, worker, and deployment."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from apps.api.services.auth.keys import generate_api_key
from db.models import APIKey, Deployment, Model, ModelAlias, Organization, QuotaPolicy, Worker
from db.session import AsyncSessionLocal, init_db


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        org_result = await db.execute(select(Organization).where(Organization.name == "dev-org"))
        org = org_result.scalar_one_or_none()
        if not org:
            org = Organization(name="dev-org", status="active")
            db.add(org)
            await db.flush()

        model_result = await db.execute(select(Model).where(Model.hf_repo == "Qwen/Qwen3-4B"))
        model = model_result.scalar_one_or_none()
        if not model:
            model = Model(
                name="Qwen3-4B",
                hf_repo="Qwen/Qwen3-4B",
                architecture="qwen3",
                parameter_count=4_000_000_000,
                dtype="bfloat16",
                context_length=8192,
            )
            db.add(model)
            await db.flush()

        worker_result = await db.execute(select(Worker).limit(1))
        worker = worker_result.scalar_one_or_none()
        if not worker:
            worker = Worker(
                hostname="mock-worker",
                gpu_type="mock-l4",
                gpu_vram_mb=24576,
                endpoint="http://vllm:8000",
                status="healthy",
            )
            db.add(worker)
            await db.flush()

        deployment_result = await db.execute(select(Deployment).limit(1))
        deployment = deployment_result.scalar_one_or_none()
        if not deployment:
            deployment = Deployment(
                model_id=model.id,
                worker_id=worker.id,
                endpoint="http://vllm:8000",
                config_json={
                    "dtype": "bfloat16",
                    "max_model_len": 8192,
                    "max_lora_rank": 16,
                    "max_loras": 4,
                },
                status="running",
            )
            db.add(deployment)
            await db.flush()

        alias_result = await db.execute(
            select(ModelAlias).where(
                ModelAlias.organization_id == org.id,
                ModelAlias.alias == "test-assistant",
            )
        )
        if not alias_result.scalar_one_or_none():
            db.add(
                ModelAlias(
                    organization_id=org.id,
                    alias="test-assistant",
                    base_model_id=model.id,
                    adapter_id=None,
                    deployment_id=deployment.id,
                )
            )

        policy_result = await db.execute(
            select(QuotaPolicy).where(QuotaPolicy.organization_id == org.id)
        )
        if not policy_result.scalar_one_or_none():
            db.add(
                QuotaPolicy(
                    organization_id=org.id,
                    rpm_limit=60,
                    max_concurrent_requests=5,
                    max_input_tokens=8192,
                    max_output_tokens=2048,
                    daily_token_hard_limit=1_000_000,
                )
            )

        admin_result = await db.execute(
            select(APIKey).where(APIKey.organization_id == org.id, APIKey.is_admin.is_(True))
        )
        admin_key = admin_result.scalar_one_or_none()
        if not admin_key:
            secret, key_hash, key_prefix = generate_api_key()
            admin_key = APIKey(
                organization_id=org.id,
                name="dev-admin",
                key_hash=key_hash,
                key_prefix=key_prefix,
                is_admin=True,
            )
            db.add(admin_key)
            print(f"ADMIN_KEY={secret}")

        inference_result = await db.execute(
            select(APIKey).where(
                APIKey.organization_id == org.id,
                APIKey.is_admin.is_(False),
                APIKey.name == "dev-inference",
            )
        )
        inference_key = inference_result.scalar_one_or_none()
        if not inference_key:
            secret, key_hash, key_prefix = generate_api_key()
            inference_key = APIKey(
                organization_id=org.id,
                name="dev-inference",
                key_hash=key_hash,
                key_prefix=key_prefix,
                is_admin=False,
            )
            db.add(inference_key)
            print(f"INFERENCE_KEY={secret}")

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
