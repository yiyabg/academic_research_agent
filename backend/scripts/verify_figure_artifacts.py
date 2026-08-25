"""Verify PDF table reconstruction, crop hashing, and numeric provenance."""

import asyncio
import hashlib
from uuid import uuid4

import pymupdf

from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    EvidenceLocator,
    FullTextSource,
)
from app.services.literature_research.figure_artifact_service import FigureArtifactService
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


async def main() -> None:
    document = pymupdf.open()
    page = document.new_page()
    for x in (80, 220, 360):
        page.draw_line((x, 80), (x, 200))
    for y in (80, 120, 160, 200):
        page.draw_line((80, y), (360, y))
    for x, y, text in (
        (90, 105, "Method"),
        (230, 105, "Accuracy"),
        (90, 145, "Agent A"),
        (230, 145, "95.2%"),
        (90, 185, "Agent B"),
        (230, 185, "91.0%"),
    ):
        page.insert_text((x, y), text, fontsize=10)
    caption = "Table 1: Accuracy at 10 dB."
    page.insert_text((80, 230), caption, fontsize=11)
    payload = document.tobytes()
    document.close()
    reopened = pymupdf.open(stream=payload, filetype="pdf")
    caption_bbox = tuple(reopened[0].search_for(caption)[0])
    reopened.close()

    store = MemoryObjectStore()
    source_key = await store.put("fixture/table.pdf", payload, content_type="application/pdf")
    digest = hashlib.sha256(payload).hexdigest()
    work_id, version_id = uuid4(), uuid4()
    acquired = AcquiredFullText(
        version_id=version_id,
        source=FullTextSource.ARXIV,
        url="https://arxiv.org/pdf/fixture",
        license_reference="verification-fixture",
        content_type="application/pdf",
        size_bytes=len(payload),
        object_key=source_key,
        document_sha256=digest,
        malware_scan_status="CLEAN",
    )
    evidence = EvidenceLocator(
        evidence_id="E_TABLE_VERIFY",
        work_id=work_id,
        version_id=version_id,
        block_id="page-1-table-caption",
        page_number=1,
        quote=caption,
        quote_start=0,
        quote_end=len(caption),
        block_text_sha256=hashlib.sha256(caption.encode()).hexdigest(),
        document_sha256=digest,
        bbox=caption_bbox,
    )
    artifact = (
        await FigureArtifactService(store).extract(
            work_id=work_id, acquired=acquired, evidence=[evidence]
        )
    )[0]
    crop = await store.get(artifact.image_object_key or "")
    if hashlib.sha256(crop).hexdigest() != artifact.image_sha256:
        raise RuntimeError("Figure crop hash verification failed")
    if artifact.exact_numeric_values != ["10", "91.0%", "95.2%"]:
        raise RuntimeError(f"Unexpected numeric ledger: {artifact.exact_numeric_values}")
    print(artifact.model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
