"""Reproducible evaluation of a research run against a human gold dataset."""

import hashlib
import math
from datetime import UTC, datetime
from itertools import combinations
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.db.models.literature_research.evaluation import ResearchEvaluationDataset
from app.domain.literature_research.normalization import normalize_doi, normalize_title
from app.repositories.literature_research import analysis as analysis_repository
from app.repositories.literature_research import catalog as catalog_repository
from app.repositories.literature_research import evaluation as evaluation_repository
from app.repositories.literature_research import evidence as evidence_repository
from app.schemas.literature_research.analysis import AuditedPaperAnalysis
from app.schemas.literature_research.evaluation import (
    EvaluationDatasetCreate,
    EvaluationMetric,
    EvaluationReport,
    GoldDatasetStatus,
    GoldPaperCase,
    GoldSourceObservation,
    MetricStatus,
)
from app.services.literature_research.artifact_service import ArtifactService
from app.services.literature_research.project import ResearchProjectService
from app.services.literature_research.run import ResearchRunService

THRESHOLDS = {
    "recall_at_pool": 0.95,
    "precision_at_20": 0.90,
    "ndcg_at_20": 0.90,
    "constraint_compliance": 1.00,
    "dedup_pairwise_f1": 0.98,
    "metadata_accuracy": 0.99,
    "evidence_precision": 0.95,
    "claim_evidence_coverage": 0.90,
    "numeric_accuracy": 0.99,
    "retry_recovery_success": 0.98,
    "artifact_validation": 1.00,
}

MIN_SAMPLES = {
    "recall_at_pool": 20,
    "precision_at_20": 20,
    "ndcg_at_20": 20,
    "constraint_compliance": 20,
    "dedup_pairwise_f1": 20,
    "metadata_accuracy": 20,
    "evidence_precision": 20,
    "claim_evidence_coverage": 10,
    "numeric_accuracy": 10,
    "retry_recovery_success": 10,
    "artifact_validation": 1,
}


def _identity(title: str, doi: str | None) -> str:
    normalized_doi = normalize_doi(doi)
    return f"doi:{normalized_doi}" if normalized_doi else f"title:{normalize_title(title)}"


def _metric(
    value: float | None,
    threshold: float,
    sample_size: int,
    *,
    minimum_sample_size: int = 1,
) -> EvaluationMetric:
    if value is None or sample_size < minimum_sample_size:
        status = MetricStatus.NOT_EVALUATED
    else:
        status = MetricStatus.PASS if value >= threshold else MetricStatus.FAIL
    return EvaluationMetric(
        value=value,
        threshold=threshold,
        sample_size=sample_size,
        status=status,
    )


def _ndcg(labels: list[int], ideal_grades: list[int], k: int = 20) -> float:
    dcg = sum(((2**label) - 1) / math.log2(index + 2) for index, label in enumerate(labels[:k]))
    ideal = sum(
        ((2**label) - 1) / math.log2(index + 2)
        for index, label in enumerate(sorted(ideal_grades, reverse=True)[:k])
    )
    return dcg / ideal if ideal else 1.0


def _pairwise_f1(
    observations: list[GoldSourceObservation], actual: dict[tuple[str, str], str]
) -> tuple[float | None, int]:
    pairs = list(combinations(observations, 2))
    if not pairs:
        return None, 0
    true_positive = false_positive = false_negative = 0
    for left, right in pairs:
        expected = left.expected_cluster_id == right.expected_cluster_id
        left_actual = actual.get((left.source, left.source_id))
        right_actual = actual.get((right.source, right.source_id))
        predicted = left_actual is not None and left_actual == right_actual
        true_positive += int(expected and predicted)
        false_positive += int(not expected and predicted)
        false_negative += int(expected and not predicted)
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 1
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 1
    )
    return (2 * precision * recall / (precision + recall) if precision + recall else 0.0), len(
        pairs
    )


def _relevance_score(row: catalog_repository.CandidateRow) -> float:
    relevance = row[4]
    if relevance is None:
        return -1.0
    return next(
        value
        for value in (
            relevance.cross_encoder_score,
            relevance.semantic_score,
            relevance.lexical_score,
        )
        if value is not None
    )


class ResearchEvaluationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.projects = ResearchProjectService(db)
        self.runs = ResearchRunService(db)

    async def create_dataset(self, *, owner_id: UUID, body: EvaluationDatasetCreate) -> object:
        await self.projects.get_owned(body.project_id, owner_id)
        return await evaluation_repository.create_dataset(self.db, created_by=owner_id, body=body)

    async def list_datasets(
        self, *, owner_id: UUID, project_id: UUID
    ) -> list[ResearchEvaluationDataset]:
        await self.projects.get_owned(project_id, owner_id)
        return await evaluation_repository.list_datasets(self.db, project_id=project_id)

    async def evaluate(self, *, owner_id: UUID, run_id: UUID, dataset_id: UUID) -> EvaluationReport:
        run = await self.runs.get_owned(run_id, owner_id)
        dataset = await evaluation_repository.get_dataset(
            self.db, dataset_id=dataset_id, project_id=run.project_id
        )
        if dataset is None:
            raise NotFoundError(message="Evaluation dataset not found")
        evaluation_statuses = {
            GoldDatasetStatus.ADJUDICATED.value,
            GoldDatasetStatus.EXTERNAL_BENCHMARK.value,
        }
        if dataset.status not in evaluation_statuses:
            raise ConflictError(
                message="Only adjudicated or declared external benchmark datasets may evaluate runs"
            )
        cases = [GoldPaperCase.model_validate(item) for item in dataset.cases_json]
        observations = [
            GoldSourceObservation.model_validate(item) for item in dataset.observations_json
        ]
        rows, _ = await catalog_repository.list_candidate_rows(
            self.db, run_id=run_id, skip=0, limit=100_000
        )
        candidate_by_identity = {
            _identity(work.canonical_title, version.doi if version else None): row
            for row in rows
            for work, version, *_ in [row]
        }
        relevant_gold = {_identity(case.title, case.doi) for case in cases if case.relevant}
        grade_by_identity = {
            _identity(case.title, case.doi): case.relevance_grade or 0 for case in cases
        }
        pool_hits = relevant_gold & set(candidate_by_identity)
        recall = len(pool_hits) / len(relevant_gold) if relevant_gold else 1.0
        ranked = sorted(
            (row for row in rows if row[4] is not None and row[4].decision == "PASS"),
            key=_relevance_score,
            reverse=True,
        )
        ranked_grades = [
            grade_by_identity.get(
                _identity(row[0].canonical_title, row[1].doi if row[1] else None), 0
            )
            for row in ranked[:20]
        ]
        ranked_labels = [int(grade > 0) for grade in ranked_grades]
        precision = sum(ranked_labels) / len(ranked_labels) if ranked_labels else 0.0
        ndcg = _ndcg(ranked_grades, list(grade_by_identity.values()))
        strict_rows = [row for row in ranked if row[3] is not None and row[3].eligible]
        constraint_compliance = len(strict_rows) / len(ranked) if ranked else 1.0

        metadata_correct = metadata_total = 0
        matched_work_ids: dict[str, UUID] = {}
        for case in cases:
            actual = candidate_by_identity.get(_identity(case.title, case.doi))
            if actual is None:
                continue
            work, version, venue, *_ = actual
            matched_work_ids[case.case_id] = work.id
            checks = [
                (case.doi, version.doi if version else None),
                (case.expected_date, version.effective_publication_date if version else None),
                (case.expected_venue, venue.name if venue else None),
            ]
            for expected, observed in checks:
                if expected is not None:
                    metadata_total += 1
                    metadata_correct += int(str(expected).casefold() == str(observed).casefold())

        evidence_correct = evidence_total = numeric_correct = numeric_total = 0
        coverages = []
        for case in cases:
            work_id = matched_work_ids.get(case.case_id)
            if work_id is None:
                continue
            evidence = await evidence_repository.list_evidence(
                self.db, run_id=run_id, work_id=work_id
            )
            allowed = set(case.allowed_quote_sha256)
            if allowed:
                for item in evidence:
                    evidence_total += 1
                    quote_hash = hashlib.sha256(item.quote.encode()).hexdigest()
                    evidence_correct += int(quote_hash in allowed)
            analysis_row = await analysis_repository.get_latest_analysis(
                self.db, run_id=run_id, work_id=work_id
            )
            if analysis_row is None:
                continue
            analysis = AuditedPaperAnalysis.model_validate(analysis_row.analysis_json)
            coverages.append(analysis.audit.evidence_coverage)
            if case.expected_numeric_values:
                observed_values = {
                    value for figure in analysis.figures for value in figure.extracted_values
                }
                numeric_total += len(case.expected_numeric_values)
                numeric_correct += len(set(case.expected_numeric_values) & observed_values)

        dedup, dedup_samples = _pairwise_f1(
            observations,
            await evaluation_repository.list_source_clusters(self.db, run_id=run_id),
        )
        tasks = await evaluation_repository.list_task_executions(self.db, run_id=run_id)
        retried = [item for item in tasks if item.attempt_count > 1]
        recovery = (
            sum(item.status == "SUCCEEDED" for item in retried) / len(retried) if retried else None
        )
        artifact_errors = await ArtifactService(self.db).validate_persisted(run_id)
        metrics = {
            "recall_at_pool": _metric(
                recall,
                THRESHOLDS["recall_at_pool"],
                len(relevant_gold),
                minimum_sample_size=MIN_SAMPLES["recall_at_pool"],
            ),
            "precision_at_20": _metric(
                precision,
                THRESHOLDS["precision_at_20"],
                len(ranked_labels),
                minimum_sample_size=MIN_SAMPLES["precision_at_20"],
            ),
            "ndcg_at_20": _metric(
                ndcg,
                THRESHOLDS["ndcg_at_20"],
                len(ranked_labels),
                minimum_sample_size=MIN_SAMPLES["ndcg_at_20"],
            ),
            "constraint_compliance": _metric(
                constraint_compliance,
                1.0,
                len(ranked),
                minimum_sample_size=MIN_SAMPLES["constraint_compliance"],
            ),
            "dedup_pairwise_f1": _metric(
                dedup,
                THRESHOLDS["dedup_pairwise_f1"],
                dedup_samples,
                minimum_sample_size=MIN_SAMPLES["dedup_pairwise_f1"],
            ),
            "metadata_accuracy": _metric(
                metadata_correct / metadata_total if metadata_total else None,
                THRESHOLDS["metadata_accuracy"],
                metadata_total,
                minimum_sample_size=MIN_SAMPLES["metadata_accuracy"],
            ),
            "evidence_precision": _metric(
                evidence_correct / evidence_total if evidence_total else None,
                THRESHOLDS["evidence_precision"],
                evidence_total,
                minimum_sample_size=MIN_SAMPLES["evidence_precision"],
            ),
            "claim_evidence_coverage": _metric(
                sum(coverages) / len(coverages) if coverages else None,
                THRESHOLDS["claim_evidence_coverage"],
                len(coverages),
                minimum_sample_size=MIN_SAMPLES["claim_evidence_coverage"],
            ),
            "numeric_accuracy": _metric(
                numeric_correct / numeric_total if numeric_total else None,
                THRESHOLDS["numeric_accuracy"],
                numeric_total,
                minimum_sample_size=MIN_SAMPLES["numeric_accuracy"],
            ),
            "retry_recovery_success": _metric(
                recovery,
                THRESHOLDS["retry_recovery_success"],
                len(retried),
                minimum_sample_size=MIN_SAMPLES["retry_recovery_success"],
            ),
            "artifact_validation": _metric(
                0.0 if artifact_errors else 1.0,
                THRESHOLDS["artifact_validation"],
                1,
                minimum_sample_size=MIN_SAMPLES["artifact_validation"],
            ),
        }
        failures = [
            f"{name}: {metric.status.value}"
            for name, metric in metrics.items()
            if metric.status != MetricStatus.PASS
        ]
        report = EvaluationReport(
            dataset_id=dataset.id,
            run_id=run.id,
            dataset_hash=dataset.payload_hash,
            metrics=metrics,
            passed=not failures,
            failures=failures,
            details={
                "artifact_errors": artifact_errors,
                "matched_case_count": len(matched_work_ids),
                "gold_case_count": len(cases),
                "minimum_sample_sizes": MIN_SAMPLES,
                "dataset_status": dataset.status,
                "dataset_limitations": (dataset.provenance_json or {}).get("limitations", []),
            },
            evaluated_at=datetime.now(UTC),
        )
        row = await evaluation_repository.persist_result(self.db, report=report)
        return report.model_copy(update={"id": row.id})

    async def list_results(self, *, owner_id: UUID, run_id: UUID) -> list[EvaluationReport]:
        await self.runs.get_owned(run_id, owner_id)
        rows = await evaluation_repository.list_results(self.db, run_id=run_id)
        return [
            EvaluationReport(
                id=row.id,
                dataset_id=row.dataset_id,
                run_id=row.run_id,
                dataset_hash=row.dataset_hash,
                metrics=row.metrics_json,
                passed=row.passed,
                failures=row.failures_json,
                details=row.details_json,
                evaluated_at=row.evaluated_at,
            )
            for row in rows
        ]
