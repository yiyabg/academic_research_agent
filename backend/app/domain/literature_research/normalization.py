"""Pure identifier and text canonicalization policies."""

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^]]*\])?")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    normalized = normalized.rstrip(". ")
    return normalized or None


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(value))
    text = _TAG_RE.sub(" ", text)
    text = _LATEX_COMMAND_RE.sub(" ", text)
    text = text.replace("{", " ").replace("}", " ").lower()
    text = "".join(char if char.isalnum() else " " for char in text)
    return " ".join(text.split())


def normalize_venue_name(value: str) -> str:
    return normalize_title(value)
