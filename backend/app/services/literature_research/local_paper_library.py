"""Manual, read-only ingestion and retrieval for a Zotero Better BibTeX folder."""
# ruff: noqa: RUF001, RUF003 - User-facing Chinese text intentionally uses Chinese punctuation.

from __future__ import annotations

import csv
import hashlib
import html as html_module
import io
import json
import logging
import re
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID

import fitz
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
    LocalPaperFigure,
    LocalPaperLibrary,
    LocalPaperQuarantineItem,
    LocalPaperSection,
    LocalPaperSyncEvent,
    LocalPaperSyncRun,
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
from app.services.literature_research.local_paper_vector_index import (
    LocalPaperVectorChunk,
    LocalPaperVectorIndex,
)
from app.services.llm_provider import build_llm_model, llm_is_configured

SUPPORTED_SUFFIXES = {".pdf": "pdf", ".html": "html", ".htm": "html"}
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
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


@dataclass(frozen=True)
class BibEntry:
    entry_type: str
    citekey: str
    fields: dict[str, str]
    raw: str


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


def _read_balanced(text: str, start: int, opening: str, closing: str) -> tuple[str, int]:
    if opening == closing:
        index = start + 1
        escaped = False
        while index < len(text):
            char = text[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == opening:
                return text[start + 1 : index], index + 1
            index += 1
        raise ValueError("Unclosed quoted BibTeX value")
    depth, escaped, index = 0, False, start
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    raise ValueError("Unclosed BibTeX value")


def parse_bibtex(payload: str) -> list[BibEntry]:
    """Small brace-aware parser for Better BibTeX exports (no code evaluation)."""
    entries: list[BibEntry] = []
    position = 0
    header = re.compile(r"@(\w+)\s*([\{\(])\s*([^,\s]+)\s*,", re.M)
    while match := header.search(payload, position):
        entry_type, opener, citekey = match.group(1).lower(), match.group(2), match.group(3)
        closer = "}" if opener == "{" else ")"
        try:
            body, end = _read_balanced(payload, match.start(2), opener, closer)
        except ValueError:
            break
        # body begins with citekey; field parsing starts after its first comma.
        fields: dict[str, str] = {}
        index = body.find(",") + 1
        field_pattern = re.compile(r"\s*([\w-]+)\s*=\s*", re.M)
        while index > 0 and index < len(body):
            field = field_pattern.match(body, index)
            if not field:
                index += 1
                continue
            key, index = field.group(1).lower(), field.end()
            while index < len(body) and body[index].isspace():
                index += 1
            if index >= len(body):
                break
            try:
                if body[index] == "{":
                    value, index = _read_balanced(body, index, "{", "}")
                elif body[index] == '"':
                    value, index = _read_balanced(body, index, '"', '"')
                else:
                    value_end = body.find(",", index)
                    if value_end < 0:
                        value_end = len(body)
                    value, index = body[index:value_end], value_end
            except ValueError:
                break
            fields[key] = re.sub(r"\s+", " ", value).strip()
            comma = body.find(",", index)
            index = len(body) if comma < 0 else comma + 1
        entries.append(BibEntry(entry_type, citekey, fields, payload[match.start() : end]))
        position = end
    return entries


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _safe_source(root: Path, candidate: str) -> Path | None:
    candidate = candidate.replace("\\", "/").strip().lstrip("/")
    if not candidate or ".." in Path(candidate).parts:
        return None
    path = (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES else None


def attachment_paths(entry: BibEntry) -> list[str]:
    """Extract attachment file paths from Better BibTeX file field.

    Handles multiple formats:
    - Label:path:application/pdf
    - :path:application/pdf  (no label)
    - path  (bare path, some Zotero versions)
    - Label:path:PDF  (non-standard MIME)
    """
    value = entry.fields.get("file", "")
    if not value:
        return []

    paths: list[str] = []

    # Format 1: Better BibTeX standard: [Label:]path:mime[;...]
    # Matches paths ending in .pdf or .html with optional label prefix and mime suffix
    for segment in re.split(r"(?<!\\);", value):
        segment = segment.strip()
        if not segment:
            continue
        parts = segment.split(":")
        # Try all "middle" sections as path candidates
        for _i, part in enumerate(parts):
            part = part.strip()
            if re.search(r"\.(pdf|html?)$", part, re.I) and part:
                paths.append(part)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _authors(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+and\s+", value, flags=re.I) if part.strip()]


def _doi(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _DOI_PREFIX.sub("", value.strip()).rstrip("/ .")
    return cleaned.lower() or None


def _year(value: str | None) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
    return int(match.group(1)) if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


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


def extract_structured_source(path: Path) -> StructuredSource:
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
        return StructuredSource(tuple(_sections_from_paragraphs(paragraphs)), ())

    document = fitz.open(path)
    try:
        paragraphs: list[SourceParagraph] = []
        figures: list[SourceFigure] = []
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
                for table in page.find_tables().tables:
                    markdown = _table_markdown(table)
                    if markdown:
                        page_paragraphs.append(
                            SourceParagraph(
                                page_number,
                                len(page_paragraphs),
                                markdown,
                                _bbox(table.bbox),
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
        return StructuredSource(tuple(_sections_from_paragraphs(paragraphs)), tuple(figures))
    finally:
        document.close()


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
    if item.chunk.chunk_kind in {"figure_ocr", "table"}:
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


class GroundedClaim(BaseModel):
    """A model-generated claim whose citations are validated server-side."""

    text: str = Field(min_length=1, max_length=1600)
    citation_ids: list[int] = Field(min_length=1, max_length=8)


class GroundedAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=3000)
    claims: list[GroundedClaim] = Field(min_length=1, max_length=12)
    uncertainty: str | None = Field(default=None, max_length=1000)


def _render_grounded_answer(result: GroundedAnswer, citation_count: int) -> str | None:
    """Render only citation IDs from the server-issued evidence registry."""
    valid_ids = set(range(1, citation_count + 1))
    if any(not set(claim.citation_ids).issubset(valid_ids) for claim in result.claims):
        return None
    lines = ["## 综合回答", result.answer, "", "## 有证据的观点"]
    lines.extend(
        f"- {claim.text} {' '.join(f'[{index}]' for index in claim.citation_ids)}"
        for claim in result.claims
    )
    if result.uncertainty:
        lines.extend(["", "## 不确定性", result.uncertainty])
    return "\n".join(lines)


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
                        needs_reindex = paper.ingestion_version != _local_index_version() or (
                            paper.abstract_text is None
                            and paper.introduction_text is None
                            and paper.conclusion_text is None
                        )
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
                        await self.db.execute(
                            delete(LocalPaperChunk).where(LocalPaperChunk.paper_id == paper.id)
                        )
                        await self.db.execute(
                            delete(LocalPaperSection).where(LocalPaperSection.paper_id == paper.id)
                        )
                        await self.db.execute(
                            delete(LocalPaperFigure).where(LocalPaperFigure.paper_id == paper.id)
                        )
                        # ``content_sha256`` is unique per paper.  Flush the
                        # old v3 children *before* staging v4 sections and
                        # children, otherwise PostgreSQL may check a new
                        # duplicate-page header against an undeleted old row
                        # in the same rebuild transaction.
                        await self.db.flush()
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
                    section_rows: list[LocalPaperSection] = []
                    for section in structured_source.sections:
                        section_content = _normalise_text(section.content)
                        section_rows.append(
                            LocalPaperSection(
                                paper_id=paper.id,
                                page_number=section.page_number,
                                section_index=section.section_index,
                                heading=_normalise_text(section.heading) or "正文",
                                heading_level=section.heading_level,
                                content=section_content,
                                bbox_json=section.bbox,
                                section_sha256=hashlib.sha256(
                                    section_content.encode("utf-8")
                                ).hexdigest(),
                            )
                        )
                    self.db.add_all(section_rows)
                    figure_rows = [
                        LocalPaperFigure(
                            paper_id=paper.id,
                            page_number=figure.page_number,
                            figure_index=figure.figure_index,
                            figure_label=figure.figure_label,
                            bbox_json=figure.bbox,
                            caption_text=(
                                _normalise_text(figure.caption_text)
                                if figure.caption_text
                                else None
                            ),
                            ocr_text=_normalise_text(figure.ocr_text) if figure.ocr_text else None,
                            image_sha256=figure.image_sha256,
                            extractor_version=_local_index_version(),
                        )
                        for figure in structured_source.figures
                    ]
                    self.db.add_all(figure_rows)
                    await self.db.flush()
                    figure_ids = {
                        (figure.page_number, figure.figure_index): row.id
                        for figure, row in zip(structured_source.figures, figure_rows, strict=True)
                    }
                    chunk_rows: list[LocalPaperChunk] = []
                    per_page_index: Counter[int] = Counter()
                    seen_child_hashes: set[str] = set()
                    for section_source, section_row in zip(
                        structured_source.sections, section_rows, strict=True
                    ):
                        for paragraph in section_source.paragraphs:
                            # The associated figure child below is the single
                            # indexable visual evidence.  Indexing both its
                            # caption block and OCR/caption child creates a
                            # duplicate vector for every figure.
                            if paragraph.kind == "figure_caption":
                                continue
                            for child in _split_paragraph(paragraph):
                                child_text = _normalise_text(child.text)
                                if not child_text:
                                    continue
                                child_hash = hashlib.sha256(child_text.encode("utf-8")).hexdigest()
                                # Repeated running headers, repeated page
                                # titles and duplicated figure OCR are one
                                # textual fact within a paper, not separate
                                # retrieval candidates.
                                if child_hash in seen_child_hashes:
                                    continue
                                seen_child_hashes.add(child_hash)
                                chunk_index = per_page_index[child.page_number]
                                per_page_index[child.page_number] += 1
                                chunk_rows.append(
                                    LocalPaperChunk(
                                        paper_id=paper.id,
                                        section_id=section_row.id,
                                        figure_id=figure_ids.get(
                                            (child.page_number, child.figure_index)
                                        ),
                                        page_number=child.page_number,
                                        chunk_index=chunk_index,
                                        paragraph_index=child.paragraph_index,
                                        heading=_normalise_text(section_source.heading) or "正文",
                                        bbox_json=child.bbox,
                                        chunk_kind=child.kind,
                                        content=child_text,
                                        content_sha256=child_hash,
                                    )
                                )
                    self.db.add_all(chunk_rows)
                    await self.db.flush()
                    await self.index.replace_paper_chunks(
                        collection=library.qdrant_collection,
                        paper_id=paper.id,
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
                                figure_id=chunk.figure_id,
                            )
                            for chunk in chunk_rows
                            if chunk.section_id is not None
                        ],
                    )
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
        if request.query.strip() and allowed:
            stale = any(paper.ingestion_version != _local_index_version() for paper in candidates)
            if stale:
                raise ValidationError(
                    "本地论文库索引版本已变更，必须先执行“手动同步/增量重建”后再检索。"
                )

            # PostgreSQL is the authority for metadata and structural location.
            # Fetch exactly the metadata-filtered children before either recall
            # channel, so BM25 and Qdrant share the same eligible document set.
            rows = (
                await self.db.execute(
                    select(LocalPaperChunk, LocalPaperSection)
                    .join(LocalPaperSection, LocalPaperChunk.section_id == LocalPaperSection.id)
                    .where(LocalPaperChunk.paper_id.in_(list(allowed)))
                    .order_by(
                        LocalPaperChunk.paper_id,
                        LocalPaperChunk.page_number,
                        LocalPaperChunk.chunk_index,
                    )
                    .limit(settings.LOCAL_PAPER_MAX_BM25_CORPUS)
                )
            ).all()
            corpus: dict[UUID, RetrievalChunk] = {
                chunk.id: RetrievalChunk(chunk=chunk, parent=section, paper=allowed[chunk.paper_id])
                for chunk, section in rows
            }
            if not corpus:
                return LocalPaperSearchResponse(items=[], total=0, retrieval_mode="hybrid")

            corpus_items = list(corpus.values())
            tokenized = [_bm25_tokens(item.lexical_text) for item in corpus_items]
            query_tokens = _bm25_tokens(request.query)
            bm25_scored: list[tuple[UUID, float]] = []
            if query_tokens and any(tokenized):
                bm25 = BM25Okapi(tokenized)
                bm25_scored = sorted(
                    (
                        (item.chunk.id, float(score))
                        for item, score in zip(
                            corpus_items, bm25.get_scores(query_tokens), strict=True
                        )
                        if score > 0
                    ),
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
            )
            dense_scored: list[tuple[UUID, float, list[float] | None]] = []
            for point in points:
                payload = getattr(point, "payload", {}) or {}
                try:
                    chunk_id = UUID(str(payload.get("chunk_id")))
                except (ValueError, TypeError):
                    continue
                if chunk_id not in corpus:
                    # Defensive consistency check for an interrupted re-index.
                    continue
                raw_vector = getattr(point, "vector", None)
                if isinstance(raw_vector, dict):
                    raw_vector = next(iter(raw_vector.values()), None)
                vector = (
                    [float(value) for value in raw_vector] if isinstance(raw_vector, list) else None
                )
                dense_scored.append((chunk_id, float(getattr(point, "score", 0.0)), vector))

            fused = _rrf_fuse(dense_scored, bm25_scored, rrf_k=settings.LOCAL_PAPER_RRF_K)
            ranked = []
            for chunk_id, (rrf_score, vector_score, bm25_score, vector) in fused.items():
                item = corpus[chunk_id]
                item.rrf_score = rrf_score
                item.vector_score = vector_score
                item.bm25_score = bm25_score
                item.vector = vector
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
        search = await self.search(
            owner_id=owner_id,
            request=LocalPaperSearchRequest(
                query=retrieval_query,
                paper_ids=paper_ids,
                limit=limit,
            ),
        )
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
            for paper in search.items
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
