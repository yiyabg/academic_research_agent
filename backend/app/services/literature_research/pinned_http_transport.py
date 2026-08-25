"""HTTP transport that connects only to prevalidated DNS addresses."""

from collections.abc import Iterable
from typing import cast

import httpcore
import httpx


class DNSPinRegistry:
    def __init__(self) -> None:
        self._addresses: dict[str, tuple[str, ...]] = {}

    def pin(self, hostname: str, addresses: list[str]) -> None:
        if not addresses:
            raise ValueError("Cannot pin a hostname without validated addresses")
        self._addresses[hostname.lower()] = tuple(addresses)

    def addresses_for(self, hostname: str) -> tuple[str, ...]:
        addresses = self._addresses.get(hostname.lower())
        if not addresses:
            raise OSError(f"Outbound hostname was not DNS-pinned: {hostname}")
        return addresses


class DNSPinningNetworkBackend(httpcore.AsyncNetworkBackend):
    """Preserve TLS SNI while replacing TCP DNS lookup with audited IPs."""

    def __init__(
        self,
        registry: DNSPinRegistry,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self.registry = registry
        self.backend = backend or cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        normalized_host = host.decode() if isinstance(host, bytes) else host
        last_error: OSError | None = None
        for address in self.registry.addresses_for(normalized_host):
            try:
                return await self.backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except OSError as exc:
                last_error = exc
        raise OSError(f"Every pinned address failed for {normalized_host}") from last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[tuple] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise OSError("Unix sockets are forbidden for scholarly HTTP acquisition")

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


class DNSPinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, registry: DNSPinRegistry) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=DNSPinningNetworkBackend(registry),
        )
