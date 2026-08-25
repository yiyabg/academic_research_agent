"""Concurrent membership-race and authorization latency checks on a deployed stack."""

import asyncio
import math
import secrets
import sys
from time import perf_counter
from uuid import uuid4

import httpx

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db_context
from app.schemas.user import UserCreate
from app.services.user import UserService


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile_value * len(ordered)) - 1)]


async def create_user(label: str) -> dict[str, str]:
    async with get_db_context() as db:
        user = await UserService(db).register(
            UserCreate(
                email=f"research-org-load-{label}-{uuid4().hex[:12]}@example.com",
                password=f"Concurrency-{secrets.token_urlsafe(18)}",
                full_name=f"Organization load {label}",
            )
        )
        return {
            "id": str(user.id),
            "email": user.email,
            "token": create_access_token(subject=str(user.id)),
        }


def client(base_url: str, user: dict[str, str], *, cookie: bool = False) -> httpx.AsyncClient:
    kwargs: dict[str, object] = {
        "base_url": base_url,
        "timeout": 60,
        "limits": httpx.Limits(max_connections=64, max_keepalive_connections=32),
    }
    if cookie:
        kwargs["cookies"] = {"access_token": user["token"]}
    instance = httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
    if not cookie:
        instance.headers["Authorization"] = f"Bearer {user['token']}"
    return instance


async def timed_get(
    instance: httpx.AsyncClient, path: str, *, headers: dict[str, str] | None = None
) -> tuple[int, float]:
    started = perf_counter()
    response = await instance.get(path, headers=headers)
    return response.status_code, (perf_counter() - started) * 1000


async def main(backend_url: str, frontend_url: str) -> None:
    owner, member, outsider = await asyncio.gather(
        create_user("owner"), create_user("member"), create_user("outsider")
    )
    async with (
        client(backend_url, owner) as owner_client,
        client(backend_url, member) as member_client,
        client(backend_url, outsider) as outsider_client,
        client(frontend_url, owner, cookie=True) as owner_frontend,
        client(frontend_url, outsider, cookie=True) as outsider_frontend,
    ):
        organization_response = await owner_client.post(
            "/api/v1/research/organizations",
            json={
                "name": "Organization concurrency fixture",
                "slug": f"organization-load-{uuid4().hex[:10]}",
            },
        )
        organization_response.raise_for_status()
        organization = organization_response.json()
        organization_id = str(organization["id"])
        headers = {"X-Research-Organization-ID": organization_id}
        member_path = f"/api/v1/research/organizations/{organization_id}/members"

        invite_responses = await asyncio.gather(
            *(owner_client.post(member_path, json={"email": member["email"]}) for _ in range(16))
        )
        invite_statuses = [response.status_code for response in invite_responses]
        if invite_statuses.count(201) != 1 or invite_statuses.count(409) != 15:
            raise RuntimeError(f"Concurrent invite was not deterministic: {invite_statuses}")

        project_response = await member_client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={
                "title": "Concurrent organization authorization",
                "description": "P95 and revocation fixture",
                "organization_id": organization_id,
            },
        )
        project_response.raise_for_status()
        project_id = str(project_response.json()["id"])
        project_path = f"/api/v1/research/projects/{project_id}"

        owner_reads, outsider_reads, owner_proxy_reads, outsider_proxy_reads = await asyncio.gather(
            asyncio.gather(*(timed_get(owner_client, project_path) for _ in range(100))),
            asyncio.gather(*(timed_get(outsider_client, project_path) for _ in range(100))),
            asyncio.gather(
                *(
                    timed_get(
                        owner_frontend,
                        "/api/research/projects",
                        headers=headers,
                    )
                    for _ in range(50)
                )
            ),
            asyncio.gather(
                *(
                    timed_get(
                        outsider_frontend,
                        "/api/research/projects",
                        headers=headers,
                    )
                    for _ in range(50)
                )
            ),
        )
        expected_statuses = (
            (owner_reads, 200),
            (outsider_reads, 404),
            (owner_proxy_reads, 200),
            (outsider_proxy_reads, 404),
        )
        for rows, expected in expected_statuses:
            observed = {status for status, _latency in rows}
            if observed != {expected}:
                raise RuntimeError(
                    f"Concurrent authorization changed status: expected={expected}, got={observed}"
                )

        delete_path = f"{member_path}/{member['id']}"
        revoke_responses = await asyncio.gather(
            *(owner_client.delete(delete_path) for _ in range(16))
        )
        revoke_statuses = [response.status_code for response in revoke_responses]
        if revoke_statuses.count(204) != 1 or revoke_statuses.count(404) != 15:
            raise RuntimeError(f"Concurrent revoke was not deterministic: {revoke_statuses}")

        revoked_reads = await asyncio.gather(
            *(timed_get(member_client, project_path) for _ in range(100))
        )
        if {status for status, _latency in revoked_reads} != {404}:
            raise RuntimeError("A revoked member retained access under concurrent reads")

        groups = {
            "backend_authorized": owner_reads,
            "backend_denied": outsider_reads,
            "frontend_authorized": owner_proxy_reads,
            "frontend_denied": outsider_proxy_reads,
            "post_revoke_denied": revoked_reads,
        }
        latency = {
            name: {
                "samples": len(rows),
                "p50_ms": round(percentile([item[1] for item in rows], 0.50), 2),
                "p95_ms": round(percentile([item[1] for item in rows], 0.95), 2),
                "max_ms": round(max(item[1] for item in rows), 2),
            }
            for name, rows in groups.items()
        }
        print(
            "organization_concurrency_ok",
            {
                "invite_status_counts": {201: 1, 409: 15},
                "revoke_status_counts": {204: 1, 404: 15},
                "post_revoke_status": 404,
                "latency": latency,
                "limitations": [
                    "single-host localhost baseline",
                    (
                        "uvicorn multi-worker production topology; process count verified "
                        "separately"
                        if settings.ENVIRONMENT == "production"
                        else "uvicorn development topology"
                    ),
                    "authorization endpoints only; not a 20-paper cost benchmark",
                ],
            },
        )


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000",
            sys.argv[2] if len(sys.argv) > 2 else "http://frontend:3000",
        )
    )
