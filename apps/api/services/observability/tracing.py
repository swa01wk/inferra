"""OpenTelemetry distributed tracing setup.

Disabled by default (settings.otel_enabled = False).
Activate by setting env vars:
    OTEL_ENABLED=true
    OTEL_ENDPOINT=http://<collector>:4317

When enabled, every FastAPI request and outbound httpx call (to vLLM) gets a
trace span that can be visualised in Jaeger, Grafana Tempo, or any OTLP backend.
"""

from __future__ import annotations

import logging

from apps.api.config import settings

logger = logging.getLogger("inferra.tracing")


def setup_tracing() -> None:
    """Configure and register OpenTelemetry instrumentors.

    Safe to call unconditionally — is a no-op when otel_enabled=False.
    """
    if not settings.otel_enabled:
        logger.debug("OpenTelemetry disabled (OTEL_ENABLED=false)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": settings.app_name, "service.version": settings.app_version})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()

        logger.info("OpenTelemetry tracing enabled → %s", settings.otel_endpoint)
    except ImportError as exc:
        logger.warning("OpenTelemetry packages not available: %s", exc)
    except Exception:
        logger.exception("Failed to configure OpenTelemetry tracing")
