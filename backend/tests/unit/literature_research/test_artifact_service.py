"""Artifact object storage, persistence, and release audit tests."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.literature_research import analysis as analysis_repository
from app.schemas.literature_research.analysis import SynthesisOutput
from app.schemas.literature_research.release import (
    ArtifactFormat,
    CanonicalResearchReport,
    ExclusionAuditRow,
)
from app.services.literature_research.artifact_service import ArtifactService


@pytest.mark.anyio
async def test_render_all_stores_eight_hashed_artifacts_and_manifest() -> None:
    metric_snapshot_id = uuid4()
    store = AsyncMock()
    store.put.side_effect = lambda key, *_args, **_kwargs: key
    report = CanonicalResearchReport(
        run_id=uuid4(),
        project_id=uuid4(),
        protocol_hash="sha256:" + "a" * 64,
        title="Empty strict result",
        target_count=20,
        strict_count=0,
        shortfall_disclosed=True,
        synthesis=SynthesisOutput(
            overview="No papers passed the immutable constraints.",
            themes=[],
            included_work_ids=[],
        ),
        papers=[],
    )
    with patch(
        "app.services.literature_research.artifact_service.analysis_repository.persist_artifact",
        new=AsyncMock(),
    ) as persist:
        artifacts = await ArtifactService(AsyncMock(), store).render_all(
            report,
            organization_id=uuid4(),
            source_snapshot_hashes=["b" * 64, "a" * 64, "b" * 64],
            metric_snapshot_ids=[metric_snapshot_id, metric_snapshot_id],
            exclusion_rows=[],
            metric_snapshot_rows=[],
            model_versions={"synthesis": "fixture-v1"},
        )

    assert {item.format for item in artifacts} == set(ArtifactFormat)
    assert persist.await_count == 8
    assert store.put.await_count == 8
    manifest = next(item for item in artifacts if item.format == ArtifactFormat.MANIFEST)
    payload = json.loads(manifest.data)
    assert payload["template_commit"]
    assert payload["source_snapshot_hashes"] == ["a" * 64, "b" * 64]
    assert payload["metric_snapshot_ids"] == [str(metric_snapshot_id)]


@pytest.mark.anyio
async def test_persisted_artifact_audit_rereads_bytes_and_manifest() -> None:
    report = CanonicalResearchReport(
        run_id=uuid4(),
        project_id=uuid4(),
        protocol_hash="sha256:" + "c" * 64,
        title="Audited output",
        target_count=3,
        strict_count=0,
        shortfall_disclosed=True,
        synthesis=SynthesisOutput(overview="No strict papers.", themes=[], included_work_ids=[]),
        papers=[],
    )
    stored: dict[str, bytes] = {}
    store = AsyncMock()

    async def put(key, data, **_kwargs):
        stored[key] = data
        return key

    async def get(key):
        return stored[key]

    store.put.side_effect = put
    store.get.side_effect = get
    persisted = []

    async def capture(_db, *, run_id, artifact, object_key, generation):
        persisted.append(
            SimpleNamespace(
                run_id=run_id,
                generation=generation,
                format=artifact.format.value,
                filename=artifact.filename,
                content_type=artifact.content_type,
                object_key=object_key,
                sha256=artifact.sha256,
                size_bytes=len(artifact.data),
            )
        )

    service = ArtifactService(AsyncMock(), store)
    with patch(
        "app.services.literature_research.artifact_service.analysis_repository.persist_artifact",
        new=capture,
    ):
        await service.render_all(
            report,
            organization_id=None,
            source_snapshot_hashes=[],
            metric_snapshot_ids=[],
            exclusion_rows=[],
            metric_snapshot_rows=[],
        )
    with (
        patch(
            "app.services.literature_research.artifact_service.analysis_repository.list_artifacts",
            new=AsyncMock(return_value=persisted),
        ),
        patch(
            "app.services.literature_research.artifact_service.discovery_repository.list_source_snapshot_hashes",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.literature_research.artifact_service.quality_repository.list_used_metric_snapshot_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.literature_research.artifact_service.collect_exclusion_audit_rows",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.literature_research.artifact_service.collect_metric_snapshot_audit_rows",
            new=AsyncMock(return_value=[]),
        ),
    ):
        assert await service.validate_persisted(report.run_id) == []

    with (
        patch(
            "app.services.literature_research.artifact_service.analysis_repository.list_artifacts",
            new=AsyncMock(return_value=persisted),
        ),
        patch(
            "app.services.literature_research.artifact_service.discovery_repository.list_source_snapshot_hashes",
            new=AsyncMock(return_value=["d" * 64]),
        ),
        patch(
            "app.services.literature_research.artifact_service.quality_repository.list_used_metric_snapshot_ids",
            new=AsyncMock(return_value=[uuid4()]),
        ),
        patch(
            "app.services.literature_research.artifact_service.collect_exclusion_audit_rows",
            new=AsyncMock(
                return_value=[
                    ExclusionAuditRow(
                        work_id=uuid4(),
                        title="Authoritative exclusion added after bad export",
                        document_type="journal_article",
                        reason_codes=["VALUE_MISSING"],
                    )
                ]
            ),
        ),
        patch(
            "app.services.literature_research.artifact_service.collect_metric_snapshot_audit_rows",
            new=AsyncMock(return_value=[]),
        ),
    ):
        errors = await service.validate_persisted(report.run_id)
    assert "manifest source snapshot provenance mismatch" in errors
    assert "manifest metric snapshot provenance mismatch" in errors
    assert "exclusions.csv: authoritative ledger mismatch" in errors


@pytest.mark.anyio
async def test_persisted_artifact_audit_detects_tampering_and_missing_format() -> None:
    key = "runs/bad/report.md"
    rows = [
        SimpleNamespace(
            format=ArtifactFormat.MARKDOWN.value,
            filename="research_report.md",
            content_type="text/markdown",
            object_key=key,
            sha256="0" * 64,
            size_bytes=1,
        )
    ]
    store = AsyncMock()
    store.get.return_value = b"tampered"
    service = ArtifactService(AsyncMock(), store)
    with patch(
        "app.services.literature_research.artifact_service.analysis_repository.list_artifacts",
        new=AsyncMock(return_value=rows),
    ):
        errors = await service.validate_persisted(uuid4())

    assert "research_report.md: persisted hash mismatch" in errors
    assert "research_report.md: persisted size mismatch" in errors
    assert "missing required artifact: manifest" in errors


@pytest.mark.anyio
async def test_unreleased_artifact_listing_selects_latest_generation() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = 2
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result

    await analysis_repository.list_artifacts(
        db,
        run_id=uuid4(),
        generation=None,
        released_only=False,
    )

    generation_sql = str(db.scalar.await_args.args[0])
    assert "research_release_checks" not in generation_sql
