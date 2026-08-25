"""CPU-worker entry point for explicit local Zotero syncs."""

import asyncio
from uuid import UUID

from celery import shared_task

from app.db.session import get_worker_db_context
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
