"""Protocol drafting must consume bounded, authoritative research memories."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.schemas.literature_research.protocol import DocumentType, ProtocolCompileRequest
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.protocol_memory_context import (
    ResearchProtocolMemoryContextService,
)


def _memory(
    *, memory_id: UUID | None = None, content: dict[str, object] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=memory_id or uuid4(),
        memory_type="CORRECTION",
        source="USER_FEEDBACK",
        confidence=1.0,
        content_json=content or {"note": "prefer evidence-grounded agents"},
        created_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_memory_context_semantically_retrieves_then_validates_with_postgres() -> None:
    project = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    owner_id = uuid4()
    semantic_memory = _memory()
    recent_memory = _memory()
    profile = SimpleNamespace(
        id=uuid4(),
        version=4,
        preferences_json={"citation_style": "IEEE"},
    )
    policy = SimpleNamespace(
        policy_key="research.semantic-agents",
        version=3,
        content_hash="b" * 64,
        content_json={"synonyms": ["agentic system"]},
    )
    approved = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="previous topic",
            topic_definition="approved definition",
            as_of_date=date(2026, 8, 22),
            allowed_types=[DocumentType.JOURNAL_ARTICLE],
        )
    )
    approved_row = SimpleNamespace(
        protocol_json=approved.protocol.model_dump(mode="json", by_alias=True),
        protocol_hash=approved.protocol_hash,
    )
    vector = MagicMock()
    vector.search_project_memories = AsyncMock(
        return_value=[SimpleNamespace(payload={"memory_id": str(semantic_memory.id)})]
    )

    with (
        patch(
            "app.services.literature_research.protocol_memory_context.ResearchVectorIndex",
            return_value=vector,
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "list_recent_project_memories",
            new=AsyncMock(return_value=[recent_memory]),
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "list_project_memories_by_ids",
            new=AsyncMock(return_value=[semantic_memory]),
        ) as by_ids,
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "get_latest_profile",
            new=AsyncMock(return_value=profile),
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "list_active_policy_versions",
            new=AsyncMock(return_value=[policy]),
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.protocol_repository."
            "get_latest_approved",
            new=AsyncMock(return_value=approved_row),
        ),
    ):
        bundle = await ResearchProtocolMemoryContextService(
            AsyncMock()
        ).resolve_for_protocol_advice(
            project=project,
            owner_id=owner_id,
            request=ProtocolCompileRequest(
                topic="current topic",
                topic_definition="current definition",
                as_of_date=date(2026, 8, 22),
            ),
        )

    assert bundle.provenance.retrieval_mode == "semantic_plus_recent"
    assert bundle.provenance.project_memory_ids == [semantic_memory.id, recent_memory.id]
    assert bundle.provenance.profile_version == 4
    assert bundle.provenance.policy_versions == {"research.semantic-agents": 3}
    assert bundle.provenance.approved_protocol_hash == approved.protocol_hash
    assert bundle.resolved.values["topic"] == "current topic"
    assert bundle.resolved.values["topic_definition"] == "current definition"
    by_ids.assert_awaited_once_with(
        ANY,
        project_id=project.id,
        memory_ids=[semantic_memory.id],
    )


@pytest.mark.anyio
async def test_memory_context_falls_back_and_removes_hard_or_secret_keys() -> None:
    project = SimpleNamespace(id=uuid4(), organization_id=None)
    memory = _memory(
        content={
            "note": "use corrected terminology",
            "constraints": {"minimum_jif": 0},
            "nested": {"api_key": "must-not-enter-prompt"},
        }
    )
    vector = MagicMock()
    vector.search_project_memories = AsyncMock(side_effect=RuntimeError("qdrant down"))

    with (
        patch(
            "app.services.literature_research.protocol_memory_context.ResearchVectorIndex",
            return_value=vector,
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "list_recent_project_memories",
            new=AsyncMock(return_value=[memory]),
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "get_latest_profile",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.memory_repository."
            "list_active_policy_versions",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.literature_research.protocol_memory_context.protocol_repository."
            "get_latest_approved",
            new=AsyncMock(return_value=None),
        ),
    ):
        bundle = await ResearchProtocolMemoryContextService(
            AsyncMock()
        ).resolve_for_protocol_advice(
            project=project,
            owner_id=uuid4(),
            request=ProtocolCompileRequest(topic="current topic"),
        )

    context_text = str(bundle.resolved.model_dump(mode="json"))
    assert bundle.provenance.retrieval_mode == "postgres_fallback"
    assert bundle.provenance.retrieval_error_type == "RuntimeError"
    assert "constraints" not in context_text
    assert "api_key" not in context_text
    assert any("constraints" in item for item in bundle.provenance.ignored_memory_keys)
    assert any("api_key" in item for item in bundle.provenance.ignored_memory_keys)
