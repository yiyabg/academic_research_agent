"""Authoritative L2/L3/L4 memory retrieval for protocol drafting."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.memory import ResearchProjectMemory
from app.db.models.literature_research.project import ResearchProject
from app.repositories.literature_research import memory as memory_repository
from app.repositories.literature_research import protocol as protocol_repository
from app.schemas.literature_research.memory import ResolvedMemoryContext
from app.schemas.literature_research.protocol import (
    ProtocolAdviceMemoryProvenance,
    ProtocolCompileRequest,
    ResearchProtocol,
)
from app.services.literature_research.memory_resolver import (
    FORBIDDEN_MEMORY_KEYS,
    resolve_memory_context,
)
from app.services.literature_research.vector_index import ResearchVectorIndex

logger = logging.getLogger(__name__)

MAX_PROJECT_MEMORIES = 10
MAX_CONTEXT_VALUE_CHARS = 4000
SENSITIVE_KEY_MARKERS = ("api_key", "password", "cookie", "secret", "token", "credential")


@dataclass(frozen=True)
class ProtocolMemoryContextBundle:
    resolved: ResolvedMemoryContext
    provenance: ProtocolAdviceMemoryProvenance


def _bounded_json(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical) <= MAX_CONTEXT_VALUE_CHARS:
        return canonical
    return canonical[:MAX_CONTEXT_VALUE_CHARS] + "…[truncated]"


def _sanitize_context_value(value: Any, *, path: str) -> tuple[Any, list[str]]:
    """Remove hard-semantics and credential-shaped keys before prompt injection."""
    ignored: list[str] = []
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower()
            child_path = f"{path}.{key}"
            if normalized in FORBIDDEN_MEMORY_KEYS or any(
                marker in normalized for marker in SENSITIVE_KEY_MARKERS
            ):
                ignored.append(child_path)
                continue
            clean_value, child_ignored = _sanitize_context_value(
                raw_value, path=child_path
            )
            clean[key] = clean_value
            ignored.extend(child_ignored)
        return clean, ignored
    if isinstance(value, list):
        clean_list = []
        for index, item in enumerate(value):
            clean_item, child_ignored = _sanitize_context_value(
                item, path=f"{path}[{index}]"
            )
            clean_list.append(clean_item)
            ignored.extend(child_ignored)
        return clean_list, ignored
    return value, ignored


def _semantic_request_values(request: ProtocolCompileRequest) -> dict[str, Any]:
    values: dict[str, Any] = {"topic": request.topic}
    if request.topic_definition:
        values["topic_definition"] = request.topic_definition
    if request.research_questions:
        values["research_questions"] = request.research_questions
    if request.must_have_facets:
        values["must_have_facets"] = [
            item.model_dump(mode="json") for item in request.must_have_facets
        ]
    return values


def _approved_semantic_values(protocol: ResearchProtocol | None) -> dict[str, Any]:
    if protocol is None:
        return {}
    return {
        "topic": protocol.topic,
        "topic_definition": protocol.topic_definition,
        "research_questions": protocol.research_questions,
        "must_have_facets": [
            item.model_dump(mode="json") for item in protocol.topic_model.must_have_facets
        ],
    }


def _memory_prompt_item(
    memory: ResearchProjectMemory,
) -> tuple[dict[str, Any], list[str]]:
    clean_content, ignored = _sanitize_context_value(
        memory.content_json, path=f"project_memory:{memory.id}"
    )
    return {
        "memory_id": str(memory.id),
        "memory_type": memory.memory_type,
        "source": memory.source,
        "confidence": memory.confidence,
        "content_json": _bounded_json(clean_content),
    }, ignored


class ResearchProtocolMemoryContextService:
    """Retrieve bounded memories while keeping PostgreSQL authoritative."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def resolve_for_protocol_advice(
        self,
        *,
        project: ResearchProject,
        owner_id: UUID,
        request: ProtocolCompileRequest,
    ) -> ProtocolMemoryContextBundle:
        recent = await memory_repository.list_recent_project_memories(
            self.db, project_id=project.id, limit=MAX_PROJECT_MEMORIES
        )
        semantic: list[ResearchProjectMemory] = []
        retrieval_error_type: str | None = None
        semantic_search_succeeded = False
        try:
            points = await ResearchVectorIndex().search_project_memories(
                organization_id=project.organization_id,
                project_id=project.id,
                query=request.topic,
                limit=MAX_PROJECT_MEMORIES,
            )
            semantic_search_succeeded = True
            memory_ids: list[UUID] = []
            for point in points:
                payload = point.payload or {}
                raw_id = payload.get("memory_id")
                try:
                    memory_id = UUID(str(raw_id))
                except (TypeError, ValueError):
                    continue
                if memory_id not in memory_ids:
                    memory_ids.append(memory_id)
            semantic = await memory_repository.list_project_memories_by_ids(
                self.db,
                project_id=project.id,
                memory_ids=memory_ids,
            )
        except Exception as exc:  # optional index: PostgreSQL is the source of truth
            retrieval_error_type = type(exc).__name__
            logger.warning(
                "Protocol memory semantic retrieval unavailable; using PostgreSQL fallback (%s)",
                retrieval_error_type,
            )

        combined: list[ResearchProjectMemory] = []
        seen: set[UUID] = set()
        for memory in [*semantic, *recent]:
            if memory.id in seen:
                continue
            combined.append(memory)
            seen.add(memory.id)
            if len(combined) == MAX_PROJECT_MEMORIES:
                break

        profile = await memory_repository.get_latest_profile(self.db, user_id=owner_id)
        policies = await memory_repository.list_active_policy_versions(self.db)
        approved_row = await protocol_repository.get_latest_approved(self.db, project.id)
        approved_protocol = (
            ResearchProtocol.model_validate(approved_row.protocol_json)
            if approved_row is not None
            else None
        )

        memory_items: list[dict[str, Any]] = []
        context_ignored: list[str] = []
        for item in combined:
            prompt_item, ignored = _memory_prompt_item(item)
            memory_items.append(prompt_item)
            context_ignored.extend(ignored)
        project_layer = {"relevant_project_memories": memory_items}
        clean_preferences, ignored = _sanitize_context_value(
            profile.preferences_json if profile is not None else {},
            path="confirmed_user_profile",
        )
        context_ignored.extend(ignored)
        profile_layer = (
            {"confirmed_user_preferences_json": _bounded_json(clean_preferences)}
            if profile is not None
            else {}
        )
        policy_items = []
        for item in policies:
            clean_policy, ignored = _sanitize_context_value(
                item.content_json, path=f"policy:{item.policy_key}@{item.version}"
            )
            context_ignored.extend(ignored)
            policy_items.append(
                {
                    "policy_key": item.policy_key,
                    "version": item.version,
                    "content_json": _bounded_json(clean_policy),
                }
            )
        policy_layer = {
            "active_policy_context": policy_items
        }
        resolved = resolve_memory_context(
            current_input=_semantic_request_values(request),
            approved_protocol=_approved_semantic_values(approved_protocol),
            project_memory=project_layer,
            user_profile=profile_layer,
            policy=policy_layer,
            defaults={},
        )
        if combined:
            retrieval_mode = (
                "semantic_plus_recent"
                if semantic_search_succeeded
                else "postgres_fallback"
            )
        else:
            retrieval_mode = "none"
        provenance = ProtocolAdviceMemoryProvenance(
            retrieval_mode=retrieval_mode,
            project_memory_ids=[item.id for item in combined],
            profile_id=profile.id if profile is not None else None,
            profile_version=profile.version if profile is not None else None,
            policy_versions={item.policy_key: item.version for item in policies},
            policy_hashes={item.policy_key: item.content_hash for item in policies},
            approved_protocol_hash=(
                approved_row.protocol_hash if approved_row is not None else None
            ),
            ignored_memory_keys=sorted({*resolved.ignored, *context_ignored}),
            retrieval_error_type=retrieval_error_type,
        )
        return ProtocolMemoryContextBundle(resolved=resolved, provenance=provenance)
