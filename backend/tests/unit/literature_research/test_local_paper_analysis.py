import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.literature_research.local_library import LocalPaperAnalysisCreate, LocalPaperRead
from app.services.literature_research.local_paper_analysis import _event_payload, _hash_event
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
