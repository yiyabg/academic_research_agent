"""Unified asynchronous evidence-grounded analysis for the local paper corpus."""
# ruff: noqa: RUF001 - User-facing Chinese prompt text intentionally uses Chinese punctuation.

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    LocalPaperSearchRequest,
)
from app.schemas.literature_research.memory import ResearchProfileConfirm, SessionMemoryWrite
from app.services.literature_research.local_paper_library import LocalPaperLibraryService
from app.services.literature_research.object_store import get_research_object_store
from app.services.literature_research.paper_mindmap_service import PaperMindmapService
from app.services.literature_research.session_memory import ResearchSessionMemoryService
from app.services.llm_provider import (
    build_local_paper_analysis_model,
    selected_llm_model_identifier,
    selected_llm_provider,
)

logger = logging.getLogger(__name__)
TERMINAL_JOB_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}


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
        if body.client_request_id:
            existing = await self.db.scalar(
                select(LocalPaperAnalysisJob).where(
                    LocalPaperAnalysisJob.owner_id == owner_id,
                    LocalPaperAnalysisJob.idempotency_key == body.client_request_id,
                )
            )
            if existing is not None:
                return existing, False
        session = await self._resolve_session(owner_id=owner_id, library=library, body=body)
        job = LocalPaperAnalysisJob(
            session_id=session.id,
            library_id=library.id,
            owner_id=owner_id,
            project_id=session.project_id,
            mode=body.mode.upper(),
            question=body.question,
            request_json=body.model_dump(mode="json", exclude_none=True),
            idempotency_key=body.client_request_id,
        )
        self.db.add(job)
        await self.db.flush()
        await self._append_event(job, "QUEUED", {"stage": "QUEUED", "mode": job.mode})
        return job, True

    async def get_job(self, *, job_id: UUID, owner_id: UUID) -> LocalPaperAnalysisJob:
        job = await self.db.get(LocalPaperAnalysisJob, job_id)
        if job is None:
            raise NotFoundError("分析任务不存在")
        if job.owner_id != owner_id:
            raise AuthorizationError("无权访问该分析任务")
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
        return content, output_format, digest

    async def run_job(self, *, job_id: UUID) -> None:
        """Execute a job without ever leaving infrastructure failures as RUNNING."""
        try:
            await self._run_job(job_id=job_id)
        except Exception as exc:
            await self.db.rollback()
            job = await self.db.get(LocalPaperAnalysisJob, job_id, with_for_update=True)
            if job is not None and job.status not in TERMINAL_JOB_STATUSES:
                job.status = "FAILED"
                job.error_code = type(exc).__name__
                job.error_message = str(exc)[:2000]
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
            logger.exception("Local paper analysis failed job_id=%s", job_id)
            raise

    async def _run_job(self, *, job_id: UUID) -> None:
        job = await self.db.get(LocalPaperAnalysisJob, job_id, with_for_update=True)
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return
        if job.cancellation_requested:
            await self._cancel(job)
            return
        await self._progress(job, "RETRIEVING", "RETRIEVING", {})
        request = LocalPaperAnalysisCreate.model_validate(job.request_json)
        library_service = LocalPaperLibraryService(self.db)
        search = await library_service.search(
            owner_id=job.owner_id,
            request=LocalPaperSearchRequest(
                query=request.query or request.question,
                paper_ids=request.paper_ids,
                limit=request.limit,
            ),
        )
        # ``search`` accepts its Pydantic contract; model validation here makes
        # the persisted request an explicit compatibility boundary.
        # (A dict was used only to avoid inheriting optional UI-only fields.)
        job.retrieval_run_id = search.retrieval_run_id
        job.source_versions_json = await self._source_versions(search.items)
        job.evidence_json = self._evidence_manifest(search.items)
        await self._progress(
            job,
            "ANALYZING",
            "EVIDENCE_READY",
            {
                "paper_count": len(search.items),
                "retrieval_run_id": str(search.retrieval_run_id)
                if search.retrieval_run_id
                else None,
            },
        )
        if job.cancellation_requested:
            await self._cancel(job)
            return
        await self._progress(job, "SYNTHESIZING", "SYNTHESIZING", {})
        memory_context = await self._resolved_memory_context(job)
        question_for_model = self._question_with_confirmed_preferences(
            job.question, memory_context["presentation"]
        )
        started = time.monotonic()
        analysis = await PaperMindmapService().analyze_detailed(
            papers=search.items,
            question=question_for_model,
            output_format=request.output_format,
            model=build_local_paper_analysis_model(),
            timeout_seconds=settings.LOCAL_PAPER_ANALYSIS_PRIMARY_TIMEOUT_SECONDS,
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        await self._record_attempt(
            job,
            attempt_number=1,
            provider=selected_llm_provider(),
            model=selected_llm_model_identifier(),
            status="SUCCEEDED" if analysis.generated_by_llm else "FAILED",
            latency_ms=latency_ms,
            error_message=analysis.fallback_reason,
        )
        # The primary compatible gateway is allowed to fail over only through
        # the official OpenAI provider, never silently to an unknown endpoint.
        if (
            not analysis.generated_by_llm
            and settings.LOCAL_PAPER_ANALYSIS_ENABLE_OPENAI_FALLBACK
            and selected_llm_provider() != "openai"
        ):
            fallback_started = time.monotonic()
            try:
                analysis = await PaperMindmapService().analyze_detailed(
                    papers=search.items,
                    question=question_for_model,
                    output_format=request.output_format,
                    model=build_local_paper_analysis_model(fallback_to_official_openai=True),
                    timeout_seconds=settings.LOCAL_PAPER_ANALYSIS_FALLBACK_TIMEOUT_SECONDS,
                )
                await self._record_attempt(
                    job,
                    attempt_number=2,
                    provider="openai",
                    model=settings.LOCAL_PAPER_ANALYSIS_FALLBACK_MODEL,
                    status="SUCCEEDED" if analysis.generated_by_llm else "FAILED",
                    latency_ms=round((time.monotonic() - fallback_started) * 1000),
                    error_message=analysis.fallback_reason,
                )
            except Exception as exc:
                await self._record_attempt(
                    job,
                    attempt_number=2,
                    provider="openai",
                    model=settings.LOCAL_PAPER_ANALYSIS_FALLBACK_MODEL,
                    status="FAILED",
                    latency_ms=round((time.monotonic() - fallback_started) * 1000),
                    error_message=f"{type(exc).__name__}: {exc}",
                )
        if job.cancellation_requested:
            await self._cancel(job)
            return
        await self._progress(job, "RENDERING", "RENDERING", {})
        payload = analysis.content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        extension = "opml" if request.output_format == "opml" else "md"
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
            "output_format": request.output_format,
            "generated_by_llm": analysis.generated_by_llm,
            "fallback_reason": analysis.fallback_reason,
            "paper_count": len(search.items),
            "evidence_count": len(job.evidence_json),
            "artifact_sha256": digest,
            "memory_provenance": memory_context["provenance"],
        }
        job.status = "COMPLETED" if analysis.generated_by_llm else "PARTIAL"
        turn = await self.db.scalar(
            select(LocalPaperAnalysisTurn).where(LocalPaperAnalysisTurn.job_id == job.id)
        )
        if turn is None:
            turn = LocalPaperAnalysisTurn(
                session_id=job.session_id,
                job_id=job.id,
                user_input=job.question,
                assistant_output=analysis.content,
                evidence_manifest_json=job.evidence_json,
                metadata_json={
                    "output_format": request.output_format,
                    "generated_by_llm": analysis.generated_by_llm,
                },
            )
            self.db.add(turn)
        await self._append_event(
            job,
            "COMPLETED" if job.status == "COMPLETED" else "PARTIAL",
            {
                "artifact_sha256": digest,
                "generated_by_llm": analysis.generated_by_llm,
                "fallback_reason": analysis.fallback_reason,
            },
        )
        await self.db.commit()
        await self._update_short_term_memory(job)
        await _publish_event(
            _event_payload(
                job,
                sequence=await self._latest_sequence(job.id),
                event_type=job.status,
                detail=job.result_json,
            )
        )
        logger.info("Local paper analysis completed job_id=%s status=%s", job.id, job.status)

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
        attempt_number: int,
        provider: str,
        model: str,
        status: str,
        latency_ms: int,
        error_message: str | None,
    ) -> None:
        self.db.add(
            LocalPaperAnalysisLLMAttempt(
                job_id=job.id,
                attempt_number=attempt_number,
                provider=provider,
                model=model,
                status=status,
                latency_ms=latency_ms,
                error_type=error_message.split(":", 1)[0] if error_message else None,
                error_message=error_message,
                prompt_sha256=hashlib.sha256(job.question.encode()).hexdigest(),
            )
        )
        await self.db.flush()

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
