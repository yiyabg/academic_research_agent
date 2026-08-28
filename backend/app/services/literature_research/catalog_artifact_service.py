"""Immutable metadata-only catalog exports for ``search_only`` research runs."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.literature_research import analysis as analysis_repository
from app.schemas.literature_research.release import (
    ArtifactFormat,
    CatalogResearchReport,
    RenderedArtifact,
)
from app.services.literature_research.exporters import (
    render_catalog_bibtex,
    render_catalog_csv,
    render_catalog_markdown,
    render_catalog_opml,
    validate_artifacts,
)
from app.services.literature_research.object_store import (
    ResearchObjectStore,
    get_research_object_store,
    research_object_prefix,
)

CATALOG_ARTIFACT_FORMATS = {
    ArtifactFormat.MARKDOWN,
    ArtifactFormat.OPML,
    ArtifactFormat.BIBTEX,
    ArtifactFormat.CSV,
}


class CatalogArtifactService:
    """Keep search-only output separate from full-text release-gated artifacts."""

    def __init__(self, db: AsyncSession, object_store: ResearchObjectStore | None = None) -> None:
        self.db = db
        self.object_store = object_store or get_research_object_store()

    async def render_all(
        self,
        report: CatalogResearchReport,
        *,
        organization_id: UUID | None,
        generation: int = 1,
    ) -> list[RenderedArtifact]:
        artifacts = [
            render_catalog_markdown(report),
            render_catalog_opml(report),
            render_catalog_bibtex(report),
            render_catalog_csv(report),
        ]
        errors = validate_artifacts(artifacts)
        if errors:
            raise ValueError(f"Catalog artifact validation failed: {'; '.join(errors)}")
        if {item.format for item in artifacts} != CATALOG_ARTIFACT_FORMATS:
            raise ValueError("Catalog artifact set is incomplete")
        prefix = research_object_prefix(
            organization_id=organization_id,
            project_id=report.project_id,
            run_id=report.run_id,
        )
        for artifact in artifacts:
            key = (
                f"{prefix}/catalog/generation-{generation:04d}/{artifact.format.value}/"
                f"{artifact.sha256}-{artifact.filename}"
            )
            object_key = await self.object_store.put(
                key,
                artifact.data,
                content_type=artifact.content_type,
                metadata={"sha256": artifact.sha256, "scope": "metadata-only"},
            )
            await analysis_repository.persist_artifact(
                self.db,
                run_id=report.run_id,
                artifact=artifact,
                object_key=object_key,
                generation=generation,
            )
        return artifacts

    async def validate_persisted(self, run_id: UUID, *, generation: int = 1) -> list[str]:
        """Verify exactly the four catalog outputs without full-research release checks."""
        rows = await analysis_repository.list_artifacts(
            self.db,
            run_id=run_id,
            generation=generation,
            released_only=False,
        )
        by_format = {row.format: row for row in rows}
        errors = [
            f"missing required catalog artifact: {format_.value}"
            for format_ in CATALOG_ARTIFACT_FORMATS
            if format_.value not in by_format
        ]
        extra_formats = set(by_format) - {format_.value for format_ in CATALOG_ARTIFACT_FORMATS}
        errors.extend(
            f"unexpected catalog artifact: {format_name}" for format_name in extra_formats
        )
        artifacts: list[RenderedArtifact] = []
        for format_ in CATALOG_ARTIFACT_FORMATS:
            row = by_format.get(format_.value)
            if row is None:
                continue
            try:
                payload = await self.object_store.get(row.object_key)
            except Exception as exc:
                errors.append(f"{row.filename}: unreadable object ({type(exc).__name__})")
                continue
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row.sha256:
                errors.append(f"{row.filename}: persisted hash mismatch")
            if len(payload) != row.size_bytes:
                errors.append(f"{row.filename}: persisted size mismatch")
            artifacts.append(
                RenderedArtifact(
                    format=format_,
                    filename=row.filename,
                    content_type=row.content_type,
                    data=payload,
                    sha256=digest,
                )
            )
        errors.extend(validate_artifacts(artifacts))
        return sorted(set(errors))
