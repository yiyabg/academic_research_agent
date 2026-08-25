"""Readiness-contract tests for sustained dependency outage automation."""

from copy import deepcopy

import pytest

from scripts.run_research_dependency_outage_e2e import (
    ReadinessObservation,
    assert_outage_semantics,
    assert_recovered,
)


def _ready() -> dict:
    return {
        "status": "ready",
        "checks": {
            "vector_store": {"status": "healthy"},
            "research_parsing": {"status": "healthy"},
        },
        "capabilities": {"search_only": True, "full_research": False},
    }


def _observation(status_code: int, payload: dict) -> ReadinessObservation:
    return ReadinessObservation(status_code=status_code, payload=payload, latency_ms=1.0)


def test_qdrant_outage_must_close_search_capability() -> None:
    payload = _ready()
    payload["status"] = "not_ready"
    payload["checks"]["vector_store"]["status"] = "unhealthy"
    payload["capabilities"]["search_only"] = False

    assert_outage_semantics("qdrant", _observation(503, payload))


def test_grobid_outage_keeps_search_but_closes_full_research() -> None:
    payload = _ready()
    payload["checks"]["research_parsing"]["status"] = "unavailable"

    assert_outage_semantics("grobid", _observation(200, payload))


@pytest.mark.parametrize(
    ("service", "mutation"),
    [
        ("qdrant", lambda payload: payload["capabilities"].update({"search_only": True})),
        ("grobid", lambda payload: payload["capabilities"].update({"full_research": True})),
    ],
)
def test_outage_contract_rejects_unsafe_capability(service, mutation) -> None:
    payload = _ready()
    status_code = 200
    if service == "qdrant":
        payload["status"] = "not_ready"
        payload["checks"]["vector_store"]["status"] = "unhealthy"
        payload["capabilities"]["search_only"] = False
        status_code = 503
    else:
        payload["checks"]["research_parsing"]["status"] = "unavailable"
    mutation(payload)

    with pytest.raises(RuntimeError, match="violated readiness semantics"):
        assert_outage_semantics(service, _observation(status_code, payload))


@pytest.mark.parametrize("service", ["qdrant", "grobid"])
def test_recovery_requires_healthy_ready_search_capability(service: str) -> None:
    assert_recovered(service, _observation(200, _ready()))

    unsafe = deepcopy(_ready())
    unsafe["capabilities"]["search_only"] = False
    with pytest.raises(RuntimeError, match="did not recover readiness"):
        assert_recovered(service, _observation(200, unsafe))
