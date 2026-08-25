"""Phase 4 relevance, lawful acquisition, evidence, and isolation tests."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.literature_research.evidence import (
    ResearchFigureArtifact,
    ResearchParsingResult,
)
from app.repositories.literature_research import evidence as evidence_repository
from app.schemas.literature_research.evidence import (
    FullTextCandidate,
    FullTextSource,
    LicenseDecision,
    RelevanceDecision,
)
from app.schemas.literature_research.protocol import TopicFacet, TopicModel
from app.services.literature_research.evidence_locator import (
    build_parsed_block,
    locate_quote,
)
from app.services.literature_research.fulltext_policy import LawfulFullTextPolicy
from app.services.literature_research.pipeline_stages import ResearchPipelineStages
from app.services.literature_research.relevance import RelevanceScoringService
from app.services.literature_research.vector_namespace import (
    research_collection_name,
    research_payload_filter,
)


class FixedModel:
    def __init__(self, version: str, values: list[float]) -> None:
        self.version = version
        self.values = values

    async def score(self, query: str, documents: list[str]) -> list[float]:
        del query
        return self.values[: len(documents)]


def test_audit_ledger_timestamp_models_match_non_null_database_defaults() -> None:
    for model in (ResearchParsingResult, ResearchFigureArtifact):
        column = model.__table__.c.updated_at
        assert column.nullable is False
        assert column.server_default is not None


@pytest.mark.anyio
async def test_relevance_ordering_is_applied_only_after_scores_are_joined() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    await evidence_repository.list_eligible_work_documents(db, run_id=uuid4())
    eligible_sql = str(db.execute.await_args.args[0])
    assert "ORDER BY research_works.id ASC" in eligible_sql
    assert "ORDER BY research_relevance_scores" not in eligible_sql

    await evidence_repository.list_analysis_ready_versions(db, run_id=uuid4())
    analysis_sql = str(db.execute.await_args.args[0])
    assert "JOIN research_relevance_scores" in analysis_sql
    assert "ORDER BY research_relevance_scores.cross_encoder_score DESC NULLS LAST" in analysis_sql


@pytest.mark.anyio
async def test_release_safety_query_is_scoped_to_selected_papers() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=0)
    work_id = uuid4()

    assert (
        await evidence_repository.count_unsafe_or_unscanned_fulltexts(
            db, run_id=uuid4(), work_ids={work_id}
        )
        == 0
    )
    statement = str(db.scalar.await_args.args[0])
    assert "JOIN research_work_versions" in statement
    assert "research_work_versions.work_id IN" in statement


@pytest.mark.anyio
async def test_release_requires_one_verified_figure_not_every_caption() -> None:
    db = AsyncMock()
    parsing_result = MagicMock()
    version_id = uuid4()
    parsing_result.all.return_value = [(version_id,)]
    db.execute = AsyncMock(return_value=parsing_result)
    db.scalar = AsyncMock(return_value=1)

    missing = await evidence_repository.count_incomplete_figure_artifacts(
        db, run_id=uuid4(), work_ids={uuid4()}
    )

    assert missing == 0
    statement = str(db.execute.await_args.args[0])
    assert "JOIN research_work_versions" in statement
    assert "research_work_versions.work_id IN" in statement


def topic_model() -> TopicModel:
    return TopicModel(
        must_have_facets=[
            TopicFacet(
                facet_id="audit",
                name="audit provenance",
                description="The system records audit provenance.",
                minimum_score=0.5,
            )
        ]
    )


@pytest.mark.anyio
async def test_three_stage_relevance_requires_all_floors_and_records_versions() -> None:
    works = [
        (uuid4(), "auditable research agents preserve audit provenance"),
        (uuid4(), "unrelated shopping recommender"),
    ]
    scores = await RelevanceScoringService(
        semantic_model=FixedModel("embedding-fixture-v1", [0.9, 0.9]),
        cross_encoder=FixedModel("cross-fixture-v1", [0.8]),
    ).score(
        query="auditable research agents",
        topic_model=topic_model(),
        documents=works,
    )
    assert scores[0].decision == RelevanceDecision.PASS
    assert scores[0].model_versions == {
        "semantic": "embedding-fixture-v1",
        "cross_encoder": "cross-fixture-v1",
    }
    assert scores[1].decision == RelevanceDecision.FAIL
    assert "LEXICAL_FLOOR_NOT_MET" in scores[1].reasons


@pytest.mark.anyio
async def test_missing_cross_encoder_requires_review_not_silent_pass() -> None:
    score = (
        await RelevanceScoringService().score(
            query="auditable research agents",
            topic_model=topic_model(),
            documents=[(uuid4(), "auditable research agents with audit provenance")],
        )
    )[0]
    assert score.decision == RelevanceDecision.REVIEW
    assert score.reasons == ["CROSS_ENCODER_UNAVAILABLE"]


def test_fulltext_policy_rejects_unknown_license_and_forbidden_hosts() -> None:
    version_id = uuid4()
    unknown = FullTextCandidate(
        version_id=version_id,
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/paper.pdf",
        license_decision=LicenseDecision.UNKNOWN,
    )
    forbidden = FullTextCandidate(
        version_id=version_id,
        source=FullTextSource.USER_UPLOAD,
        url="https://sci-hub.example/paper.pdf",
        license_decision=LicenseDecision.ALLOWED,
        license_reference="user-claimed",
    )
    decision = LawfulFullTextPolicy().select([unknown, forbidden])
    assert decision.allowed is False
    assert decision.reason_code == "NO_VERIFIABLY_LAWFUL_FULLTEXT"


def test_fulltext_policy_prefers_verified_publisher_copy() -> None:
    version_id = uuid4()
    arxiv = FullTextCandidate(
        version_id=version_id,
        source=FullTextSource.ARXIV,
        url="https://arxiv.org/pdf/2607.00001",
        license_decision=LicenseDecision.ALLOWED,
        license_reference="arxiv-record:2607.00001",
        is_open_access=True,
    )
    publisher = FullTextCandidate(
        version_id=version_id,
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/paper.pdf",
        license_decision=LicenseDecision.ALLOWED,
        license_reference="publisher-license:cc-by-4.0",
        is_open_access=True,
    )
    assert LawfulFullTextPolicy().select([arxiv, publisher]).selected == publisher


def test_evidence_quote_is_exact_hash_bound_and_page_located() -> None:
    block = build_parsed_block(
        block_id="p3-b2",
        text="The system records an immutable audit trail for every claim.",
        char_start=1200,
        page_number=3,
        section_path=["Methods", "Audit"],
    )
    document_hash = hashlib.sha256(b"fixture-pdf").hexdigest()
    evidence = locate_quote(
        work_id=uuid4(),
        version_id=uuid4(),
        block=block,
        quote="immutable audit trail",
        document_sha256=document_hash,
    )
    assert evidence.page_number == 3
    assert evidence.quote_start == 1222
    assert evidence.block_text_sha256 == block.text_sha256
    with pytest.raises(ValueError, match="exact substring"):
        locate_quote(
            work_id=evidence.work_id,
            version_id=evidence.version_id,
            block=block,
            quote="fabricated evidence",
            document_sha256=document_hash,
        )


def test_vector_namespace_is_project_isolated_and_filter_is_mandatory() -> None:
    organization_id, project_a, project_b, run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    assert research_collection_name(organization_id, project_a) != (
        research_collection_name(organization_id, project_b)
    )
    payload_filter = research_payload_filter(
        organization_id=organization_id, project_id=project_a, run_id=run_id
    )
    keys = {item["key"] for item in payload_filter["must"]}
    assert keys == {"tenant_id", "project_id", "run_id"}


@pytest.mark.anyio
async def test_fulltext_stage_queries_unpaywall_once_per_normalized_doi() -> None:
    first_id, second_id = uuid4(), uuid4()
    rows = [
        (
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(
                id=first_id,
                source="crossref",
                open_access_pdf_url=None,
                arxiv_id=None,
                source_id="one",
                doi="10.1000/SAME",
            ),
        ),
        (
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(
                id=second_id,
                source="openalex",
                open_access_pdf_url=None,
                arxiv_id=None,
                source_id="two",
                doi="https://doi.org/10.1000/same",
            ),
        ),
    ]
    resolver = MagicMock()
    resolver.candidate = AsyncMock(
        return_value=FullTextCandidate(
            version_id=first_id,
            source=FullTextSource.UNPAYWALL,
            url="https://repository.example/paper.pdf",
            license_decision=LicenseDecision.ALLOWED,
            license_reference="unpaywall:10.1000/same:cc-by",
            is_open_access=True,
        )
    )
    resolver.aclose = AsyncMock()
    acquisition = MagicMock()
    acquisition.acquire = AsyncMock(
        side_effect=[ValueError("publisher returned mislabeled content"), None]
    )
    acquisition.aclose = AsyncMock()
    with (
        patch(
            "app.services.literature_research.pipeline_stages.evidence_repository.list_relevant_versions",
            new=AsyncMock(return_value=rows),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.evidence_repository.persist_acquisition",
            new=AsyncMock(),
        ) as persist_acquisition,
        patch(
            "app.services.literature_research.pipeline_stages.UnpaywallClient",
            return_value=resolver,
        ),
        patch(
            "app.services.literature_research.pipeline_stages.FullTextAcquisitionService",
            return_value=acquisition,
        ),
        patch(
            "app.services.literature_research.pipeline_stages.settings.CROSSREF_MAILTO",
            "research@example.org",
        ),
    ):
        outcome = await ResearchPipelineStages(AsyncMock()).acquire_fulltext(
            SimpleNamespace(
                id=uuid4(),
                organization_id=uuid4(),
                project_id=uuid4(),
            )
        )

    resolver.candidate.assert_awaited_once_with(version_id=first_id, doi="10.1000/same")
    assert persist_acquisition.await_count == 2
    assert outcome["requested_count"] == 2
    assert outcome["unique_doi_lookup_count"] == 1
    assert outcome["fulltext_fetch_failure_count"] == 1
