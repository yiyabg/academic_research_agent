"""Unified asynchronous evidence-grounded analysis for the local paper corpus."""
# ruff: noqa: RUF001 - User-facing Chinese prompt text intentionally uses Chinese punctuation.

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.clients.redis import RedisClient
from app.core.config import settings
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.models.local_paper_analysis import (
    LocalPaperAnalysisEvent,
    LocalPaperAnalysisJob,
    LocalPaperAnalysisLLMAttempt,
    LocalPaperAnalysisSession,
    LocalPaperAnalysisTurn,
    LocalPaperLibraryProjectGrant,
    LocalPaperMemoryCandidate,
)
from app.db.models.local_paper_library import (
    LocalPaper,
    LocalPaperDocumentVersion,
    LocalPaperLibrary,
)
from app.repositories.literature_research import memory as memory_repository
from app.schemas.literature_research.local_library import (
    LocalPaperAnalysisCreate,
    LocalPaperAnalysisEventRead,
    LocalPaperAnalysisJobRead,
    LocalPaperAnalysisSessionCreate,
    LocalPaperAnalysisSessionRead,
    LocalPaperMemoryCandidateCreate,
    LocalPaperMemoryCandidateRead,
)
from app.schemas.literature_research.memory import ResearchProfileConfirm, SessionMemoryWrite
from app.services.literature_research.local_paper_library import LocalPaperLibraryService
from app.services.literature_research.object_store import get_research_object_store
from app.services.literature_research.session_memory import ResearchSessionMemoryService
from app.services.llm_provider import (
    selected_llm_model_identifier,
    selected_llm_provider,
)

logger = logging.getLogger(__name__)
TERMINAL_JOB_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}


def _safe_error_message(code: str | None, message: str | None) -> str | None:
    """Never return historical proxy payloads through a user-facing read API."""
    if not message:
        return None
    if code == "UPSTREAM_GATEWAY_TIMEOUT" or "524" in message:
        return "上游模型服务未能在规定时间内完成响应。"
    if code in {"PROVIDER_UNAVAILABLE", "QUEUE_UNAVAILABLE"}:
        return "模型分析暂不可用，本地检索证据已保留。"
    if code == "BACKGROUND_STORAGE_NOT_ALLOWED":
        return "后台分析未获管理员授权。"
    # Old jobs may contain provider JSON, host names or exception trace text.
    suspicious = ("http", "traceback", "error:", "exception", "ray id", "cloudflare")
    if any(token in message.casefold() for token in suspicious):
        return "模型分析未完成，本地检索证据已保留。"
    return message[:500]


def _safe_report_content(content: str) -> str:
    """Redact legacy provider diagnostics from reports returned to users.

    Old metadata fallbacks embedded the complete proxy response in a markdown
    warning line.  The raw diagnostic remains in restricted audit storage, but
    neither the inline preview nor the downloaded report may expose it.
    """
    warning = re.compile(
        r"(?im)^>\s*⚠️\s*LLM深度分析不可用.*(?:modelhttperror|cloudflare|status[_ ]?code:\s*524|ray_id).*$(?:\n)?"
    )
    return warning.sub("> ⚠️ 模型深度分析未完成；以下仅展示已保留的本地页码证据。\n", content)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_event(previous_hash: str | None, payload: dict[str, object]) -> str:
    return hashlib.sha256(f"{previous_hash or ''}:{_canonical_json(payload)}".encode()).hexdigest()


def _event_payload(
    job: LocalPaperAnalysisJob, *, sequence: int, event_type: str, detail: dict[str, object]
) -> dict[str, object]:
    return {
        "type": "local_paper_analysis_event",
        "data": {
            "job_id": str(job.id),
            "session_id": str(job.session_id),
            "sequence": sequence,
            "event_type": event_type,
            "status": job.status,
            "detail": detail,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }


async def _publish_event(payload: dict[str, object]) -> None:
    """Fan-out only committed events; PostgreSQL remains replay authority."""
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("job_id"), str):
        logger.warning("Refusing malformed local-paper analysis event")
        return
    redis = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    try:
        await redis.publish(
            f"local_paper_analysis:{data['job_id']}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        logger.warning(
            "Analysis event persisted but could not be published: %s", type(exc).__name__
        )
    finally:
        await redis.aclose()


class LocalPaperAnalysisService:
    """Job state machine shared by focused Q&A and multi-paper deep analysis."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    terminal_statuses = TERMINAL_JOB_STATUSES

    async def create_session(
        self, *, owner_id: UUID, body: LocalPaperAnalysisSessionCreate
    ) -> LocalPaperAnalysisSession:
        library = await self._owned_library(owner_id)
        if body.project_id is not None:
            await self._assert_project_granted(library.id, body.project_id, owner_id)
        row = LocalPaperAnalysisSession(
            library_id=library.id,
            owner_id=owner_id,
            project_id=body.project_id,
            title=body.title,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def create_job(
        self, *, owner_id: UUID, body: LocalPaperAnalysisCreate
    ) -> tuple[LocalPaperAnalysisJob, bool]:
        library = await self._owned_library(owner_id)
        if (
            settings.LOCAL_PAPER_ANALYSIS_EXECUTION_MODE == "background"
            and not settings.LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE
        ):
            raise ValueError("BACKGROUND_STORAGE_NOT_ALLOWED")
        # Serialize submissions for one private library.  Client request IDs
        # protect retries from one browser, while this lock also protects
        # double-clicks, stale tabs, and concurrent browser sessions.
        library = await self.db.scalar(
            select(LocalPaperLibrary).where(LocalPaperLibrary.id == library.id).with_for_update()
        )
        if library is None:
            raise NotFoundError("本地文献库不存在")
        if body.client_request_id:
            existing = await self.db.scalar(
                select(LocalPaperAnalysisJob).where(
                    LocalPaperAnalysisJob.owner_id == owner_id,
                    LocalPaperAnalysisJob.idempotency_key == body.client_request_id,
                )
            )
            if existing is not None:
                return existing, False
        normalized_request = body.model_dump(mode="json", exclude_none=True)
        normalized_request.pop("client_request_id", None)
        active_jobs = (
            await self.db.scalars(
                select(LocalPaperAnalysisJob).where(
                    LocalPaperAnalysisJob.owner_id == owner_id,
                    LocalPaperAnalysisJob.library_id == library.id,
                    LocalPaperAnalysisJob.status.not_in(TERMINAL_JOB_STATUSES),
                )
            )
        ).all()
        for active_job in active_jobs:
            active_request = dict(active_job.request_json)
            active_request.pop("client_request_id", None)
            if _canonical_json(active_request) == _canonical_json(normalized_request):
                logger.info(
                    "Reusing active local-paper analysis job_id=%s instead of duplicate submission",
                    active_job.id,
                )
                return active_job, False
        session = await self._resolve_session(owner_id=owner_id, library=library, body=body)
        job = LocalPaperAnalysisJob(
            session_id=session.id,
            library_id=library.id,
            owner_id=owner_id,
            project_id=session.project_id,
            mode=body.mode.upper(),
            execution_mode=settings.LOCAL_PAPER_ANALYSIS_EXECUTION_MODE,
            question=body.question,
            request_json=body.model_dump(mode="json", exclude_none=True),
            idempotency_key=body.client_request_id,
        )
        self.db.add(job)
        await self.db.flush()
        await self._append_event(
            job,
            "QUEUED",
            {"stage": "QUEUED", "mode": job.mode, "execution_mode": job.execution_mode},
        )
        return job, True

    async def get_job(self, *, job_id: UUID, owner_id: UUID) -> LocalPaperAnalysisJob:
        job = await self.db.get(LocalPaperAnalysisJob, job_id)
        if job is None:
            raise NotFoundError("分析任务不存在")
        if job.owner_id != owner_id:
            raise AuthorizationError("无权访问该分析任务")
        safe_error = _safe_error_message(job.error_code, job.error_message)
        if safe_error != job.error_message:
            # Do not overwrite the audit row; its raw, access-controlled data
            # remains in the attempt table while the response model is safe.
            set_committed_value(job, "error_message", safe_error)
        # Backfill the read model for reports generated before inline previews
        # were introduced.  The durable L4 turn remains the source of truth.
        if job.status in {"COMPLETED", "PARTIAL"} and not job.result_json.get("content_preview"):
            turn = await self.db.scalar(
                select(LocalPaperAnalysisTurn).where(LocalPaperAnalysisTurn.job_id == job.id)
            )
            if turn is not None and turn.assistant_output:
                job.result_json = {
                    **job.result_json,
                    "content_preview": turn.assistant_output[:200_000],
                    "content_preview_truncated": len(turn.assistant_output) > 200_000,
                }
        preview = job.result_json.get("content_preview")
        if isinstance(preview, str):
            safe_preview = _safe_report_content(preview)
            if safe_preview != preview:
                set_committed_value(
                    job,
                    "result_json",
                    {**job.result_json, "content_preview": safe_preview},
                )
        return job

    async def get_session(self, *, session_id: UUID, owner_id: UUID) -> LocalPaperAnalysisSession:
        row = await self.db.get(LocalPaperAnalysisSession, session_id)
        if row is None:
            raise NotFoundError("分析会话不存在")
        if row.owner_id != owner_id:
            raise AuthorizationError("无权访问该分析会话")
        return row

    async def list_events(
        self, *, job_id: UUID, owner_id: UUID, after_sequence: int = 0
    ) -> list[LocalPaperAnalysisEvent]:
        await self.get_job(job_id=job_id, owner_id=owner_id)
        return (
            await self.db.scalars(
                select(LocalPaperAnalysisEvent)
                .where(
                    LocalPaperAnalysisEvent.job_id == job_id,
                    LocalPaperAnalysisEvent.sequence > after_sequence,
                )
                .order_by(LocalPaperAnalysisEvent.sequence)
                .limit(1000)
            )
        ).all()

    async def request_cancel(self, *, job_id: UUID, owner_id: UUID) -> LocalPaperAnalysisJob:
        job = await self.get_job(job_id=job_id, owner_id=owner_id)
        if job.status not in TERMINAL_JOB_STATUSES:
            job.cancellation_requested = True
            await self._append_event(job, "CANCEL_REQUESTED", {"stage": job.status})
        return job

    async def create_memory_candidate(
        self, *, job_id: UUID, owner_id: UUID, body: LocalPaperMemoryCandidateCreate
    ) -> LocalPaperMemoryCandidate:
        job = await self.get_job(job_id=job_id, owner_id=owner_id)
        if job.status not in {"COMPLETED", "PARTIAL"}:
            raise AuthorizationError("完成分析后才能把偏好加入长期记忆候选队列")
        row = LocalPaperMemoryCandidate(
            owner_id=owner_id,
            session_id=job.session_id,
            job_id=job.id,
            candidate_json={"preferences": body.preferences},
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def confirm_memory_candidate(
        self, *, candidate_id: UUID, owner_id: UUID, confirmation_note: str
    ) -> LocalPaperMemoryCandidate:
        row = await self.db.get(LocalPaperMemoryCandidate, candidate_id, with_for_update=True)
        if row is None:
            raise NotFoundError("长期记忆候选不存在")
        if row.owner_id != owner_id:
            raise AuthorizationError("无权确认该长期记忆候选")
        if row.status != "PENDING":
            return row
        preferences = row.candidate_json.get("preferences")
        if not isinstance(preferences, dict):
            raise RuntimeError("长期记忆候选格式无效")
        latest = await memory_repository.get_latest_profile(self.db, user_id=owner_id)
        merged = {**(latest.preferences_json if latest else {}), **preferences}
        profile = await memory_repository.confirm_profile(
            self.db,
            user_id=owner_id,
            body=ResearchProfileConfirm(
                preferences=merged,
                confirmation_note=confirmation_note,
            ),
        )
        row.status, row.confirmed_profile_id = "CONFIRMED", profile.id
        return row

    async def artifact(self, *, job_id: UUID, owner_id: UUID) -> tuple[bytes, str, str]:
        job = await self.get_job(job_id=job_id, owner_id=owner_id)
        if not job.artifact_key:
            raise NotFoundError("分析产物尚未生成")
        content = await get_research_object_store().get(job.artifact_key)
        digest = hashlib.sha256(content).hexdigest()
        if digest != job.artifact_sha256:
            logger.error("Analysis artifact hash mismatch job_id=%s", job.id)
            raise RuntimeError("分析产物完整性校验失败")
        output_format = str(job.result_json.get("output_format", "markdown"))
        safe_content = _safe_report_content(content.decode("utf-8", errors="replace")).encode()
        # Legacy reports can be sanitized at read time without mutating the
        # auditable object.  The response checksum describes exactly what was
        # delivered to the browser, not the protected original.
        return safe_content, output_format, hashlib.sha256(safe_content).hexdigest()

    async def run_job(self, *, job_id: UUID) -> None:
        """Execute a job without ever leaving infrastructure failures as RUNNING."""
        try:
            from app.services.literature_research.local_paper_analysis_orchestrator import (
                LocalPaperAnalysisOrchestrator,
            )

            await LocalPaperAnalysisOrchestrator(self).run(job_id)
        except Exception as exc:
            await self.db.rollback()
            job = await self.db.get(LocalPaperAnalysisJob, job_id, with_for_update=True)
            if job is not None and job.status not in TERMINAL_JOB_STATUSES:
                job.status = "FAILED"
                job.error_code = "PROVIDER_UNAVAILABLE"
                job.error_message = "分析任务执行失败，系统已保留本地检索证据。"
                await self._append_event(
                    job,
                    "FAILED",
                    {"error_code": job.error_code, "error_message": job.error_message},
                )
                await self.db.commit()
                await _publish_event(
                    _event_payload(
                        job,
                        sequence=await self._latest_sequence(job.id),
                        event_type="FAILED",
                        detail={"error_code": job.error_code},
                    )
                )
            logger.exception(
                "Local paper analysis failed job_id=%s error_type=%s", job_id, type(exc).__name__
            )
            raise

    async def poll_background_stage(self, *, stage_id: UUID) -> None:
        from app.services.literature_research.local_paper_analysis_orchestrator import (
            LocalPaperAnalysisOrchestrator,
        )

        await LocalPaperAnalysisOrchestrator(self).poll_background_stage(stage_id)

    async def _resolve_session(
        self, *, owner_id: UUID, library: LocalPaperLibrary, body: LocalPaperAnalysisCreate
    ) -> LocalPaperAnalysisSession:
        if body.session_id is not None:
            session = await self.get_session(session_id=body.session_id, owner_id=owner_id)
            if session.library_id != library.id:
                raise AuthorizationError("分析会话不属于当前本地文献库")
            if body.project_id is not None and body.project_id != session.project_id:
                raise AuthorizationError("会话项目与请求项目不一致")
            return session
        if body.project_id is not None:
            await self._assert_project_granted(library.id, body.project_id, owner_id)
        session = LocalPaperAnalysisSession(
            library_id=library.id,
            owner_id=owner_id,
            project_id=body.project_id,
            title=(body.question.strip()[:80] or "本地文献分析"),
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def _owned_library(self, owner_id: UUID) -> LocalPaperLibrary:
        return await LocalPaperLibraryService(self.db)._required_owned_library(owner_id)

    async def _assert_project_granted(
        self, library_id: UUID, project_id: UUID, owner_id: UUID
    ) -> None:
        # Project service also handles organization membership; a library grant
        # is an additional explicit boundary, not a substitute for project ACL.
        from app.services.literature_research.project import ResearchProjectService

        await ResearchProjectService(self.db).get_owned(project_id, owner_id)
        grant = await self.db.scalar(
            select(LocalPaperLibraryProjectGrant).where(
                LocalPaperLibraryProjectGrant.library_id == library_id,
                LocalPaperLibraryProjectGrant.project_id == project_id,
                LocalPaperLibraryProjectGrant.permission == "ANALYZE",
            )
        )
        if grant is None:
            raise AuthorizationError("该项目尚未获授权使用本地文献库")

    async def grant_project(
        self, *, owner_id: UUID, project_id: UUID
    ) -> LocalPaperLibraryProjectGrant:
        library = await self._owned_library(owner_id)
        from app.services.literature_research.project import ResearchProjectService

        await ResearchProjectService(self.db).get_owned(project_id, owner_id)
        grant = await self.db.scalar(
            select(LocalPaperLibraryProjectGrant).where(
                LocalPaperLibraryProjectGrant.library_id == library.id,
                LocalPaperLibraryProjectGrant.project_id == project_id,
            )
        )
        if grant is None:
            grant = LocalPaperLibraryProjectGrant(
                library_id=library.id, project_id=project_id, granted_by=owner_id
            )
            self.db.add(grant)
            await self.db.flush()
        return grant

    async def _progress(
        self, job: LocalPaperAnalysisJob, status: str, event_type: str, detail: dict[str, object]
    ) -> None:
        job.status = status
        await self._append_event(job, event_type, detail)
        await self.db.commit()
        await _publish_event(
            _event_payload(
                job,
                sequence=await self._latest_sequence(job.id),
                event_type=event_type,
                detail=detail,
            )
        )

    async def _cancel(self, job: LocalPaperAnalysisJob) -> None:
        job.status = "CANCELLED"
        await self._append_event(job, "CANCELLED", {"reason": "user_requested"})
        await self.db.commit()
        await _publish_event(
            _event_payload(
                job, sequence=await self._latest_sequence(job.id), event_type="CANCELLED", detail={}
            )
        )

    async def _append_event(
        self, job: LocalPaperAnalysisJob, event_type: str, detail: dict[str, object]
    ) -> None:
        sequence = await self._latest_sequence(job.id) + 1
        payload = _event_payload(job, sequence=sequence, event_type=event_type, detail=detail)
        session = await self.db.get(LocalPaperAnalysisSession, job.session_id)
        previous_hash = session.audit_head_hash if session else None
        event_hash = _hash_event(previous_hash, payload)
        self.db.add(
            LocalPaperAnalysisEvent(
                job_id=job.id,
                sequence=sequence,
                event_type=event_type,
                payload_json=payload,
                previous_hash=previous_hash,
                event_hash=event_hash,
            )
        )
        if session is not None:
            session.audit_head_hash = event_hash

    async def _latest_sequence(self, job_id: UUID) -> int:
        return int(
            await self.db.scalar(
                select(func.coalesce(func.max(LocalPaperAnalysisEvent.sequence), 0)).where(
                    LocalPaperAnalysisEvent.job_id == job_id
                )
            )
            or 0
        )

    async def _source_versions(self, papers: list[object]) -> list[dict[str, object]]:
        paper_ids = [paper.id for paper in papers if hasattr(paper, "id")]
        if not paper_ids:
            return []
        rows = (
            await self.db.execute(
                select(
                    LocalPaper.id,
                    LocalPaper.active_document_version_id,
                    LocalPaperDocumentVersion.source_sha256,
                )
                .outerjoin(
                    LocalPaperDocumentVersion,
                    LocalPaper.active_document_version_id == LocalPaperDocumentVersion.id,
                )
                .where(LocalPaper.id.in_(paper_ids))
            )
        ).all()
        return [
            {
                "paper_id": str(paper_id),
                "document_version_id": str(version_id) if version_id else None,
                "source_sha256": digest,
            }
            for paper_id, version_id, digest in rows
        ]

    @staticmethod
    def _evidence_manifest(papers: list[object]) -> list[dict[str, object]]:
        manifest: list[dict[str, object]] = []
        for paper in papers:
            for item in getattr(paper, "evidence", []):
                manifest.append(
                    {
                        "paper_id": str(paper.id),
                        "citekey": paper.citekey,
                        "title": paper.title,
                        "page_number": item.page_number,
                        "chunk_index": item.chunk_index,
                        "section_heading": item.section_heading,
                        "text": item.text,
                    }
                )
        return manifest

    async def _record_attempt(
        self,
        job: LocalPaperAnalysisJob,
        *,
        stage: object | None = None,
        status: str,
        latency_ms: int | None,
        error: object | None,
    ) -> None:
        from app.services.literature_research.paper_analysis_model_gateway import ModelGatewayError

        stage_id = getattr(stage, "id", None)
        paper_id = getattr(stage, "paper_id", None)
        attempt_number = int(getattr(stage, "attempt_count", 1))
        normalized_error_code = error.code if isinstance(error, ModelGatewayError) else None
        error_summary = error.raw_summary if isinstance(error, ModelGatewayError) else None
        self.db.add(
            LocalPaperAnalysisLLMAttempt(
                job_id=job.id,
                stage_id=stage_id,
                paper_id=paper_id,
                attempt_number=attempt_number,
                provider=selected_llm_provider(),
                model=selected_llm_model_identifier(),
                status=status,
                latency_ms=latency_ms,
                error_type=normalized_error_code,
                error_message=error_summary,
                normalized_error_code=normalized_error_code,
                endpoint_hash=hashlib.sha256(
                    settings.LLM_BASE_URL.rstrip("/").encode()
                ).hexdigest()[:16],
                prompt_sha256=hashlib.sha256(job.question.encode()).hexdigest(),
            )
        )
        await self.db.flush()

    async def _persist_turn(self, job: LocalPaperAnalysisJob, content: str) -> None:
        turn = await self.db.scalar(
            select(LocalPaperAnalysisTurn).where(LocalPaperAnalysisTurn.job_id == job.id)
        )
        if turn is None:
            self.db.add(
                LocalPaperAnalysisTurn(
                    session_id=job.session_id,
                    job_id=job.id,
                    user_input=job.question,
                    assistant_output=content,
                    evidence_manifest_json=job.evidence_json,
                    metadata_json={
                        "output_format": job.request_json.get("output_format", "markdown")
                    },
                )
            )
        else:
            turn.assistant_output = content

    async def _resolved_memory_context(self, job: LocalPaperAnalysisJob) -> dict[str, object]:
        """Resolve only confirmed memories; audit records their exact provenance.

        Audit history is never fed back to the model.  It rebuilds the Redis
        cache after expiry but has no authority to override a current request.
        """
        profile = await memory_repository.get_latest_profile(self.db, user_id=job.owner_id)
        project_memories = (
            await memory_repository.list_project_memories(self.db, project_id=job.project_id)
            if job.project_id is not None
            else []
        )
        presentation: dict[str, object] = {}
        if profile is not None:
            for key in ("output_language", "citation_style", "analysis_depth"):
                value = profile.preferences_json.get(key)
                if isinstance(value, (str, int, float, bool)):
                    presentation[key] = value
        for memory in project_memories:
            if memory.memory_type != "DISPLAY_PREFERENCE":
                continue
            values = memory.content_json.get("preferences", memory.content_json)
            if isinstance(values, dict):
                for key in ("output_language", "citation_style", "analysis_depth"):
                    value = values.get(key)
                    if isinstance(value, (str, int, float, bool)):
                        presentation[key] = value
        return {
            "presentation": presentation,
            "provenance": {
                "user_profile_id": str(profile.id) if profile else None,
                "user_profile_version": profile.version if profile else None,
                "project_memory_ids": [str(memory.id) for memory in project_memories],
            },
        }

    @staticmethod
    def _question_with_confirmed_preferences(question: str, presentation: object) -> str:
        if not isinstance(presentation, dict) or not presentation:
            return question
        rendered = "; ".join(f"{key}={value}" for key, value in sorted(presentation.items()))
        return f"{question}\n\n已确认的输出偏好（不得改变证据范围）：{rendered}"

    async def _update_short_term_memory(self, job: LocalPaperAnalysisJob) -> None:
        """L1 cache is bounded and disposable; durable turn/event rows rebuild it."""
        client = RedisClient()
        try:
            await client.connect()
            await ResearchSessionMemoryService(client).put(
                user_id=job.owner_id,
                session_id=job.session_id,
                body=SessionMemoryWrite(
                    project_id=job.project_id,
                    draft_slots={
                        "local_paper_analysis": {
                            "latest_job_id": str(job.id),
                            "question": job.question[:500],
                            "status": job.status,
                            "retrieval_run_id": str(job.retrieval_run_id)
                            if job.retrieval_run_id
                            else None,
                        }
                    },
                ),
            )
        except Exception as exc:
            logger.warning(
                "Analysis completed but L1 session cache was unavailable: %s", type(exc).__name__
            )
        finally:
            await client.close()

    @staticmethod
    def session_read(row: LocalPaperAnalysisSession) -> LocalPaperAnalysisSessionRead:
        return LocalPaperAnalysisSessionRead.model_validate(row)

    @staticmethod
    def job_read(row: LocalPaperAnalysisJob) -> LocalPaperAnalysisJobRead:
        return LocalPaperAnalysisJobRead.model_validate(row)

    @staticmethod
    def event_read(row: LocalPaperAnalysisEvent) -> LocalPaperAnalysisEventRead:
        return LocalPaperAnalysisEventRead.model_validate(row)

    @staticmethod
    def candidate_read(row: LocalPaperMemoryCandidate) -> LocalPaperMemoryCandidateRead:
        return LocalPaperMemoryCandidateRead.model_validate(row)
