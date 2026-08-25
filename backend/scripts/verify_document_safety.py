"""Verify clamd and DNS-pinned HTTPS transport from the deployed backend."""

import asyncio

from app.services.literature_research.document_safety import ClamAVDocumentScanner
from app.services.literature_research.fulltext_acquisition import FullTextAcquisitionService

EICAR_HEX = (
    "58354f2150254041505b345c505a58353428505e2937434329377d244549434152"
    "2d5354414e444152442d414e544956495255532d544553542d46494c452124482b482a"
)


async def main() -> None:
    scanner = ClamAVDocumentScanner()
    await scanner.ping()
    benign = await scanner.scan(b"plain scholarly text", content_type="text/html")
    eicar = await scanner.scan(bytes.fromhex(EICAR_HEX), content_type="text/html")
    if benign.status != "CLEAN" or eicar.status != "INFECTED":
        raise RuntimeError(f"Unexpected malware results: benign={benign}, eicar={eicar}")

    acquisition = FullTextAcquisitionService(max_bytes=1024 * 1024)
    try:
        body, content_type, resolved_ips, redirect_chain = await acquisition._download(
            "https://arxiv.org/"
        )
    finally:
        await acquisition.aclose()
    if not body or not resolved_ips:
        raise RuntimeError("DNS-pinned HTTPS probe produced no bytes or address ledger")
    print(
        {
            "clamav_benign": benign.status,
            "clamav_eicar": eicar.status,
            "clamav_signature": eicar.signature,
            "https_bytes": len(body),
            "https_content_type": content_type,
            "resolved_ips": resolved_ips,
            "redirect_chain": redirect_chain,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
