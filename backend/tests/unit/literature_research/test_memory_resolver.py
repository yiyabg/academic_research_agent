"""Five-layer memory precedence and protocol-protection tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.literature_research.memory import (
    FeedbackType,
    MemorySource,
    MemoryType,
    PolicyVersionCreate,
    ProjectMemoryCreate,
    ResearchFeedbackCreate,
    ResearchProfileConfirm,
)
from app.services.literature_research.memory_resolver import resolve_memory_context


def test_memory_precedence_matches_current_protocol_project_profile_policy_default() -> None:
    context = resolve_memory_context(
        current_input={"language": "zh", "topic": "current"},
        approved_protocol={"topic": "approved", "constraints": ["strict"]},
        project_memory={"topic": "project", "layout": "mindmap"},
        user_profile={"language": "en", "layout": "table"},
        policy={"language": "de", "source": "crossref"},
        defaults={"language": "fr", "source": "default"},
    )
    assert context.values["topic"] == "current"
    assert context.values["constraints"] == ["strict"]
    assert context.values["layout"] == "mindmap"
    assert context.values["source"] == "crossref"
    assert context.provenance["language"] == "current_input"


def test_lower_memory_layers_cannot_override_hard_semantics() -> None:
    context = resolve_memory_context(
        current_input={},
        approved_protocol={"quality_floor": "locked"},
        project_memory={"quality_floor": "relaxed"},
        user_profile={"constraints": []},
        policy={},
        defaults={"quantity_policy": {"target": 1}},
    )
    assert context.values["quality_floor"] == "locked"
    assert "project_memory:quality_floor" in context.ignored
    assert "confirmed_user_profile:constraints" in context.ignored
    assert "system_default:quantity_policy" in context.ignored


def test_memory_write_schema_rejects_protocol_override() -> None:
    with pytest.raises(ValidationError, match="cannot override"):
        ProjectMemoryCreate(
            memory_type=MemoryType.CORRECTION,
            content={"constraints": []},
            source=MemorySource.USER_FEEDBACK,
            source_id="feedback-1",
            confidence=1.0,
            valid_from=datetime.now(UTC),
        )

    with pytest.raises(ValidationError, match="cannot override"):
        ProjectMemoryCreate(
            memory_type=MemoryType.CORRECTION,
            content={"nested": {"time_scope": {"from": "2020-01-01"}}},
            source=MemorySource.USER_FEEDBACK,
            source_id="feedback-2",
            confidence=1.0,
            valid_from=datetime.now(UTC),
        )


def test_research_profile_rejects_credential_shaped_fields_recursively() -> None:
    with pytest.raises(ValidationError, match="credential-shaped"):
        ResearchProfileConfirm(
            preferences={"provider": {"api_key": "must-not-be-stored"}},
            confirmation_note="Explicit preference update",
        )


def test_policy_version_requires_ordered_validity_window() -> None:
    with pytest.raises(ValidationError, match="valid_from"):
        PolicyVersionCreate(
            policy_key="computer_science.sources",
            content={"sources": ["openalex"]},
            valid_from=datetime(2026, 8, 22, tzinfo=UTC),
            valid_to=datetime(2026, 8, 21, tzinfo=UTC),
        )


def test_policy_version_rejects_nested_hard_semantics_and_credentials() -> None:
    with pytest.raises(ValidationError, match="cannot override"):
        PolicyVersionCreate(
            policy_key="computer_science.sources",
            content={"nested": {"constraints": [{"field": "jif"}]}},
            valid_from=datetime.now(UTC),
        )
    with pytest.raises(ValidationError, match="credential-shaped"):
        PolicyVersionCreate(
            policy_key="computer_science.sources",
            content={"provider": {"access_token": "must-not-be-stored"}},
            valid_from=datetime.now(UTC),
        )


def test_relevance_feedback_requires_paper_and_bounded_decision() -> None:
    with pytest.raises(ValidationError, match="requires work_id"):
        ResearchFeedbackCreate(
            feedback_type=FeedbackType.RELEVANCE_CORRECTION,
            payload={"decision": "EXCLUDE"},
        )
    with pytest.raises(ValidationError, match="must be INCLUDE, EXCLUDE, or REVIEW"):
        ResearchFeedbackCreate(
            work_id=uuid4(),
            feedback_type=FeedbackType.RELEVANCE_CORRECTION,
            payload={"decision": "DROP_EVERYTHING"},
        )
