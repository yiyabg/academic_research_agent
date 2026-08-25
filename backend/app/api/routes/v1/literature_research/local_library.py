"""Administrator-private endpoints for the deployment-managed Zotero corpus."""
# ruff: noqa: RUF001 - User-facing Chinese fallback text uses Chinese punctuation.

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select

from app.api.deps import CurrentAppAdmin, CurrentUser, CurrentUserWS, DBSession
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
    LocalPaperAskRequest,
    LocalPaperAskResponse,
    LocalPaperExportRequest,
    LocalPaperMindmapRequest,
    LocalPaperSearchRequest,
    LocalPaperSearchResponse,
)
from app.services.literature_research.local_paper_library import LocalPaperLibraryService
from app.services.literature_research.paper_mindmap_service import PaperMindmapService
from app.worker.tasks.local_paper_library_tasks import sync_local_paper_library

router = APIRouter()


def _sync_event(run: LocalPaperSyncRun) -> dict[str, object]:
    """The same envelope is used by REST/SSE snapshots and WebSocket events."""
    return {
        "type": "local_paper_sync_event",
        "data": {
            "sync_run_id": str(run.id),
            "status": run.status,
            "summary_json": run.summary_json,
            "error_message": run.error_message,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        },
    }


def _event_sequence(event: dict[str, object]) -> int:
    """Read the monotonic client cursor from a persisted event envelope."""
    data = event.get("data")
    if not isinstance(data, dict):
        return 0
    summary = data.get("summary_json")
    if not isinstance(summary, dict):
        return 0
    try:
        return int(summary.get("sequence", 0))
    except (TypeError, ValueError):
        return 0


async def _owned_sync_run(sync_run_id: UUID, owner_id: UUID) -> LocalPaperSyncRun:
    async with get_db_context() as db:
        run = await db.get(LocalPaperSyncRun, sync_run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="同步任务不存在")
        library = await db.get(LocalPaperLibrary, run.library_id)
        if library is None or library.owner_id != owner_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权读取该同步任务")
        return run


async def _owned_sync_events(
    sync_run_id: UUID, owner_id: UUID, *, after_sequence: int
) -> list[dict[str, object]]:
    """Replay durable events; Redis is never the source of recovery truth."""
    await _owned_sync_run(sync_run_id, owner_id)
    async with get_db_context() as db:
        events = (
            await db.scalars(
                select(LocalPaperSyncEvent)
                .where(
                    LocalPaperSyncEvent.sync_run_id == sync_run_id,
                    LocalPaperSyncEvent.sequence > after_sequence,
                )
                .order_by(LocalPaperSyncEvent.sequence)
                .limit(1000)
            )
        ).all()
    return [dict(event.payload_json) for event in events]


@router.get("/status", response_model=LocalLibraryStatusRead)
async def local_library_status(db: DBSession, current_admin: CurrentAppAdmin) -> object:
    return await LocalPaperLibraryService(db).get_status(owner_id=current_admin.id)


@router.post("/sync", response_model=LocalLibrarySyncAccepted, status_code=status.HTTP_202_ACCEPTED)
async def sync_local_library(db: DBSession, current_admin: CurrentAppAdmin) -> object:
    service = LocalPaperLibraryService(db)
    run = await service.request_sync(owner_id=current_admin.id)
    await db.commit()
    if run.status == "QUEUED":
        sync_local_paper_library.apply_async(
            args=(str(run.library_id), str(run.id)), queue="research-cpu"
        )
    return LocalLibrarySyncAccepted(sync_run_id=run.id, status=run.status)


@router.get("/sync/{sync_run_id}/stream", response_class=EventSourceResponse)
async def stream_local_library_sync(
    sync_run_id: UUID,
    request: Request,
    current_admin: CurrentAppAdmin,
    after_sequence: int = Query(default=0, ge=0),
) -> AsyncIterator[ServerSentEvent]:
    """SSE fallback for the same persisted local-paper sync status stream."""
    initial = _sync_event(await _owned_sync_run(sync_run_id, current_admin.id))
    redis: RedisClient = request.state.redis
    pubsub = redis.raw.pubsub()
    channel = f"local_paper_sync:{sync_run_id}"
    await pubsub.subscribe(channel)
    last_sequence = after_sequence
    try:
        replay = await _owned_sync_events(
            sync_run_id, current_admin.id, after_sequence=after_sequence
        )
        if replay:
            for event in replay:
                sequence = _event_sequence(event)
                yield ServerSentEvent(
                    data=event,
                    event="local_paper_sync_event",
                    id=str(sequence),
                )
                last_sequence = max(last_sequence, sequence)
        elif after_sequence == 0:
            # Runs created before event logging, and a freshly QUEUED run, still
            # need a useful first snapshot.
            yield ServerSentEvent(
                data=initial,
                event="local_paper_sync_event",
                id=str(_event_sequence(initial)),
            )
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            payload = message["data"]
            raw = payload.decode() if isinstance(payload, bytes) else str(payload)
            event = json.loads(raw)
            sequence = _event_sequence(event)
            if sequence <= last_sequence:
                continue
            yield ServerSentEvent(data=event, event="local_paper_sync_event", id=str(sequence))
            last_sequence = sequence
    except asyncio.CancelledError:
        raise
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.websocket("/sync/{sync_run_id}/stream")
async def stream_local_library_sync_ws(
    websocket: WebSocket,
    sync_run_id: UUID,
    user: CurrentUserWS,
    after_sequence: int = Query(default=0, ge=0),
) -> None:
    """Subscribe first, replay PostgreSQL, then fan out Redis notifications."""
    try:
        initial = _sync_event(await _owned_sync_run(sync_run_id, user.id))
    except HTTPException:
        await websocket.close(code=4403)
        return
    redis: RedisClient = websocket.state.redis
    pubsub = redis.raw.pubsub()
    channel = f"local_paper_sync:{sync_run_id}"
    await pubsub.subscribe(channel)
    subprotocol = getattr(websocket.state, "accept_subprotocol", None)
    await websocket.accept(subprotocol=subprotocol)
    last_sequence = after_sequence
    try:
        replay = await _owned_sync_events(sync_run_id, user.id, after_sequence=after_sequence)
        if replay:
            for event in replay:
                await websocket.send_json(event)
                last_sequence = max(last_sequence, _event_sequence(event))
        elif after_sequence == 0:
            await websocket.send_json(initial)
        async for message in iter_pubsub_until_disconnect(websocket, pubsub):
            if message["type"] != "message":
                continue
            raw = message["data"]
            event = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            sequence = _event_sequence(event)
            if sequence <= last_sequence:
                continue
            await websocket.send_json(event)
            last_sequence = sequence
    except WebSocketDisconnect:
        return
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.post("/search", response_model=LocalPaperSearchResponse)
async def search_local_library(
    body: LocalPaperSearchRequest, db: DBSession, current_user: CurrentUser
) -> object:
    return await LocalPaperLibraryService(db).search(owner_id=current_user.id, request=body)


@router.post("/ask", response_model=LocalPaperAskResponse)
async def ask_local_library(
    body: LocalPaperAskRequest, db: DBSession, current_user: CurrentUser
) -> object:
    return await LocalPaperLibraryService(db).ask(
        owner_id=current_user.id,
        question=body.question,
        limit=body.limit,
        paper_ids=body.paper_ids,
        query_context=body.query_context,
    )


@router.post("/export")
async def export_local_library(
    body: LocalPaperExportRequest, db: DBSession, current_user: CurrentUser
) -> Response:
    request = LocalPaperSearchRequest(**body.model_dump(exclude={"format"}))
    payload, media_type, filename = await LocalPaperLibraryService(db).export(
        owner_id=current_user.id, request=request, format=body.format
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/mindmap")
async def analyze_papers_mindmap(
    body: LocalPaperMindmapRequest, db: DBSession, current_user: CurrentUser
) -> Response:
    import asyncio

    # First search for papers using the existing library service
    library_service = LocalPaperLibraryService(db)
    search_request = LocalPaperSearchRequest(
        query=body.query,
        limit=body.limit,
        year_from=body.year_from,
        year_to=body.year_to,
    )
    search_result = await library_service.search(owner_id=current_user.id, request=search_request)

    # Then analyze with the new interface - with route-level timeout
    mindmap_service = PaperMindmapService()
    question = body.question or body.query

    try:
        content, filename = await asyncio.wait_for(
            mindmap_service.analyze(
                papers=search_result.items,
                question=question,
                output_format=body.output_format,
            ),
            timeout=200.0,  # 200s route-level timeout (slightly longer than service-level 180s)
        )
    except TimeoutError:
        # Force timeout fallback
        content = (
            f"# 思维导图生成超时\n\n"
            f"分析 {len(search_result.items)} 篇论文超过200秒，已强制中止。\n\n"
            f"**建议**：减少论文数量或稍后重试。\n\n"
            f"## 检索到的论文\n\n"
        )
        for i, p in enumerate(search_result.items, 1):
            content += f"{i}. {p.title}\n"
        filename = f"timeout_{question[:20]}.md"

    # Determine media type based on format
    if body.output_format == "opml":
        media_type = "text/x-opml; charset=utf-8"
    else:
        media_type = "text/markdown; charset=utf-8"

    return Response(
        content=content.encode() if isinstance(content, str) else content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
