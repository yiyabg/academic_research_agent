"""Admin-only authorized venue metric snapshot endpoints."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import CurrentAdmin, DBSession
from app.repositories.literature_research import quality as quality_repository
from app.schemas.literature_research.quality import MetricSnapshotRead
from app.services.literature_research.metric_import import MetricSnapshotImportService

router = APIRouter()


@router.post(
    "",
    response_model=MetricSnapshotRead,
    status_code=status.HTTP_201_CREATED,
)
async def import_metric_snapshot(
    db: DBSession,
    current_admin: CurrentAdmin,
    file: Annotated[UploadFile, File(description="Authorized metric CSV")],
    source_name: Annotated[str, Form(min_length=2, max_length=200)],
    source_version: Annotated[str, Form(min_length=1, max_length=100)],
    effective_from: Annotated[date, Form()],
    license_reference: Annotated[str, Form(min_length=3, max_length=1000)],
    authorized_scope: Annotated[str, Form(min_length=3, max_length=1000)],
    license_attested: Annotated[bool, Form()],
    effective_to: Annotated[date | None, Form()] = None,
) -> object:
    """Import user-supplied licensed data; this endpoint never scrapes metric sites."""
    payload = await file.read()
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Metric snapshot exceeds 20 MiB",
        )
    try:
        snapshot = await MetricSnapshotImportService(db).import_csv(
            imported_by=current_admin.id,
            payload=payload,
            source_name=source_name,
            source_version=source_version,
            effective_from=effective_from,
            effective_to=effective_to,
            license_reference=license_reference,
            authorized_scope=authorized_scope,
            license_attested=license_attested,
        )
        await db.commit()
        return snapshot
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("", response_model=list[MetricSnapshotRead])
async def list_metric_snapshots(
    db: DBSession,
    current_admin: CurrentAdmin,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> object:
    del current_admin
    return await quality_repository.list_snapshots(db, skip=skip, limit=limit)
