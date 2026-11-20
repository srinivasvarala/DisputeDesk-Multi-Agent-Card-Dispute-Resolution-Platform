"""One in-memory OTel SDK for the whole test run (the API only lets the globals be set once)."""

from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_SPANS = InMemorySpanExporter()
_READER = InMemoryMetricReader()
_installed = False


def install_test_sdk() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    global _installed
    if not _installed:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_SPANS))
        trace.set_tracer_provider(provider)
        metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))
        _installed = True
    return _SPANS, _READER
