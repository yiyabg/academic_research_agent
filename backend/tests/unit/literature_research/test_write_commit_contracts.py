"""Write endpoints must commit before returning a success response."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.routes.v1.literature_research.evaluations import (
    create_dataset,
    evaluate_run,
)
from app.api.routes.v1.literature_research.memory import (
    confirm_profile,
    create_policy_version,
)
from app.api.routes.v1.literature_research.metrics import import_metric_snapshot
from app.api.routes.v1.literature_research.protocols import (
    advise_and_compile_protocol,
    approve_protocol,
    compile_protocol,
)


@pytest.mark.anyio
async def test_protocol_writes_commit_before_returning() -> None:
    project_id, user_id = uuid4(), uuid4()
    stored = object()
    service = SimpleNamespace(
        db=AsyncMock(),
        compile=AsyncMock(return_value=stored),
        advise_and_compile=AsyncMock(return_value=stored),
        approve=AsyncMock(return_value=stored),
    )
    user = SimpleNamespace(id=user_id)

    assert (
        await compile_protocol(project_id, object(), user, service)  # type: ignore[arg-type]
        is stored
    )
    assert (
        await advise_and_compile_protocol(  # type: ignore[arg-type]
            project_id, object(), user, service
        )
        is stored
    )
    assert (
        await approve_protocol(  # type: ignore[arg-type]
            project_id,
            1,
            SimpleNamespace(protocol_hash="sha256:" + "a" * 64),
            user,
            service,
        )
        is stored
    )
    assert service.db.commit.await_count == 3


@pytest.mark.anyio
async def test_memory_governance_writes_commit_before_returning() -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=uuid4())
    profile = object()
    policy = object()
    with (
        patch(
            "app.api.routes.v1.literature_research.memory.memory_repository."
            "confirm_profile",
            new=AsyncMock(return_value=profile),
        ),
        patch(
            "app.api.routes.v1.literature_research.memory.memory_repository."
            "create_policy_version",
            new=AsyncMock(return_value=policy),
        ),
    ):
        assert (
            await confirm_profile(object(), user, db)  # type: ignore[arg-type]
            is profile
        )
        assert (
            await create_policy_version(object(), user, db)  # type: ignore[arg-type]
            is policy
        )
    assert db.commit.await_count == 2


@pytest.mark.anyio
async def test_evaluation_writes_commit_before_returning() -> None:
    project_id, run_id, dataset_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    dataset = object()
    report = object()
    service = SimpleNamespace(
        db=AsyncMock(),
        create_dataset=AsyncMock(return_value=dataset),
        evaluate=AsyncMock(return_value=report),
    )
    user = SimpleNamespace(id=user_id)
    body = SimpleNamespace(project_id=project_id)

    assert (
        await create_dataset(project_id, body, user, service)  # type: ignore[arg-type]
        is dataset
    )
    assert (
        await evaluate_run(run_id, dataset_id, user, service)  # type: ignore[arg-type]
        is report
    )
    assert service.db.commit.await_count == 2


@pytest.mark.anyio
async def test_metric_snapshot_import_commits_before_returning() -> None:
    db = AsyncMock()
    stored = object()
    importer = SimpleNamespace(import_csv=AsyncMock(return_value=stored))
    with patch(
        "app.api.routes.v1.literature_research.metrics.MetricSnapshotImportService",
        return_value=importer,
    ):
        result = await import_metric_snapshot(
            db=db,
            current_admin=SimpleNamespace(id=uuid4()),
            file=SimpleNamespace(read=AsyncMock(return_value=b"authorized,csv")),
            source_name="Licensed source",
            source_version="2025",
            effective_from=date(2025, 1, 1),
            effective_to=None,
            license_reference="Institutional contract",
            authorized_scope="Private deployment",
            license_attested=True,
        )
    assert result is stored
    db.commit.assert_awaited_once()
