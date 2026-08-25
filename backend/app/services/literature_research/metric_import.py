"""Explicitly authorized CSV import for licensed venue metric snapshots."""

import csv
import hashlib
import io
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.quality import ResearchMetricSnapshot
from app.repositories.literature_research import quality as quality_repository
from app.schemas.literature_research.quality import MetricFactInput, MetricSnapshotCreate
from app.services.literature_research.object_store import (
    ResearchObjectStore,
    get_research_object_store,
)

REQUIRED_COLUMNS = {
    "venue_name",
    "venue_type",
    "metric_name",
    "metric_value",
    "metric_year",
}


class MetricSnapshotImportService:
    def __init__(self, db: AsyncSession, object_store: ResearchObjectStore | None = None) -> None:
        self.db = db
        self.object_store = object_store or get_research_object_store()

    async def import_csv(
        self,
        *,
        imported_by: UUID,
        payload: bytes,
        source_name: str,
        source_version: str,
        effective_from: date,
        effective_to: date | None,
        license_reference: str,
        authorized_scope: str,
        license_attested: bool,
    ) -> ResearchMetricSnapshot:
        if not license_attested:
            raise ValueError("metric snapshot import requires an explicit license attestation")
        digest = hashlib.sha256(payload).hexdigest()
        facts = self._parse(payload)
        metric_names = sorted({fact.metric_name for fact in facts})
        safe_source = "-".join(source_name.lower().split())
        key = f"metric-snapshots/{safe_source}/{source_version}/{digest}.csv"
        object_key = await self.object_store.put(
            key,
            payload,
            content_type="text/csv",
            metadata={"sha256": digest},
        )
        snapshot = MetricSnapshotCreate(
            source_name=source_name,
            source_version=source_version,
            metric_names=metric_names,
            effective_from=effective_from,
            effective_to=effective_to,
            license_reference=license_reference,
            authorized_scope=authorized_scope,
            license_attested=license_attested,
            imported_at=datetime.now(UTC),
            payload_sha256=digest,
            object_key=object_key,
        )
        return await quality_repository.create_snapshot(
            self.db, imported_by=imported_by, snapshot=snapshot, facts=facts
        )

    @staticmethod
    def _parse(payload: bytes) -> list[MetricFactInput]:
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or not set(reader.fieldnames) >= REQUIRED_COLUMNS:
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            raise ValueError(f"Metric CSV missing columns: {', '.join(missing)}")
        facts = []
        for row_number, row in enumerate(reader, start=2):
            raw_value = row["metric_value"].strip()
            raw_year = row["metric_year"].strip()
            if not raw_year:
                raise ValueError(f"Metric CSV row {row_number} is missing metric_year")
            try:
                metric_year = int(raw_year)
            except ValueError as exc:
                raise ValueError(
                    f"Metric CSV row {row_number} has invalid metric_year"
                ) from exc
            try:
                numeric = Decimal(raw_value)
                value = int(numeric) if numeric == numeric.to_integral() else float(numeric)
            except InvalidOperation:
                value = raw_value
            facts.append(
                MetricFactInput(
                    venue_name=row["venue_name"].strip(),
                    venue_type=row["venue_type"].strip().lower(),
                    issn_l=(row.get("issn_l") or "").strip() or None,
                    metric_name=row["metric_name"].strip(),
                    metric_value=value,
                    metric_year=metric_year,
                    source_row=row_number,
                )
            )
        if not facts:
            raise ValueError("Metric CSV contains no data rows")
        return facts
