"""Figure/table caption indexing and hash-bound crop tests."""

import hashlib
from uuid import uuid4

import pymupdf
import pytest

from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    EvidenceLocator,
    FullTextSource,
)
from app.services.literature_research.figure_artifact_service import FigureArtifactService
from app.services.literature_research.figure_extractor import extract_figure_artifacts
from app.services.literature_research.object_store import LocalResearchObjectStore


def _evidence(quote: str, *, page: int | None = 7) -> EvidenceLocator:
    return EvidenceLocator(
        evidence_id="E_FIGURE_01",
        work_id=uuid4(),
        version_id=uuid4(),
        block_id="page-7",
        page_number=page,
        quote=quote,
        quote_start=0,
        quote_end=len(quote),
        block_text_sha256="a" * 64,
        document_sha256="b" * 64,
    )


def test_extracts_stable_figure_and_table_with_auditable_location() -> None:
    work_id = uuid4()
    evidence = _evidence(
        "Figure 2: Accuracy across three datasets.\n"
        "Some body text.\nTable 4 - Ablation results by component."
    )
    first = extract_figure_artifacts(work_id=work_id, evidence=[evidence])
    second = extract_figure_artifacts(work_id=work_id, evidence=[evidence])

    assert first == second
    assert [item.label for item in first] == ["Figure 2", "Table 4"]
    assert all(item.page_number == 7 for item in first)
    assert all(item.evidence_ids == ["E_FIGURE_01"] for item in first)
    assert all(item.document_sha256 == "b" * 64 for item in first)


def test_does_not_invent_figure_for_incidental_reference() -> None:
    artifacts = extract_figure_artifacts(
        work_id=uuid4(),
        evidence=[_evidence("As shown in Figure 3, the method improves accuracy.")],
    )
    assert artifacts == []


@pytest.mark.anyio
async def test_extracts_hash_bound_crop_bbox_and_exact_caption_numbers(tmp_path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.draw_rect(pymupdf.Rect(80, 80, 420, 280), width=2)
    caption = "Figure 2: Accuracy reaches 95.2% at 10 dB."
    page.insert_text((80, 320), caption, fontsize=12)
    payload = document.tobytes()
    document.close()

    reopened = pymupdf.open(stream=payload, filetype="pdf")
    caption_bbox = tuple(reopened[0].search_for(caption)[0])
    reopened.close()
    digest = hashlib.sha256(payload).hexdigest()
    store = LocalResearchObjectStore(tmp_path)
    key = await store.put("fixture/figure.pdf", payload, content_type="application/pdf")
    version_id = uuid4()
    acquired = AcquiredFullText(
        version_id=version_id,
        source=FullTextSource.ARXIV,
        url="https://arxiv.org/pdf/fixture",
        license_reference="fixture",
        content_type="application/pdf",
        size_bytes=len(payload),
        object_key=key,
        document_sha256=digest,
        malware_scan_status="CLEAN",
    )
    work_id = uuid4()
    evidence = EvidenceLocator(
        evidence_id="E_FIGURE_02",
        work_id=work_id,
        version_id=version_id,
        block_id="page-1-caption",
        page_number=1,
        quote=caption,
        quote_start=0,
        quote_end=len(caption),
        block_text_sha256=hashlib.sha256(caption.encode()).hexdigest(),
        document_sha256=digest,
        bbox=caption_bbox,
    )
    artifacts = await FigureArtifactService(store).extract(
        work_id=work_id, acquired=acquired, evidence=[evidence]
    )
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.extraction_status == "VERIFIED"
    assert artifact.bbox is not None
    assert artifact.image_object_key is not None
    assert hashlib.sha256(await store.get(artifact.image_object_key)).hexdigest() == (
        artifact.image_sha256
    )
    assert artifact.exact_numeric_values == ["10", "95.2%"]


@pytest.mark.anyio
async def test_extracts_table_cells_and_exact_values(tmp_path) -> None:
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

    digest = hashlib.sha256(payload).hexdigest()
    store = LocalResearchObjectStore(tmp_path)
    key = await store.put("fixture/table.pdf", payload, content_type="application/pdf")
    version_id, work_id = uuid4(), uuid4()
    acquired = AcquiredFullText(
        version_id=version_id,
        source=FullTextSource.ARXIV,
        url="https://arxiv.org/pdf/table-fixture",
        license_reference="fixture",
        content_type="application/pdf",
        size_bytes=len(payload),
        object_key=key,
        document_sha256=digest,
        malware_scan_status="CLEAN",
    )
    evidence = EvidenceLocator(
        evidence_id="E_TABLE_01",
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
    assert artifact.artifact_kind == "table"
    assert artifact.table_cells == [
        ["Method", "Accuracy"],
        ["Agent A", "95.2%"],
        ["Agent B", "91.0%"],
    ]
    assert artifact.exact_numeric_values == ["10", "91.0%", "95.2%"]
