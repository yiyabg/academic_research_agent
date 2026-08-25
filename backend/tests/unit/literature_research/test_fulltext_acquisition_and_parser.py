"""Bounded full-text download and parsing adapter tests."""

import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    FullTextAcquisitionDecision,
    FullTextCandidate,
    FullTextSource,
    LicenseDecision,
)
from app.services.literature_research.document_parser import ResearchDocumentParser
from app.services.literature_research.document_safety import (
    DocumentScanResult,
    UnsafeDocumentError,
    reject_active_document_content,
    validate_resolved_addresses,
)
from app.services.literature_research.fulltext_acquisition import (
    FullTextAcquisitionService,
    validate_public_https_url,
)
from app.services.literature_research.object_store import LocalResearchObjectStore
from app.services.literature_research.pinned_http_transport import (
    DNSPinningNetworkBackend,
    DNSPinRegistry,
)


async def public_resolver(_hostname: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


class CleanScanner:
    async def ping(self) -> None:
        return None

    async def scan(self, _payload: bytes, *, content_type: str) -> DocumentScanResult:
        assert content_type in {"application/pdf", "text/html", "application/xhtml+xml"}
        return DocumentScanResult(status="CLEAN", engine="fixture")


class InfectedScanner(CleanScanner):
    async def scan(self, _payload: bytes, *, content_type: str) -> DocumentScanResult:
        del content_type
        return DocumentScanResult(
            status="INFECTED", engine="fixture-av", signature="Eicar-Signature"
        )


def allowed_candidate() -> FullTextCandidate:
    return FullTextCandidate(
        version_id=uuid4(),
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/article",
        license_decision=LicenseDecision.ALLOWED,
        license_reference="publisher:cc-by-4.0",
        is_open_access=True,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://publisher.example/paper.pdf",
        "https://localhost/paper.pdf",
        "https://127.0.0.1/paper.pdf",
        "https://10.0.0.1/paper.pdf",
    ],
)
def test_fulltext_url_rejects_non_https_and_local_networks(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_https_url(url)


def test_grobid_tei_headings_are_deduplicated_and_namespace_safe() -> None:
    tei = b"""<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
    <div><head>Methods</head><p>Text.</p></div>
    <div><head>Methods</head></div><div><head>Results</head></div>
    </body></text></TEI>"""
    assert ResearchDocumentParser._tei_headings(tei) == ["Methods", "Results"]
    assert ResearchDocumentParser._tei_headings(b"not xml") == []


def test_static_document_policy_rejects_active_pdf_content() -> None:
    with pytest.raises(ValueError, match="PDF_JAVASCRIPT"):
        reject_active_document_content(b"%PDF-1.7 /JavaScript", "application/pdf")


def test_dns_validation_rejects_mixed_public_private_answers() -> None:
    with pytest.raises(ValueError, match="non-public"):
        validate_resolved_addresses("publisher.example", ["93.184.216.34", "169.254.169.254"])


@pytest.mark.anyio
async def test_pinned_transport_connects_to_audited_ip_not_hostname() -> None:
    registry = DNSPinRegistry()
    registry.pin("publisher.example", ["93.184.216.34"])
    network = AsyncMock()
    expected_stream = object()
    network.connect_tcp.return_value = expected_stream
    backend = DNSPinningNetworkBackend(registry, backend=network)
    stream = await backend.connect_tcp("publisher.example", 443)
    assert stream is expected_stream
    network.connect_tcp.assert_awaited_once_with(
        "93.184.216.34",
        443,
        timeout=None,
        local_address=None,
        socket_options=None,
    )


@pytest.mark.anyio
async def test_pinned_transport_fails_closed_for_unvalidated_hostname() -> None:
    backend = DNSPinningNetworkBackend(DNSPinRegistry(), backend=AsyncMock())
    with pytest.raises(OSError, match="not DNS-pinned"):
        await backend.connect_tcp("unvalidated.example", 443)


@pytest.mark.anyio
async def test_acquisition_checks_type_and_stores_content_addressed_html(tmp_path) -> None:
    html = b"<html><body><h1>Methods</h1><p>Audit evidence.</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://publisher.example/article"
        return httpx.Response(
            200, content=html, headers={"Content-Type": "text/html; charset=utf-8"}
        )

    candidate = allowed_candidate()
    decision = FullTextAcquisitionDecision(
        version_id=candidate.version_id,
        allowed=True,
        selected=candidate,
        reason_code="LAWFUL_SOURCE_SELECTED",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FullTextAcquisitionService(
        client=client,
        object_store=LocalResearchObjectStore(tmp_path),
        resolver=public_resolver,
        scanner=CleanScanner(),
    )
    organization_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    acquired = await service.acquire(
        decision,
        organization_id=organization_id,
        project_id=project_id,
        run_id=run_id,
    )
    await client.aclose()

    assert acquired.document_sha256 == hashlib.sha256(html).hexdigest()
    assert acquired.document_sha256 in acquired.object_key
    assert acquired.content_type == "text/html"
    assert acquired.resolved_ips == ["publisher.example=93.184.216.34"]
    assert acquired.redirect_chain == ["https://publisher.example/article"]
    assert acquired.malware_scan_status == "CLEAN"
    assert acquired.malware_scan_engine == "fixture"
    assert f"tenants/{organization_id}/projects/{project_id}/runs/{run_id}" in acquired.object_key


@pytest.mark.anyio
async def test_acquisition_rejects_redirect_to_private_host_before_second_request(
    tmp_path,
) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/secret"})

    candidate = allowed_candidate()
    decision = FullTextAcquisitionDecision(
        version_id=candidate.version_id,
        allowed=True,
        selected=candidate,
        reason_code="LAWFUL_SOURCE_SELECTED",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FullTextAcquisitionService(
        client=client,
        object_store=LocalResearchObjectStore(tmp_path),
        resolver=public_resolver,
        scanner=CleanScanner(),
    )
    with pytest.raises(ValueError, match="non-global"):
        await service.acquire(
            decision,
            organization_id=uuid4(),
            project_id=uuid4(),
            run_id=uuid4(),
        )
    await client.aclose()
    assert requested == ["https://publisher.example/article"]


@pytest.mark.anyio
async def test_infected_document_is_rejected_without_storing(tmp_path) -> None:
    payload = b"<html>EICAR-STANDARD-ANTIVIRUS-TEST-FILE</html>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"Content-Type": "text/html"})

    candidate = allowed_candidate()
    decision = FullTextAcquisitionDecision(
        version_id=candidate.version_id,
        allowed=True,
        selected=candidate,
        reason_code="LAWFUL_SOURCE_SELECTED",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FullTextAcquisitionService(
        client=client,
        object_store=LocalResearchObjectStore(tmp_path),
        resolver=public_resolver,
        scanner=InfectedScanner(),
    )
    with pytest.raises(UnsafeDocumentError) as caught:
        await service.acquire(
            decision,
            organization_id=uuid4(),
            project_id=uuid4(),
            run_id=uuid4(),
        )
    await client.aclose()
    assert caught.value.result.status == "INFECTED"
    assert caught.value.result.engine == "fixture-av"
    assert not list(tmp_path.rglob("*.*"))


@pytest.mark.anyio
async def test_html_parser_removes_script_and_preserves_offsets(tmp_path) -> None:
    payload = (
        b"<html><script>ignore()</script><body><h1>Methods</h1><p>Audit evidence.</p></body></html>"
    )
    digest = hashlib.sha256(payload).hexdigest()
    store = LocalResearchObjectStore(tmp_path)
    key = await store.put("fixture/article.html", payload, content_type="text/html")
    acquired = AcquiredFullText(
        version_id=uuid4(),
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/article",
        license_reference="publisher:cc-by-4.0",
        content_type="text/html",
        size_bytes=len(payload),
        object_key=key,
        document_sha256=digest,
        malware_scan_status="CLEAN",
    )
    blocks = await ResearchDocumentParser(store).parse(acquired)
    assert len(blocks) == 1
    assert blocks[0].text == "Methods\n\nAudit evidence."
    assert "ignore" not in blocks[0].text
    assert blocks[0].char_start == 0


@pytest.mark.anyio
async def test_parser_rejects_object_hash_mismatch(tmp_path) -> None:
    store = LocalResearchObjectStore(tmp_path)
    key = await store.put("fixture/article.html", b"changed", content_type="text/html")
    acquired = AcquiredFullText(
        version_id=uuid4(),
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/article",
        license_reference="publisher:cc-by-4.0",
        content_type="text/html",
        size_bytes=7,
        object_key=key,
        document_sha256="a" * 64,
        malware_scan_status="CLEAN",
    )
    with pytest.raises(ValueError, match="hash"):
        await ResearchDocumentParser(store).parse(acquired)


@pytest.mark.anyio
async def test_short_html_is_ledgered_as_low_confidence(tmp_path) -> None:
    payload = b"<html><body>short text</body></html>"
    store = LocalResearchObjectStore(tmp_path)
    key = await store.put("fixture/short.html", payload, content_type="text/html")
    acquired = AcquiredFullText(
        version_id=uuid4(),
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/short",
        license_reference="publisher:cc-by-4.0",
        content_type="text/html",
        size_bytes=len(payload),
        object_key=key,
        document_sha256=hashlib.sha256(payload).hexdigest(),
        malware_scan_status="CLEAN",
    )
    parsed = await ResearchDocumentParser(store).parse_with_quality(acquired)
    assert parsed.quality.status == "PARSING_LOW_CONFIDENCE"
    assert parsed.quality.error_codes == ["TOTAL_TEXT_TOO_SHORT"]
    assert parsed.quality.blocks_object_key


@pytest.mark.anyio
async def test_parser_rejects_unscanned_document_before_reading(tmp_path) -> None:
    acquired = AcquiredFullText(
        version_id=uuid4(),
        source=FullTextSource.PUBLISHER,
        url="https://publisher.example/article",
        license_reference="publisher:cc-by-4.0",
        content_type="text/html",
        size_bytes=1,
        object_key="missing.html",
        document_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="malware-scanned CLEAN"):
        await ResearchDocumentParser(LocalResearchObjectStore(tmp_path)).parse(acquired)
