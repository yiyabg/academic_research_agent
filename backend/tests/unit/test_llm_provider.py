"""Generative LLM provider-selection regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.llm_provider import (
    available_llm_models,
    build_llm_model,
    build_llm_provider,
    llm_is_configured,
    probe_llm_provider,
    reset_llm_health_cache,
    selected_llm_api_key,
    selected_llm_credential_name,
    selected_llm_model_identifier,
    selected_llm_provider,
)


def test_openai_provider_uses_only_openai_credential() -> None:
    with (
        patch("app.services.llm_provider.settings.LLM_PROVIDER", "openai"),
        patch("app.services.llm_provider.settings.OPENAI_API_KEY", "openai-fixture"),
        patch("app.services.llm_provider.settings.DEEPSEEK_API_KEY", "deepseek-fixture"),
        patch("app.services.llm_provider.settings.AI_MODEL", "gpt-fixture"),
    ):
        assert selected_llm_api_key() == "openai-fixture"
        assert selected_llm_credential_name() == "OPENAI_API_KEY"
        assert selected_llm_model_identifier() == "openai:gpt-fixture"
        assert build_llm_provider().name == "openai"
        assert build_llm_model().model_name == "gpt-fixture"


def test_deepseek_provider_uses_only_deepseek_credential_and_models() -> None:
    with (
        patch("app.services.llm_provider.settings.LLM_PROVIDER", "deepseek"),
        patch("app.services.llm_provider.settings.OPENAI_API_KEY", ""),
        patch("app.services.llm_provider.settings.DEEPSEEK_API_KEY", "deepseek-fixture"),
        patch("app.services.llm_provider.settings.AI_MODEL", "deepseek-v4-pro"),
    ):
        assert llm_is_configured() is True
        assert selected_llm_api_key() == "deepseek-fixture"
        assert selected_llm_credential_name() == "DEEPSEEK_API_KEY"
        assert selected_llm_model_identifier() == "deepseek:deepseek-v4-pro"
        assert available_llm_models() == ["deepseek-v4-pro", "deepseek-v4-flash"]
        assert build_llm_provider().name == "deepseek"
        assert build_llm_model().model_name == "deepseek-v4-pro"


def test_selected_provider_does_not_fall_back_to_other_provider_key() -> None:
    with (
        patch("app.services.llm_provider.settings.LLM_PROVIDER", "deepseek"),
        patch("app.services.llm_provider.settings.OPENAI_API_KEY", "openai-fixture"),
        patch("app.services.llm_provider.settings.DEEPSEEK_API_KEY", ""),
    ):
        assert llm_is_configured() is False
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
            build_llm_provider()


def test_project_scoped_openai_compatible_provider_uses_custom_base_url() -> None:
    with (
        patch("app.services.llm_provider.settings.LLM_PROVIDER", "openai_compatible"),
        patch("app.services.llm_provider.settings.LLM_BASE_URL", "https://example.net/v1"),
        patch("app.services.llm_provider.settings.OPENAI_API_KEY", "gateway-fixture"),
        patch("app.services.llm_provider.settings.AI_MODEL", "gpt-5.5"),
    ):
        provider = build_llm_provider()
        assert selected_llm_provider() == "openai_compatible"
        assert str(provider.client.base_url) == "https://example.net/v1/"
        identity = selected_llm_model_identifier()
        assert identity.startswith("openai_compatible[")
        assert identity.endswith(":gpt-5.5")


@pytest.mark.parametrize(
    ("provider", "model"),
    [("deepseek", "gpt-5.5"), ("openai", "deepseek-v4-pro")],
)
def test_settings_reject_cross_provider_model_pair(provider: str, model: str) -> None:
    with pytest.raises(ValidationError, match=r"LLM_PROVIDER|AI_MODEL"):
        Settings(LLM_PROVIDER=provider, AI_MODEL=model)  # type: ignore[arg-type]


def test_settings_require_https_for_openai_compatible() -> None:
    with pytest.raises(ValidationError, match="HTTPS LLM_BASE_URL"):
        Settings(
            LLM_PROVIDER="openai_compatible",
            LLM_BASE_URL="http://gateway.example.net/v1",
            AI_MODEL="gpt-5.5",
        )


@pytest.mark.anyio
async def test_compatible_health_probe_uses_responses_api() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=SimpleNamespace(model="gpt-5.5"))
    client.close = AsyncMock()
    provider = MagicMock()
    provider.client.with_options.return_value = client

    reset_llm_health_cache()
    with (
        patch("app.services.llm_provider.settings.LLM_PROVIDER", "openai_compatible"),
        patch("app.services.llm_provider.settings.LLM_BASE_URL", "https://example.net/v1"),
        patch("app.services.llm_provider.settings.OPENAI_API_KEY", "gateway-fixture"),
        patch("app.services.llm_provider.settings.AI_MODEL", "gpt-5.5"),
        patch("app.services.llm_provider.build_llm_provider", return_value=provider),
    ):
        result = await probe_llm_provider()

    assert result["status"] == "healthy"
    assert result["probe"] == "responses.create"
    assert result["resolved_model"] == "gpt-5.5"
    provider.client.with_options.assert_called_once_with(timeout=15.0, max_retries=0)
    client.responses.create.assert_awaited_once_with(
        model="gpt-5.5",
        input="Reply with exactly OK.",
        max_output_tokens=16,
        store=False,
    )
    client.models.retrieve.assert_not_called()
    client.close.assert_awaited_once()
    reset_llm_health_cache()
