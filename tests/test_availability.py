"""Tests for runtime/model availability probing."""

from __future__ import annotations

from unittest import mock

import pytest

from open_maestro.config.capabilities import CapabilityRegistry
from open_maestro.runtime import availability


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.load()


def test_local_models_present(registry: CapabilityRegistry) -> None:
    local_models = [m for m in registry.list_models() if m.provider in ("ollama", "local")]
    assert len(local_models) >= 4
    ids = {m.id for m in local_models}
    assert "ollama-llama3-3" in ids
    assert "ollama-qwen2-5-coder" in ids


def test_is_runtime_available_checks_binary() -> None:
    with mock.patch("shutil.which", return_value="/bin/kimi"):
        assert availability.is_runtime_available("kimi-cli") is True
    with mock.patch("shutil.which", return_value=None):
        assert availability.is_runtime_available("kimi-cli") is False


def test_is_runtime_available_openai_sdk_no_package() -> None:
    with mock.patch.object(availability, "_sdk_package_available", return_value=False):
        assert availability.is_runtime_available("openai-sdk") is False


def test_is_runtime_available_openai_sdk_cloud() -> None:
    with mock.patch.object(availability, "_sdk_package_available", return_value=True):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            assert availability.is_runtime_available("openai-sdk") is True


def test_is_model_available_cloud_without_key(registry: CapabilityRegistry) -> None:
    model = registry.models["openai-gpt4o-mini"]
    with mock.patch.object(availability, "_sdk_package_available", return_value=True):
        with mock.patch.dict("os.environ", {}, clear=True):
            assert availability.is_model_available("openai-sdk", model) is False


def test_is_model_available_cloud_with_key(registry: CapabilityRegistry) -> None:
    model = registry.models["openai-gpt4o-mini"]
    with mock.patch.object(availability, "_sdk_package_available", return_value=True):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=True):
            assert availability.is_model_available("openai-sdk", model) is True


def test_is_model_available_ollama_model_pulled(registry: CapabilityRegistry) -> None:
    model = registry.models["ollama-llama3-3"]
    with mock.patch.object(availability, "_sdk_package_available", return_value=True):
        with mock.patch.dict(
            "os.environ",
            {"OPENAI_BASE_URL": "http://localhost:11434/v1"},
            clear=True,
        ):
            with mock.patch.object(
                availability, "_ollama_models", return_value={"llama3.3"}
            ):
                assert availability.is_model_available("openai-sdk", model) is True


def test_is_model_available_ollama_model_missing(registry: CapabilityRegistry) -> None:
    model = registry.models["ollama-llama3-3"]
    with mock.patch.object(availability, "_sdk_package_available", return_value=True):
        with mock.patch.dict(
            "os.environ",
            {"OPENAI_BASE_URL": "http://localhost:11434/v1"},
            clear=True,
        ):
            with mock.patch.object(availability, "_ollama_models", return_value=set()):
                assert availability.is_model_available("openai-sdk", model) is False


def test_is_model_available_cli_runtime_unavailable(registry: CapabilityRegistry) -> None:
    model = registry.models["claude-sonnet"]
    with mock.patch("shutil.which", return_value=None):
        assert availability.is_model_available("claude-cli", model) is False
