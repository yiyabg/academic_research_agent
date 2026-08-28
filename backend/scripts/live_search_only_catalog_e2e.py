"""Zero-LLM deployed E2E for the metadata-only search-only catalog.

The verifier starts from three strict metadata candidates already persisted in
PostgreSQL. It executes the real SELECTING and RENDERING workflow stages,
stores four catalog exports in MinIO, verifies that no PDF/parser/analysis row
was created, and removes every object before rolling the fixture transaction
back. It intentionally does not manufacture formal venue metrics or call any
scholarly source.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.db.models.literature_research.analysis import ResearchArtifact, ResearchPaperAnalysis
from app.db.models.literature_research.discovery import (
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.evidence import (
    ResearchFullTextAcquisition,
    ResearchParsingResult,
    ResearchRelevanceScore,
)
from app.db.models.literature_research.project import ResearchProject
from app.db.models.literature_research.protocol import ResearchProtocolVersion
from app.db.models.literature_research.quality import ResearchWorkEligibility
from app.db.models.literature_research.run import ResearchRun
from app.db.models.user import User
from app.db.session import async_session_maker
from app.schemas.literature_research.protocol import (
    AmbiguityStatus,
    ResearchProtocol,
    default_analysis_template,
)
from app.schemas.literature_research.run import RunState
from app.services.literature_research.object_store import (
    S3ResearchObjectStore,
    get_research_object_store,
    research_object_prefix,
)
from app.services.literature_research.pipeline_stages import ResearchPipelineStages
from app.services.literature_research.workflow import ResearchWorkflowService


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def prefix_keys(store: S3ResearchObjectStore, prefix: str) -> list[str]:
    response = await asyncio.to_thread(
        store.client.list_objects_v2,
        Bucket=store.bucket,
        Prefix=prefix,
    )
    return [item["Key"] for item in response.get("Contents", [])]


async def remove_prefix(store: S3ResearchObjectStore, prefix: str) -> None:
    keys = await prefix_keys(store, prefix)
    if keys:
        await asyncio.to_thread(
            store.client.delete_objects,
            Bucket=store.bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )


async def fixture_root_count(*, run_id: UUID, project_id: UUID, user_id: UUID) -> int:
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
    require(isinstance(store, S3ResearchObjectStore), "Deployed verifier requires MinIO/S3")
    now = datetime.now(UTC)
    suffix = uuid4().hex
    user_id, project_id, protocol_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    venue_id = uuid4()
    protocol_hash = "sha256:" + "f" * 64
    protocol_json = ResearchProtocol(
        topic="synthetic metadata catalog",
        topic_definition="Ephemeral fixture for metadata-only catalog verification.",
        research_questions=["Does the catalog pipeline preserve strict metadata ranking?"],
        topic_model={
            "must_have_facets": [
                {
                    "facet_id": "catalog",
                    "name": "catalog",
                    "description": "catalog fixture topic",
                }
            ]
        },
        time_scope={"from": date(2025, 1, 1), "to": date(2025, 12, 31)},
        document_scope={"allowed_types": ["journal_article"], "allowed_languages": ["en"]},
        source_policy={"required_sources": ["crossref"], "minimum_source_families": 1},
        constraints=[
            {
                "constraint_id": "fixture-document-type",
                "field": "document_type",
                "operator": "in",
                "value": ["journal_article"],
                "verification_source": "e2e-fixture",
            }
        ],
        quantity_policy={"target_count": 2},
        analysis_template=default_analysis_template(),
        output_policy={},
        ambiguity_status=AmbiguityStatus.RESOLVED,
    ).model_dump(mode="json")
    prefix = research_object_prefix(
        organization_id=None,
        project_id=project_id,
        run_id=run_id,
    )
    output: dict[str, object] = {}

    async with async_session_maker() as db:
        transaction = await db.begin()
        try:
            db.add(
                User(
                    id=user_id,
                    email=f"search-only-catalog-{suffix}@example.invalid",
                    full_name="Search-only catalog E2E fixture",
                    is_active=True,
                    role="user",
                    is_app_admin=False,
                )
            )
            await db.flush()
            db.add(
                ResearchProject(
                    id=project_id,
                    owner_id=user_id,
                    title="Synthetic metadata catalog verification",
                    description="Ephemeral fixture; not formal research output.",
                    status="active",
                )
            )
            await db.flush()
            db.add(
                ResearchProtocolVersion(
                    id=protocol_id,
                    project_id=project_id,
                    version=1,
                    protocol_json=protocol_json,
                    protocol_hash=protocol_hash,
                    status="APPROVED",
                    approved_at=now,
                    approved_by=user_id,
                )
            )
            await db.flush()
            db.add(
                ResearchRun(
                    id=run_id,
                    project_id=project_id,
                    protocol_version_id=protocol_id,
                    owner_id=user_id,
                    state=RunState.SELECTING.value,
                    state_version=9,
                    execution_mode="search_only",
                    client_request_id=f"search-only-catalog-{suffix}",
                    protocol_hash=protocol_hash,
                    target_count=2,
                    strict_count=0,
                    candidate_count=3,
                    analyzed_count=0,
                    progress_json={
                        "query_count": 1,
                        "successful_query_count": 1,
                        "exhausted_query_count": 1,
                        "stage": RunState.SELECTING.value,
                    },
                    started_at=now,
                )
            )
            await db.flush()
            venue = ResearchVenue(
                id=venue_id,
                name=f"Catalog Fixture Journal {suffix[:8]}",
                normalized_name=f"catalog fixture journal {suffix[:8]}",
                venue_type="journal",
                issn_l="9999-9999",
                issns_json=["9999-9999"],
                publisher="Fixture publisher",
            )
            db.add(venue)
            await db.flush()

            work_ids: list[UUID] = []
            for index, score in enumerate((0.96, 0.87, 0.72), start=1):
                work_id, version_id = uuid4(), uuid4()
                work_ids.append(work_id)
                db.add(
                    ResearchWork(
                        id=work_id,
                        run_id=run_id,
                        cluster_key=f"{index}" * 64,
                        canonical_title=f"Catalog fixture paper {index}",
                        normalized_title=f"catalog fixture paper {index}",
                        abstract="Synthetic metadata-only result for deployed verification.",
                        document_type="journal_article",
                        language="en",
                        authors_json=[{"name": f"Fixture Author {index}"}],
                        field_provenance_json={"title": "e2e-fixture"},
                        duplicate_decisions_json=[],
                    )
                )
                await db.flush()
                db.add(
                    ResearchWorkVersion(
                        id=version_id,
                        work_id=work_id,
                        venue_id=venue_id,
                        source="crossref",
                        source_id=f"10.9999/catalog-{suffix}-{index}",
                        version_type="version_of_record",
                        doi=f"10.9999/catalog-{suffix}-{index}",
                        published_online=date(2025, 1, index),
                        effective_publication_date=date(2025, 1, index),
                        effective_date_field="published_online",
                        effective_date_source="crossref",
                        canonical_url=f"https://doi.org/10.9999/catalog-{suffix}-{index}",
                        raw_sha256=(str(index) * 64),
                    )
                )
                await db.flush()
                work = await db.get(ResearchWork, work_id)
                require(work is not None, "Fixture work disappeared")
                work.preferred_version_id = version_id
                db.add_all(
                    [
                        ResearchWorkEligibility(
                            run_id=run_id,
                            work_id=work_id,
                            version_id=version_id,
                            protocol_hash=protocol_hash,
                            eligible=True,
                            hard_pass_count=1,
                            hard_fail_count=0,
                            hard_unknown_count=0,
                            evaluated_at=now,
                        ),
                        ResearchRelevanceScore(
                            run_id=run_id,
                            work_id=work_id,
                            lexical_score=score - 0.1,
                            semantic_score=score - 0.05,
                            cross_encoder_score=score,
                            facet_scores_json={"catalog": score},
                            decision="PASS",
                            model_versions_json={"fixture": "zero-llm"},
                            reasons_json=[],
                            facet_judgement_json=None,
                        ),
                    ]
                )
            await db.flush()

            workflow = ResearchWorkflowService(db, ResearchPipelineStages(db).handlers())
            selected = await workflow.execute_stage(run_id, RunState.SELECTING)
            require(selected.state == RunState.RENDERING.value, "Selection did not enter rendering")
            rendered = await workflow.execute_stage(run_id, RunState.RENDERING)
            require(
                rendered.state == RunState.COMPLETED.value,
                "Catalog render did not complete the search-only run",
            )
            artifacts = list(
                (
                    await db.execute(
                        select(ResearchArtifact)
                        .where(ResearchArtifact.run_id == run_id)
                        .order_by(ResearchArtifact.format.asc())
                    )
                ).scalars()
            )
            require(
                {item.format for item in artifacts} == {"markdown", "opml", "bibtex", "csv"},
                "Expected exactly four catalog artifacts",
            )
            markdown = next(item for item in artifacts if item.format == "markdown")
            markdown_bytes = await store.get(markdown.object_key)
            require(
                b"Not performed: PDF acquisition" in markdown_bytes, "Catalog scope notice missing"
            )
            current_run = await db.get(ResearchRun, run_id)
            require(current_run is not None, "Run disappeared")
            selection = current_run.progress_json.get("catalog_selection")
            require(
                isinstance(selection, list) and [item.get("rank") for item in selection] == [1, 2],
                "Catalog rank was not persisted",
            )
            forbidden_row_count = 0
            for model in (
                ResearchFullTextAcquisition,
                ResearchParsingResult,
                ResearchPaperAnalysis,
            ):
                forbidden_row_count += int(
                    await db.scalar(
                        select(func.count()).select_from(model).where(model.run_id == run_id)
                    )
                    or 0
                )
            require(
                forbidden_row_count == 0,
                "Search-only catalog unexpectedly created PDF/analysis rows",
            )
            output = {
                "artifact_formats": sorted(item.format for item in artifacts),
                "catalog_selected_count": len(selection),
                "database": "postgresql",
                "llm_calls": 0,
                "object_store": "minio",
                "pdf_parser_analysis_rows": 0,
                "status": "search_only_catalog_e2e_ok",
            }
        finally:
            await transaction.rollback()
            await remove_prefix(store, prefix)

    require(
        await fixture_root_count(run_id=run_id, project_id=project_id, user_id=user_id) == 0,
        "PostgreSQL rollback left catalog fixture roots",
    )
    require(not await prefix_keys(store, prefix), "MinIO cleanup left catalog objects")
    output["cleanup"] = {"minio_objects": 0, "postgresql_fixture_roots": 0}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
