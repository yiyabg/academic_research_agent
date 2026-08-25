"""Run-level quality evaluation orchestration tests."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.literature_research.protocol import (
    ConstraintOperator,
    DocumentType,
    ProtocolCompileRequest,
    ProtocolConstraint,
)
from app.schemas.literature_research.quality import MetricObservation
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.quality_evaluation import QualityEvaluationService


@pytest.mark.anyio
async def test_run_evaluation_persists_ledger_and_counts_only_eligible_work() -> None:
    protocol = (
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
                        verification_source="licensed-jcr",
                    )
                ],
            )
        )
        .protocol
    )
    work = MagicMock(
        id=uuid4(),
        document_type="journal_article",
        language="en",
        canonical_title="Auditable Research Agents",
    )
    version = MagicMock(id=uuid4(), effective_publication_date=date(2026, 7, 1))
    venue = SimpleNamespace(
        name="Journal of Agent Systems",
        venue_type="journal",
        issn_l="1234-5678",
    )
    metric = MetricObservation(
        fact_id=uuid4(),
        metric_name="jif",
        value=8.2,
        metric_year=2025,
        venue_name=venue.name,
        snapshot_id=uuid4(),
        source_name="licensed",
        source_version="2025",
        effective_from=date(2026, 1, 1),
        authorized=True,
        evidence_reference="snapshot:fixture:row:2",
    )
    with (
        patch(
            "app.services.literature_research.quality_evaluation.quality_repository.list_preferred_work_rows",
            new=AsyncMock(return_value=[(work, version, venue)]),
        ),
        patch(
            "app.services.literature_research.quality_evaluation.quality_repository.find_metric",
            new=AsyncMock(return_value=metric),
        ),
        patch(
            "app.services.literature_research.quality_evaluation.quality_repository.persist_ledger",
            new=AsyncMock(),
        ) as persist,
    ):
        outcome = await QualityEvaluationService(AsyncMock()).evaluate_run(
            run_id=uuid4(), protocol=protocol, protocol_hash="sha256:" + "a" * 64
        )

    assert outcome.candidate_count == 1
    assert outcome.eligible_count == 1
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["ledger"].eligible is True
