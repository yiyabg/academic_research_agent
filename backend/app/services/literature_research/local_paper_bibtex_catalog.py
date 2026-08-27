"""Safe Better BibTeX parsing and catalog-field normalization.

This module has no database or filesystem dependency: it converts an export
into deterministic catalog records only.  Ingestion owns persistence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


@dataclass(frozen=True)
class BibEntry:
    entry_type: str
    citekey: str
    fields: dict[str, str]
    raw: str


def _read_balanced(text: str, start: int, opening: str, closing: str) -> tuple[str, int]:
    if opening == closing:
        index, escaped = start + 1, False
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
    """Parse Better BibTeX without evaluating input or accepting code."""
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


def authors(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+and\s+", value, flags=re.I) if part.strip()]


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _DOI_PREFIX.sub("", value.strip()).rstrip("/ .")
    return cleaned.lower() or None


def publication_year(value: str | None) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
    return int(match.group(1)) if match else None
