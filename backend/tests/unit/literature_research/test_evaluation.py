"""Offline evaluation metric and gold-dataset contract tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.literature_research.evaluation import (
    EvaluationDatasetCreate,
    GoldDatasetProvenance,
    GoldDatasetStatus,
    GoldPaperCase,
    GoldSourceObservation,
    MetricStatus,
)
from app.services.literature_research.evaluation import _metric, _ndcg, _pairwise_f1


def test_ndcg_rewards_correct_top_ranking() -> None:
    assert _ndcg([3, 2, 0], ideal_grades=[3, 2, 0]) == 1.0
    assert _ndcg([0, 2, 3], ideal_grades=[3, 2, 0]) < 1.0


def test_pairwise_dedup_f1_compares_source_observation_clusters() -> None:
    observations = [
        GoldSourceObservation(source="crossref", source_id="doi:1", expected_cluster_id="A"),
        GoldSourceObservation(source="arxiv", source_id="arxiv:1", expected_cluster_id="A"),
        GoldSourceObservation(source="openalex", source_id="W2", expected_cluster_id="B"),
    ]
    score, samples = _pairwise_f1(
        observations,
        {
            ("crossref", "doi:1"): "W_A",
            ("arxiv", "arxiv:1"): "W_A",
            ("openalex", "W2"): "W_B",
        },
    )
    assert score == 1.0
    assert samples == 3


def test_missing_gold_dimension_is_not_silently_passed() -> None:
    metric = _metric(None, threshold=0.99, sample_size=0)
    assert metric.status == MetricStatus.NOT_EVALUATED


def test_tiny_gold_sample_is_not_allowed_to_claim_threshold_pass() -> None:
    metric = _metric(1.0, threshold=0.95, sample_size=1, minimum_sample_size=20)
    assert metric.status == MetricStatus.NOT_EVALUATED


def test_gold_case_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="case_id"):
        EvaluationDatasetCreate(
            project_id=uuid4(),
            name="Agent systems gold set",
            version="1.0",
            cases=[
                GoldPaperCase(case_id="same", title="Paper A", relevant=True),
                GoldPaperCase(case_id="same", title="Paper B", relevant=False),
            ],
        )


def test_adjudicated_gold_requires_human_provenance() -> None:
    with pytest.raises(ValidationError, match="annotation provenance"):
        EvaluationDatasetCreate(
            project_id=uuid4(),
            name="Agent systems gold set",
            version="1.0",
            status=GoldDatasetStatus.ADJUDICATED,
            cases=[GoldPaperCase(case_id="paper-1", title="Paper A", relevant=True)],
        )


def test_adjudicated_gold_requires_two_annotators() -> None:
    with pytest.raises(ValidationError, match="at least two annotators"):
        EvaluationDatasetCreate(
            project_id=uuid4(),
            name="Agent systems gold set",
            version="1.0",
            status=GoldDatasetStatus.ADJUDICATED,
            provenance=GoldDatasetProvenance(
                source_name="Local annotation",
                source_url="https://example.org/provenance",
                license="Project internal",
                annotator_count=1,
                judgment_method="One annotator only",
                completed_at="2026-08-21T00:00:00Z",
                domain_coverage=["computer science"],
                language_coverage=["en"],
            ),
            cases=[GoldPaperCase(case_id="paper-1", title="Paper A", relevant=True)],
        )


def test_relevance_boolean_must_match_grade() -> None:
    with pytest.raises(ValidationError, match="must agree"):
        GoldPaperCase(
            case_id="paper-1",
            title="Paper A",
            relevant=False,
            relevance_grade=2,
        )
