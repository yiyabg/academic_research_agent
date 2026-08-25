"""Load authorized metrics, evaluate every constraint, and persist the audit ledger."""

from collections import Counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.literature_research import quality as quality_repository
from app.schemas.literature_research.protocol import ResearchProtocol
from app.schemas.literature_research.quality import (
    QualityEvaluationOutcome,
    WorkEvaluationContext,
)
from app.services.literature_research.constraint_engine import ConstraintEngine


class QualityEvaluationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.engine = ConstraintEngine()

    async def evaluate_run(
        self,
        *,
        run_id: UUID,
        protocol: ResearchProtocol,
        protocol_hash: str,
    ) -> QualityEvaluationOutcome:
        rows = await quality_repository.list_preferred_work_rows(self.db, run_id=run_id)
        ledgers = []
        for work, version, venue in rows:
            metrics = {}
            if venue is not None:
                for constraint in protocol.constraints:
                    if not constraint.field.startswith("venue.metric."):
                        continue
                    observation = await quality_repository.find_metric(
                        self.db,
                        venue_name=venue.name,
                        venue_type=venue.venue_type,
                        issn_l=venue.issn_l,
                        metric_name=constraint.field.removeprefix("venue.metric."),
                        as_of_date=protocol.time_scope.date_to,
                    )
                    if observation is not None:
                        metrics[constraint.field] = observation
            context = WorkEvaluationContext(
                work_id=work.id,
                version_id=version.id,
                as_of_date=protocol.time_scope.date_to,
                document_type=work.document_type,
                work_fields={
                    "work.effective_publication_date": (
                        version.effective_publication_date.isoformat()
                        if version.effective_publication_date
                        else None
                    ),
                    "work.document_type": work.document_type,
                    "work.language": work.language,
                    "work.title": work.canonical_title,
                },
                metrics=metrics,
            )
            ledger = self.engine.evaluate(protocol, protocol_hash, context)
            await quality_repository.persist_ledger(self.db, run_id=run_id, ledger=ledger)
            ledgers.append(ledger)

        reasons = Counter(
            item.reason_code
            for ledger in ledgers
            for item in ledger.evaluations
            if item.decision.value != "PASS"
        )
        return QualityEvaluationOutcome(
            candidate_count=len(ledgers),
            eligible_count=sum(ledger.eligible for ledger in ledgers),
            hard_fail_count=sum(ledger.hard_fail_count for ledger in ledgers),
            hard_unknown_count=sum(ledger.hard_unknown_count for ledger in ledgers),
            reason_counts=dict(reasons),
        )
