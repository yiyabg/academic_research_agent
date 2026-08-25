"""Online feedback must become identity-bearing project memory."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.routes.v1.literature_research.memory import create_feedback
from app.schemas.literature_research.memory import FeedbackType, ResearchFeedbackCreate


@pytest.mark.anyio
async def test_relevance_feedback_persists_cross_run_paper_identity_and_indexes_memory() -> None:
    run_id, work_id, project_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(project_id=project_id)
    work = SimpleNamespace(
        canonical_title="Evidence-grounded agents",
        normalized_title="evidence grounded agents",
    )
    version = SimpleNamespace(doi="10.1000/example", arxiv_id="2608.12345")
    feedback_row = SimpleNamespace(id=uuid4())
    memory_row = SimpleNamespace(id=uuid4())
    db = AsyncMock()
    run_service = SimpleNamespace(get_owned=AsyncMock(return_value=run))

    with (
        patch(
            "app.api.routes.v1.literature_research.memory.catalog_repository."
            "get_candidate_row",
            new=AsyncMock(return_value=(work, version, None, None, None)),
        ),
        patch(
            "app.api.routes.v1.literature_research.memory.memory_repository."
            "create_feedback",
            new=AsyncMock(return_value=feedback_row),
        ),
        patch(
            "app.api.routes.v1.literature_research.memory.memory_repository."
            "create_project_memory",
            new=AsyncMock(return_value=memory_row),
        ) as create_memory,
        patch(
            "app.api.routes.v1.literature_research.memory._enqueue_memory_index"
        ) as enqueue,
    ):
        accepted = await create_feedback(
            run_id=run_id,
            body=ResearchFeedbackCreate(
                work_id=work_id,
                feedback_type=FeedbackType.RELEVANCE_CORRECTION,
                payload={"decision": "EXCLUDE"},
            ),
            current_user=SimpleNamespace(id=user_id),
            run_service=run_service,  # type: ignore[arg-type]
            db=db,
        )

    body = create_memory.await_args.kwargs["body"]
    assert body.content["correction"] == {"decision": "EXCLUDE"}
    assert body.content["paper_identity"] == {
        "title": "Evidence-grounded agents",
        "normalized_title": "evidence grounded agents",
        "doi": "10.1000/example",
        "arxiv_id": "2608.12345",
    }
    assert accepted.feedback_id == feedback_row.id
    assert accepted.project_memory_id == memory_row.id
    db.commit.assert_awaited_once()
    enqueue.assert_called_once_with(memory_row.id)
