"""Resumable staged/background orchestration for local-paper analysis."""
# ruff: noqa: RUF001 - Chinese user-facing analysis prompts intentionally use full-width punctuation.

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.db.models.local_paper_analysis import LocalPaperAnalysisJob, LocalPaperAnalysisStage
from app.db.models.local_paper_library import LocalPaper, LocalPaperLibrary
from app.schemas.literature_research.local_library import (
    LocalPaperAnalysisCreate,
    LocalPaperSearchRequest,
)
from app.services.literature_research.local_paper_evidence import (
    LocalPaperEvidenceRetriever,
    PaperEvidenceResult,
)
from app.services.literature_research.local_paper_library import LocalPaperLibraryService
from app.services.literature_research.local_paper_retrieval import LocalPaperChunkRetriever
from app.services.literature_research.object_store import get_research_object_store
from app.services.literature_research.paper_analysis_model_gateway import (
    ModelGatewayError,
    ModelStageResult,
    PaperAnalysisModelGateway,
)

if TYPE_CHECKING:
    from app.services.literature_research.local_paper_analysis import LocalPaperAnalysisService

logger = logging.getLogger(__name__)
PAPER_STAGE = "PAPER"
SYNTHESIS_STAGE = "SYNTHESIS"
STAGE_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
OPML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>{title}</title></head>
  <body>
{outlines}
  </body>
</opml>"""


class EvidencePaperPayload(BaseModel):
    """Identity carried with every persisted paper-stage evidence packet."""

    id: UUID
    title: str
    citekey: str | None
    document_version_id: UUID


class EvidenceLineagePayload(BaseModel):
    """A cited child chunk and its parent-context lineage."""

    paper_id: UUID
    document_version_id: UUID
    section_id: UUID
    chunk_id: UUID
    section_type: str
    section_heading: str
    page_number: int
    child_text: str
    context_text: str
    rerank_score: float
    retrieval_pass: int


class PaperEvidencePayload(BaseModel):
    """The single JSON contract persisted by all analysis stage paths."""

    paper: EvidencePaperPayload
    question: str
    queries_used: list[str] = Field(default_factory=list)
    insufficient_evidence: bool
    evidence: list[EvidenceLineagePayload] = Field(default_factory=list)


class PaperEvidenceService:
    """Build a bounded per-paper evidence packet; no full prompt is logged."""

    @staticmethod
    def payload(paper_result: PaperEvidenceResult, question: str) -> dict[str, object]:
        """Create the sole persisted evidence contract used by every stage."""
        payload = PaperEvidencePayload(
            paper=EvidencePaperPayload(
                id=paper_result.paper_id,
                title=paper_result.paper_title,
                citekey=paper_result.paper_citekey,
                document_version_id=paper_result.document_version_id,
            ),
            question=question,
            queries_used=paper_result.queries_used,
            insufficient_evidence=paper_result.insufficient_evidence,
            evidence=[
                EvidenceLineagePayload(
                    paper_id=ev.paper_id,
                    document_version_id=ev.document_version_id,
                    section_id=ev.section_id,
                    chunk_id=ev.chunk_id,
                    section_type=ev.section_type,
                    section_heading=ev.section_heading,
                    page_number=ev.page_number,
                    child_text=ev.child_text,
                    context_text=ev.context_text,
                    rerank_score=ev.rerank_score,
                    retrieval_pass=ev.retrieval_pass,
                )
                for ev in paper_result.evidence
            ],
        )
        return payload.model_dump(mode="json")

    @staticmethod
    def prompt(
        evidence_payload: dict[str, object], question: str, mode: str = "focused"
    ) -> tuple[str, str]:
        """Build analysis prompt from evidence payload.

        Uses section-based evidence with context around child chunks.
        No truncated parent prefix, no deprecated abstract/intro/conclusion fields.
        """
        payload = PaperEvidencePayload.model_validate(evidence_payload)
        paper_title = payload.paper.title
        evidence_items = payload.evidence

        if not evidence_items:
            # No evidence available
            system = (
                "你是严谨的学术分析助手。当本地论文证据不足时，明确说明[摘录不足]，不编造内容。"
            )
            user = f"研究问题：{question}\n\n论文：{paper_title}\n\n证据：无可用证据"
            return system, user

        # Build evidence sections with handles [E1], [E2], etc.
        evidence_blocks = []
        for idx, ev in enumerate(evidence_items, 1):
            section_type = ev.section_type
            section_heading = ev.section_heading
            page = ev.page_number if ev.page_number is not None else "?"
            context = ev.context_text

            evidence_blocks.append(
                f"[E{idx}] {section_type} - {section_heading} (p.{page})\n{context}"
            )

        instructions = {
            "focused": "只回答用户问题，逐项给出明确答案、证据句柄和页码；不要补写背景或完整综述。",
            "comparative": "按可横向比较的字段提取本论文答案、方法、结果与局限，所有结论附证据句柄和页码。",
            "comprehensive": "输出背景、问题定义、方法、模型/系统结构、实验设置、主要结果、局限和与其他论文的关系；缺项标记[摘录不足]。",
        }
        system = (
            "你是严谨的学术分析助手。只能依据给定本地论文页码证据作答；"
            f"{instructions.get(mode, instructions['focused'])} 每项附证据句柄 [E1]、[E2] 等。"
        )
        user = f"研究问题：{question}\n\n论文：{paper_title}\n\n证据：\n" + "\n\n".join(
            evidence_blocks
        )
        return system, user


class PaperAnalysisReportService:
    @staticmethod
    def _paper_title(stage: LocalPaperAnalysisStage) -> str:
        """Read stage JSON through the shared evidence schema, never ad-hoc keys."""
        return PaperEvidencePayload.model_validate(stage.evidence_json).paper.title

    @staticmethod
    def synthesis_prompt(
        question: str,
        successful: list[LocalPaperAnalysisStage],
        failed: list[LocalPaperAnalysisStage],
        mode: str = "focused",
    ) -> tuple[str, str]:
        summaries = []
        for stage in successful:
            title = PaperAnalysisReportService._paper_title(stage)
            content = stage.result_json.get("content", "")
            summaries.append(f"## {title}\n{str(content)[:9000]}")
        failed_titles = []
        for stage in failed:
            title = PaperAnalysisReportService._paper_title(stage)
            failed_titles.append(f"- {title}：{stage.normalized_error_code or '分析失败'}")
        mode_instruction = {
            "focused": "仅直接回答研究问题，逐篇列出答案、证据句柄和页码。",
            "comparative": "输出严格按论文分列的横向比较表，禁止把一篇论文的证据归给另一篇。",
            "comprehensive": "输出背景、问题定义、方法、系统结构、实验、结果、局限和论文之间的关系。",
        }.get(mode, "仅直接回答研究问题，逐篇列出答案、证据句柄和页码。")
        system = "你是论文综述编辑。仅综合各篇已完成的结构化分析，不补造证据。" + mode_instruction
        user = f"研究问题：{question}\n\n已完成逐篇分析：\n" + "\n\n".join(summaries)
        if failed_titles:
            user += "\n\n未完成论文（必须在报告中透明说明）：\n" + "\n".join(failed_titles)
        return system, user

    @staticmethod
    def partial_report(
        question: str,
        successful: list[LocalPaperAnalysisStage],
        failed: list[LocalPaperAnalysisStage],
        mode: str = "focused",
    ) -> str:
        mode_heading = {
            "focused": "## 针对研究问题的逐篇回答",
            "comparative": "## 逐篇可比较字段（部分完成）",
            "comprehensive": "## 背景、方法、实验与局限（部分完成）",
        }.get(mode, "## 针对研究问题的逐篇回答")
        lines = [
            "# 文献深度分析（部分完成）",
            "",
            f"**研究主题**：{question}",
            "",
            mode_heading,
            "",
        ]
        for stage in successful:
            title = PaperAnalysisReportService._paper_title(stage)
            lines += [f"### {title}", str(stage.result_json.get("content", "[无可用分析]")), ""]
        lines += ["## 未完成论文", ""]
        for stage in failed:
            title = PaperAnalysisReportService._paper_title(stage)
            lines.append(f"- {title}：{stage.error_summary or '模型分析未完成'}")
        return "\n".join(lines)

    @staticmethod
    def markdown_to_opml(markdown: str, title: str) -> str:
        """Convert the unified analysis report to an OPML artifact.

        This is deliberately format-only: all content comes from the staged or
        background analysis result, so the deprecated mind-map service is not
        part of the active artifact path.
        """
        stack: list[int] = []
        outlines: list[str] = []

        def close_to(target_level: int) -> None:
            while stack and stack[-1] >= target_level:
                level = stack.pop()
                outlines.append("  " * level + "</outline>")

        for line in markdown.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---"):
                continue
            if stripped.startswith("#### "):
                level, text = 4, stripped[5:]
            elif stripped.startswith("### "):
                level, text = 3, stripped[4:]
            elif stripped.startswith("## "):
                level, text = 2, stripped[3:]
            elif stripped.startswith("# "):
                level, text = 1, stripped[2:]
            elif stripped.startswith(("- ", "* ")):
                level, text = (stack[-1] + 1) if stack else 5, stripped[2:]
            elif stripped.startswith("|"):
                level = (stack[-1] + 1) if stack else 5
                text = " | ".join(
                    cell.strip() for cell in stripped.strip("|").split("|") if cell.strip()
                )
            else:
                continue

            close_to(level)
            outlines.append(
                "  " * level + f'<outline text="{html_lib.escape(text.strip(), quote=True)}">'
            )
            stack.append(level)

        close_to(0)
        return OPML_TEMPLATE.format(
            title=html_lib.escape(title, quote=True), outlines="\n".join(outlines)
        )


class LocalPaperAnalysisOrchestrator:
    def __init__(self, service: LocalPaperAnalysisService) -> None:
        self.service = service
        self.db = service.db
        self.gateway = PaperAnalysisModelGateway()

    async def run(self, job_id: UUID) -> None:
        job = await self.db.get(LocalPaperAnalysisJob, job_id, with_for_update=True)
        if job is None or job.status in self.service.terminal_statuses:
            return
        if job.cancellation_requested:
            await self._cancel_with_remote(job)
            return
        if (
            job.execution_mode == "background"
            and not settings.LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE
        ):
            await self._fail(
                job, "BACKGROUND_STORAGE_NOT_ALLOWED", "未授权模型服务临时保存后台任务。"
            )
            return
        await self._prepare(job)
        if job.execution_mode == "background":
            await self._submit_background_available(job)
        else:
            await self._run_staged(job)

    async def _prepare(self, job: LocalPaperAnalysisJob) -> None:
        """Prepare analysis stages with evidence retrieval.

        If paper_ids provided: strict load and independent evidence retrieval per paper
        If not: discovery search first, then evidence retrieval
        """
        exists = await self.db.scalar(
            select(LocalPaperAnalysisStage.id)
            .where(LocalPaperAnalysisStage.job_id == job.id)
            .limit(1)
        )
        if exists is not None:
            return
        await self.service._progress(
            job, "RETRIEVING", "RETRIEVING", {"execution_mode": job.execution_mode}
        )
        request = LocalPaperAnalysisCreate.model_validate(job.request_json)

        # Step 1: Determine which papers to analyze
        if request.paper_ids:
            # User-selected papers: strict load by owner/library/INDEXED/active version
            library = await self.db.scalar(
                select(LocalPaperLibrary).where(LocalPaperLibrary.owner_id == job.owner_id)
            )
            if not library:
                await self._fail(job, "LIBRARY_NOT_FOUND", "本地论文库未初始化")
                return

            papers = (
                (
                    await self.db.execute(
                        select(LocalPaper).where(
                            LocalPaper.id.in_(request.paper_ids),
                            LocalPaper.library_id == library.id,
                            LocalPaper.status == "INDEXED",
                            LocalPaper.active_document_version_id.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

            # Preserve user-selected order
            paper_map = {p.id: p for p in papers}
            selected_papers = [paper_map[pid] for pid in request.paper_ids if pid in paper_map]

            if len(selected_papers) != len(request.paper_ids):
                await self._fail(
                    job, "INVALID_PAPER_SCOPE", "所选论文包含无权限、未索引或无活动版本的条目。"
                )
                return

            collection = library.qdrant_collection
        else:
            # Discovery: use query to find papers
            library_service = LocalPaperLibraryService(self.db)
            search = await library_service.search(
                owner_id=job.owner_id,
                request=LocalPaperSearchRequest(
                    query=request.query or request.question,
                    limit=request.limit,
                ),
            )
            selected_papers = [await self.db.get(LocalPaper, item.id) for item in search.items]
            selected_papers = [p for p in selected_papers if p is not None]

            if not selected_papers:
                await self._fail(job, "NO_PAPERS_FOUND", "未找到匹配的论文")
                return

            library = await self.db.scalar(
                select(LocalPaperLibrary).where(LocalPaperLibrary.owner_id == job.owner_id)
            )
            collection = library.qdrant_collection if library else ""

        # Step 2: Retrieve evidence for each paper using the evidence retriever
        # Need to instantiate chunk retriever and evidence retriever
        from app.services.literature_research.local_paper_reranker import BGERerankerV2M3HTTP
        from app.services.literature_research.local_paper_vector_index import LocalPaperVectorIndex

        vector_index = LocalPaperVectorIndex()
        reranker = BGERerankerV2M3HTTP()
        chunk_retriever = LocalPaperChunkRetriever(self.db, vector_index, reranker)
        evidence_retriever = LocalPaperEvidenceRetriever(self.db, chunk_retriever)

        paper_ids = [p.id for p in selected_papers]
        evidence_results = await evidence_retriever.retrieve_for_papers(
            paper_ids=paper_ids,
            question=request.question,
            query_context=request.query,
            collection=collection,
        )

        # Step 3: Create stages with evidence
        job.retrieval_run_id = None  # Could link to a retrieval run if needed
        job.source_versions_json = {
            str(p.id): str(p.active_document_version_id)
            for p in selected_papers
            if p.active_document_version_id
        }
        job.evidence_json = {
            "paper_count": len(selected_papers),
            "papers": [
                {"id": str(p.id), "title": p.title, "citekey": p.citekey} for p in selected_papers
            ],
        }
        job.stage_total = len(selected_papers) + 1
        job.stage_index = 0
        job.stage = "EVIDENCE_READY"

        for index, evidence_result in enumerate(evidence_results, start=1):
            payload = PaperEvidenceService.payload(evidence_result, request.question)
            self.db.add(
                LocalPaperAnalysisStage(
                    job_id=job.id,
                    paper_id=evidence_result.paper_id,
                    stage_type=PAPER_STAGE,
                    stage_index=index,
                    input_sha256=hashlib.sha256(_canonical(payload).encode()).hexdigest(),
                    evidence_json=payload,
                )
            )

        self.db.add(
            LocalPaperAnalysisStage(
                job_id=job.id,
                paper_id=None,
                stage_type=SYNTHESIS_STAGE,
                stage_index=job.stage_total,
                status="BLOCKED",
                input_sha256=hashlib.sha256(job.question.encode()).hexdigest(),
            )
        )
        await self.service._progress(
            job,
            "ANALYZING",
            "EVIDENCE_READY",
            {"paper_count": len(selected_papers), "stage_total": job.stage_total},
        )

    async def _run_staged(self, job: LocalPaperAnalysisJob) -> None:
        while True:
            stages = await self._paper_stages(job.id)
            pending = [stage for stage in stages if stage.status == "PENDING"]
            if pending:
                claimed = await self._claim(
                    pending[: settings.LOCAL_PAPER_ANALYSIS_MAX_CONCURRENCY]
                )
                results = await asyncio.gather(
                    *(self._complete_paper(stage, job) for stage in claimed), return_exceptions=True
                )
                for stage, result in zip(claimed, results, strict=True):
                    await self._store_stage_result(job, stage, result)
                continue
            if any(stage.status in {"PENDING", "RUNNING"} for stage in stages):
                return
            await self._synthesize_or_finalize(job)
            return

    async def _claim(self, stages: list[LocalPaperAnalysisStage]) -> list[LocalPaperAnalysisStage]:
        claimed: list[LocalPaperAnalysisStage] = []
        for original in stages:
            stage = await self.db.get(LocalPaperAnalysisStage, original.id, with_for_update=True)
            if stage is None or stage.status != "PENDING":
                continue
            stage.status = "RUNNING"
            stage.attempt_count += 1
            claimed.append(stage)
        await self.db.commit()
        return claimed

    async def _complete_paper(
        self, stage: LocalPaperAnalysisStage, job: LocalPaperAnalysisJob
    ) -> ModelStageResult:
        """Complete analysis for one paper using evidence payload."""
        evidence_payload = stage.evidence_json
        system, user = PaperEvidenceService.prompt(evidence_payload, job.question, job.mode)
        result = await self.gateway.complete(
            system_prompt=system,
            user_prompt=user,
            max_output_tokens=settings.LOCAL_PAPER_ANALYSIS_PAPER_MAX_OUTPUT_TOKENS,
        )
        return result

    async def _store_stage_result(
        self, job: LocalPaperAnalysisJob, stage: LocalPaperAnalysisStage, result: object
    ) -> None:
        current = await self.db.get(LocalPaperAnalysisStage, stage.id, with_for_update=True)
        if current is None or current.status != "RUNNING":
            return
        if isinstance(result, ModelStageResult):
            current.status = "SUCCEEDED"
            current.result_json = {"content": result.content}
            await self.service._record_attempt(
                job,
                stage=current,
                status="SUCCEEDED",
                latency_ms=result.latency_ms,
                error=None,
            )
            job.stage_index += 1
            job.stage = f"PAPER_{current.stage_index}_COMPLETED"
            await self.service._progress(
                job,
                "ANALYZING",
                "PAPER_COMPLETED",
                {"stage_index": current.stage_index, "stage_total": job.stage_total},
            )
            return
        error = (
            result
            if isinstance(result, ModelGatewayError)
            else ModelGatewayError(
                "PROVIDER_UNAVAILABLE", "模型服务暂不可用。", raw_summary=repr(result)
            )
        )
        await self.service._record_attempt(
            job, stage=current, status="FAILED", latency_ms=None, error=error
        )
        if current.attempt_count <= settings.LOCAL_PAPER_ANALYSIS_STAGE_MAX_RETRIES:
            current.status = "PENDING"
            current.normalized_error_code = error.code
            current.error_summary = error.summary
            await self.db.commit()
            return
        current.status = "FAILED"
        current.normalized_error_code = error.code
        current.error_summary = error.summary
        job.stage_index += 1
        job.stage = f"PAPER_{current.stage_index}_FAILED"
        await self.service._progress(
            job,
            "ANALYZING",
            "PAPER_FAILED",
            {"stage_index": current.stage_index, "error_code": error.code},
        )

    async def _synthesize_or_finalize(self, job: LocalPaperAnalysisJob) -> None:
        papers = await self._paper_stages(job.id)
        successful = [stage for stage in papers if stage.status == "SUCCEEDED"]
        failed = [stage for stage in papers if stage.status == "FAILED"]
        if not successful:
            await self._finalize(
                job, content=None, successful=successful, failed=failed, synthesis_error=None
            )
            return
        synthesis = await self.db.scalar(
            select(LocalPaperAnalysisStage)
            .where(
                LocalPaperAnalysisStage.job_id == job.id,
                LocalPaperAnalysisStage.stage_type == SYNTHESIS_STAGE,
            )
            .with_for_update()
        )
        if synthesis is None or synthesis.status in STAGE_TERMINAL:
            return
        synthesis.status = "RUNNING"
        synthesis.attempt_count += 1
        job.stage = "SYNTHESIZING"
        await self.service._progress(
            job,
            "SYNTHESIZING",
            "SYNTHESIZING",
            {"successful_papers": len(successful), "failed_papers": len(failed)},
        )
        system, user = PaperAnalysisReportService.synthesis_prompt(
            job.question, successful, failed, job.mode
        )
        try:
            response = await self.gateway.complete(
                system_prompt=system,
                user_prompt=user,
                max_output_tokens=settings.LOCAL_PAPER_ANALYSIS_SYNTHESIS_MAX_OUTPUT_TOKENS,
            )
            synthesis.status = "SUCCEEDED"
            synthesis.result_json = {"content": response.content}
            await self.service._record_attempt(
                job, stage=synthesis, status="SUCCEEDED", latency_ms=response.latency_ms, error=None
            )
            await self._finalize(
                job,
                content=response.content,
                successful=successful,
                failed=failed,
                synthesis_error=None,
            )
        except ModelGatewayError as error:
            await self.service._record_attempt(
                job, stage=synthesis, status="FAILED", latency_ms=None, error=error
            )
            if synthesis.attempt_count <= settings.LOCAL_PAPER_ANALYSIS_STAGE_MAX_RETRIES:
                synthesis.status = "BLOCKED"
                synthesis.normalized_error_code = error.code
                synthesis.error_summary = error.summary
                await self.db.commit()
                await self._synthesize_or_finalize(job)
                return
            synthesis.status = "FAILED"
            synthesis.normalized_error_code = error.code
            synthesis.error_summary = error.summary
            await self._finalize(
                job, content=None, successful=successful, failed=failed, synthesis_error=error
            )

    async def _submit_background_available(self, job: LocalPaperAnalysisJob) -> None:
        stages = await self._paper_stages(job.id)
        active = sum(stage.status in {"SUBMITTED", "POLLING"} for stage in stages)
        for stage in [row for row in stages if row.status == "PENDING"][
            : max(0, settings.LOCAL_PAPER_ANALYSIS_MAX_CONCURRENCY - active)
        ]:
            await self._submit_background_stage(job, stage)
        if any(stage.status in {"PENDING", "SUBMITTED", "POLLING"} for stage in stages):
            return
        successful = [stage for stage in stages if stage.status == "SUCCEEDED"]
        failed = [stage for stage in stages if stage.status == "FAILED"]
        if not successful:
            await self._finalize(
                job, content=None, successful=successful, failed=failed, synthesis_error=None
            )
            return
        synthesis = await self.db.scalar(
            select(LocalPaperAnalysisStage)
            .where(
                LocalPaperAnalysisStage.job_id == job.id,
                LocalPaperAnalysisStage.stage_type == SYNTHESIS_STAGE,
            )
            .with_for_update()
        )
        if synthesis is not None and synthesis.status == "BLOCKED":
            synthesis.status = "PENDING"
            await self.db.commit()
        if synthesis is not None and synthesis.status == "PENDING":
            await self._submit_background_stage(job, synthesis)

    async def _submit_background_stage(
        self, job: LocalPaperAnalysisJob, stage: LocalPaperAnalysisStage
    ) -> None:
        if stage.stage_type == SYNTHESIS_STAGE:
            papers = await self._paper_stages(job.id)
            system, user = PaperAnalysisReportService.synthesis_prompt(
                job.question,
                [row for row in papers if row.status == "SUCCEEDED"],
                [row for row in papers if row.status == "FAILED"],
                job.mode,
            )
            output_limit = settings.LOCAL_PAPER_ANALYSIS_SYNTHESIS_MAX_OUTPUT_TOKENS
            job.stage = "SYNTHESIZING"
        else:
            if not PaperEvidencePayload.model_validate(stage.evidence_json).paper.title:
                return
            system, user = PaperEvidenceService.prompt(stage.evidence_json, job.question, job.mode)
            output_limit = settings.LOCAL_PAPER_ANALYSIS_PAPER_MAX_OUTPUT_TOKENS
        try:
            response_id, status = await self.gateway.submit_background(
                system_prompt=system, user_prompt=user, max_output_tokens=output_limit
            )
        except ModelGatewayError as error:
            stage.status, stage.normalized_error_code, stage.error_summary = (
                "FAILED",
                error.code,
                error.summary,
            )
            await self.service._record_attempt(
                job, stage=stage, status="FAILED", latency_ms=None, error=error
            )
            await self.db.commit()
            return
        now = datetime.now(UTC)
        stage.status, stage.provider_response_id, stage.provider_status = (
            "SUBMITTED",
            response_id,
            status,
        )
        stage.submitted_at, stage.deadline_at, stage.next_poll_at = (
            now,
            now
            + timedelta(seconds=settings.LOCAL_PAPER_ANALYSIS_BACKGROUND_TOTAL_DEADLINE_SECONDS),
            now + timedelta(seconds=5),
        )
        job.provider_status = status
        await self.service._progress(
            job,
            "ANALYZING",
            "BACKGROUND_SUBMITTED",
            {"stage_index": stage.stage_index, "provider_status": status},
        )
        self._schedule_poll(stage.id, 5)

    async def poll_background_stage(self, stage_id: UUID) -> None:
        stage = await self.db.get(LocalPaperAnalysisStage, stage_id, with_for_update=True)
        if (
            stage is None
            or stage.status not in {"SUBMITTED", "POLLING"}
            or not stage.provider_response_id
        ):
            return
        job = await self.db.get(LocalPaperAnalysisJob, stage.job_id, with_for_update=True)
        if job is None:
            return
        now = datetime.now(UTC)
        # A duplicate Celery delivery must not produce an early extra provider
        # retrieve.  PostgreSQL's next_poll_at is the durable scheduling source.
        if stage.next_poll_at and stage.next_poll_at > now:
            return
        if job.cancellation_requested or (stage.deadline_at and stage.deadline_at <= now):
            await self.gateway.cancel_background(stage.provider_response_id)
            stage.status = "CANCELLED" if job.cancellation_requested else "FAILED"
            stage.normalized_error_code = (
                None if job.cancellation_requested else "UPSTREAM_GATEWAY_TIMEOUT"
            )
            await self.db.commit()
            if job.cancellation_requested:
                await self._cancel_with_remote(job)
            else:
                await self.run(job.id)
            return
        try:
            status, output, error_message = await self.gateway.retrieve_background(
                stage.provider_response_id
            )
        except ModelGatewayError as error:
            stage.status, stage.normalized_error_code, stage.error_summary = (
                "FAILED",
                error.code,
                error.summary,
            )
            await self.service._record_attempt(
                job, stage=stage, status="FAILED", latency_ms=None, error=error
            )
            await self.db.commit()
            await self.run(job.id)
            return
        stage.provider_status, stage.last_polled_at = status, datetime.now(UTC)
        job.provider_status = status
        if status == "completed" and output:
            stage.status, stage.result_json = "SUCCEEDED", {"content": output}
            await self.service._record_attempt(
                job, stage=stage, status="SUCCEEDED", latency_ms=None, error=None
            )
            if stage.stage_type == SYNTHESIS_STAGE:
                papers = await self._paper_stages(job.id)
                await self._finalize(
                    job,
                    content=output,
                    successful=[row for row in papers if row.status == "SUCCEEDED"],
                    failed=[row for row in papers if row.status == "FAILED"],
                    synthesis_error=None,
                )
                return
            job.stage_index += 1
            await self.service._progress(
                job,
                "ANALYZING",
                "PAPER_COMPLETED",
                {"stage_index": stage.stage_index, "stage_total": job.stage_total},
            )
            await self.run(job.id)
            return
        if status in {"failed", "cancelled", "incomplete"}:
            stage.status, stage.normalized_error_code, stage.error_summary = (
                "FAILED",
                "PROVIDER_UNAVAILABLE",
                "后台模型任务未完成。",
            )
            await self.db.commit()
            await self.run(job.id)
            return
        stage.status = "POLLING"
        delay = min(30, 5 * max(1, stage.attempt_count + 1))
        stage.next_poll_at = datetime.now(UTC) + timedelta(seconds=delay)
        await self.db.commit()
        self._schedule_poll(stage.id, delay)

    async def recover_staged_stage(self, stage_id: UUID) -> bool:
        """Reclaim only a stale, persisted lease after a Worker disappearance."""
        stage = await self.db.get(LocalPaperAnalysisStage, stage_id, with_for_update=True)
        if stage is None or stage.status != "RUNNING":
            return False
        job = await self.db.get(LocalPaperAnalysisJob, stage.job_id, with_for_update=True)
        if (
            job is None
            or job.execution_mode != "staged"
            or job.status in self.service.terminal_statuses
        ):
            return False
        cutoff = datetime.now(UTC) - timedelta(
            seconds=settings.LOCAL_PAPER_ANALYSIS_STAGE_TIMEOUT_SECONDS
            + settings.LOCAL_PAPER_ANALYSIS_STAGE_RECOVERY_GRACE_SECONDS
        )
        if stage.updated_at and stage.updated_at > cutoff:
            return False
        if stage.attempt_count > settings.LOCAL_PAPER_ANALYSIS_STAGE_MAX_RETRIES:
            stage.status = "FAILED"
            stage.normalized_error_code = "WORKER_LEASE_EXPIRED"
            stage.error_summary = "分析执行进程中断，重试次数已用尽。"
        else:
            stage.status = "BLOCKED" if stage.stage_type == SYNTHESIS_STAGE else "PENDING"
            stage.normalized_error_code = "WORKER_LEASE_EXPIRED"
            stage.error_summary = "分析执行进程中断，任务已从持久化状态恢复。"
        await self.db.commit()
        await self.run(job.id)
        return True

    async def _finalize(
        self,
        job: LocalPaperAnalysisJob,
        *,
        content: str | None,
        successful: list[LocalPaperAnalysisStage],
        failed: list[LocalPaperAnalysisStage],
        synthesis_error: ModelGatewayError | None,
    ) -> None:
        if content is None and successful:
            content = PaperAnalysisReportService.partial_report(
                job.question, successful, failed, job.mode
            )
        if content is None:
            job.status, job.stage = "FAILED", "FAILED"
            job.error_code = failed[0].normalized_error_code if failed else "PROVIDER_UNAVAILABLE"
            job.error_message = "模型服务未能完成任何论文分析；本地检索证据已保留。"
            await self.service._progress(job, "FAILED", "FAILED", {"error_code": job.error_code})
            return
        job.status = "COMPLETED" if not failed and synthesis_error is None else "PARTIAL"
        job.stage = job.status
        job.error_code = (
            synthesis_error.code
            if synthesis_error
            else (failed[0].normalized_error_code if failed else None)
        )
        job.error_message = (
            synthesis_error.summary
            if synthesis_error
            else ("部分论文未能完成分析。" if failed else None)
        )
        extension = (
            "opml" if str(job.request_json.get("output_format", "markdown")) == "opml" else "md"
        )
        if extension == "opml":
            content = PaperAnalysisReportService.markdown_to_opml(content, job.question)
        payload = content.encode()
        digest = hashlib.sha256(payload).hexdigest()
        key = f"local-library/{job.owner_id}/{job.session_id}/{job.id}/analysis-{digest[:16]}.{extension}"
        job.artifact_key = await get_research_object_store().put(
            key,
            payload,
            content_type="text/x-opml; charset=utf-8"
            if extension == "opml"
            else "text/markdown; charset=utf-8",
            metadata={"sha256": digest, "job-id": str(job.id)},
        )
        job.artifact_sha256 = digest
        job.result_json = {
            "output_format": str(job.request_json.get("output_format", "markdown")),
            "generated_by_llm": bool(successful),
            "paper_count": len(successful) + len(failed),
            "completed_paper_count": len(successful),
            "failed_paper_count": len(failed),
            "artifact_sha256": digest,
            "content_preview": content[:200_000],
            "content_preview_truncated": len(content) > 200_000,
        }
        await self.service._persist_turn(job, content)
        await self.service._progress(
            job,
            job.status,
            job.status,
            {
                "completed_paper_count": len(successful),
                "failed_paper_count": len(failed),
                "artifact_sha256": digest,
            },
        )
        await self.service._update_short_term_memory(job)

    async def _fail(self, job: LocalPaperAnalysisJob, code: str, message: str) -> None:
        job.status, job.stage, job.error_code, job.error_message = "FAILED", "FAILED", code, message
        await self.service._progress(job, "FAILED", "FAILED", {"error_code": code})

    async def _cancel_with_remote(self, job: LocalPaperAnalysisJob) -> None:
        stages = (
            await self.db.scalars(
                select(LocalPaperAnalysisStage).where(
                    LocalPaperAnalysisStage.job_id == job.id,
                    LocalPaperAnalysisStage.provider_response_id.is_not(None),
                )
            )
        ).all()
        for stage in stages:
            if stage.status in {"SUBMITTED", "POLLING"} and stage.provider_response_id:
                try:
                    await self.gateway.cancel_background(stage.provider_response_id)
                except ModelGatewayError:
                    logger.warning("Could not cancel provider response stage_id=%s", stage.id)
        await self.service._cancel(job)

    async def _paper_stages(self, job_id: UUID) -> list[LocalPaperAnalysisStage]:
        return (
            await self.db.scalars(
                select(LocalPaperAnalysisStage)
                .where(
                    LocalPaperAnalysisStage.job_id == job_id,
                    LocalPaperAnalysisStage.stage_type == PAPER_STAGE,
                )
                .order_by(LocalPaperAnalysisStage.stage_index)
            )
        ).all()

    @staticmethod
    def _schedule_poll(stage_id: UUID, delay: int) -> None:
        from app.worker.tasks.local_paper_library_tasks import poll_local_paper_analysis_stage

        poll_local_paper_analysis_stage.apply_async(
            args=(str(stage_id),), queue="research-llm", countdown=delay
        )


def _canonical(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
