"""Bounded expert schema and evidence-boundary orchestration tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.literature_research.analysis import (
    AnalysisSection,
    AuditDecision,
    AuditReport,
    ClaimAudit,
    ClaimKind,
    EvidenceGroundedClaim,
    FigureInterpretation,
    NumericSource,
    PaperAnalysisTask,
    ProtocolDraftAdvice,
    SynthesisOutput,
    UnknownAwareStatus,
)
from app.schemas.literature_research.protocol import TopicFacet
from app.services.literature_research.analysis_orchestrator import (
    AnalysisOrchestrator,
    EvidenceBoundaryError,
)


class AnalysisFake:
    async def run(self, payload):
        work_id = payload["work_id"]
        section = payload["section_id"]
        return AnalysisSection(
            work_id=work_id,
            section_id=section,
            status=UnknownAwareStatus.REPORTED,
            summary="The method preserves an audit trail.",
            claims=[
                EvidenceGroundedClaim(
                    claim_id=f"C_{section.upper()}_01",
                    text="The method preserves an audit trail.",
                    kind=ClaimKind.AUTHOR_STATED,
                    evidence_ids=["E_1"],
                    confidence=0.9,
                )
            ],
            evidence_coverage=1.0,
        )


class FigureFake:
    async def run(self, payload):
        return FigureInterpretation(
            work_id=payload["work_id"],
            figure_id=payload["figure"]["figure_id"],
            figure_kind="architecture",
            caption_summary="System architecture.",
            numeric_source=NumericSource.NOT_EXTRACTED,
        )


class AuditFake:
    async def run(self, payload):
        return AuditReport(
            work_id=payload["work_id"],
            claims=[
                ClaimAudit(
                    claim_id=item["claim_id"],
                    decision=AuditDecision.SUPPORTED,
                    evidence_ids=item["evidence_ids"],
                    explanation="Exact evidence supports the claim.",
                )
                for item in payload["claims"]
            ],
            evidence_coverage=1.0,
            contradicted_count=0,
            unsupported_count=0,
        )


class SynthesisFake:
    def __init__(self, work_ids):
        self.work_ids = work_ids

    async def run(self, payload):
        del payload
        return SynthesisOutput(
            overview="Audited overview.",
            themes=[],
            research_gaps=[],
            included_work_ids=self.work_ids,
        )


def task():
    return PaperAnalysisTask(
        work_id=uuid4(),
        metadata={"title": "Auditable Agents"},
        evidence=[{"evidence_id": "E_1", "quote": "audit trail"}],
        section_ids=["method", "experiment"],
        figures=[
            {
                "figure_id": "F_A1B2C3D4E5F60708",
                "label": "Figure 1",
                "caption": "System architecture.",
                "page_number": 3,
                "evidence_ids": ["E_1"],
                "document_sha256": "a" * 64,
                "image_object_key": "figures/figure-1.png",
                "image_sha256": "b" * 64,
                "bbox": [10, 20, 300, 400],
                "extraction_status": "VERIFIED",
            }
        ],
    )


def orchestrator(work_ids):
    return AnalysisOrchestrator(
        analysis_expert=AnalysisFake(),
        figure_expert=FigureFake(),
        audit_expert=AuditFake(),
        synthesis_expert=SynthesisFake(work_ids),
    )


def test_protocol_expert_schema_cannot_approve() -> None:
    with pytest.raises(ValidationError, match="may draft"):
        ProtocolDraftAdvice(
            topic_definition="Auditable agent systems",
            research_questions=["How are claims audited?"],
            must_have_facets=[
                TopicFacet(
                    facet_id="audit",
                    name="audit",
                    description="Auditability is central.",
                )
            ],
            approval_requested=True,
        )


def test_unknown_analysis_section_cannot_invent_claims() -> None:
    with pytest.raises(ValidationError, match="cannot contain"):
        AnalysisSection(
            work_id=uuid4(),
            section_id="experiment",
            status=UnknownAwareStatus.NOT_REPORTED,
            summary="",
            claims=[
                EvidenceGroundedClaim(
                    claim_id="C_FAKE_01",
                    text="Invented experiment.",
                    kind=ClaimKind.AUTHOR_STATED,
                    evidence_ids=["E_1"],
                    confidence=0.2,
                )
            ],
            evidence_coverage=0,
        )


@pytest.mark.anyio
async def test_parallel_analysis_audits_exact_claim_set_and_synthesizes() -> None:
    assigned = task()
    service = orchestrator([assigned.work_id])
    analyses = await service.analyze_papers([assigned])
    assert len(analyses[0].sections) == 2
    assert len(analyses[0].audit.claims) == 2
    synthesis = await service.synthesize(analyses)
    assert synthesis.included_work_ids == [assigned.work_id]


@pytest.mark.anyio
async def test_unknown_evidence_id_is_rejected_before_audit() -> None:
    assigned = task()

    class BadAnalysis(AnalysisFake):
        async def run(self, payload):
            output = await super().run(payload)
            output.claims[0].evidence_ids = ["E_FABRICATED"]
            return output

    service = AnalysisOrchestrator(
        analysis_expert=BadAnalysis(),
        figure_expert=FigureFake(),
        audit_expert=AuditFake(),
        synthesis_expert=SynthesisFake([assigned.work_id]),
    )
    with pytest.raises(EvidenceBoundaryError, match="unknown evidence"):
        await service.analyze_papers([assigned])


@pytest.mark.anyio
async def test_unknown_section_evidence_gets_one_targeted_repair() -> None:
    assigned = task().model_copy(update={"section_ids": ["method"], "figures": []})

    class RepairingAnalysis(AnalysisFake):
        def __init__(self):
            self.calls = 0

        async def run(self, payload):
            self.calls += 1
            output = await super().run(payload)
            if "validation_error" not in payload:
                output.claims[0].evidence_ids = ["E_FABRICATED"]
            return output

    analysis = RepairingAnalysis()
    service = AnalysisOrchestrator(
        analysis_expert=analysis,
        figure_expert=FigureFake(),
        audit_expert=AuditFake(),
        synthesis_expert=SynthesisFake([assigned.work_id]),
    )

    result = await service.analyze_papers([assigned])

    assert analysis.calls == 2
    assert result[0].sections[0].claims[0].evidence_ids == ["E_1"]


@pytest.mark.anyio
async def test_exact_figure_number_must_exist_in_source_artifact() -> None:
    assigned = task()
    assigned.figures[0].exact_numeric_values = ["95.2%"]

    class InventedNumberFigure(FigureFake):
        async def run(self, payload):
            output = await super().run(payload)
            output.numeric_source = NumericSource.TEXT_EXACT
            output.extracted_values = ["99.9%"]
            return output

    service = AnalysisOrchestrator(
        analysis_expert=AnalysisFake(),
        figure_expert=InventedNumberFigure(),
        audit_expert=AuditFake(),
        synthesis_expert=SynthesisFake([assigned.work_id]),
    )
    with pytest.raises(EvidenceBoundaryError, match="Exact figure values"):
        await service.analyze_papers([assigned])


@pytest.mark.anyio
async def test_invalid_figure_number_gets_one_targeted_repair() -> None:
    assigned = task()
    assigned.figures[0].exact_numeric_values = ["95.2%"]

    class RepairingFigure(FigureFake):
        def __init__(self):
            self.calls = 0

        async def run(self, payload):
            self.calls += 1
            output = await super().run(payload)
            if "validation_error" not in payload:
                output.numeric_source = NumericSource.TEXT_EXACT
                output.extracted_values = ["99.9%"]
            return output

    figure = RepairingFigure()
    service = AnalysisOrchestrator(
        analysis_expert=AnalysisFake(),
        figure_expert=figure,
        audit_expert=AuditFake(),
        synthesis_expert=SynthesisFake([assigned.work_id]),
    )

    analyses = await service.analyze_papers([assigned])

    assert figure.calls == 2
    assert analyses[0].figures[0].numeric_source == NumericSource.NOT_EXTRACTED
    assert analyses[0].figures[0].extracted_values == []


@pytest.mark.anyio
async def test_synthesis_cannot_introduce_unreviewed_work() -> None:
    assigned = task()
    service = orchestrator([assigned.work_id, uuid4()])
    analyses = await service.analyze_papers([assigned])
    with pytest.raises(EvidenceBoundaryError, match="outside audited inputs"):
        await service.synthesize(analyses)


@pytest.mark.anyio
async def test_audit_cannot_claim_full_coverage_for_no_claims() -> None:
    assigned = task().model_copy(update={"section_ids": ["method"], "figures": []})

    class EmptyAnalysis:
        async def run(self, payload):
            return AnalysisSection(
                work_id=payload["work_id"],
                section_id=payload["section_id"],
                status=UnknownAwareStatus.NOT_REPORTED,
                summary="Not reported in the available evidence.",
                evidence_coverage=0,
            )

    service = AnalysisOrchestrator(
        analysis_expert=EmptyAnalysis(),
        figure_expert=FigureFake(),
        audit_expert=AuditFake(),
        synthesis_expert=SynthesisFake([]),
    )
    with pytest.raises(EvidenceBoundaryError, match="evidence_coverage"):
        await service.analyze_papers([assigned])


@pytest.mark.anyio
async def test_audit_cannot_move_evidence_between_claims() -> None:
    assigned = task().model_copy(
        update={
            "evidence": [
                {"evidence_id": "E_1", "quote": "audit trail"},
                {"evidence_id": "E_2", "quote": "other evidence"},
            ]
        }
    )

    class MisboundAudit(AuditFake):
        async def run(self, payload):
            output = await super().run(payload)
            output.claims[0].evidence_ids = ["E_2"]
            return output

    service = AnalysisOrchestrator(
        analysis_expert=AnalysisFake(),
        figure_expert=FigureFake(),
        audit_expert=MisboundAudit(),
        synthesis_expert=SynthesisFake([assigned.work_id]),
    )
    with pytest.raises(EvidenceBoundaryError, match="not attached"):
        await service.analyze_papers([assigned])


@pytest.mark.anyio
async def test_misbound_audit_evidence_gets_one_targeted_repair() -> None:
    assigned = task().model_copy(
        update={
            "section_ids": ["method"],
            "figures": [],
            "evidence": [
                {"evidence_id": "E_1", "quote": "audit trail"},
                {"evidence_id": "E_2", "quote": "other evidence"},
            ],
        }
    )

    class RepairingAudit(AuditFake):
        def __init__(self):
            self.calls = 0

        async def run(self, payload):
            self.calls += 1
            output = await super().run(payload)
            if "validation_error" not in payload:
                output.claims[0].evidence_ids = ["E_2"]
            return output

    audit = RepairingAudit()
    service = AnalysisOrchestrator(
        analysis_expert=AnalysisFake(),
        figure_expert=FigureFake(),
        audit_expert=audit,
        synthesis_expert=SynthesisFake([assigned.work_id]),
    )

    result = await service.analyze_papers([assigned])

    assert audit.calls == 2
    assert result[0].audit.claims[0].evidence_ids == ["E_1"]
