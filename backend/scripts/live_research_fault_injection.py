"""Reproducible deployed-worker outage and PostgreSQL-watchdog recovery check."""

import argparse
import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx


async def _checked(response: httpx.Response) -> dict:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Expected JSON object")
    return payload


async def exercise(base_url: str) -> None:
    """Create a queued run while research-io is down, then await watchdog recovery."""
    suffix = uuid4().hex[:12]
    email = f"research-fault-{suffix}@example.com"
    password = f"Fault-{secrets.token_urlsafe(18)}"
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        await _checked(
            await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": password, "full_name": "Fault E2E"},
            )
        )
        token = await _checked(
            await client.post("/api/v1/auth/login", data={"username": email, "password": password})
        )
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        project = await _checked(
            await client.post(
                "/api/v1/research/projects",
                json={"title": "Worker recovery E2E", "description": "Disposable fault test"},
            )
        )
        protocol = await _checked(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols:compile",
                json={
                    "topic": "fault tolerant research workflow",
                    "topic_definition": "Durable workflow recovery under worker interruption.",
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
        approved = await _checked(
            await client.post(
                f"/api/v1/research/projects/{project['id']}/protocols/"
                f"{protocol['version']}:approve",
                json={"protocol_hash": protocol["protocol_hash"]},
            )
        )
        run = await _checked(
            await client.post(
                "/api/v1/research/runs",
                json={
                    "project_id": project["id"],
                    "protocol_version": approved["version"],
                    "execution_mode": "validate_only",
                    "client_request_id": f"worker-recovery-{suffix}",
                },
            )
        )
        print(f"fault_run_id={run['id']} initial_state={run['state']}", flush=True)
        deadline = asyncio.get_running_loop().time() + 300
        while asyncio.get_running_loop().time() < deadline:
            run = await _checked(await client.get(f"/api/v1/research/runs/{run['id']}"))
            if run["state"] in {"COMPLETED", "FAILED_RETRYABLE", "FAILED_TERMINAL"}:
                break
            await asyncio.sleep(1)
        else:
            raise TimeoutError("Fault-injection run did not recover within five minutes")
        events_response = await client.get(f"/api/v1/research/runs/{run['id']}/events")
        events_response.raise_for_status()
        events = events_response.json()
        sequences = [item["sequence"] for item in events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise RuntimeError(f"Event sequence is not contiguous: {sequences}")
        recovery_events = [
            item
            for item in events
            if item["payload"].get("recovery") == "REENQUEUED_STALLED_STAGE"
        ]
        if not recovery_events:
            raise RuntimeError("Watchdog recovery event was not persisted")
        if run["state"] != "COMPLETED" or run["state_version"] != 1:
            raise RuntimeError(f"Duplicate delivery changed workflow more than once: {run}")
        suffix_response = await client.get(
            f"/api/v1/research/runs/{run['id']}/events",
            params={"after_sequence": recovery_events[0]["sequence"]},
        )
        suffix_response.raise_for_status()
        suffix_events = suffix_response.json()
        if not suffix_events or any(
            item["sequence"] <= recovery_events[0]["sequence"] for item in suffix_events
        ):
            raise RuntimeError("after_sequence recovery replay failed")
        print(
            "fault_injection_ok",
            {
                "run_id": run["id"],
                "terminal_state": run["state"],
                "state_version": run["state_version"],
                "event_count": len(events),
                "recovery_event_count": len(recovery_events),
                "replayed_suffix_count": len(suffix_events),
            },
            flush=True,
        )


async def mark_stale(run_id: UUID) -> None:
    """Age exactly one fixture run so the production watchdog may claim it."""
    from app.db.models.literature_research.run import ResearchRun
    from app.db.session import get_worker_db_context

    async with get_worker_db_context() as db:
        run = await db.get(ResearchRun, run_id, with_for_update=True)
        if run is None:
            raise RuntimeError("Fault-injection run does not exist")
        if run.state != "QUEUED":
            raise RuntimeError(f"Expected QUEUED fixture, got {run.state}")
        run.updated_at = datetime.now(UTC) - timedelta(hours=1)
        run.lease_owner = None
        run.lease_expires_at = None
    print(f"marked_stale={run_id}", flush=True)


def recover_now() -> None:
    """Invoke the same production watchdog body synchronously for deterministic testing."""
    from app.worker.tasks.literature_research_tasks import recover_stalled_research_runs

    claimed = recover_stalled_research_runs.run()
    if claimed < 1:
        raise RuntimeError("Production watchdog did not claim a stale run")
    print(f"watchdog_claimed={claimed}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    exercise_parser = subparsers.add_parser("exercise")
    exercise_parser.add_argument("--base-url", default="http://127.0.0.1:58000")
    stale_parser = subparsers.add_parser("mark-stale")
    stale_parser.add_argument("--run-id", type=UUID, required=True)
    subparsers.add_parser("recover-now")
    args = parser.parse_args()
    if args.command == "exercise":
        asyncio.run(exercise(args.base_url))
    elif args.command == "mark-stale":
        asyncio.run(mark_stale(args.run_id))
    else:
        recover_now()


if __name__ == "__main__":
    main()
