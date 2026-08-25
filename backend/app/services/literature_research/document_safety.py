"""Fail-closed DNS and malware controls for scholarly full-text bytes."""

import asyncio
import ipaddress
import socket
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings

AddressResolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class DocumentScanResult:
    status: str
    engine: str
    engine_version: str | None = None
    signature: str | None = None


class DocumentScanner(Protocol):
    async def ping(self) -> None: ...

    async def scan(self, payload: bytes, *, content_type: str) -> DocumentScanResult: ...


class UnsafeDocumentError(ValueError):
    def __init__(
        self,
        result: DocumentScanResult,
        *,
        resolved_ips: list[str],
        redirect_chain: list[str],
    ) -> None:
        self.result = result
        self.resolved_ips = resolved_ips
        self.redirect_chain = redirect_chain
        super().__init__(f"Unsafe full-text document: {result.signature or result.status}")


async def resolve_public_addresses(hostname: str, port: int) -> list[str]:
    """Resolve every address and reject mixed/public-private DNS answers."""
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ValueError(f"Full-text hostname cannot be resolved: {hostname}") from exc
    addresses = sorted({str(record[4][0]).split("%", 1)[0] for record in records})
    return validate_resolved_addresses(hostname, addresses)


def validate_resolved_addresses(hostname: str, addresses: list[str]) -> list[str]:
    """Reject empty, invalid, private, reserved, or mixed DNS answers."""
    if not addresses:
        raise ValueError(f"Full-text hostname has no addresses: {hostname}")
    try:
        non_public = [value for value in addresses if not ipaddress.ip_address(value).is_global]
    except ValueError as exc:
        raise ValueError(f"Full-text hostname returned an invalid address: {hostname}") from exc
    if non_public:
        raise ValueError(
            f"Full-text hostname resolves to a non-public address: {', '.join(non_public)}"
        )
    return addresses


def reject_active_document_content(payload: bytes, content_type: str) -> None:
    """Reject known active-content primitives before invoking the AV daemon."""
    if content_type != "application/pdf":
        return
    folded = payload.lower()
    dangerous_tokens = {
        b"/javascript": "PDF_JAVASCRIPT",
        b"/launch": "PDF_LAUNCH_ACTION",
        b"/embeddedfile": "PDF_EMBEDDED_FILE",
        b"/richmedia": "PDF_RICH_MEDIA",
    }
    detected = [reason for token, reason in dangerous_tokens.items() if token in folded]
    if detected:
        raise ValueError(f"Document contains forbidden active content: {','.join(detected)}")


class ClamAVDocumentScanner:
    """Minimal clamd INSTREAM client; any transport ambiguity fails closed."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.host = host or settings.CLAMAV_HOST
        self.port = port or settings.CLAMAV_PORT
        self.timeout_seconds = timeout_seconds or settings.CLAMAV_TIMEOUT_SECONDS

    async def ping(self) -> None:
        if not self.host:
            raise RuntimeError("CLAMAV_HOST is required for full-research readiness")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout_seconds
        )
        try:
            writer.write(b"zPING\0")
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\0"), timeout=self.timeout_seconds)
            if response.rstrip(b"\0") != b"PONG":
                raise RuntimeError(f"Unexpected clamd PING response: {response!r}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def scan(self, payload: bytes, *, content_type: str) -> DocumentScanResult:
        try:
            reject_active_document_content(payload, content_type)
        except ValueError as exc:
            return DocumentScanResult(status="REJECTED", engine="static-policy", signature=str(exc))
        if not self.host:
            raise RuntimeError("CLAMAV_HOST is required for fail-closed full-text scanning")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout_seconds
        )
        try:
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(payload), 64 * 1024):
                chunk = payload[offset : offset + 64 * 1024]
                writer.write(struct.pack("!I", len(chunk)))
                writer.write(chunk)
                await writer.drain()
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readuntil(b"\0"), timeout=self.timeout_seconds)
        finally:
            writer.close()
            await writer.wait_closed()
        response = raw.rstrip(b"\0").decode("utf-8", errors="replace")
        if response.endswith(" OK"):
            return DocumentScanResult(status="CLEAN", engine="clamav")
        if response.endswith(" FOUND"):
            signature = response.rsplit(": ", 1)[-1].removesuffix(" FOUND")
            return DocumentScanResult(status="INFECTED", engine="clamav", signature=signature)
        raise RuntimeError(f"Ambiguous clamd scan response: {response}")
