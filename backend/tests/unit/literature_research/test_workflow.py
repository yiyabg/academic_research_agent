"""Deterministic workflow transition tests."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError
from app.db.models.literature_research.run import ResearchRun
from app.schemas.literature_research.run import RunState
from app.services.literature_research.workflow import ResearchWorkflowService


@pytest.fixture
def service() -> ResearchWorkflowService:
    return ResearchWorkflowService(AsyncMock())


@pytest.fixture(autouse=True)
def no_pending_control_request():
    with patch(
        "app.services.literature_research.workflow.run_repo.get_control",
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.mark.anyio
async def test_illegal_transition_is_rejected_before_database_write(
    service: ResearchWorkflowService,
) -> None:
    with (
        patch("app.services.literature_research.workflow.run_repo.transition") as transition,
        pytest.raises(ConflictError, match="Illegal research run transition"),
    ):
        await service.transition(
            run_id=uuid4(),
            owner_id=uuid4(),
            expected_state=RunState.QUEUED,
            expected_version=0,
            next_state=RunState.PARTIALLY_COMPLETED,
        )
    transition.assert_not_called()


@pytest.mark.anyio
async def test_optimistic_lock_conflict_does_not_emit_event(
    service: ResearchWorkflowService,
) -> None:
    with (
        patch(
            "app.services.literature_research.workflow.run_repo.transition",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.literature_research.workflow.outbox_repo.create",
            new=AsyncMock(),
        ) as create_event,
        pytest.raises(ConflictError, match="state_version changed"),
    ):
        await service.transition(
            run_id=uuid4(),
            owner_id=uuid4(),
            expected_state=RunState.QUEUED,
            expected_version=3,
            next_state=RunState.DISCOVERING,
        )
    create_event.assert_not_awaited()


@pytest.mark.anyio
async def test_valid_transition_persists_event(service: ResearchWorkflowService) -> None:
    run = MagicMock(id=uuid4(), state=RunState.DISCOVERING.value, state_version=1)
    with (
        patch(
            "app.services.literature_research.workflow.run_repo.transition",
            new=AsyncMock(return_value=run),
        ) as transition,
        patch(
            "app.services.literature_research.workflow.outbox_repo.create",
            new=AsyncMock(),
        ) as create_event,
    ):
        result = await service.transition(
            run_id=run.id,
            owner_id=uuid4(),
            expected_state=RunState.QUEUED,
            expected_version=0,
            next_state=RunState.DISCOVERING,
        )
    assert result is run
    transition.assert_awaited_once()
    create_event.assert_awaited_once()


@pytest.mark.anyio
async def test_stage_handler_runs_before_transition_and_progress_is_preserved() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        state=RunState.DISCOVERING.value,
        state_version=2,
        progress_json={"previous": "kept"},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    handler = AsyncMock(return_value={"raw_record_count": 42})
    service = ResearchWorkflowService(db, stage_handlers={RunState.DISCOVERING: handler})
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]

    await service.execute_stage(run.id, RunState.DISCOVERING)

    db.get.assert_awaited_once_with(ResearchRun, run.id, with_for_update={"key_share": True})
    handler.assert_awaited_once_with(run)
    assert service.transition.await_args.kwargs["next_state"] == RunState.NORMALIZING
    assert service.transition.await_args.kwargs["progress"] == {
        "previous": "kept",
        "raw_record_count": 42,
        "stage": RunState.NORMALIZING.value,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler_result", "expected"),
    [
        ({"release_allowed": True, "release_partial": False}, RunState.COMPLETED),
        (
            {"release_allowed": True, "release_partial": True},
            RunState.PARTIALLY_COMPLETED,
        ),
        (
            {"release_allowed": False, "release_blockers": ["ARTIFACT_INVALID"]},
            RunState.FAILED_TERMINAL,
        ),
    ],
)
async def test_release_handler_controls_terminal_state(handler_result, expected) -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        state=RunState.RELEASE_CHECKING.value,
        state_version=14,
        progress_json={},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(
        db,
        stage_handlers={RunState.RELEASE_CHECKING: AsyncMock(return_value=handler_result)},
    )
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    await service.execute_stage(run.id, RunState.RELEASE_CHECKING)
    assert service.transition.await_args.kwargs["next_state"] == expected


@pytest.mark.anyio
async def test_cancel_requested_transitions_to_cancelled() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        state=RunState.CANCEL_REQUESTED.value,
        state_version=5,
        progress_json={},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(db)
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    await service.execute_stage(run.id, RunState.CANCEL_REQUESTED)
    assert service.transition.await_args.kwargs["next_state"] == RunState.CANCELLED


@pytest.mark.anyio
async def test_pause_control_skips_handler_and_transitions_at_stage_boundary() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        execution_mode="full_research",
        state=RunState.DISCOVERING.value,
        state_version=2,
        progress_json={},
    )
    control = MagicMock(requested_action="pause")
    handler = AsyncMock(return_value={"should_not": "run"})
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(db, stage_handlers={RunState.DISCOVERING: handler})
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    with (
        patch(
            "app.services.literature_research.workflow.run_repo.get_control",
            new=AsyncMock(return_value=control),
        ),
        patch(
            "app.services.literature_research.workflow.run_repo.clear_control",
            new=AsyncMock(),
        ) as clear_control,
    ):
        await service.execute_stage(run.id, RunState.DISCOVERING)

    handler.assert_not_awaited()
    assert service.transition.await_args.kwargs["next_state"] == RunState.PAUSED
    clear_control.assert_awaited_once_with(db, run_id=run.id)


@pytest.mark.anyio
async def test_validate_only_completes_without_discovery() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        execution_mode="validate_only",
        state=RunState.QUEUED.value,
        state_version=0,
        progress_json={},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(
        db, stage_handlers={RunState.QUEUED: AsyncMock(return_value={"validated": True})}
    )
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    await service.execute_stage(run.id, RunState.QUEUED)
    assert service.transition.await_args.kwargs["next_state"] == RunState.COMPLETED


@pytest.mark.anyio
async def test_search_only_renders_after_selection() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        execution_mode="search_only",
        state=RunState.SELECTING.value,
        state_version=9,
        progress_json={},
        strict_count=3,
        target_count=5,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(
        db, stage_handlers={RunState.SELECTING: AsyncMock(return_value={})}
    )
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    await service.execute_stage(run.id, RunState.SELECTING)
    assert service.transition.await_args.kwargs["next_state"] == RunState.RENDERING


@pytest.mark.anyio
async def test_search_only_finishes_after_catalog_rendering() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        execution_mode="search_only",
        state=RunState.RENDERING.value,
        state_version=10,
        progress_json={"catalog_scope": "metadata_only"},
        strict_count=3,
        target_count=5,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(
        db, stage_handlers={RunState.RENDERING: AsyncMock(return_value={"artifact_count": 4})}
    )
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]

    await service.execute_stage(run.id, RunState.RENDERING)

    assert service.transition.await_args.kwargs["next_state"] == RunState.PARTIALLY_COMPLETED


@pytest.mark.anyio
async def test_search_only_skips_fulltext_and_parsing_after_relevance() -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        execution_mode="search_only",
        state=RunState.RELEVANCE_SCORING.value,
        state_version=6,
        progress_json={},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)
    service = ResearchWorkflowService(
        db, stage_handlers={RunState.RELEVANCE_SCORING: AsyncMock(return_value={})}
    )
    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    await service.execute_stage(run.id, RunState.RELEVANCE_SCORING)
    assert service.transition.await_args.kwargs["next_state"] == RunState.SELECTING


@pytest.mark.anyio
async def test_selecting_shortfall_waits_for_user_and_emits_shortfall_event(
    service: ResearchWorkflowService,
) -> None:
    run = MagicMock(
        id=uuid4(),
        owner_id=uuid4(),
        state=RunState.SELECTING.value,
        state_version=9,
        target_count=20,
        strict_count=7,
        progress_json={
            "all_query_families_executed": True,
            "all_sources_paginated_to_stop_rule": True,
            "citation_neighbors_explored": True,
            "keyword_neighbors_explored": True,
            "loss_funnel": {"raw_records": 100, "relevance_pass": 7},
        },
        shortage_report_json=None,
    )
    service.db.get = AsyncMock(return_value=run)

    async def set_report(_db, *, run, report):
        run.shortage_report_json = report
        return run

    service.transition = AsyncMock(return_value=run)  # type: ignore[method-assign]
    with (
        patch(
            "app.services.literature_research.workflow.run_repo.set_shortfall_report",
            new=AsyncMock(side_effect=set_report),
        ) as persist_report,
        patch(
            "app.services.literature_research.workflow.outbox_repo.create",
            new=AsyncMock(),
        ) as create_event,
    ):
        result = await service.execute_stage(run.id, RunState.SELECTING)

    assert result is run
    assert run.shortage_report_json["strict_count"] == 7
    assert run.shortage_report_json["target_count"] == 20
    persist_report.assert_awaited_once()
    assert service.transition.await_args.kwargs["next_state"] == (
        RunState.AWAITING_RELAXATION_AUTHORIZATION
    )
    assert create_event.await_args.kwargs["event_type"].value == "SHORTAGE_REQUIRES_ACTION"
