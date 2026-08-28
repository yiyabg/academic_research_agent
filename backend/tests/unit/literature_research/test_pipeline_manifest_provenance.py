"""Production pipeline wiring for immutable artifact provenance."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.schemas.literature_research.analysis import SynthesisOutput
from app.services.literature_research.pipeline_stages import ResearchPipelineStages


@pytest.mark.anyio
async def test_render_generation_collects_run_source_and_metric_provenance() -> None:
    run_id = uuid4()
    metric_snapshot_ids = [uuid4(), uuid4()]
    source_snapshot_hashes = ["a" * 64, "b" * 64]
    run = SimpleNamespace(
        id=run_id,
        project_id=uuid4(),
        organization_id=uuid4(),
        protocol_hash="sha256:" + "c" * 64,
        target_count=20,
        progress_json={},
    )
    synthesis = SynthesisOutput(
        overview="No paper passed the immutable constraints.",
        themes=[],
        included_work_ids=[],
    )
    stages = ResearchPipelineStages(AsyncMock())
    stages.protocol = AsyncMock(return_value=SimpleNamespace(topic="Auditable topic"))

    with (
        patch(
            "app.services.literature_research.pipeline_stages.analysis_repository.get_synthesis",
            new=AsyncMock(
                return_value=SimpleNamespace(synthesis_json=synthesis.model_dump(mode="json"))
            ),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.analysis_repository.list_analyses",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.evidence_repository.list_relevant_report_rows",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.literature_research.pipeline_stages.discovery_repository.list_source_snapshot_hashes",
            new=AsyncMock(return_value=source_snapshot_hashes),
        ) as list_sources,
        patch(
            "app.services.literature_research.pipeline_stages.quality_repository.list_used_metric_snapshot_ids",
            new=AsyncMock(return_value=metric_snapshot_ids),
        ) as list_metrics,
        patch(
            "app.services.literature_research.pipeline_stages.collect_exclusion_audit_rows",
            new=AsyncMock(return_value=[]),
        ) as list_exclusions,
        patch(
            "app.services.literature_research.pipeline_stages.collect_metric_snapshot_audit_rows",
            new=AsyncMock(return_value=[]),
        ) as list_metric_rows,
        patch(
            "app.services.literature_research.pipeline_stages.ArtifactService.render_all",
            new=AsyncMock(return_value=[object()] * 8),
        ) as render_all,
        patch(
            "app.services.literature_research.pipeline_stages.run_repository.set_counts_and_progress",
            new=AsyncMock(),
        ),
    ):
        result = await stages.render_generation(run, generation=3)

    list_sources.assert_awaited_once_with(stages.db, run_id=run_id)
    list_metrics.assert_awaited_once_with(stages.db, run_id=run_id)
    list_exclusions.assert_awaited_once_with(stages.db, run_id=run_id, included_work_ids=set())
    list_metric_rows.assert_awaited_once_with(stages.db, run_id=run_id)
    render_all.assert_awaited_once()
    call = render_all.await_args
    assert call.kwargs["source_snapshot_hashes"] == source_snapshot_hashes
    assert call.kwargs["metric_snapshot_ids"] == metric_snapshot_ids
    assert call.kwargs["exclusion_rows"] == []
    assert call.kwargs["metric_snapshot_rows"] == []
    assert call.kwargs["generation"] == 3
    assert result == {
        "artifact_count": 8,
        "rendered_paper_count": 0,
        "output_generation": 3,
    }
