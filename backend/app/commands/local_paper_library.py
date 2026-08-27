"""Operational CLI entry points for the administrator-owned Zotero library."""
# ruff: noqa: RUF001 - Operational messages are intentionally in Chinese.

from __future__ import annotations

import asyncio
from uuid import UUID

import click
from sqlalchemy import select

from app.commands import command, success
from app.db.models.local_paper_library import LocalPaperLibrary, LocalPaperSyncRun
from app.db.models.user import User, UserRole
from app.db.session import get_db_context
from app.services.literature_research.local_paper_library import LocalPaperLibraryService
from app.worker.tasks.local_paper_library_tasks import sync_local_paper_library


@command("sync-local-library", help="Queue an incremental sync for the local Zotero library")
@click.option(
    "--owner-id",
    type=click.UUID,
    help="Library owner UUID. Required only before the first library has been created.",
)
def sync_local_library(owner_id: UUID | None) -> None:
    """Persist one sync run and dispatch it to the CPU worker.

    The command intentionally follows the same durable request-and-dispatch
    path as ``POST /research/local-library/sync``.  It does not parse PDFs in
    the CLI process, so worker queue routing, audit events and recovery remain
    consistent with the browser flow.
    """

    async def queue_sync() -> tuple[str, str, bool]:
        async with get_db_context() as db:
            resolved_owner_id = owner_id
            if resolved_owner_id is None:
                libraries = (await db.scalars(select(LocalPaperLibrary))).all()
                if not libraries:
                    raise click.UsageError(
                        "首次同步必须提供 --owner-id，并且该用户必须是应用管理员。"
                    )
                if len(libraries) > 1:
                    raise click.UsageError("存在多个本地论文库时必须明确提供 --owner-id。")
                resolved_owner_id = libraries[0].owner_id

            owner = await db.get(User, resolved_owner_id)
            if owner is None or not (owner.is_app_admin or owner.role == UserRole.ADMIN.value):
                raise click.UsageError("--owner-id 必须属于应用管理员。")

            existing_library = await db.scalar(
                select(LocalPaperLibrary).where(LocalPaperLibrary.owner_id == resolved_owner_id)
            )
            if existing_library is not None:
                active = await db.scalar(
                    select(LocalPaperSyncRun).where(
                        LocalPaperSyncRun.library_id == existing_library.id,
                        LocalPaperSyncRun.status.in_(["QUEUED", "RUNNING"]),
                    )
                )
                if active is not None:
                    return str(existing_library.id), str(active.id), False

            service = LocalPaperLibraryService(db)
            run = await service.request_sync(owner_id=resolved_owner_id)
            return str(run.library_id), str(run.id), True

    library_id, run_id, should_dispatch = asyncio.run(queue_sync())
    if should_dispatch:
        sync_local_paper_library.apply_async(args=(library_id, run_id), queue="research-cpu")
        success(f"本地论文库同步已入队：{run_id}")
    else:
        click.echo(f"本地论文库已有活动同步：{run_id}（未重复投递）")
