"""Audit exports must be derived from the first authoritative failed boundary."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.literature_research.audit_exports import (
    collect_exclusion_audit_rows,
    collect_metric_snapshot_audit_rows,
)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return iter(self.rows)


@pytest.mark.anyio
async def test_exclusions_stop_at_first_failed_pipeline_boundary() -> None:
    run_id = uuid4()
    hard_failed_id, fulltext_failed_id = uuid4(), uuid4()
    hard_failed = SimpleNamespace(
        id=hard_failed_id,
        canonical_title="Hard constraint failure",
        document_type="journal_article",
    )
    fulltext_failed = SimpleNamespace(
        id=fulltext_failed_id,
        canonical_title="Licensed full text unavailable",
        document_type="journal_article",
    )
    version_a = SimpleNamespace(id=uuid4(), doi="10.1000/hard")
    version_b = SimpleNamespace(id=uuid4(), doi="10.1000/fulltext")
    venue = SimpleNamespace(name="Auditable Journal")
    hard_eligibility = SimpleNamespace(
        eligible=False, hard_fail_count=1, hard_unknown_count=0
    )
    eligible = SimpleNamespace(eligible=True, hard_fail_count=0, hard_unknown_count=0)
    relevance = SimpleNamespace(
        decision="PASS",
        lexical_score=0.8,
        semantic_score=0.9,
        cross_encoder_score=0.95,
        reasons_json=[],
    )
    constraint = SimpleNamespace(decision="FAIL", reason_code="COMPARISON_FAILED")
    denied_acquisition = SimpleNamespace(
        allowed=False,
        malware_scan_status="NOT_SCANNED",
        object_key=None,
        reason_code="LICENSE_NOT_ALLOWED",
    )
    db = AsyncMock()
    db.execute.side_effect = [
        Result([(fulltext_failed_id, denied_acquisition)]),
        Result([]),
        Result([]),
    ]

    with (
        patch(
            "app.services.literature_research.audit_exports.catalog_repository."
            "list_candidate_rows",
            new=AsyncMock(
                return_value=(
                    [
                        (hard_failed, version_a, venue, hard_eligibility, None),
                        (fulltext_failed, version_b, venue, eligible, relevance),
                    ],
                    2,
                )
            ),
        ),
        patch(
            "app.services.literature_research.audit_exports.catalog_repository."
            "list_constraints_for_works",
            new=AsyncMock(
                return_value={
                    hard_failed_id: [constraint],
                    fulltext_failed_id: [],
                }
            ),
        ),
    ):
        rows = await collect_exclusion_audit_rows(
            db, run_id=run_id, included_work_ids=set()
        )

    by_id = {row.work_id: row for row in rows}
    assert by_id[hard_failed_id].reason_codes == ["COMPARISON_FAILED"]
    assert "FULLTEXT_NOT_ACQUIRED" not in by_id[hard_failed_id].reason_codes
    assert by_id[fulltext_failed_id].reason_codes == ["LICENSE_NOT_ALLOWED"]
    assert by_id[fulltext_failed_id].relevance_score == 0.95


@pytest.mark.anyio
async def test_metric_export_contains_only_ledger_referenced_snapshot() -> None:
    run_id, snapshot_id, work_id = uuid4(), uuid4(), uuid4()
    evaluated_at = datetime.now(UTC)
    evaluation = SimpleNamespace(
        metric_fact_id=uuid4(),
        metric_year=2025,
        constraint_id="jif-floor",
        field="venue.metric.jif",
        observed_value=8.1,
        decision="PASS",
        reason_code="COMPARISON_PASSED",
        evidence_reference=f"metric-snapshot:{snapshot_id}:row:2",
        evaluated_at=evaluated_at,
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        source_name="Licensed source",
        source_version="2025",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        license_reference="Institutional contract",
        authorized_scope="Private evaluation",
        license_attested=True,
        status="ACTIVE",
        payload_sha256="e" * 64,
    )
    work = SimpleNamespace(id=work_id, canonical_title="Metric-audited paper")
    venue = SimpleNamespace(name="Journal of Audits")
    db = AsyncMock()
    db.execute.return_value = Result([(evaluation, snapshot, work, venue)])

    rows = await collect_metric_snapshot_audit_rows(db, run_id=run_id)

    assert len(rows) == 1
    assert rows[0].snapshot_id == snapshot_id
    assert rows[0].work_id == work_id
    assert rows[0].observed_value == 8.1
    assert rows[0].metric_year == 2025
    assert rows[0].payload_sha256 == "e" * 64
