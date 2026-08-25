"""Authorized metrics and hard fail-closed constraint tests."""

from datetime import date
from uuid import uuid4

from app.schemas.literature_research.protocol import (
    ConstraintOperator,
    ConstraintSeverity,
    DocumentType,
    MissingValuePolicy,
    ProtocolCompileRequest,
    ProtocolConstraint,
)
from app.schemas.literature_research.quality import (
    ConstraintDecision,
    ConstraintReasonCode,
    MetricObservation,
    WorkEvaluationContext,
)
from app.services.literature_research.constraint_engine import ConstraintEngine
from app.services.literature_research.protocol_compiler import ProtocolCompilerService

HASH = "sha256:" + "a" * 64


def protocol_with_metric(*, severity: ConstraintSeverity = ConstraintSeverity.HARD):
    return (
        ProtocolCompilerService()
        .compile(
            ProtocolCompileRequest(
                topic="auditable research agents",
                as_of_date=date(2026, 8, 21),
                allowed_types=[DocumentType.JOURNAL_ARTICLE],
                constraints=[
                    ProtocolConstraint(
                        constraint_id="jif-minimum",
                        field="venue.metric.jif",
                        operator=ConstraintOperator.GT,
                        value=7,
                        severity=severity,
                        verification_source="licensed-jcr-snapshot",
                        missing_value_policy=(
                            MissingValuePolicy.FAIL
                            if severity == ConstraintSeverity.HARD
                            else MissingValuePolicy.REVIEW
                        ),
                    )
                ],
            )
        )
        .protocol
    )


def context(metric: MetricObservation | None) -> WorkEvaluationContext:
    return WorkEvaluationContext(
        work_id=uuid4(),
        as_of_date=date(2026, 8, 21),
        document_type=DocumentType.JOURNAL_ARTICLE,
        work_fields={
            "work.effective_publication_date": "2026-07-01",
            "work.document_type": "journal_article",
        },
        metrics={"venue.metric.jif": metric} if metric else {},
    )


def observation(*, value=8.2, authorized=True, effective_from=date(2026, 1, 1)):
    return MetricObservation(
        fact_id=uuid4(),
        metric_name="jif",
        value=value,
        metric_year=2025,
        venue_name="Journal of Agent Systems",
        snapshot_id=uuid4(),
        source_name="JCR licensed export",
        source_version="2026",
        effective_from=effective_from,
        authorized=authorized,
        evidence_reference="metric-snapshot:fixture:row:2",
    )


def test_authorized_metric_passes_and_carries_snapshot_evidence() -> None:
    ledger = ConstraintEngine().evaluate(protocol_with_metric(), HASH, context(observation()))
    metric = next(item for item in ledger.evaluations if item.constraint_id == "jif-minimum")
    assert ledger.eligible is True
    assert metric.decision == ConstraintDecision.PASS
    assert metric.metric_snapshot_id is not None
    assert metric.metric_fact_id is not None
    assert metric.metric_year == 2025
    assert metric.evidence_reference == "metric-snapshot:fixture:row:2"


def test_missing_hard_metric_is_unknown_and_fails_closed() -> None:
    ledger = ConstraintEngine().evaluate(protocol_with_metric(), HASH, context(None))
    metric = next(item for item in ledger.evaluations if item.constraint_id == "jif-minimum")
    assert ledger.eligible is False
    assert ledger.hard_unknown_count == 1
    assert metric.decision == ConstraintDecision.UNKNOWN
    assert metric.reason_code == ConstraintReasonCode.VALUE_MISSING


def test_unauthorized_or_future_snapshot_cannot_pass_hard_constraint() -> None:
    unauthorized = ConstraintEngine().evaluate(
        protocol_with_metric(), HASH, context(observation(authorized=False))
    )
    future = ConstraintEngine().evaluate(
        protocol_with_metric(),
        HASH,
        context(observation(effective_from=date(2027, 1, 1))),
    )
    assert unauthorized.eligible is False
    assert unauthorized.evaluations[-1].reason_code == (ConstraintReasonCode.METRIC_NOT_AUTHORIZED)
    assert future.eligible is False
    assert future.evaluations[-1].reason_code == (
        ConstraintReasonCode.METRIC_SNAPSHOT_OUT_OF_WINDOW
    )


def test_soft_unknown_does_not_make_otherwise_valid_work_ineligible() -> None:
    ledger = ConstraintEngine().evaluate(
        protocol_with_metric(severity=ConstraintSeverity.SOFT), HASH, context(None)
    )
    assert ledger.eligible is True
    assert ledger.hard_unknown_count == 0
    assert ledger.evaluations[-1].decision == ConstraintDecision.UNKNOWN


def test_actual_metric_failure_is_distinct_from_unknown() -> None:
    ledger = ConstraintEngine().evaluate(
        protocol_with_metric(), HASH, context(observation(value=6.9))
    )
    assert ledger.eligible is False
    assert ledger.hard_fail_count == 1
    assert ledger.evaluations[-1].decision == ConstraintDecision.FAIL
    assert ledger.evaluations[-1].reason_code == ConstraintReasonCode.COMPARISON_FAILED
