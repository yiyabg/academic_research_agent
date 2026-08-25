"""Read-only live contract smoke test for public scholarly adapters."""

import asyncio
from datetime import date

from app.clients.scholarly import ArxivSource, CrossrefSource, OpenAlexSource
from app.schemas.literature_research.discovery import ScholarlySourceName, SourceQuery
from app.schemas.literature_research.protocol import DocumentType


async def main() -> None:
    sources = [
        (ScholarlySourceName.CROSSREF, CrossrefSource()),
        (ScholarlySourceName.OPENALEX, OpenAlexSource()),
        (ScholarlySourceName.ARXIV, ArxivSource()),
    ]
    for name, client in sources:
        query = SourceQuery(
            query_id=f"live-{name.value}",
            family="live-smoke",
            source=name,
            query_text="research agent",
            date_from=date(2025, 1, 1),
            date_to=date(2026, 8, 21),
            publication_types=[DocumentType.JOURNAL_ARTICLE],
        )
        try:
            page = await client.search(query)
            print(
                name.value,
                f"status={page.http_status}",
                f"records={len(page.records)}",
                f"raw_bytes={len(page.raw_body)}",
                f"fingerprint={page.request_fingerprint[:20]}",
            )
        except Exception as exc:
            print(name.value, type(exc).__name__, str(exc)[:160])
        finally:
            await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
