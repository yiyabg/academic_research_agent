"""RateLimitService — per-category, per-plan, sliding-window rate limiter."""

from __future__ import annotations

import logging
from ipaddress import ip_address, ip_network

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.core.config import settings
from app.services.rate_limit.rules import DEFAULT_RATE_LIMITS, RateLimitRule
from app.services.rate_limit.storage import RateLimitResult, RateLimitStorage, get_storage

logger = logging.getLogger(__name__)

_storage: RateLimitStorage | None = None


def _get_storage() -> RateLimitStorage:
    global _storage
    if _storage is None:
        _storage = get_storage()
    return _storage


async def _check_one(
    storage: RateLimitStorage,
    key: str,
    limit: int,
    period: int,
) -> RateLimitResult:
    return await storage.increment_and_check(key, limit, period)


def client_ip(request: Request) -> str:
    """Return the caller IP, accepting forwarding data only from trusted proxies."""
    peer_ip = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(peer_ip):
        return peer_ip

    forwarded = request.headers.get("x-forwarded-for", "")
    candidate = forwarded.split(",", 1)[0].strip()
    try:
        return str(ip_address(candidate)) if candidate else peer_ip
    except ValueError:
        return peer_ip


def _is_trusted_proxy(peer_ip: str) -> bool:
    try:
        address = ip_address(peer_ip)
    except ValueError:
        return False
    for cidr in settings.TRUSTED_PROXY_CIDRS:
        try:
            if address in ip_network(cidr, strict=False):
                return True
        except ValueError:
            logger.warning("invalid_trusted_proxy_cidr", extra={"cidr": cidr})
    return False


async def check_rate_limit(
    *,
    category: str,
    client_ip: str,
    user_id: str | None = None,
    org_id: str | None = None,
    is_admin: bool = False,
    plan_features: dict | None = None,
) -> None:
    """Check rate limit; raise HTTP 429 if exceeded.

    Args:
        category: One of RateLimitCategory constants.
        client_ip: Caller's IP for the per-IP scope. Pass ``"unknown"`` when it
            cannot be determined — the limit then applies to that shared bucket.
        user_id: Authenticated user ID (None for anonymous callers).
        org_id: Active organization ID (None for no-org context).
        is_admin: If True, all limits are bypassed.
        plan_features: org.subscription.price.plan.features dict (or None for free-tier defaults).
    """
    if is_admin:
        return

    storage = _get_storage()

    # Resolve rule: plan features > defaults
    rule: RateLimitRule | None = None
    if plan_features:
        rl = plan_features.get("rate_limits", {})
        if category in rl:
            rule = RateLimitRule.from_dict(rl[category])

    if rule is None:
        rule = DEFAULT_RATE_LIMITS.get(category)

    if rule is None or rule.is_unlimited():
        return

    if rule.per_ip is not None:
        result = await _check_one(
            storage, f"rl:{category}:ip:{client_ip}", rule.per_ip, rule.ip_period_seconds
        )
        if not result.allowed:
            _raise_429(result, category, "ip")

    if rule.per_user is not None and user_id:
        result = await _check_one(
            storage, f"rl:{category}:user:{user_id}", rule.per_user, rule.period_seconds
        )
        if not result.allowed:
            _raise_429(result, category, "user")

    if rule.per_org is not None and org_id:
        result = await _check_one(
            storage, f"rl:{category}:org:{org_id}", rule.per_org, rule.org_period_seconds
        )
        if not result.allowed:
            _raise_429(result, category, "org")


def _raise_429(result: RateLimitResult, category: str, scope: str) -> None:
    retry_after = result.retry_after_seconds or 60
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": f"Rate limit exceeded for category '{category}' (scope: {scope}). "
                f"Retry after {retry_after} seconds.",
                "details": {
                    "category": category,
                    "scope": scope,
                    "limit": result.limit,
                    "current": result.current_count,
                    "retry_after_seconds": retry_after,
                },
            }
        },
        headers={"Retry-After": str(retry_after)},
    )


def make_rate_limit_dep(category: str):
    """FastAPI dependency factory for an *authenticated* route.

    Scopes the limit to the user.
    Unauthenticated routes must use :func:`make_anonymous_rate_limit_dep` — they
    run before auth and so cannot depend on ``CurrentUser``.

    Usage::

        AgentRateLimit = make_rate_limit_dep(RateLimitCategory.AGENT_INVOCATION)

        @router.post("/invoke", dependencies=[AgentRateLimit])
        async def invoke(user: CurrentUser) -> Any:
            ...
    """

    async def _dep(request: Request, user: CurrentUser) -> None:
        await check_rate_limit(
            category=category,
            client_ip=client_ip(request),
            user_id=str(user.id),
            is_admin=getattr(user, "is_app_admin", False),
        )

    return Depends(_dep)


def make_anonymous_rate_limit_dep(category: str):
    """FastAPI dependency factory for an *unauthenticated* route — per-IP only.

    This is what guards ``/auth/*``: those endpoints run before authentication,
    so there is no user to scope to, and they are exactly the ones that need an
    IP limit (credential stuffing, reset-email flooding).

    Usage::

        AuthRateLimit = make_anonymous_rate_limit_dep(RateLimitCategory.AUTH)

        @router.post("/login", dependencies=[AuthRateLimit])
        async def login(...) -> Any:
            ...
    """

    async def _dep(request: Request) -> None:
        await check_rate_limit(category=category, client_ip=client_ip(request))

    return Depends(_dep)
