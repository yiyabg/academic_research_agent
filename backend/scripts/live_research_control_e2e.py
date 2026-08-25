"""Verify deployed pause/resume/cancel and persisted event replay semantics."""

import asyncio
import secrets
import sys
from datetime import UTC, datetime
from uuid import uuid4

import httpx

TERMINAL = {"COMPLETED", "PARTIALLY_COMPLETED", "FAILED_TERMINAL", "CANCELLED"}


async def json_object(response: httpx.Response) -> dict:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object")
    return payload


async def wait_for_state(
    client: httpx.AsyncClient,
    run_id: str,
    wanted: set[str],
    *,
    timeout: float,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    previous = None
    while asyncio.get_running_loop().time() < deadline:
        run = await json_object(await client.get(f"/api/v1/research/runs/{run_id}"))
        if run["state"] != previous:
            print(f"run={run_id[:8]} state={run['state']} version={run['state_version']}")
            previous = run["state"]
        if run["state"] in wanted:
            return run
        await asyncio.sleep(1)
    raise TimeoutError(f"Run {run_id} did not reach {sorted(wanted)}")


async def main(base_url: str) -> None:
    suffix = uuid4().hex[:12]
    email = f"research-control-{suffix}@example.com"
    password = f"Control-{secrets.token_urlsafe(18)}"
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        await json_object(
            await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": password, "full_name": "Control E2E"},
            )
        )
        token = await json_object(
            await client.post("/api/v1/auth/login", data={"username": email, "password": password})
        )
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        project = await json_object(
            await client.post(
                "/api/v1/research/projects",
                json={"title": "Control-plane E2E", "description": "Disposable"},
            )
        )
        protocol = await json_object(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols:compile",
                json={
                    "topic": "auditable scholarly agents",
                    "topic_definition": "Agents with reproducible scholarly provenance.",
                    "as_of_date": datetime.now(UTC).date().isoformat(),
                    "rolling_months": 12,
                    "allowed_types": ["journal_article", "conference_paper", "preprint"],
                    "allowed_languages": ["en"],
                    "required_sources": ["crossref", "openalex", "arxiv"],
                    "optional_sources": [],
                    "minimum_source_families": 3,
                    "publisher_verification_required": False,
                    "target_count": 2,
                    "shortfall_action": "return_strict_only",
                },
            )
        )
        approved = await json_object(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols/"
                f"{protocol['version']}:approve",
                json={"protocol_hash": protocol["protocol_hash"]},
            )
        )

        async def create_run(label: str) -> dict:
            return await json_object(
                await client.post(
                    "/api/v1/research/runs",
                    json={
                        "project_id": project["id"],
                        "protocol_version": approved["version"],
                        "execution_mode": "search_only",
                        "client_request_id": f"{label}-{suffix}",
                    },
                )
            )

        paused_run = await create_run("pause")
        active = await wait_for_state(
            client,
            paused_run["id"],
            {
                "DISCOVERING",
                "NORMALIZING",
                "ENRICHING_METRICS",
                "DEDUPLICATING",
                "HARD_FILTERING",
                "RELEVANCE_SCORING",
            },
            timeout=60,
        )
        pause_started = asyncio.get_running_loop().time()
        pause_response = await client.post(f"/api/v1/research/runs/{paused_run['id']}:pause")
        pause_latency = asyncio.get_running_loop().time() - pause_started
        if pause_response.status_code != 202:
            raise RuntimeError(f"Pause request failed: {pause_response.text}")
        if pause_latency >= 5:
            raise RuntimeError(
                f"Pause request blocked behind {active['state']} for {pause_latency:.2f}s"
            )
        paused = await wait_for_state(client, paused_run["id"], {"PAUSED"}, timeout=180)
        paused_from = paused["progress"].get("paused_from")
        if not paused_from:
            raise RuntimeError("Paused run did not persist its exact checkpoint")

        events_before = await client.get(f"/api/v1/research/runs/{paused_run['id']}/events")
        events_before.raise_for_status()
        first_events = events_before.json()
        last_sequence = max(item["sequence"] for item in first_events)

        resume_response = await client.post(f"/api/v1/research/runs/{paused_run['id']}:resume")
        if resume_response.status_code != 202:
            raise RuntimeError(f"Resume request failed: {resume_response.text}")
        completed = await wait_for_state(
            client, paused_run["id"], {"COMPLETED", "PARTIALLY_COMPLETED"}, timeout=900
        )
        replay = await client.get(
            f"/api/v1/research/runs/{paused_run['id']}/events",
            params={"after_sequence": last_sequence},
        )
        replay.raise_for_status()
        replayed = replay.json()
        if not replayed or any(item["sequence"] <= last_sequence for item in replayed):
            raise RuntimeError("after_sequence event replay did not return the missing suffix")

        rejected_regeneration = await client.post(
            f"/api/v1/research/runs/{paused_run['id']}/artifacts:regenerate",
            json={"client_request_id": f"regen-{suffix}"},
        )
        if rejected_regeneration.status_code != 409:
            raise RuntimeError("Search-only artifact regeneration was not rejected")

        cancelled_run = await create_run("cancel")
        cancel_response = await client.post(f"/api/v1/research/runs/{cancelled_run['id']}:cancel")
        if cancel_response.status_code != 202:
            raise RuntimeError(f"Cancel request failed: {cancel_response.text}")
        cancelled = await wait_for_state(client, cancelled_run["id"], {"CANCELLED"}, timeout=180)
        print(
            "control_e2e_ok",
            {
                "paused_from": paused_from,
                "pause_request_latency_seconds": round(pause_latency, 3),
                "completed_state": completed["state"],
                "replayed_event_count": len(replayed),
                "cancelled_state": cancelled["state"],
                "artifact_regeneration_http": rejected_regeneration.status_code,
            },
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:58000"))
