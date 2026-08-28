"""Research run creation, ownership, and idempotency service."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.db.models.literature_research.outbox import ResearchOutboxEvent
from app.db.models.literature_research.run import ResearchRun
from app.repositories.literature_research import outbox as outbox_repo
from app.repositories.literature_research import run as run_repo
from app.schemas.literature_research.event import ResearchEventType
from app.schemas.literature_research.protocol import ProtocolStatus, ResearchProtocol
from app.schemas.literature_research.run import ExecutionMode, ResearchRunCreate
from app.services.literature_research.organization import ResearchOrganizationService
from app.services.literature_research.project import ResearchProjectService
from app.services.literature_research.protocol import ResearchProtocolService
from app.services.llm_provider import (
    llm_is_configured,
    probe_llm_provider,
    selected_llm_credential_name,
)


class ResearchRunService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.project_service = ResearchProjectService(db)
        self.protocol_service = ResearchProtocolService(db)
        self.organizations = ResearchOrganizationService(db)

    async def create(
        self,
        request: ResearchRunCreate,
        owner_id: UUID,
        *,
        active_organization_id: UUID | None = None,
    ) -> tuple[ResearchRun, bool]:
        existing = await run_repo.get_by_client_request(
            self.db, owner_id, request.client_request_id
        )
        if existing is not None:
            if (
                active_organization_id is not None
                and existing.organization_id != active_organization_id
            ):
                raise ValidationError(
                    message="The idempotent run belongs to a different organization context"
                )
            return existing, False
        if request.execution_mode == ExecutionMode.FULL_RESEARCH and not llm_is_configured():
            raise ExternalServiceError(
                message=(
                    "Full research requires an LLM provider credential; "
                    "use search_only/validate_only or configure "
                    f"{selected_llm_credential_name()}"
                ),
                code="RESEARCH_LLM_NOT_CONFIGURED",
            )
        if request.execution_mode == ExecutionMode.FULL_RESEARCH:
            llm_health = await probe_llm_provider()
            if llm_health["status"] != "healthy":
                raise ExternalServiceError(
                    message="Full research requires a reachable configured LLM provider",
                    code="RESEARCH_LLM_UNAVAILABLE",
                    details={
                        "provider": llm_health.get("provider"),
                        "model": llm_health.get("model"),
                        "error_type": llm_health.get("error_type"),
                    },
                )
        project = await self.project_service.get_owned(request.project_id, owner_id)
        if active_organization_id is not None and project.organization_id != active_organization_id:
            raise ValidationError(message="Project does not belong to X-Research-Organization-ID")
        version = await self.protocol_service.get(
            request.project_id, request.protocol_version, owner_id
        )
        if version.status != ProtocolStatus.APPROVED.value:
            raise ConflictError(
                message="Research runs require an approved protocol version",
                code="PROTOCOL_NOT_APPROVED",
            )
        protocol = ResearchProtocol.model_validate(version.protocol_json)
        run = await run_repo.create(
            self.db,
            project_id=project.id,
            protocol_version_id=version.id,
            owner_id=owner_id,
            organization_id=project.organization_id,
            execution_mode=request.execution_mode,
            client_request_id=request.client_request_id,
            protocol_hash=version.protocol_hash,
            target_count=protocol.quantity_policy.target_count,
        )
        await outbox_repo.create(
            self.db,
            run_id=run.id,
            event_type=ResearchEventType.RUN_STATE_CHANGED,
            stage=run.state,
            payload={"previous_state": None, "state": run.state, "state_version": 0},
        )
        return run, True

    async def get_owned(self, run_id: UUID, owner_id: UUID) -> ResearchRun:
        """Return a personal owned run or a run visible through current membership."""
        run = await run_repo.get_owned(self.db, run_id, owner_id)
        if run is None:
            raise NotFoundError(message="Research run not found", details={"run_id": str(run_id)})
        return run

    async def list_owned(
        self,
        owner_id: UUID,
        *,
        organization_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ResearchRun]:
        if organization_id is not None:
            await self.organizations.require_member(organization_id, owner_id)
        return await run_repo.list_owned(
            self.db,
            owner_id,
            organization_id=organization_id,
            skip=skip,
            limit=limit,
        )

    async def list_events(
        self, run_id: UUID, owner_id: UUID, *, after_sequence: int = 0, limit: int = 500
    ) -> list[ResearchOutboxEvent]:
        await self.get_owned(run_id, owner_id)
        return await outbox_repo.list_after(
            self.db, run_id, after_sequence=after_sequence, limit=limit
        )
