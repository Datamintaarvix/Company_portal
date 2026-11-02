"""Pydantic schemas for attendance operations, reporting, and audited corrections."""

from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.attendance import AttendanceStatus


class CheckInRequest(BaseModel):
    """Optional payload for self-service check-in."""

    notes: Optional[str] = Field(None, max_length=255, description="Optional notes or remarks for check-in")


class CheckOutRequest(BaseModel):
    """Optional payload for self-service check-out."""

    notes: Optional[str] = Field(None, max_length=255, description="Optional notes or remarks for check-out")


class AttendanceCorrectionRequest(BaseModel):
    """Payload for HR/Admin manual attendance correction with mandatory audit reason."""

    reason: str = Field(..., min_length=3, max_length=255, description="Mandatory justification reason")
    check_in_at: Optional[datetime] = Field(None, description="Corrected check-in timestamp")
    check_out_at: Optional[datetime] = Field(None, description="Corrected check-out timestamp")
    status: Optional[str] = Field(None, description="Corrected operational status")
    notes: Optional[str] = Field(None, max_length=255, description="Optional notes")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 3:
            raise ValueError("Correction reason must be at least 3 non-whitespace characters")
        return clean

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip().lower()
        allowed = {s.value for s in AttendanceStatus}
        if clean not in allowed:
            raise ValueError(f"Invalid status '{value}'. Allowed: {', '.join(sorted(allowed))}")
        return clean


class AttendanceCorrectionLogResponse(BaseModel):
    """Audit log entry showing who changed an attendance record, when, and why."""

    id: int
    attendance_record_id: int
    corrected_by_id: Optional[int] = None
    corrected_by_name: Optional[str] = None
    corrected_at: datetime
    corrected_at_ist: Optional[str] = None
    reason: str
    previous_check_in_at: Optional[datetime] = None
    previous_check_out_at: Optional[datetime] = None
    previous_status: Optional[str] = None
    new_check_in_at: Optional[datetime] = None
    new_check_out_at: Optional[datetime] = None
    new_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AttendanceResponse(BaseModel):
    """Full attendance record response schema."""

    id: int
    employee_id: int
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department: Optional[str] = None
    work_date: date
    work_date_formatted: Optional[str] = None
    check_in_at: datetime
    check_in_ist: Optional[str] = None
    check_out_at: Optional[datetime] = None
    check_out_ist: Optional[str] = None
    status: str
    is_weekend: bool
    is_corrected: bool
    shift_code: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    corrections: List[AttendanceCorrectionLogResponse] = []

    model_config = ConfigDict(from_attributes=True)


class TodayAttendanceResponse(BaseModel):
    """Status summary of the employee's current workday attendance."""

    work_date: date
    is_weekend: bool
    has_checked_in: bool
    has_checked_out: bool
    current_status: Optional[str] = None
    record: Optional[AttendanceResponse] = None
    server_time_ist: str


class PaginatedAttendanceResponse(BaseModel):
    """Paginated attendance history or company report."""

    items: List[AttendanceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
