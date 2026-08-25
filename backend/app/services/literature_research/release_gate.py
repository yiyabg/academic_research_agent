"""Deterministic final release gate; quantity shortfall never lowers quality."""

from app.schemas.literature_research.release import (
    ReleaseBlocker,
    ReleaseDecision,
    ReleaseSnapshot,
)


def evaluate_release(snapshot: ReleaseSnapshot) -> ReleaseDecision:
    blockers = []
    if snapshot.protocol_hash != snapshot.approved_protocol_hash:
        blockers.append(ReleaseBlocker.PROTOCOL_CHANGED)
    if snapshot.constraint_violation_count:
        blockers.append(ReleaseBlocker.HARD_CONSTRAINT_VIOLATION)
    if snapshot.duplicate_cluster_conflicts:
        blockers.append(ReleaseBlocker.UNRESOLVED_DUPLICATES)
    if snapshot.min_relevance_score < 0.72:
        blockers.append(ReleaseBlocker.RELEVANCE_BELOW_THRESHOLD)
    if snapshot.min_evidence_coverage < 0.90:
        blockers.append(ReleaseBlocker.EVIDENCE_COVERAGE_LOW)
    if snapshot.contradicted_claim_count:
        blockers.append(ReleaseBlocker.CONTRADICTED_CLAIMS)
    if snapshot.unsupported_claim_count:
        blockers.append(ReleaseBlocker.UNSUPPORTED_CLAIMS)
    if snapshot.artifact_validation_errors:
        blockers.append(ReleaseBlocker.ARTIFACT_INVALID)
    if snapshot.document_safety_failure_count:
        blockers.append(ReleaseBlocker.DOCUMENT_SAFETY_FAILED)
    if snapshot.figure_audit_failure_count:
        blockers.append(ReleaseBlocker.FIGURE_AUDIT_INCOMPLETE)
    partial = snapshot.strict_count < snapshot.target_count
    if partial and not snapshot.shortfall_disclosed:
        blockers.append(ReleaseBlocker.SHORTFALL_NOT_DISCLOSED)
    return ReleaseDecision(allowed=not blockers, partial=partial, blockers=blockers)
