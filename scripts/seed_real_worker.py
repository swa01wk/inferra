"""Promote real RunPod vLLM worker to active.

Run after the SSH tunnel is up and docker-compose is running with the real vLLM overlay.

What this script does:
  1. Marks all existing workers/deployments as stopped (retires the mock).
  2. Creates a new Worker pointing to the real vLLM via SSH tunnel.
  3. Creates a new Deployment linked to that worker.
  4. Re-points the 'test-assistant' model alias to the new deployment.

Environment variable (optional):
  REAL_VLLM_ENDPOINT — where the api-gateway container can reach real vLLM
                       Default: http://host.docker.internal:8001
                       (host.docker.internal resolves to the Mac host from
                        inside a Docker Desktop container; port 8001 is the
                        SSH tunnel forwarding RunPod:8000)
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select, update

from db.models import Deployment, Model, ModelAlias, Worker
from db.session import AsyncSessionLocal, init_db

REAL_VLLM_ENDPOINT = os.getenv("REAL_VLLM_ENDPOINT", "http://host.docker.internal:8001")


async def seed_real_worker() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        # ── 1. Retire all existing workers and deployments ──────────────
        await db.execute(update(Worker).values(status="stopped"))
        await db.execute(update(Deployment).values(status="stopped"))
        await db.flush()
        print("  Retired all existing workers and deployments.")

        # ── 2. Ensure Qwen3-4B model record exists ───────────────────────
        model = (
            await db.execute(select(Model).where(Model.hf_repo == "Qwen/Qwen3-4B"))
        ).scalar_one_or_none()
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
            print("  Created Qwen3-4B model record.")
        else:
            print(f"  Found existing model: {model.name}")

        # ── 3. Create real worker ────────────────────────────────────────
        worker = Worker(
            hostname="runpod-l4",
            gpu_type="NVIDIA L4",
            gpu_vram_mb=23034,
            endpoint=REAL_VLLM_ENDPOINT,
            status="healthy",
        )
        db.add(worker)
        await db.flush()
        print(f"  Created real worker: {worker.hostname} → {worker.endpoint}")

        # ── 4. Create real deployment ────────────────────────────────────
        deployment = Deployment(
            model_id=model.id,
            worker_id=worker.id,
            endpoint=REAL_VLLM_ENDPOINT,
            config_json={
                "dtype": "bfloat16",
                "max_model_len": 8192,
                "gpu_memory_utilization": 0.90,
                "max_lora_rank": 16,
                "max_loras": 4,
                "enable_prefix_caching": True,
                "vllm_version": "0.28.0",
            },
            status="running",
        )
        db.add(deployment)
        await db.flush()
        print(f"  Created real deployment: {deployment.id}")

        # ── 5. Re-point 'test-assistant' alias to real deployment ────────
        alias = (
            await db.execute(select(ModelAlias).where(ModelAlias.alias == "test-assistant"))
        ).scalar_one_or_none()
        if alias:
            alias.deployment_id = deployment.id
            alias.base_model_id = model.id
            alias.adapter_id = None
            print(f"  Updated 'test-assistant' alias → new deployment {deployment.id}")
        else:
            print("  'test-assistant' alias not found — run seed_dev_data.py first.")

        await db.commit()
        print("")
        print("Real worker seeded successfully.")
        print(f"  Worker endpoint : {REAL_VLLM_ENDPOINT}")
        print(f"  Deployment ID   : {deployment.id}")
        print(f"  Model           : Qwen/Qwen3-4B (8192 ctx, LoRA enabled)")


if __name__ == "__main__":
    asyncio.run(seed_real_worker())
