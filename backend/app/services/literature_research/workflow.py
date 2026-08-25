"""Deterministic literature research workflow state machine."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.db.models.literature_research.run import ResearchRun
from app.repositories.literature_research import outbox as outbox_repo
from app.repositories.literature_research import run as run_repo
from app.schemas.literature_research.event import ResearchEventType
from app.schemas.literature_research.run import (
    ExecutionMode,
    RunState,
    SearchExhaustion,
    ShortfallReport,
)

TERMINAL_STATES = {
    RunState.COMPLETED,
    RunState.PARTIALLY_COMPLETED,
    RunState.FAILED_TERMINAL,
    RunState.CANCELLED,
}

PIPELINE_STATES = [
    RunState.QUEUED,
    RunState.DISCOVERING,
    RunState.NORMALIZING,
    RunState.ENRICHING_METRICS,
    RunState.DEDUPLICATING,
    RunState.HARD_FILTERING,
    RunState.RELEVANCE_SCORING,
    RunState.FULLTEXT_ACQUIRING,
    RunState.PARSING,
    RunState.SELECTING,
    RunState.ANALYZING,
    RunState.EVIDENCE_AUDITING,
    RunState.SYNTHESIZING,
    RunState.RENDERING,
    RunState.RELEASE_CHECKING,
    RunState.COMPLETED,
]

ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    current: {
        following,
        RunState.CANCEL_REQUESTED,
        RunState.PAUSED,
        RunState.FAILED_RETRYABLE,
        RunState.FAILED_TERMINAL,
    }
    for current, following in pairwise(PIPELINE_STATES)
}
ALLOWED_TRANSITIONS.update(
    {
        RunState.RELEVANCE_SCORING: {
            RunState.FULLTEXT_ACQUIRING,
            RunState.SELECTING,
            RunState.CANCEL_REQUESTED,
            RunState.PAUSED,
            RunState.FAILED_RETRYABLE,
            RunState.FAILED_TERMINAL,
        },
        RunState.SELECTING: {
            RunState.ANALYZING,
            RunState.RENDERING,
            RunState.AWAITING_RELAXATION_AUTHORIZATION,
            RunState.COMPLETED,
            RunState.PARTIALLY_COMPLETED,
            RunState.CANCEL_REQUESTED,
            RunState.PAUSED,
            RunState.FAILED_RETRYABLE,
            RunState.FAILED_TERMINAL,
        },
        RunState.AWAITING_RELAXATION_AUTHORIZATION: {
            RunState.ANALYZING,
            RunState.PARTIALLY_COMPLETED,
            RunState.CANCEL_REQUESTED,
        },
        RunState.FAILED_RETRYABLE: {
            *PIPELINE_STATES[:-1],
            RunState.FAILED_TERMINAL,
        },
        RunState.PAUSED: {*PIPELINE_STATES[:-1], RunState.CANCEL_REQUESTED},
        RunState.CANCEL_REQUESTED: {RunState.CANCELLED},
        RunState.RELEASE_CHECKING: {
            RunState.COMPLETED,
            RunState.PARTIALLY_COMPLETED,
            RunState.FAILED_TERMINAL,
            RunState.CANCEL_REQUESTED,
            RunState.PAUSED,
            RunState.FAILED_RETRYABLE,
        },
        RunState.RENDERING: {
            RunState.RELEASE_CHECKING,
            RunState.COMPLETED,
            RunState.PARTIALLY_COMPLETED,
            RunState.CANCEL_REQUESTED,
            RunState.PAUSED,
            RunState.FAILED_RETRYABLE,
            RunState.FAILED_TERMINAL,
        },
        RunState.QUEUED: {
            RunState.DISCOVERING,
            RunState.COMPLETED,
            RunState.CANCEL_REQUESTED,
            RunState.PAUSED,
            RunState.FAILED_RETRYABLE,
        },
    }
)


class ResearchWorkflowService:
    def __init__(
        self,
        db: AsyncSession,
        stage_handlers: dict[RunState, Callable[[ResearchRun], Awaitable[dict[str, object]]]]
        | None = None,
    ):
        self.db = db
        self.stage_handlers = stage_handlers or {}

    async def execute_stage(self, run_id: UUID, expected_state: RunState) -> ResearchRun:
        """Execute one bounded stage transition from worker-owned database state.

        Production stage handlers own bounded scholarly work; this method owns
        serialization, durable control requests, and atomic state/outbox changes.
        """
        # Stage execution is a long transaction already. Lock the run row so a
        # duplicate Celery delivery cannot execute the same side effects in
        # parallel; after the first commit the waiter observes a stale state.
        # FOR NO KEY UPDATE still serializes duplicate stage deliveries, while
        # allowing the control table's FK check to take KEY SHARE so pause/cancel
        # requests do not wait behind a long external-I/O transaction.
        run = await self.db.get(ResearchRun, run_id, with_for_update={"key_share": True})
        if run is None:
            raise ConflictError(
                message="Research run does not exist",
                code="RUN_NOT_FOUND_FOR_WORKER",
            )
        current = RunState(run.state)
        if current != expected_state:
            raise ConflictError(
                message="Worker delivery is stale for the current run state",
                code="STALE_STAGE_DELIVERY",
            )
        stage_progress: dict[str, object] = {}
        control = await run_repo.get_control(self.db, run_id=run.id)
        handler = self.stage_handlers.get(current)
        if control is None and handler is not None:
            stage_progress = await handler(run)
            # Control requests live outside the locked run row and can commit
            # while a stage is blocked on an external scholarly service.
            control = await run_repo.get_control(self.db, run_id=run.id)
        try:
            mode = ExecutionMode(run.execution_mode)
        except (TypeError, ValueError):
            mode = ExecutionMode.FULL_RESEARCH
        if control is not None and control.requested_action == "cancel":
            next_state = RunState.CANCEL_REQUESTED
            stage_progress["control_action"] = "cancel"
        elif control is not None and control.requested_action == "pause":
            next_state = RunState.PAUSED
            stage_progress["control_action"] = "pause"
            stage_progress["paused_from"] = current.value
        elif current == RunState.CANCEL_REQUESTED:
            next_state = RunState.CANCELLED
        elif current == RunState.QUEUED and mode == ExecutionMode.VALIDATE_ONLY:
            next_state = RunState.COMPLETED
        elif current == RunState.RELEVANCE_SCORING and mode == ExecutionMode.SEARCH_ONLY:
            # Search-only returns metadata/relevance results and deliberately avoids
            # PDF acquisition, parsing, evidence extraction, and LLM analysis.
            next_state = RunState.SELECTING
        elif current == RunState.RELEASE_CHECKING:
            if stage_progress.get("release_allowed"):
                next_state = (
                    RunState.PARTIALLY_COMPLETED
                    if stage_progress.get("release_partial")
                    else RunState.COMPLETED
                )
            else:
                next_state = RunState.FAILED_TERMINAL
        elif current == RunState.SELECTING and mode == ExecutionMode.SEARCH_ONLY:
            # Metadata-only catalog mode freezes the strict ranking and renders
            # its four exports; it never enters PDF, evidence, or LLM stages.
            next_state = RunState.RENDERING
        elif current == RunState.RENDERING and mode == ExecutionMode.SEARCH_ONLY:
            next_state = (
                RunState.PARTIALLY_COMPLETED
                if run.strict_count < run.target_count
                else RunState.COMPLETED
            )
        elif current == RunState.SELECTING and run.strict_count < run.target_count:
            next_state = RunState.AWAITING_RELAXATION_AUTHORIZATION
            raw_loss_funnel = run.progress_json.get("loss_funnel", {})
            loss_funnel = (
                {key: int(value) for key, value in raw_loss_funnel.items()}
                if isinstance(raw_loss_funnel, dict)
                else {}
            )
            report = ShortfallReport(
                run_id=run.id,
                target_count=run.target_count,
                strict_count=run.strict_count,
                search_exhaustion=SearchExhaustion(
                    all_query_families_executed=bool(
                        run.progress_json.get("all_query_families_executed", False)
                    ),
                    all_sources_paginated_to_stop_rule=bool(
                        run.progress_json.get("all_sources_paginated_to_stop_rule", False)
                    ),
                    citation_neighbors_explored=bool(
                        run.progress_json.get("citation_neighbors_explored", False)
                    ),
                    keyword_neighbors_explored=bool(
                        run.progress_json.get("keyword_neighbors_explored", False)
                    ),
                ),
                loss_funnel=loss_funnel,
            )
            await run_repo.set_shortfall_report(
                self.db,
                run=run,
                report=report.model_dump(mode="json"),
            )
        else:
            try:
                next_state = PIPELINE_STATES[PIPELINE_STATES.index(current) + 1]
            except (ValueError, IndexError) as exc:
                raise ConflictError(
                    message=f"No automatic successor for state {current.value}",
                    code="NO_STAGE_SUCCESSOR",
                ) from exc
        transitioned = await self.transition(
            run_id=run.id,
            owner_id=run.owner_id,
            expected_state=current,
            expected_version=run.state_version,
            next_state=next_state,
            progress={
                **run.progress_json,
                **stage_progress,
                "stage": next_state.value,
            },
        )
        if control is not None:
            await run_repo.clear_control(self.db, run_id=run.id)
        if next_state == RunState.AWAITING_RELAXATION_AUTHORIZATION:
            await outbox_repo.create(
                self.db,
                run_id=run.id,
                event_type=ResearchEventType.SHORTAGE_REQUIRES_ACTION,
                stage=next_state.value,
                payload=run.shortage_report_json or {},
            )
        return transitioned

    async def transition(
        self,
        *,
        run_id: UUID,
        owner_id: UUID,
        expected_state: RunState,
        expected_version: int,
        next_state: RunState,
        progress: dict[str, Any] | None = None,
    ) -> ResearchRun:
        allowed = ALLOWED_TRANSITIONS.get(expected_state, set())
        if next_state not in allowed:
            raise ConflictError(
                message=f"Illegal research run transition: {expected_state} -> {next_state}",
                code="ILLEGAL_RUN_TRANSITION",
            )
        now = datetime.now(UTC)
        final_progress = progress or {"stage": next_state.value}
        run = await run_repo.transition(
            self.db,
            run_id=run_id,
            owner_id=owner_id,
            expected_state=expected_state,
            expected_version=expected_version,
            next_state=next_state,
            progress=final_progress,
            started_at=now if expected_state == RunState.QUEUED else None,
            finished_at=now if next_state in TERMINAL_STATES else None,
        )
        if run is None:
            raise ConflictError(
                message="Run state or state_version changed; reload before retrying",
                code="RUN_OPTIMISTIC_LOCK_CONFLICT",
            )
        await outbox_repo.create(
            self.db,
            run_id=run.id,
            event_type=ResearchEventType.RUN_STATE_CHANGED,
            stage=next_state.value,
            payload={
                "previous_state": expected_state.value,
                "state": next_state.value,
                "state_version": expected_version + 1,
                "progress": final_progress,
            },
        )
        return run
