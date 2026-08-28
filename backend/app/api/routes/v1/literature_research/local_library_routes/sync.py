"""Library synchronization status and durable stream endpoints."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select

from app.api.deps import CurrentAppAdmin, CurrentUserWS, DBSession
from app.api.routes.v1.literature_research.websocket_stream import iter_pubsub_until_disconnect
from app.clients.redis import RedisClient
from app.db.models.local_paper_library import (
    LocalPaperLibrary,
    LocalPaperSyncEvent,
    LocalPaperSyncRun,
)
from app.db.session import get_db_context
from app.schemas.literature_research.local_library import (
    LocalLibraryStatusRead,
    LocalLibrarySyncAccepted,
)
from app.services.literature_research.local_paper_library import (
    LocalPaperLibraryService,
    _local_paper_sync_event_payload,
)
from app.worker.tasks.local_paper_library_tasks import sync_local_paper_library

from .streaming import decode_pubsub_event, sync_event_sequence

router = APIRouter()


def _payload(run: LocalPaperSyncRun) -> dict[str, object]:
    return _local_paper_sync_event_payload(
        sync_run_id=run.id,
        status=run.status,
        summary=dict(run.summary_json),
        error_message=run.error_message,
    )


async def _owned(run_id: UUID, owner_id: UUID) -> LocalPaperSyncRun:
    async with get_db_context() as db:
        run = await db.get(LocalPaperSyncRun, run_id)
        library = await db.get(LocalPaperLibrary, run.library_id) if run else None
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="同步任务不存在")
        if library is None or library.owner_id != owner_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权读取该同步任务")
        return run


async def _replay(run_id: UUID, owner_id: UUID, after: int) -> list[dict[str, object]]:
    await _owned(run_id, owner_id)
    async with get_db_context() as db:
        rows = (
            await db.scalars(
                select(LocalPaperSyncEvent)
                .where(
                    LocalPaperSyncEvent.sync_run_id == run_id, LocalPaperSyncEvent.sequence > after
                )
                .order_by(LocalPaperSyncEvent.sequence)
            )
        ).all()
    return [dict(row.payload_json) for row in rows]


@router.get("/status", response_model=LocalLibraryStatusRead)
async def local_library_status(db: DBSession, current_admin: CurrentAppAdmin) -> object:
    return await LocalPaperLibraryService(db).get_status(owner_id=current_admin.id)


@router.post("/sync", response_model=LocalLibrarySyncAccepted, status_code=status.HTTP_202_ACCEPTED)
async def sync_local_library(db: DBSession, current_admin: CurrentAppAdmin) -> object:
    run = await LocalPaperLibraryService(db).request_sync(owner_id=current_admin.id)
    await db.commit()
    if run.status == "QUEUED":
        sync_local_paper_library.apply_async(
            args=(str(run.library_id), str(run.id)), queue="research-cpu"
        )
    return LocalLibrarySyncAccepted(sync_run_id=run.id, status=run.status)


@router.get("/sync/{sync_run_id}/stream", response_class=EventSourceResponse)
async def stream_sync(
    sync_run_id: UUID,
    request: Request,
    current_admin: CurrentAppAdmin,
    after_sequence: int = Query(default=0, ge=0),
) -> AsyncIterator[ServerSentEvent]:
    initial = _payload(await _owned(sync_run_id, current_admin.id))
    redis: RedisClient = request.state.redis
    pubsub = redis.raw.pubsub()
    await pubsub.subscribe(f"local_paper_sync:{sync_run_id}")
    try:
        replay = await _replay(sync_run_id, current_admin.id, after_sequence)
        last_sequence = after_sequence
        for event in replay:
            sequence = sync_event_sequence(event)
            yield ServerSentEvent(data=event, event="local_paper_sync_event", id=str(sequence))
            last_sequence = max(last_sequence, sequence)
        if not replay and after_sequence == 0:
            yield ServerSentEvent(
                data=initial,
                event="local_paper_sync_event",
                id=str(sync_event_sequence(initial)),
            )
        async for message in pubsub.listen():
            if message["type"] == "message":
                event = decode_pubsub_event(message["data"])
                if event is None:
                    continue
                sequence = sync_event_sequence(event)
                if sequence <= last_sequence:
                    continue
                last_sequence = sequence
                yield ServerSentEvent(data=event, event="local_paper_sync_event", id=str(sequence))
    except asyncio.CancelledError:
        raise
    finally:
        await pubsub.unsubscribe(f"local_paper_sync:{sync_run_id}")
        await pubsub.aclose()


@router.websocket("/sync/{sync_run_id}/stream")
async def stream_sync_ws(
    websocket: WebSocket,
    sync_run_id: UUID,
    user: CurrentUserWS,
    after_sequence: int = Query(default=0, ge=0),
) -> None:
    try:
        initial = _payload(await _owned(sync_run_id, user.id))
    except HTTPException:
        await websocket.close(code=4403)
        return
    redis: RedisClient = websocket.state.redis
    pubsub = redis.raw.pubsub()
    await pubsub.subscribe(f"local_paper_sync:{sync_run_id}")
    await websocket.accept(subprotocol=getattr(websocket.state, "accept_subprotocol", None))
    try:
        last_sequence = after_sequence
        replay = await _replay(sync_run_id, user.id, after_sequence)
        for event in replay:
            await websocket.send_json(event)
            last_sequence = max(last_sequence, sync_event_sequence(event))
        if after_sequence == 0 and not replay:
            await websocket.send_json(initial)
        async for message in iter_pubsub_until_disconnect(websocket, pubsub):
            if message["type"] == "message":
                event = decode_pubsub_event(message["data"])
                if event is None:
                    continue
                sequence = sync_event_sequence(event)
                if sequence <= last_sequence:
                    continue
                last_sequence = sequence
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    finally:
        await pubsub.unsubscribe(f"local_paper_sync:{sync_run_id}")
        await pubsub.aclose()
