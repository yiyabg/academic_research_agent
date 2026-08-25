"""Bounded lawful full-text download into immutable object storage."""

import hashlib
import ipaddress
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx

from app.core.config import settings
from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    FullTextAcquisitionDecision,
)
from app.services.literature_research.document_safety import (
    ClamAVDocumentScanner,
    DocumentScanner,
    DocumentScanResult,
    UnsafeDocumentError,
    resolve_public_addresses,
)
from app.services.literature_research.object_store import (
    ResearchObjectStore,
    get_research_object_store,
    research_object_prefix,
)
from app.services.literature_research.pinned_http_transport import (
    DNSPinnedAsyncHTTPTransport,
    DNSPinRegistry,
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


def validate_public_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Full-text acquisition requires a public HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Credentials are forbidden in full-text URLs")
    if parsed.port not in {None, 443}:
        raise ValueError("Full-text acquisition only permits HTTPS port 443")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local full-text hosts are forbidden")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Private or non-global full-text IP addresses are forbidden")


class FullTextAcquisitionService:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        object_store: ResearchObjectStore | None = None,
        resolver: Resolver | None = None,
        scanner: DocumentScanner | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        max_redirects: int = 5,
    ) -> None:
        self._owns_client = client is None
        if client is None:
            self.pin_registry: DNSPinRegistry | None = DNSPinRegistry()
            self.client = httpx.AsyncClient(
                timeout=settings.SCHOLARLY_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
                headers={"User-Agent": settings.SCHOLARLY_USER_AGENT},
                transport=DNSPinnedAsyncHTTPTransport(self.pin_registry),
            )
        else:
            self.pin_registry = None
            self.client = client
        self.object_store = object_store or get_research_object_store()
        self.resolver = resolver or resolve_public_addresses
        self.scanner = scanner or ClamAVDocumentScanner()
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects

    async def _download(self, url: str) -> tuple[bytes, str, list[str], list[str]]:
        current_url = url
        redirect_chain = [url]
        resolved_ips: list[str] = []
        for redirect_count in range(self.max_redirects + 1):
            validate_public_https_url(current_url)
            parsed = urlparse(current_url)
            host = parsed.hostname or ""
            addresses = await self.resolver(host, parsed.port or 443)
            if self.pin_registry is not None:
                self.pin_registry.pin(host, addresses)
            resolved_ips.extend(
                item
                for item in (f"{host}={address}" for address in addresses)
                if item not in resolved_ips
            )
            request = self.client.build_request("GET", current_url)
            response = await self.client.send(request, stream=True, follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get("Location")
                await response.aclose()
                if not location:
                    raise ValueError("Full-text redirect is missing a Location header")
                if redirect_count >= self.max_redirects:
                    raise ValueError("Full-text response exceeds the redirect limit")
                current_url = urljoin(current_url, location)
                redirect_chain.append(current_url)
                continue
            try:
                response.raise_for_status()
            except Exception:
                await response.aclose()
                raise
            declared_size = response.headers.get("Content-Length")
            if declared_size and declared_size.isdigit() and int(declared_size) > self.max_bytes:
                await response.aclose()
                raise ValueError("Full-text response exceeds configured size limit")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            chunks: list[bytes] = []
            size = 0
            try:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("Full-text response exceeds configured size limit")
                    chunks.append(chunk)
            finally:
                await response.aclose()
            return b"".join(chunks), content_type, resolved_ips, redirect_chain
        raise ValueError("Full-text redirect state is invalid")

    async def acquire(
        self,
        decision: FullTextAcquisitionDecision,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
    ) -> AcquiredFullText:
        if not decision.allowed or decision.selected is None:
            raise ValueError("Acquisition decision does not authorize a download")
        selected = decision.selected
        url = str(selected.url)
        validate_public_https_url(url)
        payload, content_type, resolved_ips, redirect_chain = await self._download(url)
        if content_type not in {"application/pdf", "text/html", "application/xhtml+xml"}:
            raise ValueError(f"Unsupported full-text content type: {content_type or 'missing'}")
        if content_type == "application/pdf" and not payload.startswith(b"%PDF-"):
            raise ValueError("Response claims PDF but does not have a PDF signature")
        scan_result: DocumentScanResult = await self.scanner.scan(
            payload, content_type=content_type
        )
        if scan_result.status != "CLEAN":
            raise UnsafeDocumentError(
                scan_result,
                resolved_ips=resolved_ips,
                redirect_chain=redirect_chain,
            )
        digest = hashlib.sha256(payload).hexdigest()
        suffix = "pdf" if content_type == "application/pdf" else "html"
        prefix = research_object_prefix(
            organization_id=organization_id, project_id=project_id, run_id=run_id
        )
        key = f"{prefix}/fulltext/{selected.version_id}/{digest}.{suffix}"
        object_key = await self.object_store.put(
            key,
            payload,
            content_type=content_type,
            metadata={
                "sha256": digest,
                "license-reference": selected.license_reference or "",
            },
        )
        return AcquiredFullText(
            version_id=selected.version_id,
            source=selected.source,
            url=selected.url,
            license_reference=selected.license_reference or "",
            content_type=content_type,
            size_bytes=len(payload),
            object_key=object_key,
            document_sha256=digest,
            resolved_ips=resolved_ips,
            redirect_chain=redirect_chain,
            malware_scan_status=scan_result.status,
            malware_scan_engine=scan_result.engine,
            malware_signature=scan_result.signature,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
