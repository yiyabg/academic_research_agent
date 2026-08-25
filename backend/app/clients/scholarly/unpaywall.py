"""Unpaywall open-access location adapter for DOI full text."""

from uuid import UUID

from app.clients.scholarly.base import BaseHttpScholarlySource, ScholarlySourceError
from app.core.config import settings
from app.schemas.literature_research.discovery import SourcePage, SourceQuery
from app.schemas.literature_research.evidence import (
    FullTextCandidate,
    FullTextSource,
    LicenseDecision,
)


class UnpaywallClient(BaseHttpScholarlySource):
    name = "unpaywall"
    endpoint = "https://api.unpaywall.org/v2"

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        del query, cursor
        raise NotImplementedError("Unpaywall is an OA resolver, not a discovery source")

    async def candidate(self, *, version_id: UUID, doi: str) -> FullTextCandidate | None:
        if not settings.CROSSREF_MAILTO:
            raise ValueError("CROSSREF_MAILTO is required for Unpaywall requests")
        try:
            response = await self._get(
                f"{self.endpoint}/{doi}", params={"email": settings.CROSSREF_MAILTO}
            )
        except ScholarlySourceError as exc:
            if exc.status_code == 404:
                return None
            raise
        payload = response.json()
        location = payload.get("best_oa_location") or {}
        url = location.get("url_for_pdf") or location.get("url")
        if not payload.get("is_oa") or not url:
            return None
        license_value = location.get("license") or payload.get("oa_status") or "oa"
        return FullTextCandidate(
            version_id=version_id,
            source=FullTextSource.UNPAYWALL,
            url=url,
            license_decision=LicenseDecision.ALLOWED,
            license_reference=f"unpaywall:{doi}:{license_value}",
            content_type="application/pdf" if location.get("url_for_pdf") else None,
            is_open_access=True,
        )
