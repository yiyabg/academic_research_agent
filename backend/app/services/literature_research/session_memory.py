"""Redis-backed L1 clarification memory with a bounded 24-hour lifetime."""

import json
from datetime import UTC, datetime
from uuid import UUID

from app.clients.redis import RedisClient
from app.schemas.literature_research.memory import SessionMemoryRead, SessionMemoryWrite

SESSION_MEMORY_TTL_SECONDS = 24 * 60 * 60


class ResearchSessionMemoryService:
    def __init__(self, redis: RedisClient) -> None:
        self.redis = redis

    @staticmethod
    def _key(user_id: UUID, session_id: UUID) -> str:
        return f"research:l1:{user_id}:{session_id}"

    async def put(
        self, *, user_id: UUID, session_id: UUID, body: SessionMemoryWrite
    ) -> SessionMemoryRead:
        value = SessionMemoryRead(
            **body.model_dump(),
            session_id=session_id,
            user_id=user_id,
            updated_at=datetime.now(UTC),
            expires_in_seconds=SESSION_MEMORY_TTL_SECONDS,
        )
        await self.redis.set(
            self._key(user_id, session_id),
            json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
            ttl=SESSION_MEMORY_TTL_SECONDS,
        )
        return value

    async def get(self, *, user_id: UUID, session_id: UUID) -> SessionMemoryRead | None:
        raw = await self.redis.get(self._key(user_id, session_id))
        return SessionMemoryRead.model_validate_json(raw) if raw else None
