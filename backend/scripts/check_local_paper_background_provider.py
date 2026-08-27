"""Administrator-only probe for a Responses-compatible background provider.

Run manually after explicitly allowing temporary provider-side task storage.
The probe deliberately creates one tiny request, retrieves it once, and
cancels it if still pending; it is never called by end-user analysis jobs.
"""

from __future__ import annotations

import asyncio
import json

from openai import AsyncOpenAI

from app.core.config import settings


async def main() -> None:
    if not settings.LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE:
        raise SystemExit("Set LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE=true first.")
    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.LLM_BASE_URL.rstrip("/"),
        timeout=settings.LOCAL_PAPER_ANALYSIS_BACKGROUND_SUBMIT_TIMEOUT_SECONDS,
        max_retries=0,
    )
    try:
        response = await client.responses.create(
            model=settings.AI_MODEL,
            input="Reply with exactly OK.",
            background=True,
            store=False,
            max_output_tokens=16,
            reasoning={"effort": "low"},
        )
        result: dict[str, object] = {
            "background_create_supported": True,
            "initial_status": str(response.status),
            "retrieve_supported": False,
            "cancel_supported": False,
        }
        try:
            retrieved = await client.responses.retrieve(response.id)
            result["retrieve_supported"] = True
            result["retrieved_status"] = str(retrieved.status)
        except Exception as exc:
            result["retrieve_error_type"] = type(exc).__name__
            result["retrieve_status_code"] = getattr(exc, "status_code", None)
        try:
            await client.responses.cancel(response.id)
            result["cancel_supported"] = True
        except Exception as exc:
            result["cancel_error_type"] = type(exc).__name__
            result["cancel_status_code"] = getattr(exc, "status_code", None)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
