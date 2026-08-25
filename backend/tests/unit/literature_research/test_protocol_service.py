"""Protocol version approval gate tests."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError, RateLimitError, ValidationError
from app.schemas.literature_research.analysis import ProtocolDraftAdvice
from app.schemas.literature_research.memory import ResolvedMemoryContext
from app.schemas.literature_research.protocol import (
    ConstraintOperator,
    DocumentType,
    ProtocolAdviceMemoryProvenance,
    ProtocolCompileRequest,
    ProtocolConstraint,
    ProtocolStatus,
    TopicFacet,
)
from app.services.literature_research.llm_usage import ResearchLLMBudgetExceeded
from app.services.literature_research.protocol import ResearchProtocolService
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.protocol_memory_context import (
    ProtocolMemoryContextBundle,
)


def memory_bundle() -> ProtocolMemoryContextBundle:
    return ProtocolMemoryContextBundle(
        resolved=ResolvedMemoryContext(
            values={"relevant_project_memories": [{"memory_id": "memory-1"}]},
            provenance={"relevant_project_memories": "project_memory"},
        ),
        provenance=ProtocolAdviceMemoryProvenance(
            retrieval_mode="postgres_fallback",
            project_memory_ids=[],
            profile_version=2,
            policy_versions={"research.default": 3},
            policy_hashes={"research.default": "a" * 64},
        ),
    )


@pytest.fixture
def service() -> ResearchProtocolService:
    return ResearchProtocolService(AsyncMock())


@pytest.mark.anyio
async def test_approval_rejects_hash_mismatch(service: ResearchProtocolService) -> None:
    stored = MagicMock(protocol_hash="sha256:" + "a" * 64)
    service.get = AsyncMock(return_value=stored)  # type: ignore[method-assign]
    with pytest.raises(ConflictError, match="hash does not match"):
        await service.approve(uuid4(), 1, uuid4(), "sha256:" + "b" * 64)


@pytest.mark.anyio
async def test_approval_rejects_blocking_ambiguity(service: ResearchProtocolService) -> None:
    compiled = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="auditable agents",
            as_of_date=date(2026, 8, 21),
            allowed_types=[DocumentType.JOURNAL_ARTICLE],
            required_sources=["crossref"],
            optional_sources=[],
            minimum_source_families=2,
        )
    )
    stored = MagicMock(
        protocol_hash=compiled.protocol_hash,
        protocol_json=compiled.protocol.model_dump(mode="json", by_alias=True),
        status=ProtocolStatus.DRAFT.value,
    )
    service.get = AsyncMock(return_value=stored)  # type: ignore[method-assign]
    with pytest.raises(ValidationError, match="blocking ambiguities"):
        await service.approve(uuid4(), 1, uuid4(), compiled.protocol_hash)


@pytest.mark.anyio
async def test_approval_freezes_executable_version(service: ResearchProtocolService) -> None:
    compiled = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="auditable agents",
            as_of_date=date(2026, 8, 21),
            allowed_types=[DocumentType.JOURNAL_ARTICLE],
        )
    )
    stored = MagicMock(
        protocol_hash=compiled.protocol_hash,
        protocol_json=compiled.protocol.model_dump(mode="json", by_alias=True),
        status=ProtocolStatus.DRAFT.value,
    )
    approved = MagicMock(status=ProtocolStatus.APPROVED.value)
    service.get = AsyncMock(return_value=stored)  # type: ignore[method-assign]
    with patch(
        "app.services.literature_research.protocol.protocol_repo.approve",
        new=AsyncMock(return_value=approved),
    ) as approve:
        result = await service.approve(uuid4(), 1, uuid4(), compiled.protocol_hash)
    assert result is approved
    approve.assert_awaited_once()


@pytest.mark.anyio
async def test_advice_fills_only_omitted_semantics_and_persists_provenance(
    service: ResearchProtocolService,
) -> None:
    request = ProtocolCompileRequest(
        topic="auditable agents",
        topic_definition="User definition wins",
        as_of_date=date(2026, 8, 21),
        date_from=date(2026, 1, 1),
        date_to=date(2026, 8, 21),
        allowed_types=[DocumentType.JOURNAL_ARTICLE],
        target_count=35,
        constraints=[
            ProtocolConstraint(
                constraint_id="quality-floor",
                field="venue.metric.jif",
                operator=ConstraintOperator.GTE,
                value=7,
                verification_source="licensed-jcr",
            )
        ],
    )
    advice = ProtocolDraftAdvice(
        topic_definition="Model definition must not overwrite explicit input",
        research_questions=["Which evidence controls improve auditability?"],
        must_have_facets=[
            TopicFacet(
                facet_id="auditability",
                name="Auditability",
                description="The work centrally evaluates auditable evidence controls.",
            )
        ],
        ambiguities=[],
    )
    expert = MagicMock()
    expert.protocol.run = AsyncMock(return_value=advice)
    service.project_service.get_owned = AsyncMock(return_value=MagicMock())
    service.memory_context.resolve_for_protocol_advice = AsyncMock(
        return_value=memory_bundle()
    )
    stored = MagicMock()

    with (
        patch(
            "app.services.literature_research.protocol.LiteratureResearchExperts",
            return_value=expert,
        ),
        patch(
            "app.services.literature_research.protocol.selected_llm_provider",
            return_value="deepseek",
        ),
        patch(
            "app.services.literature_research.protocol.selected_llm_model_identifier",
            return_value="deepseek:deepseek-chat",
        ),
        patch(
            "app.services.literature_research.protocol.protocol_repo.get_by_hash",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.literature_research.protocol.protocol_repo.next_version",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.services.literature_research.protocol.protocol_repo.create",
            new=AsyncMock(return_value=stored),
        ) as create,
    ):
        result = await service.advise_and_compile(uuid4(), uuid4(), request)

    assert result is stored
    protocol_json = create.await_args.kwargs["protocol_json"]
    assert protocol_json["topic_definition"] == "User definition wins"
    assert protocol_json["research_questions"] == advice.research_questions
    assert protocol_json["topic_model"]["must_have_facets"][0]["facet_id"] == "auditability"
    assert protocol_json["time_scope"] == {
        "from": "2026-01-01",
        "to": "2026-08-21",
        "timezone": "Asia/Shanghai",
        "date_field_priority": [
            "published_online",
            "issued",
            "published_print",
            "preprint_first_posted",
        ],
        "start_inclusive": True,
        "end_inclusive": True,
    }
    assert protocol_json["quantity_policy"]["target_count"] == 35
    assert protocol_json["draft_advice_provenance"]["model_identifier"] == (
        "deepseek:deepseek-chat"
    )
    assert protocol_json["draft_advice_provenance"]["memory_context"]["profile_version"] == 2
    expert.protocol.run.assert_awaited_once()
    assert (
        expert.protocol.run.await_args.args[0]["memory_context"]["values"]
        == memory_bundle().resolved.values
    )


@pytest.mark.anyio
async def test_advice_budget_exhaustion_maps_to_http_429(
    service: ResearchProtocolService,
) -> None:
    service.project_service.get_owned = AsyncMock(return_value=MagicMock())
    service.memory_context.resolve_for_protocol_advice = AsyncMock(
        return_value=memory_bundle()
    )
    expert = MagicMock()
    expert.protocol.run = AsyncMock(
        side_effect=ResearchLLMBudgetExceeded("approved limit reached")
    )
    with (
        patch(
            "app.services.literature_research.protocol.LiteratureResearchExperts",
            return_value=expert,
        ),
        pytest.raises(RateLimitError) as exc_info,
    ):
        await service.advise_and_compile(
            uuid4(),
            uuid4(),
            ProtocolCompileRequest(topic="auditable agents"),
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "LLM_BUDGET_EXCEEDED"
    assert exc_info.value.details is not None
    assert "llm_usage" in exc_info.value.details
