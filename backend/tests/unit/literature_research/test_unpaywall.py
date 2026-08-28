"""Unpaywall lawful OA candidate contract tests."""

from uuid import uuid4

import httpx
import pytest

from app.clients.scholarly.unpaywall import UnpaywallClient
from app.core.config import settings
from app.schemas.literature_research.evidence import LicenseDecision


@pytest.mark.anyio
async def test_unpaywall_candidate_carries_oa_license_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CROSSREF_MAILTO", "researcher@example.org")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["email"] == "researcher@example.org"
        return httpx.Response(
            200,
            json={
                "is_oa": True,
                "oa_status": "gold",
                "best_oa_location": {
                    "url_for_pdf": "https://publisher.example/paper.pdf",
                    "license": "cc-by",
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    candidate = await UnpaywallClient(client).candidate(version_id=uuid4(), doi="10.1000/agent")
    await client.aclose()
    assert candidate is not None
    assert candidate.license_decision == LicenseDecision.ALLOWED
    assert candidate.license_reference == "unpaywall:10.1000/agent:cc-by"


@pytest.mark.anyio
async def test_unpaywall_closed_record_returns_no_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CROSSREF_MAILTO", "researcher@example.org")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"is_oa": False}))
    )
    candidate = await UnpaywallClient(client).candidate(version_id=uuid4(), doi="10.1000/closed")
    await client.aclose()
    assert candidate is None
