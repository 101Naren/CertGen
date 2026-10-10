"""Pydantic models: what the API accepts and returns.

Validation happens in two layers:
  1. JobCreate checks the *request* (title present, list not empty, size cap).
     If this fails FastAPI answers 422 and nothing is stored.
  2. RecipientIn checks each *row* separately (see services.py). A bad row is
     stored as "invalid" and does not stop the other rows.
"""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.config import settings

CertStatusLiteral = Literal["pending", "generated", "failed", "invalid"]


class RecipientIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class JobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=200)
    issue_date: date = Field(default_factory=date.today)
    # Rows are kept as raw values here so one bad row cannot reject the request.
    recipients: list[Any] = Field(min_length=1, max_length=settings.max_recipients)


class JobCreated(BaseModel):
    job_id: str
    status: str
    total: int


class RowError(BaseModel):
    row_index: int
    status: str
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobStatusOut(BaseModel):
    job_id: str
    title: str
    status: str
    total: int
    generated: int
    failed: int
    invalid: int
    pending: int
    progress_percent: int
    errors: list[RowError]


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
