from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from apps.api.config import settings
from apps.api.middleware.logging import RequestLoggingMiddleware
from apps.api.routes import adapters, admin, admin_ui, chat, health, metrics, models
from apps.api.services.adapters.registry import ensure_bucket_exists
from apps.api.services.observability.tracing import setup_tracing
from db.session import init_db

logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing()
    await init_db()
    try:
        await ensure_bucket_exists()
    except Exception:
        logging.getLogger("inferra").exception("MinIO bucket setup skipped/failed")
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(chat.router, prefix="/v1")
app.include_router(models.router, prefix="/v1")
app.include_router(admin.router, prefix="/v1")
app.include_router(adapters.router, prefix="/v1")
app.include_router(admin_ui.router)
