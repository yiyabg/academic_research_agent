"""Zero-LLM deployed E2E for research memory, governance, and evaluation.

The script talks to the running API and Next.js BFF, waits for the real Celery
memory-index task, exercises the production Qdrant retrieval service, and then
removes every fixture it created.  Run it from the deployed ``app`` container
so the container-only PostgreSQL, Redis, and Qdrant host names resolve.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete, func, or_, select

from app.clients.redis import RedisClient
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.literature_research.evaluation import (
    ResearchEvaluationDataset,
    ResearchEvaluationResult,
)
from app.db.models.literature_research.memory import (
    ResearchPolicyVersion,
    ResearchProjectMemory,
)
from app.db.models.literature_research.project import ResearchProject
from app.db.models.literature_research.run import ResearchRun
from app.db.models.user import User, UserRole
from app.db.session import get_db_context
from app.schemas.literature_research.protocol import ProtocolCompileRequest
from app.schemas.user import UserCreate
from app.services.literature_research.protocol_memory_context import (
    ResearchProtocolMemoryContextService,
)
from app.services.literature_research.session_memory import ResearchSessionMemoryService
from app.services.literature_research.vector_namespace import (
    research_memory_collection_name,
)
from app.services.user import UserService


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def response_failure(response: httpx.Response) -> str:
    return (
        f"{response.request.method} {response.request.url.path} -> "
        f"{response.status_code}: {response.text[:1000]}"
    )


async def expect_status(response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise RuntimeError(f"Expected HTTP {expected}; {response_failure(response)}")


async def checked_object(
    response: httpx.Response, *, expected: int = 200
) -> dict[str, Any]:
    await expect_status(response, expected)
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object")
    return payload


async def checked_list(response: httpx.Response) -> list[dict[str, Any]]:
    await expect_status(response, 200)
    payload = response.json()
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RuntimeError("Expected a JSON object array")
    return payload


async def create_fixture_users() -> tuple[dict[str, UUID], dict[str, str]]:
    user_ids: dict[str, UUID] = {}
    tokens: dict[str, str] = {}
    async with get_db_context() as db:
        service = UserService(db)
        for label in ("owner", "outsider", "admin"):
            suffix = uuid4().hex[:12]
            user = await service.register(
                UserCreate(
                    email=f"research-phase6-e2e-{label}-{suffix}@example.com",
                    password=f"Phase6-{secrets.token_urlsafe(18)}",
                    full_name=f"Research Phase 6 E2E {label}",
                )
            )
            if label == "admin":
                user.role = UserRole.ADMIN.value
                user.is_app_admin = True
            await db.flush()
            user_ids[label] = user.id
            tokens[label] = create_access_token(subject=str(user.id))
    return user_ids, tokens


def protocol_payload() -> dict[str, Any]:
    return {
        "topic": "deployed memory governance verification",
        "topic_definition": (
            "Verify tenant-isolated memory, immutable governance, and evaluation controls."
        ),
        "as_of_date": datetime.now(UTC).date().isoformat(),
        "rolling_months": 12,
        "allowed_types": ["journal_article"],
        "allowed_languages": ["en"],
        "required_sources": ["crossref", "openalex", "arxiv"],
        "optional_sources": [],
        "minimum_source_families": 3,
        "publisher_verification_required": False,
        "target_count": 1,
        "shortfall_action": "return_strict_only",
    }


async def create_validate_only_run(
    owner: httpx.AsyncClient, project_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = await checked_object(
        await owner.post(
            f"/api/v1/research/projects/{project_id}/protocols:compile",
            json=protocol_payload(),
        )
    )
    approved = await checked_object(
        await owner.post(
            f"/api/v1/research/projects/{project_id}/protocols/"
            f"{protocol['version']}:approve",
            json={"protocol_hash": protocol["protocol_hash"]},
        )
    )
    run = await checked_object(
        await owner.post(
            "/api/v1/research/runs",
            json={
                "project_id": project_id,
                "protocol_version": approved["version"],
                "execution_mode": "validate_only",
                "client_request_id": f"phase6-zero-llm-{uuid4()}",
            },
        ),
        expected=201,
    )
    deadline = asyncio.get_running_loop().time() + 90
    while asyncio.get_running_loop().time() < deadline:
        run = await checked_object(
            await owner.get(f"/api/v1/research/runs/{run['id']}")
        )
        if run["state"] == "COMPLETED":
            return approved, run
        if run["state"] in {"FAILED_RETRYABLE", "FAILED_TERMINAL", "CANCELLED"}:
            raise RuntimeError(f"validate_only run entered terminal failure: {run['state']}")
        await asyncio.sleep(0.5)
    raise TimeoutError("validate_only run did not complete within 90 seconds")


async def wait_for_memory_index(
    qdrant: AsyncQdrantClient, *, collection: str, memory_id: str
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 120
    while asyncio.get_running_loop().time() < deadline:
        if await qdrant.collection_exists(collection):
            points = await qdrant.retrieve(
                collection_name=collection,
                ids=[UUID(memory_id).hex],
                with_payload=True,
            )
            if points:
                payload = points[0].payload or {}
                if isinstance(payload, dict):
                    return payload
        await asyncio.sleep(1)
    raise TimeoutError("Celery did not materialize project memory in Qdrant")


async def verify_production_memory_read(
    *, project_id: UUID, owner_id: UUID, memory_id: UUID, policy_key: str, protocol_hash: str
) -> dict[str, Any]:
    async with get_db_context() as db:
        project = await db.get(ResearchProject, project_id)
        if project is None:
            raise RuntimeError("E2E project disappeared before memory retrieval")
        bundle = await ResearchProtocolMemoryContextService(db).resolve_for_protocol_advice(
            project=project,
            owner_id=owner_id,
            request=ProtocolCompileRequest.model_validate(protocol_payload()),
        )
    provenance = bundle.provenance
    require(
        provenance.retrieval_mode == "semantic_plus_recent",
        f"Expected semantic Qdrant retrieval, got {provenance.retrieval_mode}",
    )
    require(memory_id in provenance.project_memory_ids, "L2 memory absent from provenance")
    require(provenance.profile_version == 2, "Latest confirmed L3 profile was not selected")
    require(policy_key in provenance.policy_versions, "Active L4 policy was not selected")
    require(
        provenance.approved_protocol_hash == protocol_hash,
        "Approved protocol did not retain precedence in memory resolution",
    )
    return {
        "retrieval_mode": provenance.retrieval_mode,
        "project_memory_count": len(provenance.project_memory_ids),
        "profile_version": provenance.profile_version,
        "policy_version": provenance.policy_versions[policy_key],
        "approved_protocol_precedence": True,
    }


async def cleanup(
    *,
    user_ids: dict[str, UUID],
    project_id: UUID | None,
    policy_id: UUID | None,
    session_id: UUID,
    collection: str | None,
) -> dict[str, bool]:
    redis = RedisClient()
    await redis.connect()
    try:
        for user_id in user_ids.values():
            await redis.delete(ResearchSessionMemoryService._key(user_id, session_id))
        redis_removed = not any(
            [
                await redis.exists(ResearchSessionMemoryService._key(user_id, session_id))
                for user_id in user_ids.values()
            ]
        )
    finally:
        await redis.close()

    qdrant = AsyncQdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_API_KEY or None,
    )
    try:
        if collection and await qdrant.collection_exists(collection):
            await qdrant.delete_collection(collection)
        qdrant_removed = not collection or not await qdrant.collection_exists(collection)
    finally:
        await qdrant.close()

    async with get_db_context() as db:
        if project_id is not None:
            run_ids = select(ResearchRun.id).where(ResearchRun.project_id == project_id)
            dataset_ids = select(ResearchEvaluationDataset.id).where(
                ResearchEvaluationDataset.project_id == project_id
            )
            await db.execute(
                delete(ResearchEvaluationResult).where(
                    or_(
                        ResearchEvaluationResult.run_id.in_(run_ids),
                        ResearchEvaluationResult.dataset_id.in_(dataset_ids),
                    )
                )
            )
            await db.execute(
                delete(ResearchEvaluationDataset).where(
                    ResearchEvaluationDataset.project_id == project_id
                )
            )
            await db.execute(
                delete(ResearchProjectMemory).where(
                    ResearchProjectMemory.project_id == project_id
                )
            )
            await db.execute(delete(ResearchRun).where(ResearchRun.project_id == project_id))
            await db.execute(delete(ResearchProject).where(ResearchProject.id == project_id))
        if policy_id is not None:
            await db.execute(
                delete(ResearchPolicyVersion).where(ResearchPolicyVersion.id == policy_id)
            )
        if user_ids:
            await db.execute(delete(User).where(User.id.in_(list(user_ids.values()))))
        remaining_users = int(
            await db.scalar(
                select(func.count()).select_from(User).where(
                    User.id.in_(list(user_ids.values()))
                )
            )
            or 0
        )
        remaining_projects = (
            int(
                await db.scalar(
                    select(func.count()).select_from(ResearchProject).where(
                        ResearchProject.id == project_id
                    )
                )
                or 0
            )
            if project_id is not None
            else 0
        )
        remaining_policies = (
            int(
                await db.scalar(
                    select(func.count()).select_from(ResearchPolicyVersion).where(
                        ResearchPolicyVersion.id == policy_id
                    )
                )
                or 0
            )
            if policy_id is not None
            else 0
        )
    database_removed = not (remaining_users or remaining_projects or remaining_policies)
    require(redis_removed, "E2E cleanup left Redis session memory behind")
    require(qdrant_removed, "E2E cleanup left its Qdrant collection behind")
    require(database_removed, "E2E cleanup left PostgreSQL fixtures behind")
    return {
        "postgresql_removed": database_removed,
        "redis_removed": redis_removed,
        "qdrant_removed": qdrant_removed,
    }


async def main(base_url: str, frontend_base_url: str) -> None:
    user_ids: dict[str, UUID] = {}
    project_id: UUID | None = None
    policy_id: UUID | None = None
    collection: str | None = None
    session_id = uuid4()
    clients: list[httpx.AsyncClient] = []
    try:
        user_ids, tokens = await create_fixture_users()
        owner = httpx.AsyncClient(
            base_url=base_url,
            timeout=120,
            headers={"Authorization": f"Bearer {tokens['owner']}"},
        )
        outsider = httpx.AsyncClient(
            base_url=base_url,
            timeout=120,
            headers={"Authorization": f"Bearer {tokens['outsider']}"},
        )
        admin = httpx.AsyncClient(
            base_url=base_url,
            timeout=120,
            headers={"Authorization": f"Bearer {tokens['admin']}"},
        )
        owner_frontend = httpx.AsyncClient(
            base_url=frontend_base_url,
            timeout=120,
            cookies={"access_token": tokens["owner"]},
        )
        clients.extend((owner, outsider, admin, owner_frontend))

        for label, client in (("owner", owner), ("outsider", outsider), ("admin", admin)):
            me = await checked_object(await client.get("/api/v1/auth/me"))
            require(me["id"] == str(user_ids[label]), f"JWT identity mismatch for {label}")

        project = await checked_object(
            await owner.post(
                "/api/v1/research/projects",
                json={"title": "Phase 6 deployed E2E", "description": "Automatic fixture"},
            ),
            expected=201,
        )
        project_id = UUID(project["id"])
        collection = research_memory_collection_name(None, project_id)

        l1_body = {
            "project_id": str(project_id),
            "draft_slots": {"topic": "memory governance", "language": "en"},
            "missing_slots": ["research_questions"],
            "source_message_ids": [],
        }
        l1 = await checked_object(
            await owner_frontend.put(
                f"/api/research/sessions/{session_id}/memory", json=l1_body
            )
        )
        require(l1["user_id"] == str(user_ids["owner"]), "BFF wrote L1 under wrong user")
        require(l1["expires_in_seconds"] == 86400, "L1 TTL contract changed")
        owner_l1 = await checked_object(
            await owner.get(f"/api/v1/research/sessions/{session_id}/memory")
        )
        require(owner_l1["draft_slots"] == l1_body["draft_slots"], "Redis L1 did not round-trip")
        outsider_l1 = await outsider.get(
            f"/api/v1/research/sessions/{session_id}/memory"
        )
        await expect_status(outsider_l1, 200)
        require(outsider_l1.json() is None, "L1 memory leaked across users")
        await expect_status(
            await owner.put(
                f"/api/v1/research/sessions/{session_id}/memory",
                json={"draft_slots": {"approved": True}},
            ),
            422,
        )

        profile_1 = await checked_object(
            await owner.post(
                "/api/v1/research/me/profile",
                json={
                    "preferences": {"citation_style": "APA"},
                    "confirmation_note": "Confirmed for deployed E2E version one",
                },
            )
        )
        profile_2 = await checked_object(
            await owner.post(
                "/api/v1/research/me/profile",
                json={
                    "preferences": {"citation_style": "GB/T 7714", "language": "zh-CN"},
                    "confirmation_note": "Confirmed for deployed E2E version two",
                },
            )
        )
        require((profile_1["version"], profile_2["version"]) == (1, 2), "L3 is not versioned")
        await expect_status(
            await owner.post(
                "/api/v1/research/me/profile",
                json={
                    "preferences": {"nested": {"access_token": "must-not-persist"}},
                    "confirmation_note": "Credential rejection fixture",
                },
            ),
            422,
        )
        proxied_profile = await checked_object(
            await owner_frontend.get("/api/research/me/profile")
        )
        require(proxied_profile["version"] == 2, "BFF did not expose latest L3 profile")

        now = datetime.now(UTC)
        memory = await checked_object(
            await owner_frontend.post(
                f"/api/research/projects/{project_id}/memories",
                json={
                    "memory_type": "QUERY_TERM",
                    "content": {"preferred_terms": ["reproducible evaluation"]},
                    "source": "VERIFIED_SYSTEM_EVENT",
                    "source_id": f"phase6-e2e-{uuid4()}",
                    "confidence": 0.95,
                    "valid_from": now.isoformat(),
                },
            ),
            expected=201,
        )
        memory_id = UUID(memory["id"])
        memories = await checked_list(
            await owner.get(f"/api/v1/research/projects/{project_id}/memories")
        )
        require(str(memory_id) in {item["id"] for item in memories}, "L2 DB memory missing")
        await expect_status(
            await outsider.get(f"/api/v1/research/projects/{project_id}/memories"), 404
        )
        await expect_status(
            await owner.post(
                f"/api/v1/research/projects/{project_id}/memories",
                json={
                    "memory_type": "CORRECTION",
                    "content": {"nested": {"constraints": {"date_from": "1900-01-01"}}},
                    "source": "USER_FEEDBACK",
                    "source_id": "forbidden-hard-semantics",
                    "confidence": 1,
                    "valid_from": now.isoformat(),
                },
            ),
            422,
        )

        policy_key = f"phase6.e2e.{uuid4().hex[:12]}"
        await expect_status(
            await owner.post(
                "/api/v1/research/admin/policies",
                json={
                    "policy_key": policy_key,
                    "content": {"display_hint": "owner must not create this"},
                    "valid_from": (now - timedelta(minutes=1)).isoformat(),
                },
            ),
            403,
        )
        await expect_status(
            await admin.post(
                "/api/v1/research/admin/policies",
                json={
                    "policy_key": policy_key,
                    "content": {"nested": {"api_key": "must-not-persist"}},
                    "valid_from": (now - timedelta(minutes=1)).isoformat(),
                },
            ),
            422,
        )
        policy = await checked_object(
            await admin.post(
                "/api/v1/research/admin/policies",
                json={
                    "policy_key": policy_key,
                    "content": {"display_hint": "show provenance before synthesis"},
                    "valid_from": (now - timedelta(minutes=1)).isoformat(),
                },
            ),
            expected=201,
        )
        policy_id = UUID(policy["id"])
        require(len(policy["content_hash"]) == 64, "L4 policy has no content hash")
        policies = await checked_list(await owner.get("/api/v1/research/policies"))
        require(policy_key in {item["policy_key"] for item in policies}, "L4 policy not readable")

        approved, run = await create_validate_only_run(owner, str(project_id))

        draft_payload = {
            "project_id": str(project_id),
            "name": f"phase6-draft-{uuid4().hex[:8]}",
            "version": "1.0.0",
            "description": "Synthetic E2E fixture; not scientific ground truth.",
            "cases": [
                {
                    "case_id": "e2e-case-1",
                    "title": "Synthetic non-production evaluation fixture",
                    "doi": "10.5555/phase6.e2e.fixture",
                    "relevant": True,
                    "relevance_grade": 3,
                }
            ],
            "status": "DRAFT",
        }
        draft = await checked_object(
            await owner.post(
                f"/api/v1/research/projects/{project_id}/evaluation-datasets",
                json=draft_payload,
            ),
            expected=201,
        )
        require(len(draft["payload_hash"]) == 64, "Gold snapshot hash missing")
        await expect_status(
            await owner.post(
                f"/api/v1/research/runs/{run['id']}/evaluations/{draft['id']}"
            ),
            409,
        )

        provenance = {
            "source_name": "Phase 6 synthetic deployment fixture",
            "source_url": "https://example.invalid/academic-research-agent/phase6-e2e",
            "license": "CC0-1.0",
            "annotator_count": 1,
            "judgment_method": "Synthetic fixture used only for control-flow validation.",
            "completed_at": now.isoformat(),
            "domain_coverage": ["deployment-testing"],
            "language_coverage": ["en"],
            "limitations": ["Not a scientific performance benchmark"],
        }
        adjudicated_payload = {
            **draft_payload,
            "name": f"phase6-adjudicated-{uuid4().hex[:8]}",
            "status": "ADJUDICATED",
            "provenance": provenance,
        }
        await expect_status(
            await owner.post(
                f"/api/v1/research/projects/{project_id}/evaluation-datasets",
                json=adjudicated_payload,
            ),
            422,
        )
        adjudicated_payload["provenance"] = {**provenance, "annotator_count": 2}
        adjudicated = await checked_object(
            await owner.post(
                f"/api/v1/research/projects/{project_id}/evaluation-datasets",
                json=adjudicated_payload,
            ),
            expected=201,
        )
        report = await checked_object(
            await owner.post(
                f"/api/v1/research/runs/{run['id']}/evaluations/{adjudicated['id']}"
            ),
            expected=201,
        )
        require(report["dataset_hash"] == adjudicated["payload_hash"], "Evaluation lost hash")
        require(
            any(metric["status"] == "NOT_EVALUATED" for metric in report["metrics"].values()),
            "Undersized synthetic fixture was incorrectly presented as measured performance",
        )
        listed_reports = await checked_list(
            await owner.get(f"/api/v1/research/runs/{run['id']}/evaluations")
        )
        require(report["id"] in {item["id"] for item in listed_reports}, "Report not persisted")
        await expect_status(
            await outsider.get(
                f"/api/v1/research/projects/{project_id}/evaluation-datasets"
            ),
            404,
        )

        qdrant = AsyncQdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
            api_key=settings.QDRANT_API_KEY or None,
        )
        try:
            indexed_payload = await wait_for_memory_index(
                qdrant, collection=collection, memory_id=str(memory_id)
            )
        finally:
            await qdrant.close()
        require(indexed_payload.get("project_id") == str(project_id), "Qdrant project scope lost")
        require(indexed_payload.get("memory_id") == str(memory_id), "Qdrant memory identity lost")

        memory_read = await verify_production_memory_read(
            project_id=project_id,
            owner_id=user_ids["owner"],
            memory_id=memory_id,
            policy_key=policy_key,
            protocol_hash=approved["protocol_hash"],
        )
        print(
            "phase6_deployed_e2e_ok",
            {
                "zero_llm_execution_mode": run["execution_mode"],
                "run_state": run["state"],
                "frontend_bff": True,
                "redis_l1_ttl_seconds": l1["expires_in_seconds"],
                "postgres_l2_isolated": True,
                "qdrant_worker_indexed": True,
                "memory_read": memory_read,
                "draft_evaluation_fail_closed": True,
                "undersized_metrics_not_evaluated": True,
                "admin_policy_enforced": True,
            },
        )
    finally:
        if clients:
            await asyncio.gather(*(client.aclose() for client in clients))
        cleanup_evidence = await cleanup(
            user_ids=user_ids,
            project_id=project_id,
            policy_id=policy_id,
            session_id=session_id,
            collection=collection,
        )
        print(
            "phase6_deployed_e2e_cleanup_ok",
            {"fixtures_removed": True, **cleanup_evidence},
        )


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000",
            sys.argv[2] if len(sys.argv) > 2 else "http://frontend:3000",
        )
    )
