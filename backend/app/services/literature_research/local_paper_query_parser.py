"""Deterministic natural language query parser for local paper search.

Extracts high-confidence structured filters from user queries without LLM calls.
"""

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

    # Year patterns - must be standalone to avoid matching model names
    YEAR_EXACT = re.compile(
        r"(?:^|[\s，、。；;])"
        r"(?:发表于|出版于|published\s+in\s+)?"
        r"(1[5-9]\d{2}|20\d{2}|21\d{2})"
        r"(?:年|年份)?(?:发表|出版|的|published)?"
        r"(?=[\s，、。；;]|$)",
        re.I,
    )
    YEAR_RANGE = re.compile(
        r"(?:^|[\s，、。；;])"
        r"(1[5-9]\d{2}|20\d{2}|21\d{2})"
        r"[\s]*[-~—至到]+[\s]*"
        r"(1[5-9]\d{2}|20\d{2}|21\d{2})"
        r"(?:年)?",
        re.I,
    )
    YEAR_FROM = re.compile(
        r"(?:^|[\s，、。；;])"
        r"(?:从|since|after|自|)[\s]*"
        r"(1[5-9]\d{2}|20\d{2}|21\d{2})"
        r"(?:年)?[\s]*(?:起|开始|及以后|onwards?|later)?",
        re.I,
    )
    YEAR_TO = re.compile(
        r"(?:^|[\s，、。；;])"
        r"(?:截至|截止|直到|until|before|到)[\s]*"
        r"(1[5-9]\d{2}|20\d{2}|21\d{2})"
        r"(?:年)?[\s]*(?:及以前|之前)?",
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

    # False positive patterns to exclude from year matching
    FALSE_POSITIVE_PATTERNS = [
        r"\b\d+G\b",  # 5G, 6G
        r"\b\d+D\b",  # 2D, 3D
        r"\bGPT-?\d+\b",  # GPT-4, GPT4
        r"\bIEEE\s*802\.\d+\b",  # IEEE 802.11
        r"\bRFC\s*\d+\b",  # RFC 2616
        r"\b(?:sha|md5)[-]?\d+\b",  # sha256, md5
    ]

    def parse(
        self,
        *,
        query: str,
        author: str | None = None,
        doi: str | None = None,
        bibtex_type: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
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

        # Extract year patterns - check for false positives first
        if not any(re.search(pattern, query, re.I) for pattern in self.FALSE_POSITIVE_PATTERNS):
            # Try exact year first
            year_exact_match = self.YEAR_EXACT.search(query)
            if year_exact_match:
                year_val = int(year_exact_match.group(1))
                parsed_filters["year_from"] = year_val
                parsed_filters["year_to"] = year_val
                removed_spans.append((year_exact_match.start(), year_exact_match.end()))

            # Try year range
            year_range_match = self.YEAR_RANGE.search(query)
            if year_range_match and "year_from" not in parsed_filters:
                y_from = int(year_range_match.group(1))
                y_to = int(year_range_match.group(2))
                if y_from <= y_to:
                    parsed_filters["year_from"] = y_from
                    parsed_filters["year_to"] = y_to
                    removed_spans.append((year_range_match.start(), year_range_match.end()))
                else:
                    warnings.append(f"年份范围无效：{y_from}>{y_to}，已忽略")

            # Try "from year"
            year_from_match = self.YEAR_FROM.search(query)
            if year_from_match and "year_from" not in parsed_filters:
                parsed_filters["year_from"] = int(year_from_match.group(1))
                removed_spans.append((year_from_match.start(), year_from_match.end()))

            # Try "until year"
            year_to_match = self.YEAR_TO.search(query)
            if year_to_match and "year_to" not in parsed_filters:
                parsed_filters["year_to"] = int(year_to_match.group(1))
                removed_spans.append((year_to_match.start(), year_to_match.end()))

        # Extract BibTeX type (only when explicit markers present)
        for alias, canonical in self.BIBTEX_TYPES.items():
            pattern = rf"\b{re.escape(alias)}\b"
            if re.search(pattern, query, re.I):
                parsed_filters["bibtex_type"] = canonical
                break

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
        if (
            isinstance(y_from, int)
            and isinstance(y_to, int)
            and y_from > y_to
        ):
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

        return ParsedQuery(
            raw_query=query,
            semantic_query=semantic_query,
            parsed_filters=parsed_filters,
            effective_filters=effective_filters,
            filter_sources=filter_sources,
            warnings=warnings,
        )
