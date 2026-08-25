"""Celery execution adapter for deterministic literature research stages."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from celery import shared_task
from redis import asyncio as aioredis
from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.db.models.literature_research.memory import ResearchProjectMemory
from app.db.models.literature_research.project import ResearchProject
from app.db.models.literature_research.run import ResearchRun
from app.db.session import get_worker_db_context
from app.repositories.literature_research import analysis as analysis_repo
from app.repositories.literature_research import catalog as catalog_repo
from app.repositories.literature_research import evidence as evidence_repo
from app.repositories.literature_research import outbox as outbox_repo
from app.repositories.literature_research import run as run_repo
from app.schemas.literature_research.event import ResearchEventType, ResearchRunEventRead
from app.schemas.literature_research.run import RunState
from app.services.literature_research.llm_usage import (
    ResearchLLMBudgetExceeded,
    attached_usage,
)
from app.services.literature_research.pipeline_stages import ResearchPipelineStages
from app.services.literature_research.vector_index import ResearchVectorIndex
from app.services.literature_research.workflow import ResearchWorkflowService
from app.services.llm_provider import selected_llm_model_identifier, selected_llm_provider


class TransientResearchStageError(RuntimeError):
    """Retryable infrastructure failure raised by a bounded stage handler."""


ANALYSIS_SCHEMA_VERSION = "3.0"
ANALYSIS_PROMPT_VERSION = "2026-08-22.2"
ANALYSIS_MAX_RETRIES = 2
ANALYSIS_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED_TERMINAL", "BLOCKED"}
ANALYSIS_DISPATCHABLE_STATUSES = {"PENDING", "FAILED_RETRYABLE", "RUNNING"}


def stable_analysis_task_id(run_id: UUID, work_id: UUID) -> str:
    """Return the deterministic Celery identity required by the migration design."""
    return f"research:{run_id}:analyze:{work_id}"


def analysis_shard_input_hash(
    *,
    protocol_hash: str,
    work_id: UUID,
    version_id: UUID,
    model_version: str,
) -> str:
    payload = (
        f"{protocol_hash}:{work_id}:{version_id}:{ANALYSIS_SCHEMA_VERSION}:"
        f"{ANALYSIS_PROMPT_VERSION}:{model_version}"
    )
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def research_queue_for_state(state: RunState) -> str:
    """Route stages by dominant resource without moving workflow truth into Celery."""
    if state in {
        RunState.QUEUED,
        RunState.DISCOVERING,
        RunState.NORMALIZING,
        RunState.ENRICHING_METRICS,
        RunState.FULLTEXT_ACQUIRING,
    }:
        return "research-io"
    if state in {
        RunState.DEDUPLICATING,
        RunState.HARD_FILTERING,
        RunState.PARSING,
        RunState.SELECTING,
        RunState.RENDERING,
        RunState.RELEASE_CHECKING,
        RunState.CANCEL_REQUESTED,
        RunState.PAUSED,
    }:
        return "research-cpu"
    return "research-llm"


@shared_task(
    bind=True,
    acks_late=True,
)
def execute_research_stage(self: Any, run_id: str, expected_state: str) -> dict[str, object]:
    """Execute one state-owned stage; Celery is not the workflow truth source."""

    if RunState(expected_state) == RunState.ANALYZING:
        try:
            shard_ids = asyncio.run(_prepare_initial_analysis_shards(UUID(run_id)))
        except Exception as exc:
            asyncio.run(_mark_stage_failed(UUID(run_id), RunState.ANALYZING, exc))
            raise
        for task_execution_id, work_id in shard_ids:
            analyze_research_paper.apply_async(
                args=(task_execution_id,),
                queue="paper-analysis",
                task_id=stable_analysis_task_id(UUID(run_id), UUID(work_id)),
            )
        finalize_research_analysis.apply_async(args=(run_id,), queue="research-llm")
        return {
            "run_id": run_id,
            "state": RunState.ANALYZING.value,
            "status": "SHARDS_SCHEDULED",
            "scheduled_shard_count": len(shard_ids),
            "celery_task_id": self.request.id,
        }

    async def _execute() -> dict[str, object]:
        async with get_worker_db_context() as db:
            stages = ResearchPipelineStages(db)
            try:
                run = await ResearchWorkflowService(
                    db, stage_handlers=stages.handlers()
                ).execute_stage(UUID(run_id), RunState(expected_state))
            except ConflictError as exc:
                if exc.code == "STALE_STAGE_DELIVERY":
                    return {
                        "run_id": run_id,
                        "state": expected_state,
                        "status": "STALE_IGNORED",
                        "celery_task_id": self.request.id,
                    }
                raise
            return {
                "run_id": str(run.id),
                "state": run.state,
                "state_version": run.state_version,
                "celery_task_id": self.request.id,
            }

    run_uuid = UUID(run_id)
    state = RunState(expected_state)
    try:
        result = asyncio.run(_execute())
    except ConflictError as exc:
        asyncio.run(_mark_stage_failed(run_uuid, state, exc))
        raise
    except Exception as exc:
        asyncio.run(_mark_stage_failed(run_uuid, state, exc))
        raise
    if result.get("status") == "STALE_IGNORED":
        return result
    next_state = RunState(str(result["state"]))
    if next_state not in {
        RunState.AWAITING_RELAXATION_AUTHORIZATION,
        RunState.COMPLETED,
        RunState.PARTIALLY_COMPLETED,
        RunState.FAILED_TERMINAL,
        RunState.CANCELLED,
        RunState.PAUSED,
    }:
        execute_research_stage.apply_async(
            args=(run_id, next_state.value), queue=research_queue_for_state(next_state)
        )
    return result


async def _prepare_initial_analysis_shards(run_id: UUID) -> list[tuple[str, str]]:
    """Persist the complete shard set before dispatching any Celery messages."""
    async with get_worker_db_context() as db:
        run = await db.get(ResearchRun, run_id, with_for_update=True)
        if run is None:
            raise RuntimeError("Research run does not exist")
        if RunState(run.state) != RunState.ANALYZING:
            return []
        pinned_model = str(run.progress_json.get("analysis_model") or settings.AI_MODEL)
        pinned_provider = str(
            run.progress_json.get("analysis_provider") or selected_llm_provider()
        )
        pinned_identity = str(
            run.progress_json.get("analysis_model_identifier")
            or selected_llm_model_identifier(pinned_model)
        )
        rows = (
            await evidence_repo.list_analysis_ready_versions(db, run_id=run.id)
        )[: run.target_count]
        dispatch: list[tuple[str, str]] = []
        for work, version in rows:
            task, _ = await analysis_repo.get_or_create_initial_analysis_task(
                db,
                run_id=run.id,
                work_id=work.id,
                input_hash=analysis_shard_input_hash(
                    protocol_hash=run.protocol_hash,
                    work_id=work.id,
                    version_id=version.id,
                    model_version=pinned_identity,
                ),
            )
            if task.status in ANALYSIS_DISPATCHABLE_STATUSES:
                dispatch.append((str(task.id), str(work.id)))
        counts = await analysis_repo.summarize_initial_analysis_tasks(db, run_id=run.id)
        run.progress_json = {
            **run.progress_json,
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
            "analysis_provider": pinned_provider,
            "analysis_model": pinned_model,
            "analysis_model_identifier": pinned_identity,
            "analysis_shard_total": counts["total"],
            "analysis_shards_succeeded": counts["succeeded"],
            "analysis_shards_failed_terminal": counts["failed_terminal"],
            "analysis_shards_blocked": counts["blocked"],
        }
        await outbox_repo.create(
            db,
            run_id=run.id,
            event_type=ResearchEventType.STAGE_PROGRESS,
            stage=RunState.ANALYZING.value,
            payload={
                "analysis_shard_total": counts["total"],
                "scheduled_shard_count": len(dispatch),
                "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
                "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
                "analysis_provider": pinned_provider,
                "analysis_model": pinned_model,
                "analysis_model_identifier": pinned_identity,
            },
        )
        return dispatch


async def _mark_analysis_shard_failed(
    task_execution_id: UUID,
    exc: Exception,
    *,
    terminal: bool,
) -> UUID | None:
    """Persist one shard failure separately from every successful paper."""
    async with get_worker_db_context() as db:
        task = await analysis_repo.get_task_execution(
            db, task_execution_id=task_execution_id, for_update=True
        )
        if task is None or task.status == "SUCCEEDED":
            return None
        attempt_count = task.attempt_count + 1
        previous_history = (
            task.error_json.get("attempt_history", [])
            if isinstance(task.error_json, dict)
            else []
        )
        history = list(previous_history) if isinstance(previous_history, list) else []
        attempt_error: dict[str, object] = {
            "attempt": attempt_count,
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
            "retryable": not terminal,
        }
        if (usage := attached_usage(exc)) is not None:
            attempt_error["llm_usage"] = usage
        history.append(attempt_error)
        task.attempt_count = attempt_count
        task.status = "FAILED_TERMINAL" if terminal else "FAILED_RETRYABLE"
        shard_error: dict[str, object] = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
            "retryable": not terminal,
            "attempt_history": history,
        }
        task.error_json = shard_error
        task.finished_at = datetime.now(UTC) if terminal else None
        await outbox_repo.create(
            db,
            run_id=task.run_id,
            event_type=ResearchEventType.PAPER_STATE_CHANGED,
            stage=RunState.ANALYZING.value,
            payload={
                "work_id": task.shard_key,
                "task_execution_id": str(task.id),
                "status": task.status,
                "attempt_count": task.attempt_count,
                "error_type": type(exc).__name__,
            },
        )
        return task.run_id


@shared_task(bind=True, acks_late=True, max_retries=ANALYSIS_MAX_RETRIES)
def analyze_research_paper(self: Any, task_execution_id: str) -> dict[str, object]:
    """Analyze one paper shard and atomically persist its audited output and status."""

    async def _execute() -> dict[str, object]:
        async with get_worker_db_context() as db:
            task = await analysis_repo.get_task_execution(
                db, task_execution_id=UUID(task_execution_id), for_update=True
            )
            if task is None or task.stage != "ANALYZE_PAPER":
                raise RuntimeError("Initial paper-analysis task execution does not exist")
            if task.status in ANALYSIS_TERMINAL_STATUSES:
                return {
                    **(task.output_json or {}),
                    "task_execution_id": str(task.id),
                    "run_id": str(task.run_id),
                    "status": task.status,
                }
            run = await db.get(ResearchRun, task.run_id)
            if run is None:
                raise RuntimeError("Research run does not exist")
            if RunState(run.state) != RunState.ANALYZING:
                return {
                    "task_execution_id": str(task.id),
                    "run_id": str(task.run_id),
                    "status": "STALE_IGNORED",
                    "run_state": run.state,
                }
            pinned_identity = str(run.progress_json.get("analysis_model_identifier") or "")
            if pinned_identity != selected_llm_model_identifier():
                raise RuntimeError(
                    "Pinned analysis provider/model no longer matches worker configuration"
                )
            task.status = "RUNNING"
            task.celery_task_id = self.request.id
            task.attempt_count += 1
            task.started_at = datetime.now(UTC)
            work_id = UUID(task.shard_key)
            candidate = await catalog_repo.get_candidate_row(
                db, run_id=run.id, work_id=work_id
            )
            if candidate is None:
                raise RuntimeError("Selected paper version does not exist")
            work, version, _, eligibility, relevance = candidate
            if version is None:
                raise RuntimeError("Selected paper version does not exist")
            if (
                eligibility is None
                or not eligibility.eligible
                or relevance is None
                or relevance.decision != "PASS"
            ):
                raise RuntimeError("Paper is no longer strictly selected")
            output = await ResearchPipelineStages(db).analyze_work_initial(
                run=run,
                work=work,
                version=version,
            )
            task.status = "SUCCEEDED"
            task.output_json = output
            task.finished_at = datetime.now(UTC)
            await outbox_repo.create(
                db,
                run_id=run.id,
                event_type=ResearchEventType.PAPER_STATE_CHANGED,
                stage=RunState.ANALYZING.value,
                payload={
                    **output,
                    "task_execution_id": str(task.id),
                    "status": "SUCCEEDED",
                    "attempt_count": task.attempt_count,
                },
            )
            return {**output, "run_id": str(run.id), "status": "SUCCEEDED"}

    execution_id = UUID(task_execution_id)
    try:
        result = asyncio.run(_execute())
    except Exception as exc:
        terminal = isinstance(exc, ResearchLLMBudgetExceeded) or (
            self.request.retries >= ANALYSIS_MAX_RETRIES
        )
        failed_run_id = asyncio.run(
            _mark_analysis_shard_failed(execution_id, exc, terminal=terminal)
        )
        if terminal and failed_run_id is not None:
            finalize_research_analysis.apply_async(
                args=(str(failed_run_id),), queue="research-llm"
            )
            raise
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc
    finalize_research_analysis.apply_async(
        args=(str(result["run_id"]),), queue="research-llm"
    )
    return result


@shared_task(bind=True, acks_late=True)
def finalize_research_analysis(self: Any, run_id: str) -> dict[str, object]:
    """Advance ANALYZING only after the PostgreSQL shard barrier is complete."""

    async def _finalize() -> dict[str, object]:
        async with get_worker_db_context() as db:
            run = await db.get(ResearchRun, UUID(run_id), with_for_update=True)
            if run is None:
                raise RuntimeError("Research run does not exist")
            if RunState(run.state) != RunState.ANALYZING:
                return {"run_id": run_id, "state": run.state, "status": "STALE_IGNORED"}
            control = await run_repo.get_control(db, run_id=run.id)
            if control is not None:
                transitioned = await ResearchWorkflowService(
                    db,
                    stage_handlers=ResearchPipelineStages(db).handlers(),
                ).execute_stage(run.id, RunState.ANALYZING)
                return {
                    "run_id": run_id,
                    "state": transitioned.state,
                    "state_version": transitioned.state_version,
                    "status": "CONTROL_APPLIED",
                    "control_action": control.requested_action,
                }
            counts = await analysis_repo.summarize_initial_analysis_tasks(db, run_id=run.id)
            terminal = counts["succeeded"] + counts["failed_terminal"] + counts["blocked"]
            if terminal != counts["total"]:
                return {"run_id": run_id, "state": run.state, "status": "BARRIER_WAITING", **counts}
            transitioned = await ResearchWorkflowService(
                db,
                stage_handlers=ResearchPipelineStages(db).handlers(),
            ).execute_stage(run.id, RunState.ANALYZING)
            return {
                "run_id": run_id,
                "state": transitioned.state,
                "state_version": transitioned.state_version,
                "status": "BARRIER_COMPLETE",
                **counts,
            }

    result = asyncio.run(_finalize())
    if result.get("status") in {"BARRIER_COMPLETE", "CONTROL_APPLIED"}:
        next_state = RunState(str(result["state"]))
        if next_state != RunState.PAUSED:
            execute_research_stage.apply_async(
                args=(run_id, next_state.value), queue=research_queue_for_state(next_state)
            )
    return {**result, "celery_task_id": self.request.id}


async def _mark_stage_failed(run_id: UUID, expected_state: RunState, exc: Exception) -> None:
    """Move a crashed stage to a user-visible resumable state in a new transaction."""
    async with get_worker_db_context() as db:
        run = await db.get(ResearchRun, run_id)
        if run is None or RunState(run.state) != expected_state:
            return
        budget_exceeded = isinstance(exc, ResearchLLMBudgetExceeded)
        error_payload: dict[str, object] = {
            "stage": expected_state.value,
            "error_type": type(exc).__name__,
            "error_code": "LLM_BUDGET_EXCEEDED" if budget_exceeded else "STAGE_FAILED",
            "message": str(exc)[:2000],
            "retryable": (
                isinstance(exc, TransientResearchStageError) and not budget_exceeded
            ),
            "failed_at": datetime.now(UTC).isoformat(),
        }
        if (usage := attached_usage(exc)) is not None:
            error_payload["llm_usage"] = usage
        await ResearchWorkflowService(db).transition(
            run_id=run.id,
            owner_id=run.owner_id,
            expected_state=expected_state,
            expected_version=run.state_version,
            next_state=(
                RunState.FAILED_TERMINAL if budget_exceeded else RunState.FAILED_RETRYABLE
            ),
            progress={**run.progress_json, "failure": error_payload},
        )


async def _mark_reanalysis_failed(task_execution_id: UUID, exc: Exception) -> None:
    """Persist failure in a fresh transaction after the work transaction rolls back."""
    async with get_worker_db_context() as db:
        task = await analysis_repo.get_task_execution(
            db, task_execution_id=task_execution_id, for_update=True
        )
        if task is None or task.status == "SUCCEEDED":
            return
        task.status = "FAILED"
        error_payload: dict[str, object] = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
        }
        if (usage := attached_usage(exc)) is not None:
            error_payload["llm_usage"] = usage
        task.error_json = error_payload
        task.finished_at = datetime.now(UTC)
        await outbox_repo.create(
            db,
            run_id=task.run_id,
            event_type=ResearchEventType.PAPER_STATE_CHANGED,
            stage="REANALYZE",
            payload={
                "work_id": task.shard_key,
                "task_execution_id": str(task.id),
                "status": "FAILED",
                "error_type": type(exc).__name__,
            },
        )


async def _mark_artifact_regeneration_failed(task_execution_id: UUID, exc: Exception) -> None:
    """Make a failed output generation visible and safe to retry explicitly."""
    async with get_worker_db_context() as db:
        task = await analysis_repo.get_task_execution(
            db, task_execution_id=task_execution_id, for_update=True
        )
        if task is None or task.status == "SUCCEEDED":
            return
        task.status = "FAILED"
        task.error_json = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
        }
        task.finished_at = datetime.now(UTC)
        await outbox_repo.create(
            db,
            run_id=task.run_id,
            event_type=ResearchEventType.RUN_FAILED,
            stage="REGENERATE_ARTIFACTS",
            payload={
                "task_execution_id": str(task.id),
                "status": "FAILED",
                "error_type": type(exc).__name__,
            },
        )


@shared_task(bind=True, acks_late=True)
def reanalyze_research_paper(self: Any, task_execution_id: str) -> dict[str, object]:
    """Run one immutable paper-analysis attempt without replaying the whole pipeline."""

    async def _execute() -> dict[str, object]:
        async with get_worker_db_context() as db:
            task = await analysis_repo.get_task_execution(
                db, task_execution_id=UUID(task_execution_id), for_update=True
            )
            if task is None:
                raise RuntimeError("Reanalysis task execution does not exist")
            if task.status == "SUCCEEDED":
                return task.output_json or {}
            task.status = "RUNNING"
            task.celery_task_id = self.request.id
            task.attempt_count += 1
            task.started_at = datetime.now(UTC)
            run = await db.get(ResearchRun, task.run_id)
            if run is None:
                raise RuntimeError("Research run does not exist")
            work_id = UUID(task.shard_key)
            candidate = await catalog_repo.get_candidate_row(db, run_id=run.id, work_id=work_id)
            if candidate is None:
                raise RuntimeError("Selected paper version does not exist")
            work, version, _, eligibility, relevance = candidate
            if version is None:
                raise RuntimeError("Selected paper version does not exist")
            if (
                eligibility is None
                or not eligibility.eligible
                or relevance is None
                or relevance.decision != "PASS"
            ):
                raise RuntimeError("Paper is no longer strictly selected")
            output = await ResearchPipelineStages(db).reanalyze_work(
                run=run, work=work, version=version
            )
            task.status = "SUCCEEDED"
            task.output_json = output
            task.finished_at = datetime.now(UTC)
            await outbox_repo.create(
                db,
                run_id=run.id,
                event_type=ResearchEventType.PAPER_STATE_CHANGED,
                stage="REANALYZE",
                payload={**output, "status": "SUCCEEDED"},
            )
            return output

    execution_id = UUID(task_execution_id)
    try:
        return asyncio.run(_execute())
    except Exception as exc:
        asyncio.run(_mark_reanalysis_failed(execution_id, exc))
        raise


@shared_task(bind=True, acks_late=True)
def regenerate_research_artifacts(self: Any, task_execution_id: str) -> dict[str, object]:
    """Create, release-audit, and publish one immutable output generation."""

    async def _execute() -> dict[str, object]:
        async with get_worker_db_context() as db:
            task = await analysis_repo.get_task_execution(
                db, task_execution_id=UUID(task_execution_id), for_update=True
            )
            if task is None or task.stage != "REGENERATE_ARTIFACTS":
                raise RuntimeError("Artifact regeneration task execution does not exist")
            if task.status == "SUCCEEDED":
                return task.output_json or {}
            task.status = "RUNNING"
            task.celery_task_id = self.request.id
            task.attempt_count += 1
            task.error_json = None
            task.started_at = datetime.now(UTC)
            run = await db.get(ResearchRun, task.run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("Research run does not exist")
            if run.state not in {"COMPLETED", "PARTIALLY_COMPLETED"}:
                raise RuntimeError("Only a terminal research run can regenerate artifacts")
            if run.execution_mode != "full_research":
                raise RuntimeError("Only full-research runs have analysis artifacts")
            output = await ResearchPipelineStages(db).regenerate_outputs(run)
            task.status = "SUCCEEDED"
            task.output_json = output
            task.finished_at = datetime.now(UTC)
            await outbox_repo.create(
                db,
                run_id=run.id,
                event_type=ResearchEventType.ARTIFACT_READY,
                stage="REGENERATE_ARTIFACTS",
                payload={
                    **output,
                    "task_execution_id": str(task.id),
                    "status": "SUCCEEDED",
                },
            )
            return output

    execution_id = UUID(task_execution_id)
    try:
        return asyncio.run(_execute())
    except Exception as exc:
        asyncio.run(_mark_artifact_regeneration_failed(execution_id, exc))
        raise


@shared_task
def index_research_project_memory(memory_id: str) -> dict[str, str]:
    """Materialize a PostgreSQL project-memory row in its isolated Qdrant index."""

    async def _index() -> dict[str, str]:
        async with get_worker_db_context() as db:
            memory = await db.get(ResearchProjectMemory, UUID(memory_id))
            if memory is None:
                raise RuntimeError("Project memory does not exist")
            project = await db.get(ResearchProject, memory.project_id)
            if project is None:
                raise RuntimeError("Research project does not exist")
            content = json.dumps(
                memory.content_json,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            await ResearchVectorIndex().upsert_project_memory(
                organization_id=project.organization_id,
                project_id=project.id,
                memory_id=memory.id,
                memory_type=memory.memory_type,
                content=content,
                source=memory.source,
            )
            return {"memory_id": str(memory.id), "status": "INDEXED"}

    return asyncio.run(_index())


@shared_task
def publish_research_outbox() -> int:
    """Publish committed outbox rows; consumers deduplicate by run sequence."""

    async def _publish() -> int:
        redis = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)  # type: ignore[no-untyped-call]
        published = 0
        try:
            async with get_worker_db_context() as db:
                events = await outbox_repo.list_unpublished(db)
                for event in events:
                    payload = ResearchRunEventRead.model_validate(event).model_dump(mode="json")
                    await redis.publish(
                        f"research_run:{event.run_id}",
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    )
                    await outbox_repo.mark_published(db, event, datetime.now(UTC))
                    published += 1
        finally:
            await redis.aclose()
        return published

    return asyncio.run(_publish())


@shared_task
def recover_stalled_research_runs() -> int:
    """Re-enqueue orphaned stages from PostgreSQL truth, independent of Celery results."""

    async def _claim() -> list[tuple[str, str]]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=settings.RESEARCH_STALLED_AFTER_SECONDS)
        lease_until = now + timedelta(seconds=settings.RESEARCH_STALLED_AFTER_SECONDS)
        waiting_states = {
            RunState.AWAITING_RELAXATION_AUTHORIZATION.value,
            RunState.COMPLETED.value,
            RunState.PARTIALLY_COMPLETED.value,
            RunState.FAILED_RETRYABLE.value,
            RunState.FAILED_TERMINAL.value,
            RunState.CANCELLED.value,
            RunState.PAUSED.value,
        }
        claimed: list[tuple[str, str]] = []
        async with get_worker_db_context() as db:
            result = await db.execute(
                select(ResearchRun)
                .where(
                    ResearchRun.state.not_in(waiting_states),
                    func.coalesce(ResearchRun.updated_at, ResearchRun.created_at) < cutoff,
                    or_(
                        ResearchRun.lease_expires_at.is_(None),
                        ResearchRun.lease_expires_at < now,
                    ),
                )
                .order_by(ResearchRun.created_at.asc())
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for run in result.scalars().all():
                run.lease_owner = "research-recovery-watchdog"
                run.lease_expires_at = lease_until
                claimed.append((str(run.id), run.state))
                await outbox_repo.create(
                    db,
                    run_id=run.id,
                    event_type=ResearchEventType.STAGE_PROGRESS,
                    stage=run.state,
                    payload={"recovery": "REENQUEUED_STALLED_STAGE"},
                )
        return claimed

    claimed = asyncio.run(_claim())
    for run_id, state_value in claimed:
        state = RunState(state_value)
        execute_research_stage.apply_async(
            args=(run_id, state.value), queue=research_queue_for_state(state)
        )
    return len(claimed)
