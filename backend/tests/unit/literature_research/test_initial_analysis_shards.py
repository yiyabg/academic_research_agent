"""Durable per-paper analysis shard and barrier regression tests."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.literature_research.pipeline_stages import ResearchPipelineStages
from app.worker.tasks.literature_research_tasks import (
    _prepare_initial_analysis_shards,
    analysis_shard_input_hash,
    execute_research_stage,
    stable_analysis_task_id,
)


@pytest.mark.anyio
async def test_prepare_initial_shards_caps_work_to_run_target_and_pins_full_identity() -> None:
    run_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        state="ANALYZING",
        target_count=2,
        protocol_hash="sha256:protocol",
        progress_json={},
    )
    rows = [(SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())) for _ in range(4)]
    db = AsyncMock()
    db.get = AsyncMock(return_value=run)

    @asynccontextmanager
    async def db_context():
        yield db

    created_tasks = [
        SimpleNamespace(id=uuid4(), status="PENDING"),
        SimpleNamespace(id=uuid4(), status="PENDING"),
    ]
    with (
        patch(
            "app.worker.tasks.literature_research_tasks.get_worker_db_context",
            db_context,
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.evidence_repo.list_analysis_ready_versions",
            new=AsyncMock(return_value=rows),
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.analysis_repo."
            "get_or_create_initial_analysis_task",
            new=AsyncMock(side_effect=[(created_tasks[0], True), (created_tasks[1], True)]),
        ) as create_task,
        patch(
            "app.worker.tasks.literature_research_tasks.analysis_repo."
            "summarize_initial_analysis_tasks",
            new=AsyncMock(
                return_value={
                    "total": 2,
                    "succeeded": 0,
                    "failed_terminal": 0,
                    "blocked": 0,
                    "pending": 2,
                    "running": 0,
                    "failed_retryable": 0,
                }
            ),
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.outbox_repo.create",
            new=AsyncMock(),
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.selected_llm_provider",
            return_value="openai_compatible",
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.selected_llm_model_identifier",
            return_value="openai_compatible[endpoint-hash]:gpt-5.5",
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.settings.AI_MODEL",
            "gpt-5.5",
        ),
    ):
        dispatch = await _prepare_initial_analysis_shards(run_id)

    assert len(dispatch) == 2
    assert create_task.await_count == 2
    assert run.progress_json["analysis_shard_total"] == 2
    assert run.progress_json["analysis_model_identifier"] == (
        "openai_compatible[endpoint-hash]:gpt-5.5"
    )


def test_analysis_shard_identity_is_stable_and_versioned() -> None:
    run_id = uuid4()
    work_id = uuid4()
    version_id = uuid4()
    first = analysis_shard_input_hash(
        protocol_hash="sha256:protocol",
        work_id=work_id,
        version_id=version_id,
        model_version="model-a",
    )
    assert first == analysis_shard_input_hash(
        protocol_hash="sha256:protocol",
        work_id=work_id,
        version_id=version_id,
        model_version="model-a",
    )
    assert first != analysis_shard_input_hash(
        protocol_hash="sha256:protocol",
        work_id=work_id,
        version_id=version_id,
        model_version="model-b",
    )
    assert stable_analysis_task_id(run_id, work_id) == f"research:{run_id}:analyze:{work_id}"


def test_analyzing_stage_dispatches_independent_stable_shards() -> None:
    run_id = uuid4()
    work_ids = [uuid4(), uuid4()]
    executions = [(str(uuid4()), str(work_id)) for work_id in work_ids]
    with (
        patch(
            "app.worker.tasks.literature_research_tasks._prepare_initial_analysis_shards",
            new=AsyncMock(return_value=executions),
        ),
        patch(
            "app.worker.tasks.literature_research_tasks.analyze_research_paper.apply_async"
        ) as analyze_apply,
        patch(
            "app.worker.tasks.literature_research_tasks.finalize_research_analysis.apply_async"
        ) as finalize_apply,
    ):
        result = execute_research_stage.apply(args=(str(run_id), "ANALYZING"), throw=True).get()

    assert result["status"] == "SHARDS_SCHEDULED"
    assert result["scheduled_shard_count"] == 2
    assert analyze_apply.call_count == 2
    for call, (execution_id, work_id) in zip(analyze_apply.call_args_list, executions, strict=True):
        assert call.kwargs == {
            "args": (execution_id,),
            "queue": "paper-analysis",
            "task_id": stable_analysis_task_id(run_id, UUID(work_id)),
        }
    finalize_apply.assert_called_once_with(args=(str(run_id),), queue="research-llm")


@pytest.mark.anyio
async def test_analysis_barrier_rejects_nonterminal_shards() -> None:
    counts = {
        "total": 2,
        "succeeded": 1,
        "failed_terminal": 0,
        "blocked": 0,
        "pending": 1,
        "running": 0,
        "failed_retryable": 0,
    }
    with (
        patch(
            "app.services.literature_research.pipeline_stages."
            "analysis_repository.summarize_initial_analysis_tasks",
            new=AsyncMock(return_value=counts),
        ),
        pytest.raises(RuntimeError, match="barrier is not complete"),
    ):
        await ResearchPipelineStages(AsyncMock()).analyze(SimpleNamespace(id=uuid4()))


@pytest.mark.anyio
async def test_analysis_barrier_counts_success_and_terminal_failures() -> None:
    run = SimpleNamespace(id=uuid4())
    counts = {
        "total": 3,
        "succeeded": 2,
        "failed_terminal": 1,
        "blocked": 0,
        "pending": 0,
        "running": 0,
        "failed_retryable": 0,
    }
    with (
        patch(
            "app.services.literature_research.pipeline_stages."
            "analysis_repository.summarize_initial_analysis_tasks",
            new=AsyncMock(return_value=counts),
        ),
        patch(
            "app.services.literature_research.pipeline_stages."
            "run_repository.set_counts_and_progress",
            new=AsyncMock(),
        ) as set_counts,
        patch(
            "app.services.literature_research.pipeline_stages."
            "analysis_repository.list_initial_analysis_tasks",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await ResearchPipelineStages(AsyncMock()).analyze(run)

    assert result["analysis_barrier_complete"] is True
    assert result["analyzed_count"] == 2
    assert result["analysis_failed_terminal_count"] == 1
    assert result["analysis_shards_succeeded"] == 2
    assert result["analysis_shards_failed_terminal"] == 1
    assert result["analysis_shards_blocked"] == 0
    set_counts.assert_awaited_once()
    assert set_counts.await_args.kwargs["analyzed_count"] == 2
    progress = set_counts.await_args.kwargs["progress"]
    assert progress["analysis_shards_succeeded"] == 2
    assert progress["analysis_shards_failed_terminal"] == 1
    assert progress["analysis_shards_blocked"] == 0
    assert progress["analysis_llm_usage"]["total"]["total_tokens"] == 0
