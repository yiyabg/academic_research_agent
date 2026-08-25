"""Search-only selection must use strict metadata candidates, not parsed PDFs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.literature_research.pipeline_stages import ResearchPipelineStages


@pytest.mark.anyio
async def test_search_only_freezes_top_ranked_metadata_selection_without_pdf() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        execution_mode="search_only",
        target_count=1,
        progress_json={
            "query_count": 1,
            "successful_query_count": 1,
            "exhausted_query_count": 1,
        },
    )
    first_work, second_work = uuid4(), uuid4()
    first_version, second_version = uuid4(), uuid4()
    rows = [
        (
            SimpleNamespace(id=first_work),
            SimpleNamespace(id=first_version),
            None,
            SimpleNamespace(cross_encoder_score=0.93, semantic_score=0.8, lexical_score=0.7),
        ),
        (
            SimpleNamespace(id=second_work),
            SimpleNamespace(id=second_version),
            None,
            SimpleNamespace(cross_encoder_score=0.81, semantic_score=0.9, lexical_score=0.8),
        ),
    ]
    db = AsyncMock()
    stage = ResearchPipelineStages(db)
    with (
        patch(
            "app.services.literature_research.pipeline_stages.evidence_repository.list_catalog_report_rows",
            new=AsyncMock(return_value=rows),
        ) as catalog_rows,
        patch(
            "app.services.literature_research.pipeline_stages.evidence_repository.list_analysis_ready_versions",
            new=AsyncMock(),
        ) as analysis_ready,
        patch(
            "app.services.literature_research.pipeline_stages.run_repository.set_counts_and_progress",
            new=AsyncMock(),
        ) as persist,
    ):
        output = await stage.selection_checkpoint(run)

    catalog_rows.assert_awaited_once_with(db, run_id=run.id)
    analysis_ready.assert_not_awaited()
    assert output["strict_count"] == 1
    assert output["catalog_eligible_count"] == 2
    progress = persist.await_args.kwargs["progress"]
    assert progress["catalog_scope"] == "metadata_only"
    assert progress["catalog_selection"] == [
        {
            "rank": 1,
            "work_id": str(first_work),
            "version_id": str(first_version),
            "relevance_score": 0.93,
        }
    ]
