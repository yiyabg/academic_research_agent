"""Fail-closed lawful full-text selection; shadow-library sources are forbidden."""

from urllib.parse import urlparse

from app.schemas.literature_research.evidence import (
    FullTextAcquisitionDecision,
    FullTextCandidate,
    LicenseDecision,
)

FORBIDDEN_HOST_MARKERS = ("sci-hub", "libgen", "z-library")
SOURCE_PRIORITY = {
    "publisher": 5,
    "pubmed_central": 4,
    "unpaywall": 3,
    "arxiv": 2,
    "user_upload": 1,
}


class LawfulFullTextPolicy:
    def select(self, candidates: list[FullTextCandidate]) -> FullTextAcquisitionDecision:
        if not candidates:
            raise ValueError("At least one full-text candidate is required")
        rejected = []
        allowed = []
        for candidate in candidates:
            host = (urlparse(str(candidate.url)).hostname or "").lower()
            scheme = urlparse(str(candidate.url)).scheme.lower()
            forbidden = any(marker in host for marker in FORBIDDEN_HOST_MARKERS)
            if (
                forbidden
                or scheme != "https"
                or candidate.license_decision != LicenseDecision.ALLOWED
                or not candidate.license_reference
            ):
                rejected.append(candidate)
            else:
                allowed.append(candidate)
        if not allowed:
            return FullTextAcquisitionDecision(
                version_id=candidates[0].version_id,
                allowed=False,
                reason_code="NO_VERIFIABLY_LAWFUL_FULLTEXT",
                rejected=rejected,
            )
        selected = max(allowed, key=lambda item: SOURCE_PRIORITY[item.source.value])
        return FullTextAcquisitionDecision(
            version_id=selected.version_id,
            allowed=True,
            selected=selected,
            reason_code="LAWFUL_SOURCE_SELECTED",
            rejected=rejected,
        )
