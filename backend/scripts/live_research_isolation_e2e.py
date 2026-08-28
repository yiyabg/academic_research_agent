"""Verify deployed cross-user isolation for literature-research resources."""

import asyncio
import secrets
import sys
from datetime import UTC, datetime
from uuid import uuid4

import httpx


async def checked(response: httpx.Response) -> dict:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Expected JSON object")
    return payload


async def register_client(base_url: str, label: str) -> httpx.AsyncClient:
    suffix = uuid4().hex[:12]
    email = f"research-isolation-{label}-{suffix}@example.com"
    password = f"Isolation-{secrets.token_urlsafe(18)}"
    client = httpx.AsyncClient(base_url=base_url, timeout=60)
    await checked(
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": f"Isolation {label}"},
        )
    )
    token = await checked(
        await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    )
    client.headers["Authorization"] = f"Bearer {token['access_token']}"
    return client


async def main(base_url: str) -> None:
    owner = await register_client(base_url, "owner")
    outsider = await register_client(base_url, "outsider")
    try:
        project = await checked(
            await owner.post(
                "/api/v1/research/projects",
                json={"title": "Isolation E2E", "description": "Owner-only fixture"},
            )
        )
        protocol = await checked(
            await owner.post(
                f"/api/v1/research/projects/{project['id']}/protocols:compile",
                json={
                    "topic": "tenant isolated research",
                    "topic_definition": "Research data must remain visible only to its owner.",
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
        approved = await checked(
            await owner.post(
                f"/api/v1/research/projects/{project['id']}/protocols/"
                f"{protocol['version']}:approve",
                json={"protocol_hash": protocol["protocol_hash"]},
            )
        )
        run = await checked(
            await owner.post(
                "/api/v1/research/runs",
                json={
                    "project_id": project["id"],
                    "protocol_version": approved["version"],
                    "execution_mode": "validate_only",
                    "client_request_id": f"isolation-{uuid4()}",
                },
            )
        )
        deadline = asyncio.get_running_loop().time() + 60
        while asyncio.get_running_loop().time() < deadline:
            run = await checked(await owner.get(f"/api/v1/research/runs/{run['id']}"))
            if run["state"] == "COMPLETED":
                break
            await asyncio.sleep(0.5)
        else:
            raise TimeoutError("Owner fixture did not complete")

        outsider_projects = await outsider.get("/api/v1/research/projects")
        outsider_projects.raise_for_status()
        if any(item["id"] == project["id"] for item in outsider_projects.json()):
            raise RuntimeError("Owner project leaked into outsider project list")

        probes = {
            "run": await outsider.get(f"/api/v1/research/runs/{run['id']}"),
            "events": await outsider.get(f"/api/v1/research/runs/{run['id']}/events"),
            "candidates": await outsider.get(f"/api/v1/research/runs/{run['id']}/candidates"),
            "artifacts": await outsider.get(f"/api/v1/research/runs/{run['id']}/artifacts"),
            "cancel": await outsider.post(f"/api/v1/research/runs/{run['id']}:cancel"),
            "pause": await outsider.post(f"/api/v1/research/runs/{run['id']}:pause"),
        }
        statuses = {name: response.status_code for name, response in probes.items()}
        if any(status != 404 for status in statuses.values()):
            raise RuntimeError(f"Cross-user resource existence leaked: {statuses}")
        owner_run = await checked(await owner.get(f"/api/v1/research/runs/{run['id']}"))
        if owner_run["state"] != "COMPLETED":
            raise RuntimeError("Outsider control request changed the owner run")
        print(
            "isolation_e2e_ok",
            {
                "project_hidden": True,
                "resource_probe_statuses": statuses,
                "owner_state_after_probes": owner_run["state"],
            },
        )
    finally:
        await owner.aclose()
        await outsider.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:58000"))
