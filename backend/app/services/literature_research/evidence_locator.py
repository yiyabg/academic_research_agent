"""Hash-bound evidence locators over parsed document blocks."""

import hashlib
from uuid import UUID

from app.schemas.literature_research.evidence import EvidenceLocator, ParsedBlock


def build_parsed_block(
    *,
    block_id: str,
    text: str,
    char_start: int,
    page_number: int | None = None,
    section_path: list[str] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    extraction_method: str = "native",
) -> ParsedBlock:
    return ParsedBlock(
        block_id=block_id,
        page_number=page_number,
        section_path=section_path or [],
        text=text,
        char_start=char_start,
        char_end=char_start + len(text),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        bbox=bbox,
        extraction_method=extraction_method,
    )


def locate_quote(
    *,
    work_id: UUID,
    version_id: UUID,
    block: ParsedBlock,
    quote: str,
    document_sha256: str,
) -> EvidenceLocator:
    local_start = block.text.find(quote)
    if local_start < 0:
        raise ValueError("Evidence quote is not an exact substring of the parsed block")
    start = block.char_start + local_start
    seed = f"{version_id}:{block.block_id}:{start}:{quote}"
    return EvidenceLocator(
        evidence_id=f"E_{hashlib.sha256(seed.encode()).hexdigest()[:20].upper()}",
        work_id=work_id,
        version_id=version_id,
        block_id=block.block_id,
        page_number=block.page_number,
        section_path=block.section_path,
        quote=quote,
        quote_start=start,
        quote_end=start + len(quote),
        block_text_sha256=block.text_sha256,
        document_sha256=document_sha256,
        bbox=block.bbox,
        extraction_method=block.extraction_method,
    )
