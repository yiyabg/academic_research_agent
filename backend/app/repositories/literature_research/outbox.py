"""Data access for the transactional research event outbox."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.outbox import ResearchOutboxEvent
from app.schemas.literature_research.event import ResearchEventType


async def next_sequence(db: AsyncSession, run_id: UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(ResearchOutboxEvent.sequence), 0)).where(
            ResearchOutboxEvent.run_id == run_id
        )
    )
    return int(result.scalar_one()) + 1


async def create(
    db: AsyncSession,
    *,
    run_id: UUID,
    event_type: ResearchEventType,
    stage: str,
    payload: dict[str, object],
) -> ResearchOutboxEvent:
    event = ResearchOutboxEvent(
        run_id=run_id,
        sequence=await next_sequence(db, run_id),
        event_type=event_type.value,
        stage=stage,
        payload=payload,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)
    return event


async def list_after(
    db: AsyncSession, run_id: UUID, *, after_sequence: int = 0, limit: int = 500
) -> list[ResearchOutboxEvent]:
    result = await db.execute(
        select(ResearchOutboxEvent)
        .where(
            ResearchOutboxEvent.run_id == run_id,
            ResearchOutboxEvent.sequence > after_sequence,
        )
        .order_by(ResearchOutboxEvent.sequence.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_unpublished(db: AsyncSession, *, limit: int = 200) -> list[ResearchOutboxEvent]:
    result = await db.execute(
        select(ResearchOutboxEvent)
        .where(ResearchOutboxEvent.published_at.is_(None))
        .order_by(ResearchOutboxEvent.occurred_at.asc(), ResearchOutboxEvent.sequence.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def mark_published(
    db: AsyncSession, event: ResearchOutboxEvent, published_at: datetime
) -> None:
    event.published_at = published_at
    db.add(event)
    await db.flush()
