"""Explicit source-native publication type mappings.

Unknown source values must stay UNKNOWN. Mapping them to a permitted type
would let unverified metadata pass the protocol's hard document-type gate.
"""

from collections.abc import Iterable

from app.schemas.literature_research.protocol import DocumentType

CROSSREF_NATIVE_TYPES: dict[str, DocumentType] = {
    "journal-article": DocumentType.JOURNAL_ARTICLE,
    "proceedings-article": DocumentType.CONFERENCE_PAPER,
    "posted-content": DocumentType.PREPRINT,
    "book-chapter": DocumentType.BOOK_CHAPTER,
    "standard": DocumentType.STANDARD,
    "dissertation": DocumentType.THESIS,
}

OPENALEX_NATIVE_TYPES: dict[str, DocumentType] = {
    "article": DocumentType.JOURNAL_ARTICLE,
    "proceedings-article": DocumentType.CONFERENCE_PAPER,
    "preprint": DocumentType.PREPRINT,
    "review": DocumentType.REVIEW,
    "book-chapter": DocumentType.BOOK_CHAPTER,
    "standard": DocumentType.STANDARD,
    "dissertation": DocumentType.THESIS,
}

# Crossref has no separate type for a review article. Journal articles are an
# upstream candidate envelope; the normalized hard gate still makes the final
# inclusion decision.
CROSSREF_PROTOCOL_TYPES: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.JOURNAL_ARTICLE: ("journal-article",),
    DocumentType.CONFERENCE_PAPER: ("proceedings-article",),
    DocumentType.PREPRINT: ("posted-content",),
    DocumentType.REVIEW: ("journal-article",),
    DocumentType.SYSTEMATIC_REVIEW: ("journal-article",),
    DocumentType.BOOK_CHAPTER: ("book-chapter",),
    DocumentType.STANDARD: ("standard",),
    DocumentType.THESIS: ("dissertation",),
}

OPENALEX_PROTOCOL_TYPES: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.JOURNAL_ARTICLE: ("article",),
    DocumentType.CONFERENCE_PAPER: ("proceedings-article",),
    DocumentType.PREPRINT: ("preprint",),
    DocumentType.REVIEW: ("review",),
    DocumentType.SYSTEMATIC_REVIEW: ("review",),
    DocumentType.BOOK_CHAPTER: ("book-chapter",),
    DocumentType.STANDARD: ("standard",),
    DocumentType.THESIS: ("dissertation",),
}


def _native_types(
    publication_types: Iterable[DocumentType],
    mapping: dict[DocumentType, tuple[str, ...]],
) -> tuple[str, ...]:
    values: list[str] = []
    for publication_type in publication_types:
        for value in mapping.get(publication_type, ()):
            if value not in values:
                values.append(value)
    return tuple(values)


def crossref_types_for(publication_types: Iterable[DocumentType]) -> tuple[str, ...]:
    return _native_types(publication_types, CROSSREF_PROTOCOL_TYPES)


def openalex_types_for(publication_types: Iterable[DocumentType]) -> tuple[str, ...]:
    return _native_types(publication_types, OPENALEX_PROTOCOL_TYPES)
