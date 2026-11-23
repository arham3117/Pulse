"""Pydantic v2 request/response schemas for the JSON API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Method = Literal["GET", "HEAD"]


class _StatusRange(BaseModel):
    @model_validator(mode="after")
    def _check(self) -> Self:
        lo = getattr(self, "expected_status_min", None)
        hi = getattr(self, "expected_status_max", None)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("expected_status_min must be <= expected_status_max")
        if getattr(self, "method", None) == "HEAD" and getattr(self, "keyword", None):
            raise ValueError("keyword checks need a response body; use GET, not HEAD")
        return self


class MonitorCreate(_StatusRange):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    method: Method = "GET"
    interval_seconds: int = Field(60, ge=30, le=3600)
    timeout_ms: int = Field(5000, ge=1000, le=30000)
    expected_status_min: int = Field(200, ge=100, le=599)
    expected_status_max: int = Field(399, ge=100, le=599)
    keyword: str | None = Field(None, min_length=1, max_length=200)
    failure_threshold: int = Field(3, ge=1, le=10)
    is_public: bool = True


class MonitorUpdate(_StatusRange):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=100)
    url: str | None = Field(None, min_length=1, max_length=2048)
    method: Method | None = None
    interval_seconds: int | None = Field(None, ge=30, le=3600)
    timeout_ms: int | None = Field(None, ge=1000, le=30000)
    expected_status_min: int | None = Field(None, ge=100, le=599)
    expected_status_max: int | None = Field(None, ge=100, le=599)
    keyword: str | None = Field(None, max_length=200)
    failure_threshold: int | None = Field(None, ge=1, le=10)
    is_public: bool | None = None


class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    method: str
    interval_seconds: int
    timeout_ms: int
    expected_status_min: int
    expected_status_max: int
    keyword: str | None
    failure_threshold: int
    is_active: bool
    is_public: bool
    status: str
    consecutive_failures: int
    last_checked_at: datetime | None
    next_check_at: datetime
    tls_expires_at: datetime | None
    tls_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CheckResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scheduled_for: datetime
    checked_at: datetime
    success: bool
    status_code: int | None
    response_time_ms: int | None
    error_type: str | None
    error_message: str | None
    worker_id: str


class CheckResultPage(BaseModel):
    items: list[CheckResultOut]
    next_before: datetime | None = Field(
        None, description="Pass as ?before= to fetch the next (older) page."
    )


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    monitor_id: uuid.UUID
    started_at: datetime
    resolved_at: datetime | None
    first_error: str
    notified_open_at: datetime | None
    notified_resolved_at: datetime | None


class MonitorStats(BaseModel):
    monitor_id: uuid.UUID
    uptime_24h: float | None = Field(None, description="Percent of successful checks, 0-100")
    uptime_7d: float | None = None
    uptime_30d: float | None = None
    avg_response_ms_24h: float | None = None
    p95_response_ms_24h: float | None = None
    checks_24h: int = 0


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
