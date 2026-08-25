"""L1 session memory lifetime and approval-boundary tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.literature_research.memory import SessionMemoryWrite
from app.services.literature_research.session_memory import (
    SESSION_MEMORY_TTL_SECONDS,
    ResearchSessionMemoryService,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


@pytest.mark.anyio
async def test_session_memory_is_user_namespaced_and_expires_in_24_hours() -> None:
    redis = FakeRedis()
    service = ResearchSessionMemoryService(redis)  # type: ignore[arg-type]
    user_id, other_user_id, session_id = uuid4(), uuid4(), uuid4()
    body = SessionMemoryWrite(
        draft_slots={"topic": "auditable agents"}, missing_slots=["date_range"]
    )
    saved = await service.put(user_id=user_id, session_id=session_id, body=body)

    assert saved.expires_in_seconds == SESSION_MEMORY_TTL_SECONDS
    assert redis.ttls[f"research:l1:{user_id}:{session_id}"] == SESSION_MEMORY_TTL_SECONDS
    assert await service.get(user_id=other_user_id, session_id=session_id) is None
    restored = await service.get(user_id=user_id, session_id=session_id)
    assert restored is not None
    assert restored.draft_slots["topic"] == "auditable agents"


def test_session_memory_cannot_claim_protocol_approval() -> None:
    with pytest.raises(ValidationError, match="cannot represent protocol approval"):
        SessionMemoryWrite(draft_slots={"approved": True})
