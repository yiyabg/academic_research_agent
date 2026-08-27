"""Resumable staged/background orchestration for local-paper analysis."""
# ruff: noqa: RUF001 - Chinese user-facing analysis prompts intentionally use full-width punctuation.

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.core.config import settings
from app.db.models.local_paper_analysis import LocalPaperAnalysisJob, LocalPaperAnalysisStage
from app.schemas.literature_research.local_library import (
    LocalPaperAnalysisCreate,
    LocalPaperRead,
    LocalPaperSearchRequest,
)
from app.services.literature_research.local_paper_library import LocalPaperLibraryService
from app.services.literature_research.object_store import get_research_object_store
from app.services.literature_research.paper_analysis_model_gateway import (
    ModelGatewayError,
    PaperAnalysisModelGateway,
)
from app.services.literature_research.paper_mindmap_service import PaperMindmapService

if TYPE_CHECKING:
    from app.services.literature_research.local_paper_analysis import LocalPaperAnalysisService

logger = logging.getLogger(__name__)
PAPER_STAGE = "PAPER"
SYNTHESIS_STAGE = "SYNTHESIS"
STAGE_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


class PaperEvidenceService:
    """Build a bounded per-paper evidence packet; no full prompt is logged."""

    @staticmethod
    def payload(paper: LocalPaperRead) -> dict[str, object]:
        return paper.model_dump(mode="json")

    @staticmethod
    def prompt(paper: LocalPaperRead, question: str) -> tuple[str, str]:
        sections = []
        for label, value in (("摘要", paper.abstract_text), ("引言", paper.introduction_text), ("结论", paper.conclusion_text)):
            if value:
                sections.append(f"{label}：{value[:700]}")
        for evidence in paper.evidence[:3]:
            text = (evidence.parent_text or evidence.text)[:550]
            sections.append(f"[p.{evidence.page_number}] {text}")
        system = (
            "你是严谨的学术分析助手。只能依据给定本地论文页码证据作答；"
            "缺失信息标记为[摘录不足]。用中文输出：研究问题、创新点、方法、结果/局限，"
            "每项附 p.N 证据，不超过 8 个要点。"
        )
        user = f"研究问题：{question}\n\n论文：{paper.title}\n\n证据：\n" + "\n\n".join(sections)
        return system, user


class PaperAnalysisReportService:
    @staticmethod
    def synthesis_prompt(question: str, successful: list[LocalPaperAnalysisStage], failed: list[LocalPaperAnalysisStage]) -> tuple[str, str]:
        summaries = []
        for stage in successful:
            paper = stage.evidence_json.get("paper", {})
            title = paper.get("title", "未命名论文") if isinstance(paper, dict) else "未命名论文"
            content = stage.result_json.get("content", "")
            summaries.append(f"## {title}\n{str(content)[:9000]}")
        failed_titles = []
        for stage in failed:
            paper = stage.evidence_json.get("paper", {})
            title = paper.get("title", "未命名论文") if isinstance(paper, dict) else "未命名论文"
            failed_titles.append(f"- {title}：{stage.normalized_error_code or '分析失败'}")
        system = (
            "你是论文综述编辑。仅综合各篇已完成的结构化分析，不补造证据。"
            "输出研究结论、逐篇创新点、横向比较、研究缺口，并保留页码证据。"
        )
        user = f"研究问题：{question}\n\n已完成逐篇分析：\n" + "\n\n".join(summaries)
        if failed_titles:
            user += "\n\n未完成论文（必须在报告中透明说明）：\n" + "\n".join(failed_titles)
        return system, user

    @staticmethod
    def partial_report(question: str, successful: list[LocalPaperAnalysisStage], failed: list[LocalPaperAnalysisStage]) -> str:
        lines = ["# 文献深度分析（部分完成）", "", f"**研究主题**：{question}", "", "## 已完成的逐篇分析", ""]
        for stage in successful:
            paper = stage.evidence_json.get("paper", {})
            title = paper.get("title", "未命名论文") if isinstance(paper, dict) else "未命名论文"
            lines += [f"### {title}", str(stage.result_json.get("content", "[无可用分析]")), ""]
        lines += ["## 未完成论文", ""]
        for stage in failed:
            paper = stage.evidence_json.get("paper", {})
            title = paper.get("title", "未命名论文") if isinstance(paper, dict) else "未命名论文"
            lines.append(f"- {title}：{stage.error_summary or '模型分析未完成'}")
        return "\n".join(lines)


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
        if job.execution_mode == "background" and not settings.LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE:
            await self._fail(job, "BACKGROUND_STORAGE_NOT_ALLOWED", "未授权模型服务临时保存后台任务。")
            return
        await self._prepare(job)
        if job.execution_mode == "background":
            await self._submit_background_available(job)
        else:
            await self._run_staged(job)

    async def _prepare(self, job: LocalPaperAnalysisJob) -> None:
        exists = await self.db.scalar(select(LocalPaperAnalysisStage.id).where(LocalPaperAnalysisStage.job_id == job.id).limit(1))
        if exists is not None:
            return
        await self.service._progress(job, "RETRIEVING", "RETRIEVING", {"execution_mode": job.execution_mode})
        request = LocalPaperAnalysisCreate.model_validate(job.request_json)
        search = await LocalPaperLibraryService(self.db).search(
            owner_id=job.owner_id,
            request=LocalPaperSearchRequest(query=request.query or request.question, paper_ids=request.paper_ids, limit=request.limit),
        )
        job.retrieval_run_id = search.retrieval_run_id
        job.source_versions_json = await self.service._source_versions(search.items)
        job.evidence_json = self.service._evidence_manifest(search.items)
        job.stage_total = len(search.items) + 1
        job.stage_index = 0
        job.stage = "EVIDENCE_READY"
        for index, paper in enumerate(search.items, start=1):
            payload = PaperEvidenceService.payload(paper)
            self.db.add(LocalPaperAnalysisStage(
                job_id=job.id,
                paper_id=paper.id,
                stage_type=PAPER_STAGE,
                stage_index=index,
                input_sha256=hashlib.sha256(_canonical(payload).encode()).hexdigest(),
                evidence_json={"paper": payload},
            ))
        self.db.add(LocalPaperAnalysisStage(
            job_id=job.id,
            paper_id=None,
            stage_type=SYNTHESIS_STAGE,
            stage_index=job.stage_total,
            status="BLOCKED",
            input_sha256=hashlib.sha256(job.question.encode()).hexdigest(),
        ))
        await self.service._progress(job, "ANALYZING", "EVIDENCE_READY", {"paper_count": len(search.items), "stage_total": job.stage_total})

    async def _run_staged(self, job: LocalPaperAnalysisJob) -> None:
        while True:
            stages = await self._paper_stages(job.id)
            pending = [stage for stage in stages if stage.status == "PENDING"]
            if pending:
                claimed = await self._claim(pending[: settings.LOCAL_PAPER_ANALYSIS_MAX_CONCURRENCY])
                results = await asyncio.gather(*(self._complete_paper(stage, job) for stage in claimed), return_exceptions=True)
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

    async def _complete_paper(self, stage: LocalPaperAnalysisStage, job: LocalPaperAnalysisJob) -> str:
        payload = stage.evidence_json.get("paper")
        paper = LocalPaperRead.model_validate(payload)
        system, user = PaperEvidenceService.prompt(paper, job.question)
        result = await self.gateway.complete(system_prompt=system, user_prompt=user, max_output_tokens=settings.LOCAL_PAPER_ANALYSIS_PAPER_MAX_OUTPUT_TOKENS)
        return result.content

    async def _store_stage_result(self, job: LocalPaperAnalysisJob, stage: LocalPaperAnalysisStage, result: object) -> None:
        current = await self.db.get(LocalPaperAnalysisStage, stage.id, with_for_update=True)
        if current is None or current.status != "RUNNING":
            return
        if isinstance(result, str):
            current.status = "SUCCEEDED"
            current.result_json = {"content": result}
            await self.service._record_attempt(job, stage=current, status="SUCCEEDED", latency_ms=None, error=None)
            job.stage_index += 1
            job.stage = f"PAPER_{current.stage_index}_COMPLETED"
            await self.service._progress(job, "ANALYZING", "PAPER_COMPLETED", {"stage_index": current.stage_index, "stage_total": job.stage_total})
            return
        error = result if isinstance(result, ModelGatewayError) else ModelGatewayError("PROVIDER_UNAVAILABLE", "模型服务暂不可用。", raw_summary=repr(result))
        await self.service._record_attempt(job, stage=current, status="FAILED", latency_ms=None, error=error)
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
        await self.service._progress(job, "ANALYZING", "PAPER_FAILED", {"stage_index": current.stage_index, "error_code": error.code})

    async def _synthesize_or_finalize(self, job: LocalPaperAnalysisJob) -> None:
        papers = await self._paper_stages(job.id)
        successful = [stage for stage in papers if stage.status == "SUCCEEDED"]
        failed = [stage for stage in papers if stage.status == "FAILED"]
        if not successful:
            await self._finalize(job, content=None, successful=successful, failed=failed, synthesis_error=None)
            return
        synthesis = await self.db.scalar(select(LocalPaperAnalysisStage).where(LocalPaperAnalysisStage.job_id == job.id, LocalPaperAnalysisStage.stage_type == SYNTHESIS_STAGE).with_for_update())
        if synthesis is None or synthesis.status in STAGE_TERMINAL:
            return
        synthesis.status = "RUNNING"
        synthesis.attempt_count += 1
        job.stage = "SYNTHESIZING"
        await self.service._progress(job, "SYNTHESIZING", "SYNTHESIZING", {"successful_papers": len(successful), "failed_papers": len(failed)})
        system, user = PaperAnalysisReportService.synthesis_prompt(job.question, successful, failed)
        try:
            response = await self.gateway.complete(system_prompt=system, user_prompt=user, max_output_tokens=settings.LOCAL_PAPER_ANALYSIS_SYNTHESIS_MAX_OUTPUT_TOKENS)
            synthesis.status = "SUCCEEDED"
            synthesis.result_json = {"content": response.content}
            await self.service._record_attempt(job, stage=synthesis, status="SUCCEEDED", latency_ms=response.latency_ms, error=None)
            await self._finalize(job, content=response.content, successful=successful, failed=failed, synthesis_error=None)
        except ModelGatewayError as error:
            await self.service._record_attempt(job, stage=synthesis, status="FAILED", latency_ms=None, error=error)
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
            await self._finalize(job, content=None, successful=successful, failed=failed, synthesis_error=error)

    async def _submit_background_available(self, job: LocalPaperAnalysisJob) -> None:
        stages = await self._paper_stages(job.id)
        active = sum(stage.status in {"SUBMITTED", "POLLING"} for stage in stages)
        for stage in [row for row in stages if row.status == "PENDING"][: max(0, settings.LOCAL_PAPER_ANALYSIS_MAX_CONCURRENCY - active)]:
            await self._submit_background_stage(job, stage)
        if any(stage.status in {"PENDING", "SUBMITTED", "POLLING"} for stage in stages):
            return
        successful = [stage for stage in stages if stage.status == "SUCCEEDED"]
        failed = [stage for stage in stages if stage.status == "FAILED"]
        if not successful:
            await self._finalize(job, content=None, successful=successful, failed=failed, synthesis_error=None)
            return
        synthesis = await self.db.scalar(
            select(LocalPaperAnalysisStage).where(
                LocalPaperAnalysisStage.job_id == job.id,
                LocalPaperAnalysisStage.stage_type == SYNTHESIS_STAGE,
            ).with_for_update()
        )
        if synthesis is not None and synthesis.status == "BLOCKED":
            synthesis.status = "PENDING"
            await self.db.commit()
        if synthesis is not None and synthesis.status == "PENDING":
            await self._submit_background_stage(job, synthesis)

    async def _submit_background_stage(self, job: LocalPaperAnalysisJob, stage: LocalPaperAnalysisStage) -> None:
        if stage.stage_type == SYNTHESIS_STAGE:
            papers = await self._paper_stages(job.id)
            system, user = PaperAnalysisReportService.synthesis_prompt(
                job.question,
                [row for row in papers if row.status == "SUCCEEDED"],
                [row for row in papers if row.status == "FAILED"],
            )
            output_limit = settings.LOCAL_PAPER_ANALYSIS_SYNTHESIS_MAX_OUTPUT_TOKENS
            job.stage = "SYNTHESIZING"
        else:
            payload = stage.evidence_json.get("paper")
            if not payload:
                return
            paper = LocalPaperRead.model_validate(payload)
            system, user = PaperEvidenceService.prompt(paper, job.question)
            output_limit = settings.LOCAL_PAPER_ANALYSIS_PAPER_MAX_OUTPUT_TOKENS
        try:
            response_id, status = await self.gateway.submit_background(
                system_prompt=system, user_prompt=user, max_output_tokens=output_limit
            )
        except ModelGatewayError as error:
            stage.status, stage.normalized_error_code, stage.error_summary = "FAILED", error.code, error.summary
            await self.service._record_attempt(job, stage=stage, status="FAILED", latency_ms=None, error=error)
            await self.db.commit()
            return
        now = datetime.now(UTC)
        stage.status, stage.provider_response_id, stage.provider_status = "SUBMITTED", response_id, status
        stage.submitted_at, stage.deadline_at, stage.next_poll_at = now, now + timedelta(seconds=settings.LOCAL_PAPER_ANALYSIS_BACKGROUND_TOTAL_DEADLINE_SECONDS), now + timedelta(seconds=5)
        job.provider_status = status
        await self.service._progress(job, "ANALYZING", "BACKGROUND_SUBMITTED", {"stage_index": stage.stage_index, "provider_status": status})
        self._schedule_poll(stage.id, 5)

    async def poll_background_stage(self, stage_id: UUID) -> None:
        stage = await self.db.get(LocalPaperAnalysisStage, stage_id, with_for_update=True)
        if stage is None or stage.status not in {"SUBMITTED", "POLLING"} or not stage.provider_response_id:
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
            stage.normalized_error_code = None if job.cancellation_requested else "UPSTREAM_GATEWAY_TIMEOUT"
            await self.db.commit()
            if job.cancellation_requested:
                await self._cancel_with_remote(job)
            else:
                await self.run(job.id)
            return
        try:
            status, output, error_message = await self.gateway.retrieve_background(stage.provider_response_id)
        except ModelGatewayError as error:
            stage.status, stage.normalized_error_code, stage.error_summary = "FAILED", error.code, error.summary
            await self.service._record_attempt(job, stage=stage, status="FAILED", latency_ms=None, error=error)
            await self.db.commit()
            await self.run(job.id)
            return
        stage.provider_status, stage.last_polled_at = status, datetime.now(UTC)
        job.provider_status = status
        if status == "completed" and output:
            stage.status, stage.result_json = "SUCCEEDED", {"content": output}
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
            await self.service._record_attempt(job, stage=stage, status="SUCCEEDED", latency_ms=None, error=None)
            await self.service._progress(job, "ANALYZING", "PAPER_COMPLETED", {"stage_index": stage.stage_index, "stage_total": job.stage_total})
            await self.run(job.id)
            return
        if status in {"failed", "cancelled", "incomplete"}:
            stage.status, stage.normalized_error_code, stage.error_summary = "FAILED", "PROVIDER_UNAVAILABLE", "后台模型任务未完成。"
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
        if job is None or job.execution_mode != "staged" or job.status in self.service.terminal_statuses:
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

    async def _finalize(self, job: LocalPaperAnalysisJob, *, content: str | None, successful: list[LocalPaperAnalysisStage], failed: list[LocalPaperAnalysisStage], synthesis_error: ModelGatewayError | None) -> None:
        if content is None and successful:
            content = PaperAnalysisReportService.partial_report(job.question, successful, failed)
        if content is None:
            job.status, job.stage = "FAILED", "FAILED"
            job.error_code = failed[0].normalized_error_code if failed else "PROVIDER_UNAVAILABLE"
            job.error_message = "模型服务未能完成任何论文分析；本地检索证据已保留。"
            await self.service._progress(job, "FAILED", "FAILED", {"error_code": job.error_code})
            return
        job.status = "COMPLETED" if not failed and synthesis_error is None else "PARTIAL"
        job.stage = job.status
        job.error_code = synthesis_error.code if synthesis_error else (failed[0].normalized_error_code if failed else None)
        job.error_message = synthesis_error.summary if synthesis_error else ("部分论文未能完成分析。" if failed else None)
        extension = "opml" if str(job.request_json.get("output_format", "markdown")) == "opml" else "md"
        if extension == "opml":
            content = PaperMindmapService()._markdown_to_opml(content, job.question)
        payload = content.encode()
        digest = hashlib.sha256(payload).hexdigest()
        key = f"local-library/{job.owner_id}/{job.session_id}/{job.id}/analysis-{digest[:16]}.{extension}"
        job.artifact_key = await get_research_object_store().put(key, payload, content_type="text/x-opml; charset=utf-8" if extension == "opml" else "text/markdown; charset=utf-8", metadata={"sha256": digest, "job-id": str(job.id)})
        job.artifact_sha256 = digest
        job.result_json = {"output_format": str(job.request_json.get("output_format", "markdown")), "generated_by_llm": bool(successful), "paper_count": len(successful) + len(failed), "completed_paper_count": len(successful), "failed_paper_count": len(failed), "artifact_sha256": digest, "content_preview": content[:200_000], "content_preview_truncated": len(content) > 200_000}
        await self.service._persist_turn(job, content)
        await self.service._progress(job, job.status, job.status, {"completed_paper_count": len(successful), "failed_paper_count": len(failed), "artifact_sha256": digest})
        await self.service._update_short_term_memory(job)

    async def _fail(self, job: LocalPaperAnalysisJob, code: str, message: str) -> None:
        job.status, job.stage, job.error_code, job.error_message = "FAILED", "FAILED", code, message
        await self.service._progress(job, "FAILED", "FAILED", {"error_code": code})

    async def _cancel_with_remote(self, job: LocalPaperAnalysisJob) -> None:
        stages = (await self.db.scalars(select(LocalPaperAnalysisStage).where(LocalPaperAnalysisStage.job_id == job.id, LocalPaperAnalysisStage.provider_response_id.is_not(None)))).all()
        for stage in stages:
            if stage.status in {"SUBMITTED", "POLLING"} and stage.provider_response_id:
                try:
                    await self.gateway.cancel_background(stage.provider_response_id)
                except ModelGatewayError:
                    logger.warning("Could not cancel provider response stage_id=%s", stage.id)
        await self.service._cancel(job)

    async def _paper_stages(self, job_id: UUID) -> list[LocalPaperAnalysisStage]:
        return (await self.db.scalars(select(LocalPaperAnalysisStage).where(LocalPaperAnalysisStage.job_id == job_id, LocalPaperAnalysisStage.stage_type == PAPER_STAGE).order_by(LocalPaperAnalysisStage.stage_index))).all()

    @staticmethod
    def _schedule_poll(stage_id: UUID, delay: int) -> None:
        from app.worker.tasks.local_paper_library_tasks import poll_local_paper_analysis_stage
        poll_local_paper_analysis_stage.apply_async(args=(str(stage_id),), queue="research-llm", countdown=delay)


def _canonical(value: object) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
