"""Create a scanned PDF fixture and verify OCR plus parsing-quality gates."""

import asyncio
import hashlib
from uuid import uuid4

import pymupdf

from app.schemas.literature_research.evidence import AcquiredFullText, FullTextSource
from app.services.literature_research.document_parser import ResearchDocumentParser
from app.services.literature_research.object_store import ResearchObjectStore


class MemoryObjectStore(ResearchObjectStore):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        del content_type, metadata
        self.objects[key] = data
        return key

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def healthcheck(self) -> None:
        return None


def scanned_pdf() -> bytes:
    text = (
        "Methods and results. The controlled experiment reports accuracy, latency, "
        "sample size, confidence intervals, ablation settings, and reproducibility. "
    ) * 12
    source = pymupdf.open()
    page = source.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 792), text, fontsize=14)
    image = page.get_pixmap(dpi=200, alpha=False).tobytes("png")
    source.close()

    scanned = pymupdf.open()
    page = scanned.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=image)
    payload = scanned.tobytes()
    scanned.close()
    return payload


async def main() -> None:
    payload = scanned_pdf()
    store = MemoryObjectStore()
    key = await store.put("fixture/scanned.pdf", payload, content_type="application/pdf")
    acquired = AcquiredFullText(
        version_id=uuid4(),
        source=FullTextSource.ARXIV,
        url="https://arxiv.org/pdf/fixture",
        license_reference="verification-fixture",
        content_type="application/pdf",
        size_bytes=len(payload),
        object_key=key,
        document_sha256=hashlib.sha256(payload).hexdigest(),
        malware_scan_status="CLEAN",
    )
    parsed = await ResearchDocumentParser(store).parse_with_quality(acquired)
    if parsed.quality.status != "PASSED" or parsed.quality.ocr_page_count != 1:
        raise RuntimeError(f"OCR quality verification failed: {parsed.quality}")
    if not parsed.blocks or not all(block.bbox for block in parsed.blocks):
        raise RuntimeError("OCR blocks are missing page coordinates")
    print(parsed.quality.model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
