"""Evidence-bounded stage-C LLM facet judgement for full-research runs."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.schemas.literature_research.analysis import (
    Centrality,
    FacetJudgement,
    FacetJudgementBatch,
    FacetStatus,
)
from app.schemas.literature_research.evidence import RelevanceDecision, RelevanceScore
from app.schemas.literature_research.protocol import ResearchProtocol

RELEVANCE_PROMPT_VERSION = "2026-08-22.1"
RELEVANCE_BATCH_SIZE = 10


class RelevanceExpert(Protocol):
    async def run(self, payload: dict[str, Any]) -> FacetJudgementBatch: ...


class RelevanceJudgementError(ValueError):
    """Raised when a facet judge leaves its assigned paper/evidence boundary."""


@dataclass(frozen=True)
class RelevanceJudgementTask:
    work_id: UUID
    title: str
    abstract: str

    @property
    def evidence(self) -> list[dict[str, str]]:
        rows = [
            {
                "evidence_id": f"META_TITLE_{self.work_id.hex}",
                "field": "title",
                "text": self.title,
            }
        ]
        if self.abstract.strip():
            rows.append(
                {
                    "evidence_id": f"META_ABSTRACT_{self.work_id.hex}",
                    "field": "abstract",
                    "text": self.abstract,
                }
            )
        return rows


class RelevanceFacetJudge:
    def __init__(self, expert: RelevanceExpert) -> None:
        self.expert = expert

    async def judge(
        self,
        *,
        protocol: ResearchProtocol,
        tasks: list[RelevanceJudgementTask],
    ) -> list[FacetJudgement]:
        judgements: list[FacetJudgement] = []
        for offset in range(0, len(tasks), RELEVANCE_BATCH_SIZE):
            batch = tasks[offset : offset + RELEVANCE_BATCH_SIZE]
            payload = self._payload(protocol, batch)
            output = await self.expert.run(payload)
            try:
                self._validate(protocol, batch, output)
            except RelevanceJudgementError as exc:
                output = await self.expert.run(
                    {
                        **payload,
                        "previous_output": output.model_dump(mode="json"),
                        "validation_error": str(exc),
                        "repair_instruction": (
                            "Repair this batch once. Return exactly one judgement per assigned "
                            "work_id and exactly the assigned facets. Copy evidence_id values only "
                            "from that same paper; never invent or move evidence between papers."
                        ),
                    }
                )
                self._validate(protocol, batch, output)
            judgements.extend(output.judgements)
        return judgements

    @staticmethod
    def _payload(
        protocol: ResearchProtocol, tasks: list[RelevanceJudgementTask]
    ) -> dict[str, Any]:
        return {
            "topic": protocol.topic,
            "topic_definition": protocol.topic_definition,
            "research_questions": protocol.research_questions,
            "must_have_facets": [
                item.model_dump(mode="json") for item in protocol.topic_model.must_have_facets
            ],
            "should_have_facets": [
                item.model_dump(mode="json") for item in protocol.topic_model.should_have_facets
            ],
            "exclude_facets": [
                item.model_dump(mode="json") for item in protocol.topic_model.exclude_facets
            ],
            "papers": [
                {
                    "work_id": str(task.work_id),
                    "evidence": task.evidence,
                }
                for task in tasks
            ],
        }

    @staticmethod
    def _validate(
        protocol: ResearchProtocol,
        tasks: list[RelevanceJudgementTask],
        output: FacetJudgementBatch,
    ) -> None:
        expected_work_ids = {task.work_id for task in tasks}
        actual_work_ids = [item.work_id for item in output.judgements]
        if len(actual_work_ids) != len(set(actual_work_ids)):
            raise RelevanceJudgementError("Facet judge returned a duplicate work_id")
        if set(actual_work_ids) != expected_work_ids:
            raise RelevanceJudgementError("Facet judge changed or omitted assigned work_ids")
        expected_facets = {
            item.facet_id
            for item in (
                protocol.topic_model.must_have_facets
                + protocol.topic_model.should_have_facets
            )
        }
        tasks_by_id = {task.work_id: task for task in tasks}
        for judgement in output.judgements:
            facet_ids = [item.facet_id for item in judgement.facets]
            if len(facet_ids) != len(set(facet_ids)) or set(facet_ids) != expected_facets:
                raise RelevanceJudgementError(
                    f"Facet judge changed the assigned facet set for {judgement.work_id}"
                )
            allowed_evidence = {
                item["evidence_id"] for item in tasks_by_id[judgement.work_id].evidence
            }
            cited = set(judgement.evidence_ids)
            cited.update(
                evidence_id
                for facet in judgement.facets
                for evidence_id in facet.evidence_ids
            )
            if unknown := cited - allowed_evidence:
                raise RelevanceJudgementError(
                    f"Facet judge cited unknown evidence IDs for {judgement.work_id}: "
                    f"{sorted(unknown)}"
                )
            if any(
                facet.status == FacetStatus.SUPPORTED and not facet.evidence_ids
                for facet in judgement.facets
            ):
                raise RelevanceJudgementError(
                    f"SUPPORTED facet lacks evidence for {judgement.work_id}"
                )


def apply_facet_judgement(
    *,
    score: RelevanceScore,
    judgement: FacetJudgement,
    protocol: ResearchProtocol,
    model_identifier: str,
) -> RelevanceScore:
    """Combine local stages A/B with strict stage-C semantics without score substitution."""
    must_ids = {item.facet_id for item in protocol.topic_model.must_have_facets}
    must = [item for item in judgement.facets if item.facet_id in must_ids]
    reasons = [*score.reasons]
    if judgement.exclusion_triggered:
        decision = RelevanceDecision.FAIL
        reasons.append("FACET_LLM_EXCLUSION_TRIGGERED")
    elif unsupported := [
        item.facet_id for item in must if item.status == FacetStatus.NOT_SUPPORTED
    ]:
        decision = RelevanceDecision.FAIL
        reasons.extend(f"FACET_LLM_NOT_SUPPORTED:{item}" for item in unsupported)
    elif uncertain := [
        item.facet_id for item in must if item.status == FacetStatus.UNCERTAIN
    ]:
        decision = RelevanceDecision.REVIEW
        reasons.extend(f"FACET_LLM_UNCERTAIN:{item}" for item in uncertain)
    elif judgement.centrality != Centrality.CENTRAL:
        decision = RelevanceDecision.FAIL
        reasons.append(f"FACET_LLM_CENTRALITY:{judgement.centrality.value}")
    else:
        weight = sum(item.weight for item in protocol.topic_model.must_have_facets)
        threshold = sum(
            item.minimum_score * item.weight
            for item in protocol.topic_model.must_have_facets
        ) / weight
        if judgement.score < threshold:
            decision = RelevanceDecision.FAIL
            reasons.append("FACET_LLM_SCORE_BELOW_PROTOCOL_FLOOR")
        else:
            decision = RelevanceDecision.PASS
            reasons.append("FACET_LLM_PASS")
    return score.model_copy(
        update={
            "decision": decision,
            "model_versions": {
                **score.model_versions,
                "facet_llm": model_identifier,
                "facet_prompt": RELEVANCE_PROMPT_VERSION,
            },
            "reasons": reasons,
            "facet_judgement": judgement.model_dump(mode="json"),
        }
    )
