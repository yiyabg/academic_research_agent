"""Sustained deployed Qdrant/GROBID outage and recovery acceptance test.

Run this script on the Docker host. It stops one exact Compose service, samples
the production readiness contract throughout the requested outage window, and
always attempts to restore the service in ``finally`` before returning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_SERVICES = {"qdrant", "grobid"}


@dataclass(frozen=True)
class ReadinessObservation:
    status_code: int
    payload: dict[str, Any]
    latency_ms: float


def fetch_readiness(url: str) -> ReadinessObservation:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            status_code = response.status
            body = response.read()
    except urllib.error.HTTPError as error:
        status_code = error.code
        body = error.read()
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError("Readiness endpoint did not return a JSON object")
    return ReadinessObservation(
        status_code=status_code,
        payload=payload,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def assert_outage_semantics(service: str, observation: ReadinessObservation) -> None:
    checks = observation.payload.get("checks", {})
    capabilities = observation.payload.get("capabilities", {})
    if service == "qdrant":
        expected = {
            "status_code": 503,
            "status": "not_ready",
            "dependency_status": "unhealthy",
            "search_only": False,
            "full_research": False,
        }
        observed = {
            "status_code": observation.status_code,
            "status": observation.payload.get("status"),
            "dependency_status": checks.get("vector_store", {}).get("status"),
            "search_only": capabilities.get("search_only"),
            "full_research": capabilities.get("full_research"),
        }
    elif service == "grobid":
        expected = {
            "status_code": 200,
            "status": "ready",
            "dependency_status": "unavailable",
            "search_only": True,
            "full_research": False,
        }
        observed = {
            "status_code": observation.status_code,
            "status": observation.payload.get("status"),
            "dependency_status": checks.get("research_parsing", {}).get("status"),
            "search_only": capabilities.get("search_only"),
            "full_research": capabilities.get("full_research"),
        }
    else:
        raise ValueError(f"Unsupported dependency outage: {service}")
    if observed != expected:
        raise RuntimeError(
            f"{service} outage violated readiness semantics: "
            f"expected={expected}, observed={observed}"
        )


def assert_recovered(service: str, observation: ReadinessObservation) -> None:
    checks = observation.payload.get("checks", {})
    dependency_key = "vector_store" if service == "qdrant" else "research_parsing"
    observed = {
        "status_code": observation.status_code,
        "status": observation.payload.get("status"),
        "dependency_status": checks.get(dependency_key, {}).get("status"),
        "search_only": observation.payload.get("capabilities", {}).get("search_only"),
    }
    expected = {
        "status_code": 200,
        "status": "ready",
        "dependency_status": "healthy",
        "search_only": True,
    }
    if observed != expected:
        raise RuntimeError(
            f"{service} did not recover readiness: expected={expected}, observed={observed}"
        )


class ComposeController:
    def __init__(self, project_dir: Path, compose_files: list[str], env_file: str) -> None:
        self.project_dir = project_dir
        self.prefix = ["docker", "compose", "--env-file", env_file]
        for compose_file in compose_files:
            self.prefix.extend(("-f", compose_file))

    def run(self, *args: str) -> str:
        completed = subprocess.run(
            [*self.prefix, *args],
            cwd=self.project_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def assert_service_exists_and_runs(self, service: str) -> None:
        services = set(self.run("config", "--services").splitlines())
        if service not in services:
            raise RuntimeError(f"Compose topology does not define {service}")
        running = set(
            self.run("ps", "--services", "--filter", "status=running", service).splitlines()
        )
        if service not in running:
            raise RuntimeError(f"Refusing outage test because {service} is not running")

    def stop(self, service: str) -> None:
        self.run("stop", "--timeout", "15", service)

    def restore(self, service: str) -> None:
        self.run("up", "-d", "--wait", "--wait-timeout", "240", service)


def wait_for_semantics(
    *,
    url: str,
    validator,
    service: str,
    timeout_seconds: float,
) -> ReadinessObservation:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            observation = fetch_readiness(url)
            validator(service, observation)
            return observation
        except Exception as error:  # readiness is expected to converge asynchronously
            last_error = error
            time.sleep(2)
    raise TimeoutError(f"{service} readiness did not converge: {last_error}")


def exercise(
    *,
    controller: ComposeController,
    service: str,
    readiness_url: str,
    duration_seconds: float,
    sample_interval_seconds: float,
) -> dict[str, Any]:
    controller.assert_service_exists_and_runs(service)
    baseline = wait_for_semantics(
        url=readiness_url,
        validator=assert_recovered,
        service=service,
        timeout_seconds=60,
    )
    stopped = False
    samples: list[ReadinessObservation] = []
    started = time.monotonic()
    try:
        controller.stop(service)
        stopped = True
        wait_for_semantics(
            url=readiness_url,
            validator=assert_outage_semantics,
            service=service,
            timeout_seconds=60,
        )
        deadline = time.monotonic() + duration_seconds
        while True:
            observation = fetch_readiness(readiness_url)
            assert_outage_semantics(service, observation)
            samples.append(observation)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(sample_interval_seconds, remaining))
    finally:
        if stopped:
            controller.restore(service)
    recovered = wait_for_semantics(
        url=readiness_url,
        validator=assert_recovered,
        service=service,
        timeout_seconds=120,
    )
    return {
        "service": service,
        "requested_outage_seconds": duration_seconds,
        "observed_wall_seconds": round(time.monotonic() - started, 2),
        "outage_samples": len(samples),
        "outage_latency_max_ms": round(max(item.latency_ms for item in samples), 2),
        "baseline_latency_ms": round(baseline.latency_ms, 2),
        "recovery_latency_ms": round(recovered.latency_ms, 2),
        "recovered_status": recovered.payload.get("status"),
        "recovered_search_only": recovered.payload.get("capabilities", {}).get("search_only"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=sorted(SUPPORTED_SERVICES))
    parser.add_argument("--duration-seconds", type=float, default=120)
    parser.add_argument("--sample-interval-seconds", type=float, default=10)
    parser.add_argument(
        "--readiness-url",
        default="http://127.0.0.1:58000/api/v1/health/ready",
    )
    parser.add_argument("--env-file", default="backend/.env")
    parser.add_argument(
        "--compose-file",
        action="append",
        dest="compose_files",
        default=[],
    )
    args = parser.parse_args()
    if args.duration_seconds < 60:
        raise SystemExit("Sustained outage acceptance requires at least 60 seconds")
    if args.sample_interval_seconds <= 0:
        raise SystemExit("sample interval must be positive")

    project_dir = Path(__file__).resolve().parents[2]
    compose_files = args.compose_files or [
        "docker-compose.yml",
        "docker-compose.research.yml",
        "docker-compose.frontend.yml",
    ]
    report = exercise(
        controller=ComposeController(project_dir, compose_files, args.env_file),
        service=args.service,
        readiness_url=args.readiness_url,
        duration_seconds=args.duration_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
    )
    print("research_dependency_outage_ok", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
