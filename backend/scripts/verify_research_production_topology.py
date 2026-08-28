"""Fail-closed verifier for the standalone research production Compose file."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REQUIRED_SERVICES = {
    "app",
    "celery-beat",
    "clamav",
    "config-check",
    "db",
    "flower",
    "frontend",
    "grobid",
    "migrate",
    "minio",
    "minio-init",
    "model-init",
    "qdrant",
    "redis",
    "research-worker-cpu",
    "research-worker-io",
    "research-worker-llm",
}
PRIVATE_SERVICES = {"db", "redis", "qdrant", "minio", "grobid", "clamav"}
PLACEHOLDERS = {
    "",
    "postgres",
    "password",
    "changeme",
    "change-me-in-production",
    "change-me-in-production-use-openssl-rand-hex-32",
}


def _command_text(service: dict[str, Any]) -> str:
    command = service.get("command", [])
    if isinstance(command, str):
        return command
    return " ".join(str(part) for part in command)


def _environment(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    result: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        result[key] = value
    return result


def _dependencies(service: dict[str, Any]) -> set[str]:
    raw = service.get("depends_on", {})
    if isinstance(raw, dict):
        return set(raw)
    return {str(item) for item in raw}


def _assert_secret(name: str, value: str) -> None:
    normalized = value.strip().lower()
    if normalized in PLACEHOLDERS or normalized.startswith("change-me"):
        raise RuntimeError(f"{name} is still a production placeholder")
    if len(value) < 24:
        raise RuntimeError(f"{name} must contain at least 24 characters")


def verify(config: dict[str, Any], *, allow_local_domain: bool) -> dict[str, Any]:
    services = config.get("services")
    if not isinstance(services, dict):
        raise RuntimeError("Resolved Compose config has no services object")
    missing = REQUIRED_SERVICES - set(services)
    if missing:
        raise RuntimeError(f"Production topology is missing services: {sorted(missing)}")

    app = services["app"]
    app_command = _command_text(app)
    if "--reload" in app_command or "--workers" not in app_command:
        raise RuntimeError("Production API must use multiple immutable workers without reload")
    worker_match = re.search(r"--workers(?:=|\s+)(\d+)", app_command)
    if worker_match is None or int(worker_match.group(1)) < 2:
        raise RuntimeError("Production API requires at least two Uvicorn workers")

    app_env = _environment(app)
    if app_env.get("ENVIRONMENT") != "production" or app_env.get("DEBUG") != "false":
        raise RuntimeError("Production environment/debug flags are not fail-closed")
    multiprocess_dir = app_env.get("PROMETHEUS_MULTIPROC_DIR", "")
    if not multiprocess_dir.startswith("/tmp/"):
        raise RuntimeError("Multi-worker API requires an isolated Prometheus multiprocess dir")
    if "PROMETHEUS_MULTIPROC_DIR" not in app_command or (
        "rm -rf" not in app_command or "mkdir -p" not in app_command
    ):
        raise RuntimeError("API startup must clear and recreate the Prometheus multiprocess dir")
    for name in (
        "SECRET_KEY",
        "API_KEY",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "S3_SECRET_KEY",
        "PROMETHEUS_AUTH_TOKEN",
    ):
        _assert_secret(name, app_env.get(name, ""))
    if app_env.get("S3_ACCESS_KEY", "").strip().lower() in PLACEHOLDERS:
        raise RuntimeError("S3_ACCESS_KEY is still a production placeholder")
    redis_password = app_env["REDIS_PASSWORD"]
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", redis_password):
        raise RuntimeError(
            "REDIS_PASSWORD must be URL-safe because it is embedded in Celery broker URLs"
        )
    for name in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        value = app_env.get(name, "")
        if redis_password not in value or "@redis:6379/0" not in value:
            raise RuntimeError(f"{name} does not use the authenticated production Redis")

    llm_provider = app_env.get("LLM_PROVIDER", "").strip().lower()
    llm_model = app_env.get("AI_MODEL", "").strip()
    if llm_provider not in {"openai", "deepseek", "openai_compatible"}:
        raise RuntimeError("LLM_PROVIDER must be openai, deepseek or openai_compatible")
    if not llm_model:
        raise RuntimeError("AI_MODEL must be configured")
    if llm_provider == "deepseek" and not llm_model.startswith("deepseek-"):
        raise RuntimeError("LLM_PROVIDER=deepseek requires a deepseek-* AI_MODEL")
    if llm_provider == "openai" and llm_model.startswith("deepseek-"):
        raise RuntimeError("A deepseek-* AI_MODEL requires LLM_PROVIDER=deepseek")
    llm_base_url = app_env.get("LLM_BASE_URL", "").strip()
    if llm_provider == "openai_compatible":
        parsed_llm_url = urlsplit(llm_base_url)
        if parsed_llm_url.scheme != "https" or not parsed_llm_url.hostname:
            raise RuntimeError("openai_compatible requires an HTTPS LLM_BASE_URL")
        if parsed_llm_url.username or parsed_llm_url.password:
            raise RuntimeError("LLM_BASE_URL must not contain credentials")
    elif llm_base_url:
        raise RuntimeError("LLM_BASE_URL is only valid for openai_compatible")
    llm_credential_name = "DEEPSEEK_API_KEY" if llm_provider == "deepseek" else "OPENAI_API_KEY"

    expected_dependencies = {"migrate", "minio-init", "grobid", "clamav", "qdrant", "redis"}
    missing_dependencies = expected_dependencies - _dependencies(app)
    if missing_dependencies:
        raise RuntimeError(f"API is missing startup gates: {sorted(missing_dependencies)}")

    queues = {
        "research-worker-io": ("research-io",),
        "research-worker-cpu": ("research-cpu",),
        "research-worker-llm": ("research-llm", "paper-analysis"),
    }
    for service_name, required_queues in queues.items():
        service = services[service_name]
        command = _command_text(service)
        missing_queues = [queue for queue in required_queues if queue not in command]
        if missing_queues:
            raise RuntimeError(f"{service_name} does not declare required queues {missing_queues}")
        worker_env = _environment(service)
        for name in ("S3_ENDPOINT", "GROBID_URL", "CLAMAV_HOST", "CELERY_BROKER_URL"):
            if worker_env.get(name) != app_env.get(name):
                raise RuntimeError(f"{service_name} has inconsistent {name}")

    published_private = sorted(name for name in PRIVATE_SERVICES if services[name].get("ports"))
    if published_private:
        raise RuntimeError(f"Private data services publish host ports: {published_private}")

    bind_mounts: list[str] = []
    for service_name, service in services.items():
        for volume in service.get("volumes", []):
            if isinstance(volume, dict) and volume.get("type") == "bind":
                bind_mounts.append(f"{service_name}:{volume.get('source', '')}")
    if bind_mounts:
        raise RuntimeError(f"Production services use mutable bind mounts: {sorted(bind_mounts)}")

    network = config.get("networks", {}).get("backend", {})
    if network.get("internal") is True:
        raise RuntimeError(
            "Research workers need outbound HTTPS; backend network cannot be internal"
        )
    if services["model-init"].get("network_mode") != "host":
        raise RuntimeError("Model preloading must reach a host-loopback download proxy")
    required_no_proxy = {"db", "redis", "qdrant", "minio", "grobid", "clamav"}
    for service_name in ("app", *queues):
        runtime_env = _environment(services[service_name])
        if any(runtime_env.get(name) for name in ("HTTP_PROXY", "ALL_PROXY")):
            raise RuntimeError(f"Broad download proxy leaked into runtime service {service_name}")
        if runtime_env.get("HTTPS_PROXY"):
            no_proxy = {
                item.strip() for item in runtime_env.get("NO_PROXY", "").split(",") if item.strip()
            }
            if required_no_proxy - no_proxy:
                raise RuntimeError(f"{service_name} HTTPS proxy lacks internal NO_PROXY exclusions")

    volumes = set(config.get("volumes", {}))
    expected_volumes = {
        "postgres_data",
        "redis_data",
        "qdrant_data",
        "minio_data",
        "models_cache",
        "clamav_data",
    }
    if expected_volumes - volumes:
        raise RuntimeError(f"Missing durable volumes: {sorted(expected_volumes - volumes)}")

    frontend_args = services["frontend"].get("build", {}).get("args", {})
    public_site_url = str(frontend_args.get("NEXT_PUBLIC_SITE_URL", ""))
    if not allow_local_domain and (
        not public_site_url.startswith("https://")
        or "localhost" in public_site_url
        or "example.com" in public_site_url
    ):
        raise RuntimeError("NEXT_PUBLIC_SITE_URL must name the real TLS deployment host")

    flower_env = _environment(services["flower"])
    flower_auth = flower_env.get("FLOWER_BASIC_AUTH", "")
    _, separator, flower_password = flower_auth.partition(":")
    if not separator:
        raise RuntimeError("Flower monitoring must require basic authentication")
    _assert_secret("FLOWER_PASSWORD", flower_password)

    return {
        "service_count": len(services),
        "api_workers": int(worker_match.group(1)),
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_credential_configured": bool(app_env.get(llm_credential_name)),
        "research_queues": sorted(queue for values in queues.values() for queue in values),
        "private_services_without_host_ports": sorted(PRIVATE_SERVICES),
        "durable_volumes": sorted(expected_volumes),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="backend/.env")
    parser.add_argument("--compose-file", default="docker-compose.prod.yml")
    parser.add_argument("--allow-local-domain", action="store_true")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[2]
    command = [
        "docker",
        "compose",
        "--env-file",
        args.env_file,
        "-f",
        args.compose_file,
        "config",
        "--format",
        "json",
    ]
    completed = subprocess.run(
        command,
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip() or "docker compose config failed"
        raise RuntimeError(diagnostic[:4000])
    report = verify(json.loads(completed.stdout), allow_local_domain=args.allow_local_domain)
    print("research_production_topology_ok", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
