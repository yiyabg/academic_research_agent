"""Exercise the deployed research API and Celery pipeline with live scholarly data."""

import asyncio
import secrets
import sys
from datetime import UTC, datetime
from uuid import uuid4

import httpx

TERMINAL_STATES = {
    "COMPLETED",
    "PARTIALLY_COMPLETED",
    "FAILED_RETRYABLE",
    "FAILED_TERMINAL",
    "CANCELLED",
}


async def checked(response: httpx.Response) -> dict:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {response.request.url.path}")
    return payload


async def main(base_url: str) -> None:
    suffix = uuid4().hex[:12]
    email = f"research-e2e-{suffix}@example.com"
    password = f"E2E-{secrets.token_urlsafe(18)}"
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        user = await checked(
            await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": password, "full_name": "Research E2E"},
            )
        )
        token = await checked(
            await client.post(
                "/api/v1/auth/login",
                data={"username": email, "password": password},
            )
        )
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        project = await checked(
            await client.post(
                "/api/v1/research/projects",
                json={
                    "title": "Live auditable research-agent E2E",
                    "description": "Disposable deployment verification project",
                },
            )
        )
        protocol = await checked(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols:compile",
                json={
                    "topic": "auditable research agents",
                    "topic_definition": (
                        "Software agents that retrieve scholarly literature with "
                        "auditable provenance and evidence."
                    ),
                    "as_of_date": datetime.now(UTC).date().isoformat(),
                    "rolling_months": 24,
                    "allowed_types": ["journal_article", "conference_paper", "preprint"],
                    "allowed_languages": ["en"],
                    "required_sources": ["crossref", "openalex", "arxiv"],
                    "optional_sources": [],
                    "minimum_source_families": 3,
                    "publisher_verification_required": False,
                    "target_count": 3,
                    "shortfall_action": "return_strict_only",
                },
            )
        )
        approved = await checked(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols/"
                f"{protocol['version']}:approve",
                json={"protocol_hash": protocol["protocol_hash"]},
            )
        )
        run = await checked(
            await client.post(
                "/api/v1/research/runs",
                json={
                    "project_id": project["id"],
                    "protocol_version": approved["version"],
                    "execution_mode": "search_only",
                    "client_request_id": f"live-search-{suffix}",
                },
            )
        )
        print(
            f"created user={user['id']} project={project['id']} run={run['id']} "
            f"state={run['state']}"
        )
        deadline = asyncio.get_running_loop().time() + 900
        last_state = None
        while asyncio.get_running_loop().time() < deadline:
            run = await checked(await client.get(f"/api/v1/research/runs/{run['id']}"))
            if run["state"] != last_state:
                print(
                    f"state={run['state']} version={run['state_version']} "
                    f"candidates={run['candidate_count']} strict={run['strict_count']}"
                )
                last_state = run["state"]
            if run["state"] in TERMINAL_STATES:
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("Research run did not reach a terminal state within 15 minutes")

        print(
            "terminal",
            {
                "state": run["state"],
                "state_version": run["state_version"],
                "candidate_count": run["candidate_count"],
                "strict_count": run["strict_count"],
                "failed_code": run["failed_code"],
                "progress": run["progress"],
            },
        )
        if run["state"] not in {"COMPLETED", "PARTIALLY_COMPLETED"}:
            raise RuntimeError(f"Live search pipeline failed in {run['state']}")

        blocked = await client.post(
            "/api/v1/research/runs",
            json={
                "project_id": project["id"],
                "protocol_version": approved["version"],
                "execution_mode": "full_research",
                "client_request_id": f"live-full-{suffix}",
            },
        )
        print(f"full_research_with_unavailable_llm_http={blocked.status_code}")
        if blocked.status_code != 503:
            raise RuntimeError("Full research was not rejected while the LLM was unavailable")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:58000"))
