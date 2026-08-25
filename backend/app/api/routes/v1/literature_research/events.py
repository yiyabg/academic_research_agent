"""Replayable REST and WebSocket research run event endpoints."""

import json
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.deps import CurrentUser, CurrentUserWS, ResearchRunSvc
from app.api.routes.v1.literature_research.websocket_stream import iter_pubsub_until_disconnect
from app.clients.redis import RedisClient
from app.db.session import get_db_context
from app.schemas.literature_research.event import ResearchRunEventRead
from app.services.literature_research.run import ResearchRunService

router = APIRouter()


@router.get("/{run_id}/events", response_model=list[ResearchRunEventRead])
async def list_run_events(
    run_id: UUID,
    current_user: CurrentUser,
    service: ResearchRunSvc,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
) -> object:
    """Replay monotonically sequenced events after a reconnect."""
    return await service.list_events(
        run_id, current_user.id, after_sequence=after_sequence, limit=limit
    )


@router.websocket("/{run_id}/stream")
async def stream_run_events(
    websocket: WebSocket,
    run_id: UUID,
    user: CurrentUserWS,
    after_sequence: int = 0,
) -> None:
    """Subscribe first, replay persisted events, then forward live outbox events."""
    async with get_db_context() as db:
        service = ResearchRunService(db)
        await service.get_owned(run_id, user.id)

    redis: RedisClient = websocket.state.redis
    pubsub = redis.raw.pubsub()
    channel = f"research_run:{run_id}"
    await pubsub.subscribe(channel)
    subprotocol = getattr(websocket.state, "accept_subprotocol", None)
    await websocket.accept(subprotocol=subprotocol)
    last_sequence = after_sequence
    try:
        async with get_db_context() as db:
            replay = await ResearchRunService(db).list_events(
                run_id, user.id, after_sequence=after_sequence
            )
        for event in replay:
            data = ResearchRunEventRead.model_validate(event).model_dump(mode="json")
            await websocket.send_json({"type": "research_run_event", "data": data})
            last_sequence = max(last_sequence, event.sequence)

        async for message in iter_pubsub_until_disconnect(websocket, pubsub):
            if message["type"] != "message":
                continue
            raw = message["data"]
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            sequence = int(data["sequence"])
            if sequence <= last_sequence:
                continue
            await websocket.send_json({"type": "research_run_event", "data": data})
            last_sequence = sequence
    except WebSocketDisconnect:
        return
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
