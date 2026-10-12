from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RecipientData(BaseModel):
    """Validated per recipient (not per request) so one bad row doesn't reject the whole job."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    note: Optional[str] = Field(default=None, max_length=100)  # e.g. "with Distinction"


class JobCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "event_name": "Aereo Hackathon 2026",
                "issuer_name": "Aereo Club",
                "issue_date": "2026-10-12",
                "recipients": [
                    {"name": "Asha Kumar", "email": "asha@example.com", "note": "with Distinction"},
                    {"name": "Ravi Shankar"},
                ],
            }
        },
    )

    event_name: str = Field(min_length=1, max_length=150)
    issuer_name: str = Field(default="The Organizing Committee", min_length=1, max_length=150)
    issue_date: date = Field(default_factory=date.today)
    recipients: list[Any] = Field(min_length=1)


class _UTC(BaseModel):
    @field_validator("created_at", "completed_at", check_fields=False)
    @classmethod
    def _as_utc(cls, v: Optional[datetime]):
        # SQLite drops tzinfo; everything we store is UTC.
        return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


class FailureOut(BaseModel):
    position: int
    recipient_name: Optional[str]
    error: str


class JobOut(_UTC):
    id: str
    event_name: str
    status: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    completed_at: Optional[datetime]
    failures: list[FailureOut]
    failures_truncated: bool = False
    certificates_url: str
    download_all_url: str


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: Optional[str]
    recipient_email: Optional[str]
    status: str
    error: Optional[str]
    download_url: Optional[str]


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
