"""Verify exact Prometheus request aggregation across deployed API workers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx
from prometheus_client.parser import text_string_to_metric_families


def request_total(metrics_text: str, *, handler: str, status: str, method: str) -> float:
    total = 0.0
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name != "http_requests_total":
                continue
            if (
                sample.labels.get("handler") == handler
                and sample.labels.get("status") == status
                and sample.labels.get("method") == method
            ):
                total += float(sample.value)
    return total


async def generate_requests(base_url: str, count: int) -> None:
    limits = httpx.Limits(max_connections=count, max_keepalive_connections=0)
    async with httpx.AsyncClient(base_url=base_url, timeout=30, limits=limits) as client:
        responses = await asyncio.gather(
            *(client.get("/api/v1/auth/me", headers={"Connection": "close"}) for _ in range(count))
        )
    statuses = {response.status_code for response in responses}
    if statuses != {401}:
        raise RuntimeError(f"Metrics load endpoint changed authorization semantics: {statuses}")


async def scrape(base_url: str, metrics_token: str) -> str:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        unauthorized = await client.get("/metrics")
        if unauthorized.status_code != 401:
            raise RuntimeError(
                f"Production metrics endpoint is not Bearer protected: {unauthorized.status_code}"
            )
        response = await client.get(
            "/metrics", headers={"Authorization": f"Bearer {metrics_token}"}
        )
    response.raise_for_status()
    return response.text


class ComposeInspector:
    def __init__(
        self,
        *,
        project_dir: Path,
        project_name: str,
        compose_file: str,
        env_file: str,
    ) -> None:
        self.project_dir = project_dir
        self.prefix = [
            "docker",
            "compose",
            "-p",
            project_name,
            "--env-file",
            env_file,
            "-f",
            compose_file,
        ]

    def metric_worker_pids(self) -> list[int]:
        code = (
            "import json,os,re; "
            "d=os.environ['PROMETHEUS_MULTIPROC_DIR']; "
            "p=sorted({int(m.group(1)) for f in os.listdir(d) "
            "if (m:=re.search(r'_(\\d+)\\.db$',f))}); print(json.dumps(p))"
        )
        completed = subprocess.run(
            [*self.prefix, "exec", "-T", "app", "python", "-c", code],
            cwd=self.project_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list) or not all(isinstance(item, int) for item in payload):
            raise RuntimeError("Multiprocess metric PID probe returned invalid JSON")
        return payload


async def exercise(
    *,
    base_url: str,
    count: int,
    expected_workers: int,
    metrics_token: str,
    inspector: ComposeInspector,
) -> dict:
    before_text = await scrape(base_url, metrics_token)
    before = request_total(
        before_text,
        handler="/api/v1/auth/me",
        status="4xx",
        method="GET",
    )
    await generate_requests(base_url, count)
    after_text = await scrape(base_url, metrics_token)
    after = request_total(
        after_text,
        handler="/api/v1/auth/me",
        status="4xx",
        method="GET",
    )
    delta = after - before
    if delta != count:
        raise RuntimeError(
            f"Prometheus lost or double-counted multi-worker requests: "
            f"expected_delta={count}, observed_delta={delta}"
        )
    worker_pids = inspector.metric_worker_pids()
    if len(worker_pids) < expected_workers:
        raise RuntimeError(
            f"Only {len(worker_pids)} workers wrote metric shards; expected {expected_workers}"
        )
    return {
        "requests": count,
        "counter_before": before,
        "counter_after": after,
        "counter_delta": delta,
        "metric_worker_pids": worker_pids,
        "expected_workers": expected_workers,
        "unauthorized_metrics_status": 401,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:58200")
    parser.add_argument("--requests", type=int, default=256)
    parser.add_argument("--expected-workers", type=int, default=4)
    parser.add_argument("--project-name", default="academic_research_metricscheck")
    parser.add_argument("--compose-file", default="docker-compose.prod.yml")
    parser.add_argument("--env-file", default="backend/.env")
    args = parser.parse_args()
    if args.requests < args.expected_workers:
        raise SystemExit("request count must be at least the expected worker count")
    metrics_token = os.environ.get("PROMETHEUS_AUTH_TOKEN", "")
    if len(metrics_token) < 24:
        raise SystemExit("PROMETHEUS_AUTH_TOKEN must be supplied through the environment")
    project_dir = Path(__file__).resolve().parents[2]
    report = asyncio.run(
        exercise(
            base_url=args.base_url,
            count=args.requests,
            expected_workers=args.expected_workers,
            metrics_token=metrics_token,
            inspector=ComposeInspector(
                project_dir=project_dir,
                project_name=args.project_name,
                compose_file=args.compose_file,
                env_file=args.env_file,
            ),
        )
    )
    print("research_multiprocess_metrics_ok", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
