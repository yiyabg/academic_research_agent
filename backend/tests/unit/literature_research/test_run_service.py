"""Run idempotency and protocol-approval gate tests."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError, ExternalServiceError
from app.schemas.literature_research.protocol import (
    DocumentType,
    ProtocolCompileRequest,
    ProtocolStatus,
)
from app.schemas.literature_research.run import ExecutionMode, ResearchRunCreate
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.run import ResearchRunService


@pytest.fixture
def service() -> ResearchRunService:
    return ResearchRunService(AsyncMock())


def request() -> ResearchRunCreate:
    return ResearchRunCreate(
        project_id=uuid4(),
        protocol_version=1,
        client_request_id="client-request-0001",
        execution_mode=ExecutionMode.SEARCH_ONLY,
    )


@pytest.mark.anyio
async def test_full_research_rejected_when_llm_is_not_configured(
    service: ResearchRunService,
) -> None:
    body = request().model_copy(update={"execution_mode": ExecutionMode.FULL_RESEARCH})
    with (
        patch(
            "app.services.literature_research.run.run_repo.get_by_client_request",
            new=AsyncMock(return_value=None),
        ),
        patch("app.services.literature_research.run.llm_is_configured", return_value=False),
        pytest.raises(ExternalServiceError) as caught,
    ):
        await service.create(body, uuid4())
    assert caught.value.code == "RESEARCH_LLM_NOT_CONFIGURED"


@pytest.mark.anyio
async def test_full_research_rejected_when_llm_is_unreachable(
    service: ResearchRunService,
) -> None:
    body = request().model_copy(update={"execution_mode": ExecutionMode.FULL_RESEARCH})
    with (
        patch(
            "app.services.literature_research.run.run_repo.get_by_client_request",
            new=AsyncMock(return_value=None),
        ),
        patch("app.services.literature_research.run.llm_is_configured", return_value=True),
        patch(
            "app.services.literature_research.run.probe_llm_provider",
            new=AsyncMock(
                return_value={
                    "status": "unavailable",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "error_type": "APITimeoutError",
                }
            ),
        ),
        pytest.raises(ExternalServiceError) as caught,
    ):
        await service.create(body, uuid4())
    assert caught.value.code == "RESEARCH_LLM_UNAVAILABLE"
    assert caught.value.details == {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "error_type": "APITimeoutError",
    }


@pytest.mark.anyio
async def test_repeated_client_request_returns_existing_run(
    service: ResearchRunService,
) -> None:
    existing = MagicMock()
    with patch(
        "app.services.literature_research.run.run_repo.get_by_client_request",
        new=AsyncMock(return_value=existing),
    ):
        result, created = await service.create(request(), uuid4())
    assert result is existing
    assert created is False


@pytest.mark.anyio
async def test_unapproved_protocol_cannot_start_run(service: ResearchRunService) -> None:
    owner_id = uuid4()
    project = MagicMock(id=uuid4(), organization_id=None)
    version = MagicMock(status=ProtocolStatus.DRAFT.value)
    service.project_service.get_owned = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service.protocol_service.get = AsyncMock(return_value=version)  # type: ignore[method-assign]
    with (
        patch(
            "app.services.literature_research.run.run_repo.get_by_client_request",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(ConflictError, match="approved protocol"),
    ):
        await service.create(request(), owner_id)


@pytest.mark.anyio
async def test_approved_protocol_creates_run_and_initial_event(
    service: ResearchRunService,
) -> None:
    owner_id = uuid4()
    project = MagicMock(id=uuid4(), organization_id=None)
    protocol = (
        ProtocolCompilerService()
        .compile(
            ProtocolCompileRequest(
                topic="auditable agents",
                as_of_date=date(2026, 8, 21),
                allowed_types=[DocumentType.JOURNAL_ARTICLE],
            )
        )
        .protocol
    )
    version = MagicMock(
        id=uuid4(),
        status=ProtocolStatus.APPROVED.value,
        protocol_json=protocol.model_dump(mode="json", by_alias=True),
        protocol_hash="sha256:" + "a" * 64,
    )
    created_run = MagicMock(id=uuid4(), state="QUEUED")
    service.project_service.get_owned = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service.protocol_service.get = AsyncMock(return_value=version)  # type: ignore[method-assign]
    with (
        patch(
            "app.services.literature_research.run.run_repo.get_by_client_request",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.literature_research.run.run_repo.create",
            new=AsyncMock(return_value=created_run),
        ) as create_run,
        patch(
            "app.services.literature_research.run.outbox_repo.create",
            new=AsyncMock(),
        ) as create_event,
    ):
        result, created = await service.create(request(), owner_id)

    assert result is created_run
    assert created is True
    assert create_run.await_args.kwargs["target_count"] == 20
    create_event.assert_awaited_once()
