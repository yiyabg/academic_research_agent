"""Stage-C evidence-bounded relevance facet judgement tests."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.schemas.literature_research.analysis import (
    Centrality,
    FacetJudgement,
    FacetJudgementBatch,
    FacetJudgementItem,
    FacetStatus,
)
from app.schemas.literature_research.evidence import RelevanceDecision, RelevanceScore
from app.schemas.literature_research.protocol import (
    DocumentType,
    ProtocolCompileRequest,
    TopicFacet,
)
from app.services.literature_research.pipeline_stages import ResearchPipelineStages
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.relevance_judge import (
    RELEVANCE_BATCH_SIZE,
    RelevanceFacetJudge,
    RelevanceJudgementTask,
    apply_facet_judgement,
)


def protocol():  # type: ignore[no-untyped-def]
    return (
        ProtocolCompilerService()
        .compile(
            ProtocolCompileRequest(
                topic="evidence grounded research agents",
                as_of_date=date(2026, 8, 22),
                allowed_types=[DocumentType.JOURNAL_ARTICLE],
                must_have_facets=[
                    TopicFacet(
                        facet_id="grounding",
                        name="evidence grounding",
                        description="Claims are grounded in traceable evidence.",
                        minimum_score=0.6,
                    )
                ],
            )
        )
        .protocol
    )


def judgement(
    work_id: UUID,
    evidence_id: str,
    *,
    status: FacetStatus = FacetStatus.SUPPORTED,
    centrality: Centrality = Centrality.CENTRAL,
) -> FacetJudgement:
    return FacetJudgement(
        work_id=work_id,
        facets=[
            FacetJudgementItem(
                facet_id="grounding",
                status=status,
                evidence_ids=[evidence_id] if status == FacetStatus.SUPPORTED else [],
                rationale="The assigned metadata supports this judgement.",
            )
        ],
        centrality=centrality,
        score=0.9,
        exclusion_triggered=False,
        evidence_ids=[evidence_id],
    )


class EchoExpert:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, payload: dict[str, object]) -> FacetJudgementBatch:
        self.calls.append(payload)
        papers = payload["papers"]
        assert isinstance(papers, list)
        return FacetJudgementBatch(
            judgements=[
                judgement(
                    UUID(str(paper["work_id"])),
                    str(paper["evidence"][0]["evidence_id"]),
                )
                for paper in papers
            ]
        )


@pytest.mark.anyio
async def test_judge_batches_candidates_and_keeps_metadata_evidence_per_paper() -> None:
    expert = EchoExpert()
    tasks = [
        RelevanceJudgementTask(
            work_id=uuid4(),
            title=f"Evidence grounded agent {index}",
            abstract="The agent binds every claim to provenance.",
        )
        for index in range(RELEVANCE_BATCH_SIZE + 1)
    ]

    result = await RelevanceFacetJudge(expert).judge(protocol=protocol(), tasks=tasks)

    assert len(result) == len(tasks)
    assert [len(call["papers"]) for call in expert.calls] == [RELEVANCE_BATCH_SIZE, 1]
    assert {item.work_id for item in result} == {item.work_id for item in tasks}


@pytest.mark.anyio
async def test_unknown_evidence_triggers_one_targeted_batch_repair() -> None:
    task = RelevanceJudgementTask(
        work_id=uuid4(), title="Grounded agent", abstract="Traceable evidence."
    )

    class RepairingExpert:
        def __init__(self) -> None:
            self.call_count = 0

        async def run(self, payload: dict[str, object]) -> FacetJudgementBatch:
            self.call_count += 1
            evidence_id = (
                "INVENTED" if self.call_count == 1 else str(task.evidence[0]["evidence_id"])
            )
            return FacetJudgementBatch(judgements=[judgement(task.work_id, evidence_id)])

    expert = RepairingExpert()
    result = await RelevanceFacetJudge(expert).judge(protocol=protocol(), tasks=[task])

    assert expert.call_count == 2
    assert result[0].evidence_ids == [task.evidence[0]["evidence_id"]]


@pytest.mark.parametrize(
    ("status", "centrality", "expected"),
    [
        (FacetStatus.SUPPORTED, Centrality.CENTRAL, RelevanceDecision.PASS),
        (FacetStatus.NOT_SUPPORTED, Centrality.CENTRAL, RelevanceDecision.FAIL),
        (FacetStatus.UNCERTAIN, Centrality.CENTRAL, RelevanceDecision.REVIEW),
        (FacetStatus.SUPPORTED, Centrality.SUPPORTING, RelevanceDecision.FAIL),
    ],
)
def test_local_pass_requires_supported_central_llm_judgement(
    status: FacetStatus,
    centrality: Centrality,
    expected: RelevanceDecision,
) -> None:
    work_id = uuid4()
    local = RelevanceScore(
        work_id=work_id,
        lexical_score=0.8,
        semantic_score=0.9,
        cross_encoder_score=0.9,
        facet_scores={"grounding": 0.8},
        decision=RelevanceDecision.PASS,
    )

    result = apply_facet_judgement(
        score=local,
        judgement=judgement(
            work_id,
            f"META_TITLE_{work_id.hex}",
            status=status,
            centrality=centrality,
        ),
        protocol=protocol(),
        model_identifier="openai_compatible[fixture]:gpt-5.5",
    )

    assert result.decision == expected
    assert result.facet_judgement is not None
    assert result.model_versions["facet_prompt"] == "2026-08-22.1"


@pytest.mark.anyio
async def test_search_only_relevance_never_constructs_or_calls_llm_expert() -> None:
    work_id = uuid4()
    work = SimpleNamespace(
        id=work_id,
        canonical_title="Evidence grounded agent",
        abstract="Claims bind to provenance.",
    )
    local = RelevanceScore(
        work_id=work_id,
        lexical_score=0.8,
        semantic_score=0.9,
        cross_encoder_score=0.9,
        facet_scores={"grounding": 0.8},
        decision=RelevanceDecision.PASS,
    )
    scorer = MagicMock()
    scorer.score = AsyncMock(return_value=[local])
    stages = ResearchPipelineStages(AsyncMock())
    stages.protocol = AsyncMock(return_value=protocol())
    with (
        patch(
            "app.services.literature_research.pipeline_stages."
            "evidence_repository.list_eligible_work_documents",
            new=AsyncMock(return_value=[(work, None, None)]),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.get_research_embedding_provider",
            return_value=(MagicMock(), 384, "embedding-fixture"),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.RelevanceScoringService",
            return_value=scorer,
        ),
        patch(
            "app.services.literature_research.pipeline_stages."
            "evidence_repository.persist_relevance",
            new=AsyncMock(),
        ) as persist,
        patch(
            "app.services.literature_research.pipeline_stages."
            "run_repository.set_counts_and_progress",
            new=AsyncMock(),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.LiteratureResearchExperts"
        ) as experts,
    ):
        result = await stages.score_relevance(
            SimpleNamespace(
                id=uuid4(),
                execution_mode="search_only",
            )
        )

    experts.assert_not_called()
    assert "relevance_llm_usage" not in result
    assert persist.await_args.kwargs["scores"][0].facet_judgement is None


@pytest.mark.anyio
async def test_full_research_persists_facet_judgement_and_usage_progress() -> None:
    work_id = uuid4()
    work = SimpleNamespace(
        id=work_id,
        canonical_title="Evidence grounded agent",
        abstract="Claims bind to provenance.",
    )
    local = RelevanceScore(
        work_id=work_id,
        lexical_score=0.8,
        semantic_score=0.9,
        cross_encoder_score=0.9,
        facet_scores={"grounding": 0.8},
        decision=RelevanceDecision.PASS,
    )
    scorer = MagicMock()
    scorer.score = AsyncMock(return_value=[local])
    judged = judgement(work_id, f"META_TITLE_{work_id.hex}")
    stages = ResearchPipelineStages(AsyncMock())
    stages.protocol = AsyncMock(return_value=protocol())
    with (
        patch(
            "app.services.literature_research.pipeline_stages."
            "evidence_repository.list_eligible_work_documents",
            new=AsyncMock(return_value=[(work, None, None)]),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.get_research_embedding_provider",
            return_value=(MagicMock(), 384, "embedding-fixture"),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.RelevanceScoringService",
            return_value=scorer,
        ),
        patch(
            "app.services.literature_research.pipeline_stages.RelevanceFacetJudge.judge",
            new=AsyncMock(return_value=[judged]),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.LiteratureResearchExperts",
            return_value=SimpleNamespace(relevance=AsyncMock()),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.selected_llm_model_identifier",
            return_value="openai_compatible[fixture]:gpt-5.5",
        ),
        patch(
            "app.services.literature_research.pipeline_stages."
            "evidence_repository.persist_relevance",
            new=AsyncMock(),
        ) as persist,
        patch(
            "app.services.literature_research.pipeline_stages."
            "run_repository.set_counts_and_progress",
            new=AsyncMock(),
        ) as set_progress,
    ):
        result = await stages.score_relevance(
            SimpleNamespace(
                id=uuid4(),
                execution_mode="full_research",
            )
        )

    persisted = persist.await_args.kwargs["scores"][0]
    assert persisted.decision == RelevanceDecision.PASS
    assert persisted.facet_judgement is not None
    assert result["facet_judged_count"] == 1
    progress = set_progress.await_args.kwargs["progress"]
    assert progress["relevance_prompt_version"] == "2026-08-22.1"
    assert progress["relevance_llm_usage"]["total"]["total_tokens"] == 0
