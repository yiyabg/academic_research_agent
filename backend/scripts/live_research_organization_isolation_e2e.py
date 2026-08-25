"""Deployed multi-user/multi-organization authorization matrix for research APIs."""

import asyncio
import secrets
import sys
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from app.core.security import create_access_token
from app.db.session import get_db_context
from app.schemas.user import UserCreate
from app.services.user import UserService


async def checked_object(response: httpx.Response) -> dict:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object")
    return payload


async def checked_list(response: httpx.Response) -> list[dict]:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Expected a JSON array")
    return payload


async def create_clients(base_url: str) -> dict[str, tuple[httpx.AsyncClient, dict]]:
    result: dict[str, tuple[httpx.AsyncClient, dict]] = {}
    async with get_db_context() as db:
        users = UserService(db)
        for label in ("owner-a", "member-a", "owner-b", "outsider"):
            suffix = uuid4().hex[:12]
            email = f"research-org-e2e-{label}-{suffix}@example.com"
            user = await users.register(
                UserCreate(
                    email=email,
                    password=f"Organization-{secrets.token_urlsafe(18)}",
                    full_name=f"Organization E2E {label}",
                )
            )
            client = httpx.AsyncClient(base_url=base_url, timeout=60)
            client.headers["Authorization"] = (
                f"Bearer {create_access_token(subject=str(user.id))}"
            )
            result[label] = (client, {"id": str(user.id), "email": user.email})
    for label, (client, expected_user) in result.items():
        observed_user = await checked_object(await client.get("/api/v1/auth/me"))
        if observed_user["id"] != expected_user["id"]:
            raise RuntimeError(
                f"JWT identity mismatch for {label}: "
                f"expected={expected_user['id']} observed={observed_user['id']}"
            )
    return result


async def make_validate_only_run(
    client: httpx.AsyncClient, project_id: str, organization_id: str
) -> dict:
    headers = {"X-Research-Organization-ID": organization_id}
    protocol = await checked_object(
        await client.post(
            f"/api/v1/research/projects/{project_id}/protocols:compile",
            headers=headers,
            json={
                "topic": "organization isolated evidence synthesis",
                "topic_definition": "Verify durable collaboration without cross-tenant leakage.",
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
            },
        )
    )
    approved = await checked_object(
        await client.post(
            f"/api/v1/research/projects/{project_id}/protocols/"
            f"{protocol['version']}:approve",
            headers=headers,
            json={"protocol_hash": protocol["protocol_hash"]},
        )
    )
    run = await checked_object(
        await client.post(
            "/api/v1/research/runs",
            headers=headers,
            json={
                "project_id": project_id,
                "protocol_version": approved["version"],
                "execution_mode": "validate_only",
                "client_request_id": f"organization-e2e-{uuid4()}",
            },
        )
    )
    deadline = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < deadline:
        run = await checked_object(
            await client.get(f"/api/v1/research/runs/{run['id']}", headers=headers)
        )
        if run["state"] == "COMPLETED":
            return run
        await asyncio.sleep(0.5)
    raise TimeoutError("Organization validate-only run did not complete")


async def main(base_url: str, frontend_base_url: str) -> None:
    clients = await create_clients(base_url)
    owner_a, owner_a_user = clients["owner-a"]
    member_a, member_a_user = clients["member-a"]
    owner_b, _owner_b_user = clients["owner-b"]
    outsider, outsider_user = clients["outsider"]
    try:
        organization_a = await checked_object(
            await owner_a.post(
                "/api/v1/research/organizations",
                json={"name": "Organization E2E A", "slug": f"org-e2e-a-{uuid4().hex[:8]}"},
            )
        )
        organization_b = await checked_object(
            await owner_b.post(
                "/api/v1/research/organizations",
                json={"name": "Organization E2E B", "slug": f"org-e2e-b-{uuid4().hex[:8]}"},
            )
        )
        org_a_headers = {"X-Research-Organization-ID": organization_a["id"]}
        org_b_headers = {"X-Research-Organization-ID": organization_b["id"]}

        added = await checked_object(
            await owner_a.post(
                f"/api/v1/research/organizations/{organization_a['id']}/members",
                json={"email": member_a_user["email"]},
            )
        )
        if added["role"] != "MEMBER":
            raise RuntimeError("Invited research member did not receive MEMBER role")

        member_manage = await member_a.post(
            f"/api/v1/research/organizations/{organization_a['id']}/members",
            json={"email": outsider_user["email"]},
        )
        remove_owner = await owner_a.delete(
            f"/api/v1/research/organizations/{organization_a['id']}/members/"
            f"{owner_a_user['id']}"
        )
        if member_manage.status_code != 403 or remove_owner.status_code != 403:
            raise RuntimeError(
                "Organization role enforcement failed: "
                f"member_add={member_manage.status_code}, owner_remove={remove_owner.status_code}"
            )

        personal_project = await checked_object(
            await owner_a.post(
                "/api/v1/research/projects",
                json={"title": "Owner A personal project", "description": "Must stay personal"},
            )
        )
        project_a = await checked_object(
            await member_a.post(
                "/api/v1/research/projects",
                headers=org_a_headers,
                json={
                    "title": "Organization A shared project",
                    "description": "Created by a member and owned by the organization scope",
                    "organization_id": organization_a["id"],
                },
            )
        )
        project_b = await checked_object(
            await owner_b.post(
                "/api/v1/research/projects",
                headers=org_b_headers,
                json={
                    "title": "Organization B project",
                    "description": "Must not leak to organization A",
                },
            )
        )
        if project_a.get("organization_id") != organization_a["id"]:
            raise RuntimeError(f"Organization A project was not scoped: {project_a}")
        if project_b.get("organization_id") != organization_b["id"]:
            raise RuntimeError(f"Organization B project was not scoped: {project_b}")
        run_a = await make_validate_only_run(
            member_a, project_a["id"], organization_a["id"]
        )

        owner_a_project = await owner_a.get(
            f"/api/v1/research/projects/{project_a['id']}"
        )
        owner_a_run = await owner_a.get(f"/api/v1/research/runs/{run_a['id']}")
        member_personal = await member_a.get(
            f"/api/v1/research/projects/{personal_project['id']}"
        )
        cross_org_probes = {
            "owner_b_to_project_a": await owner_b.get(
                f"/api/v1/research/projects/{project_a['id']}"
            ),
            "owner_b_to_run_a": await owner_b.get(
                f"/api/v1/research/runs/{run_a['id']}"
            ),
            "member_a_to_project_b": await member_a.get(
                f"/api/v1/research/projects/{project_b['id']}"
            ),
            "outsider_to_project_a": await outsider.get(
                f"/api/v1/research/projects/{project_a['id']}"
            ),
            "outsider_to_events_a": await outsider.get(
                f"/api/v1/research/runs/{run_a['id']}/events"
            ),
            "outsider_active_org_list": await outsider.get(
                "/api/v1/research/projects", headers=org_a_headers
            ),
        }
        cross_statuses = {
            name: response.status_code for name, response in cross_org_probes.items()
        }
        if owner_a_project.status_code != 200 or owner_a_run.status_code != 200:
            raise RuntimeError("Organization owner could not access member-created resources")
        if member_personal.status_code != 404:
            raise RuntimeError("Personal project leaked to a member of the same organization")
        if any(status != 404 for status in cross_statuses.values()):
            raise RuntimeError(f"Cross-organization resource leaked: {cross_statuses}")

        member_orgs = await checked_list(
            await member_a.get("/api/v1/research/organizations")
        )
        outsider_orgs = await checked_list(
            await outsider.get("/api/v1/research/organizations")
        )
        if organization_a["id"] not in {item["id"] for item in member_orgs}:
            raise RuntimeError("Organization membership was not discoverable by its member")
        if organization_a["id"] in {item["id"] for item in outsider_orgs}:
            raise RuntimeError("Organization membership list leaked to outsider")

        removed = await owner_a.delete(
            f"/api/v1/research/organizations/{organization_a['id']}/members/"
            f"{member_a_user['id']}"
        )
        if removed.status_code != 204:
            raise RuntimeError(f"Owner could not revoke membership: {removed.status_code}")
        revoked_statuses = {
            "project": (
                await member_a.get(f"/api/v1/research/projects/{project_a['id']}")
            ).status_code,
            "run": (
                await member_a.get(f"/api/v1/research/runs/{run_a['id']}")
            ).status_code,
            "active_org_list": (
                await member_a.get("/api/v1/research/projects", headers=org_a_headers)
            ).status_code,
        }
        if any(status != 404 for status in revoked_statuses.values()):
            raise RuntimeError(f"Revoked member retained access: {revoked_statuses}")
        if (await owner_a.get(f"/api/v1/research/runs/{run_a['id']}")).status_code != 200:
            raise RuntimeError("Membership revocation damaged owner access")

        owner_token = owner_a.headers["Authorization"].removeprefix("Bearer ")
        outsider_token = outsider.headers["Authorization"].removeprefix("Bearer ")
        async with (
            httpx.AsyncClient(
                base_url=frontend_base_url,
                timeout=60,
                cookies={"access_token": owner_token},
            ) as owner_frontend,
            httpx.AsyncClient(
                base_url=frontend_base_url,
                timeout=60,
                cookies={"access_token": outsider_token},
            ) as outsider_frontend,
        ):
            proxied_projects = await checked_list(
                await owner_frontend.get("/api/research/projects", headers=org_a_headers)
            )
            proxied_outsider = await outsider_frontend.get(
                "/api/research/projects", headers=org_a_headers
            )
        if project_a["id"] not in {item["id"] for item in proxied_projects}:
            raise RuntimeError("Next.js proxy dropped organization context for its owner")
        if proxied_outsider.status_code != 404:
            raise RuntimeError(
                "Next.js proxy bypassed organization isolation: "
                f"{proxied_outsider.status_code}"
            )

        print(
            "organization_isolation_e2e_ok",
            {
                "shared_project_creator": member_a_user["id"],
                "shared_access_before_revocation": True,
                "personal_project_member_status": member_personal.status_code,
                "cross_org_probe_statuses": cross_statuses,
                "revoked_member_statuses": revoked_statuses,
                "run_state": run_a["state"],
                "role_management": {
                    "member_add": member_manage.status_code,
                    "owner_remove": remove_owner.status_code,
                },
                "frontend_proxy": {
                    "owner_list": 200,
                    "outsider_list": proxied_outsider.status_code,
                },
            },
        )
    finally:
        await asyncio.gather(*(client.aclose() for client, _ in clients.values()))


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000",
            sys.argv[2] if len(sys.argv) > 2 else "http://frontend:3000",
        )
    )
