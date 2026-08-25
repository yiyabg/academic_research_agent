"""Authentication routes."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser, DBSession, SessionSvc, UserSvc
from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
)
from app.schemas.token import RefreshTokenRequest, Token
from app.schemas.user import UserCreate, UserRead
from app.services.rate_limit import RateLimitCategory, make_anonymous_rate_limit_dep
from app.services.rate_limit.service import client_ip

# Unauthenticated endpoints: the only scope available is the caller's IP, and
# these are exactly the routes that need one (credential stuffing, reset-email
# flooding). Default rule: 5 requests per 15 minutes per IP.
AuthRateLimit = make_anonymous_rate_limit_dep(RateLimitCategory.AUTH)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/login",
    response_model=Token,
    dependencies=[AuthRateLimit],
)
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    user_service: UserSvc,
    session_service: SessionSvc,
    db: DBSession,
) -> Any:
    """OAuth2 password login, returns access and refresh tokens."""
    user = await user_service.authenticate(form_data.username, form_data.password)
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    # Track this login as a server-side session (enables remote logout).
    await session_service.create_session(
        user_id=user.id,
        refresh_token=refresh_token,
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    # FastAPI finalizes yield dependencies after the response can begin. Make
    # the token response a real durability boundary for the new session.
    await db.commit()
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AuthRateLimit],
)
async def register(
    user_in: UserCreate,
    user_service: UserSvc,
    db: DBSession,
) -> Any:
    """Register a new user."""
    user = await user_service.register(user_in)
    # A client commonly logs in immediately after registration; commit before
    # returning 201 so a concurrent request can observe the account.
    await db.commit()
    return user


@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: Request,
    body: RefreshTokenRequest,
    user_service: UserSvc,
    session_service: SessionSvc,
) -> Any:
    """Exchange a refresh token for a new access token."""

    session = await session_service.validate_refresh_token(body.refresh_token)
    if not session:
        raise AuthenticationError(message="Invalid or expired refresh token")

    user = await user_service.get_by_id(session.user_id)
    if not user.is_active:
        raise AuthenticationError(message="User account is disabled")

    access_token = create_access_token(subject=str(user.id))
    new_refresh_token = create_refresh_token(subject=str(user.id))

    await session_service.logout_by_refresh_token(body.refresh_token)
    await session_service.create_session(
        user_id=user.id,
        refresh_token=new_refresh_token,
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return Token(access_token=access_token, refresh_token=new_refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout(
    body: RefreshTokenRequest,
    session_service: SessionSvc,
) -> None:
    """Logout and invalidate the current session.

    Invalidates the refresh token, preventing further token refresh.
    """
    await session_service.logout_by_refresh_token(body.refresh_token)


@router.get("/me", response_model=UserRead)
async def get_current_user_info(current_user: CurrentUser) -> Any:
    """Get current authenticated user information."""
    return current_user
