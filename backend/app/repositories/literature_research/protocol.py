"""Data access for immutable research protocol versions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.protocol import ResearchProtocolVersion


async def get_by_version(
    db: AsyncSession, project_id: UUID, version: int
) -> ResearchProtocolVersion | None:
    result = await db.execute(
        select(ResearchProtocolVersion).where(
            ResearchProtocolVersion.project_id == project_id,
            ResearchProtocolVersion.version == version,
        )
    )
    return result.scalar_one_or_none()


async def get_by_hash(
    db: AsyncSession, project_id: UUID, protocol_hash: str
) -> ResearchProtocolVersion | None:
    result = await db.execute(
        select(ResearchProtocolVersion).where(
            ResearchProtocolVersion.project_id == project_id,
            ResearchProtocolVersion.protocol_hash == protocol_hash,
        )
    )
    return result.scalar_one_or_none()


async def next_version(db: AsyncSession, project_id: UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(ResearchProtocolVersion.version), 0)).where(
            ResearchProtocolVersion.project_id == project_id
        )
    )
    return int(result.scalar_one()) + 1


async def create(
    db: AsyncSession,
    *,
    project_id: UUID,
    version: int,
    protocol_json: dict[str, object],
    protocol_hash: str,
) -> ResearchProtocolVersion:
    protocol = ResearchProtocolVersion(
        project_id=project_id,
        version=version,
        protocol_json=protocol_json,
        protocol_hash=protocol_hash,
    )
    db.add(protocol)
    await db.flush()
    await db.refresh(protocol)
    return protocol


async def approve(
    db: AsyncSession,
    *,
    protocol: ResearchProtocolVersion,
    approved_by: UUID,
    approved_at: datetime,
) -> ResearchProtocolVersion:
    protocol.status = "APPROVED"
    protocol.approved_by = approved_by
    protocol.approved_at = approved_at
    db.add(protocol)
    await db.flush()
    await db.refresh(protocol)
    return protocol


async def list_for_project(db: AsyncSession, project_id: UUID) -> list[ResearchProtocolVersion]:
    result = await db.execute(
        select(ResearchProtocolVersion)
        .where(ResearchProtocolVersion.project_id == project_id)
        .order_by(ResearchProtocolVersion.version.desc())
    )
    return list(result.scalars().all())


async def get_latest_approved(
    db: AsyncSession, project_id: UUID
) -> ResearchProtocolVersion | None:
    return await db.scalar(
        select(ResearchProtocolVersion)
        .where(
            ResearchProtocolVersion.project_id == project_id,
            ResearchProtocolVersion.status == "APPROVED",
        )
        .order_by(ResearchProtocolVersion.version.desc())
        .limit(1)
    )
