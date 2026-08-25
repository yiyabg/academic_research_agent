"""Regression tests for the fail-closed production topology verifier."""

from copy import deepcopy

import pytest

from scripts.verify_research_production_topology import (
    PRIVATE_SERVICES,
    REQUIRED_SERVICES,
    verify,
)


def _config() -> dict:
    shared_environment = {
        "DEBUG": "false",
        "ENVIRONMENT": "production",
        "SECRET_KEY": "s" * 32,
        "API_KEY": "a" * 32,
        "POSTGRES_PASSWORD": "p" * 32,
        "REDIS_PASSWORD": "r" * 32,
        "S3_ACCESS_KEY": "m" * 24,
        "S3_SECRET_KEY": "n" * 32,
        "CELERY_BROKER_URL": f"redis://:{'r' * 32}@redis:6379/0",
        "CELERY_RESULT_BACKEND": f"redis://:{'r' * 32}@redis:6379/0",
        "S3_ENDPOINT": "http://minio:9000",
        "GROBID_URL": "http://grobid:8070",
        "CLAMAV_HOST": "clamav",
        "PROMETHEUS_MULTIPROC_DIR": "/tmp/prometheus-multiproc",
        "PROMETHEUS_AUTH_TOKEN": "t" * 32,
        "LLM_PROVIDER": "openai",
        "LLM_BASE_URL": "",
        "AI_MODEL": "gpt-5.5",
        "OPENAI_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
    }
    services = {name: {} for name in REQUIRED_SERVICES}
    services["app"] = {
        "command": [
            "sh",
            "-c",
            "rm -rf $PROMETHEUS_MULTIPROC_DIR && "
            "mkdir -p $PROMETHEUS_MULTIPROC_DIR && "
            "uvicorn app.main:app --workers 4",
        ],
        "environment": shared_environment,
        "depends_on": {
            name: {"condition": "service_healthy"}
            for name in ("migrate", "minio-init", "grobid", "clamav", "qdrant", "redis")
        },
    }
    services["flower"] = {"environment": {"FLOWER_BASIC_AUTH": f"admin:{'f' * 32}"}}
    services["frontend"] = {
        "build": {"args": {"NEXT_PUBLIC_SITE_URL": "https://research.example.org"}}
    }
    services["model-init"] = {"network_mode": "host"}
    for service_name, queue in {
        "research-worker-io": "research-io",
        "research-worker-cpu": "research-cpu",
        "research-worker-llm": "research-llm,paper-analysis",
    }.items():
        services[service_name] = {
            "command": ["celery", "-Q", queue],
            "environment": shared_environment,
        }
    return {
        "services": services,
        "networks": {"backend": {"driver": "bridge"}},
        "volumes": {
            name: {}
            for name in (
                "postgres_data",
                "redis_data",
                "qdrant_data",
                "minio_data",
                "models_cache",
                "clamav_data",
            )
        },
    }


def test_accepts_complete_split_worker_production_topology() -> None:
    report = verify(_config(), allow_local_domain=True)

    assert report["api_workers"] == 4
    assert report["service_count"] == len(REQUIRED_SERVICES)
    assert report["private_services_without_host_ports"] == sorted(PRIVATE_SERVICES)
    assert report["llm_provider"] == "openai"
    assert report["llm_credential_configured"] is False


def test_accepts_deepseek_without_openai_key() -> None:
    config = _config()
    shared = config["services"]["app"]["environment"]
    shared.update(
        {
            "LLM_PROVIDER": "deepseek",
            "AI_MODEL": "deepseek-v4-pro",
            "OPENAI_API_KEY": "",
            "DEEPSEEK_API_KEY": "deepseek-fixture-key",
        }
    )

    report = verify(config, allow_local_domain=True)

    assert report["llm_provider"] == "deepseek"
    assert report["llm_model"] == "deepseek-v4-pro"
    assert report["llm_credential_configured"] is True


def test_accepts_project_scoped_openai_compatible_endpoint() -> None:
    config = _config()
    shared = config["services"]["app"]["environment"]
    shared.update(
        {
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://gateway.example.net/codex/v1",
            "AI_MODEL": "gpt-5.5",
            "OPENAI_API_KEY": "project-scoped-fixture-key",
        }
    )

    report = verify(config, allow_local_domain=True)

    assert report["llm_provider"] == "openai_compatible"
    assert report["llm_model"] == "gpt-5.5"
    assert report["llm_credential_configured"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda config: config["services"].pop("clamav"), "missing services"),
        (
            lambda config: config["services"]["app"].update(
                {"command": ["uvicorn", "app.main:app", "--reload"]}
            ),
            "multiple immutable workers",
        ),
        (
            lambda config: config["services"]["app"]["environment"].pop("PROMETHEUS_MULTIPROC_DIR"),
            "Prometheus multiprocess",
        ),
        (
            lambda config: config["services"]["app"]["environment"].update(
                {"SECRET_KEY": "change-me-in-production-use-openssl-rand-hex-32"}
            ),
            "placeholder",
        ),
        (
            lambda config: config["services"]["db"].update({"ports": ["5432:5432"]}),
            "publish host ports",
        ),
        (
            lambda config: config["services"]["app"].update(
                {"volumes": [{"type": "bind", "source": "./backend/app", "target": "/app/app"}]}
            ),
            "mutable bind mounts",
        ),
        (
            lambda config: config["networks"]["backend"].update({"internal": True}),
            "outbound HTTPS",
        ),
        (
            lambda config: config["services"]["model-init"].update({"network_mode": "bridge"}),
            "host-loopback",
        ),
    ],
)
def test_rejects_incomplete_or_unsafe_topology(mutation, message: str) -> None:
    config = deepcopy(_config())
    mutation(config)

    with pytest.raises(RuntimeError, match=message):
        verify(config, allow_local_domain=True)
