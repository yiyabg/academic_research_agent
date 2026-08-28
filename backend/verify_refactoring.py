#!/usr/bin/env python3
"""Offline-first verification for the local-paper-library refactoring.

It never calls an LLM/provider. Database validation is optional because a
developer laptop may not have the production PostgreSQL topology running.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.schemas.literature_research.local_library import (
    LocalPaperSearchResponse,
    QueryInterpretation,
)
from app.services.literature_research.local_paper_query_parser import LocalPaperQueryParser


def check_parser() -> None:
    parser = LocalPaperQueryParser()
    result = parser.parse(query="6G 2026年发表的 semantic communication")
    assert result.semantic_query == "6G semantic communication"
    assert result.effective_filters == {"year_from": 2026, "year_to": 2026}
    assert (
        parser.parse(query="会议论文关于机器学习").effective_filters["bibtex_type"]
        == "inproceedings"
    )
    print("PASS parser: year, false-positive, and Chinese BibTeX checks")


def check_imports_and_schema() -> None:
    from app.services.literature_research.local_paper_evidence import LocalPaperEvidenceRetriever
    from app.services.literature_research.local_paper_retrieval import LocalPaperChunkRetriever

    del LocalPaperEvidenceRetriever, LocalPaperChunkRetriever
    interpretation = QueryInterpretation(
        raw_query="2026年发表的 semantic communication",
        semantic_query="semantic communication",
        effective_filters={"year_from": 2026, "year_to": 2026},
        filter_sources={"year_from": "parsed", "year_to": "parsed"},
    )
    response = LocalPaperSearchResponse(
        items=[], total=0, retrieval_mode="metadata", query_interpretation=interpretation
    )
    assert response.query_interpretation is not None
    print("PASS imports and API schema")


def check_alembic_head() -> None:
    config = Config(str(Path(__file__).with_name("alembic.ini")))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads, "Alembic has no revision head"
    print(f"PASS Alembic revision graph: {', '.join(heads)}")


async def check_database() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'local_papers' "
                    "AND column_name IN ('venue', 'keywords_json')"
                )
            )
            columns = {row[0] for row in result}
        required = {"venue", "keywords_json"}
        assert columns == required, f"missing columns: {sorted(required - columns)}"
        print("PASS database schema")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-db", action="store_true", help="do not connect to PostgreSQL")
    options = parser.parse_args()
    try:
        check_parser()
        check_imports_and_schema()
        check_alembic_head()
        if options.skip_db:
            print("UNVERIFIED database schema: skipped by --skip-db")
        else:
            asyncio.run(check_database())
    except Exception as exc:
        print(f"FAIL verification: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
