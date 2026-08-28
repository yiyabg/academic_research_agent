"""Deterministic tenant/project Qdrant namespace and mandatory payload filter."""

import hashlib
from uuid import UUID


def research_collection_name(organization_id: UUID | None, project_id: UUID) -> str:
    tenant = str(organization_id) if organization_id else "personal"
    digest = hashlib.sha256(f"{tenant}:{project_id}".encode()).hexdigest()[:24]
    return f"research_{digest}"


def research_memory_collection_name(organization_id: UUID | None, project_id: UUID) -> str:
    tenant = str(organization_id) if organization_id else "personal"
    digest = hashlib.sha256(f"memory:{tenant}:{project_id}".encode()).hexdigest()[:24]
    return f"research_memory_{digest}"


def research_payload_filter(
    *, organization_id: UUID | None, project_id: UUID, run_id: UUID
) -> dict[str, object]:
    return {
        "must": [
            {"key": "tenant_id", "match": {"value": str(organization_id or "personal")}},
            {"key": "project_id", "match": {"value": str(project_id)}},
            {"key": "run_id", "match": {"value": str(run_id)}},
        ]
    }
