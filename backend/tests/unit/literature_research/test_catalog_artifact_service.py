"""Metadata-only catalog export tests; no PDF, parser, or LLM is involved."""

import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.routes.v1.literature_research.artifacts import _catalog_artifacts_are_downloadable
from app.schemas.literature_research.release import (
    ArtifactFormat,
    CatalogPaper,
    CatalogResearchReport,
)
from app.services.literature_research.catalog_artifact_service import CatalogArtifactService


def catalog_report() -> CatalogResearchReport:
    return CatalogResearchReport(
        run_id=uuid4(),
        project_id=uuid4(),
        protocol_hash="sha256:" + "a" * 64,
        title="Metadata-only literature catalog",
        target_count=20,
        strict_count=1,
        shortfall_disclosed=True,
        papers=[
            CatalogPaper(
                work_id=uuid4(),
                version_id=uuid4(),
                rank=1,
                title="A <strict> metadata result",
                authors=["Ada Lovelace"],
                year=2025,
                doi="10.9999/catalog",
                source_url="https://doi.org/10.9999/catalog",
                document_type="journal_article",
                venue="Catalog Journal",
                relevance_score=0.91,
            )
        ],
    )


@pytest.mark.anyio
async def test_catalog_service_stores_exactly_four_metadata_artifacts() -> None:
    report = catalog_report()
    stored: dict[str, bytes] = {}
    persisted = []
    store = AsyncMock()

    async def put(key, data, **_kwargs):
        stored[key] = data
        return key

    async def get(key):
        return stored[key]

    store.put.side_effect = put
    store.get.side_effect = get

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

    service = CatalogArtifactService(AsyncMock(), store)
    with patch(
        "app.services.literature_research.catalog_artifact_service.analysis_repository.persist_artifact",
        new=capture,
    ):
        artifacts = await service.render_all(report, organization_id=None)

    assert {item.format for item in artifacts} == {
        ArtifactFormat.MARKDOWN,
        ArtifactFormat.OPML,
        ArtifactFormat.BIBTEX,
        ArtifactFormat.CSV,
    }
    assert len(persisted) == 4
    assert all("/catalog/" in item.object_key for item in persisted)
    markdown = next(item.data for item in artifacts if item.format == ArtifactFormat.MARKDOWN)
    assert b"Not performed: PDF acquisition" in markdown
    opml = next(item.data for item in artifacts if item.format == ArtifactFormat.OPML)
    ET.fromstring(opml)

    with patch(
        "app.services.literature_research.catalog_artifact_service.analysis_repository.list_artifacts",
        new=AsyncMock(return_value=persisted),
    ):
        assert await service.validate_persisted(report.run_id) == []

    stored[persisted[0].object_key] = b"tampered"
    with patch(
        "app.services.literature_research.catalog_artifact_service.analysis_repository.list_artifacts",
        new=AsyncMock(return_value=persisted),
    ):
        errors = await service.validate_persisted(report.run_id)
    assert f"{persisted[0].filename}: persisted hash mismatch" in errors


def test_only_terminal_search_only_runs_bypass_full_research_release_gate() -> None:
    assert _catalog_artifacts_are_downloadable(
        SimpleNamespace(execution_mode="search_only", state="PARTIALLY_COMPLETED")
    )
    assert not _catalog_artifacts_are_downloadable(
        SimpleNamespace(execution_mode="search_only", state="RENDERING")
    )
    assert not _catalog_artifacts_are_downloadable(
        SimpleNamespace(execution_mode="full_research", state="COMPLETED")
    )
