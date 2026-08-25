"""Deterministic three-state constraint evaluation with hard fail-closed policy."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.schemas.literature_research.protocol import (
    ConstraintOperator,
    ConstraintSeverity,
    ProtocolConstraint,
    ResearchProtocol,
)
from app.schemas.literature_research.quality import (
    ConstraintDecision,
    ConstraintEvaluation,
    ConstraintReasonCode,
    MetricObservation,
    WorkConstraintLedger,
    WorkEvaluationContext,
)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidOperation
    return Decimal(str(value))


def _compare(operator: ConstraintOperator, observed: Any, expected: Any) -> bool:
    if operator == ConstraintOperator.EXISTS:
        return observed is not None
    if operator == ConstraintOperator.EQ:
        return observed == expected
    if operator == ConstraintOperator.NEQ:
        return observed != expected
    if operator == ConstraintOperator.IN:
        return observed in expected
    if operator == ConstraintOperator.NOT_IN:
        return observed not in expected
    if operator == ConstraintOperator.CONTAINS:
        return expected in observed
    if isinstance(observed, (date, datetime)) or isinstance(expected, (date, datetime)):
        left, right = date.fromisoformat(str(observed)[:10]), date.fromisoformat(str(expected)[:10])
    elif isinstance(observed, str) and isinstance(expected, str):
        try:
            left, right = date.fromisoformat(observed[:10]), date.fromisoformat(expected[:10])
        except ValueError:
            left, right = _decimal(observed), _decimal(expected)
    else:
        left, right = _decimal(observed), _decimal(expected)
    numeric: dict[ConstraintOperator, Callable[[Any, Any], bool]] = {
        ConstraintOperator.GT: lambda a, b: a > b,
        ConstraintOperator.GTE: lambda a, b: a >= b,
        ConstraintOperator.LT: lambda a, b: a < b,
        ConstraintOperator.LTE: lambda a, b: a <= b,
    }
    return numeric[operator](left, right)


class ConstraintEngine:
    def evaluate(
        self,
        protocol: ResearchProtocol,
        protocol_hash: str,
        context: WorkEvaluationContext,
    ) -> WorkConstraintLedger:
        evaluations = [
            self._evaluate_constraint(constraint, context) for constraint in protocol.constraints
        ]
        hard = [item for item in evaluations if item.severity == ConstraintSeverity.HARD]
        hard_fail = sum(item.decision == ConstraintDecision.FAIL for item in hard)
        hard_unknown = sum(item.decision == ConstraintDecision.UNKNOWN for item in hard)
        hard_pass = sum(item.decision == ConstraintDecision.PASS for item in hard)
        return WorkConstraintLedger(
            work_id=context.work_id,
            version_id=context.version_id,
            protocol_hash=protocol_hash,
            eligible=hard_fail == 0 and hard_unknown == 0,
            hard_pass_count=hard_pass,
            hard_fail_count=hard_fail,
            hard_unknown_count=hard_unknown,
            evaluations=evaluations,
        )

    def _evaluate_constraint(
        self, constraint: ProtocolConstraint, context: WorkEvaluationContext
    ) -> ConstraintEvaluation:
        if constraint.applies_to and context.document_type not in constraint.applies_to:
            return self._result(
                constraint,
                decision=ConstraintDecision.PASS,
                reason_code=ConstraintReasonCode.NOT_APPLICABLE,
            )

        metric = context.metrics.get(constraint.field)
        if constraint.field.startswith("venue.metric."):
            unknown = self._validate_metric(metric, context)
            if unknown is not None:
                (
                    decision,
                    reason,
                    observed,
                    evidence_reference,
                    snapshot_id,
                    fact_id,
                    metric_year,
                ) = unknown
                return self._result(
                    constraint,
                    decision=decision,
                    reason_code=reason,
                    observed_value=observed,
                    evidence_reference=evidence_reference,
                    metric_snapshot_id=snapshot_id,
                    metric_fact_id=fact_id,
                    metric_year=metric_year,
                )
            assert metric is not None
            observed = metric.value
            evidence_reference = metric.evidence_reference
            snapshot_id = metric.snapshot_id
            fact_id = metric.fact_id
            metric_year = metric.metric_year
        else:
            observed = context.work_fields.get(constraint.field)
            evidence_reference = None
            snapshot_id = None
            fact_id = None
            metric_year = None
        if observed is None:
            return self._result(
                constraint,
                observed_value=None,
                decision=ConstraintDecision.UNKNOWN,
                reason_code=ConstraintReasonCode.VALUE_MISSING,
                evidence_reference=evidence_reference,
                metric_snapshot_id=snapshot_id,
                metric_fact_id=fact_id,
                metric_year=metric_year,
            )
        try:
            passed = _compare(constraint.operator, observed, constraint.value)
        except (InvalidOperation, KeyError, TypeError, ValueError):
            return self._result(
                constraint,
                observed_value=observed,
                decision=ConstraintDecision.UNKNOWN,
                reason_code=ConstraintReasonCode.TYPE_MISMATCH,
                evidence_reference=evidence_reference,
                metric_snapshot_id=snapshot_id,
                metric_fact_id=fact_id,
                metric_year=metric_year,
            )
        return self._result(
            constraint,
            observed_value=observed,
            decision=(ConstraintDecision.PASS if passed else ConstraintDecision.FAIL),
            reason_code=(
                ConstraintReasonCode.COMPARISON_PASSED
                if passed
                else ConstraintReasonCode.COMPARISON_FAILED
            ),
            evidence_reference=evidence_reference,
            metric_snapshot_id=snapshot_id,
            metric_fact_id=fact_id,
            metric_year=metric_year,
        )

    @staticmethod
    def _result(
        constraint: ProtocolConstraint,
        *,
        decision: ConstraintDecision,
        reason_code: ConstraintReasonCode,
        observed_value: Any = None,
        evidence_reference: str | None = None,
        metric_snapshot_id: UUID | None = None,
        metric_fact_id: UUID | None = None,
        metric_year: int | None = None,
    ) -> ConstraintEvaluation:
        return ConstraintEvaluation(
            constraint_id=constraint.constraint_id,
            field=constraint.field,
            operator=constraint.operator,
            expected_value=constraint.value,
            observed_value=observed_value,
            severity=constraint.severity,
            decision=decision,
            reason_code=reason_code,
            evidence_reference=evidence_reference,
            metric_snapshot_id=metric_snapshot_id,
            metric_fact_id=metric_fact_id,
            metric_year=metric_year,
            evaluated_at=datetime.now(UTC),
        )

    @staticmethod
    def _validate_metric(
        metric: MetricObservation | None, context: WorkEvaluationContext
    ) -> (
        tuple[
            ConstraintDecision,
            ConstraintReasonCode,
            Any,
            str | None,
            UUID | None,
            UUID | None,
            int | None,
        ]
        | None
    ):
        if metric is None:
            return (
                ConstraintDecision.UNKNOWN,
                ConstraintReasonCode.VALUE_MISSING,
                None,
                None,
                None,
                None,
                None,
            )
        if not metric.authorized:
            return (
                ConstraintDecision.UNKNOWN,
                ConstraintReasonCode.METRIC_NOT_AUTHORIZED,
                metric.value,
                metric.evidence_reference,
                metric.snapshot_id,
                metric.fact_id,
                metric.metric_year,
            )
        if not metric.is_effective_on(context.as_of_date):
            return (
                ConstraintDecision.UNKNOWN,
                ConstraintReasonCode.METRIC_SNAPSHOT_OUT_OF_WINDOW,
                metric.value,
                metric.evidence_reference,
                metric.snapshot_id,
                metric.fact_id,
                metric.metric_year,
            )
        return None
