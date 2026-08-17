#!/usr/bin/env python3
"""Smoke test for model arbitration (slices 1 and 2).

Run from the project root with the Maestro venv active:

    python scripts/smoke_test_model_arbitration.py

The script exercises:
1. Runtime availability detection.
2. Capability-aware model selection (cheapest capable model).
3. Latency-aware filtering (--latency-tolerance).
4. Local-model preference (--prefer-local) with a mocked Ollama server.
5. Max-cost-level filtering (--max-cost-level).
6. Latency cache write/read.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

from open_maestro.config.capabilities import CostLevel, TaskProfile
from open_maestro.runtime import availability, factory, latency


def _clear_env() -> None:
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPEN_MAESTRO_PREFER_CLI"):
        os.environ.pop(key, None)


def test_runtimes_listed() -> None:
    runtimes = factory.list_runtimes()
    print("Available runtimes:")
    for name, available in runtimes.items():
        print(f"  {name}: {'available' if available else 'not available'}")
    assert any(runtimes.values()), "At least one runtime should be available"


def test_cheapest_capable_model() -> None:
    _clear_env()
    os.environ["OPENAI_API_KEY"] = "sk-test"
    runtime, model = factory.select_runtime_for_task(TaskProfile())
    print(f"\nDefault cheapest capable: {runtime} / {model}")
    assert runtime == "openai-sdk"
    assert model == "gpt-4o-mini"


def test_max_cost_level() -> None:
    _clear_env()
    os.environ["OPENAI_API_KEY"] = "sk-test"
    runtime, model = factory.select_runtime_for_task(
        TaskProfile(), max_cost_level=CostLevel.LOW
    )
    print(f"Max cost level LOW: {runtime} / {model}")
    assert model == "gpt-4o-mini"


def test_latency_tolerance() -> None:
    _clear_env()
    os.environ["OPENAI_API_KEY"] = "sk-test"
    # With tolerance 1.0 only the fastest models survive. The exact winner
    # depends on declared latency hints, but it should still be openai-sdk.
    runtime, model = factory.select_runtime_for_task(
        TaskProfile(), latency_tolerance=1.0
    )
    print(f"Latency tolerance 1.0: {runtime} / {model}")
    assert runtime == "openai-sdk"


def test_prefer_local_with_mocked_ollama() -> None:
    _clear_env()
    os.environ["OPENAI_BASE_URL"] = "http://localhost:11434/v1"

    with mock.patch.object(
        availability, "_ollama_models", return_value={"llama3.3", "qwen2.5-coder", "llama3.1:8b"}
    ):
        runtime, model = factory.select_runtime_for_task(
            TaskProfile(), prefer_local=True
        )
        print(f"Prefer local (Ollama mocked): {runtime} / {model}")
        assert runtime == "openai-sdk"
        assert model in {"llama3.3", "qwen2.5-coder", "llama3.1:8b"}


def test_local_endpoint_does_not_serve_cloud_models() -> None:
    _clear_env()
    os.environ["OPENAI_BASE_URL"] = "http://localhost:11434/v1"

    with mock.patch.object(availability, "_ollama_models", return_value={"llama3.3"}):
        runtime, model = factory.select_runtime_for_task(
            TaskProfile(), runtime_type="openai-sdk"
        )
        print(f"Local endpoint only (no API key): {runtime} / {model}")
        assert model == "llama3.3"


def test_latency_cache() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "latency.yaml"
        cache = latency.LatencyCache.load(cache_path)
        cache.record("gpt-4o-mini", tokens_per_second=100.0)
        cache.save(cache_path)

        reloaded = latency.LatencyCache.load(cache_path)
        assert reloaded.tokens_per_second("gpt-4o-mini") == 100.0
        print("\nLatency cache write/read: OK")


def main() -> None:
    print("=== Open Maestro model-arbitration smoke test ===\n")
    test_runtimes_listed()
    test_cheapest_capable_model()
    test_max_cost_level()
    test_latency_tolerance()
    test_prefer_local_with_mocked_ollama()
    test_local_endpoint_does_not_serve_cloud_models()
    test_latency_cache()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
