import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.literature_research.local_library import LocalPaperAnalysisCreate, LocalPaperRead
from app.services.literature_research.local_paper_analysis import (
    _event_payload,
    _hash_event,
    _safe_error_message,
    _safe_report_content,
)
from app.services.literature_research.local_paper_analysis_orchestrator import (
    LocalPaperAnalysisOrchestrator,
    PaperAnalysisReportService,
)
from app.services.literature_research.paper_analysis_model_gateway import (
    ModelGatewayError,
    normalize_model_error,
)
from app.services.literature_research.paper_mindmap_service import PaperMindmapService


def test_unified_analysis_requires_a_visible_retrieval_scope() -> None:
    with pytest.raises(ValidationError, match="requires query or paper_ids"):
        LocalPaperAnalysisCreate(question="如何设计语义编码器")

    request = LocalPaperAnalysisCreate(
        question="如何设计语义编码器", paper_ids=[uuid4()], mode="focused"
    )
    assert request.output_format == "markdown"


def test_analysis_event_hash_includes_previous_event_and_plain_payload() -> None:
    job = SimpleNamespace(id=uuid4(), session_id=uuid4(), status="RETRIEVING")
    payload = _event_payload(job, sequence=2, event_type="RETRIEVING", detail={"papers": 3})

    first = _hash_event(None, payload)
    second = _hash_event(first, payload)

    assert payload["data"]["sequence"] == 2
    assert first != second
    assert len(first) == 64


def test_deep_analysis_reports_truthful_evidence_fallback_when_llm_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_research.paper_mindmap_service.llm_is_configured", lambda: False
    )
    paper = LocalPaperRead(
        id=uuid4(),
        citekey="paper",
        title="A local paper",
        authors=["Author"],
        bibtex_type="article",
        source_kind="pdf",
        relative_source_path="paper.pdf",
    )

    result = asyncio.run(
        PaperMindmapService().analyze_detailed(papers=[paper], question="测试问题")
    )

    assert result.generated_by_llm is False
    assert result.fallback_reason == "LLM_NOT_CONFIGURED"
    assert "元数据版" in result.content


def test_deep_analysis_persists_a_non_empty_timeout_reason(monkeypatch) -> None:
    paper = LocalPaperRead(
        id=uuid4(),
        citekey="paper",
        title="A local paper",
        authors=["Author"],
        bibtex_type="article",
        source_kind="pdf",
        relative_source_path="paper.pdf",
    )

    async def raise_timeout(_self, **_kwargs):
        raise TimeoutError()

    monkeypatch.setattr(PaperMindmapService, "_deep_analyze_via_llm", raise_timeout)
    result = asyncio.run(
        PaperMindmapService().analyze_detailed(
            papers=[paper], question="测试问题", model=object(), timeout_seconds=180
        )
    )

    assert result.generated_by_llm is False
    assert result.fallback_reason == "TIMEOUT: 代理在 180 秒内未返回模型结果（请求已安全取消）"
    assert "LLM深度分析不可用（TIMEOUT:" in result.content


def test_cloudflare_524_is_normalized_without_proxy_payload() -> None:
    error = RuntimeError("status_code: 524, ray_id: secret-proxy-diagnostic")
    normalized = normalize_model_error(error)

    assert normalized.code == "UPSTREAM_GATEWAY_TIMEOUT"
    assert normalized.summary == "上游模型服务未能在规定时间内完成响应（HTTP 524）。"
    assert "ray_id" not in normalized.summary


def test_historical_provider_payload_is_not_returned_to_user() -> None:
    message = "ModelHTTPError: https://proxy.example/v1 ray id=private-diagnostic"

    assert _safe_error_message("PROVIDER_UNAVAILABLE", message) == "模型分析暂不可用，本地检索证据已保留。"
    assert _safe_error_message("UPSTREAM_GATEWAY_TIMEOUT", "status 524 response") == "上游模型服务未能在规定时间内完成响应。"


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
        evidence_json={"paper": {"title": "已完成论文"}}, result_json={"content": "方法：证据 p.3"}
    )
    failed = SimpleNamespace(
        evidence_json={"paper": {"title": "失败论文"}}, error_summary="模型服务暂不可用。"
    )

    report = PaperAnalysisReportService.partial_report("测试主题", [successful], [failed])

    assert "部分完成" in report
    assert "已完成论文" in report
    assert "失败论文" in report


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
