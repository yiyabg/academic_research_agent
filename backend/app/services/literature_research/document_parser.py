"""Layout-aware scholarly parsing with OCR fallback and a quality ledger."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

import httpx
import pymupdf

from app.core.config import settings
from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    ParsedBlock,
    ParsedDocument,
    ParsingQuality,
)
from app.services.literature_research.evidence_locator import build_parsed_block
from app.services.literature_research.object_store import ResearchObjectStore

logger = logging.getLogger(__name__)

_CAPTION = re.compile(r"(?im)^(?:fig(?:ure)?\.?|table)\s+[A-Za-z]?\d+[A-Za-z]?")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if not self.suppressed and data.strip():
            self.parts.append(data.strip())


@dataclass(frozen=True, slots=True)
class _LayoutBlock:
    page_number: int
    text: str
    bbox: tuple[float, float, float, float]
    extraction_method: str


@dataclass(frozen=True, slots=True)
class _PDFExtraction:
    blocks: list[_LayoutBlock]
    page_count: int
    parsed_pages: set[int]
    ocr_pages: set[int]
    table_count: int
    figure_count: int
    caption_count: int
    linked_caption_count: int
    error_codes: list[str]


class ResearchDocumentParser:
    def __init__(self, object_store: ResearchObjectStore) -> None:
        self.object_store = object_store

    async def _grobid_tei(self, payload: bytes) -> bytes | None:
        """Return structural TEI when configured; quality policy handles failure."""
        if not settings.GROBID_URL:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=settings.GROBID_TIMEOUT_SECONDS, trust_env=False
            ) as client:
                response = await client.post(
                    f"{settings.GROBID_URL.rstrip('/')}/api/processFulltextDocument",
                    files={"input": ("paper.pdf", payload, "application/pdf")},
                    data={"consolidateHeader": "1", "consolidateCitations": "0"},
                )
                response.raise_for_status()
                return response.content
        except (httpx.HTTPError, OSError):
            logger.warning("GROBID unavailable; structural score will reflect the outage")
            return None

    @staticmethod
    async def runtime_healthcheck() -> dict[str, str]:
        executable = shutil.which("tesseract")
        if executable is None:
            raise RuntimeError("Tesseract executable is unavailable")
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        if process.returncode != 0:
            raise RuntimeError("Tesseract version probe failed")
        tesseract_version = output.decode(errors="replace").splitlines()[0]
        if not settings.GROBID_URL:
            raise RuntimeError("GROBID_URL is required for full-research parsing")
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get(f"{settings.GROBID_URL.rstrip('/')}/api/isalive")
            response.raise_for_status()
        return {
            "pymupdf": pymupdf.VersionBind,
            "tesseract": tesseract_version,
            "grobid": response.text.strip() or "alive",
        }

    @staticmethod
    def _tei_headings(tei: bytes | None) -> list[str]:
        if not tei:
            return []
        try:
            root = ET.fromstring(tei)
        except ET.ParseError:
            logger.warning("GROBID returned invalid TEI; ignoring structural enrichment")
            return []
        headings: list[str] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "head":
                continue
            heading = " ".join("".join(element.itertext()).split())
            if heading and heading not in headings:
                headings.append(heading)
        return headings

    @staticmethod
    def _text_blocks(page, *, textpage=None) -> list[tuple]:
        return [
            block
            for block in page.get_text("blocks", textpage=textpage)
            if len(block) > 6 and block[6] == 0 and str(block[4]).strip()
        ]

    @classmethod
    def _extract_pdf_layout(cls, payload: bytes) -> _PDFExtraction:
        document = pymupdf.open(stream=payload, filetype="pdf")
        blocks: list[_LayoutBlock] = []
        parsed_pages: set[int] = set()
        ocr_pages: set[int] = set()
        table_count = 0
        figure_count = 0
        caption_count = 0
        linked_caption_count = 0
        error_codes: list[str] = []
        try:
            page_count = document.page_count
            for page in document:
                page_number = page.number + 1
                page_blocks = cls._text_blocks(page)
                native_characters = sum(len(str(item[4]).strip()) for item in page_blocks)
                extraction_method = "native"
                if native_characters < settings.OCR_MIN_NATIVE_CHARACTERS:
                    try:
                        textpage = page.get_textpage_ocr(
                            language=settings.OCR_LANGUAGES,
                            dpi=settings.OCR_DPI,
                            full=True,
                        )
                        ocr_blocks = cls._text_blocks(page, textpage=textpage)
                        if (
                            sum(len(str(item[4]).strip()) for item in ocr_blocks)
                            > native_characters
                        ):
                            page_blocks = ocr_blocks
                            extraction_method = "ocr"
                            ocr_pages.add(page_number)
                    except Exception as exc:
                        logger.warning("OCR failed for page %d: %s", page_number, exc)
                        error_codes.append(f"OCR_FAILED_PAGE_{page_number}")

                page_text = "\n".join(str(item[4]).strip() for item in page_blocks)
                if page_text:
                    parsed_pages.add(page_number)
                else:
                    error_codes.append(f"NO_TEXT_PAGE_{page_number}")

                page_figure_count = len(page.get_images(full=True))
                try:
                    page_figure_count += sum(
                        rect.get_area() >= page.rect.get_area() * 0.01
                        for rect in page.cluster_drawings()
                    )
                except Exception:
                    error_codes.append(f"DRAWING_DETECTION_FAILED_PAGE_{page_number}")
                figure_count += page_figure_count
                try:
                    tables = page.find_tables()
                    page_table_count = len(tables.tables) if tables else 0
                except Exception:
                    page_table_count = 0
                    error_codes.append(f"TABLE_DETECTION_FAILED_PAGE_{page_number}")
                table_count += page_table_count
                page_caption_count = len(_CAPTION.findall(page_text))
                caption_count += page_caption_count
                if page_figure_count or page_table_count:
                    linked_caption_count += page_caption_count

                for item in page_blocks:
                    text = str(item[4]).strip()
                    blocks.append(
                        _LayoutBlock(
                            page_number=page_number,
                            text=text,
                            bbox=(
                                round(float(item[0]), 3),
                                round(float(item[1]), 3),
                                round(float(item[2]), 3),
                                round(float(item[3]), 3),
                            ),
                            extraction_method=extraction_method,
                        )
                    )
        finally:
            document.close()
        return _PDFExtraction(
            blocks=blocks,
            page_count=page_count,
            parsed_pages=parsed_pages,
            ocr_pages=ocr_pages,
            table_count=table_count,
            figure_count=figure_count,
            caption_count=caption_count,
            linked_caption_count=linked_caption_count,
            error_codes=error_codes,
        )

    async def _store_blocks(self, acquired: AcquiredFullText, blocks: list[ParsedBlock]) -> str:
        payload = "\n".join(
            json.dumps(block.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            for block in blocks
        ).encode()
        prefix, separator, _ = acquired.object_key.partition("/fulltext/")
        root = f"{prefix}/parsed" if separator else "parsed"
        key = f"{root}/{acquired.version_id}/{acquired.document_sha256}.blocks.jsonl"
        return await self.object_store.put(
            key,
            payload,
            content_type="application/x-ndjson",
            metadata={"source-document-sha256": acquired.document_sha256},
        )

    @staticmethod
    def _quality_status(
        *,
        text_coverage: float,
        page_count_match: bool,
        total_characters: int,
        caption_count: int,
        caption_link_rate: float,
    ) -> tuple[str, list[str]]:
        errors: list[str] = []
        if text_coverage < settings.PARSING_MIN_TEXT_COVERAGE:
            errors.append("TEXT_COVERAGE_LOW")
        if not page_count_match:
            errors.append("PAGE_COUNT_MISMATCH")
        if total_characters < settings.PARSING_MIN_TOTAL_CHARACTERS:
            errors.append("TOTAL_TEXT_TOO_SHORT")
        if caption_count and caption_link_rate < settings.PARSING_MIN_CAPTION_LINK_RATE:
            errors.append("CAPTION_LINK_RATE_LOW")
        return ("PASSED" if not errors else "PARSING_LOW_CONFIDENCE", errors)

    async def parse_with_quality(self, acquired: AcquiredFullText) -> ParsedDocument:
        if acquired.malware_scan_status != "CLEAN":
            raise ValueError("Only malware-scanned CLEAN documents may be parsed")
        payload = await self.object_store.get(acquired.object_key)
        if acquired.document_sha256 != hashlib.sha256(payload).hexdigest():
            raise ValueError("Stored full-text hash does not match acquisition ledger")

        if acquired.content_type in {"text/html", "application/xhtml+xml"}:
            extractor = _HTMLTextExtractor()
            extractor.feed(payload.decode("utf-8", errors="replace"))
            text = "\n\n".join(extractor.parts)
            blocks = (
                [build_parsed_block(block_id="html-0001", text=text, char_start=0)] if text else []
            )
            status, errors = self._quality_status(
                text_coverage=float(bool(text)),
                page_count_match=True,
                total_characters=len(text),
                caption_count=len(_CAPTION.findall(text)),
                caption_link_rate=0.0,
            )
            quality = ParsingQuality(
                status=status,
                page_count=1,
                parsed_page_count=int(bool(text)),
                text_coverage=float(bool(text)),
                page_count_match=True,
                section_detection_f1_estimate=0.0,
                table_count=0,
                figure_count=0,
                caption_count=len(_CAPTION.findall(text)),
                caption_link_rate=0.0,
                ocr_page_count=0,
                ocr_page_ratio=0.0,
                total_characters=len(text),
                parser_versions={"html_parser": "stdlib"},
                error_codes=errors,
            )
        elif acquired.content_type == "application/pdf":
            extraction = await asyncio.to_thread(self._extract_pdf_layout, payload)
            if extraction.page_count < 1:
                raise ValueError("PDF contains no pages")
            tei = await self._grobid_tei(payload)
            if tei:
                prefix, separator, suffix = acquired.object_key.partition("/fulltext/")
                if separator:
                    await self.object_store.put(
                        f"{prefix}/parsed/{suffix.rsplit('.', 1)[0]}.tei.xml",
                        tei,
                        content_type="application/tei+xml",
                        metadata={"source-document-sha256": acquired.document_sha256},
                    )
            headings = self._tei_headings(tei)
            matched_headings: set[str] = set()
            blocks = []
            offset = 0
            current_heading: str | None = None
            for index, item in enumerate(extraction.blocks, start=1):
                folded = " ".join(item.text.casefold().split())
                for heading in headings:
                    if " ".join(heading.casefold().split()) in folded:
                        current_heading = heading
                        matched_headings.add(heading)
                block = build_parsed_block(
                    block_id=f"page-{item.page_number:04d}-block-{index:05d}",
                    text=item.text,
                    char_start=offset,
                    page_number=item.page_number,
                    section_path=[current_heading] if current_heading else [],
                    bbox=item.bbox,
                    extraction_method=item.extraction_method,
                )
                blocks.append(block)
                offset = block.char_end + 2
            text_coverage = len(extraction.parsed_pages) / extraction.page_count
            page_count_match = all(
                1 <= block.page_number <= extraction.page_count
                for block in blocks
                if block.page_number is not None
            )
            total_characters = sum(len(block.text) for block in blocks)
            status, threshold_errors = self._quality_status(
                text_coverage=text_coverage,
                page_count_match=page_count_match,
                total_characters=total_characters,
                caption_count=extraction.caption_count,
                caption_link_rate=(
                    extraction.linked_caption_count / extraction.caption_count
                    if extraction.caption_count
                    else 1.0
                ),
            )
            quality = ParsingQuality(
                status=status,
                page_count=extraction.page_count,
                parsed_page_count=len(extraction.parsed_pages),
                text_coverage=text_coverage,
                page_count_match=page_count_match,
                section_detection_f1_estimate=(
                    len(matched_headings) / len(headings) if headings else 0.0
                ),
                table_count=extraction.table_count,
                figure_count=extraction.figure_count,
                caption_count=extraction.caption_count,
                caption_link_rate=(
                    extraction.linked_caption_count / extraction.caption_count
                    if extraction.caption_count
                    else 1.0
                ),
                ocr_page_count=len(extraction.ocr_pages),
                ocr_page_ratio=len(extraction.ocr_pages) / extraction.page_count,
                total_characters=total_characters,
                parser_versions={
                    "pymupdf": pymupdf.VersionBind,
                    "grobid": "0.8.2" if tei else "unavailable",
                    "ocr": "tesseract" if extraction.ocr_pages else "not_used",
                },
                error_codes=sorted(set(extraction.error_codes + threshold_errors)),
            )
        else:
            raise ValueError(f"Unsupported parser content type: {acquired.content_type}")

        blocks_key = await self._store_blocks(acquired, blocks)
        return ParsedDocument(
            blocks=blocks,
            quality=quality.model_copy(update={"blocks_object_key": blocks_key}),
        )

    async def parse(self, acquired: AcquiredFullText) -> list[ParsedBlock]:
        return (await self.parse_with_quality(acquired)).blocks
