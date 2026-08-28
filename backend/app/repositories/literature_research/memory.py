"""Persistence for explicit research memories, profiles, policies, and feedback."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.memory import (
    ResearchFeedbackSample,
    ResearchPolicyVersion,
    ResearchProjectMemory,
    UserResearchProfile,
)
from app.schemas.literature_research.memory import (
    PolicyVersionCreate,
    ProjectMemoryCreate,
    ResearchFeedbackCreate,
    ResearchProfileConfirm,
)


async def create_project_memory(
    db: AsyncSession,
    *,
    project_id: UUID,
    created_by: UUID,
    body: ProjectMemoryCreate,
) -> ResearchProjectMemory:
    row = ResearchProjectMemory(
        project_id=project_id,
        memory_type=body.memory_type.value,
        content_json=body.content,
        source=body.source.value,
        source_id=body.source_id,
        confidence=body.confidence,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        supersedes=body.supersedes,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def list_project_memories(
    db: AsyncSession, *, project_id: UUID
) -> list[ResearchProjectMemory]:
    now = datetime.now(UTC)
    result = await db.execute(
        select(ResearchProjectMemory)
        .where(
            ResearchProjectMemory.project_id == project_id,
            ResearchProjectMemory.valid_from <= now,
            (ResearchProjectMemory.valid_to.is_(None)) | (ResearchProjectMemory.valid_to >= now),
        )
        .order_by(ResearchProjectMemory.created_at.desc())
    )
    return list(result.scalars().all())


async def list_recent_project_memories(
    db: AsyncSession, *, project_id: UUID, limit: int = 10
) -> list[ResearchProjectMemory]:
    """Return bounded, currently valid PostgreSQL memories for prompt fallback."""
    now = datetime.now(UTC)
    result = await db.execute(
        select(ResearchProjectMemory)
        .where(
            ResearchProjectMemory.project_id == project_id,
            ResearchProjectMemory.valid_from <= now,
            (ResearchProjectMemory.valid_to.is_(None)) | (ResearchProjectMemory.valid_to >= now),
        )
        .order_by(ResearchProjectMemory.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_project_memories_by_ids(
    db: AsyncSession, *, project_id: UUID, memory_ids: list[UUID]
) -> list[ResearchProjectMemory]:
    """Resolve Qdrant hits through PostgreSQL and preserve semantic rank."""
    if not memory_ids:
        return []
    now = datetime.now(UTC)
    result = await db.execute(
        select(ResearchProjectMemory).where(
            ResearchProjectMemory.project_id == project_id,
            ResearchProjectMemory.id.in_(memory_ids),
            ResearchProjectMemory.valid_from <= now,
            (ResearchProjectMemory.valid_to.is_(None)) | (ResearchProjectMemory.valid_to >= now),
        )
    )
    by_id = {item.id: item for item in result.scalars().all()}
    return [by_id[item_id] for item_id in memory_ids if item_id in by_id]


async def confirm_profile(
    db: AsyncSession,
    *,
    user_id: UUID,
    body: ResearchProfileConfirm,
) -> UserResearchProfile:
    version = await db.scalar(
        select(func.coalesce(func.max(UserResearchProfile.version), 0)).where(
            UserResearchProfile.user_id == user_id
        )
    )
    row = UserResearchProfile(
        user_id=user_id,
        version=int(version or 0) + 1,
        preferences_json=body.preferences,
        confirmation_note=body.confirmation_note,
        confirmed_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    return row


async def get_latest_profile(db: AsyncSession, *, user_id: UUID) -> UserResearchProfile | None:
    return await db.scalar(
        select(UserResearchProfile)
        .where(UserResearchProfile.user_id == user_id)
        .order_by(UserResearchProfile.version.desc())
        .limit(1)
    )


async def create_feedback(
    db: AsyncSession,
    *,
    run_id: UUID,
    user_id: UUID,
    body: ResearchFeedbackCreate,
) -> ResearchFeedbackSample:
    row = ResearchFeedbackSample(
        run_id=run_id,
        work_id=body.work_id,
        user_id=user_id,
        feedback_type=body.feedback_type.value,
        payload_json=body.payload,
    )
    db.add(row)
    await db.flush()
    return row


async def create_policy_version(
    db: AsyncSession, *, body: PolicyVersionCreate
) -> ResearchPolicyVersion:
    version = await db.scalar(
        select(func.coalesce(func.max(ResearchPolicyVersion.version), 0)).where(
            ResearchPolicyVersion.policy_key == body.policy_key
        )
    )
    canonical = json.dumps(body.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row = ResearchPolicyVersion(
        policy_key=body.policy_key,
        version=int(version or 0) + 1,
        content_json=body.content,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        status="ACTIVE",
    )
    db.add(row)
    await db.flush()
    return row


async def list_policy_versions(
    db: AsyncSession, *, policy_key: str | None = None
) -> list[ResearchPolicyVersion]:
    query = select(ResearchPolicyVersion)
    if policy_key is not None:
        query = query.where(ResearchPolicyVersion.policy_key == policy_key)
    result = await db.execute(
        query.order_by(ResearchPolicyVersion.policy_key.asc(), ResearchPolicyVersion.version.desc())
    )
    return list(result.scalars().all())


async def list_active_policy_versions(db: AsyncSession) -> list[ResearchPolicyVersion]:
    """Return only the newest currently effective version for each policy key."""
    now = datetime.now(UTC)
    result = await db.execute(
        select(ResearchPolicyVersion)
        .where(
            ResearchPolicyVersion.status == "ACTIVE",
            ResearchPolicyVersion.valid_from <= now,
            (ResearchPolicyVersion.valid_to.is_(None)) | (ResearchPolicyVersion.valid_to >= now),
        )
        .order_by(
            ResearchPolicyVersion.policy_key.asc(),
            ResearchPolicyVersion.version.desc(),
        )
    )
    newest: dict[str, ResearchPolicyVersion] = {}
    for item in result.scalars().all():
        newest.setdefault(item.policy_key, item)
    return list(newest.values())
