"""CPU-worker entry point for explicit local Zotero syncs."""

import asyncio
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

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
