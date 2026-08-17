---
id: opentelemetry
name: Opentelemetry
tags:
- observability
- opentelemetry
- tracing
- metrics
- logs
- otlp
---

# OpenTelemetry

## Quick Start (signal design)

- Export OTLP via an OpenTelemetry Collector (vendor-neutral endpoint).
- Standardize resource attributes: `service.name`, `service.version`, `deployment.environment`.
- Start with auto-instrumentation, then add manual spans and log correlation.

## Load Next (References)

- `references/concepts.md` — traces/metrics/logs, context propagation, sampling, semantic conventions
- `references/collector-and-otlp.md` — Collector pipelines, processors, deployment patterns, tail sampling
- `references/instrumentation-and-troubleshooting.md` — manual spans, propagation pitfalls, cardinality, debugging