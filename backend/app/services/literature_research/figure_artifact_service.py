"""Extract hash-bound figure/table crops and exact numeric sources from PDFs."""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

import pymupdf

from app.schemas.literature_research.analysis import FigureArtifact
from app.schemas.literature_research.evidence import AcquiredFullText, EvidenceLocator
from app.services.literature_research.figure_extractor import extract_figure_artifacts
from app.services.literature_research.object_store import ResearchObjectStore

_NUMBER = re.compile(r"(?<![\w.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+|%)?")


@dataclass(frozen=True, slots=True)
class _Crop:
    bbox: tuple[float, float, float, float]
    png: bytes
    table_cells: list[list[str]]


def _distance(rect: pymupdf.Rect, caption: pymupdf.Rect) -> tuple[float, float]:
    if rect.y1 <= caption.y0:
        vertical = caption.y0 - rect.y1
        preference = 0.0
    elif rect.y0 >= caption.y1:
        vertical = rect.y0 - caption.y1
        preference = 1.0
    else:
        vertical = 0.0
        preference = 0.5
    horizontal = abs(rect.x0 - caption.x0) + abs(rect.x1 - caption.x1)
    return vertical + preference, horizontal


def _extract_crop(payload: bytes, artifact: FigureArtifact) -> _Crop | None:
    if artifact.page_number is None or artifact.bbox is None:
        return None
    document = pymupdf.open(stream=payload, filetype="pdf")
    try:
        if artifact.page_number > document.page_count:
            return None
        page = document[artifact.page_number - 1]
        caption = pymupdf.Rect(artifact.bbox)
        candidates: list[tuple[pymupdf.Rect, list[list[str]]]] = []
        if artifact.artifact_kind == "table":
            try:
                for table in page.find_tables().tables:
                    cells = [
                        [str(value or "").strip() for value in row]
                        for row in (table.extract() or [])
                    ]
                    candidates.append((pymupdf.Rect(table.bbox), cells))
            except Exception:
                pass
        else:
            for info in page.get_image_info(xrefs=True):
                bbox = info.get("bbox")
                if bbox:
                    candidates.append((pymupdf.Rect(bbox), []))
            try:
                for rect in page.cluster_drawings():
                    if rect.get_area() >= page.rect.get_area() * 0.01:
                        candidates.append((pymupdf.Rect(rect), []))
            except Exception:
                pass
        if not candidates:
            return None
        region, table_cells = min(candidates, key=lambda item: _distance(item[0], caption))
        region &= page.rect
        if region.is_empty or region.get_area() < 100:
            return None
        png = page.get_pixmap(clip=region, dpi=160, alpha=False).tobytes("png")
        return _Crop(
            bbox=(region.x0, region.y0, region.x1, region.y1),
            png=png,
            table_cells=table_cells,
        )
    finally:
        document.close()


class FigureArtifactService:
    def __init__(self, object_store: ResearchObjectStore) -> None:
        self.object_store = object_store

    async def extract(
        self,
        *,
        work_id: UUID,
        acquired: AcquiredFullText,
        evidence: list[EvidenceLocator],
    ) -> list[FigureArtifact]:
        if acquired.content_type != "application/pdf":
            return []
        payload = await self.object_store.get(acquired.object_key)
        if hashlib.sha256(payload).hexdigest() != acquired.document_sha256:
            raise ValueError("Figure extraction source hash does not match acquisition ledger")
        captions = extract_figure_artifacts(work_id=work_id, evidence=evidence)
        output: list[FigureArtifact] = []
        prefix, separator, _ = acquired.object_key.partition("/fulltext/")
        root = f"{prefix}/figures" if separator else "figures"
        for artifact in captions:
            crop = await asyncio.to_thread(_extract_crop, payload, artifact)
            if crop is None:
                continue
            digest = hashlib.sha256(crop.png).hexdigest()
            key = f"{root}/{work_id}/{artifact.figure_id}-{digest[:16]}.png"
            object_key = await self.object_store.put(
                key,
                crop.png,
                content_type="image/png",
                metadata={
                    "source-document-sha256": acquired.document_sha256,
                    "crop-sha256": digest,
                },
            )
            numeric_text = [artifact.caption]
            numeric_text.extend(cell for row in crop.table_cells for cell in row)
            exact_values = sorted(
                {match.group(0) for text in numeric_text for match in _NUMBER.finditer(text)}
            )
            output.append(
                FigureArtifact.model_validate(
                    {
                        **artifact.model_dump(mode="python"),
                        "bbox": crop.bbox,
                        "image_object_key": object_key,
                        "image_sha256": digest,
                        "extraction_status": "VERIFIED",
                        "table_cells": crop.table_cells,
                        "exact_numeric_values": exact_values,
                    }
                )
            )
        return output
