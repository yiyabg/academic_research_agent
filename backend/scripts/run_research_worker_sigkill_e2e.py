"""Real SIGKILL, PostgreSQL watchdog, and duplicate-delivery recovery E2E."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

WORKER_SERVICE = "research-worker-io"


def assert_sigkill_state(state: dict[str, Any]) -> None:
    observed = {"status": state.get("Status"), "exit_code": state.get("ExitCode")}
    expected = {"status": "exited", "exit_code": 137}
    if observed != expected:
        raise RuntimeError(f"Worker was not terminated by SIGKILL: {observed}")


def validate_recovered_run(run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    if run.get("state") != "COMPLETED" or run.get("state_version") != 1:
        raise RuntimeError(f"SIGKILL recovery did not complete exactly once: {run}")
    sequences = [item.get("sequence") for item in events]
    if sequences != list(range(1, len(events) + 1)):
        raise RuntimeError(f"Recovered event sequence is not contiguous: {sequences}")
    recovery_events = [
        item
        for item in events
        if item.get("payload", {}).get("recovery") == "REENQUEUED_STALLED_STAGE"
    ]
    if len(recovery_events) != 1:
        raise RuntimeError(f"Expected exactly one watchdog recovery event: {recovery_events}")
    return {
        "event_count": len(events),
        "recovery_sequence": recovery_events[0]["sequence"],
        "state_version": run["state_version"],
    }


class ComposeController:
    def __init__(self, project_dir: Path, compose_files: list[str], env_file: str) -> None:
        self.project_dir = project_dir
        self.prefix = ["docker", "compose", "--env-file", env_file]
        for compose_file in compose_files:
            self.prefix.extend(("-f", compose_file))

    def compose(self, *args: str) -> str:
        completed = subprocess.run(
            [*self.prefix, *args],
            cwd=self.project_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def docker(self, *args: str) -> str:
        completed = subprocess.run(
            ["docker", *args],
            cwd=self.project_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def worker_container(self) -> str:
        services = set(self.compose("config", "--services").splitlines())
        if WORKER_SERVICE not in services:
            raise RuntimeError(f"Compose topology does not define {WORKER_SERVICE}")
        containers = self.compose("ps", "-q", WORKER_SERVICE).splitlines()
        if len(containers) != 1:
            raise RuntimeError(f"Expected exactly one {WORKER_SERVICE} container: {containers}")
        return containers[0]

    def inspect_state(self, container_id: str) -> dict[str, Any]:
        payload = json.loads(self.docker("inspect", "--format", "{{json .State}}", container_id))
        if not isinstance(payload, dict):
            raise RuntimeError("docker inspect returned a non-object state")
        return payload

    def inspect_restart_policy(self, container_id: str) -> str:
        return self.docker(
            "inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", container_id
        )

    def kill_without_restart(self, container_id: str) -> dict[str, Any]:
        self.docker("update", "--restart=no", container_id)
        self.docker("kill", "--signal=SIGKILL", container_id)
        state = self.inspect_state(container_id)
        assert_sigkill_state(state)
        return state

    def restore_worker(self, container_id: str) -> None:
        try:
            self.docker("update", "--restart=unless-stopped", container_id)
        finally:
            # Even if docker update fails, still attempt the declarative Compose
            # recovery so the experiment cannot strand the queue intentionally.
            self.compose("up", "-d", "--wait", "--wait-timeout", "240", WORKER_SERVICE)

    def mark_stale_and_recover(self, run_id: str) -> tuple[str, str]:
        mark_output = self.compose(
            "exec",
            "-T",
            "app",
            "python",
            "scripts/live_research_fault_injection.py",
            "mark-stale",
            "--run-id",
            run_id,
        )
        recover_output = self.compose(
            "exec",
            "-T",
            "app",
            "python",
            "scripts/live_research_fault_injection.py",
            "recover-now",
        )
        if f"marked_stale={run_id}" not in mark_output or "watchdog_claimed=" not in recover_output:
            raise RuntimeError(
                f"Watchdog fixture commands did not confirm work: {mark_output!r}, {recover_output!r}"
            )
        return mark_output, recover_output


class ResearchFixtureClient:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=60)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def checked(response: httpx.Response) -> dict[str, Any]:
        if response.is_error:
            raise RuntimeError(
                f"{response.request.method} {response.request.url.path} -> "
                f"{response.status_code}: {response.text[:1000]}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Expected a JSON object")
        return payload

    def create_queued_validate_only_run(self) -> dict[str, Any]:
        suffix = uuid4().hex[:12]
        email = f"research-sigkill-{suffix}@example.com"
        password = f"Sigkill-{secrets.token_urlsafe(18)}"
        self.checked(
            self.client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": password, "full_name": "SIGKILL E2E"},
            )
        )
        token = self.checked(
            self.client.post(
                "/api/v1/auth/login",
                data={"username": email, "password": password},
            )
        )
        self.client.headers["Authorization"] = f"Bearer {token['access_token']}"
        project = self.checked(
            self.client.post(
                "/api/v1/research/projects",
                json={"title": "Worker SIGKILL E2E", "description": "Disposable recovery run"},
            )
        )
        protocol = self.checked(
            self.client.post(
                f"/api/v1/research/projects/{project['id']}/protocols:compile",
                json={
                    "topic": "durable workflow recovery after forced worker termination",
                    "topic_definition": "Validate state durability and duplicate delivery safety.",
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
        approved = self.checked(
            self.client.post(
                f"/api/v1/research/projects/{project['id']}/protocols/"
                f"{protocol['version']}:approve",
                json={"protocol_hash": protocol["protocol_hash"]},
            )
        )
        run = self.checked(
            self.client.post(
                "/api/v1/research/runs",
                json={
                    "project_id": project["id"],
                    "protocol_version": approved["version"],
                    "execution_mode": "validate_only",
                    "client_request_id": f"sigkill-{suffix}",
                },
            )
        )
        if run.get("state") != "QUEUED":
            raise RuntimeError(f"Worker-down fixture was not durably queued: {run}")
        return run

    def await_terminal(self, run_id: str, timeout_seconds: float = 180) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.checked(self.client.get(f"/api/v1/research/runs/{run_id}"))
            if run.get("state") in {"COMPLETED", "FAILED_RETRYABLE", "FAILED_TERMINAL"}:
                return run
            time.sleep(1)
        raise TimeoutError(f"Recovered run {run_id} did not terminate")

    def events(self, run_id: str) -> list[dict[str, Any]]:
        response = self.client.get(f"/api/v1/research/runs/{run_id}/events")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Expected an event list")
        return payload

    def suffix_events(self, run_id: str, sequence: int) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/api/v1/research/runs/{run_id}/events",
            params={"after_sequence": sequence},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Expected an event suffix list")
        return payload


def exercise(controller: ComposeController, client: ResearchFixtureClient) -> dict[str, Any]:
    container_id = controller.worker_container()
    before = controller.inspect_state(container_id)
    if before.get("Status") != "running" or int(before.get("Pid", 0)) <= 0:
        raise RuntimeError(f"Worker is not running before SIGKILL: {before}")
    before_pid = int(before["Pid"])
    restore_required = False
    started = time.monotonic()
    try:
        # Set before the destructive call so a partial docker update/kill still
        # enters the recovery path.
        restore_required = True
        killed_state = controller.kill_without_restart(container_id)
        run = client.create_queued_validate_only_run()
        controller.mark_stale_and_recover(str(run["id"]))
    finally:
        if restore_required:
            controller.restore_worker(container_id)
    after = controller.inspect_state(container_id)
    after_pid = int(after.get("Pid", 0))
    if after.get("Status") != "running" or after_pid <= 0 or after_pid == before_pid:
        raise RuntimeError(f"Worker process was not cleanly replaced after SIGKILL: {after}")
    restart_policy = controller.inspect_restart_policy(container_id)
    if restart_policy != "unless-stopped":
        raise RuntimeError(f"Worker restart policy was not restored: {restart_policy}")

    terminal = client.await_terminal(str(run["id"]))
    events = client.events(str(run["id"]))
    report = validate_recovered_run(terminal, events)
    suffix = client.suffix_events(str(run["id"]), report["recovery_sequence"])
    if not suffix or any(item["sequence"] <= report["recovery_sequence"] for item in suffix):
        raise RuntimeError("after_sequence did not replay the post-recovery suffix")
    return {
        "run_id": str(run["id"]),
        "signal": "SIGKILL",
        "worker_exit_code": killed_state["ExitCode"],
        "worker_pid_replaced": True,
        "restart_policy_restored": True,
        "terminal_state": terminal["state"],
        **report,
        "replayed_suffix_count": len(suffix),
        "wall_seconds": round(time.monotonic() - started, 2),
        "scope_limitation": "validate_only fixture; not a 20-paper full-research run",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:58000")
    parser.add_argument("--env-file", default="backend/.env")
    parser.add_argument("--compose-file", action="append", dest="compose_files", default=[])
    args = parser.parse_args()
    compose_files = args.compose_files or [
        "docker-compose.yml",
        "docker-compose.research.yml",
        "docker-compose.frontend.yml",
    ]
    project_dir = Path(__file__).resolve().parents[2]
    client = ResearchFixtureClient(args.base_url)
    try:
        report = exercise(
            ComposeController(project_dir, compose_files, args.env_file),
            client,
        )
    finally:
        client.close()
    print("research_worker_sigkill_ok", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
