"""Prometheus multi-worker aggregation parser tests."""

from scripts.live_research_multiprocess_metrics_e2e import request_total


def test_request_total_sums_matching_shards_only() -> None:
    metrics = """
# TYPE http_requests_total counter
http_requests_total{handler="/api/v1/auth/me",method="GET",status="4xx"} 17
http_requests_total{handler="/api/v1/auth/me",method="POST",status="4xx"} 3
http_requests_total{handler="/api/v1/health",method="GET",status="2xx"} 99
"""

    assert (
        request_total(
            metrics,
            handler="/api/v1/auth/me",
            status="4xx",
            method="GET",
        )
        == 17
    )


def test_request_total_returns_zero_when_series_is_absent() -> None:
    assert (
        request_total(
            "# TYPE process_cpu_seconds counter\nprocess_cpu_seconds 1\n",
            handler="/api/v1/auth/me",
            status="4xx",
            method="GET",
        )
        == 0
    )
