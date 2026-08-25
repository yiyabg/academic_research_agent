"""Parallel bounded-expert orchestration with deterministic evidence boundaries."""

import asyncio
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.schemas.literature_research.analysis import (
    AnalysisSection,
    AuditDecision,
    AuditedPaperAnalysis,
    AuditReport,
    EvidenceGroundedClaim,
    FigureArtifact,
    FigureInterpretation,
    NumericSource,
    PaperAnalysisTask,
    SynthesisOutput,
)

OutputT = TypeVar("OutputT", bound=BaseModel, covariant=True)


class ExpertRunner(Protocol[OutputT]):
    async def run(self, payload: dict[str, Any]) -> OutputT: ...


class EvidenceBoundaryError(ValueError):
    pass


def _allowed_evidence(task: PaperAnalysisTask) -> set[str]:
    return {
        str(item["evidence_id"]) for item in task.evidence if item.get("evidence_id") is not None
    }


def _claim_evidence(claims: list[EvidenceGroundedClaim]) -> set[str]:
    return {evidence_id for claim in claims for evidence_id in claim.evidence_ids}


class AnalysisOrchestrator:
    def __init__(
        self,
        *,
        analysis_expert: ExpertRunner[AnalysisSection],
        figure_expert: ExpertRunner[FigureInterpretation],
        audit_expert: ExpertRunner[AuditReport],
        synthesis_expert: ExpertRunner[SynthesisOutput],
        concurrency: int = 6,
    ) -> None:
        self.analysis_expert = analysis_expert
        self.figure_expert = figure_expert
        self.audit_expert = audit_expert
        self.synthesis_expert = synthesis_expert
        self.semaphore = asyncio.Semaphore(concurrency)

    async def analyze_papers(self, tasks: list[PaperAnalysisTask]) -> list[AuditedPaperAnalysis]:
        return list(await asyncio.gather(*(self._analyze_one(task) for task in tasks)))

    async def _run(self, expert: ExpertRunner[OutputT], payload: dict[str, Any]) -> OutputT:
        async with self.semaphore:
            return await expert.run(payload)

    async def _analyze_one(self, task: PaperAnalysisTask) -> AuditedPaperAnalysis:
        base = {
            "work_id": str(task.work_id),
            "metadata": task.metadata,
            "evidence": task.evidence,
        }
        sections, figures = await asyncio.gather(
            asyncio.gather(
                *(
                    self._analyze_section_with_repair(task, base=base, section_id=section_id)
                    for section_id in task.section_ids
                )
            ),
            asyncio.gather(
                *(
                    self._analyze_figure_with_repair(task, base=base, artifact=figure)
                    for figure in task.figures
                )
            ),
        )
        self._validate_outputs(task, list(sections), list(figures))
        claims = [claim for section in sections for claim in section.claims]
        claims.extend(claim for figure in figures for claim in figure.observations)
        audit = await self._audit_with_repair(task, base=base, claims=claims)
        return AuditedPaperAnalysis(
            work_id=task.work_id,
            sections=list(sections),
            figures=list(figures),
            audit=audit,
        )

    async def _analyze_section_with_repair(
        self,
        task: PaperAnalysisTask,
        *,
        base: dict[str, Any],
        section_id: str,
    ) -> AnalysisSection:
        payload = {**base, "section_id": section_id}
        output = await self._run(self.analysis_expert, payload)
        try:
            self._validate_section_output(task, section_id, output)
            return output
        except EvidenceBoundaryError as exc:
            repaired = await self._run(
                self.analysis_expert,
                {
                    **payload,
                    "previous_output": output.model_dump(mode="json"),
                    "validation_error": str(exc),
                    "repair_instruction": (
                        "Repair only the rejected fields once. Every evidence_id must be "
                        "copied exactly from the input evidence list. Remove any claim that "
                        "cannot be grounded without inventing an ID."
                    ),
                },
            )
            self._validate_section_output(task, section_id, repaired)
            return repaired

    async def _analyze_figure_with_repair(
        self,
        task: PaperAnalysisTask,
        *,
        base: dict[str, Any],
        artifact: FigureArtifact,
    ) -> FigureInterpretation:
        """Retry only an invalid figure field once with deterministic boundary feedback."""
        payload = {**base, "figure": artifact.model_dump(mode="json")}
        output = await self._run(self.figure_expert, payload)
        try:
            self._validate_figure_output(task, artifact, output)
            return output
        except EvidenceBoundaryError as exc:
            repaired = await self._run(
                self.figure_expert,
                {
                    **payload,
                    "previous_output": output.model_dump(mode="json"),
                    "validation_error": str(exc),
                    "repair_instruction": (
                        "Repair only the rejected fields once. Do not add evidence or "
                        "numbers. If a precise value is absent from "
                        "figure.exact_numeric_values, use NOT_EXTRACTED and return an "
                        "empty extracted_values list."
                    ),
                },
            )
            self._validate_figure_output(task, artifact, repaired)
            return repaired

    async def _audit_with_repair(
        self,
        task: PaperAnalysisTask,
        *,
        base: dict[str, Any],
        claims: list[EvidenceGroundedClaim],
    ) -> AuditReport:
        payload = {
            **base,
            "claims": [claim.model_dump(mode="json") for claim in claims],
        }
        output = await self._run(self.audit_expert, payload)
        try:
            self._validate_audit(task, claims, output)
            return output
        except EvidenceBoundaryError as exc:
            repaired = await self._run(
                self.audit_expert,
                {
                    **payload,
                    "previous_output": output.model_dump(mode="json"),
                    "validation_error": str(exc),
                    "repair_instruction": (
                        "Repair only the rejected audit fields once. Keep the exact claim "
                        "set. Each audit item may cite only evidence_ids already attached "
                        "to that same claim, then recompute all summary counts and coverage."
                    ),
                },
            )
            self._validate_audit(task, claims, repaired)
            return repaired

    async def synthesize(self, analyses: list[AuditedPaperAnalysis]) -> SynthesisOutput:
        releasable = [
            item
            for item in analyses
            if item.audit.contradicted_count == 0
            and item.audit.unsupported_count == 0
            and not item.audit.requires_human_review
        ]
        output = await self._run(
            self.synthesis_expert,
            {
                "papers": [item.model_dump(mode="json") for item in releasable],
            },
        )
        allowed_work_ids = {item.work_id for item in releasable}
        if not set(output.included_work_ids) <= allowed_work_ids:
            raise EvidenceBoundaryError("Synthesis introduced a work outside audited inputs")
        return output

    @staticmethod
    def _validate_outputs(
        task: PaperAnalysisTask,
        sections: list[AnalysisSection],
        figures: list[FigureInterpretation],
    ) -> None:
        allowed = _allowed_evidence(task)
        for output in [*sections, *figures]:
            if output.work_id != task.work_id:
                raise EvidenceBoundaryError("Expert changed the assigned work_id")
        if [item.section_id for item in sections] != task.section_ids:
            raise EvidenceBoundaryError("Analysis must cover each requested section exactly once")
        if [item.figure_id for item in figures] != [item.figure_id for item in task.figures]:
            raise EvidenceBoundaryError("Figure analysis must cover the assigned figures exactly")
        for artifact, interpretation in zip(task.figures, figures, strict=True):
            AnalysisOrchestrator._validate_figure_output(task, artifact, interpretation)
        claims = [claim for section in sections for claim in section.claims]
        claims.extend(claim for figure in figures for claim in figure.observations)
        unknown = _claim_evidence(claims) - allowed
        if unknown:
            raise EvidenceBoundaryError(f"Expert cited unknown evidence IDs: {sorted(unknown)}")

    @staticmethod
    def _validate_section_output(
        task: PaperAnalysisTask,
        section_id: str,
        output: AnalysisSection,
    ) -> None:
        if output.work_id != task.work_id:
            raise EvidenceBoundaryError("Expert changed the assigned work_id")
        if output.section_id != section_id:
            raise EvidenceBoundaryError("Analysis expert changed the assigned section_id")
        unknown = _claim_evidence(output.claims) - _allowed_evidence(task)
        if unknown:
            raise EvidenceBoundaryError(f"Expert cited unknown evidence IDs: {sorted(unknown)}")

    @staticmethod
    def _validate_figure_output(
        task: PaperAnalysisTask,
        artifact: FigureArtifact,
        interpretation: FigureInterpretation,
    ) -> None:
        if interpretation.work_id != task.work_id:
            raise EvidenceBoundaryError("Expert changed the assigned work_id")
        if interpretation.figure_id != artifact.figure_id:
            raise EvidenceBoundaryError("Figure expert changed the assigned figure_id")
        if artifact.extraction_status != "VERIFIED":
            raise EvidenceBoundaryError("Figure analysis requires a verified source crop")
        if interpretation.numeric_source == NumericSource.TABLE_EXACT and (
            artifact.artifact_kind != "table" or not artifact.table_cells
        ):
            raise EvidenceBoundaryError("table_exact requires extracted table cells")
        if interpretation.numeric_source in {
            NumericSource.TABLE_EXACT,
            NumericSource.TEXT_EXACT,
        } and not set(interpretation.extracted_values) <= set(artifact.exact_numeric_values):
            raise EvidenceBoundaryError(
                "Exact figure values must occur in the extracted source artifact"
            )
        unknown = _claim_evidence(interpretation.observations) - _allowed_evidence(task)
        if unknown:
            raise EvidenceBoundaryError(f"Expert cited unknown evidence IDs: {sorted(unknown)}")

    @staticmethod
    def _validate_audit(
        task: PaperAnalysisTask,
        claims: list[EvidenceGroundedClaim],
        audit: AuditReport,
    ) -> None:
        if audit.work_id != task.work_id:
            raise EvidenceBoundaryError("Audit changed the assigned work_id")
        claim_ids = {claim.claim_id for claim in claims}
        audited_ids = {item.claim_id for item in audit.claims}
        if audited_ids != claim_ids:
            raise EvidenceBoundaryError("Audit must cover exactly the submitted claim IDs")
        unknown = {
            evidence_id for item in audit.claims for evidence_id in item.evidence_ids
        } - _allowed_evidence(task)
        if unknown:
            raise EvidenceBoundaryError(f"Audit cited unknown evidence IDs: {sorted(unknown)}")
        claim_by_id = {claim.claim_id: claim for claim in claims}
        supported = 0
        for item in audit.claims:
            source_ids = set(claim_by_id[item.claim_id].evidence_ids)
            audit_ids = set(item.evidence_ids)
            if not audit_ids <= source_ids:
                raise EvidenceBoundaryError("Audit cited evidence not attached to its claim")
            if item.decision in {
                AuditDecision.SUPPORTED,
                AuditDecision.PARTIALLY_SUPPORTED,
            }:
                if not audit_ids:
                    raise EvidenceBoundaryError("Supported audit decisions require claim evidence")
                supported += 1
        deterministic_coverage = supported / len(claims) if claims else 0.0
        if abs(audit.evidence_coverage - deterministic_coverage) > 1e-6:
            raise EvidenceBoundaryError("Audit evidence_coverage does not match audited claims")
