"""FastAPI dependencies: settings, DB sessions, API-key auth, and the error type."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pulse.core.config import Settings


class ApiError(Exception):
    """Raised by handlers; rendered as ``{"error": {"code": ..., "message": ...}}``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def _key_matches(provided: str | None, settings: Settings) -> bool:
    expected = settings.admin_api_key.get_secret_value()
    if not expected or not provided:
        return False
    # Constant-time comparison so response timing doesn't leak the key.
    return hmac.compare_digest(provided.encode(), expected.encode())


def optional_admin(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> bool:
    """True with a valid key, False without one, 401 with a wrong one."""
    if x_api_key is None:
        return False
    if not _key_matches(x_api_key, settings):
        raise ApiError(401, "unauthorized", "invalid API key")
    return True


def require_admin(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if not _key_matches(x_api_key, settings):
        raise ApiError(401, "unauthorized", "a valid X-API-Key header is required")


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
IsAdmin = Annotated[bool, Depends(optional_admin)]
RequireAdmin = Depends(require_admin)
