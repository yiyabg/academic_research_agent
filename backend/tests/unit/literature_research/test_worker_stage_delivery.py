"""Worker delivery idempotency adapter tests."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError
from app.schemas.literature_research.run import RunState
from app.services.literature_research.llm_usage import ResearchLLMBudgetExceeded
from app.worker.tasks.literature_research_tasks import (
    _mark_stage_failed,
    execute_research_stage,
    research_queue_for_state,
)


def test_relevance_stage_runs_on_budgeted_llm_queue() -> None:
    assert research_queue_for_state(RunState.RELEVANCE_SCORING) == "research-llm"


@pytest.mark.anyio
async def test_llm_budget_stage_failure_is_terminal_not_resumed_in_a_loop() -> None:
    run_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        owner_id=uuid4(),
        state=RunState.RELEVANCE_SCORING.value,
        state_version=7,
        progress_json={"relevance_prompt_version": "fixture"},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)

    @asynccontextmanager
    async def db_context():
        yield db

    with (
        patch(
            "app.worker.tasks.literature_research_tasks.get_worker_db_context",
            db_context,
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.ResearchWorkflowService.transition",
            new=AsyncMock(),
        ) as transition,
    ):
        await _mark_stage_failed(
            run_id,
            RunState.RELEVANCE_SCORING,
            ResearchLLMBudgetExceeded("approved limit reached"),
        )

    assert transition.await_args.kwargs["next_state"] == RunState.FAILED_TERMINAL
    failure = transition.await_args.kwargs["progress"]["failure"]
    assert failure["error_code"] == "LLM_BUDGET_EXCEEDED"
    assert failure["retryable"] is False


def test_stale_duplicate_delivery_is_an_idempotent_success() -> None:
    run_id = str(uuid4())
    with (
        patch(
            "app.worker.tasks.literature_research_tasks.ResearchWorkflowService.execute_stage",
            new=AsyncMock(
                side_effect=ConflictError(
                    message="stale",
                    code="STALE_STAGE_DELIVERY",
                )
            ),
        ),
        patch(
            "app.worker.tasks.literature_research_tasks._mark_stage_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        result = execute_research_stage.apply(args=(run_id, "QUEUED"), throw=True).get()

    assert result["run_id"] == run_id
    assert result["state"] == "QUEUED"
    assert result["status"] == "STALE_IGNORED"
    assert result["celery_task_id"]
    mark_failed.assert_not_awaited()
