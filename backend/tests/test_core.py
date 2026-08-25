"""Tests for core modules."""

# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
import contextlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import settings
from app.core.exceptions import (
    AlreadyExistsError,
    AppException,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.core.middleware import RequestIDMiddleware
from app.core.cache import setup_cache
from app.services.rate_limit import DEFAULT_RATE_LIMITS, RateLimitCategory, check_rate_limit
from app.services.rate_limit.service import client_ip
from app.services.rate_limit.storage import InMemoryStorage
from unittest.mock import patch

from fastapi import FastAPI

from app.core.logfire_setup import instrument_app, setup_logfire


class TestSettings:
    """Tests for settings configuration."""

    def test_project_name_is_set(self):
        """Test project name is configured."""
        assert settings.PROJECT_NAME == "academic_research_agent"

    def test_api_v1_str_is_set(self):
        """Test API version string is set."""
        assert settings.API_V1_STR == "/api/v1"

    def test_debug_mode_default(self):
        """Test debug mode has default value."""
        assert isinstance(settings.DEBUG, bool)

    def test_cors_origins_is_list(self):
        """Test CORS origins is a list."""
        assert isinstance(settings.CORS_ORIGINS, list)


class TestExceptions:
    """Tests for custom exceptions."""

    def test_app_exception(self):
        """Test AppException initialization."""
        error = AppException(message="Test error", code="TEST_ERROR")
        assert error.message == "Test error"
        assert error.code == "TEST_ERROR"
        assert str(error) == "Test error"

    def test_not_found_error(self):
        """Test NotFoundError."""
        error = NotFoundError(message="Item not found")
        assert error.status_code == 404
        assert error.code == "NOT_FOUND"

    def test_already_exists_error(self):
        """Test AlreadyExistsError."""
        error = AlreadyExistsError(message="Item already exists")
        assert error.status_code == 409
        assert error.code == "ALREADY_EXISTS"

    def test_authentication_error(self):
        """Test AuthenticationError."""
        error = AuthenticationError(message="Invalid credentials")
        assert error.status_code == 401
        assert error.code == "AUTHENTICATION_ERROR"

    def test_authorization_error(self):
        """Test AuthorizationError."""
        error = AuthorizationError(message="Not authorized")
        assert error.status_code == 403
        assert error.code == "AUTHORIZATION_ERROR"

    def test_validation_error(self):
        """Test ValidationError."""
        error = ValidationError(message="Invalid input")
        assert error.status_code == 422
        assert error.code == "VALIDATION_ERROR"


class TestCacheSetup:
    """Tests for cache setup."""

    def test_setup_cache_function_exists(self):
        """Test setup_cache function exists."""
        assert setup_cache is not None
        assert callable(setup_cache)


class TestMiddleware:
    """Tests for middleware."""

    def test_request_id_middleware_exists(self):
        """Test request ID middleware is configured."""
        assert RequestIDMiddleware is not None


class TestRateLimit:
    """Tests for rate limiting.

    These assert the *effect* on a caller, not that the limiter object exists —
    a limiter that is never attached to a route passes the latter and protects
    nothing.
    """

    def test_auth_category_has_a_per_ip_limit(self):
        """The auth rule must be per-IP: /auth/* runs before authentication."""
        rule = DEFAULT_RATE_LIMITS[RateLimitCategory.AUTH]
        assert rule.per_ip is not None

    @pytest.mark.anyio
    async def test_exceeding_the_auth_limit_raises_429(self):
        """The (limit + 1)-th call from one IP is rejected with Retry-After."""
        limit = DEFAULT_RATE_LIMITS[RateLimitCategory.AUTH].per_ip
        assert limit is not None

        for _ in range(limit):
            await check_rate_limit(category=RateLimitCategory.AUTH, client_ip="203.0.113.7")

        with pytest.raises(HTTPException) as exc_info:
            await check_rate_limit(category=RateLimitCategory.AUTH, client_ip="203.0.113.7")

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers is not None
        assert "Retry-After" in exc_info.value.headers

    @pytest.mark.anyio
    async def test_limits_are_scoped_per_ip(self):
        """One IP exhausting its budget must not lock out another."""
        limit = DEFAULT_RATE_LIMITS[RateLimitCategory.AUTH].per_ip
        assert limit is not None

        for _ in range(limit + 1):
            with contextlib.suppress(HTTPException):
                await check_rate_limit(category=RateLimitCategory.AUTH, client_ip="203.0.113.7")

        # A different caller is unaffected.
        await check_rate_limit(category=RateLimitCategory.AUTH, client_ip="198.51.100.4")

    def test_trusted_proxy_uses_forwarded_client_ip(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", ["172.16.0.0/12"])
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/login",
                "headers": [(b"x-forwarded-for", b"192.168.31.88, 172.26.0.14")],
                "client": ("172.26.0.14", 12345),
            }
        )
        assert client_ip(request) == "192.168.31.88"

    def test_untrusted_peer_cannot_spoof_forwarded_client_ip(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", ["172.16.0.0/12"])
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/login",
                "headers": [(b"x-forwarded-for", b"192.168.31.88")],
                "client": ("192.168.31.77", 12345),
            }
        )
        assert client_ip(request) == "192.168.31.77"

    @pytest.mark.anyio
    async def test_rejected_attempt_does_not_extend_sliding_window(self):
        storage = InMemoryStorage()
        with patch("app.services.rate_limit.storage.time.time", return_value=100.0):
            assert (await storage.increment_and_check("auth", 2, 10)).allowed is True
        with patch("app.services.rate_limit.storage.time.time", return_value=101.0):
            assert (await storage.increment_and_check("auth", 2, 10)).allowed is True
        with patch("app.services.rate_limit.storage.time.time", return_value=102.0):
            rejected = await storage.increment_and_check("auth", 2, 10)
        assert rejected.allowed is False
        assert rejected.retry_after_seconds == 8

        # The rejected request at t=102 was not inserted. Once the oldest
        # allowed request expires, the next request is accepted.
        with patch("app.services.rate_limit.storage.time.time", return_value=110.1):
            accepted = await storage.increment_and_check("auth", 2, 10)
        assert accepted.allowed is True
        assert accepted.current_count == 2


class TestLogfireSetup:
    """Tests for Logfire setup."""

    @patch("app.core.logfire_setup.logfire")
    def test_setup_logfire_configures(self, mock_logfire):
        """Test setup_logfire calls configure."""
        setup_logfire()
        mock_logfire.configure.assert_called_once()

    @patch("app.core.logfire_setup.logfire")
    def test_instrument_app_instruments_fastapi(self, mock_logfire):
        """Test instrument_app instruments FastAPI."""
        app = FastAPI()
        instrument_app(app)
        mock_logfire.instrument_fastapi.assert_called()
