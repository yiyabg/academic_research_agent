"""Persistence for relevance, lawful acquisition, parsed blocks, and evidence."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.discovery import (
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.evidence import (
    ResearchEvidenceLocator,
    ResearchFigureArtifact,
    ResearchFullTextAcquisition,
    ResearchParsedBlock,
    ResearchParsingResult,
    ResearchRelevanceScore,
)
from app.db.models.literature_research.quality import ResearchWorkEligibility
from app.schemas.literature_research.analysis import FigureArtifact
from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    EvidenceLocator,
    FullTextAcquisitionDecision,
    ParsedBlock,
    ParsingQuality,
    RelevanceScore,
)


async def persist_relevance(
    db: AsyncSession, *, run_id: UUID, scores: list[RelevanceScore]
) -> None:
    for score in scores:
        existing = await db.scalar(
            select(ResearchRelevanceScore).where(
                ResearchRelevanceScore.run_id == run_id,
                ResearchRelevanceScore.work_id == score.work_id,
            )
        )
        if existing is not None:
            continue
        db.add(
            ResearchRelevanceScore(
                run_id=run_id,
                work_id=score.work_id,
                lexical_score=score.lexical_score,
                semantic_score=score.semantic_score,
                cross_encoder_score=score.cross_encoder_score,
                facet_scores_json=score.facet_scores,
                decision=score.decision.value,
                model_versions_json=score.model_versions,
                reasons_json=score.reasons,
                facet_judgement_json=score.facet_judgement,
            )
        )
    await db.flush()


async def persist_acquisition(
    db: AsyncSession,
    *,
    run_id: UUID,
    decision: FullTextAcquisitionDecision,
    acquired: AcquiredFullText | None,
    scan_status: str | None = None,
    scan_engine: str | None = None,
    scan_signature: str | None = None,
    resolved_ips: list[str] | None = None,
    redirect_chain: list[str] | None = None,
) -> ResearchFullTextAcquisition:
    selected = decision.selected or (decision.rejected[0] if decision.rejected else None)
    row = ResearchFullTextAcquisition(
        run_id=run_id,
        version_id=decision.version_id,
        source=selected.source.value if selected else "none",
        url=str(selected.url) if selected else "",
        license_decision=(selected.license_decision.value if selected else "UNKNOWN"),
        license_reference=selected.license_reference if selected else None,
        content_type=(
            acquired.content_type if acquired else selected.content_type if selected else None
        ),
        allowed=decision.allowed,
        reason_code=decision.reason_code,
        object_key=acquired.object_key if acquired else None,
        document_sha256=acquired.document_sha256 if acquired else None,
        acquired_at=datetime.now(UTC) if acquired else None,
        resolved_ips_json=acquired.resolved_ips if acquired else resolved_ips or [],
        redirect_chain_json=acquired.redirect_chain if acquired else redirect_chain or [],
        malware_scan_status=(
            acquired.malware_scan_status
            if acquired
            else scan_status
            if scan_status
            else "NOT_SCANNED"
        ),
        malware_scan_engine=(acquired.malware_scan_engine if acquired else scan_engine),
        malware_signature=(acquired.malware_signature if acquired else scan_signature),
        malware_scanned_at=(datetime.now(UTC) if acquired or scan_status else None),
    )
    db.add(row)
    await db.flush()
    return row


async def persist_blocks(
    db: AsyncSession, *, run_id: UUID, version_id: UUID, blocks: list[ParsedBlock]
) -> None:
    for block in blocks:
        existing = await db.scalar(
            select(ResearchParsedBlock.id).where(
                ResearchParsedBlock.version_id == version_id,
                ResearchParsedBlock.block_id == block.block_id,
            )
        )
        if existing is not None:
            continue
        db.add(
            ResearchParsedBlock(
                run_id=run_id,
                version_id=version_id,
                block_id=block.block_id,
                page_number=block.page_number,
                section_path_json=block.section_path,
                text=block.text,
                char_start=block.char_start,
                char_end=block.char_end,
                text_sha256=block.text_sha256,
                bbox_json=list(block.bbox) if block.bbox else None,
                extraction_method=block.extraction_method,
            )
        )
    await db.flush()


async def persist_parsing_result(
    db: AsyncSession,
    *,
    run_id: UUID,
    version_id: UUID,
    quality: ParsingQuality,
) -> ResearchParsingResult:
    existing = await db.scalar(
        select(ResearchParsingResult).where(
            ResearchParsingResult.run_id == run_id,
            ResearchParsingResult.version_id == version_id,
        )
    )
    if existing is not None:
        return existing
    row = ResearchParsingResult(
        run_id=run_id,
        version_id=version_id,
        status=quality.status,
        page_count=quality.page_count,
        parsed_page_count=quality.parsed_page_count,
        text_coverage=quality.text_coverage,
        page_count_match=quality.page_count_match,
        section_detection_f1_estimate=quality.section_detection_f1_estimate,
        table_count=quality.table_count,
        figure_count=quality.figure_count,
        caption_count=quality.caption_count,
        caption_link_rate=quality.caption_link_rate,
        ocr_page_count=quality.ocr_page_count,
        ocr_page_ratio=quality.ocr_page_ratio,
        total_characters=quality.total_characters,
        parser_versions_json=quality.parser_versions,
        error_codes_json=quality.error_codes,
        blocks_object_key=quality.blocks_object_key,
    )
    db.add(row)
    await db.flush()
    return row


async def persist_figure_artifacts(
    db: AsyncSession,
    *,
    run_id: UUID,
    work_id: UUID,
    version_id: UUID,
    artifacts: list[FigureArtifact],
) -> None:
    for artifact in artifacts:
        existing = await db.scalar(
            select(ResearchFigureArtifact.id).where(
                ResearchFigureArtifact.run_id == run_id,
                ResearchFigureArtifact.figure_id == artifact.figure_id,
            )
        )
        if existing is not None:
            continue
        if (
            artifact.page_number is None
            or artifact.bbox is None
            or artifact.image_object_key is None
            or artifact.image_sha256 is None
        ):
            raise ValueError("Only fully located figure artifacts may be persisted")
        db.add(
            ResearchFigureArtifact(
                figure_id=artifact.figure_id,
                run_id=run_id,
                work_id=work_id,
                version_id=version_id,
                artifact_kind=artifact.artifact_kind,
                label=artifact.label,
                caption=artifact.caption,
                page_number=artifact.page_number,
                bbox_json=list(artifact.bbox),
                image_object_key=artifact.image_object_key,
                image_sha256=artifact.image_sha256,
                document_sha256=artifact.document_sha256,
                evidence_ids_json=artifact.evidence_ids,
                table_cells_json=artifact.table_cells,
                exact_numeric_values_json=artifact.exact_numeric_values,
                extraction_status=artifact.extraction_status,
                license_scope=artifact.license_scope,
            )
        )
    await db.flush()


async def persist_evidence(
    db: AsyncSession, *, run_id: UUID, locator: EvidenceLocator
) -> ResearchEvidenceLocator:
    existing = await db.scalar(
        select(ResearchEvidenceLocator).where(
            ResearchEvidenceLocator.run_id == run_id,
            ResearchEvidenceLocator.evidence_id == locator.evidence_id,
        )
    )
    if existing is not None:
        return existing
    row = ResearchEvidenceLocator(
        evidence_id=locator.evidence_id,
        run_id=run_id,
        work_id=locator.work_id,
        version_id=locator.version_id,
        block_id=locator.block_id,
        page_number=locator.page_number,
        section_path_json=locator.section_path,
        quote=locator.quote,
        quote_start=locator.quote_start,
        quote_end=locator.quote_end,
        block_text_sha256=locator.block_text_sha256,
        document_sha256=locator.document_sha256,
        bbox_json=list(locator.bbox) if locator.bbox else None,
        extraction_method=locator.extraction_method,
    )
    db.add(row)
    await db.flush()
    return row


async def list_evidence(
    db: AsyncSession, *, run_id: UUID, work_id: UUID | None = None
) -> list[ResearchEvidenceLocator]:
    query = select(ResearchEvidenceLocator).where(ResearchEvidenceLocator.run_id == run_id)
    if work_id is not None:
        query = query.where(ResearchEvidenceLocator.work_id == work_id)
    result = await db.execute(query.order_by(ResearchEvidenceLocator.created_at.asc()))
    return list(result.scalars().all())


async def list_eligible_work_documents(
    db: AsyncSession, *, run_id: UUID
) -> list[tuple[ResearchWork, ResearchWorkVersion, ResearchVenue | None]]:
    result = await db.execute(
        select(ResearchWork, ResearchWorkVersion, ResearchVenue)
        .join(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.work_id == ResearchWork.id)
            & (ResearchWorkEligibility.run_id == run_id)
            & ResearchWorkEligibility.eligible.is_(True),
        )
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .where(ResearchWork.run_id == run_id)
        .order_by(ResearchWork.id.asc())
    )
    return [(work, version, venue) for work, version, venue in result.all()]


async def list_relevant_versions(
    db: AsyncSession, *, run_id: UUID
) -> list[tuple[ResearchWork, ResearchWorkVersion]]:
    result = await db.execute(
        select(ResearchWork, ResearchWorkVersion)
        .join(
            ResearchRelevanceScore,
            (ResearchRelevanceScore.work_id == ResearchWork.id)
            & (ResearchRelevanceScore.run_id == run_id)
            & (ResearchRelevanceScore.decision == "PASS"),
        )
        .join(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.work_id == ResearchWork.id)
            & (ResearchWorkEligibility.run_id == run_id)
            & ResearchWorkEligibility.eligible.is_(True),
        )
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .where(ResearchWork.run_id == run_id)
    )
    return [(work, version) for work, version in result.all()]


async def list_analysis_ready_versions(
    db: AsyncSession, *, run_id: UUID
) -> list[tuple[ResearchWork, ResearchWorkVersion]]:
    """Return strict-pass papers backed by scanned and parsed full text."""
    result = await db.execute(
        select(ResearchWork, ResearchWorkVersion)
        .join(
            ResearchRelevanceScore,
            (ResearchRelevanceScore.work_id == ResearchWork.id)
            & (ResearchRelevanceScore.run_id == run_id)
            & (ResearchRelevanceScore.decision == "PASS"),
        )
        .join(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.work_id == ResearchWork.id)
            & (ResearchWorkEligibility.run_id == run_id)
            & ResearchWorkEligibility.eligible.is_(True),
        )
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .join(
            ResearchFullTextAcquisition,
            (ResearchFullTextAcquisition.version_id == ResearchWorkVersion.id)
            & (ResearchFullTextAcquisition.run_id == run_id)
            & ResearchFullTextAcquisition.allowed.is_(True)
            & (ResearchFullTextAcquisition.malware_scan_status == "CLEAN")
            & ResearchFullTextAcquisition.object_key.is_not(None),
        )
        .join(
            ResearchParsingResult,
            (ResearchParsingResult.version_id == ResearchWorkVersion.id)
            & (ResearchParsingResult.run_id == run_id)
            & (ResearchParsingResult.status == "PASSED"),
        )
        .where(ResearchWork.run_id == run_id)
        .order_by(
            ResearchRelevanceScore.cross_encoder_score.desc().nullslast(),
            ResearchRelevanceScore.semantic_score.desc().nullslast(),
            ResearchRelevanceScore.lexical_score.desc(),
            ResearchWork.id.asc(),
        )
    )
    return [(work, version) for work, version in result.all()]


async def list_relevant_report_rows(
    db: AsyncSession, *, run_id: UUID
) -> list[
    tuple[
        ResearchWork,
        ResearchWorkVersion,
        ResearchVenue | None,
        ResearchRelevanceScore,
    ]
]:
    result = await db.execute(
        select(
            ResearchWork,
            ResearchWorkVersion,
            ResearchVenue,
            ResearchRelevanceScore,
        )
        .join(
            ResearchRelevanceScore,
            (ResearchRelevanceScore.work_id == ResearchWork.id)
            & (ResearchRelevanceScore.run_id == run_id)
            & (ResearchRelevanceScore.decision == "PASS"),
        )
        .join(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.work_id == ResearchWork.id)
            & (ResearchWorkEligibility.run_id == run_id)
            & ResearchWorkEligibility.eligible.is_(True),
        )
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .join(
            ResearchFullTextAcquisition,
            (ResearchFullTextAcquisition.version_id == ResearchWorkVersion.id)
            & (ResearchFullTextAcquisition.run_id == run_id)
            & ResearchFullTextAcquisition.allowed.is_(True)
            & (ResearchFullTextAcquisition.malware_scan_status == "CLEAN")
            & ResearchFullTextAcquisition.object_key.is_not(None),
        )
        .join(
            ResearchParsingResult,
            (ResearchParsingResult.version_id == ResearchWorkVersion.id)
            & (ResearchParsingResult.run_id == run_id)
            & (ResearchParsingResult.status == "PASSED"),
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .where(ResearchWork.run_id == run_id)
        .order_by(ResearchRelevanceScore.cross_encoder_score.desc().nullslast())
    )
    return [tuple(row) for row in result.all()]


async def list_catalog_report_rows(
    db: AsyncSession, *, run_id: UUID
) -> list[
    tuple[
        ResearchWork,
        ResearchWorkVersion,
        ResearchVenue | None,
        ResearchRelevanceScore,
    ]
]:
    """Return strict metadata-only candidates without requiring a PDF or parse result."""
    result = await db.execute(
        select(
            ResearchWork,
            ResearchWorkVersion,
            ResearchVenue,
            ResearchRelevanceScore,
        )
        .join(
            ResearchRelevanceScore,
            (ResearchRelevanceScore.work_id == ResearchWork.id)
            & (ResearchRelevanceScore.run_id == run_id)
            & (ResearchRelevanceScore.decision == "PASS"),
        )
        .join(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.work_id == ResearchWork.id)
            & (ResearchWorkEligibility.run_id == run_id)
            & ResearchWorkEligibility.eligible.is_(True),
        )
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .where(ResearchWork.run_id == run_id)
        .order_by(
            ResearchRelevanceScore.cross_encoder_score.desc().nullslast(),
            ResearchRelevanceScore.semantic_score.desc().nullslast(),
            ResearchRelevanceScore.lexical_score.desc(),
            ResearchWork.id.asc(),
        )
    )
    return [tuple(row) for row in result.all()]


async def list_acquired_fulltexts(
    db: AsyncSession, *, run_id: UUID
) -> list[tuple[ResearchFullTextAcquisition, ResearchWork]]:
    result = await db.execute(
        select(ResearchFullTextAcquisition, ResearchWork)
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchFullTextAcquisition.version_id,
        )
        .join(ResearchWork, ResearchWork.id == ResearchWorkVersion.work_id)
        .where(
            ResearchFullTextAcquisition.run_id == run_id,
            ResearchFullTextAcquisition.allowed.is_(True),
            ResearchFullTextAcquisition.object_key.is_not(None),
            ResearchFullTextAcquisition.malware_scan_status == "CLEAN",
        )
    )
    return [(acquisition, work) for acquisition, work in result.all()]


async def get_clean_acquisition(
    db: AsyncSession, *, run_id: UUID, version_id: UUID
) -> ResearchFullTextAcquisition | None:
    return await db.scalar(
        select(ResearchFullTextAcquisition)
        .where(
            ResearchFullTextAcquisition.run_id == run_id,
            ResearchFullTextAcquisition.version_id == version_id,
            ResearchFullTextAcquisition.allowed.is_(True),
            ResearchFullTextAcquisition.object_key.is_not(None),
            ResearchFullTextAcquisition.malware_scan_status == "CLEAN",
        )
        .order_by(ResearchFullTextAcquisition.acquired_at.desc().nullslast())
        .limit(1)
    )


async def count_unsafe_or_unscanned_fulltexts(
    db: AsyncSession, *, run_id: UUID, work_ids: set[UUID]
) -> int:
    """Count unsafe documents only among papers included in the final report."""
    if not work_ids:
        return 0
    value = await db.scalar(
        select(func.count(ResearchFullTextAcquisition.id))
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchFullTextAcquisition.version_id,
        )
        .where(
            ResearchFullTextAcquisition.run_id == run_id,
            ResearchWorkVersion.work_id.in_(work_ids),
            ResearchFullTextAcquisition.allowed.is_(True),
            ResearchFullTextAcquisition.object_key.is_not(None),
            ResearchFullTextAcquisition.malware_scan_status != "CLEAN",
        )
    )
    return int(value or 0)


async def count_incomplete_figure_artifacts(
    db: AsyncSession, *, run_id: UUID, work_ids: set[UUID]
) -> int:
    """Require a verified, hash-bound figure/table for each selected paper with captions.

    The research plan prioritizes core figures and explicitly does not require mechanical
    analysis of every decorative figure.  Release checks are therefore scoped to papers
    actually included in the report and require at least one verified artifact whenever
    the parser detected one or more captions.
    """
    if not work_ids:
        return 0
    parsing_rows = await db.execute(
        select(ResearchParsingResult.version_id)
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchParsingResult.version_id,
        )
        .where(
            ResearchParsingResult.run_id == run_id,
            ResearchWorkVersion.work_id.in_(work_ids),
            ResearchParsingResult.status == "PASSED",
            ResearchParsingResult.caption_count > 0,
        )
    )
    missing = 0
    for (version_id,) in parsing_rows.all():
        verified = await db.scalar(
            select(func.count(ResearchFigureArtifact.id)).where(
                ResearchFigureArtifact.run_id == run_id,
                ResearchFigureArtifact.version_id == version_id,
                ResearchFigureArtifact.extraction_status == "VERIFIED",
            )
        )
        missing += int(int(verified or 0) == 0)
    return missing
