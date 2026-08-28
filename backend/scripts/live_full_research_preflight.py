"""Run a small, real full-research job against the deployed project stack."""

from __future__ import annotations

import asyncio
import json
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


async def checked(response: httpx.Response) -> dict | list:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    return response.json()


async def auth_request(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: object
) -> dict | list:
    """Respect the deployed auth limiter while retaining one-time credentials."""
    for attempt in range(3):
        response = await client.request(method, path, **kwargs)
        if response.status_code != 429:
            return await checked(response)
        payload = response.json()
        details = payload.get("detail", {}).get("error", {}).get("details", {})
        retry_after = max(1, int(details.get("retry_after_seconds", 1)))
        print(
            json.dumps(
                {
                    "event": "auth_rate_limited",
                    "path": path,
                    "retry_after_seconds": retry_after,
                    "attempt": attempt + 1,
                }
            ),
            flush=True,
        )
        await asyncio.sleep(retry_after + 1)
    raise RuntimeError(f"Authentication rate limit did not clear for {path}")


async def main(base_url: str) -> None:
    suffix = uuid4().hex[:12]
    email = f"full-research-preflight-{suffix}@example.com"
    password = f"E2E-{secrets.token_urlsafe(18)}"
    async with httpx.AsyncClient(base_url=base_url, timeout=90) as client:
        user = await auth_request(
            client,
            "POST",
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": "Full Research E2E"},
        )
        token = await auth_request(
            client,
            "POST",
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        assert isinstance(user, dict) and isinstance(token, dict)
        client.headers["Authorization"] = f"Bearer {token['access_token']}"

        project = await checked(
            await client.post(
                "/api/v1/research/projects",
                json={
                    "title": "Live two-paper full-research preflight",
                    "description": "Disposable real-provider deployment acceptance run",
                },
            )
        )
        assert isinstance(project, dict)
        protocol = await checked(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols:compile",
                json={
                    "topic": "scientific literature review",
                    "topic_definition": (
                        "Systems that search, analyze, or synthesize scholarly literature "
                        "with traceable evidence."
                    ),
                    "as_of_date": datetime.now(UTC).date().isoformat(),
                    "rolling_months": 60,
                    "allowed_types": ["journal_article", "conference_paper", "preprint"],
                    "allowed_languages": ["en"],
                    # The quota-bounded production path performs one Crossref
                    # keyword search and enriches those DOI seeds via OpenAlex.
                    "required_sources": ["crossref", "openalex"],
                    "optional_sources": [],
                    "minimum_source_families": 2,
                    "publisher_verification_required": False,
                    "target_count": 2,
                    "shortfall_action": "return_strict_only",
                },
            )
        )
        assert isinstance(protocol, dict)
        approved = await checked(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols/"
                f"{protocol['version']}:approve",
                json={"protocol_hash": protocol["protocol_hash"]},
            )
        )
        assert isinstance(approved, dict)
        run = await checked(
            await client.post(
                "/api/v1/research/runs",
                json={
                    "project_id": project["id"],
                    "protocol_version": approved["version"],
                    "execution_mode": "full_research",
                    "client_request_id": f"live-full-{suffix}",
                },
            )
        )
        assert isinstance(run, dict)
        print(
            json.dumps(
                {
                    "event": "created",
                    "user_id": user["id"],
                    "project_id": project["id"],
                    "run_id": run["id"],
                    "state": run["state"],
                    "target_count": run["target_count"],
                }
            ),
            flush=True,
        )

        deadline = asyncio.get_running_loop().time() + 1800
        last_state = None
        accepted_shortfall = False
        while asyncio.get_running_loop().time() < deadline:
            run = await checked(await client.get(f"/api/v1/research/runs/{run['id']}"))
            assert isinstance(run, dict)
            if run["state"] != last_state:
                print(
                    json.dumps(
                        {
                            "event": "state",
                            "state": run["state"],
                            "state_version": run["state_version"],
                            "candidates": run["candidate_count"],
                            "strict": run["strict_count"],
                            "analyzed": run["analyzed_count"],
                        }
                    ),
                    flush=True,
                )
                last_state = run["state"]
            if run["state"] == "AWAITING_RELAXATION_AUTHORIZATION":
                if run["strict_count"] < 1:
                    raise RuntimeError("No strict paper is available for the preflight")
                if not accepted_shortfall:
                    run = await checked(
                        await client.post(
                            f"/api/v1/research/runs/{run['id']}/shortage-actions",
                            json={"action": "accept_strict_shortfall"},
                        )
                    )
                    assert isinstance(run, dict)
                    accepted_shortfall = True
                    print(json.dumps({"event": "accepted_strict_shortfall"}), flush=True)
            if run["state"] in TERMINAL_STATES:
                break
            await asyncio.sleep(3)
        else:
            raise TimeoutError("Full-research preflight did not finish within 30 minutes")

        candidates = await checked(
            await client.get(f"/api/v1/research/runs/{run['id']}/candidates?limit=200")
        )
        evidence = await checked(await client.get(f"/api/v1/research/runs/{run['id']}/evidence"))
        artifacts = await checked(await client.get(f"/api/v1/research/runs/{run['id']}/artifacts"))
        assert isinstance(candidates, dict)
        assert isinstance(evidence, list) and isinstance(artifacts, list)
        summary = {
            "event": "terminal",
            "run_id": run["id"],
            "state": run["state"],
            "failed_code": run["failed_code"],
            "candidate_count": run["candidate_count"],
            "strict_count": run["strict_count"],
            "analyzed_count": run["analyzed_count"],
            "candidate_api_total": candidates["total"],
            "evidence_count": len(evidence),
            "artifact_count": len(artifacts),
            "artifact_formats": sorted({item["format"] for item in artifacts}),
        }
        print(json.dumps(summary), flush=True)
        if run["state"] not in {"COMPLETED", "PARTIALLY_COMPLETED"}:
            raise RuntimeError(f"Full-research preflight failed in {run['state']}")
        if run["analyzed_count"] < 1 or not evidence or len(artifacts) < 6:
            raise RuntimeError("Terminal run is missing analyses, evidence, or release artifacts")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
