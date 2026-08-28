"""Deterministic natural language query parser for local paper search.

Extracts high-confidence structured filters from user queries without LLM calls.
"""
# ruff: noqa: RUF001 - Chinese punctuation is part of the accepted query grammar.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ParsedQuery:
    """Result of parsing a natural language query into filters."""

    raw_query: str
    semantic_query: str
    parsed_filters: dict[str, object]
    effective_filters: dict[str, object]
    filter_sources: dict[str, Literal["parsed", "explicit"]]
    warnings: list[str] = field(default_factory=list)


class LocalPaperQueryParser:
    """Extract structured filters from natural language queries.

    Only applies high-confidence, unambiguous patterns to avoid false positives.
    Year expressions like "6G", "3D", "GPT-4" are explicitly excluded.
    """

    # Chinese text has no word boundary between a year and its grammar, so
    # these deliberately use the grammatical suffix/prefix rather than ``\b``.
    # Requiring a four digit calendar year also keeps 6G, 3D and IEEE 802.11
    # out without disabling a real year elsewhere in the same query.
    _YEAR = r"(1[5-9]\d{2}|20\d{2}|21\d{2})"
    YEAR_RANGE = re.compile(
        rf"{_YEAR}\s*(?:年)?\s*(?:-|~|—|至|到)\s*{_YEAR}\s*(?:年)?(?:的)?(?:论文|文章|papers?)?",
        re.I,
    )
    YEAR_FROM = re.compile(
        rf"(?:从|自)\s*{_YEAR}\s*(?:年)?\s*(?:起|开始|及以后|以后)|"
        rf"{_YEAR}\s*年\s*(?:及以后|以后)|"
        rf"(?:since|after)\s+{_YEAR}\b",
        re.I,
    )
    YEAR_TO = re.compile(
        rf"(?:截至|截止|直到)\s*{_YEAR}\s*(?:年)?\s*(?:的|及以前|以前|之前)?|"
        rf"{_YEAR}\s*年\s*(?:及以前|以前|之前)|"
        rf"(?:until|before)\s+{_YEAR}\b",
        re.I,
    )
    YEAR_EXACT = re.compile(
        rf"(?:发表于|出版于)\s*{_YEAR}\s*年\s*(?:发表|出版)?\s*的?|"
        rf"{_YEAR}\s*年\s*(?:发表|出版)?\s*的?|"
        rf"published\s+in\s+{_YEAR}\b",
        re.I,
    )

    # DOI pattern
    DOI = re.compile(
        r"(?:doi[:\s]*|https?://(?:dx\.)?doi\.org/)?"
        r"(10\.\d{4,}/[^\s]+)",
        re.I,
    )

    # BibTeX type aliases
    BIBTEX_TYPES = {
        "期刊论文": "article",
        "会议论文": "inproceedings",
        "学位论文": "phdthesis",
        "硕士论文": "mastersthesis",
        "技术报告": "techreport",
        "书籍": "book",
        "书籍章节": "incollection",
        "article": "article",
        "inproceedings": "inproceedings",
        "conference": "inproceedings",
        "phdthesis": "phdthesis",
        "mastersthesis": "mastersthesis",
        "techreport": "techreport",
        "book": "book",
        "incollection": "incollection",
    }

    _MARKED_FIELD = re.compile(r"(?:作者|author|期刊|venue)\s*[:：]\s*([^，,；;\n]+)", re.I)
    _KEYWORDS_FIELD = re.compile(r"(?:关键词|keywords)\s*[:：]\s*([^；;\n]+)", re.I)

    def parse(
        self,
        *,
        query: str,
        author: str | None = None,
        doi: str | None = None,
        bibtex_type: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        venue: str | None = None,
        keywords: list[str] | None = None,
    ) -> ParsedQuery:
        """Parse query and merge with explicit filters.

        Explicit API parameters always override parsed values.
        """
        parsed_filters: dict[str, object] = {}
        filter_sources: dict[str, Literal["parsed", "explicit"]] = {}
        warnings: list[str] = []
        semantic_query = query.strip()
        removed_spans: list[tuple[int, int]] = []

        # Extract DOI
        doi_match = self.DOI.search(query)
        if doi_match:
            parsed_filters["doi"] = doi_match.group(1)
            removed_spans.append((doi_match.start(), doi_match.end()))

        # Parse the most specific expressions first.  Unlike the old global
        # false-positive guard this still recognises a real year in "6G 2026年".
        year_range_match = self.YEAR_RANGE.search(query)
        if year_range_match:
            y_from, y_to = int(year_range_match.group(1)), int(year_range_match.group(2))
            if y_from <= y_to:
                parsed_filters.update(year_from=y_from, year_to=y_to)
                removed_spans.append((year_range_match.start(), year_range_match.end()))
            else:
                warnings.append(f"年份范围无效：year_from({y_from}) > year_to({y_to})")
                removed_spans.append((year_range_match.start(), year_range_match.end()))
        else:
            year_from_match = self.YEAR_FROM.search(query)
            if year_from_match:
                parsed_filters["year_from"] = int(
                    next(group for group in year_from_match.groups() if group)
                )
                removed_spans.append((year_from_match.start(), year_from_match.end()))
            year_to_match = self.YEAR_TO.search(query)
            if year_to_match:
                parsed_filters["year_to"] = int(
                    next(group for group in year_to_match.groups() if group)
                )
                removed_spans.append((year_to_match.start(), year_to_match.end()))
            if not year_from_match and not year_to_match:
                year_exact_match = self.YEAR_EXACT.search(query)
                if year_exact_match:
                    year_val = int(next(group for group in year_exact_match.groups() if group))
                    parsed_filters.update(year_from=year_val, year_to=year_val)
                    removed_spans.append((year_exact_match.start(), year_exact_match.end()))

        # Extract BibTeX type (only when explicit markers present)
        for alias, canonical in self.BIBTEX_TYPES.items():
            pattern = (
                re.escape(alias)
                if any("\u4e00" <= char <= "\u9fff" for char in alias)
                else rf"\b{re.escape(alias)}\b"
            )
            type_match = re.search(pattern, query, re.I)
            if type_match:
                parsed_filters["bibtex_type"] = canonical
                removed_spans.append((type_match.start(), type_match.end()))
                break

        # These labels are intentionally the only natural-language extraction
        # for people/venues/keywords: unlabelled names remain semantic text.
        for match in self._MARKED_FIELD.finditer(query):
            label, value = (
                match.group(0).split(match.group(1), 1)[0].casefold(),
                match.group(1).strip(),
            )
            key = "author" if "作者" in label or "author" in label else "venue"
            parsed_filters[key] = value
            removed_spans.append((match.start(), match.end()))
        keyword_match = self._KEYWORDS_FIELD.search(query)
        if keyword_match:
            values = [
                item.strip()
                for item in re.split(r"[,，、]", keyword_match.group(1))
                if item.strip()
            ]
            if values:
                parsed_filters["keywords"] = values
                removed_spans.append((keyword_match.start(), keyword_match.end()))

        # Remove extracted spans from semantic query
        if removed_spans:
            # Sort by start position in reverse to maintain indices
            removed_spans.sort(reverse=True)
            chars = list(semantic_query)
            for start, end in removed_spans:
                # Replace with single space to avoid word concatenation
                chars[start:end] = [" "]
            semantic_query = "".join(chars)

        # Clean up semantic query
        semantic_query = re.sub(r"\s+", " ", semantic_query).strip()
        semantic_query = re.sub(r"^[，、。；;]+|[，、。；;]+$", "", semantic_query).strip()
        # Year spans intentionally include the calendar expression.  Remove
        # only the grammar left at their edge, never ordinary topic words.
        semantic_query = re.sub(r"^(?:发表|出版)的?\s*", "", semantic_query)
        semantic_query = re.sub(r"^的\s*", "", semantic_query)
        if re.fullmatch(r"(?:论文|文章|papers?)", semantic_query, re.I):
            semantic_query = ""

        # Merge with explicit filters (explicit wins)
        effective_filters: dict[str, object] = {}

        if doi is not None:
            effective_filters["doi"] = doi
            filter_sources["doi"] = "explicit"
        elif "doi" in parsed_filters:
            effective_filters["doi"] = parsed_filters["doi"]
            filter_sources["doi"] = "parsed"

        if year_from is not None:
            effective_filters["year_from"] = year_from
            filter_sources["year_from"] = "explicit"
            if "year_from" in parsed_filters and parsed_filters["year_from"] != year_from:
                warnings.append(
                    f"显式年份起始({year_from})覆盖了自动解析({parsed_filters['year_from']})"
                )
        elif "year_from" in parsed_filters:
            effective_filters["year_from"] = parsed_filters["year_from"]
            filter_sources["year_from"] = "parsed"

        if year_to is not None:
            effective_filters["year_to"] = year_to
            filter_sources["year_to"] = "explicit"
            if "year_to" in parsed_filters and parsed_filters["year_to"] != year_to:
                warnings.append(
                    f"显式年份截止({year_to})覆盖了自动解析({parsed_filters['year_to']})"
                )
        elif "year_to" in parsed_filters:
            effective_filters["year_to"] = parsed_filters["year_to"]
            filter_sources["year_to"] = "parsed"

        # Validate year range
        y_from = effective_filters.get("year_from")
        y_to = effective_filters.get("year_to")
        if isinstance(y_from, int) and isinstance(y_to, int) and y_from > y_to:
            warnings.append(f"年份范围无效：year_from({y_from}) > year_to({y_to})")

        if bibtex_type is not None:
            effective_filters["bibtex_type"] = bibtex_type
            filter_sources["bibtex_type"] = "explicit"
        elif "bibtex_type" in parsed_filters:
            effective_filters["bibtex_type"] = parsed_filters["bibtex_type"]
            filter_sources["bibtex_type"] = "parsed"

        if author is not None:
            effective_filters["author"] = author
            filter_sources["author"] = "explicit"
        elif "author" in parsed_filters:
            effective_filters["author"] = parsed_filters["author"]
            filter_sources["author"] = "parsed"

        if venue is not None:
            effective_filters["venue"] = venue
            filter_sources["venue"] = "explicit"
        elif "venue" in parsed_filters:
            effective_filters["venue"] = parsed_filters["venue"]
            filter_sources["venue"] = "parsed"

        if keywords is not None:
            effective_filters["keywords"] = keywords
            filter_sources["keywords"] = "explicit"
        elif "keywords" in parsed_filters:
            effective_filters["keywords"] = parsed_filters["keywords"]
            filter_sources["keywords"] = "parsed"

        return ParsedQuery(
            raw_query=query,
            semantic_query=semantic_query,
            parsed_filters=parsed_filters,
            effective_filters=effective_filters,
            filter_sources=filter_sources,
            warnings=warnings,
        )
