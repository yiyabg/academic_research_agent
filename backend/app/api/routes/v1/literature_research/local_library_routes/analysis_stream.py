"""Analysis WebSocket delivery with PostgreSQL replay and Redis fan-out."""

from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.deps import CurrentUserWS
from app.api.routes.v1.literature_research.websocket_stream import iter_pubsub_until_disconnect
from app.clients.redis import RedisClient
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.session import get_db_context
from app.services.literature_research.local_paper_analysis import LocalPaperAnalysisService

from .streaming import analysis_event_sequence, decode_pubsub_event

router = APIRouter()
@router.websocket("/analysis-jobs/{job_id}/stream")
async def stream_analysis(websocket: WebSocket, job_id: UUID, user: CurrentUserWS, after_sequence: int = Query(default=0, ge=0)) -> None:
    async with get_db_context() as db:
        try:
            events = await LocalPaperAnalysisService(db).list_events(job_id=job_id, owner_id=user.id, after_sequence=after_sequence)
        except (AuthorizationError, NotFoundError):
            await websocket.close(code=4403)
            return
    redis: RedisClient = websocket.state.redis
    pubsub = redis.raw.pubsub()
    await pubsub.subscribe(f"local_paper_analysis:{job_id}")
    await websocket.accept(subprotocol=getattr(websocket.state, "accept_subprotocol", None))
    try:
        last_sequence = after_sequence
        for event in events:
            await websocket.send_json(event.payload_json)
            last_sequence = max(last_sequence, event.sequence)
        async for message in iter_pubsub_until_disconnect(websocket, pubsub):
            if message["type"] == "message":
                payload = decode_pubsub_event(message["data"])
                if payload is None:
                    continue
                sequence = analysis_event_sequence(payload)
                if sequence <= last_sequence:
                    continue
                last_sequence = sequence
                await websocket.send_json(payload)
    except WebSocketDisconnect:
        return
    finally:
        await pubsub.unsubscribe(f"local_paper_analysis:{job_id}")
        await pubsub.aclose()
