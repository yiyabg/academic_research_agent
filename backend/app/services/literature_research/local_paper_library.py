"""Manual, read-only ingestion and retrieval for a Zotero Better BibTeX folder."""
# ruff: noqa: RUF001, RUF003 - User-facing Chinese text intentionally uses Chinese punctuation.

from __future__ import annotations

import csv
import hashlib
import html as html_module
import io
import json
import logging
import os
import re
import subprocess
from collections import Counter, OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID

import fitz
import httpx
import redis.asyncio as aioredis
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from rank_bm25 import BM25Okapi
from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.db.models.local_paper_library import (
    LocalPaper,
    LocalPaperChunk,
    LocalPaperChunkLocator,
    LocalPaperDocumentVersion,
    LocalPaperFigure,
    LocalPaperLibrary,
    LocalPaperQuarantineItem,
    LocalPaperRetrievalRun,
    LocalPaperSection,
    LocalPaperSyncEvent,
    LocalPaperSyncRun,
    LocalPaperTable,
)
from app.schemas.literature_research.local_library import (
    LocalLibraryQuarantineRead,
    LocalLibraryStatusRead,
    LocalLibrarySyncRunRead,
    LocalPaperAskResponse,
    LocalPaperCitationRead,
    LocalPaperEvidenceRead,
    LocalPaperRead,
    LocalPaperSearchRequest,
    LocalPaperSearchResponse,
)
from app.services.literature_research.local_paper_reranker import (
    BGERerankerV2M3HTTP,
    LocalPaperReranker,
)
from app.services.literature_research.local_paper_bibtex_catalog import (
    authors as _authors,
    normalize_doi as _doi,
    parse_bibtex,
    publication_year as _year,
)
from app.services.literature_research.local_paper_source_matcher import (
    SUPPORTED_SUFFIXES,
    attachment_paths,
    relative_source as _relative,
    safe_source as _safe_source,
    sha256_file as _sha256,
)
from app.services.literature_research.local_paper_grounded_qa import (
    GroundedAnswer,
    GroundedClaim,
    render_grounded_answer as _render_grounded_answer,
)
from app.services.literature_research.local_paper_vector_index import (
    LocalPaperVectorChunk,
    LocalPaperVectorIndex,
)
from app.services.llm_provider import build_llm_model, llm_is_configured

_HEADING_NUMBER = re.compile(
    r"^(?:(?:[IVXLC]+|\d+(?:\.\d+){0,4})[.)]?)\s+|(?:abstract|introduction|conclusion|references|"
    r"methods?|results?|discussion|acknowledg(?:e)?ments?|摘要|引言|结论|参考文献)\b",
    re.I,
)
_BM25_TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.I)
_FIGURE_REFERENCE = re.compile(r"(?i)(?:\bfig(?:ure)?\.?\s*|图\s*)(\d+[a-z]?)")
_REFERENCE_SECTION = re.compile(r"(?i)\b(?:references?|bibliography)\b|参考文献")
_UTF16_SURROGATE = re.compile(r"[\ud800-\udfff]")
logger = logging.getLogger(__name__)


def _strip_null(text: str) -> str:
    """Make extracted PDF/HTML text safe for UTF-8, JSON, hashes and PostgreSQL.

    A surrogate code point is only an internal half of a UTF-16 pair.  PyMuPDF
    can expose a lone half from malformed font mappings; it is not a Unicode
    scalar value and Python's UTF-8 encoder correctly rejects it.  Replacing it
    with U+FFFD preserves the evidence location while making the document
    processable.  NUL is removed because PostgreSQL text values reject it.
    """
    return _UTF16_SURROGATE.sub("\ufffd", text.replace("\x00", ""))


class _SafeHTMLText(HTMLParser):
    """Extract text only; scripts, styles and event-bearing markup are never executed."""

    ignored = {"script", "style", "noscript", "svg", "canvas", "template"}
    breaks = {"p", "div", "br", "li", "section", "article", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in self.ignored:
            self._ignored_depth += 1
        if not self._ignored_depth and tag in self.breaks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.ignored and self._ignored_depth:
            self._ignored_depth -= 1
        if not self._ignored_depth and tag in self.breaks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(self.parts))).strip()


def _local_index_version() -> str:
    """Parser and embedding identity; changing either requires re-ingestion."""
    return (
        f"{settings.LOCAL_PAPER_INGESTION_VERSION}:"
        f"{settings.LOCAL_PAPER_EMBEDDING_MODEL}:{settings.LOCAL_PAPER_EMBEDDING_DIM}"
    )


def _collection_name(owner_id: UUID) -> str:
    signature = hashlib.sha256(_local_index_version().encode()).hexdigest()[:12]
    return f"local_papers_{owner_id.hex}_{signature}"


def _local_paper_sync_event_payload(
    *,
    sync_run_id: UUID,
    status: str,
    summary: dict[str, object],
    error_message: str | None,
) -> dict[str, object]:
    """Create a plain, post-commit-safe event envelope without ORM access."""
    return {
        "type": "local_paper_sync_event",
        "data": {
            "sync_run_id": str(sync_run_id),
            "status": status,
            "summary_json": summary,
            "error_message": error_message,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }


async def _publish_local_paper_sync_event(payload: dict[str, object]) -> None:
    """Best-effort fan-out of a previously committed, plain JSON payload."""
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("sync_run_id"), str):
        logger.warning("Refusing to publish malformed local-paper sync event")
        return
    redis = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    try:
        await redis.publish(
            f"local_paper_sync:{data['sync_run_id']}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        # Progress is already durable in PostgreSQL. A transient Redis outage
        # must not roll back a successfully indexed paper.
        logger.warning("Failed to publish local-paper sync progress: %s", exc)
    finally:
        await redis.aclose()


@dataclass(frozen=True)
class SourceParagraph:
    page_number: int
    paragraph_index: int
    text: str
    bbox: list[float] | None
    font_size: float = 0.0
    kind: str = "text"
    figure_index: int | None = None


@dataclass(frozen=True)
class SourceFigure:
    page_number: int
    figure_index: int
    bbox: list[float]
    figure_label: str | None
    caption_text: str | None
    ocr_text: str | None
    image_sha256: str | None


@dataclass(frozen=True)
class SourceTable:
    page_number: int
    table_index: int
    bbox: list[float] | None
    markdown_text: str


@dataclass(frozen=True)
class SourceSection:
    page_number: int
    section_index: int
    heading: str
    heading_level: int
    content: str
    bbox: list[float] | None
    paragraphs: tuple[SourceParagraph, ...]


@dataclass(frozen=True)
class StructuredSource:
    sections: tuple[SourceSection, ...]
    figures: tuple[SourceFigure, ...]
    tables: tuple[SourceTable, ...] = ()
    parser_name: str = "pymupdf"

    @property
    def pages(self) -> list[tuple[int, str]]:
        by_page: dict[int, list[str]] = {}
        for section in self.sections:
            # This projection feeds the legacy abstract/introduction/conclusion
            # extractor.  Keep the structural heading here as well as in the
            # parent row: otherwise regular expressions never see e.g.
            # "Abstract" followed by its paragraph and silently return no
            # structured deep-analysis fields.
            content = section.content
            if section.heading and section.heading != "正文":
                content = f"{section.heading}\n{content}"
            by_page.setdefault(section.page_number, []).append(content)
        return [(page, "\n\n".join(parts)) for page, parts in sorted(by_page.items())]


class DoclingPrimaryParseError(RuntimeError):
    """The v7 PDF parser is unavailable or did not produce structure."""


@dataclass(frozen=True)
class V7ParentSection:
    """A logical parent section built before it is written to PostgreSQL."""

    page_number: int
    page_end: int
    section_index: int
    heading: str
    heading_level: int
    heading_path: tuple[str, ...]
    section_type: str
    content: str
    paragraphs: tuple[SourceParagraph, ...]
    bbox: list[float] | None
    token_count: int


@dataclass(frozen=True)
class V7ChildChunk:
    """A BGE-token bounded, semantically coherent retrieval child."""

    page_number: int
    paragraph_index: int
    text: str
    bbox: list[float] | None
    kind: str
    figure_index: int | None
    token_count: int
    locators: tuple[SourceParagraph, ...]


def _section_type(heading: str) -> str:
    compact = _normalise_text(heading).casefold()
    rules = (
        ("ABSTRACT", r"\babstract\b|摘要"),
        ("INTRODUCTION", r"\bintroduction\b|引言|绪论"),
        ("METHODS", r"\bmethod(?:ology|s)?\b|\bapproach\b|方法"),
        ("ABLATION", r"\bablation\b|消融"),
        ("EXPERIMENTS", r"\bexperiment(?:s|al)?\b|\bevaluation\b|实验|评估"),
        ("RESULTS", r"\bresults?\b|结果"),
        ("DISCUSSION", r"\bdiscussion\b|讨论"),
        ("CONCLUSION", r"\bconclusion(?:s)?\b|结论"),
        ("REFERENCES", r"\breferences?\b|\bbibliography\b|参考文献"),
    )
    for name, pattern in rules:
        if re.search(pattern, compact, re.I):
            return name
    return "BODY"


class BGETokenizerHTTPClient:
    """Use the same BGE-M3 tokenizer as the embedding container.

    This is intentionally an internal Docker request, not an OpenAI call and
    not a second transformer process in each Celery worker.
    """

    def __init__(self, service_url: str | None = None) -> None:
        self.service_url = (service_url or settings.LOCAL_PAPER_EMBEDDING_SERVICE_URL).rstrip("/")

    async def token_counts(self, texts: list[str]) -> list[int]:
        counts: list[int] = []
        for start in range(0, len(texts), 256):
            batch = texts[start : start + 256]
            async with httpx.AsyncClient(
                timeout=settings.LOCAL_PAPER_MODEL_HTTP_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(f"{self.service_url}/tokenize", json={"texts": batch})
            response.raise_for_status()
            token_ids = response.json().get("token_ids")
            if not isinstance(token_ids, list) or len(token_ids) != len(batch):
                raise RuntimeError("BGE tokenizer returned a count that does not match its input")
            counts.extend(len(ids) if isinstance(ids, list) else 0 for ids in token_ids)
        return counts


class V7StructureChunker:
    """Build clean logical parents and <=500-token children from source blocks."""

    def __init__(self, tokenizer: BGETokenizerHTTPClient | None = None) -> None:
        self.tokenizer = tokenizer or BGETokenizerHTTPClient()

    async def build(self, source: StructuredSource) -> list[V7ParentSection]:
        paragraphs = [
            paragraph
            for section in source.sections
            for paragraph in section.paragraphs
            if _normalise_text(paragraph.text)
            and paragraph.kind != "figure_ocr"  # v7 never indexes OCR over a figure.
        ]
        counts = await self.tokenizer.token_counts([paragraph.text for paragraph in paragraphs])
        token_count_by_id = {id(paragraph): count for paragraph, count in zip(paragraphs, counts, strict=True)}

        parents: list[V7ParentSection] = []
        active: list[SourceParagraph] = []
        active_count = 0
        active_heading = "正文"
        active_level = 0
        active_type = "BODY"

        def flush() -> None:
            nonlocal active, active_count
            if not active:
                return
            start_page, end_page = active[0].page_number, active[-1].page_number
            parents.append(
                V7ParentSection(
                    page_number=start_page,
                    page_end=end_page,
                    section_index=len(parents),
                    heading=active_heading,
                    heading_level=active_level,
                    heading_path=(active_heading,),
                    section_type=active_type,
                    content="\n\n".join(_normalise_text(item.text) for item in active),
                    paragraphs=tuple(active),
                    bbox=_union_bbox(active) if start_page == end_page else None,
                    token_count=active_count,
                )
            )
            active, active_count = [], 0

        for source_section in source.sections:
            heading = _normalise_text(source_section.heading) or "正文"
            section_type = _section_type(heading)
            # A changed heading always starts a new logical parent.  A repeated
            # heading on the next page stays together up to the explicit parent
            # budget, preserving a usable context for child-hit generation.
            if active and heading != active_heading:
                flush()
            if not active:
                active_heading, active_level, active_type = (
                    heading,
                    source_section.heading_level,
                    section_type,
                )
            for paragraph in source_section.paragraphs:
                if paragraph.kind == "figure_ocr" or not _normalise_text(paragraph.text):
                    continue
                count = token_count_by_id.get(id(paragraph), 0)
                if active and active_count + count > settings.LOCAL_PAPER_PARENT_MAX_TOKENS:
                    flush()
                    active_heading, active_level, active_type = (
                        heading,
                        source_section.heading_level,
                        section_type,
                    )
                active.append(paragraph)
                active_count += count
        flush()
        return parents

    async def children(self, parent: V7ParentSection) -> list[V7ChildChunk]:
        """Pack adjacent prose blocks; retain table/caption nodes separately."""
        result: list[V7ChildChunk] = []
        pending: list[SourceParagraph] = []
        pending_count = 0

        async def append_pending() -> None:
            nonlocal pending, pending_count
            if not pending:
                return
            result.append(
                V7ChildChunk(
                    page_number=pending[0].page_number,
                    paragraph_index=pending[0].paragraph_index,
                    text="\n\n".join(_normalise_text(item.text) for item in pending),
                    bbox=_union_bbox(pending) if len({item.page_number for item in pending}) == 1 else None,
                    kind="text",
                    figure_index=None,
                    token_count=pending_count,
                    locators=tuple(pending),
                )
            )
            pending, pending_count = [], 0

        counts = await self.tokenizer.token_counts([item.text for item in parent.paragraphs])
        for paragraph, count in zip(parent.paragraphs, counts, strict=True):
            text = _normalise_text(paragraph.text)
            if not text or paragraph.kind == "figure_ocr":
                continue
            # Tables and figure captions have a distinct evidence type and are
            # never mixed into prose. They remain recallable only for a visual
            # request (the substantive filter enforces that policy).
            if paragraph.kind in {"table", "figure_caption"}:
                await append_pending()
                result.append(
                    V7ChildChunk(
                        page_number=paragraph.page_number,
                        paragraph_index=paragraph.paragraph_index,
                        text=text,
                        bbox=paragraph.bbox,
                        kind=paragraph.kind,
                        figure_index=paragraph.figure_index,
                        token_count=count,
                        locators=(paragraph,),
                    )
                )
                continue
            if count > settings.LOCAL_PAPER_CHUNK_SIZE:
                await append_pending()
                result.extend(await self._split_oversized(paragraph))
                continue
            if pending and pending_count + count > settings.LOCAL_PAPER_CHUNK_SIZE:
                await append_pending()
            pending.append(paragraph)
            pending_count += count
        await append_pending()
        return result

    async def _split_oversized(self, paragraph: SourceParagraph) -> list[V7ChildChunk]:
        """Split only an oversized prose block at sentence boundaries.

        The 64-token overlap is used only here, as agreed for v7. Normal
        adjacent paragraphs remain non-overlapping to avoid duplicate evidence.
        """
        sentences = [
            segment.strip()
            for segment in re.split(r"(?<=[.!?。！？；;])\s+", _normalise_text(paragraph.text))
            if segment.strip()
        ] or [_normalise_text(paragraph.text)]
        counts = await self.tokenizer.token_counts(sentences)
        output: list[V7ChildChunk] = []
        current: list[str] = []
        current_count = 0
        for sentence, count in zip(sentences, counts, strict=True):
            # A pathological no-space sequence is split conservatively by
            # characters, then token-verified before writing a child.
            pieces = [sentence]
            if count > settings.LOCAL_PAPER_CHUNK_SIZE:
                pieces = await self._bisect_to_token_limit(sentence)
            for piece in pieces:
                piece_count = (await self.tokenizer.token_counts([piece]))[0]
                if current and current_count + piece_count > settings.LOCAL_PAPER_CHUNK_SIZE:
                    output.append(self._oversized_child(paragraph, current, current_count))
                    overlap = await self._tail_overlap(current)
                    current = overlap
                    current_count = sum(await self.tokenizer.token_counts(current)) if current else 0
                current.append(piece)
                current_count += piece_count
        if current:
            output.append(self._oversized_child(paragraph, current, current_count))
        return output

    async def _bisect_to_token_limit(self, text: str) -> list[str]:
        if (await self.tokenizer.token_counts([text]))[0] <= settings.LOCAL_PAPER_CHUNK_SIZE:
            return [text]
        midpoint = max(1, len(text) // 2)
        boundary = text.rfind(" ", max(1, midpoint - 200), midpoint + 200)
        if boundary <= 0:
            boundary = midpoint
        return [
            *(await self._bisect_to_token_limit(text[:boundary].strip())),
            *(await self._bisect_to_token_limit(text[boundary:].strip())),
        ]

    async def _tail_overlap(self, pieces: list[str]) -> list[str]:
        # Sentences are kept whole. The BGE-count validation in the caller
        # ensures this overlap never exceeds the configured 64-token budget.
        if not settings.LOCAL_PAPER_CHUNK_OVERLAP or not pieces:
            return []
        tail = pieces[-1]
        return (
            [tail]
            if (await self.tokenizer.token_counts([tail]))[0]
            <= settings.LOCAL_PAPER_CHUNK_OVERLAP
            else []
        )

    @staticmethod
    def _oversized_child(
        paragraph: SourceParagraph, parts: list[str], token_count: int
    ) -> V7ChildChunk:
        return V7ChildChunk(
            page_number=paragraph.page_number,
            paragraph_index=paragraph.paragraph_index,
            text=" ".join(parts),
            bbox=paragraph.bbox,
            kind="text",
            figure_index=paragraph.figure_index,
            token_count=token_count,
            locators=(paragraph,),
        )


def _bbox(value: object) -> list[float] | None:
    try:
        values = list(value)  # PyMuPDF Rect is iterable but not a list/tuple.
    except TypeError:
        return None
    if len(values) != 4:
        return None
    try:
        return [round(float(item), 2) for item in values]
    except (TypeError, ValueError):
        return None


def _union_bbox(paragraphs: list[SourceParagraph]) -> list[float] | None:
    boxes = [paragraph.bbox for paragraph in paragraphs if paragraph.bbox]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _bbox_iou(left: list[float], right: list[float]) -> float:
    """Intersection-over-union for duplicate PDF image detections."""
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _unique_figure_boxes(boxes: Iterable[list[float]]) -> list[list[float]]:
    """PyMuPDF exposes an image both as a block and inventory entry.

    Treat near-identical rectangles as one visual.  This is intentionally
    geometric rather than caption based: two different subfigures may share a
    caption, whereas one image must never create many figure children.
    """
    unique: list[list[float]] = []
    for box in boxes:
        if any(_bbox_iou(box, known) >= 0.98 for known in unique):
            continue
        unique.append(box)
    return unique


def _box_area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _select_page_figure_boxes(page: fitz.Page, boxes: Iterable[list[float]]) -> list[list[float]]:
    """Keep only rendered, materially sized visual regions on one page.

    PDF resource inventories often contain thousands of tiny masks, glyphs or
    tiled fragments.  They are not independent figures and must never become
    separate OCR jobs or vector children.
    """
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    meaningful = [
        box
        for box in _unique_figure_boxes(boxes)
        if _box_area(box) / page_area >= settings.LOCAL_PAPER_MIN_FIGURE_AREA_RATIO
    ]
    largest = sorted(meaningful, key=_box_area, reverse=True)[
        : settings.LOCAL_PAPER_MAX_FIGURES_PER_PAGE
    ]
    # Stable reading order is more useful for deterministic caption matching.
    return sorted(largest, key=lambda box: (box[1], box[0], box[3], box[2]))


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_null(text)).strip()


def _figure_labels(text: str) -> set[str]:
    """Return normalized printed figure numbers mentioned by a text block."""
    return {match.group(1).casefold() for match in _FIGURE_REFERENCE.finditer(text)}


def _is_heading(text: str, font_size: float, body_font_size: float) -> tuple[bool, int]:
    compact = _normalise_text(text)
    if not compact or len(compact) > 180 or compact.endswith((".", "。", ";", "；", ":", "：")):
        return False, 0
    if _HEADING_NUMBER.match(compact):
        number = re.match(r"^(\d+(?:\.\d+)*)", compact)
        return True, min(6, (number.group(1).count(".") + 1) if number else 1)
    uppercase = compact.isupper() and len(compact.split()) <= 12
    larger_font = body_font_size > 0 and font_size >= body_font_size + 1.5
    return (uppercase or larger_font), 1 if (uppercase or larger_font) else 0


def _split_paragraph(paragraph: SourceParagraph) -> list[SourceParagraph]:
    """Split only inside a paragraph; never merge unrelated paragraphs."""
    text = _normalise_text(paragraph.text)
    if not text:
        return []
    size, overlap = settings.LOCAL_PAPER_CHUNK_SIZE, settings.LOCAL_PAPER_CHUNK_OVERLAP
    if len(text) <= size:
        return [SourceParagraph(**{**paragraph.__dict__, "text": text})]
    result: list[SourceParagraph] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            candidates = [text.rfind(mark, start + size // 2, end) for mark in ".!?。！？；;"]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
            else:
                boundary = text.rfind(" ", start + size // 2, end)
                if boundary > start:
                    end = boundary
        result.append(SourceParagraph(**{**paragraph.__dict__, "text": text[start:end].strip()}))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return result


def _sections_from_paragraphs(paragraphs: list[SourceParagraph]) -> list[SourceSection]:
    """Build page-bound parent sections and preserve source paragraph boundaries."""
    sections: list[SourceSection] = []
    state = {"heading": "正文", "level": 0}
    page_groups: dict[int, list[SourceParagraph]] = {}
    for paragraph in paragraphs:
        page_groups.setdefault(paragraph.page_number, []).append(paragraph)
    for page_number, page_paragraphs in sorted(page_groups.items()):
        positive_fonts = [p.font_size for p in page_paragraphs if p.font_size > 0]
        body_font = min(positive_fonts) if positive_fonts else 0.0
        active: list[SourceParagraph] = []
        section_index = 0

        def flush(page_number: int = page_number) -> None:
            nonlocal active, section_index
            if not active:
                return
            content = "\n\n".join(item.text for item in active if item.text).strip()
            if content:
                sections.append(
                    SourceSection(
                        page_number=page_number,
                        section_index=section_index,
                        heading=str(state["heading"]),
                        heading_level=int(state["level"]),
                        content=content,
                        bbox=_union_bbox(active),
                        paragraphs=tuple(active),
                    )
                )
                section_index += 1
            active = []

        for paragraph in page_paragraphs:
            is_heading, level = _is_heading(paragraph.text, paragraph.font_size, body_font)
            if is_heading:
                flush()
                state["heading"], state["level"] = _normalise_text(paragraph.text), level
                continue
            active.append(paragraph)
        flush()
    return sections


def _table_markdown(table: object) -> str:
    try:
        rows = table.extract()  # type: ignore[attr-defined]
    except Exception:
        return ""
    cleaned = [[_normalise_text(str(cell or "")) for cell in row] for row in rows]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    rows = [row + [""] * (width - len(row)) for row in cleaned]
    header = rows[0]
    return "\n".join(
        [
            f"| {' | '.join(header)} |",
            f"| {' | '.join(['---'] * width)} |",
            *[f"| {' | '.join(row)} |" for row in rows[1:]],
        ]
    )


def _ocr_figure(page: fitz.Page, bbox: list[float]) -> tuple[str | None, str | None]:
    """OCR one cropped figure, never the whole page. Optional at runtime."""
    if not settings.LOCAL_PAPER_ENABLE_FIGURE_OCR:
        return None, None
    try:
        pixmap = page.get_pixmap(clip=fitz.Rect(bbox), matrix=fitz.Matrix(2, 2), alpha=False)
        image_bytes = pixmap.tobytes("png")
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "eng+chi_sim"],
                input=image_bytes,
                capture_output=True,
                check=True,
                timeout=45,
            )
        except subprocess.CalledProcessError:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "eng"],
                input=image_bytes,
                capture_output=True,
                check=True,
                timeout=45,
            )
        text = _normalise_text(result.stdout.decode("utf-8", errors="ignore"))
        return text or None, hashlib.sha256(image_bytes).hexdigest()
    except Exception:
        # OCR is evidence enrichment. Text/structure ingestion must remain usable
        # when the optional system Tesseract binary is not installed.
        return None, None


def _docling_bbox(value: object) -> list[float] | None:
    """Read Docling provenance without pinning to a minor-version model type."""
    for names in (("l", "t", "r", "b"), ("left", "top", "right", "bottom")):
        try:
            return [round(float(getattr(value, name)), 2) for name in names]
        except (AttributeError, TypeError, ValueError):
            continue
    return _bbox(value)


def _docling_structured_source(path: Path) -> StructuredSource | None:
    """Primary v7 structure extraction, with PyMuPDF retained for locators.

    Docling's public item/provenance model has evolved across 2.x releases.
    This small adapter intentionally uses stable attributes only and returns
    ``None`` only for a malformed conversion. The caller decides whether that
    is acceptable; production v7 PDFs require this parser.
    """
    if path.suffix.lower() != ".pdf":
        return None
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline_options = PdfPipelineOptions()
        # OCR is only a narrowly-scoped PyMuPDF fallback for scanned pages.
        # Enabling Docling OCR here would reintroduce figure/table OCR into
        # every sync and make a text PDF needlessly expensive.
        pipeline_options.do_ocr = False
        pipeline_options.do_table_structure = True
        # Runtime is offline. Supply the immutable artifact directory
        # explicitly so Docling cannot silently use a transient HF cache.
        artifacts_path = os.environ.get("DOCLING_SERVE_ARTIFACTS_PATH", "").strip()
        if artifacts_path and hasattr(pipeline_options, "artifacts_path"):
            pipeline_options.artifacts_path = Path(artifacts_path)
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        document = converter.convert(str(path)).document
        iterator = getattr(document, "iterate_items", None)
        if not callable(iterator):
            return None
        sections: list[SourceSection] = []
        tables: list[SourceTable] = []
        active: list[SourceParagraph] = []
        heading, heading_level, section_index = "正文", 0, 0

        def flush() -> None:
            nonlocal active, section_index
            if not active:
                return
            sections.append(
                SourceSection(
                    page_number=active[0].page_number,
                    section_index=section_index,
                    heading=heading,
                    heading_level=heading_level,
                    content="\n\n".join(item.text for item in active),
                    bbox=_union_bbox(active)
                    if len({item.page_number for item in active}) == 1
                    else None,
                    paragraphs=tuple(active),
                )
            )
            section_index += 1
            active = []

        for item, level in iterator():
            text = _normalise_text(str(getattr(item, "text", "") or ""))
            label = str(getattr(item, "label", "") or item.__class__.__name__).casefold()
            provenance = list(getattr(item, "prov", []) or [])
            first_prov = provenance[0] if provenance else None
            page_number = int(getattr(first_prov, "page_no", 1) or 1)
            bbox = _docling_bbox(getattr(first_prov, "bbox", None)) if first_prov else None
            if "section" in label and "header" in label and text:
                flush()
                heading = text
                heading_level = int(level or 1)
                continue
            if "table" in label:
                markdown = ""
                exporter = getattr(item, "export_to_markdown", None)
                if callable(exporter):
                    try:
                        markdown = _normalise_text(str(exporter(document=document)))
                    except Exception:
                        markdown = ""
                markdown = markdown or text
                if markdown:
                    tables.append(SourceTable(page_number, len(tables), bbox, markdown))
                    active.append(
                        SourceParagraph(
                            page_number,
                            len(active),
                            markdown,
                            bbox,
                            kind="table",
                        )
                    )
                continue
            # Pictures have no text child in v7. Their pixels/caption are
            # retained by the PyMuPDF locator extraction, then interpreted
            # only inside a deep-research run.
            if text and "picture" not in label and "figure" not in label:
                active.append(SourceParagraph(page_number, len(active), text, bbox))
        flush()
        return StructuredSource(tuple(sections), (), tuple(tables), parser_name="docling") if sections else None
    except Exception as exc:
        logger.warning("Docling conversion failed for %s: %s", path.name, exc)
        return None


def _extract_pymupdf_structured_source(path: Path) -> StructuredSource:
    """Extract parent sections, child paragraphs, tables, figures and locations."""
    kind = SUPPORTED_SUFFIXES[path.suffix.lower()]
    if kind == "html":
        parser = _SafeHTMLText()
        parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
        parser.close()
        paragraphs = [
            SourceParagraph(1, index, text, None)
            for index, text in enumerate(re.split(r"\n\s*\n", parser.text()))
            if _normalise_text(text)
        ]
        return StructuredSource(tuple(_sections_from_paragraphs(paragraphs)), (), parser_name="html")

    document = fitz.open(path)
    try:
        paragraphs: list[SourceParagraph] = []
        figures: list[SourceFigure] = []
        tables: list[SourceTable] = []
        for page in document:
            page_number = page.number + 1
            page_paragraphs: list[SourceParagraph] = []
            try:
                blocks = page.get_text("dict", flags=0).get("blocks", [])
            except Exception:
                blocks = []
            figure_boxes: list[list[float]] = []
            for block in blocks:
                if block.get("type") == 1 and (box := _bbox(block.get("bbox"))):
                    figure_boxes.append(box)
                    continue
                if block.get("type") != 0:
                    continue
                lines = block.get("lines", [])
                line_texts: list[str] = []
                font_sizes: list[float] = []
                for line in lines:
                    spans = line.get("spans", [])
                    text = " ".join(_strip_null(span.get("text", "")) for span in spans).strip()
                    if text:
                        line_texts.append(text)
                    font_sizes.extend(float(span.get("size", 0.0)) for span in spans)
                text = _normalise_text(" ".join(line_texts))
                if text:
                    page_paragraphs.append(
                        SourceParagraph(
                            page_number,
                            len(page_paragraphs),
                            text,
                            _bbox(block.get("bbox")),
                            max(font_sizes, default=0.0),
                        )
                    )
            page_text_characters = sum(len(item.text) for item in page_paragraphs)
            # The text dictionary reports real rendered image blocks.  A
            # digital-text page with no such block must *not* expand a huge
            # resource inventory: PDF internals often contain thousands of
            # masks/glyphs that are not visible figures. Small inventories
            # still cover normal PDFs whose image blocks are omitted.
            if not figure_boxes:
                try:
                    images = page.get_images(full=True)
                    if (
                        page_text_characters < settings.LOCAL_PAPER_OCR_MIN_TEXT_CHARS
                        or len(images) <= settings.LOCAL_PAPER_MAX_IMAGE_RESOURCES_FOR_FALLBACK
                    ):
                        for image in images:
                            for rect in page.get_image_rects(image[0]):
                                if box := _bbox(rect):
                                    figure_boxes.append(box)
                except Exception:
                    pass
            figure_boxes = _select_page_figure_boxes(page, figure_boxes)
            # OCR is evaluated per page, so scanned pages in a mixed PDF are not lost.
            if page_text_characters < settings.LOCAL_PAPER_OCR_MIN_TEXT_CHARS:
                try:
                    text_page = page.get_textpage_ocr(flags=0, full=False)
                    ocr_text = _normalise_text(page.get_text(textpage=text_page))
                    if ocr_text:
                        page_paragraphs.append(
                            SourceParagraph(
                                page_number,
                                len(page_paragraphs),
                                ocr_text,
                                [0.0, 0.0, float(page.rect.width), float(page.rect.height)],
                                kind="page_ocr",
                            )
                        )
                except Exception:
                    pass
            try:
                for table_index, table in enumerate(page.find_tables().tables):
                    markdown = _table_markdown(table)
                    if markdown:
                        table_bbox = _bbox(table.bbox)
                        tables.append(
                            SourceTable(page_number, len(tables), table_bbox, markdown)
                        )
                        page_paragraphs.append(
                            SourceParagraph(
                                page_number,
                                len(page_paragraphs),
                                markdown,
                                table_bbox,
                                kind="table",
                            )
                        )
            except Exception:
                pass
            figure_by_label: dict[str, int] = {}
            used_caption_positions: set[int] = set()
            ocr_figure_indexes = set(
                sorted(
                    range(len(figure_boxes)),
                    key=lambda index: _box_area(figure_boxes[index]),
                    reverse=True,
                )[: settings.LOCAL_PAPER_MAX_FIGURE_OCR_PER_PAGE]
            )
            for figure_index, box in enumerate(figure_boxes):
                caption_position = next(
                    (
                        position
                        for position, item in enumerate(page_paragraphs)
                        if item.bbox
                        and item.bbox[1] >= box[3]
                        and item.bbox[1] - box[3] < 100
                        and _figure_labels(item.text)
                    ),
                    None,
                )
                if caption_position in used_caption_positions:
                    # One printed caption belongs to one visual only.  A PDF
                    # image inventory can contain repeated image resources.
                    caption_position = None
                caption = (
                    page_paragraphs[caption_position].text if caption_position is not None else None
                )
                labels = _figure_labels(caption or "")
                figure_label = next(iter(labels), None)
                if figure_label is not None:
                    figure_by_label[figure_label] = figure_index
                if caption_position is not None:
                    used_caption_positions.add(caption_position)
                    page_paragraphs[caption_position] = replace(
                        page_paragraphs[caption_position],
                        figure_index=figure_index,
                        kind="figure_caption",
                    )
                ocr_text, image_sha256 = (
                    _ocr_figure(page, box) if figure_index in ocr_figure_indexes else (None, None)
                )
                figures.append(
                    SourceFigure(
                        page_number,
                        figure_index,
                        box,
                        figure_label,
                        caption,
                        ocr_text,
                        image_sha256,
                    )
                )
                if ocr_text or caption:
                    page_paragraphs.append(
                        SourceParagraph(
                            page_number,
                            len(page_paragraphs),
                            "\n".join(filter(None, [caption, ocr_text])),
                            box,
                            kind="figure_ocr",
                            figure_index=figure_index,
                        )
                    )
            # A body paragraph can be linked deterministically only if it
            # mentions one detected caption label on the same page.  Multiple
            # figure mentions deliberately remain unlinked rather than being
            # assigned to an arbitrary image.
            for position, item in enumerate(page_paragraphs):
                if item.figure_index is not None:
                    continue
                referenced = {
                    figure_by_label[label]
                    for label in _figure_labels(item.text)
                    if label in figure_by_label
                }
                if len(referenced) == 1:
                    page_paragraphs[position] = replace(item, figure_index=referenced.pop())
            paragraphs.extend(page_paragraphs)
        return StructuredSource(
            tuple(_sections_from_paragraphs(paragraphs)), tuple(figures), tuple(tables), "pymupdf"
        )
    finally:
        document.close()


def extract_structured_source(path: Path) -> StructuredSource:
    """Docling PDF structure plus PyMuPDF page-accurate locators.

    A missing Docling model is a deployment error, not a reason to quietly
    create a structurally different v7 index. Static HTML remains local and
    intentionally does not depend on Docling.
    """
    if path.suffix.lower() != ".pdf":
        return _extract_pymupdf_structured_source(path)
    docling = _docling_structured_source(path)
    if docling is None:
        if settings.LOCAL_PAPER_REQUIRE_DOCLING:
            raise DoclingPrimaryParseError(
                "Docling did not produce structured PDF content. Verify model-init "
                "and DOCLING_SERVE_ARTIFACTS_PATH before synchronizing."
            )
        return _extract_pymupdf_structured_source(path)
    # PyMuPDF does not decide textual structure. It supplies source bboxes,
    # page-local figure artifacts and locator fallback only.
    fallback = _extract_pymupdf_structured_source(path)
    return StructuredSource(
        sections=docling.sections,
        figures=fallback.figures,
        tables=docling.tables or fallback.tables,
        parser_name="docling+pymupdf-locators",
    )


def extract_source(path: Path) -> list[tuple[int, str]]:
    """Compatibility projection used by section summary extraction and tests."""
    return extract_structured_source(path).pages


def extract_structured_sections(pages: list[tuple[int, str]]) -> dict[str, str | None]:
    """Extract Abstract, Introduction (first 2 paragraphs), and Conclusion sections.

    Returns:
        dict with keys: abstract_text, introduction_text, conclusion_text
        Each value is None if not found, or truncated to ~2000 chars if too long.
    """
    # Merge all pages into one text for pattern matching
    full_text = "\n\n".join(text for _, text in pages)

    result: dict[str, str | None] = {
        "abstract_text": None,
        "introduction_text": None,
        "conclusion_text": None,
    }

    # Pattern 1: Extract Abstract (common patterns in academic papers)
    # Matches: "Abstract", "ABSTRACT", "摘要", "Abstract—", etc.
    abstract_patterns = [
        r"(?i)abstract\s*[—\-:.]?\s*\n(.*?)(?=\n\s*(?:keywords?|introduction|i\s*\.?\s+introduction|1\s*\.?\s+introduction|\d+\s*\.?\s+\w+|$))",
        r"摘\s*要\s*[：:.]?\s*\n(.*?)(?=\n\s*(?:关键词|引言|绪论|一、|1\s*\.|\d+\s*\.))",
    ]
    for pattern in abstract_patterns:
        match = re.search(pattern, full_text, re.DOTALL | re.IGNORECASE)
        if match:
            abstract = match.group(1).strip()
            # Clean up: remove excessive whitespace
            abstract = re.sub(r"\s+", " ", abstract)
            # Truncate if too long (keep first 2000 chars)
            result["abstract_text"] = abstract[:2000] if len(abstract) > 2000 else abstract
            break

    # Pattern 2: Extract Introduction (first 2 paragraphs after "Introduction" heading)
    intro_patterns = [
        r"(?i)(?:^|\n)\s*(?:i\s*\.?\s+)?introduction\s*\n(.*?)(?=\n\s*(?:ii\s*\.|\d+\s*\.|[A-Z][a-z]+\s+[A-Z]))",
        r"(?:^|\n)\s*(?:1\s*\.?\s+)?引言\s*\n(.*?)(?=\n\s*(?:2\s*\.|二、|\d+\s*\.))",
        r"(?:^|\n)\s*(?:1\s*\.?\s+)?绪论\s*\n(.*?)(?=\n\s*(?:2\s*\.|二、|\d+\s*\.))",
    ]
    for pattern in intro_patterns:
        match = re.search(pattern, full_text, re.DOTALL | re.IGNORECASE)
        if match:
            intro_raw = match.group(1).strip()
            # Extract first 2 paragraphs (split by double newline or paragraph markers)
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", intro_raw) if p.strip()]
            intro = "\n\n".join(paragraphs[:2])
            intro = re.sub(r"\s+", " ", intro)
            result["introduction_text"] = intro[:2000] if len(intro) > 2000 else intro
            break

    # Pattern 3: Extract Conclusion (last section before References)
    # Look from the end of the document backwards
    conclusion_patterns = [
        r"(?i)(?:^|\n)\s*(?:v?i?i?\s*\.?\s+)?conclus(?:ion|ions)\s*\n(.*?)(?=\n\s*(?:references?|bibliography|acknowledge?ments?|$))",
        r"(?:^|\n)\s*(?:\d+\s*\.?\s+)?结\s*论\s*\n(.*?)(?=\n\s*(?:参考文献|致谢|$))",
    ]
    for pattern in conclusion_patterns:
        # Search from end to beginning (use last match)
        matches = list(re.finditer(pattern, full_text, re.DOTALL | re.IGNORECASE))
        if matches:
            conclusion = matches[-1].group(1).strip()
            conclusion = re.sub(r"\s+", " ", conclusion)
            result["conclusion_text"] = conclusion[:2000] if len(conclusion) > 2000 else conclusion
            break

    return result


@dataclass
class RetrievalChunk:
    chunk: LocalPaperChunk
    parent: LocalPaperSection
    paper: LocalPaper
    vector_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    mmr_score: float | None = None
    vector: list[float] | None = None

    @property
    def reranker_text(self) -> str:
        return (
            f"论文标题：{self.paper.title}\n"
            f"作者：{'；'.join(self.paper.authors_json)}\n"
            f"章节：{self.parent.heading}\n"
            f"段落：{self.chunk.content}"
        )

    @property
    def lexical_text(self) -> str:
        """Field-aware BM25 text without treating an exact title as a hard top."""
        return f"{self.paper.title}\n{self.paper.doi or ''}\n{self.chunk.content}"


def _bm25_tokens(text: str) -> list[str]:
    """English terms plus CJK unigrams and bigrams, without a dictionary dependency."""
    raw = _BM25_TOKEN.findall(text.casefold())
    cjk = "".join(token for token in raw if len(token) == 1 and "\u4e00" <= token <= "\u9fff")
    bigrams = [cjk[index : index + 2] for index in range(max(0, len(cjk) - 1))]
    return raw + bigrams


@dataclass(frozen=True)
class _BM25Corpus:
    """Immutable, metadata-scope-specific Okapi corpus held only in process memory."""

    chunk_ids: tuple[UUID, ...]
    model: BM25Okapi


class _BM25CorpusCache:
    """Small LRU cache; PostgreSQL remains the durable source and sync changes the key."""

    max_entries = 16
    _entries: OrderedDict[str, _BM25Corpus] = OrderedDict()

    @classmethod
    def get(cls, key: str) -> _BM25Corpus | None:
        corpus = cls._entries.get(key)
        if corpus is not None:
            cls._entries.move_to_end(key)
        return corpus

    @classmethod
    def put(cls, key: str, corpus: _BM25Corpus) -> _BM25Corpus:
        cls._entries[key] = corpus
        cls._entries.move_to_end(key)
        while len(cls._entries) > cls.max_entries:
            cls._entries.popitem(last=False)
        return corpus


def _bm25_scope_key(active_versions: set[UUID], paper_ids: set[UUID]) -> str:
    # A document version change creates a new key, so sync rebuilds cannot
    # accidentally reuse an old lexical corpus.
    return hashlib.sha256(
        (",".join(sorted(str(version) for version in active_versions)) + "|" + ",".join(sorted(str(paper_id) for paper_id in paper_ids))).encode()
    ).hexdigest()


def _rrf_fuse(
    dense: list[tuple[UUID, float, list[float] | None]],
    bm25: list[tuple[UUID, float]],
    *,
    rrf_k: int,
) -> dict[UUID, tuple[float, float | None, float | None, list[float] | None]]:
    """Fuse ranked dense/BM25 lists without mixing incomparable raw scores."""
    fused: dict[UUID, list[object]] = {}
    for rank, (chunk_id, score, vector) in enumerate(dense, 1):
        values = fused.setdefault(chunk_id, [0.0, None, None, None])
        values[0] = float(values[0]) + 1.0 / (rrf_k + rank)
        values[1], values[3] = score, vector
    for rank, (chunk_id, score) in enumerate(bm25, 1):
        values = fused.setdefault(chunk_id, [0.0, None, None, None])
        values[0] = float(values[0]) + 1.0 / (rrf_k + rank)
        values[2] = score
    return {
        chunk_id: (float(values[0]), values[1], values[2], values[3])
        for chunk_id, values in fused.items()
    }


def _cosine(left: list[float], right: list[float]) -> float:
    denominator = (
        sum(value * value for value in left) ** 0.5 * sum(value * value for value in right) ** 0.5
    )
    return (
        sum(a * b for a, b in zip(left, right, strict=False)) / denominator if denominator else 0.0
    )


def _select_diverse_papers(
    ranked: list[RetrievalChunk], *, limit: int, lambda_mult: float
) -> list[RetrievalChunk]:
    """MMR-select one best evidence chunk per paper for diverse final papers."""
    first_per_paper: dict[UUID, RetrievalChunk] = {}
    for item in ranked:
        first_per_paper.setdefault(item.chunk.paper_id, item)
    remaining = list(first_per_paper.values())
    selected: list[RetrievalChunk] = []
    while remaining and len(selected) < limit:

        def score(item: RetrievalChunk) -> float:
            relevance = item.rerank_score if item.rerank_score is not None else item.rrf_score
            if not selected or item.vector is None:
                return relevance
            diversity = max(
                (
                    _cosine(item.vector, previous.vector)
                    for previous in selected
                    if previous.vector is not None
                ),
                default=0.0,
            )
            return lambda_mult * relevance - (1.0 - lambda_mult) * diversity

        best = max(remaining, key=score)
        best.mmr_score = score(best)
        selected.append(best)
        remaining.remove(best)
    return selected


def _cap_chunks_per_paper(
    ranked: list[RetrievalChunk], *, limit: int, max_per_paper: int
) -> list[RetrievalChunk]:
    """Preserve RRF order while reserving reranker slots for other papers."""
    selected: list[RetrievalChunk] = []
    counts: Counter[UUID] = Counter()
    for item in ranked:
        if counts[item.chunk.paper_id] >= max_per_paper:
            continue
        selected.append(item)
        counts[item.chunk.paper_id] += 1
        if len(selected) >= limit:
            break
    return selected


def _is_visual_query(query: str) -> bool:
    return bool(re.search(r"(?i)\b(?:fig(?:ure)?|table)\b|[图表]", query))


def _is_substantive_retrieval_chunk(item: RetrievalChunk, *, query: str) -> bool:
    """Reject headers and tiny visual labels for ordinary research retrieval.

    A one-line running header can have excellent lexical/BGE scores merely
    because it repeats a paper title.  It cannot substantiate an answer.  A
    short caption remains searchable when the user explicitly asks for a
    figure/table; otherwise a visual child needs enough OCR/caption content to
    stand on its own.  Short normal passages are retained only when their
    parent is also short, avoiding the loss of genuinely compact HTML notes.
    """
    # Citation lists repeat the exact terminology of many papers, but they
    # are not evidence about the current paper's method, result or argument.
    heading = _normalise_text(item.parent.heading)
    if _REFERENCE_SECTION.search(heading) or _REFERENCE_SECTION.search(re.sub(r"\s+", "", heading)):
        return False
    text = _normalise_text(item.chunk.content)
    minimum = settings.LOCAL_PAPER_MIN_SUBSTANTIVE_CHARS
    if len(text) >= minimum:
        return True
    if item.chunk.chunk_kind in {"figure_ocr", "figure_caption"}:
        return _is_visual_query(query)
    return len(_normalise_text(item.parent.content)) < minimum


def _is_new_evidence(existing: list[LocalPaperEvidenceRead], item: RetrievalChunk) -> bool:
    """Do not surface the same text/figure more than once for one paper."""
    digest = item.chunk.content_sha256
    for evidence in existing:
        if hashlib.sha256(evidence.text.encode("utf-8")).hexdigest() == digest:
            return False
        if item.chunk.figure_id is not None and evidence.figure_id == item.chunk.figure_id:
            return False
    return True


class LocalPaperLibraryService:
    def __init__(
        self,
        db: AsyncSession,
        index: LocalPaperVectorIndex | None = None,
        reranker: LocalPaperReranker | None = None,
    ) -> None:
        self.db = db
        self.index = index or LocalPaperVectorIndex()
        self.reranker = reranker or BGERerankerV2M3HTTP()

    @property
    def root(self) -> Path:
        return settings.LOCAL_PAPER_LIBRARY_ROOT.resolve()

    def _require_source(self) -> Path:
        if not self.root.is_dir():
            raise ValidationError(
                "本地文献库未挂载：请通过 LOCAL_PAPER_LIBRARY_HOST_PATH 只读挂载后再同步"
            )
        if not list(self.root.glob("*.bib")):
            raise ValidationError("本地文献库缺少根目录 Better BibTeX .bib 文件")
        return self.root

    async def _library(self, owner_id: UUID | None = None) -> LocalPaperLibrary | None:
        stmt = select(LocalPaperLibrary)
        if owner_id is not None:
            stmt = stmt.where(LocalPaperLibrary.owner_id == owner_id)
        return await self.db.scalar(stmt.limit(1))

    async def get_status(self, *, owner_id: UUID) -> LocalLibraryStatusRead:
        library = await self._library(owner_id=owner_id)
        if library is None:
            return LocalLibraryStatusRead(configured=self.root.is_dir(), status="NOT_INITIALIZED")
        indexed = await self.db.scalar(
            select(func.count())
            .select_from(LocalPaper)
            .where(LocalPaper.library_id == library.id, LocalPaper.status == "INDEXED")
        )
        missing = await self.db.scalar(
            select(func.count())
            .select_from(LocalPaper)
            .where(LocalPaper.library_id == library.id, LocalPaper.status == "MISSING")
        )
        current_indexed = await self.db.scalar(
            select(func.count())
            .select_from(LocalPaper)
            .where(
                LocalPaper.library_id == library.id,
                LocalPaper.status == "INDEXED",
                LocalPaper.ingestion_version == _local_index_version(),
            )
        )
        latest = await self.db.scalar(
            select(LocalPaperSyncRun)
            .where(LocalPaperSyncRun.library_id == library.id)
            .order_by(LocalPaperSyncRun.created_at.desc())
            .limit(1)
        )
        quarantined = await self.db.scalar(
            select(func.count())
            .select_from(LocalPaperQuarantineItem)
            .where(LocalPaperQuarantineItem.library_id == library.id)
        )
        latest_quarantined = (
            await self.db.scalar(
                select(func.count())
                .select_from(LocalPaperQuarantineItem)
                .where(LocalPaperQuarantineItem.sync_run_id == latest.id)
            )
            if latest is not None
            else 0
        )
        catalogued = await self.db.scalar(
            select(func.count()).select_from(LocalPaper).where(LocalPaper.library_id == library.id)
        )
        stale_indexed = await self.db.scalar(
            select(func.count())
            .select_from(LocalPaper)
            .where(
                LocalPaper.library_id == library.id,
                LocalPaper.status == "INDEXED",
                LocalPaper.ingestion_version != _local_index_version(),
            )
        )
        quarantine = (
            await self.db.scalars(
                select(LocalPaperQuarantineItem)
                .where(LocalPaperQuarantineItem.library_id == library.id)
                .order_by(LocalPaperQuarantineItem.created_at.desc())
                .limit(100)
            )
        ).all()
        return LocalLibraryStatusRead(
            configured=self.root.is_dir(),
            owner_id=library.owner_id,
            status=library.status,
            source_root="/zotero_local_database",
            indexed_papers=indexed or 0,
            current_indexed_papers=current_indexed or 0,
            missing_papers=missing or 0,
            quarantined_items=quarantined or 0,
            catalogued_papers=catalogued or 0,
            searchable_papers=current_indexed or 0,
            stale_indexed_papers=stale_indexed or 0,
            missing_source_papers=missing or 0,
            latest_quarantine_items=latest_quarantined or 0,
            last_sync_summary=library.last_sync_summary_json,
            latest_sync=self._sync_read(latest) if latest else None,
            quarantine=[LocalLibraryQuarantineRead.model_validate(item) for item in quarantine],
        )

    async def request_sync(self, *, owner_id: UUID) -> LocalPaperSyncRun:
        self._require_source()
        library = await self._library(owner_id=owner_id)
        if library is None:
            library = LocalPaperLibrary(
                owner_id=owner_id,
                source_root="/zotero_local_database",
                qdrant_collection=_collection_name(owner_id),
            )
            self.db.add(library)
            await self.db.flush()
        elif library.qdrant_collection != _collection_name(owner_id):
            # Do not mutate the old collection's dimension in place. New sync
            # writes a versioned collection; the old collection stays recoverable.
            library.qdrant_collection = _collection_name(owner_id)
        active = await self.db.scalar(
            select(LocalPaperSyncRun).where(
                LocalPaperSyncRun.library_id == library.id,
                LocalPaperSyncRun.status.in_(["QUEUED", "RUNNING"]),
            )
        )
        if active:
            return active
        run = LocalPaperSyncRun(library_id=library.id, requested_by=owner_id)
        library.status = "QUEUED"
        self.db.add(run)
        await self.db.flush()
        return run

    async def _checkpoint_sync_progress(
        self,
        *,
        library_id: UUID,
        sync_run_id: UUID,
        summary: Counter[str],
        stage: str,
        current_citekey: str | None = None,
        current_path: str | None = None,
    ) -> None:
        """Persist and publish a replayable local-library sync progress snapshot.

        Redis pub/sub gives connected browsers low-latency updates; the JSONB
        snapshot is the source of truth when a browser reconnects or misses an
        event.  This is deliberately committed independently from each paper's
        indexing transaction.
        """
        library = await self.db.get(LocalPaperLibrary, library_id)
        run = await self.db.get(LocalPaperSyncRun, sync_run_id)
        if library is None or run is None:
            return
        previous = run.summary_json or {}
        sequence = int(previous.get("sequence", 0)) + 1
        snapshot: dict[str, object] = {
            **dict(summary),
            "sequence": sequence,
            "stage": stage,
            "current_citekey": current_citekey,
            "current_path": current_path,
        }
        run.summary_json = snapshot
        library.last_sync_summary_json = snapshot
        event_payload = _local_paper_sync_event_payload(
            sync_run_id=sync_run_id,
            status=run.status,
            summary=snapshot,
            error_message=run.error_message,
        )
        self.db.add(
            LocalPaperSyncEvent(
                sync_run_id=sync_run_id,
                sequence=sequence,
                event_type="PROGRESS",
                payload_json=event_payload,
            )
        )
        await self.db.commit()
        await _publish_local_paper_sync_event(event_payload)

    async def run_sync(self, *, library_id: UUID, sync_run_id: UUID) -> None:
        library = await self.db.get(LocalPaperLibrary, library_id)
        run = await self.db.get(LocalPaperSyncRun, sync_run_id)
        if library is None or run is None:
            raise NotFoundError("Local paper library sync target not found")

        # CPU tasks use late acknowledgement: after a worker is restarted the
        # broker may redeliver the *same* sync_run_id even though it already
        # committed its terminal event.  Never turn that completed run back
        # into RUNNING or repeat all extraction/embedding work.  A user
        # explicitly requests another sync through a new run ID, so this
        # guard does not suppress a real manual rebuild.
        completed_event = await self.db.scalar(
            select(LocalPaperSyncEvent)
            .where(
                LocalPaperSyncEvent.sync_run_id == sync_run_id,
                LocalPaperSyncEvent.event_type == "COMPLETED",
            )
            .order_by(LocalPaperSyncEvent.sequence.desc())
            .limit(1)
        )
        if completed_event is not None:
            payload = dict(completed_event.payload_json or {})
            event_data = payload.get("data")
            if isinstance(event_data, dict):
                summary = event_data.get("summary_json")
                if isinstance(summary, dict):
                    run.summary_json = dict(summary)
                    library.last_sync_summary_json = dict(summary)
                error_message = event_data.get("error_message")
                run.error_message = error_message if isinstance(error_message, str) else None
            run.status, library.status = "COMPLETED", "READY"
            await self.db.commit()
            # Re-publish the durable terminal event for clients that were
            # disconnected while the duplicate delivery was being consumed.
            await _publish_local_paper_sync_event(payload)
            logger.info("Ignored redelivered completed local-paper sync %s", sync_run_id)
            return

        root = self._require_source()
        # A redelivered manual run can start after parser/index-version code
        # changed.  Keep its Qdrant target aligned with that version before
        # any paper is replaced, so partial prior-version points are never
        # mixed into this rebuild.
        library.qdrant_collection = _collection_name(library.owner_id)
        library.status, run.status = "RUNNING", "RUNNING"
        # Acks-late CPU tasks can be redelivered after a worker restart.  The
        # run ID is intentionally reused, so discard partial-attempt
        # quarantine rows before recomputing authoritative counts.
        await self.db.execute(
            delete(LocalPaperQuarantineItem).where(
                LocalPaperQuarantineItem.sync_run_id == sync_run_id
            )
        )
        await self.db.flush()
        summary: Counter[str] = Counter()
        try:
            bib_files = sorted(root.glob("*.bib"))
            entries = parse_bibtex(bib_files[0].read_text(encoding="utf-8", errors="ignore"))
            files = [path for path in root.rglob("*") if path.is_file()]
            supported = {
                _relative(root, path): path
                for path in files
                if path.suffix.lower() in SUPPORTED_SUFFIXES
            }
            summary.update(
                total_bibtex=len(entries),
                total_sources=len(supported),
                total_bibtex_entries=len(entries),
                total_supported_sources=len(supported),
                processed=0,
                indexed=0,
                unchanged=0,
                duplicate=0,
                unmatched_bibtex=0,
                unmatched_source=0,
                errors=0,
                matched_bibtex_entries=0,
                matched_source_files=0,
                referenced_source_files=0,
                related_attachments=0,
            )
            await self._checkpoint_sync_progress(
                library_id=library_id,
                sync_run_id=sync_run_id,
                summary=summary,
                stage="DISCOVERING",
            )
            basename_counts = Counter(path.name.casefold() for path in supported.values())
            by_basename = {
                path.name.casefold(): path
                for path in supported.values()
                if basename_counts[path.name.casefold()] == 1
            }
            existing = (
                await self.db.scalars(select(LocalPaper).where(LocalPaper.library_id == library.id))
            ).all()
            # Repair the historical state left by pre-v7 failed rebuilds.  A
            # paper is safe to recover only when the read-only source still
            # hashes to the catalogued revision *and* its active immutable
            # document version is READY.  This is deliberately a reconciliation
            # status change, not a re-parse/re-embed operation.
            active_ready_versions = set(
                (
                    await self.db.scalars(
                        select(LocalPaperDocumentVersion.id).where(
                            LocalPaperDocumentVersion.id.in_(
                                [
                                    paper.active_document_version_id
                                    for paper in existing
                                    if paper.active_document_version_id is not None
                                ]
                            ),
                            LocalPaperDocumentVersion.status == "READY",
                            LocalPaperDocumentVersion.is_active.is_(True),
                        )
                    )
                ).all()
            )
            for paper in existing:
                source = supported.get(paper.relative_source_path)
                if (
                    paper.status == "MISSING"
                    and source is not None
                    and paper.active_document_version_id in active_ready_versions
                    and _sha256(source) == paper.source_sha256
                ):
                    paper.status = "INDEXED"
                    summary["recovered"] += 1
            by_citekey = {paper.citekey: paper for paper in existing}
            by_hash = {paper.source_sha256: paper for paper in existing}
            by_doi = {paper.doi: paper for paper in existing if paper.doi}
            seen_ids: set[UUID] = set()
            claimed_sources: set[str] = set()
            referenced_sources: set[str] = set()
            claimed_dois: set[str] = set()

            for path in files:
                if path.suffix.lower() not in {".bib", *SUPPORTED_SUFFIXES}:
                    await self._quarantine(
                        run,
                        library,
                        "UNSUPPORTED_INPUT",
                        _relative(root, path),
                        None,
                        "Only PDF and static HTML are accepted; archive/property files are not opened.",
                    )
                    summary["unsupported"] += 1

            for entry in entries:
                summary["processed"] += 1
                await self._checkpoint_sync_progress(
                    library_id=library_id,
                    sync_run_id=sync_run_id,
                    summary=summary,
                    stage="MATCHING",
                    current_citekey=entry.citekey,
                )
                candidate_paths = attachment_paths(entry)
                entry_sources: dict[str, Path] = {}
                for raw in candidate_paths:
                    if candidate := _safe_source(root, raw):
                        entry_sources[_relative(root, candidate)] = candidate
                        continue
                    # A bare filename is an intentionally conservative
                    # fallback: only use it when exactly one local attachment
                    # has that basename.
                    name = Path(raw.replace("\\", "/")).name.casefold()
                    if name in by_basename:
                        candidate = by_basename[name]
                        entry_sources[_relative(root, candidate)] = candidate
                if not entry_sources:
                    for raw in candidate_paths:
                        name = Path(raw.replace("\\", "/")).name.casefold()
                        if name in by_basename:
                            entry_sources[_relative(root, by_basename[name])] = by_basename[name]
                            break
                if not entry_sources:
                    await self._quarantine(
                        run,
                        library,
                        "UNMATCHED_BIBTEX",
                        None,
                        entry.citekey,
                        "No reliable PDF/HTML attachment match in the read-only source library.",
                    )
                    summary["unmatched_bibtex"] += 1
                    continue
                # A Zotero record commonly owns a PDF plus an HTML snapshot.
                # They are two attachments for one BibTeX entry, not an
                # indexed paper plus an “unmatched file”.  Index a PDF as the
                # canonical source when available, and reconcile every linked
                # attachment separately below.
                source = next(
                    (path for path in entry_sources.values() if path.suffix.lower() == ".pdf"),
                    next(iter(entry_sources.values())),
                )
                relative = _relative(root, source)
                referenced_sources.update(entry_sources)
                summary["matched"] += 1
                summary["matched_bibtex_entries"] += 1
                await self._checkpoint_sync_progress(
                    library_id=library_id,
                    sync_run_id=sync_run_id,
                    summary=summary,
                    stage="INDEXING",
                    current_citekey=entry.citekey,
                    current_path=relative,
                )
                if relative in claimed_sources:
                    await self._quarantine(
                        run,
                        library,
                        "DUPLICATE_SOURCE",
                        relative,
                        entry.citekey,
                        "Another BibTeX entry already claims this source file.",
                    )
                    summary["duplicate"] += 1
                    continue
                doi = _doi(entry.fields.get("doi"))
                if doi and doi in claimed_dois:
                    await self._quarantine(
                        run,
                        library,
                        "DUPLICATE_DOI",
                        relative,
                        entry.citekey,
                        f"DOI {doi} already appears in this sync.",
                    )
                    summary["duplicate"] += 1
                    continue
                # 检查DOI是否已被索引——同一DOI但来自不同citekey格式的导出（如旧BibTeX vs Better BibTeX）
                # 应视为同一论文的重新导入（格式变化），而非真正的重复。
                # 只有当 existing_doi_paper 存在且当前entry没有对应DB记录时才是真重复。
                existing_doi_paper = by_doi.get(doi) if doi else None
                current_paper_in_db = by_citekey.get(entry.citekey)

                # 若DOI已有记录，且当前citekey没有对应DB记录 → 说明是同论文的不同citekey格式导出
                # 此时应更新已有记录的citekey，而非标记为重复
                if existing_doi_paper and current_paper_in_db is None:
                    # 更新 citekey 为新格式，并继续处理（当作更新，不隔离）
                    existing_doi_paper.citekey = entry.citekey
                    by_citekey[entry.citekey] = existing_doi_paper
                    paper = existing_doi_paper
                elif (
                    existing_doi_paper
                    and current_paper_in_db is not None
                    and existing_doi_paper.id != current_paper_in_db.id
                ):
                    await self._quarantine(
                        run,
                        library,
                        "DUPLICATE_DOI",
                        relative,
                        entry.citekey,
                        f"DOI {doi} is already indexed as {existing_doi_paper.citekey}.",
                    )
                    summary["duplicate"] += 1
                    continue
                claimed_sources.add(relative)
                if doi:
                    claimed_dois.add(doi)
                digest = _sha256(source)
                paper = by_citekey.get(entry.citekey)
                hashed_paper = by_hash.get(digest)
                if hashed_paper and paper is None:
                    await self._quarantine(
                        run,
                        library,
                        "DUPLICATE_SOURCE",
                        relative,
                        entry.citekey,
                        f"Same content is already indexed as {hashed_paper.citekey}.",
                    )
                    summary["duplicate"] += 1
                    continue
                try:
                    if paper and paper.source_sha256 == digest:
                        # A source hash alone is insufficient: parser/chunker/
                        # embedding format changes must force a real re-index.
                        # The active document version is immutable.  Missing
                        # convenience extracts are not a valid reason to feed
                        # the exact same source/parser/chunker tuple through
                        # the builder again: it would try to insert the same
                        # (document_version, page, section_index) rows and
                        # poison a later reconciliation as "MISSING".  A
                        # parser/chunker improvement must deliberately bump
                        # the ingestion version, which creates a new version.
                        needs_reindex = paper.ingestion_version != _local_index_version()
                        if not needs_reindex:
                            paper.status = "INDEXED"
                            paper.relative_source_path = relative
                            paper.source_kind = SUPPORTED_SUFFIXES[source.suffix.lower()]
                            paper.title = entry.fields.get("title", paper.title)
                            paper.authors_json = _authors(entry.fields.get("author", ""))
                            paper.doi = doi
                            paper.publication_year = _year(entry.fields.get("year"))
                            paper.bibtex_type = entry.entry_type
                            paper.bibtex_entry = entry.raw
                            seen_ids.add(paper.id)
                            summary["unchanged"] += 1
                            continue
                    structured_source = extract_structured_source(source)
                    pages = structured_source.pages
                    if not structured_source.sections:
                        await self._quarantine(
                            run,
                            library,
                            "EMPTY_TEXT",
                            relative,
                            entry.citekey,
                            "The PDF/HTML yielded no extractable text. "
                            "If this is a scanned PDF, install 'tesseract-ocr' in the worker "
                            "and re-sync; OCR is attempted per low-text page.",
                        )
                        summary["empty_text"] += 1
                        continue
                    sections = extract_structured_sections(pages)
                    # Build the entire v7 candidate before touching the active
                    # document version. A parser/tokenizer error therefore
                    # leaves the previously searchable paper intact.
                    v7_chunker = V7StructureChunker()
                    parent_sources = await v7_chunker.build(structured_source)
                    if not parent_sources:
                        raise RuntimeError("No v7 logical parent sections were produced")
                    child_sources = [
                        (parent_source, await v7_chunker.children(parent_source))
                        for parent_source in parent_sources
                    ]
                    if paper is None:
                        paper = LocalPaper(
                            library_id=library.id,
                            citekey=entry.citekey,
                            doi=doi,
                            title=entry.fields.get("title", entry.citekey),
                            authors_json=_authors(entry.fields.get("author", "")),
                            publication_year=_year(entry.fields.get("year")),
                            bibtex_type=entry.entry_type,
                            relative_source_path=relative,
                            source_kind=SUPPORTED_SUFFIXES[source.suffix.lower()],
                            source_sha256=digest,
                            ingestion_version=_local_index_version(),
                            bibtex_entry=entry.raw,
                            status="INDEXED",
                            page_count=len(pages),
                            text_characters=sum(len(text) for _, text in pages),
                            abstract_text=sections["abstract_text"],
                            introduction_text=sections["introduction_text"],
                            conclusion_text=sections["conclusion_text"],
                        )
                        self.db.add(paper)
                        await self.db.flush()
                    else:
                        # Do not delete the active children here. v7 builds an
                        # immutable candidate version and switches the pointer
                        # only after its DB rows and Qdrant points both exist.
                        paper.doi, paper.title = doi, entry.fields.get("title", entry.citekey)
                        paper.authors_json, paper.publication_year = (
                            _authors(entry.fields.get("author", "")),
                            _year(entry.fields.get("year")),
                        )
                        paper.bibtex_type, paper.relative_source_path = entry.entry_type, relative
                        paper.source_kind, paper.source_sha256, paper.bibtex_entry = (
                            SUPPORTED_SUFFIXES[source.suffix.lower()],
                            digest,
                            entry.raw,
                        )
                        paper.ingestion_version = _local_index_version()
                        paper.status, paper.page_count = "INDEXED", len(pages)
                        paper.text_characters = sum(len(text) for _, text in pages)
                        paper.abstract_text = sections["abstract_text"]
                        paper.introduction_text = sections["introduction_text"]
                        paper.conclusion_text = sections["conclusion_text"]
                    version = await self.db.scalar(
                        select(LocalPaperDocumentVersion).where(
                            LocalPaperDocumentVersion.paper_id == paper.id,
                            LocalPaperDocumentVersion.source_sha256 == digest,
                            LocalPaperDocumentVersion.parser_version
                            == settings.LOCAL_PAPER_INGESTION_VERSION,
                            LocalPaperDocumentVersion.chunker_version
                            == f"bge-token-{settings.LOCAL_PAPER_CHUNK_SIZE}-parent-{settings.LOCAL_PAPER_PARENT_MAX_TOKENS}",
                        )
                    )
                    if version is None:
                        version = LocalPaperDocumentVersion(
                            paper_id=paper.id,
                            source_sha256=digest,
                            parser_version=settings.LOCAL_PAPER_INGESTION_VERSION,
                            chunker_version=f"bge-token-{settings.LOCAL_PAPER_CHUNK_SIZE}-parent-{settings.LOCAL_PAPER_PARENT_MAX_TOKENS}",
                            embedding_model=settings.LOCAL_PAPER_EMBEDDING_MODEL,
                            status="BUILDING",
                            page_count=len(pages),
                            text_characters=sum(len(text) for _, text in pages),
                            quality_json={
                                "structure_parser": structured_source.parser_name,
                                "figure_ocr_indexed": False,
                                "chunk_token_limit": settings.LOCAL_PAPER_CHUNK_SIZE,
                            },
                        )
                        self.db.add(version)
                        await self.db.flush()
                    elif not version.is_active:
                        await self.db.execute(
                            delete(LocalPaperSection).where(
                                LocalPaperSection.document_version_id == version.id
                            )
                        )
                        await self.db.execute(
                            delete(LocalPaperFigure).where(
                                LocalPaperFigure.document_version_id == version.id
                            )
                        )
                        await self.db.execute(
                            delete(LocalPaperTable).where(
                                LocalPaperTable.document_version_id == version.id
                            )
                        )
                        await self.db.flush()

                    section_rows: list[LocalPaperSection] = []
                    for section in parent_sources:
                        section_content = _normalise_text(section.content)
                        section_rows.append(
                            LocalPaperSection(
                                paper_id=paper.id,
                                document_version_id=version.id,
                                page_number=section.page_number,
                                section_index=section.section_index,
                                heading=_normalise_text(section.heading) or "正文",
                                heading_level=section.heading_level,
                                content=section_content,
                                bbox_json=section.bbox,
                                section_sha256=hashlib.sha256(
                                    section_content.encode("utf-8")
                                ).hexdigest(),
                                heading_path_json=list(section.heading_path),
                                section_type=section.section_type,
                                page_end=section.page_end,
                                token_count=section.token_count,
                            )
                        )
                    self.db.add_all(section_rows)
                    await self.db.flush()
                    def parent_for_page(page_number: int) -> LocalPaperSection | None:
                        return next(
                            (
                                row
                                for row in section_rows
                                if row.page_number <= page_number <= (row.page_end or row.page_number)
                            ),
                            None,
                        )

                    figure_rows = [
                        LocalPaperFigure(
                            paper_id=paper.id,
                            document_version_id=version.id,
                            section_id=(parent_for_page(figure.page_number).id if parent_for_page(figure.page_number) else None),
                            page_number=figure.page_number,
                            figure_index=figure.figure_index,
                            figure_label=figure.figure_label,
                            bbox_json=figure.bbox,
                            caption_text=_normalise_text(figure.caption_text) if figure.caption_text else None,
                            # Figure OCR is deliberately not corpus evidence in v7.
                            ocr_text=None,
                            image_sha256=figure.image_sha256,
                            extractor_version=_local_index_version(),
                            artifact_kind="figure",
                        )
                        for figure in structured_source.figures
                    ]
                    table_rows = [
                        LocalPaperTable(
                            paper_id=paper.id,
                            document_version_id=version.id,
                            table_index=table.table_index,
                            page_number=table.page_number,
                            bbox_json=table.bbox,
                            structure_json={"format": "markdown", "source": "pymupdf"},
                            markdown_text=table.markdown_text,
                        )
                        for table in structured_source.tables
                    ]
                    self.db.add_all([*figure_rows, *table_rows])
                    await self.db.flush()
                    figure_ids = {
                        (figure.page_number, figure.figure_index): row.id
                        for figure, row in zip(structured_source.figures, figure_rows, strict=True)
                    }
                    chunk_rows: list[LocalPaperChunk] = []
                    chunk_source_pairs: list[tuple[LocalPaperChunk, V7ChildChunk]] = []
                    per_page_index: Counter[int] = Counter()
                    seen_child_hashes: set[str] = set()
                    for (parent_source, children), section_row in zip(
                        child_sources, section_rows, strict=True
                    ):
                        for child in children:
                            child_text = _normalise_text(child.text)
                            if not child_text:
                                continue
                            child_hash = hashlib.sha256(child_text.encode("utf-8")).hexdigest()
                            # Repeated running headers and duplicated text are
                            # one fact per paper. Locations are retained on the
                            # one canonical child instead of duplicate vectors.
                            if child_hash in seen_child_hashes:
                                continue
                            seen_child_hashes.add(child_hash)
                            chunk_index = per_page_index[child.page_number]
                            per_page_index[child.page_number] += 1
                            row = LocalPaperChunk(
                                paper_id=paper.id,
                                document_version_id=version.id,
                                section_id=section_row.id,
                                figure_id=figure_ids.get((child.page_number, child.figure_index)),
                                page_number=child.page_number,
                                chunk_index=chunk_index,
                                paragraph_index=child.paragraph_index,
                                heading=_normalise_text(parent_source.heading) or "正文",
                                bbox_json=child.bbox,
                                chunk_kind=child.kind,
                                content=child_text,
                                content_sha256=child_hash,
                                token_count=child.token_count,
                                embedding_text=(
                                    f"标题：{paper.title}\n章节：{section_row.heading}\n{child_text}"
                                ),
                                lexical_terms=" ".join(
                                    _bm25_tokens(
                                        f"{paper.title} {paper.doi or ''} {section_row.heading} {child_text}"
                                    )
                                )
                            )
                            chunk_rows.append(row)
                            chunk_source_pairs.append((row, child))
                    self.db.add_all(chunk_rows)
                    await self.db.flush()
                    self.db.add_all(
                        LocalPaperChunkLocator(
                            chunk_id=chunk.id,
                            page_number=locator.page_number,
                            bbox_json=locator.bbox,
                            ordinal=ordinal,
                            source_kind=locator.kind,
                        )
                        for chunk, child in chunk_source_pairs
                        for ordinal, locator in enumerate(child.locators)
                    )
                    await self.db.execute(
                        LocalPaperChunk.__table__.update()
                        .where(LocalPaperChunk.document_version_id == version.id)
                        .values(
                            lexical_tsv=func.to_tsvector(
                                "simple", LocalPaperChunk.lexical_terms
                            )
                        )
                    )
                    await self.db.flush()
                    await self.index.activate_document_version_chunks(
                        collection=library.qdrant_collection,
                        paper_id=paper.id,
                        document_version_id=version.id,
                        chunks=[
                            LocalPaperVectorChunk(
                                chunk_id=chunk.id,
                                paper_id=paper.id,
                                section_id=chunk.section_id,
                                page_number=chunk.page_number,
                                chunk_index=chunk.chunk_index,
                                paragraph_index=chunk.paragraph_index,
                                heading=chunk.heading,
                                content=chunk.content,
                                document_version_id=version.id,
                                figure_id=chunk.figure_id,
                                node_type=chunk.chunk_kind,
                                embedding_text=chunk.embedding_text,
                            )
                            for chunk in chunk_rows
                            if chunk.section_id is not None
                        ],
                    )
                    await self.db.execute(
                        LocalPaperDocumentVersion.__table__.update()
                        .where(
                            LocalPaperDocumentVersion.paper_id == paper.id,
                            LocalPaperDocumentVersion.id != version.id,
                        )
                        .values(is_active=False)
                    )
                    version.status, version.is_active = "READY", True
                    paper.active_document_version_id = version.id
                    await self.db.commit()
                    seen_ids.add(paper.id)
                    by_hash[digest] = paper
                    if doi:
                        by_doi[doi] = paper
                    summary["indexed"] += 1
                except Exception as exc:
                    await self.db.rollback()
                    library = await self.db.get(LocalPaperLibrary, library_id)
                    run = await self.db.get(LocalPaperSyncRun, sync_run_id)
                    # rollback() expires every ORM object held in these maps.
                    # Rebuild them before the next entry so a later DOI comparison
                    # never performs implicit async I/O through a stale object.
                    existing = (
                        await self.db.scalars(
                            select(LocalPaper).where(LocalPaper.library_id == library_id)
                        )
                    ).all()
                    by_citekey = {item.citekey: item for item in existing}
                    by_hash = {item.source_sha256: item for item in existing}
                    by_doi = {item.doi: item for item in existing if item.doi}
                    # A failed *rebuild* must never make an already active,
                    # searchable version disappear during reconciliation.
                    # Keep the existing document as the serving version and
                    # record the failed attempt in quarantine for operators.
                    preserved = by_citekey.get(entry.citekey) or by_hash.get(digest)
                    if (
                        preserved is not None
                        and preserved.active_document_version_id is not None
                        and preserved.status == "INDEXED"
                    ):
                        seen_ids.add(preserved.id)
                    await self._quarantine(
                        run,
                        library,
                        "PARSE_OR_INDEX_ERROR",
                        relative,
                        entry.citekey,
                        f"{type(exc).__name__}: {exc}",
                    )
                    await self.db.commit()
                    summary["errors"] += 1

            summary["matched_source_files"] = len(claimed_sources)
            summary["referenced_source_files"] = len(referenced_sources)
            summary["related_attachments"] = len(referenced_sources - claimed_sources)
            for relative in supported:
                # Only a source referenced by no BibTeX entry is genuinely
                # unmatched.  Secondary PDF/HTML attachments are accounted as
                # related attachments rather than quarantine noise.
                if relative not in referenced_sources:
                    await self._quarantine(
                        run,
                        library,
                        "UNMATCHED_SOURCE",
                        relative,
                        None,
                        "Source has no reliable Better BibTeX entry match.",
                    )
                    summary["unmatched_source"] += 1
                    if summary["unmatched_source"] % 25 == 0:
                        await self._checkpoint_sync_progress(
                            library_id=library_id,
                            sync_run_id=sync_run_id,
                            summary=summary,
                            stage="RECONCILING",
                            current_path=relative,
                        )
            for paper in existing:
                if paper.id not in seen_ids and paper.status == "INDEXED":
                    paper.status = "MISSING"
                    await self.index.delete_paper(
                        collection=library.qdrant_collection, paper_id=paper.id
                    )
                    summary["missing"] += 1
            run = await self.db.get(LocalPaperSyncRun, sync_run_id)
            library = await self.db.get(LocalPaperLibrary, library_id)
            if run is None or library is None:
                raise NotFoundError("Local paper library sync target disappeared")
            completed_summary = {
                **dict(summary),
                "sequence": int((run.summary_json or {}).get("sequence", 0)) + 1,
                "stage": "COMPLETED",
                "current_citekey": None,
                "current_path": None,
            }
            run.status, run.summary_json = "COMPLETED", completed_summary
            library.status, library.last_sync_summary_json = "READY", completed_summary
            completed_event = _local_paper_sync_event_payload(
                sync_run_id=sync_run_id,
                status="COMPLETED",
                summary=completed_summary,
                error_message=None,
            )
            self.db.add(
                LocalPaperSyncEvent(
                    sync_run_id=sync_run_id,
                    sequence=int(completed_summary["sequence"]),
                    event_type="COMPLETED",
                    payload_json=completed_event,
                )
            )
            await self.db.commit()
            await _publish_local_paper_sync_event(completed_event)
        except Exception as exc:
            await self.db.rollback()
            run = await self.db.get(LocalPaperSyncRun, sync_run_id)
            library = await self.db.get(LocalPaperLibrary, library_id)
            if run is not None and library is not None:
                failed_summary = {
                    **dict(summary),
                    "sequence": int((run.summary_json or {}).get("sequence", 0)) + 1,
                    "stage": "FAILED",
                    "current_citekey": None,
                    "current_path": None,
                }
                run.status, run.error_message, run.summary_json = (
                    "FAILED",
                    f"{type(exc).__name__}: {exc}",
                    failed_summary,
                )
                library.status, library.last_sync_summary_json = "FAILED", failed_summary
                failed_event = _local_paper_sync_event_payload(
                    sync_run_id=sync_run_id,
                    status="FAILED",
                    summary=failed_summary,
                    error_message=run.error_message,
                )
                self.db.add(
                    LocalPaperSyncEvent(
                        sync_run_id=sync_run_id,
                        sequence=int(failed_summary["sequence"]),
                        event_type="FAILED",
                        payload_json=failed_event,
                    )
                )
                await self.db.commit()
                await _publish_local_paper_sync_event(failed_event)
            raise

    async def search(
        self, *, owner_id: UUID, request: LocalPaperSearchRequest
    ) -> LocalPaperSearchResponse:
        # 阻止无条件查全库：必须提供 query 或至少一个元数据过滤器
        has_filter = bool(
            request.query.strip()
            or request.author
            or request.doi
            or request.bibtex_type
            or request.year_from
            or request.year_to
            or request.paper_ids
        )
        if not has_filter:
            return LocalPaperSearchResponse(items=[], total=0, retrieval_mode="metadata")

        library = await self._required_owned_library(owner_id)
        statement = select(LocalPaper).where(
            LocalPaper.library_id == library.id, LocalPaper.status == "INDEXED"
        )
        if request.paper_ids:
            statement = statement.where(LocalPaper.id.in_(request.paper_ids))
        if request.author:
            statement = statement.where(
                cast(LocalPaper.authors_json, Text).ilike(f"%{request.author}%")
            )
        if request.doi:
            statement = statement.where(
                LocalPaper.doi.ilike(f"%{_doi(request.doi) or request.doi}%")
            )
        if request.bibtex_type:
            statement = statement.where(LocalPaper.bibtex_type == request.bibtex_type.lower())
        if request.year_from:
            statement = statement.where(LocalPaper.publication_year >= request.year_from)
        if request.year_to:
            statement = statement.where(LocalPaper.publication_year <= request.year_to)
        candidates = (await self.db.scalars(statement)).all()
        allowed = {paper.id: paper for paper in candidates}
        evidence: dict[UUID, list[LocalPaperEvidenceRead]] = {}
        mode = "metadata"
        rerank_candidates: list[RetrievalChunk] = []
        eligible_candidates: list[RetrievalChunk] = []
        retrieval_run: LocalPaperRetrievalRun | None = None
        if request.query.strip() and allowed:
            active_versions = {
                paper.active_document_version_id
                for paper in candidates
                if paper.active_document_version_id is not None
            }
            stale = any(
                paper.ingestion_version != _local_index_version()
                or paper.active_document_version_id is None
                for paper in candidates
            )
            if stale:
                raise ValidationError(
                    "本地论文库索引版本已变更，必须先执行“手动同步/增量重建”后再检索。"
                )

            # ``ts_rank_cd`` is PostgreSQL full-text ranking, not BM25.  Keep
            # lexical_terms as the durable token store and calculate genuine
            # Okapi BM25 across the same metadata-filtered corpus.
            scope_key = _bm25_scope_key(active_versions, set(allowed))
            corpus = _BM25CorpusCache.get(scope_key)
            if corpus is None:
                lexical_rows = (
                    await self.db.execute(
                        select(LocalPaperChunk.id, LocalPaperChunk.lexical_terms).where(
                            LocalPaperChunk.paper_id.in_(list(allowed)),
                            LocalPaperChunk.document_version_id.in_(list(active_versions)),
                        )
                    )
                ).all()
                corpus_ids = tuple(row.id for row in lexical_rows if row.lexical_terms)
                corpus_tokens = [
                    _bm25_tokens(str(row.lexical_terms))
                    for row in lexical_rows
                    if row.lexical_terms
                ]
                corpus = _BM25CorpusCache.put(scope_key, _BM25Corpus(corpus_ids, BM25Okapi(corpus_tokens))) if corpus_tokens else None
            query_tokens = _bm25_tokens(request.query)
            bm25_scored: list[tuple[UUID, float]] = []
            if corpus is not None and query_tokens:
                scores = corpus.model.get_scores(query_tokens)
                bm25_scored = sorted(
                    ((chunk_id, float(score)) for chunk_id, score in zip(corpus.chunk_ids, scores, strict=True) if score > 0),
                    key=lambda item: item[1],
                    reverse=True,
                )[: settings.LOCAL_PAPER_BM25_CANDIDATE_LIMIT]

            # The same PostgreSQL-eligible paper IDs are passed as a Qdrant
            # payload filter. No finite dense result is later discarded merely
            # because it failed metadata constraints.
            points = await self.index.search(
                collection=library.qdrant_collection,
                query=request.query,
                limit=settings.LOCAL_PAPER_DENSE_CANDIDATE_LIMIT,
                paper_ids=list(allowed),
                document_version_ids=list(active_versions),
            )
            dense_scored: list[tuple[UUID, float, list[float] | None]] = []
            for point in points:
                payload = getattr(point, "payload", {}) or {}
                try:
                    chunk_id = UUID(str(payload.get("chunk_id")))
                except (ValueError, TypeError):
                    continue
                raw_vector = getattr(point, "vector", None)
                if isinstance(raw_vector, dict):
                    raw_vector = next(iter(raw_vector.values()), None)
                vector = (
                    [float(value) for value in raw_vector] if isinstance(raw_vector, list) else None
                )
                dense_scored.append((chunk_id, float(getattr(point, "score", 0.0)), vector))

            fused = _rrf_fuse(dense_scored, bm25_scored, rrf_k=settings.LOCAL_PAPER_RRF_K)
            fused_ids = list(fused)
            if not fused_ids:
                return LocalPaperSearchResponse(items=[], total=0, retrieval_mode="hybrid")
            rows = (
                await self.db.execute(
                    select(LocalPaperChunk, LocalPaperSection)
                    .join(LocalPaperSection, LocalPaperChunk.section_id == LocalPaperSection.id)
                    .where(
                        LocalPaperChunk.id.in_(fused_ids),
                        LocalPaperChunk.document_version_id.in_(list(active_versions)),
                    )
                )
            ).all()
            corpus: dict[UUID, RetrievalChunk] = {
                chunk.id: RetrievalChunk(chunk=chunk, parent=section, paper=allowed[chunk.paper_id])
                for chunk, section in rows
                if chunk.paper_id in allowed
            }
            missing_vector_ids = [chunk_id for chunk_id in fused if chunk_id not in {item[0] for item in dense_scored}]
            fetched_vectors = await self.index.fetch_chunk_vectors(
                collection=library.qdrant_collection, chunk_ids=missing_vector_ids
            )
            ranked = []
            for chunk_id, (rrf_score, vector_score, bm25_score, vector) in fused.items():
                if chunk_id not in corpus:
                    # Qdrant may retain a point from a worker crash; active
                    # PostgreSQL children are the final consistency authority.
                    continue
                item = corpus[chunk_id]
                item.rrf_score = rrf_score
                item.vector_score = vector_score
                item.bm25_score = bm25_score
                item.vector = vector or fetched_vectors.get(chunk_id)
                ranked.append(item)
            ranked.sort(key=lambda item: item.rrf_score, reverse=True)

            # RRF returns chunks.  Apply a document quota *before* BGE so a
            # long or duplicated PDF cannot monopolise the finite rerank pool.
            substantive_ranked = [
                item
                for item in ranked
                if _is_substantive_retrieval_chunk(item, query=request.query)
            ]
            rerank_candidates = _cap_chunks_per_paper(
                substantive_ranked,
                limit=settings.LOCAL_PAPER_RERANK_CANDIDATE_LIMIT,
                max_per_paper=settings.LOCAL_PAPER_MAX_RERANK_CHUNKS_PER_PAPER,
            )
            rerank_scores = await self.reranker.score(
                query=request.query,
                documents=[item.reranker_text for item in rerank_candidates],
            )
            if len(rerank_scores) != len(rerank_candidates):
                raise RuntimeError(
                    "BGE reranker returned a score count that does not match candidates"
                )
            for item, rerank_score in zip(rerank_candidates, rerank_scores, strict=True):
                item.rerank_score = rerank_score
            rerank_candidates.sort(
                key=lambda item: (
                    item.rerank_score is not None,
                    item.rerank_score if item.rerank_score is not None else item.rrf_score,
                ),
                reverse=True,
            )
            # Never present a tail as "reranked" when BGE did not score it.
            # A caller asking for more than the configured rerank pool receives
            # fewer papers rather than misleadingly ranked papers.
            eligible_candidates = [
                item
                for item in rerank_candidates
                if item.rerank_score is not None
                and item.rerank_score >= settings.LOCAL_PAPER_RERANK_MIN_SCORE
            ]
            selected = _select_diverse_papers(
                eligible_candidates,
                limit=request.limit,
                lambda_mult=settings.LOCAL_PAPER_MMR_LAMBDA,
            )
            ordered = [allowed[item.chunk.paper_id] for item in selected]
            selected_ids = {item.chunk.paper_id for item in selected}
            for paper_id in selected_ids:
                for item in (
                    candidate
                    for candidate in eligible_candidates
                    if candidate.chunk.paper_id == paper_id
                ):
                    if (
                        len(evidence.setdefault(paper_id, []))
                        >= settings.LOCAL_PAPER_EVIDENCE_PER_PAPER
                    ):
                        break
                    if not _is_new_evidence(evidence[paper_id], item):
                        continue
                    evidence[paper_id].append(
                        LocalPaperEvidenceRead(
                            page_number=item.chunk.page_number,
                            chunk_index=item.chunk.chunk_index,
                            text=item.chunk.content,
                            score=item.rerank_score
                            if item.rerank_score is not None
                            else item.rrf_score,
                            vector_score=item.vector_score,
                            bm25_score=item.bm25_score,
                            rrf_score=item.rrf_score,
                            rerank_score=item.rerank_score,
                            mmr_score=item.mmr_score,
                            section_heading=item.parent.heading,
                            paragraph_index=item.chunk.paragraph_index,
                            bbox=item.chunk.bbox_json,
                            figure_id=item.chunk.figure_id,
                            parent_text=item.parent.content,
                        )
                    )
            mode = "hybrid"
        else:
            ordered = sorted(
                candidates,
                key=lambda paper: (paper.publication_year or 0, paper.title),
                reverse=True,
            )

        # 按 source_sha256 去重，保留排序靠前的（相关性更高）
        seen_sha256: set[str] = set()
        deduped: list = []
        for paper in ordered:
            if paper.source_sha256 not in seen_sha256:
                seen_sha256.add(paper.source_sha256)
                deduped.append(paper)
        ordered = deduped

        # 实际返回的论文列表（受limit限制）
        result_items = ordered[: request.limit]

        trace = {
            "index_version": _local_index_version(),
            "dense_top_k": settings.LOCAL_PAPER_DENSE_CANDIDATE_LIMIT,
            "lexical_top_k": settings.LOCAL_PAPER_BM25_CANDIDATE_LIMIT,
            "rerank_top_k": settings.LOCAL_PAPER_RERANK_CANDIDATE_LIMIT,
            "rrf_k": settings.LOCAL_PAPER_RRF_K,
            "mmr_lambda": settings.LOCAL_PAPER_MMR_LAMBDA,
            "selected": [
                {
                    "paper_id": str(item.chunk.paper_id),
                    "chunk_id": str(item.chunk.id),
                    "vector_score": item.vector_score,
                    "lexical_score": item.bm25_score,
                    "rrf_score": item.rrf_score,
                    "rerank_score": item.rerank_score,
                    "mmr_score": item.mmr_score,
                }
                for item in eligible_candidates
            ],
        }
        if request.query.strip() and allowed:
            retrieval_run = LocalPaperRetrievalRun(
                library_id=library.id,
                owner_id=owner_id,
                index_version=_local_index_version(),
                request_json=request.model_dump(mode="json"),
                summary_json=trace,
            )
            self.db.add(retrieval_run)
            await self.db.flush()

        return LocalPaperSearchResponse(
            items=[
                self._paper_read(
                    paper,
                    evidence.get(paper.id, [])[: settings.LOCAL_PAPER_EVIDENCE_PER_PAPER],
                )
                for paper in result_items
            ],
            total=len(result_items),  # 返回实际数量，不是全部匹配数
            retrieval_mode=mode,
            candidate_chunks=len(rerank_candidates) if request.query.strip() and allowed else 0,
            candidate_papers=(
                len({item.chunk.paper_id for item in rerank_candidates})
                if request.query.strip() and allowed
                else len(ordered)
            ),
            rejected_by_score=(
                len(rerank_candidates) - len(eligible_candidates)
                if request.query.strip() and allowed
                else 0
            ),
            insufficient_evidence=bool(request.query.strip() and allowed and not result_items),
            retrieval_run_id=retrieval_run.id if retrieval_run else None,
            trace=trace,
        )

    async def ask(
        self,
        *,
        owner_id: UUID,
        question: str,
        limit: int,
        paper_ids: list[UUID],
        query_context: str | None = None,
    ) -> LocalPaperAskResponse:
        # The question is retrieved within the visible paper scope.  The
        # optional original search topic resolves anaphora such as “these
        # papers” without ever widening the corpus to unrelated documents.
        retrieval_query = "\n".join(part for part in [query_context, question] if part)
        # Do not run MMR across the visible result set again: a user-selected
        # set of papers is an explicit scope contract. Retrieve evidence within
        # each selected paper, then state which papers lacked usable evidence.
        scoped_items: list[LocalPaperRead] = []
        for paper_id in paper_ids[:limit]:
            scoped = await self.search(
                owner_id=owner_id,
                request=LocalPaperSearchRequest(
                    query=retrieval_query,
                    paper_ids=[paper_id],
                    limit=1,
                ),
            )
            scoped_items.extend(scoped.items)
        citations = [
            LocalPaperCitationRead(
                paper_id=paper.id,
                citekey=paper.citekey,
                title=paper.title,
                doi=paper.doi,
                authors=paper.authors,
                publication_year=paper.publication_year,
                page_number=item.page_number,
                text=item.text,
            )
            for paper in scoped_items
            # The retrieval service already applies per-paper evidence and
            # duplicate constraints.  Preserve its top distinct chunks rather
            # than reducing every paper to a single arbitrary sentence.
            for item in paper.evidence
        ]
        if not citations:
            return LocalPaperAskResponse(
                answer="本地库中没有找到可引用的页码证据，因此不生成无证据回答。",
                generated_by_llm=False,
                citations=[],
            )
        evidence_text = "\n\n".join(
            f"[{index + 1}] citekey={item.citekey}; 标题={item.title}; "
            f"作者={'；'.join(item.authors) or '未提供'}; 年份={item.publication_year or '未提供'}; "
            f"DOI={item.doi or '未提供'}; 页码={item.page_number}\n证据：{item.text}"
            for index, item in enumerate(citations)
        )
        if llm_is_configured():
            try:
                agent = Agent[None, GroundedAnswer](
                    model=build_llm_model(),
                    output_type=GroundedAnswer,
                    system_prompt=(
                        "你是严谨的本地文献分析助手。只可依据证据注册表中的内容，"
                        "不得提及、杜撰或改写注册表以外的论文、作者、年份、DOI。"
                        "输出必须符合给定结构：answer 是综合回答；claims 的每一个"
                        "观点必须使用 citation_ids 指向一条或多条证据注册表编号。"
                        "若证据不足，明确写入 uncertainty，不要补充外部知识。"
                    ),
                )
                prompt = (
                    f"问题：{question}\n\n"
                    f"检索主题上下文：{query_context or '无'}\n\n"
                    f"以下是检索到的 {len(citations)} 条本地文献页码证据：\n\n"
                    f"{evidence_text}\n\n"
                    "请仅用这些证据生成结构化回答。"
                )
                result = await agent.run(prompt)
                answer = _render_grounded_answer(result.output, len(citations))
                if answer is not None:
                    return LocalPaperAskResponse(
                        answer=answer, generated_by_llm=True, citations=citations
                    )
            except Exception:
                pass
        return LocalPaperAskResponse(
            answer="聊天模型当前不可用；以下是与问题最相关的本地页码证据（未生成无证据摘要）。",
            generated_by_llm=False,
            citations=citations,
        )

    async def export(
        self, *, owner_id: UUID, request: LocalPaperSearchRequest, format: str
    ) -> tuple[bytes, str, str]:
        result = await self.search(
            owner_id=owner_id, request=request.model_copy(update={"limit": 100})
        )
        if format == "csv":
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["citekey", "title", "authors", "year", "doi", "type", "source"])
            for paper in result.items:
                writer.writerow(
                    [
                        paper.citekey,
                        paper.title,
                        "; ".join(paper.authors),
                        paper.publication_year or "",
                        paper.doi or "",
                        paper.bibtex_type,
                        paper.relative_source_path,
                    ]
                )
            return stream.getvalue().encode(), "text/csv; charset=utf-8", "local-paper-library.csv"
        if format == "bibtex":
            library = await self._required_owned_library(owner_id)
            papers = (
                await self.db.scalars(
                    select(LocalPaper).where(
                        LocalPaper.library_id == library.id,
                        LocalPaper.id.in_([item.id for item in result.items]),
                    )
                )
            ).all()
            return (
                "\n\n".join(paper.bibtex_entry for paper in papers).encode(),
                "application/x-bibtex; charset=utf-8",
                "local-paper-library.bib",
            )
        if format == "opml":
            outlines = "\n".join(
                f'    <outline text="{html_module.escape(paper.title, quote=True)}" _citekey="{html_module.escape(paper.citekey, quote=True)}" _doi="{html_module.escape(paper.doi or "", quote=True)}" />'
                for paper in result.items
            )
            return (
                f'<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0"><body>\n{outlines}\n</body></opml>\n'.encode(),
                "text/x-opml; charset=utf-8",
                "local-paper-library.opml",
            )
        lines = ["# 本地论文库检索结果", "", f"共 {len(result.items)} 篇。", ""]
        for paper in result.items:
            lines.extend(
                [
                    f"## {paper.title}",
                    f"- Citekey: `{paper.citekey}`",
                    f"- 作者：{'; '.join(paper.authors) or '未提供'}",
                    f"- 年份：{paper.publication_year or '未提供'}",
                    f"- DOI：{paper.doi or '未提供'}",
                    f"- 类型：{paper.bibtex_type} · {paper.source_kind}",
                    f"- 源文件：`{paper.relative_source_path}`",
                ]
            )
            for item in paper.evidence:
                lines.append(f"- p.{item.page_number}: {item.text[:360]}")
            lines.append("")
        return "\n".join(lines).encode(), "text/markdown; charset=utf-8", "local-paper-library.md"

    async def _required_owned_library(self, owner_id: UUID) -> LocalPaperLibrary:
        library = await self._library(owner_id=owner_id)
        if library is None:
            raise NotFoundError("本地论文库尚未初始化，请由管理员先同步")
        return library

    @staticmethod
    def _assert_owner(library: LocalPaperLibrary, owner_id: UUID) -> None:
        if library.owner_id != owner_id:
            raise AuthorizationError("本地论文库仅对首个导入管理员私有")

    async def _quarantine(
        self,
        run: LocalPaperSyncRun,
        library: LocalPaperLibrary,
        kind: str,
        relative_path: str | None,
        citekey: str | None,
        detail: str,
    ) -> None:
        self.db.add(
            LocalPaperQuarantineItem(
                library_id=library.id,
                sync_run_id=run.id,
                item_kind=kind,
                relative_path=relative_path,
                citekey=citekey,
                detail=detail,
            )
        )

    @staticmethod
    def _sync_read(run: LocalPaperSyncRun) -> LocalLibrarySyncRunRead:
        return LocalLibrarySyncRunRead.model_validate(run)

    @staticmethod
    def _paper_read(
        paper: LocalPaper, evidence: Iterable[LocalPaperEvidenceRead]
    ) -> LocalPaperRead:
        return LocalPaperRead(
            id=paper.id,
            citekey=paper.citekey,
            doi=paper.doi,
            title=paper.title,
            authors=paper.authors_json,
            publication_year=paper.publication_year,
            bibtex_type=paper.bibtex_type,
            source_kind=paper.source_kind,
            relative_source_path=paper.relative_source_path,
            evidence=list(evidence),
            abstract_text=paper.abstract_text,
            introduction_text=paper.introduction_text,
            conclusion_text=paper.conclusion_text,
        )
