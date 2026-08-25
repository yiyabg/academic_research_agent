"""Protocol compiler and immutable quality-floor tests."""

from datetime import date
from decimal import Decimal

from app.schemas.literature_research.protocol import (
    AmbiguityStatus,
    ConstraintOperator,
    DocumentType,
    LLMBudgetPolicy,
    ProtocolAdviceProvenance,
    ProtocolCompileRequest,
    ProtocolConstraint,
)
from app.services.literature_research.protocol_compiler import (
    ProtocolCompilerService,
    subtract_calendar_months,
)


def test_calendar_month_subtraction_clamps_day() -> None:
    assert subtract_calendar_months(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert subtract_calendar_months(date(2024, 5, 31), 3) == date(2024, 2, 29)


def test_default_protocol_is_executable_and_hash_is_semantically_stable() -> None:
    compiler = ProtocolCompilerService()
    request = ProtocolCompileRequest(
        topic="evidence grounded agents",
        as_of_date=date(2026, 8, 21),
        allowed_types=[DocumentType.JOURNAL_ARTICLE],
    )

    first = compiler.compile(request)
    second = compiler.compile(request)

    assert first.executable is True
    assert first.protocol.ambiguity_status == AmbiguityStatus.RESOLVED
    assert first.protocol.time_scope.date_from == date(2026, 5, 21)
    assert first.protocol.time_scope.date_to == date(2026, 8, 21)
    assert first.protocol.quantity_policy.quality_floor_locked is True
    assert first.protocol_hash == second.protocol_hash
    assert first.protocol.protocol_id != second.protocol.protocol_id


def test_journal_metric_is_scoped_and_requires_separate_conference_policy() -> None:
    result = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="semantic communications agents",
            as_of_date=date(2026, 8, 21),
            constraints=[
                ProtocolConstraint(
                    constraint_id="jif",
                    field="venue.metric.jif",
                    operator=ConstraintOperator.GT,
                    value=7,
                    verification_source="licensed-jcr",
                )
            ],
        )
    )

    jif = next(item for item in result.protocol.constraints if item.constraint_id == "jif")
    assert jif.applies_to == [DocumentType.JOURNAL_ARTICLE]
    assert result.executable is False
    assert {issue.code for issue in result.protocol.issues} == {"CONFERENCE_QUALITY_POLICY_MISSING"}


def test_journal_metric_explicitly_applied_to_conference_is_blocking() -> None:
    result = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="semantic communications agents",
            as_of_date=date(2026, 8, 21),
            constraints=[
                ProtocolConstraint(
                    constraint_id="invalid-jif",
                    field="venue.metric.jif",
                    operator=ConstraintOperator.GT,
                    value=7,
                    verification_source="licensed-jcr",
                    applies_to=[DocumentType.CONFERENCE_PAPER],
                ),
                ProtocolConstraint(
                    constraint_id="conference-rank",
                    field="venue.metric.conference_rank",
                    operator=ConstraintOperator.IN,
                    value=["CCF_A"],
                    verification_source="ccf-snapshot",
                    applies_to=[DocumentType.CONFERENCE_PAPER],
                ),
            ],
        )
    )
    assert result.executable is False
    assert "JOURNAL_METRIC_NOT_APPLICABLE_TO_CONFERENCE" in {
        issue.code for issue in result.protocol.issues
    }


def test_insufficient_source_coverage_requires_clarification() -> None:
    result = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="agent systems",
            as_of_date=date(2026, 8, 21),
            allowed_types=[DocumentType.JOURNAL_ARTICLE],
            required_sources=["crossref"],
            optional_sources=[],
            minimum_source_families=2,
        )
    )
    assert result.executable is False
    assert result.protocol.issues[0].code == "INSUFFICIENT_SOURCE_FAMILIES"


def test_llm_budget_is_part_of_the_approved_protocol_hash() -> None:
    compiler = ProtocolCompilerService()
    base = ProtocolCompileRequest(
        topic="agent systems",
        as_of_date=date(2026, 8, 21),
        allowed_types=[DocumentType.JOURNAL_ARTICLE],
    )
    constrained = base.model_copy(
        update={
            "llm_budget": LLMBudgetPolicy(
                max_requests=32,
                max_input_tokens=200_000,
                max_output_tokens=20_000,
                max_total_tokens=220_000,
                max_cost_usd=Decimal("2.500000"),
            )
        }
    )

    default_result = compiler.compile(base)
    constrained_result = compiler.compile(constrained)

    assert constrained_result.protocol.llm_budget.max_requests == 32
    assert constrained_result.protocol.llm_budget.max_cost_usd == Decimal("2.500000")
    assert constrained_result.protocol_hash != default_result.protocol_hash


def test_advice_provenance_and_ambiguities_are_hash_bound_and_blocking() -> None:
    compiler = ProtocolCompilerService()
    request = ProtocolCompileRequest(
        topic="agent systems",
        as_of_date=date(2026, 8, 21),
        allowed_types=[DocumentType.JOURNAL_ARTICLE],
    )
    provenance = ProtocolAdviceProvenance(
        provider="deepseek",
        model_identifier="deepseek:deepseek-chat",
        prompt_version="test.1",
        llm_usage={"total": {"requests": 1, "total_tokens": 42}},
    )

    plain = compiler.compile(request)
    advised = compiler.compile(
        request,
        advice_provenance=provenance,
        advice_ambiguities=["研究对象范围尚未明确"],
    )

    assert advised.executable is False
    assert advised.protocol.draft_advice_provenance == provenance
    assert advised.protocol.issues[-1].code == "AGENT_AMBIGUITY_1"
    assert advised.protocol_hash != plain.protocol_hash
