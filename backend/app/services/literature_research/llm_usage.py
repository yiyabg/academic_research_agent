"""Run-scoped LLM usage collection and deterministic budget enforcement."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic_ai.usage import RunUsage, UsageLimits

from app.schemas.literature_research.protocol import LLMBudgetPolicy


class ResearchLLMBudgetExceeded(RuntimeError):
    """Raised after provider-reported usage crosses an approved protocol limit."""


@dataclass
class _AgentUsage:
    invocations: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reported_cost_usd: Decimal = Decimal("0")
    cost_reports: int = 0
    details: dict[str, int] = field(default_factory=dict)


class LLMUsageCollector:
    def __init__(self, budget: LLMBudgetPolicy) -> None:
        self.budget = budget
        self._agents: dict[str, _AgentUsage] = {}

    def record(self, agent_name: str, usage: RunUsage) -> None:
        item = self._agents.setdefault(agent_name, _AgentUsage())
        item.invocations += 1
        item.requests += int(usage.requests)
        item.input_tokens += int(usage.input_tokens)
        item.output_tokens += int(usage.output_tokens)
        item.cache_read_tokens += int(usage.cache_read_tokens)
        item.cache_write_tokens += int(usage.cache_write_tokens)
        if usage.cost is not None:
            item.reported_cost_usd += Decimal(usage.cost)
            item.cost_reports += 1
        for key, value in usage.details.items():
            if isinstance(value, (int, float)):
                item.details[key] = item.details.get(key, 0) + int(value)
        self._check_budget()

    def _check_budget(self) -> None:
        total = self._totals()
        checks = (
            ("requests", total.requests, self.budget.max_requests),
            ("input_tokens", total.input_tokens, self.budget.max_input_tokens),
            ("output_tokens", total.output_tokens, self.budget.max_output_tokens),
            (
                "total_tokens",
                total.input_tokens + total.output_tokens,
                self.budget.max_total_tokens,
            ),
        )
        for name, actual, limit in checks:
            if actual > limit:
                raise ResearchLLMBudgetExceeded(
                    f"Approved LLM budget exceeded: {name}={actual}, limit={limit}"
                )
        if self.budget.max_cost_usd is not None:
            if total.cost_reports != total.invocations:
                raise ResearchLLMBudgetExceeded(
                    "Approved USD budget cannot be verified because the provider omitted cost"
                )
            if total.reported_cost_usd > self.budget.max_cost_usd:
                raise ResearchLLMBudgetExceeded(
                    "Approved LLM budget exceeded: "
                    f"cost_usd={total.reported_cost_usd}, limit={self.budget.max_cost_usd}"
                )

    def _totals(self) -> _AgentUsage:
        total = _AgentUsage()
        for item in self._agents.values():
            total.invocations += item.invocations
            total.requests += item.requests
            total.input_tokens += item.input_tokens
            total.output_tokens += item.output_tokens
            total.cache_read_tokens += item.cache_read_tokens
            total.cache_write_tokens += item.cache_write_tokens
            total.reported_cost_usd += item.reported_cost_usd
            total.cost_reports += item.cost_reports
            for key, value in item.details.items():
                total.details[key] = total.details.get(key, 0) + value
        return total

    def snapshot(self) -> dict[str, Any]:
        return _snapshot(self._agents)

    def usage_limits(self, *, request_limit: int = 3) -> UsageLimits:
        """Return provider-side limits for the still available operation budget."""
        total = self._totals()
        remaining_requests = self.budget.max_requests - total.requests
        remaining_input = self.budget.max_input_tokens - total.input_tokens
        remaining_output = self.budget.max_output_tokens - total.output_tokens
        remaining_total = self.budget.max_total_tokens - total.input_tokens - total.output_tokens
        if min(remaining_requests, remaining_input, remaining_output, remaining_total) <= 0:
            self._check_budget()
            raise ResearchLLMBudgetExceeded("Approved LLM operation budget is exhausted")
        return UsageLimits(
            request_limit=min(request_limit, remaining_requests),
            input_tokens_limit=remaining_input,
            output_tokens_limit=remaining_output,
            total_tokens_limit=remaining_total,
            cost_limit=self.budget.max_cost_usd,
            count_tokens_before_request=True,
        )


def _usage_payload(item: _AgentUsage) -> dict[str, Any]:
    complete = item.cost_reports == item.invocations and item.invocations > 0
    return {
        "invocations": item.invocations,
        "requests": item.requests,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "total_tokens": item.input_tokens + item.output_tokens,
        "cache_read_tokens": item.cache_read_tokens,
        "cache_write_tokens": item.cache_write_tokens,
        "cost_usd": str(item.reported_cost_usd) if complete else None,
        "cost_status": "REPORTED" if complete else "UNAVAILABLE",
        "details": dict(sorted(item.details.items())),
    }


def _snapshot(agents: dict[str, _AgentUsage]) -> dict[str, Any]:
    total = _AgentUsage()
    for item in agents.values():
        total.invocations += item.invocations
        total.requests += item.requests
        total.input_tokens += item.input_tokens
        total.output_tokens += item.output_tokens
        total.cache_read_tokens += item.cache_read_tokens
        total.cache_write_tokens += item.cache_write_tokens
        total.reported_cost_usd += item.reported_cost_usd
        total.cost_reports += item.cost_reports
        for key, value in item.details.items():
            total.details[key] = total.details.get(key, 0) + value
    return {
        "total": _usage_payload(total),
        "by_agent": {name: _usage_payload(item) for name, item in sorted(agents.items())},
    }


def aggregate_usage_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    agents: dict[str, _AgentUsage] = {}
    for snapshot in snapshots:
        raw_agents = snapshot.get("by_agent", {})
        if not isinstance(raw_agents, dict):
            continue
        for name, payload in raw_agents.items():
            if not isinstance(payload, dict):
                continue
            item = agents.setdefault(str(name), _AgentUsage())
            item.invocations += int(payload.get("invocations", 0))
            item.requests += int(payload.get("requests", 0))
            item.input_tokens += int(payload.get("input_tokens", 0))
            item.output_tokens += int(payload.get("output_tokens", 0))
            item.cache_read_tokens += int(payload.get("cache_read_tokens", 0))
            item.cache_write_tokens += int(payload.get("cache_write_tokens", 0))
            cost = payload.get("cost_usd")
            if cost is not None:
                item.reported_cost_usd += Decimal(str(cost))
                item.cost_reports += int(payload.get("invocations", 0))
            details = payload.get("details", {})
            if isinstance(details, dict):
                for key, value in details.items():
                    if isinstance(value, (int, float)):
                        item.details[str(key)] = item.details.get(str(key), 0) + int(value)
    return _snapshot(agents)


_ACTIVE_COLLECTOR: ContextVar[LLMUsageCollector | None] = ContextVar(
    "literature_research_llm_usage", default=None
)


@contextmanager
def collect_llm_usage(budget: LLMBudgetPolicy) -> Iterator[LLMUsageCollector]:
    collector = LLMUsageCollector(budget)
    token = _ACTIVE_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE_COLLECTOR.reset(token)


def record_active_usage(agent_name: str, usage: RunUsage) -> None:
    collector = _ACTIVE_COLLECTOR.get()
    if collector is not None:
        collector.record(agent_name, usage)


def has_reported_usage(usage: RunUsage) -> bool:
    return bool(
        usage.requests
        or usage.input_tokens
        or usage.output_tokens
        or usage.cache_read_tokens
        or usage.cache_write_tokens
        or usage.cost is not None
    )


def active_usage_limits(*, request_limit: int = 3) -> UsageLimits:
    collector = _ACTIVE_COLLECTOR.get()
    if collector is None:
        return UsageLimits(request_limit=request_limit)
    return collector.usage_limits(request_limit=request_limit)


def attach_usage(exc: Exception, snapshot: dict[str, Any]) -> None:
    with suppress(Exception):
        exc.__dict__["research_llm_usage"] = snapshot


def attached_usage(exc: Exception) -> dict[str, Any] | None:
    value = getattr(exc, "research_llm_usage", None)
    return value if isinstance(value, dict) else None
