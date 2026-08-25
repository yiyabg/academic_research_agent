"""Registered scholarly metadata source adapters."""

from app.clients.scholarly.arxiv import ArxivSource
from app.clients.scholarly.base import ScholarlySource
from app.clients.scholarly.crossref import CrossrefSource
from app.clients.scholarly.openalex import OpenAlexSource
from app.clients.scholarly.unpaywall import UnpaywallClient

__all__ = [
    "ArxivSource",
    "CrossrefSource",
    "OpenAlexSource",
    "ScholarlySource",
    "UnpaywallClient",
]
