"""Deployed PostgreSQL + MinIO E2E for Phase 3/5 audit artifacts.

This verifier performs no network retrieval and no LLM calls. It creates a
synthetic fixture inside one PostgreSQL transaction, writes all eight release
artifacts through the production object-store service, re-reads them from
MinIO, checks both audit CSVs against the authoritative ledgers, proves that a
ledger mutation is detected, and finally removes every object and rolls the
database transaction back.

Run from the deployed ``app`` container so PostgreSQL and MinIO service names
resolve exactly as they do for the application.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.db.models.literature_research.analysis import ResearchArtifact
from app.db.models.literature_research.discovery import (
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.project import ResearchProject
from app.db.models.literature_research.protocol import ResearchProtocolVersion
from app.db.models.literature_research.quality import (
    ResearchConstraintEvaluation,
    ResearchMetricSnapshot,
    ResearchVenueMetricFact,
    ResearchWorkEligibility,
)
from app.db.models.literature_research.run import ResearchRun
from app.db.models.user import User
from app.db.session import async_session_maker
from app.schemas.literature_research.analysis import (
    AuditedPaperAnalysis,
    AuditReport,
    SynthesisOutput,
)
from app.schemas.literature_research.release import (
    ArtifactFormat,
    CanonicalResearchReport,
    ReportPaper,
)
from app.services.literature_research.artifact_service import ArtifactService
from app.services.literature_research.audit_exports import (
    collect_exclusion_audit_rows,
    collect_metric_snapshot_audit_rows,
)
from app.services.literature_research.object_store import (
    S3ResearchObjectStore,
    get_research_object_store,
    research_object_prefix,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def csv_rows(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


async def prefix_object_keys(store: S3ResearchObjectStore, prefix: str) -> list[str]:
    response = await asyncio.to_thread(
        store.client.list_objects_v2,
        Bucket=store.bucket,
        Prefix=prefix,
    )
    return [item["Key"] for item in response.get("Contents", [])]


async def remove_objects(store: S3ResearchObjectStore, keys: list[str]) -> None:
    if keys:
        await asyncio.to_thread(
            store.client.delete_objects,
            Bucket=store.bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )


async def prefix_object_count(store: S3ResearchObjectStore, prefix: str) -> int:
    return len(await prefix_object_keys(store, prefix))


async def database_fixture_count(*, run_id: UUID, project_id: UUID, user_id: UUID) -> int:
    async with async_session_maker() as db:
        counts = [
            await db.scalar(select(func.count()).select_from(model).where(model.id == row_id))
            for model, row_id in (
                (ResearchRun, run_id),
                (ResearchProject, project_id),
                (User, user_id),
            )
        ]
        return sum(int(value or 0) for value in counts)


async def main() -> None:
    store = get_research_object_store()
    require(
        isinstance(store, S3ResearchObjectStore),
        "This deployed verifier requires the configured S3/MinIO object store",
    )

    now = datetime.now(UTC)
    suffix = uuid4().hex
    user_id, project_id, protocol_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    venue_id, included_id, excluded_id = uuid4(), uuid4(), uuid4()
    included_version_id, excluded_version_id = uuid4(), uuid4()
    snapshot_id, fact_id = uuid4(), uuid4()
    protocol_hash = "sha256:" + "e" * 64
    prefix = research_object_prefix(
        organization_id=None,
        project_id=project_id,
        run_id=run_id,
    )
    object_keys: list[str] = []
    output: dict[str, object] = {}

    async with async_session_maker() as db:
        transaction = await db.begin()
        try:
            user = User(
                id=user_id,
                email=f"phase35-artifact-e2e-{suffix}@example.invalid",
                full_name="Phase 3/5 artifact E2E fixture",
                is_active=True,
                role="user",
                is_app_admin=False,
            )
            project = ResearchProject(
                id=project_id,
                owner_id=user_id,
                title="Synthetic Phase 3/5 audit fixture",
                description="Ephemeral deployed verification; never formal research data.",
                status="active",
            )
            protocol = ResearchProtocolVersion(
                id=protocol_id,
                project_id=project_id,
                version=1,
                protocol_json={"fixture": True, "allowed_types": ["journal_article"]},
                protocol_hash=protocol_hash,
                status="approved",
                approved_at=now,
                approved_by=user_id,
            )
            run = ResearchRun(
                id=run_id,
                project_id=project_id,
                protocol_version_id=protocol_id,
                owner_id=user_id,
                state="COMPLETED",
                execution_mode="full_research",
                client_request_id=f"phase35-artifact-e2e-{suffix}",
                protocol_hash=protocol_hash,
                target_count=2,
                strict_count=1,
                candidate_count=2,
                analyzed_count=1,
                progress_json={"fixture": True},
                started_at=now,
                finished_at=now,
            )
            db.add(user)
            await db.flush()
            db.add(project)
            await db.flush()
            db.add(protocol)
            await db.flush()
            db.add(run)
            await db.flush()

            venue = ResearchVenue(
                id=venue_id,
                name=f"Synthetic Audit Journal {suffix[:8]}",
                normalized_name=f"synthetic audit journal {suffix[:8]}",
                venue_type="journal",
                issn_l="9999-9999",
                issns_json=["9999-9999"],
                publisher="E2E fixture publisher",
            )
            included = ResearchWork(
                id=included_id,
                run_id=run_id,
                cluster_key="1" * 64,
                canonical_title="Included synthetic audit paper",
                normalized_title="included synthetic audit paper",
                abstract="Synthetic content used only to verify deployed artifacts.",
                document_type="journal_article",
                language="en",
                authors_json=[{"name": "Fixture Author"}],
                field_provenance_json={"title": "e2e-fixture"},
                duplicate_decisions_json=[],
            )
            excluded = ResearchWork(
                id=excluded_id,
                run_id=run_id,
                cluster_key="2" * 64,
                canonical_title="Excluded synthetic audit paper",
                normalized_title="excluded synthetic audit paper",
                abstract="Synthetic exclusion used only to verify the decision ledger.",
                document_type="journal_article",
                language="en",
                authors_json=[{"name": "Fixture Author"}],
                field_provenance_json={"title": "e2e-fixture"},
                duplicate_decisions_json=[],
            )
            db.add_all([venue, included, excluded])
            await db.flush()

            included_version = ResearchWorkVersion(
                id=included_version_id,
                work_id=included_id,
                venue_id=venue_id,
                source="crossref",
                source_id=f"10.9999/included-{suffix}",
                version_type="version_of_record",
                doi=f"10.9999/included-{suffix}",
                published_online=date(2025, 4, 1),
                effective_publication_date=date(2025, 4, 1),
                effective_date_field="published_online",
                effective_date_source="crossref",
                canonical_url=f"https://doi.org/10.9999/included-{suffix}",
                raw_sha256="a" * 64,
            )
            excluded_version = ResearchWorkVersion(
                id=excluded_version_id,
                work_id=excluded_id,
                venue_id=venue_id,
                source="crossref",
                source_id=f"10.9999/excluded-{suffix}",
                version_type="version_of_record",
                doi=f"10.9999/excluded-{suffix}",
                published_online=date(2025, 4, 2),
                effective_publication_date=date(2025, 4, 2),
                effective_date_field="published_online",
                effective_date_source="crossref",
                canonical_url=f"https://doi.org/10.9999/excluded-{suffix}",
                raw_sha256="b" * 64,
            )
            db.add_all([included_version, excluded_version])
            await db.flush()
            included.preferred_version_id = included_version_id
            excluded.preferred_version_id = excluded_version_id

            snapshot = ResearchMetricSnapshot(
                id=snapshot_id,
                source_name="Synthetic licensed-metric E2E fixture",
                source_version="2025-e2e",
                metric_names_json=["impact_factor"],
                effective_from=date(2025, 1, 1),
                effective_to=date(2025, 12, 31),
                license_reference="E2E-ONLY-NOT-A-REAL-LICENSE",
                authorized_scope="Ephemeral local deployment verification only",
                license_attested=True,
                status="ACTIVE",
                imported_by=user_id,
                imported_at=now,
                payload_sha256="c" * 64,
                object_key=f"{prefix}/fixtures/metric-snapshot.csv",
            )
            fact = ResearchVenueMetricFact(
                id=fact_id,
                snapshot_id=snapshot_id,
                venue_id=venue_id,
                venue_name=venue.name,
                venue_normalized_name=venue.normalized_name,
                venue_type="journal",
                issn_l="9999-9999",
                metric_name="impact_factor",
                metric_value=8.2,
                metric_year=2025,
                source_row=2,
            )
            db.add(snapshot)
            await db.flush()
            db.add(fact)
            await db.flush()

            included_eligibility = ResearchWorkEligibility(
                run_id=run_id,
                work_id=included_id,
                version_id=included_version_id,
                protocol_hash=protocol_hash,
                eligible=True,
                hard_pass_count=1,
                hard_fail_count=0,
                hard_unknown_count=0,
                evaluated_at=now,
            )
            excluded_eligibility = ResearchWorkEligibility(
                run_id=run_id,
                work_id=excluded_id,
                version_id=excluded_version_id,
                protocol_hash=protocol_hash,
                eligible=False,
                hard_pass_count=0,
                hard_fail_count=1,
                hard_unknown_count=0,
                evaluated_at=now,
            )
            metric_evaluation = ResearchConstraintEvaluation(
                run_id=run_id,
                work_id=included_id,
                version_id=included_version_id,
                protocol_hash=protocol_hash,
                constraint_id="impact-factor-floor",
                field="venue.metric.impact_factor",
                operator="gte",
                expected_value=5.0,
                observed_value=8.2,
                severity="hard",
                decision="PASS",
                reason_code="COMPARISON_PASSED",
                evidence_reference=f"metric-snapshot:{snapshot_id}:row:2",
                metric_snapshot_id=snapshot_id,
                metric_fact_id=fact_id,
                metric_year=2025,
                evaluated_at=now,
            )
            exclusion_evaluation = ResearchConstraintEvaluation(
                run_id=run_id,
                work_id=excluded_id,
                version_id=excluded_version_id,
                protocol_hash=protocol_hash,
                constraint_id="publication-year-floor",
                field="publication.year",
                operator="gte",
                expected_value=2026,
                observed_value=2025,
                severity="hard",
                decision="FAIL",
                reason_code="COMPARISON_FAILED",
                evidence_reference=f"work-version:{excluded_version_id}",
                evaluated_at=now,
            )
            db.add_all(
                [
                    included_eligibility,
                    excluded_eligibility,
                    metric_evaluation,
                    exclusion_evaluation,
                ]
            )
            await db.flush()

            audit = AuditReport(
                work_id=included_id,
                claims=[],
                evidence_coverage=1.0,
                contradicted_count=0,
                unsupported_count=0,
            )
            analysis = AuditedPaperAnalysis(
                work_id=included_id,
                sections=[],
                figures=[],
                audit=audit,
            )
            synthesis = SynthesisOutput(
                overview="One synthetic work passed the immutable fixture protocol.",
                themes=[],
                included_work_ids=[included_id],
            )
            report = CanonicalResearchReport(
                run_id=run_id,
                project_id=project_id,
                protocol_hash=protocol_hash,
                title="Deployed Phase 3/5 audit verification",
                target_count=2,
                strict_count=1,
                shortfall_disclosed=True,
                synthesis=synthesis,
                papers=[
                    ReportPaper(
                        work_id=included_id,
                        version_id=included_version_id,
                        title=included.canonical_title,
                        authors=["Fixture Author"],
                        year=2025,
                        doi=included_version.doi,
                        source_url=included_version.canonical_url,
                        document_type=included.document_type,
                        venue=venue.name,
                        relevance_score=0.95,
                        hard_constraints_passed=True,
                        analysis=analysis,
                    )
                ],
            )
            exclusion_rows = await collect_exclusion_audit_rows(
                db,
                run_id=run_id,
                included_work_ids={included_id},
            )
            metric_rows = await collect_metric_snapshot_audit_rows(db, run_id=run_id)
            require(len(exclusion_rows) == 1, "Expected exactly one exclusion audit row")
            require(len(metric_rows) == 1, "Expected exactly one metric provenance row")

            service = ArtifactService(db, store)
            artifacts = await service.render_all(
                report,
                organization_id=None,
                source_snapshot_hashes=[],
                metric_snapshot_ids=[snapshot_id],
                exclusion_rows=exclusion_rows,
                metric_snapshot_rows=metric_rows,
                model_versions={"fixture": "zero-llm-deployed-e2e"},
                llm_usage={"calls": 0},
            )
            artifact_rows = await service.db.execute(
                select(ResearchArtifact).where(ResearchArtifact.run_id == run_id)
            )
            object_keys = [row.object_key for row in artifact_rows.scalars().all()]
            require({item.format for item in artifacts} == set(ArtifactFormat), "Missing format")
            require(len(object_keys) == 8, "Expected eight persisted artifact rows")
            persisted_errors = await service.validate_persisted(run_id)
            require(not persisted_errors, f"Persisted audit failed: {persisted_errors}")

            by_format = {item.format: item.data for item in artifacts}
            exclusion_csv = csv_rows(by_format[ArtifactFormat.EXCLUSIONS_CSV])
            metric_csv = csv_rows(by_format[ArtifactFormat.VENUE_METRICS_CSV])
            require(
                exclusion_csv[0]["work_id"] == str(excluded_id)
                and "COMPARISON_FAILED" in exclusion_csv[0]["reason_codes"],
                "Exclusion CSV lost the hard-failure provenance",
            )
            require(
                metric_csv[0]["metric_fact_id"] == str(fact_id)
                and metric_csv[0]["metric_year"] == "2025",
                "Metric CSV lost fact/year provenance",
            )
            manifest = json.loads(by_format[ArtifactFormat.MANIFEST])
            require(manifest["llm_usage"] == {"calls": 0}, "Manifest must prove zero LLM calls")

            metric_evaluation.observed_value = 9.1
            await db.flush()
            tamper_errors = await service.validate_persisted(run_id)
            require(
                "venue_metrics_snapshot.csv: authoritative ledger mismatch" in tamper_errors,
                "Authoritative-ledger mutation was not detected",
            )
            output = {
                "artifact_formats": sorted(item.value for item in ArtifactFormat),
                "artifact_objects": len(object_keys),
                "database": "postgresql",
                "excluded_reason": "COMPARISON_FAILED",
                "llm_calls": 0,
                "metric_fact_id": str(fact_id),
                "metric_year": 2025,
                "object_store": "minio",
                "tamper_detection": True,
            }
        finally:
            await transaction.rollback()
            await remove_objects(store, await prefix_object_keys(store, prefix))

    require(
        await database_fixture_count(run_id=run_id, project_id=project_id, user_id=user_id) == 0,
        "PostgreSQL rollback left fixture rows behind",
    )
    require(await prefix_object_count(store, prefix) == 0, "MinIO cleanup left fixture objects")
    output["cleanup"] = {"minio_objects": 0, "postgresql_fixture_roots": 0}
    output["status"] = "phase35_artifact_audit_e2e_ok"
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
