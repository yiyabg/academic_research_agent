"""Deterministic figure/table caption indexing from evidence blocks."""

import hashlib
import re
from uuid import UUID

from app.schemas.literature_research.analysis import FigureArtifact
from app.schemas.literature_research.evidence import EvidenceLocator

_CAPTION = re.compile(
    r"(?im)^(?P<label>(?:fig(?:ure)?\.?|table)\s+[A-Za-z]?\d+[A-Za-z]?)"
    r"(?:\s*[:.\-—]\s*|\s+)(?P<caption>[^\n]{2,1000})"
)


def extract_figure_artifacts(
    *, work_id: UUID, evidence: list[EvidenceLocator]
) -> list[FigureArtifact]:
    """Build stable caption records without inferring invisible image content."""
    artifacts: dict[str, FigureArtifact] = {}
    for locator in evidence:
        for match in _CAPTION.finditer(locator.quote):
            label = match.group("label").strip()
            caption = match.group("caption").strip()
            identity = f"{work_id}:{locator.evidence_id}:{label.lower()}"
            figure_id = f"F_{hashlib.sha256(identity.encode()).hexdigest()[:16].upper()}"
            artifacts.setdefault(
                figure_id,
                FigureArtifact(
                    figure_id=figure_id,
                    label=label,
                    caption=caption,
                    page_number=locator.page_number,
                    evidence_ids=[locator.evidence_id],
                    document_sha256=locator.document_sha256,
                    bbox=locator.bbox,
                    artifact_kind=(
                        "table" if label.casefold().startswith("table") else "figure"
                    ),
                ),
            )
    return sorted(artifacts.values(), key=lambda item: (item.page_number or 0, item.label))
