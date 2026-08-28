"""Provider boundary for durable local-paper analysis stages."""
# ruff: noqa: RUF001 - User-facing Chinese error summaries intentionally use full-width punctuation.

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic_ai import Agent

from app.core.config import settings
from app.services.llm_provider import build_local_paper_analysis_model, selected_llm_provider


@dataclass(frozen=True)
class ModelStageResult:
    content: str
    latency_ms: int


class ModelGatewayError(RuntimeError):
    def __init__(self, code: str, summary: str, *, raw_summary: str = "") -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.raw_summary = raw_summary[:4000]


def normalize_model_error(exc: Exception) -> ModelGatewayError:
    status_code = getattr(exc, "status_code", None)
    raw = str(exc).strip() or type(exc).__name__
    if status_code == 524 or "Error 524" in raw or "status_code: 524" in raw:
        return ModelGatewayError(
            "UPSTREAM_GATEWAY_TIMEOUT",
            "上游模型服务未能在规定时间内完成响应（HTTP 524）。",
            raw_summary=raw,
        )
    if isinstance(exc, TimeoutError):
        return ModelGatewayError(
            "CLIENT_TIMEOUT", "模型调用超过本地阶段时间预算。", raw_summary=raw
        )
    if status_code in {401, 403}:
        return ModelGatewayError("PROVIDER_AUTH_ERROR", "模型服务认证失败。", raw_summary=raw)
    if status_code == 429:
        return ModelGatewayError(
            "PROVIDER_RATE_LIMITED", "模型服务当前限流，请稍后重试。", raw_summary=raw
        )
    if status_code and int(status_code) >= 500:
        return ModelGatewayError("PROVIDER_UNAVAILABLE", "上游模型服务暂不可用。", raw_summary=raw)
    return ModelGatewayError("INVALID_MODEL_OUTPUT", "模型未返回可用的分析结果。", raw_summary=raw)


class PaperAnalysisModelGateway:
    """Executes bounded synchronous stages and explicit background responses."""

    @staticmethod
    def endpoint_hash() -> str:
        return hashlib.sha256(settings.LLM_BASE_URL.rstrip("/").encode()).hexdigest()[:16]

    async def complete(
        self, *, system_prompt: str, user_prompt: str, max_output_tokens: int
    ) -> ModelStageResult:
        started = time.monotonic()
        agent: Agent[str] = Agent(
            model=build_local_paper_analysis_model(
                timeout_seconds=settings.LOCAL_PAPER_ANALYSIS_STAGE_TIMEOUT_SECONDS
            ),
            system_prompt=system_prompt,
        )
        try:
            response = await asyncio.wait_for(
                agent.run(
                    user_prompt,
                    model_settings={
                        "max_tokens": max_output_tokens,
                        "openai_store": False,
                        "openai_reasoning_effort": settings.LOCAL_PAPER_ANALYSIS_REASONING_EFFORT,
                    },
                ),
                timeout=settings.LOCAL_PAPER_ANALYSIS_STAGE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise normalize_model_error(exc) from exc
        content = response.output.strip()
        if not content:
            raise ModelGatewayError("INVALID_MODEL_OUTPUT", "模型返回了空结果。")
        return ModelStageResult(
            content=content, latency_ms=round((time.monotonic() - started) * 1000)
        )

    def _background_client(self, *, timeout_seconds: float) -> AsyncOpenAI:
        if selected_llm_provider() != "openai_compatible":
            raise ModelGatewayError(
                "BACKGROUND_NOT_SUPPORTED", "当前模型提供商不支持后台分析模式。"
            )
        return AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.LLM_BASE_URL.rstrip("/"),
            timeout=timeout_seconds,
            max_retries=0,
        )

    async def submit_background(
        self, *, system_prompt: str, user_prompt: str, max_output_tokens: int
    ) -> tuple[str, str]:
        client = self._background_client(
            timeout_seconds=settings.LOCAL_PAPER_ANALYSIS_BACKGROUND_SUBMIT_TIMEOUT_SECONDS
        )
        try:
            response = await client.responses.create(
                model=settings.AI_MODEL,
                instructions=system_prompt,
                input=user_prompt,
                background=True,
                store=False,
                max_output_tokens=max_output_tokens,
                reasoning={"effort": settings.LOCAL_PAPER_ANALYSIS_REASONING_EFFORT},
            )
            return response.id, str(response.status)
        except Exception as exc:
            raise normalize_model_error(exc) from exc
        finally:
            await client.close()

    async def retrieve_background(self, response_id: str) -> tuple[str, str | None, str | None]:
        client = self._background_client(
            timeout_seconds=settings.LOCAL_PAPER_ANALYSIS_BACKGROUND_POLL_TIMEOUT_SECONDS
        )
        try:
            response = await client.responses.retrieve(response_id)
            output = getattr(response, "output_text", None)
            error = getattr(response, "error", None)
            error_message = getattr(error, "message", None) if error else None
            return str(response.status), output, error_message
        except Exception as exc:
            raise normalize_model_error(exc) from exc
        finally:
            await client.close()

    async def cancel_background(self, response_id: str) -> None:
        client = self._background_client(
            timeout_seconds=settings.LOCAL_PAPER_ANALYSIS_BACKGROUND_POLL_TIMEOUT_SECONDS
        )
        try:
            await client.responses.cancel(response_id)
        except Exception as exc:
            raise normalize_model_error(exc) from exc
        finally:
            await client.close()
