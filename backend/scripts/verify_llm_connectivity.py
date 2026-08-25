"""Perform a minimal, secret-safe request against the selected LLM provider."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.core.config import settings
from app.services.llm_provider import (
    build_llm_provider,
    llm_is_configured,
    selected_llm_credential_name,
    selected_llm_provider,
)


def _safe_error(exc: Exception) -> dict[str, Any]:
    """Return useful API diagnostics without serializing request headers."""
    return {
        "error_type": type(exc).__name__,
        "status_code": getattr(exc, "status_code", None),
        "error_code": getattr(exc, "code", None),
        "message": str(exc)[:300],
    }


async def main() -> int:
    started = time.monotonic()
    provider_name = selected_llm_provider()
    result: dict[str, Any] = {
        "provider": provider_name,
        "credential": selected_llm_credential_name(),
        "key_configured": llm_is_configured(),
        "requested_model": settings.AI_MODEL,
    }
    if not llm_is_configured():
        result.update(
            success=False,
            error_type="ConfigurationError",
            message=f"{selected_llm_credential_name()} is not configured",
        )
        print(json.dumps(result, ensure_ascii=False))
        return 1

    client = None
    try:
        provider = build_llm_provider()
        client = provider.client.with_options(timeout=30.0, max_retries=0)
        response = await client.responses.create(
            model=settings.AI_MODEL,
            input="Reply with exactly OK.",
            max_output_tokens=16,
            store=False,
        )
        result.update(
            success=True,
            returned_model=response.model,
            output=(response.output_text or "").strip()[:40],
        )
    except Exception as exc:  # The verifier must classify SDK/network failures.
        result.update(success=False, **_safe_error(exc))
    finally:
        if client is not None:
            await client.close()

    result["latency_seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
