"""Source-aware scholarly metadata normalization with field provenance."""

import hashlib
import html
import json
import re
from datetime import date
from typing import Any

from app.domain.literature_research.normalization import (
    normalize_doi,
    normalize_title,
    normalize_venue_name,
)
from app.domain.literature_research.source_types import (
    CROSSREF_NATIVE_TYPES,
    OPENALEX_NATIVE_TYPES,
)
from app.schemas.literature_research.discovery import RawSourceRecord, ScholarlySourceName
from app.schemas.literature_research.protocol import DocumentType
from app.schemas.literature_research.work import (
    FieldCandidate,
    FieldProvenance,
    NormalizedAuthor,
    NormalizedPaper,
    NormalizedVenue,
    VenueType,
    WorkDates,
    WorkIdentifiers,
    WorkVersionType,
)

_TAG_RE = re.compile(r"<[^>]+>")
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)


def _first_text(value: Any) -> str | None:
    if isinstance(value, list):
        return str(value[0]).strip() if value else None
    return str(value).strip() if value else None


def _date_parts(value: Any) -> date | None:
    try:
        parts = value["date-parts"][0]
        return date(
            int(parts[0]),
            int(parts[1]) if len(parts) > 1 else 1,
            int(parts[2]) if len(parts) > 2 else 1,
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _raw_hash(raw: dict[str, Any]) -> str:
    data = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def _provenance(
    field: str, value: Any, record: RawSourceRecord, rule: str, confidence: float = 0.8
) -> FieldProvenance:
    return FieldProvenance(
        field=field,
        chosen=value,
        status="VERIFIED" if value is not None else "MISSING",
        candidates=[
            FieldCandidate(
                value=value,
                source=record.source,
                source_id=record.source_id,
                retrieved_at=record.retrieved_at,
                confidence=confidence,
            )
        ],
        resolution_rule=rule,
    )


class MetadataNormalizerService:
    def normalize(self, record: RawSourceRecord) -> NormalizedPaper:
        handlers = {
            ScholarlySourceName.CROSSREF: self._crossref,
            ScholarlySourceName.OPENALEX: self._openalex,
            ScholarlySourceName.ARXIV: self._arxiv,
        }
        handler = handlers.get(record.source)
        if handler is None:
            raise ValueError(f"Unsupported scholarly source: {record.source}")
        return handler(record)

    def _crossref(self, record: RawSourceRecord) -> NormalizedPaper:
        raw = record.raw
        title = _first_text(raw.get("title")) or "[untitled]"
        crossref_type = raw.get("type")
        document_type = CROSSREF_NATIVE_TYPES.get(crossref_type, DocumentType.UNKNOWN)
        venue_type = {
            DocumentType.JOURNAL_ARTICLE: VenueType.JOURNAL,
            DocumentType.CONFERENCE_PAPER: VenueType.CONFERENCE,
            DocumentType.PREPRINT: VenueType.REPOSITORY,
        }.get(document_type, VenueType.OTHER)
        venue_name = _first_text(raw.get("container-title"))
        doi = normalize_doi(raw.get("DOI"))
        authors = []
        for item in raw.get("author", []):
            given = item.get("given")
            family = item.get("family")
            name = " ".join(part for part in (given, family) if part).strip()
            if name:
                authors.append(
                    NormalizedAuthor(
                        name=name,
                        given_name=given,
                        family_name=family,
                        orcid=item.get("ORCID"),
                        affiliations=[
                            value["name"]
                            for value in item.get("affiliation", [])
                            if value.get("name")
                        ],
                    )
                )
        links = raw.get("link", [])
        pdf_url = next(
            (
                item.get("URL")
                for item in links
                if item.get("content-type") == "application/pdf" and item.get("URL")
            ),
            None,
        )
        dates = WorkDates(
            published_online=_date_parts(raw.get("published-online")),
            issued=_date_parts(raw.get("issued")),
            published_print=_date_parts(raw.get("published-print")),
        )
        return NormalizedPaper(
            source=record.source,
            source_id=record.source_id,
            retrieved_at=record.retrieved_at,
            title=title,
            title_normalized=normalize_title(title),
            abstract=_TAG_RE.sub(" ", html.unescape(raw.get("abstract", ""))).strip() or None,
            authors=authors,
            document_type=document_type,
            version_type=(
                WorkVersionType.PREPRINT
                if document_type == DocumentType.PREPRINT
                else WorkVersionType.CONFERENCE_VERSION
                if document_type == DocumentType.CONFERENCE_PAPER
                else WorkVersionType.UNKNOWN
                if document_type == DocumentType.UNKNOWN
                else WorkVersionType.VERSION_OF_RECORD
            ),
            venue=(
                NormalizedVenue(
                    name=venue_name,
                    normalized_name=normalize_venue_name(venue_name),
                    venue_type=venue_type,
                    issns=list(dict.fromkeys(raw.get("ISSN", []))),
                    publisher=raw.get("publisher"),
                )
                if venue_name
                else None
            ),
            dates=dates,
            identifiers=WorkIdentifiers(doi=doi),
            canonical_url=raw.get("URL"),
            open_access_pdf_url=pdf_url,
            volume=raw.get("volume"),
            issue=raw.get("issue"),
            pages=raw.get("page"),
            language=raw.get("language"),
            field_provenance={
                "title": _provenance("title", title, record, "crossref_publisher_deposit"),
                "doi": _provenance("doi", doi, record, "normalize_doi", 0.95),
            },
            raw_sha256=_raw_hash(raw),
        )

    def _openalex(self, record: RawSourceRecord) -> NormalizedPaper:
        raw = record.raw
        title = raw.get("display_name") or raw.get("title") or "[untitled]"
        openalex_type = raw.get("type")
        document_type = OPENALEX_NATIVE_TYPES.get(openalex_type, DocumentType.UNKNOWN)
        authors = []
        for authorship in raw.get("authorships", []):
            author = authorship.get("author") or {}
            name = author.get("display_name")
            if name:
                authors.append(
                    NormalizedAuthor(
                        name=name,
                        orcid=author.get("orcid"),
                        affiliations=[
                            institution["display_name"]
                            for institution in authorship.get("institutions", [])
                            if institution.get("display_name")
                        ],
                    )
                )
        location = raw.get("primary_location") or {}
        source = location.get("source") or {}
        venue_name = source.get("display_name")
        source_type = source.get("type")
        venue_type = {
            "journal": VenueType.JOURNAL,
            "conference": VenueType.CONFERENCE,
            "repository": VenueType.REPOSITORY,
        }.get(source_type, VenueType.OTHER)
        ids = raw.get("ids") or {}
        doi = normalize_doi(raw.get("doi") or ids.get("doi"))
        abstract = self._openalex_abstract(raw.get("abstract_inverted_index"))
        return NormalizedPaper(
            source=record.source,
            source_id=record.source_id,
            retrieved_at=record.retrieved_at,
            title=title,
            title_normalized=normalize_title(title),
            abstract=abstract,
            authors=authors,
            document_type=document_type,
            version_type=(
                WorkVersionType.PREPRINT
                if document_type == DocumentType.PREPRINT
                else WorkVersionType.CONFERENCE_VERSION
                if document_type == DocumentType.CONFERENCE_PAPER
                else WorkVersionType.UNKNOWN
                if document_type == DocumentType.UNKNOWN
                else WorkVersionType.VERSION_OF_RECORD
            ),
            venue=(
                NormalizedVenue(
                    name=venue_name,
                    normalized_name=normalize_venue_name(venue_name),
                    venue_type=venue_type,
                    issn_l=source.get("issn_l"),
                    issns=source.get("issn") or [],
                    publisher=source.get("host_organization_name"),
                )
                if venue_name
                else None
            ),
            dates=WorkDates(published_online=_iso_date(raw.get("publication_date"))),
            identifiers=WorkIdentifiers(
                doi=doi,
                openalex_id=raw.get("id"),
                pmid=ids.get("pmid"),
            ),
            canonical_url=location.get("landing_page_url") or raw.get("id"),
            open_access_pdf_url=location.get("pdf_url"),
            language=raw.get("language"),
            field_provenance={
                "title": _provenance("title", title, record, "openalex_display_name", 0.8),
                "doi": _provenance("doi", doi, record, "normalize_doi", 0.85),
            },
            raw_sha256=_raw_hash(raw),
        )

    def _arxiv(self, record: RawSourceRecord) -> NormalizedPaper:
        raw = record.raw
        title = raw.get("title") or "[untitled]"
        arxiv_url = str(raw.get("id"))
        arxiv_id = _ARXIV_VERSION_RE.sub("", arxiv_url.rsplit("/", 1)[-1])
        pdf_url = next(
            (
                item.get("href")
                for item in raw.get("links", [])
                if item.get("type") == "application/pdf"
            ),
            None,
        )
        return NormalizedPaper(
            source=record.source,
            source_id=record.source_id,
            retrieved_at=record.retrieved_at,
            title=title,
            title_normalized=normalize_title(title),
            abstract=raw.get("summary"),
            authors=[NormalizedAuthor(name=name) for name in raw.get("authors", [])],
            document_type=DocumentType.PREPRINT,
            version_type=WorkVersionType.PREPRINT,
            venue=NormalizedVenue(
                name="arXiv",
                normalized_name="arxiv",
                venue_type=VenueType.REPOSITORY,
            ),
            dates=WorkDates(preprint_first_posted=_iso_date(raw.get("published"))),
            identifiers=WorkIdentifiers(doi=normalize_doi(raw.get("doi")), arxiv_id=arxiv_id),
            canonical_url=arxiv_url,
            open_access_pdf_url=pdf_url,
            field_provenance={
                "title": _provenance("title", title, record, "arxiv_atom", 0.9),
                "arxiv_id": _provenance("arxiv_id", arxiv_id, record, "arxiv_id", 1.0),
            },
            raw_sha256=_raw_hash(raw),
        )

    @staticmethod
    def _openalex_abstract(index: Any) -> str | None:
        if not isinstance(index, dict) or not index:
            return None
        positions = [position for values in index.values() for position in values]
        if not positions:
            return None
        words = [""] * (max(positions) + 1)
        for word, indexes in index.items():
            for position in indexes:
                words[position] = word
        return " ".join(word for word in words if word)
