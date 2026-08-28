"""Single provider boundary for every generative LLM call in the application."""

from __future__ import annotations

import time
from hashlib import sha256
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.config import settings

LLMProviderName = Literal["openai", "deepseek", "openai_compatible"]
LLM_HEALTH_CACHE_SECONDS = 60.0
COMPATIBLE_LLM_HEALTH_CACHE_SECONDS = 300.0
COMPATIBLE_LLM_HEALTH_TIMEOUT_SECONDS = 15.0
_llm_health_cache: tuple[float, str, dict[str, Any]] | None = None


def selected_llm_provider() -> LLMProviderName:
    """Return the validated, normalized generative provider name."""
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider not in {"openai", "deepseek", "openai_compatible"}:
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")
    return provider  # type: ignore[return-value]


def selected_llm_api_key() -> str:
    """Return only the credential belonging to the selected provider."""
    return (
        settings.DEEPSEEK_API_KEY
        if selected_llm_provider() == "deepseek"
        else settings.OPENAI_API_KEY
    )


def selected_llm_credential_name() -> str:
    return "DEEPSEEK_API_KEY" if selected_llm_provider() == "deepseek" else "OPENAI_API_KEY"


def llm_is_configured() -> bool:
    return bool(selected_llm_api_key())


def selected_llm_model_identifier(model_name: str | None = None) -> str:
    """Stable provider-qualified model identity for hashes and manifests."""
    provider = selected_llm_provider()
    if provider == "openai_compatible":
        endpoint_hash = sha256(settings.LLM_BASE_URL.rstrip("/").encode()).hexdigest()[:12]
        provider = f"openai_compatible[{endpoint_hash}]"
    return f"{provider}:{model_name or settings.AI_MODEL}"


def available_llm_models() -> list[str]:
    """Return only models valid for the active provider, with the default first."""
    provider = selected_llm_provider()
    if provider == "openai_compatible":
        configured = [settings.AI_MODEL]
    else:
        configured = (
            settings.AI_AVAILABLE_MODELS
            if provider == "openai"
            else settings.DEEPSEEK_AVAILABLE_MODELS
        )
    return list(dict.fromkeys([settings.AI_MODEL, *configured]))


def build_llm_provider() -> OpenAIProvider | DeepSeekProvider:
    """Build the selected OpenAI-compatible PydanticAI provider."""
    provider = selected_llm_provider()
    api_key = selected_llm_api_key()
    if not api_key:
        raise RuntimeError(f"{selected_llm_credential_name()} is not configured")
    if provider == "openai":
        return OpenAIProvider(api_key=api_key)
    if provider == "deepseek":
        return DeepSeekProvider(api_key=api_key)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.LLM_BASE_URL.rstrip("/"),
    )
    return OpenAIProvider(openai_client=client)


def build_local_paper_analysis_model(
    *,
    timeout_seconds: float | None = None,
) -> OpenAIResponsesModel:
    """Create the bounded, no-retry model for one local-paper stage."""
    timeout = timeout_seconds or settings.LOCAL_PAPER_ANALYSIS_STAGE_TIMEOUT_SECONDS

    provider = selected_llm_provider()
    api_key = selected_llm_api_key()
    if not api_key:
        raise RuntimeError(f"{selected_llm_credential_name()} is not configured")
    if provider == "deepseek":
        # DeepSeekProvider owns its endpoint configuration; the analysis
        # orchestrator's outer stage timeout remains the authoritative budget.
        return OpenAIResponsesModel(settings.AI_MODEL, provider=DeepSeekProvider(api_key=api_key))
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.LLM_BASE_URL.rstrip("/") if provider == "openai_compatible" else None,
        timeout=timeout,
        max_retries=0,
    )
    return OpenAIResponsesModel(settings.AI_MODEL, provider=OpenAIProvider(openai_client=client))


def build_llm_model(model_name: str | None = None) -> OpenAIResponsesModel:
    """Build the Responses-compatible model used by assistants and experts."""
    return OpenAIResponsesModel(
        model_name or settings.AI_MODEL,
        provider=build_llm_provider(),
    )


async def probe_llm_provider(*, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Verify credential, network and model access with a provider-safe probe."""
    global _llm_health_cache

    provider_name = selected_llm_provider()
    model_identifier = selected_llm_model_identifier()
    if not llm_is_configured():
        return {
            "status": "unavailable",
            "provider": provider_name,
            "model": settings.AI_MODEL,
            "detail": f"{selected_llm_credential_name()} missing",
            "probe": "not_run",
        }

    now = time.monotonic()
    cache_seconds = (
        COMPATIBLE_LLM_HEALTH_CACHE_SECONDS
        if provider_name == "openai_compatible"
        else LLM_HEALTH_CACHE_SECONDS
    )
    if (
        _llm_health_cache
        and _llm_health_cache[1] == model_identifier
        and now - _llm_health_cache[0] < cache_seconds
    ):
        return dict(_llm_health_cache[2])

    started = time.monotonic()
    try:
        provider = build_llm_provider()
        effective_timeout = (
            max(timeout_seconds, COMPATIBLE_LLM_HEALTH_TIMEOUT_SECONDS)
            if provider_name == "openai_compatible"
            else timeout_seconds
        )
        client = provider.client.with_options(timeout=effective_timeout, max_retries=0)
        try:
            if provider_name == "openai_compatible":
                # A Responses-compatible gateway is not required to implement
                # OpenAI's Models API. Probe the contract it actually declares,
                # cache longer, and keep the request deliberately minimal.
                response = await client.responses.create(
                    model=settings.AI_MODEL,
                    input="Reply with exactly OK.",
                    max_output_tokens=16,
                    store=False,
                )
                resolved_model = response.model
                probe_name = "responses.create"
            else:
                model = await client.models.retrieve(settings.AI_MODEL)
                resolved_model = model.id
                probe_name = "models.retrieve"
        finally:
            await client.close()
        result = {
            "status": "healthy",
            "provider": provider_name,
            "model": settings.AI_MODEL,
            "resolved_model": resolved_model,
            "detail": "credential, network and model access verified",
            "probe": probe_name,
        }
    except Exception as exc:
        result = {
            "status": "unavailable",
            "provider": provider_name,
            "model": settings.AI_MODEL,
            "detail": "LLM provider probe failed",
            "probe": (
                "responses.create" if provider_name == "openai_compatible" else "models.retrieve"
            ),
            "error_type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
            "error_code": getattr(exc, "code", None),
        }
    result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    _llm_health_cache = (now, model_identifier, result)
    return dict(result)


def reset_llm_health_cache() -> None:
    """Clear the per-process probe cache for configuration reloads and tests."""
    global _llm_health_cache
    _llm_health_cache = None
