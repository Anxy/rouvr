"""
Shared kit - common response and event types.
Generated once per service and reused across modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic.generics import GenericModel

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page[T](GenericModel):
    """Generic paginated response wrapper."""

    items: list[T] = Field(..., description="List of records")
    total: int = Field(..., ge=0, description="Total number of records")
    limit: int = Field(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Page size",
    )
    offset: int = Field(0, ge=0, description="Page offset")
    has_more: bool = Field(..., description="Whether there are more records available")

    class Config:
        validate_assignment = True
        extra = "forbid"


class Envelope[T](GenericModel):
    """Generic response envelope wrapper."""

    data: T = Field(..., description="Response data")
    success: bool = Field(True, description="Operation success status")
    message: str | None = Field(None, description="Optional message")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Response timestamp",
    )

    class Config:
        validate_assignment = True
        extra = "forbid"


class ErrorEnvelope(BaseModel):
    """Standard error response envelope."""

    success: bool = Field(False, description="Always false for errors")
    error: str = Field(..., description="Error type/code")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(None, description="Additional error details")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Error timestamp",
    )

    class Config:
        validate_assignment = True
        extra = "forbid"


class CloudEvent[P](GenericModel):
    """CloudEvents v1.0 specification compliant event."""

    specversion: str = Field("1.0", description="CloudEvents specification version")
    type: str = Field(..., description="Event type")
    source: str = Field(..., description="Event source")
    subject: str | None = Field(None, description="Event subject")
    id: str = Field(..., description="Event unique identifier")
    time: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Event timestamp",
    )
    datacontenttype: str = Field("application/json", description="Data content type")
    data: P | None = Field(None, description="Event payload")

    class Config:
        validate_assignment = True
        extra = "forbid"
