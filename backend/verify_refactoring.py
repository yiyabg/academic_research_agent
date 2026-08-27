#!/usr/bin/env python3
"""Quick verification script for local paper library refactoring.

Run this after applying migration and restarting the service to verify
that the refactoring was deployed correctly.
"""

import asyncio
import sys
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.services.literature_research.local_paper_query_parser import LocalPaperQueryParser


async def check_migration():
    """Verify migration was applied."""
    print("🔍 Checking database migration...")
    engine = create_async_engine(settings.database_url)

    async with engine.begin() as conn:
        # Check if new columns exist
        result = await conn.execute(text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'local_papers'
            AND column_name IN ('venue', 'keywords_json')
            """
        ))
        columns = [row[0] for row in result]

        if len(columns) == 2:
            print("✅ Migration applied: venue and keywords_json columns exist")
        else:
            print(f"❌ Migration not applied: found {columns}")
            return False

    await engine.dispose()
    return True


def check_query_parser():
    """Verify query parser works."""
    print("\n🔍 Testing query parser...")

    parser = LocalPaperQueryParser()

    # Test case 1: Year extraction
    result = parser.parse("2026年发表的 semantic communication")
    assert result.effective_filters.get("year_from") == 2026
    assert result.effective_filters.get("year_to") == 2026
    assert "semantic communication" in result.semantic_query
    assert "2026" not in result.semantic_query
    print("✅ Year extraction works")

    # Test case 2: Year range
    result = parser.parse("2024-2026年的论文")
    assert result.effective_filters.get("year_from") == 2024
    assert result.effective_filters.get("year_to") == 2026
    print("✅ Year range extraction works")

    # Test case 3: False positive avoidance
    result = parser.parse("6G wireless communication")
    assert "year_from" not in result.effective_filters
    assert "6G" in result.semantic_query
    print("✅ False positive avoidance works (6G not parsed as year)")

    # Test case 4: DOI extraction
    result = parser.parse("DOI: 10.1109/TWC.2024.1234567")
    assert "10.1109/TWC.2024.1234567" in result.effective_filters.get("doi", "")
    print("✅ DOI extraction works")

    # Test case 5: Explicit filter override
    result = parser.parse("2026年的论文", year_from=2024)
    assert result.effective_filters.get("year_from") == 2024  # Explicit wins
    assert result.filter_sources.get("year_from") == "explicit"
    print("✅ Explicit filter override works")

    return True


async def check_schema_changes():
    """Verify schema updates."""
    print("\n🔍 Checking schema changes...")

    from app.schemas.literature_research.local_library import (
        LocalPaperSearchResponse,
        QueryInterpretation,
    )

    # Test QueryInterpretation schema
    qi = QueryInterpretation(
        raw_query="2026年发表的论文",
        semantic_query="论文",
        effective_filters={"year_from": 2026},
        filter_sources={"year_from": "parsed"},
        warnings=[],
    )
    assert qi.raw_query == "2026年发表的论文"
    print("✅ QueryInterpretation schema works")

    # Test LocalPaperSearchResponse includes query_interpretation
    response = LocalPaperSearchResponse(
        items=[],
        total=0,
        retrieval_mode="metadata",
        query_interpretation=qi,
    )
    assert response.query_interpretation is not None
    print("✅ LocalPaperSearchResponse includes query_interpretation")

    return True


async def check_imports():
    """Verify all new modules can be imported."""
    print("\n🔍 Checking imports...")

    try:
        from app.services.literature_research.local_paper_query_parser import (
            LocalPaperQueryParser,
            ParsedQuery,
        )
        print("✅ Query parser imports work")

        from app.services.literature_research.local_paper_retrieval import (
            LocalPaperChunkRetriever,
            RetrievedChunk,
        )
        print("✅ Chunk retriever imports work")

        from app.services.literature_research.local_paper_evidence import (
            LocalPaperEvidenceRetriever,
            AnalysisEvidence,
            PaperEvidenceResult,
        )
        print("✅ Evidence retriever imports work")

        from app.services.literature_research.local_paper_bibtex_catalog import (
            venue,
            keywords,
        )
        print("✅ BibTeX catalog updates import work")

        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False


async def main():
    """Run all verification checks."""
    print("=" * 60)
    print("Local Paper Library Refactoring - Verification Script")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)

    checks = [
        ("Imports", check_imports),
        ("Query Parser", lambda: asyncio.create_task(asyncio.coroutine(check_query_parser)())),
        ("Schema Changes", check_schema_changes),
        ("Database Migration", check_migration),
    ]

    results = []
    for name, check in checks:
        try:
            if asyncio.iscoroutinefunction(check):
                result = await check()
            else:
                result = check()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} check failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} checks passed")

    if passed == total:
        print("\n🎉 All checks passed! Refactoring deployed successfully.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} check(s) failed. Review errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
