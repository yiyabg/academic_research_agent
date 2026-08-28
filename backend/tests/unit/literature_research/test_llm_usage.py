"""LLM usage ledger and fail-closed operation-budget tests."""

from decimal import Decimal

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from app.agents.literature_research.experts import StructuredExpert
from app.schemas.literature_research.protocol import LLMBudgetPolicy
from app.services.literature_research.llm_usage import (
    LLMUsageCollector,
    ResearchLLMBudgetExceeded,
    aggregate_usage_snapshots,
    collect_llm_usage,
)


def _budget(**overrides: object) -> LLMBudgetPolicy:
    values: dict[str, object] = {
        "max_requests": 4,
        "max_input_tokens": 2_000,
        "max_output_tokens": 1_000,
        "max_total_tokens": 3_000,
    }
    values.update(overrides)
    return LLMBudgetPolicy.model_validate(values)


def test_collector_records_provider_usage_by_agent() -> None:
    collector = LLMUsageCollector(_budget())
    collector.record(
        "analysis",
        RunUsage(
            requests=1,
            input_tokens=120,
            output_tokens=30,
            cache_read_tokens=20,
            details={"reasoning_tokens": 7},
            cost=Decimal("0.0123"),
        ),
    )

    snapshot = collector.snapshot()
    assert snapshot["total"] == {
        "invocations": 1,
        "requests": 1,
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cache_read_tokens": 20,
        "cache_write_tokens": 0,
        "cost_usd": "0.0123",
        "cost_status": "REPORTED",
        "details": {"reasoning_tokens": 7},
    }
    assert snapshot["by_agent"]["analysis"]["total_tokens"] == 150


def test_collector_rejects_reported_token_overage() -> None:
    collector = LLMUsageCollector(_budget(max_input_tokens=1_000, max_total_tokens=2_000))

    with pytest.raises(ResearchLLMBudgetExceeded, match="input_tokens=1001"):
        collector.record("analysis", RunUsage(requests=1, input_tokens=1_001))


def test_explicit_cost_ceiling_fails_closed_when_gateway_omits_cost() -> None:
    collector = LLMUsageCollector(_budget(max_cost_usd=Decimal("1.00")))

    with pytest.raises(ResearchLLMBudgetExceeded, match="provider omitted cost"):
        collector.record("analysis", RunUsage(requests=1, input_tokens=100))


def test_aggregate_preserves_unavailable_cost_and_retry_usage() -> None:
    reported = LLMUsageCollector(_budget())
    reported.record(
        "analysis",
        RunUsage(requests=1, input_tokens=100, output_tokens=10, cost=Decimal("0.01")),
    )
    unpriced_retry = LLMUsageCollector(_budget())
    unpriced_retry.record("analysis", RunUsage(requests=1, input_tokens=50, output_tokens=5))

    aggregate = aggregate_usage_snapshots([reported.snapshot(), unpriced_retry.snapshot()])

    assert aggregate["total"]["invocations"] == 2
    assert aggregate["total"]["requests"] == 2
    assert aggregate["total"]["total_tokens"] == 165
    assert aggregate["total"]["cost_usd"] is None
    assert aggregate["total"]["cost_status"] == "UNAVAILABLE"


@pytest.mark.anyio
async def test_failed_expert_attempt_preserves_mutated_run_usage() -> None:
    class FailingAgent:
        async def run(self, _prompt: str, **kwargs: object) -> None:
            usage = kwargs["usage"]
            assert isinstance(usage, RunUsage)
            usage.requests = 1
            usage.input_tokens = 75
            usage.output_tokens = 8
            raise RuntimeError("invalid structured output")

    expert = object.__new__(StructuredExpert)
    expert.name = "audit"
    expert.agent = FailingAgent()

    with (
        collect_llm_usage(_budget()) as collector,
        pytest.raises(RuntimeError, match="invalid structured output"),
    ):
        await expert.run({"claims": []})

    assert collector.snapshot()["by_agent"]["audit"]["total_tokens"] == 83


@pytest.mark.anyio
async def test_provider_usage_limit_is_translated_to_terminal_research_budget_error() -> None:
    class LimitedAgent:
        async def run(self, _prompt: str, **kwargs: object) -> None:
            usage = kwargs["usage"]
            assert isinstance(usage, RunUsage)
            usage.requests = 1
            usage.input_tokens = 55
            raise UsageLimitExceeded("input token ceiling reached")

    expert = object.__new__(StructuredExpert)
    expert.name = "relevance"
    expert.agent = LimitedAgent()

    with (
        collect_llm_usage(_budget()) as collector,
        pytest.raises(ResearchLLMBudgetExceeded, match="input token ceiling"),
    ):
        await expert.run({"papers": []})

    assert collector.snapshot()["by_agent"]["relevance"]["input_tokens"] == 55
