"""Deterministic artifact rendering and final release gate tests."""

import json
import xml.etree.ElementTree as ET
from datetime import date
from uuid import uuid4

from app.schemas.literature_research.analysis import (
    AuditedPaperAnalysis,
    AuditReport,
    SynthesisOutput,
)
from app.schemas.literature_research.release import (
    ArtifactFormat,
    CanonicalResearchReport,
    ExclusionAuditRow,
    MetricSnapshotAuditRow,
    ReleaseBlocker,
    ReleaseSnapshot,
    ReportPaper,
    RunManifest,
)
from app.services.literature_research.exporters import (
    render_bibtex,
    render_csv,
    render_exclusions_csv,
    render_jsonl,
    render_manifest,
    render_markdown,
    render_opml,
    render_venue_metrics_csv,
    validate_artifacts,
)
from app.services.literature_research.release_gate import evaluate_release


def report() -> CanonicalResearchReport:
    run_id, project_id, work_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    audit = AuditReport(
        work_id=work_id,
        claims=[],
        evidence_coverage=1.0,
        contradicted_count=0,
        unsupported_count=0,
    )
    analysis = AuditedPaperAnalysis(work_id=work_id, sections=[], figures=[], audit=audit)
    synthesis = SynthesisOutput(
        overview="Evidence-grounded overview & comparison.",
        themes=[],
        included_work_ids=[work_id],
    )
    return CanonicalResearchReport(
        run_id=run_id,
        project_id=project_id,
        protocol_hash="sha256:" + "a" * 64,
        title="Auditable <Agents> & Evidence",
        target_count=20,
        strict_count=1,
        shortfall_disclosed=True,
        synthesis=synthesis,
        papers=[
            ReportPaper(
                work_id=work_id,
                version_id=version_id,
                title="Auditable Research Agents",
                authors=["Alice Smith", "Bob Jones"],
                year=2026,
                doi="10.1000/agent",
                source_url="https://doi.org/10.1000/agent",
                document_type="journal_article",
                venue="Journal of Agent Systems",
                relevance_score=0.91,
                hard_constraints_passed=True,
                analysis=analysis,
            )
        ],
    )


def test_all_artifacts_are_deterministic_valid_and_share_canonical_data() -> None:
    source = report()
    renderers = [render_markdown, render_opml, render_bibtex, render_jsonl, render_csv]
    first = [renderer(source) for renderer in renderers]
    second = [renderer(source) for renderer in renderers]
    assert [item.sha256 for item in first] == [item.sha256 for item in second]
    assert validate_artifacts(first) == []
    ET.fromstring(next(item.data for item in first if item.format == ArtifactFormat.OPML))
    json_line = next(item.data for item in first if item.format == ArtifactFormat.JSONL)
    assert json.loads(json_line)["doi"] == "10.1000/agent"
    assert b"10.1000/agent" in next(
        item.data for item in first if item.format == ArtifactFormat.BIBTEX
    )


def test_manifest_records_every_artifact_hash_and_template_commit() -> None:
    source = report()
    artifacts = [
        render_markdown(source),
        render_opml(source),
        render_bibtex(source),
        render_jsonl(source),
        render_csv(source),
    ]
    manifest = RunManifest(
        run_id=source.run_id,
        project_id=source.project_id,
        protocol_hash=source.protocol_hash,
        template_commit="3428d9a6214619d3514312886d59a36400747b7d",
        artifact_hashes={item.format: item.sha256 for item in artifacts},
        target_count=20,
        strict_count=1,
        shortfall_disclosed=True,
    )
    rendered = render_manifest(manifest)
    assert validate_artifacts([rendered]) == []
    payload = json.loads(rendered.data)
    assert payload["template_commit"].startswith("3428d9")
    assert len(payload["artifact_hashes"]) == 5


def test_audit_csvs_are_deterministic_and_preserve_ledger_provenance() -> None:
    work_id, version_id, snapshot_id = uuid4(), uuid4(), uuid4()
    exclusions = [
        ExclusionAuditRow(
            work_id=work_id,
            version_id=version_id,
            title="Excluded, with CSV punctuation",
            document_type="journal_article",
            doi="10.1000/excluded",
            venue="Journal, One",
            hard_eligible=False,
            hard_fail_count=1,
            relevance_decision=None,
            reason_codes=["VALUE_MISSING", "COMPARISON_FAILED"],
        )
    ]
    metrics = [
        MetricSnapshotAuditRow(
            snapshot_id=snapshot_id,
            metric_fact_id=uuid4(),
            work_id=work_id,
            title="Audited metric paper",
            venue="Journal One",
            constraint_id="jif-floor",
            field="venue.metric.jif",
            observed_value=8.2,
            metric_year=2025,
            decision="PASS",
            reason_code="COMPARISON_PASSED",
            source_name="Licensed JCR fixture",
            source_version="2025",
            effective_from=date(2025, 1, 1),
            license_reference="Institutional license reference",
            authorized_scope="This private deployment",
            license_attested=True,
            snapshot_status="ACTIVE",
            payload_sha256="d" * 64,
            evidence_reference=f"metric-snapshot:{snapshot_id}:row:2",
        )
    ]

    first_exclusions = render_exclusions_csv(exclusions)
    second_exclusions = render_exclusions_csv(exclusions)
    first_metrics = render_venue_metrics_csv(metrics)
    second_metrics = render_venue_metrics_csv(metrics)
    assert first_exclusions.sha256 == second_exclusions.sha256
    assert first_metrics.sha256 == second_metrics.sha256
    assert b"COMPARISON_FAILED;VALUE_MISSING" in first_exclusions.data
    assert str(snapshot_id).encode() in first_metrics.data
    assert b'"8.2"' not in first_metrics.data
    assert validate_artifacts([first_exclusions, first_metrics]) == []


def valid_snapshot(**updates) -> ReleaseSnapshot:
    values = {
        "protocol_hash": "sha256:" + "a" * 64,
        "approved_protocol_hash": "sha256:" + "a" * 64,
        "constraint_violation_count": 0,
        "duplicate_cluster_conflicts": 0,
        "min_relevance_score": 0.72,
        "min_evidence_coverage": 0.90,
        "contradicted_claim_count": 0,
        "unsupported_claim_count": 0,
        "artifact_validation_errors": [],
        "target_count": 20,
        "strict_count": 13,
        "shortfall_disclosed": True,
    }
    values.update(updates)
    return ReleaseSnapshot(**values)


def test_strict_shortfall_can_release_partial_without_lowering_quality() -> None:
    decision = evaluate_release(valid_snapshot())
    assert decision.allowed is True
    assert decision.partial is True
    assert decision.blockers == []


def test_shortfall_without_disclosure_and_quality_failure_are_blocked() -> None:
    decision = evaluate_release(
        valid_snapshot(
            shortfall_disclosed=False,
            min_evidence_coverage=0.89,
            contradicted_claim_count=1,
        )
    )
    assert decision.allowed is False
    assert set(decision.blockers) == {
        ReleaseBlocker.SHORTFALL_NOT_DISCLOSED,
        ReleaseBlocker.EVIDENCE_COVERAGE_LOW,
        ReleaseBlocker.CONTRADICTED_CLAIMS,
    }


def test_artifact_validation_error_blocks_release() -> None:
    decision = evaluate_release(
        valid_snapshot(artifact_validation_errors=["research_report.opml invalid"])
    )
    assert decision.blockers == [ReleaseBlocker.ARTIFACT_INVALID]


def test_unscanned_acquired_document_blocks_release() -> None:
    decision = evaluate_release(valid_snapshot(document_safety_failure_count=1))
    assert decision.blockers == [ReleaseBlocker.DOCUMENT_SAFETY_FAILED]


def test_missing_figure_crop_blocks_release() -> None:
    decision = evaluate_release(valid_snapshot(figure_audit_failure_count=1))
    assert decision.blockers == [ReleaseBlocker.FIGURE_AUDIT_INCOMPLETE]
