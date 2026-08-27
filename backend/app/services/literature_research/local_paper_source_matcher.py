"""Filesystem-safe matching between BibTeX attachments and local documents."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.services.literature_research.local_paper_bibtex_catalog import BibEntry

SUPPORTED_SUFFIXES = {".pdf": "pdf", ".html": "html", ".htm": "html"}


def relative_source(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def safe_source(root: Path, candidate: str) -> Path | None:
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
    """Return portable attachment paths from Better BibTeX's ``file`` field."""
    value = entry.fields.get("file", "")
    if not value:
        return []
    paths: list[str] = []
    for segment in re.split(r"(?<!\\);", value):
        for part in segment.strip().split(":"):
            part = part.strip()
            if part and re.search(r"\.(pdf|html?)$", part, re.I):
                paths.append(part)
    return list(dict.fromkeys(paths))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()
