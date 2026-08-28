"""Licensed metric snapshot import safety tests."""

import hashlib
from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.literature_research.metric_import import MetricSnapshotImportService
from app.services.literature_research.object_store import LocalResearchObjectStore

CSV = b"""venue_name,venue_type,issn_l,metric_name,metric_value,metric_year
Journal of Agent Systems,journal,1234-5678,jif,8.2,2025
Agent Systems Conference,conference,,conference_rank,CCF_A,2025
"""


def test_csv_parser_preserves_numeric_and_categorical_metrics() -> None:
    facts = MetricSnapshotImportService._parse(CSV)
    assert facts[0].metric_value == 8.2
    assert facts[0].source_row == 2
    assert facts[1].metric_value == "CCF_A"


def test_csv_parser_requires_an_explicit_metric_year() -> None:
    payload = b"venue_name,venue_type,metric_name,metric_value,metric_year\nJ,journal,jif,8.2,\n"
    with pytest.raises(ValueError, match="missing metric_year"):
        MetricSnapshotImportService._parse(payload)


@pytest.mark.anyio
async def test_import_requires_license_attestation_before_writing(tmp_path) -> None:
    store = LocalResearchObjectStore(tmp_path)
    service = MetricSnapshotImportService(AsyncMock(), store)
    with pytest.raises(ValueError, match="license attestation"):
        await service.import_csv(
            imported_by=uuid4(),
            payload=CSV,
            source_name="Unattested export",
            source_version="2025",
            effective_from=date(2026, 1, 1),
            effective_to=None,
            license_reference="internal-license-1",
            authorized_scope="internal research",
            license_attested=False,
        )
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.anyio
async def test_authorized_import_is_content_addressed_and_passes_facts_to_repository() -> None:
    object_store = AsyncMock()
    digest = hashlib.sha256(CSV).hexdigest()
    expected_key = f"metric-snapshots/licensed-venue-metrics/2025/{digest}.csv"
    object_store.put.return_value = expected_key
    service = MetricSnapshotImportService(AsyncMock(), object_store)
    stored = object()
    with patch(
        "app.services.literature_research.metric_import.quality_repository.create_snapshot",
        new=AsyncMock(return_value=stored),
    ) as create_snapshot:
        result = await service.import_csv(
            imported_by=uuid4(),
            payload=CSV,
            source_name="Licensed Venue Metrics",
            source_version="2025",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
            license_reference="contract-42",
            authorized_scope="this deployment",
            license_attested=True,
        )
    assert result is stored
    object_store.put.assert_awaited_once_with(
        expected_key,
        CSV,
        content_type="text/csv",
        metadata={"sha256": digest},
    )
    assert len(create_snapshot.await_args.kwargs["facts"]) == 2
    snapshot = create_snapshot.await_args.kwargs["snapshot"]
    assert snapshot.metric_names == ["conference_rank", "jif"]
