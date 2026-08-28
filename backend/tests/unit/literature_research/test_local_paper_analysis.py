# ruff: noqa: RUF001 - Assertions intentionally match Chinese user-facing text.

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError

from app.api.routes.v1.literature_research.local_library_routes import analysis as analysis_routes
from app.schemas.literature_research.local_library import (
    LocalPaperAnalysisCreate,
    LocalPaperMindmapRequest,
)
from app.services.literature_research.local_paper_analysis import (
    _event_payload,
    _hash_event,
    _safe_error_message,
    _safe_report_content,
)
from app.services.literature_research.local_paper_analysis_orchestrator import (
    LocalPaperAnalysisOrchestrator,
    PaperAnalysisReportService,
    PaperEvidencePayload,
    PaperEvidenceService,
)
from app.services.literature_research.paper_analysis_model_gateway import (
    ModelGatewayError,
    ModelStageResult,
    PaperAnalysisModelGateway,
    normalize_model_error,
)
from app.worker import celery_app as celery_module
from app.worker.tasks import local_paper_library_tasks


def _evidence_payload(
    title: str, evidence: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return PaperEvidencePayload(
        paper={
            "id": uuid4(),
            "title": title,
            "citekey": "verified",
            "document_version_id": uuid4(),
        },
        question="问题",
        queries_used=["问题"],
        insufficient_evidence=False,
        evidence=evidence or [],
    ).model_dump(mode="json")


class _WorkerDatabaseContext:
    def __init__(self, db: object) -> None:
        self.db = db

    async def __aenter__(self) -> object:
        return self.db

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_unified_analysis_requires_a_visible_retrieval_scope() -> None:
    with pytest.raises(ValidationError, match="requires query or paper_ids"):
        LocalPaperAnalysisCreate(question="如何设计语义编码器")

    request = LocalPaperAnalysisCreate(
        question="如何设计语义编码器", paper_ids=[uuid4()], mode="focused"
    )
    assert request.output_format == "markdown"


def test_legacy_mindmap_route_uses_the_unified_analysis_job(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_job(body, response, db, current_user):
        captured["body"] = body
        captured["response"] = response
        captured["db"] = db
        captured["current_user"] = current_user
        return {"status": "accepted"}

    monkeypatch.setattr(analysis_routes, "_create_job", fake_create_job)
    response = Response()
    result = asyncio.run(
        analysis_routes.legacy_mindmap(
            LocalPaperMindmapRequest(query="语义通信", output_format="opml"),
            response,
            object(),
            SimpleNamespace(id=uuid4()),
        )
    )

    body = captured["body"]
    assert isinstance(body, LocalPaperAnalysisCreate)
    assert body.query == "语义通信"
    assert body.question == "语义通信"
    assert body.output_format == "opml"
    assert result == {"status": "accepted"}


def test_analysis_event_hash_includes_previous_event_and_plain_payload() -> None:
    job = SimpleNamespace(id=uuid4(), session_id=uuid4(), status="RETRIEVING")
    payload = _event_payload(job, sequence=2, event_type="RETRIEVING", detail={"papers": 3})

    first = _hash_event(None, payload)
    second = _hash_event(first, payload)

    assert payload["data"]["sequence"] == 2
    assert first != second
    assert len(first) == 64


def test_analysis_artifact_opml_conversion_is_format_only_and_escaped() -> None:
    opml = PaperAnalysisReportService.markdown_to_opml(
        "# 总结\n\n## 论文 <A>\n\n- 证据 & 结果", "主题 <安全>"
    )

    assert "<title>主题 &lt;安全&gt;</title>" in opml
    assert 'text="论文 &lt;A&gt;"' in opml
    assert 'text="证据 &amp; 结果"' in opml


def test_cloudflare_524_is_normalized_without_proxy_payload() -> None:
    error = RuntimeError("status_code: 524, ray_id: secret-proxy-diagnostic")
    normalized = normalize_model_error(error)

    assert normalized.code == "UPSTREAM_GATEWAY_TIMEOUT"
    assert normalized.summary == "上游模型服务未能在规定时间内完成响应（HTTP 524）。"
    assert "ray_id" not in normalized.summary


def test_historical_provider_payload_is_not_returned_to_user() -> None:
    message = "ModelHTTPError: https://proxy.example/v1 ray id=private-diagnostic"

    assert (
        _safe_error_message("PROVIDER_UNAVAILABLE", message)
        == "模型分析暂不可用，本地检索证据已保留。"
    )
    assert (
        _safe_error_message("UPSTREAM_GATEWAY_TIMEOUT", "status 524 response")
        == "上游模型服务未能在规定时间内完成响应。"
    )


def test_historical_report_proxy_diagnostic_is_redacted() -> None:
    report = (
        "# 文献分析报告\n\n"
        "> ⚠️ LLM深度分析不可用（ModelHTTPError: status_code: 524, "
        "ray_id: private, zone: proxy.example），以下为元数据概览\n\n"
        "## 本地证据\n\np.2: 可保留的页码证据"
    )

    safe = _safe_report_content(report)

    assert "private" not in safe
    assert "proxy.example" not in safe
    assert "本地证据" in safe
    assert "已保留的本地页码证据" in safe


def test_partial_report_names_failed_papers_instead_of_calling_metadata_analysis() -> None:
    successful = SimpleNamespace(
        evidence_json=_evidence_payload("已完成论文"), result_json={"content": "方法：证据 p.3"}
    )
    failed = SimpleNamespace(
        evidence_json=_evidence_payload("失败论文"), error_summary="模型服务暂不可用。"
    )

    report = PaperAnalysisReportService.partial_report("测试主题", [successful], [failed])

    assert "部分完成" in report
    assert "已完成论文" in report
    assert "失败论文" in report


def test_evidence_payload_has_one_validated_persisted_contract() -> None:
    payload = PaperEvidencePayload.model_validate(
        {
            "paper": {
                "id": str(uuid4()),
                "title": "可验证论文",
                "citekey": "verified",
                "document_version_id": str(uuid4()),
            },
            "question": "问题",
            "queries_used": ["问题"],
            "insufficient_evidence": False,
            "evidence": [
                {
                    "paper_id": str(uuid4()),
                    "document_version_id": str(uuid4()),
                    "section_id": str(uuid4()),
                    "chunk_id": str(uuid4()),
                    "section_type": "METHODS",
                    "section_heading": "Method",
                    "page_number": 3,
                    "child_text": "命中的 child",
                    "context_text": "带双向 parent 上下文的命中",
                    "rerank_score": 0.87,
                    "retrieval_pass": 1,
                }
            ],
        }
    )

    persisted = payload.model_dump(mode="json")

    assert PaperEvidencePayload.model_validate(persisted).paper.title == "可验证论文"
    assert persisted["evidence"][0]["retrieval_pass"] == 1


def test_analysis_mode_prompts_are_materially_different() -> None:
    payload = _evidence_payload(
        "可验证论文",
        [
            {
                "paper_id": uuid4(),
                "document_version_id": uuid4(),
                "section_id": uuid4(),
                "chunk_id": uuid4(),
                "section_type": "METHODS",
                "section_heading": "Method",
                "page_number": 3,
                "child_text": "命中 child",
                "context_text": "证据",
                "rerank_score": 0.8,
                "retrieval_pass": 1,
            }
        ],
    )
    focused, _ = PaperEvidenceService.prompt(payload, "问题", "focused")
    comparative, _ = PaperEvidenceService.prompt(payload, "问题", "comparative")
    comprehensive, _ = PaperEvidenceService.prompt(payload, "问题", "comprehensive")

    assert len({focused, comparative, comprehensive}) == 3
    assert "横向比较" in comparative
    assert "实验设置" in comprehensive


def test_partial_reports_have_mode_specific_structures() -> None:
    completed = SimpleNamespace(
        evidence_json=_evidence_payload("已完成论文"), result_json={"content": "证据结论"}
    )
    focused = PaperAnalysisReportService.partial_report("问题", [completed], [], "focused")
    comparative = PaperAnalysisReportService.partial_report("问题", [completed], [], "comparative")
    comprehensive = PaperAnalysisReportService.partial_report(
        "问题", [completed], [], "comprehensive"
    )

    assert len({focused, comparative, comprehensive}) == 3
    assert "可比较字段" in comparative
    assert "背景、方法、实验与局限" in comprehensive


def test_background_submission_persists_response_id_before_poll_schedule(monkeypatch) -> None:
    stage = SimpleNamespace(
        id=uuid4(),
        stage_type="PAPER",
        stage_index=1,
        evidence_json=_evidence_payload("可验证论文"),
        status="PENDING",
        provider_response_id=None,
        provider_status=None,
        submitted_at=None,
        deadline_at=None,
        next_poll_at=None,
    )
    job = SimpleNamespace(question="问题", mode="focused", provider_status=None, stage="ANALYZING")
    service = SimpleNamespace(
        db=SimpleNamespace(), _progress=AsyncMock(), _record_attempt=AsyncMock()
    )
    orchestrator = LocalPaperAnalysisOrchestrator(service)
    orchestrator.gateway = SimpleNamespace(
        submit_background=AsyncMock(return_value=("resp_1", "queued"))
    )
    scheduled: list[tuple[object, int]] = []
    monkeypatch.setattr(
        orchestrator, "_schedule_poll", lambda stage_id, delay: scheduled.append((stage_id, delay))
    )

    asyncio.run(orchestrator._submit_background_stage(job, stage))

    assert stage.status == "SUBMITTED"
    assert stage.provider_response_id == "resp_1"
    assert stage.next_poll_at is not None
    assert scheduled == [(stage.id, 5)]
    service._progress.assert_awaited_once()


def test_background_poll_retrieves_once_and_reschedules(monkeypatch) -> None:
    now = datetime.now(UTC)
    stage = SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        stage_type="PAPER",
        stage_index=1,
        status="SUBMITTED",
        provider_response_id="resp_1",
        provider_status="queued",
        next_poll_at=now - timedelta(seconds=1),
        deadline_at=now + timedelta(minutes=5),
        last_polled_at=None,
        result_json={},
        attempt_count=0,
    )
    job = SimpleNamespace(
        id=stage.job_id, cancellation_requested=False, provider_status=None, stage_index=0
    )
    db = SimpleNamespace(get=AsyncMock(side_effect=[stage, job]), commit=AsyncMock())
    service = SimpleNamespace(db=db, _progress=AsyncMock(), _record_attempt=AsyncMock())
    orchestrator = LocalPaperAnalysisOrchestrator(service)
    orchestrator.gateway = SimpleNamespace(
        retrieve_background=AsyncMock(return_value=("in_progress", None, None))
    )
    scheduled: list[tuple[object, int]] = []
    monkeypatch.setattr(
        orchestrator, "_schedule_poll", lambda stage_id, delay: scheduled.append((stage_id, delay))
    )

    asyncio.run(orchestrator.poll_background_stage(stage.id))

    orchestrator.gateway.retrieve_background.assert_awaited_once_with("resp_1")
    assert stage.status == "POLLING"
    assert scheduled == [(stage.id, 5)]
    db.commit.assert_awaited_once()


def test_background_duplicate_poll_before_next_due_time_is_idempotent() -> None:
    now = datetime.now(UTC)
    stage = SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        status="SUBMITTED",
        provider_response_id="resp_1",
        next_poll_at=now + timedelta(minutes=1),
    )
    job = SimpleNamespace(cancellation_requested=False)
    db = SimpleNamespace(get=AsyncMock(side_effect=[stage, job]))
    service = SimpleNamespace(db=db)
    orchestrator = LocalPaperAnalysisOrchestrator(service)
    orchestrator.gateway = SimpleNamespace(retrieve_background=AsyncMock())

    asyncio.run(orchestrator.poll_background_stage(stage.id))

    orchestrator.gateway.retrieve_background.assert_not_awaited()


def test_background_deadline_cancels_provider_and_resumes_from_persisted_state() -> None:
    now = datetime.now(UTC)
    stage = SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        status="SUBMITTED",
        provider_response_id="resp_1",
        next_poll_at=now - timedelta(seconds=1),
        deadline_at=now - timedelta(seconds=1),
        normalized_error_code=None,
    )
    job = SimpleNamespace(id=stage.job_id, cancellation_requested=False)
    db = SimpleNamespace(get=AsyncMock(side_effect=[stage, job]), commit=AsyncMock())
    service = SimpleNamespace(db=db)
    orchestrator = LocalPaperAnalysisOrchestrator(service)
    orchestrator.gateway = SimpleNamespace(cancel_background=AsyncMock())
    orchestrator.run = AsyncMock()

    asyncio.run(orchestrator.poll_background_stage(stage.id))

    orchestrator.gateway.cancel_background.assert_awaited_once_with("resp_1")
    assert stage.status == "FAILED"
    assert stage.normalized_error_code == "UPSTREAM_GATEWAY_TIMEOUT"
    orchestrator.run.assert_awaited_once_with(job.id)


def test_background_synthesis_uses_persisted_paper_results_then_finalizes() -> None:
    now = datetime.now(UTC)
    stage = SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        stage_type="SYNTHESIS",
        status="SUBMITTED",
        provider_response_id="synth_1",
        next_poll_at=now - timedelta(seconds=1),
        deadline_at=now + timedelta(minutes=5),
        provider_status="queued",
        last_polled_at=None,
        result_json={},
    )
    job = SimpleNamespace(id=stage.job_id, cancellation_requested=False, provider_status=None)
    completed_paper = SimpleNamespace(status="SUCCEEDED")
    failed_paper = SimpleNamespace(status="FAILED")
    db = SimpleNamespace(get=AsyncMock(side_effect=[stage, job]))
    service = SimpleNamespace(db=db, _record_attempt=AsyncMock())
    orchestrator = LocalPaperAnalysisOrchestrator(service)
    orchestrator.gateway = SimpleNamespace(
        retrieve_background=AsyncMock(return_value=("completed", "综合结果", None))
    )
    orchestrator._paper_stages = AsyncMock(return_value=[completed_paper, failed_paper])
    orchestrator._finalize = AsyncMock()

    asyncio.run(orchestrator.poll_background_stage(stage.id))

    assert stage.result_json == {"content": "综合结果"}
    service._record_attempt.assert_awaited_once()
    orchestrator._finalize.assert_awaited_once_with(
        job,
        content="综合结果",
        successful=[completed_paper],
        failed=[failed_paper],
        synthesis_error=None,
    )


def test_background_not_supported_is_preserved_as_a_safe_code() -> None:
    stage = SimpleNamespace(
        id=uuid4(),
        stage_type="PAPER",
        stage_index=1,
        evidence_json=_evidence_payload("可验证论文"),
        status="PENDING",
        normalized_error_code=None,
        error_summary=None,
    )
    job = SimpleNamespace(question="问题", mode="focused", stage="ANALYZING")
    db = SimpleNamespace(commit=AsyncMock())
    service = SimpleNamespace(db=db, _progress=AsyncMock(), _record_attempt=AsyncMock())
    orchestrator = LocalPaperAnalysisOrchestrator(service)
    orchestrator.gateway = SimpleNamespace(
        submit_background=AsyncMock(
            side_effect=ModelGatewayError(
                "BACKGROUND_NOT_SUPPORTED", "当前模型提供商不支持后台分析模式。"
            )
        )
    )

    asyncio.run(orchestrator._submit_background_stage(job, stage))

    assert stage.status == "FAILED"
    assert stage.normalized_error_code == "BACKGROUND_NOT_SUPPORTED"


def test_background_gateway_uses_fake_async_client_with_ephemeral_storage_disabled(
    monkeypatch,
) -> None:
    response = SimpleNamespace(id="resp_1", status="queued")
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(return_value=response),
            retrieve=AsyncMock(
                return_value=SimpleNamespace(status="completed", output_text="完成报告", error=None)
            ),
            cancel=AsyncMock(),
        ),
        close=AsyncMock(),
    )
    gateway = PaperAnalysisModelGateway()
    monkeypatch.setattr(gateway, "_background_client", lambda **_kwargs: fake_client)

    submitted = asyncio.run(
        gateway.submit_background(system_prompt="系统", user_prompt="用户", max_output_tokens=120)
    )
    polled = asyncio.run(gateway.retrieve_background("resp_1"))
    asyncio.run(gateway.cancel_background("resp_1"))

    assert submitted == ("resp_1", "queued")
    assert polled == ("completed", "完成报告", None)
    assert fake_client.responses.create.await_args.kwargs["background"] is True
    assert fake_client.responses.create.await_args.kwargs["store"] is False
    fake_client.responses.retrieve.assert_awaited_once_with("resp_1")
    fake_client.responses.cancel.assert_awaited_once_with("resp_1")


def test_background_recovery_reschedules_due_polls_on_research_llm_queue(monkeypatch) -> None:
    stage_ids = [uuid4(), uuid4()]
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: stage_ids)),
    )
    dispatched = Mock()
    monkeypatch.setattr(
        local_paper_library_tasks,
        "get_worker_db_context",
        lambda: _WorkerDatabaseContext(db),
    )
    monkeypatch.setattr(
        local_paper_library_tasks.poll_local_paper_analysis_stage,
        "apply_async",
        dispatched,
    )

    count = local_paper_library_tasks.recover_local_paper_analysis_background.run()

    assert count == 2
    assert dispatched.call_args_list[0].kwargs == {
        "args": (str(stage_ids[0]),),
        "queue": "research-llm",
    }
    assert dispatched.call_args_list[1].kwargs == {
        "args": (str(stage_ids[1]),),
        "queue": "research-llm",
    }


def test_background_recovery_has_durable_llm_route_and_beat_schedule() -> None:
    task_name = "app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_background"

    assert celery_module.celery_app.conf.task_routes[task_name]["queue"] == "research-llm"
    assert (
        celery_module.celery_app.conf.beat_schedule["recover-local-paper-background-analysis"][
            "task"
        ]
        == task_name
    )


def test_a_failed_paper_stage_is_retried_once_without_failing_other_papers() -> None:
    stage = SimpleNamespace(
        id=uuid4(),
        status="RUNNING",
        attempt_count=1,
        normalized_error_code=None,
        error_summary=None,
        stage_index=1,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=stage), commit=AsyncMock())
    service = SimpleNamespace(
        db=db,
        _record_attempt=AsyncMock(),
        _progress=AsyncMock(),
    )
    job = SimpleNamespace(stage_index=0, stage="ANALYZING", stage_total=3)

    asyncio.run(
        LocalPaperAnalysisOrchestrator(service)._store_stage_result(
            job,
            stage,
            ModelGatewayError("UPSTREAM_GATEWAY_TIMEOUT", "上游模型服务超时。"),
        )
    )

    assert stage.status == "PENDING"
    assert stage.normalized_error_code == "UPSTREAM_GATEWAY_TIMEOUT"
    service._record_attempt.assert_awaited_once()
    service._progress.assert_not_awaited()


def test_successful_paper_stage_records_the_provider_latency() -> None:
    stage = SimpleNamespace(id=uuid4(), status="RUNNING", stage_index=1, result_json={})
    db = SimpleNamespace(get=AsyncMock(return_value=stage))
    service = SimpleNamespace(db=db, _record_attempt=AsyncMock(), _progress=AsyncMock())
    job = SimpleNamespace(stage_index=0, stage="ANALYZING", stage_total=2)

    asyncio.run(
        LocalPaperAnalysisOrchestrator(service)._store_stage_result(
            job, stage, ModelStageResult(content="按证据完成", latency_ms=317)
        )
    )

    assert stage.status == "SUCCEEDED"
    assert stage.result_json == {"content": "按证据完成"}
    assert service._record_attempt.await_args.kwargs["latency_ms"] == 317
