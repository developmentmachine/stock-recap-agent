"""OpenTelemetry：可选启用；未启用时使用 NoOpTracer。"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = logging.getLogger("agent_platform.observability.tracing")

_configured: bool = False


def configure_tracing(settings: Any) -> None:
    """幂等：按 Settings 安装 TracerProvider（或 NoOp）。"""
    global _configured
    if _configured:
        return
    _configured = True

    enabled = bool(getattr(settings, "otel_enabled", False))
    if not enabled:
        trace.set_tracer_provider(trace.NoOpTracerProvider())
        return

    exporter = str(getattr(settings, "otel_exporter", "none")).lower()
    if exporter == "none":
        logger.info("RECAP_OTEL_ENABLED=true but RECAP_OTEL_EXPORTER=none; using NoOp tracer")
        trace.set_tracer_provider(trace.NoOpTracerProvider())
        return

    resource = Resource.create(
        {
            "service.name": getattr(settings, "otel_service_name", "stock-recap"),
            "service.version": "1.0.0",
        }
    )
    provider = TracerProvider(resource=resource)

    if exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "otlp":
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or getattr(
            settings, "otel_otlp_endpoint", None
        )
        if not endpoint:
            logger.warning("otel_exporter=otlp but OTEL_EXPORTER_OTLP_ENDPOINT unset; tracing disabled")
            trace.set_tracer_provider(trace.NoOpTracerProvider())
            return
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() in (
                "1",
                "true",
                "yes",
            )
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=insecure))
            )
        except Exception as e:
            logger.warning("OTLP exporter init failed: %s; using NoOp", e)
            trace.set_tracer_provider(trace.NoOpTracerProvider())
            return

    trace.set_tracer_provider(provider)


def get_tracer(module_name: Optional[str] = None) -> trace.Tracer:
    return trace.get_tracer(module_name or "agent_platform")
