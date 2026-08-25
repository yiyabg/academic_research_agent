"""Render, validate, hash, persist, and re-audit research artifacts."""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.literature_research import analysis as analysis_repository
from app.repositories.literature_research import discovery as discovery_repository
from app.repositories.literature_research import quality as quality_repository
from app.schemas.literature_research.release import (
    ArtifactFormat,
    CanonicalResearchReport,
    ExclusionAuditRow,
    MetricSnapshotAuditRow,
    RenderedArtifact,
    RunManifest,
)
from app.services.literature_research.audit_exports import (
    collect_exclusion_audit_rows,
    collect_metric_snapshot_audit_rows,
)
from app.services.literature_research.exporters import (
    render_bibtex,
    render_csv,
    render_exclusions_csv,
    render_jsonl,
    render_manifest,
    render_markdown,
    render_opml,
    render_venue_metrics_csv,
    validate_artifacts,
)
from app.services.literature_research.object_store import (
    ResearchObjectStore,
    get_research_object_store,
    research_object_prefix,
)

TEMPLATE_COMMIT = "3428d9a6214619d3514312886d59a36400747b7d"


class ArtifactService:
    def __init__(self, db: AsyncSession, object_store: ResearchObjectStore | None = None) -> None:
        self.db = db
        self.object_store = object_store or get_research_object_store()

    async def render_all(
        self,
        report: CanonicalResearchReport,
        *,
        organization_id: UUID | None,
        source_snapshot_hashes: list[str],
        metric_snapshot_ids: list[UUID],
        exclusion_rows: list[ExclusionAuditRow],
        metric_snapshot_rows: list[MetricSnapshotAuditRow],
        model_versions: dict[str, str] | None = None,
        llm_usage: dict[str, Any] | None = None,
        generation: int = 1,
    ) -> list[RenderedArtifact]:
        artifacts = [
            render_markdown(report),
            render_opml(report),
            render_bibtex(report),
            render_jsonl(report),
            render_csv(report),
            render_exclusions_csv(exclusion_rows),
            render_venue_metrics_csv(metric_snapshot_rows),
        ]
        errors = validate_artifacts(artifacts)
        if errors:
            raise ValueError(f"Artifact validation failed: {'; '.join(errors)}")
        manifest = RunManifest(
            run_id=report.run_id,
            generation=generation,
            project_id=report.project_id,
            protocol_hash=report.protocol_hash,
            template_commit=TEMPLATE_COMMIT,
            model_versions=model_versions or {},
            llm_usage=llm_usage or {},
            source_snapshot_hashes=sorted(set(source_snapshot_hashes)),
            metric_snapshot_ids=sorted(set(metric_snapshot_ids), key=str),
            artifact_hashes={item.format: item.sha256 for item in artifacts},
            target_count=report.target_count,
            strict_count=report.strict_count,
            shortfall_disclosed=report.shortfall_disclosed,
        )
        artifacts.append(render_manifest(manifest))
        prefix = research_object_prefix(
            organization_id=organization_id,
            project_id=report.project_id,
            run_id=report.run_id,
        )
        for artifact in artifacts:
            key = (
                f"{prefix}/artifacts/generation-{generation:04d}/{artifact.format.value}/"
                f"{artifact.sha256}-{artifact.filename}"
            )
            object_key = await self.object_store.put(
                key,
                artifact.data,
                content_type=artifact.content_type,
                metadata={"sha256": artifact.sha256},
            )
            await analysis_repository.persist_artifact(
                self.db,
                run_id=report.run_id,
                artifact=artifact,
                object_key=object_key,
                generation=generation,
            )
        return artifacts

    async def validate_persisted(self, run_id: UUID, *, generation: int | None = None) -> list[str]:
        """Re-read every required object and verify bytes, metadata, and manifest."""
        rows = await analysis_repository.list_artifacts(
            self.db,
            run_id=run_id,
            generation=generation,
            released_only=False,
        )
        by_format = {row.format: row for row in rows}
        errors = [
            f"missing required artifact: {format_.value}"
            for format_ in ArtifactFormat
            if format_.value not in by_format
        ]
        artifacts: list[RenderedArtifact] = []
        payloads: dict[str, bytes] = {}
        for format_name, row in by_format.items():
            try:
                payload = await self.object_store.get(row.object_key)
                format_ = ArtifactFormat(format_name)
            except Exception as exc:
                errors.append(f"{row.filename}: unreadable object ({type(exc).__name__})")
                continue
            payloads[format_name] = payload
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
        manifest_payload = payloads.get(ArtifactFormat.MANIFEST.value)
        if manifest_payload is not None:
            try:
                manifest = RunManifest.model_validate(json.loads(manifest_payload))
                for format_, expected_hash in manifest.artifact_hashes.items():
                    row = by_format.get(format_.value)
                    if row is None or row.sha256 != expected_hash:
                        errors.append(f"manifest hash mismatch: {format_.value}")
                expected_source_hashes = (
                    await discovery_repository.list_source_snapshot_hashes(
                        self.db, run_id=run_id
                    )
                )
                if manifest.source_snapshot_hashes != sorted(
                    set(expected_source_hashes)
                ):
                    errors.append("manifest source snapshot provenance mismatch")
                expected_metric_ids = await quality_repository.list_used_metric_snapshot_ids(
                    self.db, run_id=run_id
                )
                if manifest.metric_snapshot_ids != sorted(
                    set(expected_metric_ids), key=str
                ):
                    errors.append("manifest metric snapshot provenance mismatch")
            except Exception as exc:
                errors.append(f"run_manifest.json: invalid manifest ({type(exc).__name__})")
        papers_payload = payloads.get(ArtifactFormat.JSONL.value)
        if papers_payload is not None:
            try:
                included_work_ids = {
                    UUID(str(json.loads(line)["work_id"]))
                    for line in papers_payload.decode("utf-8").splitlines()
                    if line
                }
                expected_exclusions = render_exclusions_csv(
                    await collect_exclusion_audit_rows(
                        self.db,
                        run_id=run_id,
                        included_work_ids=included_work_ids,
                    )
                ).data
                if payloads.get(ArtifactFormat.EXCLUSIONS_CSV.value) != expected_exclusions:
                    errors.append("exclusions.csv: authoritative ledger mismatch")
                expected_metrics = render_venue_metrics_csv(
                    await collect_metric_snapshot_audit_rows(self.db, run_id=run_id)
                ).data
                if payloads.get(ArtifactFormat.VENUE_METRICS_CSV.value) != expected_metrics:
                    errors.append(
                        "venue_metrics_snapshot.csv: authoritative ledger mismatch"
                    )
            except Exception as exc:
                errors.append(
                    "audit CSVs: could not verify authoritative ledgers "
                    f"({type(exc).__name__})"
                )
        return sorted(set(errors))
