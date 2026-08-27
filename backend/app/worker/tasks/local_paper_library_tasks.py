"""CPU-worker entry point for explicit local Zotero syncs."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from celery import shared_task
from sqlalchemy import or_, select

from app.db.models.local_paper_analysis import LocalPaperAnalysisJob, LocalPaperAnalysisStage
from app.db.models.local_paper_library import LocalPaperLibrary
from app.db.session import get_worker_db_context
from app.services.literature_research.local_paper_analysis import LocalPaperAnalysisService
from app.services.literature_research.local_paper_library import LocalPaperLibraryService


@shared_task(name="app.worker.tasks.local_paper_library_tasks.sync_local_paper_library")
def sync_local_paper_library(library_id: str, sync_run_id: str) -> None:
    async def run() -> None:
        async with get_worker_db_context() as db:
            await LocalPaperLibraryService(db).run_sync(
                library_id=UUID(library_id),
                sync_run_id=UUID(sync_run_id),
            )

    asyncio.run(run())


@shared_task(
    name="app.worker.tasks.local_paper_library_tasks.run_local_paper_analysis",
    bind=True,
    acks_late=True,
)
def run_local_paper_analysis(self, job_id: str) -> None:
    """Run one durable analysis job; DB state, not Celery, is the authority."""

    del self

    async def run() -> None:
        async with get_worker_db_context() as db:
            await LocalPaperAnalysisService(db).run_job(job_id=UUID(job_id))

    asyncio.run(run())


@shared_task(
    name="app.worker.tasks.local_paper_library_tasks.poll_local_paper_analysis_stage",
    bind=True,
    acks_late=True,
)
def poll_local_paper_analysis_stage(self, stage_id: str) -> None:
    """One provider retrieve per task; next poll is separately scheduled."""
    del self

    async def run() -> None:
        async with get_worker_db_context() as db:
            await LocalPaperAnalysisService(db).poll_background_stage(stage_id=UUID(stage_id))

    asyncio.run(run())


@shared_task(name="app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_background")
def recover_local_paper_analysis_background() -> int:
    """Recover due provider polls from PostgreSQL after worker/process restarts."""

    async def recover() -> list[str]:
        async with get_worker_db_context() as db:
            rows = (
                await db.scalars(
                    select(LocalPaperAnalysisStage.id).where(
                        LocalPaperAnalysisStage.status.in_(["SUBMITTED", "POLLING"]),
                        LocalPaperAnalysisStage.provider_response_id.is_not(None),
                        or_(
                            LocalPaperAnalysisStage.next_poll_at.is_(None),
                            LocalPaperAnalysisStage.next_poll_at <= datetime.now(UTC),
                        ),
                    )
                )
            ).all()
            return [str(row) for row in rows]

    stage_ids = asyncio.run(recover())
    for stage_id in stage_ids:
        poll_local_paper_analysis_stage.apply_async(args=(stage_id,), queue="research-llm")
    return len(stage_ids)


@shared_task(name="app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_staged")
def recover_local_paper_analysis_staged() -> int:
    """Recover stale staged leases after an LLM Worker process is lost.

    The orchestrator verifies the lease age under a row lock before it changes
    any state, so duplicate Beat deliveries are harmless.
    """

    async def stale_stage_ids() -> list[str]:
        async with get_worker_db_context() as db:
            rows = (
                await db.scalars(
                    select(LocalPaperAnalysisStage.id)
                    .join(LocalPaperAnalysisJob, LocalPaperAnalysisJob.id == LocalPaperAnalysisStage.job_id)
                    .where(
                        LocalPaperAnalysisStage.status == "RUNNING",
                        LocalPaperAnalysisJob.execution_mode == "staged",
                        LocalPaperAnalysisJob.status.not_in(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]),
                    )
                )
            ).all()
            return [str(row) for row in rows]

    stage_ids = asyncio.run(stale_stage_ids())

    async def recover_one(stage_id: str) -> bool:
        async with get_worker_db_context() as db:
            from app.services.literature_research.local_paper_analysis_orchestrator import (
                LocalPaperAnalysisOrchestrator,
            )

            service = LocalPaperAnalysisService(db)
            return await LocalPaperAnalysisOrchestrator(service).recover_staged_stage(UUID(stage_id))

    return sum(bool(asyncio.run(recover_one(stage_id))) for stage_id in stage_ids)


@shared_task(name="app.worker.tasks.local_paper_library_tasks.check_scheduled_local_paper_syncs")
def check_scheduled_local_paper_syncs() -> int:
    """Queue hash-based incremental syncs from the CPU worker every configured interval."""

    async def run() -> list[tuple[str, str]]:
        async with get_worker_db_context() as db:
            library_ids = (await db.scalars(select(LocalPaperLibrary.id))).all()
            service = LocalPaperLibraryService(db)
            queued: list[tuple[str, str]] = []
            for library_id in library_ids:
                library = await db.get(LocalPaperLibrary, library_id)
                if library is None:
                    continue
                sync_run = await service.request_sync(owner_id=library.owner_id)
                if sync_run.status == "QUEUED":
                    queued.append((str(sync_run.library_id), str(sync_run.id)))
            return queued

    queued = asyncio.run(run())
    for library_id, sync_run_id in queued:
        sync_local_paper_library.apply_async(args=(library_id, sync_run_id), queue="research-cpu")
    return len(queued)
