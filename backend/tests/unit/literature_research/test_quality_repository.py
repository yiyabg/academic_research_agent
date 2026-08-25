"""Metric lookup must deterministically select an annual licensed fact."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.literature_research.quality import find_metric


@pytest.mark.anyio
async def test_find_metric_filters_future_years_and_returns_exact_fact_identity() -> None:
    fact_id, snapshot_id = uuid4(), uuid4()
    fact = SimpleNamespace(
        id=fact_id,
        metric_name="jif",
        metric_value=8.2,
        metric_year=2025,
        venue_id=uuid4(),
        venue_name="Journal of Agent Systems",
        source_row=2,
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        source_name="Licensed JCR export",
        source_version="2025",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        license_attested=True,
        status="ACTIVE",
        payload_sha256="a" * 64,
    )
    result = SimpleNamespace(one_or_none=lambda: (fact, snapshot))
    db = AsyncMock()
    db.execute.return_value = result

    observation = await find_metric(
        db,
        venue_name=fact.venue_name,
        venue_type="journal",
        metric_name="jif",
        as_of_date=date(2026, 8, 22),
    )

    assert observation is not None
    assert observation.fact_id == fact_id
    assert observation.metric_year == 2025
    statement = db.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "research_venue_metric_facts.metric_year <= 2026" in sql
    assert "research_venue_metric_facts.metric_year DESC" in sql
