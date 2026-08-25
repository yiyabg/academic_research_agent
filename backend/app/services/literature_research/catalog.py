"""Owned candidate and paper-detail application service."""

import hashlib
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.db.models.literature_research.quality import ResearchConstraintEvaluation
from app.repositories.literature_research import analysis as analysis_repository
from app.repositories.literature_research import catalog as catalog_repository
from app.repositories.literature_research import evidence as evidence_repository
from app.schemas.literature_research.analysis import AuditedPaperAnalysis
from app.schemas.literature_research.catalog import (
    CandidatePage,
    CandidateRead,
    ConstraintDecisionRead,
    PaperDetailRead,
    ReanalysisAccepted,
    ReanalysisRequest,
    WorkVersionRead,
)
from app.schemas.literature_research.evidence import EvidenceLocator, RelevanceDecision
from app.services.literature_research.figure_extractor import extract_figure_artifacts
from app.services.literature_research.run import ResearchRunService


class ResearchCatalogService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.runs = ResearchRunService(db)

    @staticmethod
    def _candidate(
        row: catalog_repository.CandidateRow,
        constraints: Sequence[ResearchConstraintEvaluation],
    ) -> CandidateRead:
        work, version, venue, eligibility, relevance = row
        score = None
        if relevance is not None:
            score = next(
                value
                for value in (
                    relevance.cross_encoder_score,
                    relevance.semantic_score,
                    relevance.lexical_score,
                )
                if value is not None
            )
        return CandidateRead(
            work_id=work.id,
            version_id=version.id if version else None,
            title=work.canonical_title,
            authors=[str(item.get("name", "")) for item in work.authors_json],
            document_type=work.document_type,
            venue=venue.name if venue else None,
            effective_publication_date=(version.effective_publication_date if version else None),
            doi=version.doi if version else None,
            source_url=version.canonical_url if version else None,
            duplicate_decisions=work.duplicate_decisions_json,
            hard_eligible=eligibility.eligible if eligibility else None,
            hard_fail_count=eligibility.hard_fail_count if eligibility else 0,
            hard_unknown_count=eligibility.hard_unknown_count if eligibility else 0,
            relevance_decision=(RelevanceDecision(relevance.decision) if relevance else None),
            relevance_score=score,
            relevance_reasons=relevance.reasons_json if relevance else [],
            relevance_facet_judgement=(
                relevance.facet_judgement_json if relevance else None
            ),
            constraints=[ConstraintDecisionRead.model_validate(item) for item in constraints],
        )

    async def list_candidates(
        self, *, run_id: UUID, owner_id: UUID, skip: int, limit: int
    ) -> CandidatePage:
        await self.runs.get_owned(run_id, owner_id)
        rows, total = await catalog_repository.list_candidate_rows(
            self.db, run_id=run_id, skip=skip, limit=limit
        )
        work_ids = [row[0].id for row in rows]
        constraints = await catalog_repository.list_constraints_for_works(
            self.db, run_id=run_id, work_ids=work_ids
        )
        return CandidatePage(
            items=[self._candidate(row, constraints[row[0].id]) for row in rows],
            total=total,
            skip=skip,
            limit=limit,
        )

    async def get_paper(
        self, *, run_id: UUID, work_id: UUID, owner_id: UUID
    ) -> PaperDetailRead:
        await self.runs.get_owned(run_id, owner_id)
        work = await catalog_repository.get_work(self.db, run_id=run_id, work_id=work_id)
        if work is None:
            raise NotFoundError(message="Research paper not found")
        row = await catalog_repository.get_candidate_row(
            self.db, run_id=run_id, work_id=work_id
        )
        if row is None:
            raise NotFoundError(message="Research paper not found")
        constraints = await catalog_repository.list_constraints_for_works(
            self.db, run_id=run_id, work_ids=[work_id]
        )
        evidence_rows = await evidence_repository.list_evidence(
            self.db, run_id=run_id, work_id=work_id
        )
        evidence = [EvidenceLocator.model_validate(item) for item in evidence_rows]
        analysis_row = await catalog_repository.get_latest_analysis(
            self.db, run_id=run_id, work_id=work_id
        )
        return PaperDetailRead(
            candidate=self._candidate(row, constraints[work_id]),
            versions=[
                WorkVersionRead.model_validate(item)
                for item in await catalog_repository.list_versions(self.db, work_id=work_id)
            ],
            analysis=(
                AuditedPaperAnalysis.model_validate(analysis_row.analysis_json)
                if analysis_row
                else None
            ),
            evidence=evidence,
            figures=extract_figure_artifacts(work_id=work_id, evidence=evidence),
            analysis_attempt=analysis_row.attempt if analysis_row else None,
        )

    async def request_reanalysis(
        self,
        *,
        run_id: UUID,
        work_id: UUID,
        owner_id: UUID,
        request: ReanalysisRequest,
    ) -> ReanalysisAccepted:
        run = await self.runs.get_owned(run_id, owner_id)
        row = await catalog_repository.get_candidate_row(
            self.db, run_id=run_id, work_id=work_id
        )
        if row is None:
            raise NotFoundError(message="Research paper not found")
        _, version, _, eligibility, relevance = row
        if (
            version is None
            or eligibility is None
            or not eligibility.eligible
            or relevance is None
            or relevance.decision != "PASS"
        ):
            raise ConflictError(
                message="Only a strictly selected paper can be reanalyzed",
                code="PAPER_NOT_SELECTED",
            )
        previous = await analysis_repository.get_latest_analysis(
            self.db, run_id=run.id, work_id=work_id
        )
        if previous is None:
            raise ConflictError(
                message="The paper has no completed analysis to supersede",
                code="ANALYSIS_NOT_AVAILABLE",
            )
        digest = hashlib.sha256(
            f"{run.id}:{work_id}:{request.client_request_id}".encode()
        ).hexdigest()
        task, created = await analysis_repository.get_or_create_reanalysis_task(
            self.db,
            run_id=run.id,
            work_id=work_id,
            input_hash=f"sha256:{digest}",
        )
        return ReanalysisAccepted(
            task_execution_id=task.id,
            run_id=run.id,
            work_id=work_id,
            status=task.status,
            created=created,
        )
