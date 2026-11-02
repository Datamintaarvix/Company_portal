"""Pydantic schemas for Leave Management operations, calculations, reviews, and audits."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.leave import (
    CompOffClaimStatus,
    CompOffCreditStatus,
    HalfDayPeriod,
    LeaveCategoryCode,
    LeaveRequestStatus,
    TransactionType,
)


# ============================================================================
# Leave Category Schemas
# ============================================================================

class LeaveCategoryResponse(BaseModel):
    """Configuration details for a leave category."""

    id: int
    code: str
    name: str
    description: Optional[str] = None
    annual_quota: Decimal
    is_paid: bool
    max_carry_forward: Decimal
    accrual_rate_monthly: Decimal
    requires_approval: bool
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Leave Calculation Schemas
# ============================================================================

class LeaveCalculationRequest(BaseModel):
    """Payload to preview day deduction and sandwich rule calculations."""

    category_code: LeaveCategoryCode = Field(..., description="Leave category code")
    start_date: date = Field(..., description="Start date of the leave")
    end_date: date = Field(..., description="End date of the leave")
    is_half_day: bool = Field(False, description="Whether this request is for a half-day")
    half_day_period: Optional[HalfDayPeriod] = Field(None, description="Morning or afternoon for half-day")

    @model_validator(mode="after")
    def validate_dates_and_half_day(self) -> "LeaveCalculationRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")

        if self.is_half_day:
            if self.start_date != self.end_date:
                raise ValueError("Half-day leave must start and end on the same calendar date")
            if not self.half_day_period:
                raise ValueError("half_day_period (morning or afternoon) is required for half-day leave")
        else:
            if self.half_day_period is not None:
                raise ValueError("half_day_period must be null for full-day leave")

        return self


class LeaveCalculationResponse(BaseModel):
    """Result of leave day calculation with sandwich breakdown."""

    total_days: Decimal
    calendar_days: int
    working_days: Decimal
    weekend_days: int
    holiday_days: int
    sandwiched_weekend_days: int
    sandwiched_holiday_days: int
    breakdown_notes: List[str]


# ============================================================================
# Leave Application Schemas
# ============================================================================

class LeaveApplyRequest(BaseModel):
    """Payload for employee submitting a leave application."""

    category_code: LeaveCategoryCode = Field(..., description="Category of leave applied for")
    start_date: date = Field(..., description="Start date of leave")
    end_date: date = Field(..., description="End date of leave")
    is_half_day: bool = Field(False, description="Whether this is a half-day leave")
    half_day_period: Optional[HalfDayPeriod] = Field(None, description="Morning or afternoon for half-day")
    reason: str = Field(..., min_length=3, max_length=500, description="Justification reason for the leave")

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 3:
            raise ValueError("Reason must be at least 3 non-whitespace characters")
        return clean

    @model_validator(mode="after")
    def validate_dates_and_half_day(self) -> "LeaveApplyRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")

        if self.is_half_day:
            if self.start_date != self.end_date:
                raise ValueError("Half-day leave must start and end on the same calendar date")
            if not self.half_day_period:
                raise ValueError("half_day_period (morning or afternoon) is required for half-day leave")
        else:
            if self.half_day_period is not None:
                raise ValueError("half_day_period must be null for full-day leave")

        return self


class LeaveReviewRequest(BaseModel):
    """Payload for HR or Manager approving or rejecting a leave request."""

    status: Optional[LeaveRequestStatus] = Field(None, description="Review decision: APPROVED or REJECTED")
    review_notes: Optional[str] = Field(None, max_length=500, description="Optional feedback or rejection reason")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[LeaveRequestStatus]) -> Optional[LeaveRequestStatus]:
        if value is not None and value not in (LeaveRequestStatus.APPROVED, LeaveRequestStatus.REJECTED):
            raise ValueError("Status must be either APPROVED or REJECTED")
        return value

    @field_validator("review_notes")
    @classmethod
    def clean_review_notes(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip()
        return clean if clean else None


class LeaveCancelRequest(BaseModel):
    """Payload for cancelling a leave request."""

    reason: str = Field(..., min_length=3, max_length=500, description="Reason for cancellation")

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 3:
            raise ValueError("Cancellation reason must be at least 3 non-whitespace characters")
        return clean


class LeaveRequestResponse(BaseModel):
    """Detailed response model for a leave request."""

    id: int
    employee_id: int
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    category_id: int
    category_code: str
    category_name: str
    start_date: date
    end_date: date
    is_half_day: bool
    half_day_period: Optional[str] = None
    total_days: Decimal
    reason: str
    status: str
    reviewed_by_id: Optional[int] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Leave Balance Schemas
# ============================================================================

class LeaveBalanceResponse(BaseModel):
    """Employee annual leave balance breakdown."""

    id: int
    employee_id: int
    category_id: int
    category_code: str
    category_name: str
    year: int
    entitled_days: Decimal
    carried_forward_days: Decimal
    accrued_days: Decimal
    used_days: Decimal
    reserved_days: Decimal
    total_credited_days: Decimal
    available_days: Decimal

    model_config = ConfigDict(from_attributes=True)


class LeaveBalanceAdjustmentRequest(BaseModel):
    """Payload for HR administrative adjustment to leave balance buckets."""

    employee_id: int = Field(..., description="Target employee ID")
    category_code: LeaveCategoryCode = Field(..., description="Leave category to adjust")
    year: int = Field(..., description="Calendar year of balance")
    bucket: str = Field(..., description="Bucket to adjust: entitled, carried_forward, or accrued")
    amount: Decimal = Field(..., description="Delta to apply (can be positive or negative)")
    reason: str = Field(..., min_length=5, max_length=255, description="Mandatory audit justification")

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        clean = value.strip().lower()
        if clean not in ("entitled", "carried_forward", "accrued"):
            raise ValueError("Bucket must be one of: 'entitled', 'carried_forward', 'accrued'")
        return clean

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("Adjustment amount must not be zero")
        return value

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 5:
            raise ValueError("Adjustment reason must be at least 5 non-whitespace characters")
        return clean


# ============================================================================
# Comp-Off Claim Schemas
# ============================================================================

class CompOffClaimCreateRequest(BaseModel):
    """Payload for employee submitting an extra-work Comp-Off claim."""

    work_date: date = Field(..., description="Date extra work was performed (weekend or holiday)")
    days_claimed: Decimal = Field(..., description="Days claimed (0.5 or 1.0)")
    reason: str = Field(..., min_length=3, max_length=500, description="Details of extra work performed")

    @field_validator("days_claimed")
    @classmethod
    def validate_days_claimed(cls, value: Decimal) -> Decimal:
        if value not in (Decimal("0.5"), Decimal("1.0")):
            raise ValueError("days_claimed must be either 0.5 or 1.0")
        return value

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 3:
            raise ValueError("Claim reason must be at least 3 non-whitespace characters")
        return clean


class CompOffClaimReviewRequest(BaseModel):
    """Payload for HR reviewing an extra-work claim."""

    status: Optional[CompOffClaimStatus] = Field(None, description="Decision: APPROVED or REJECTED")
    days_approved: Optional[Decimal] = Field(None, description="Days approved (0.5 or 1.0) if approved")
    review_notes: Optional[str] = Field(None, max_length=500, description="Optional review remarks")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[CompOffClaimStatus]) -> Optional[CompOffClaimStatus]:
        if value is not None and value not in (CompOffClaimStatus.APPROVED, CompOffClaimStatus.REJECTED):
            raise ValueError("Status must be either APPROVED or REJECTED")
        return value

    @model_validator(mode="after")
    def validate_days_approved(self) -> "CompOffClaimReviewRequest":
        if self.status == CompOffClaimStatus.APPROVED:
            if self.days_approved not in (Decimal("0.5"), Decimal("1.0")):
                raise ValueError("days_approved (0.5 or 1.0) is required when approving a claim")
        return self


class CompOffClaimResponse(BaseModel):
    """Details of an employee compensatory off claim."""

    id: int
    employee_id: int
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    work_date: date
    days_claimed: Decimal
    reason: str
    status: str
    reviewed_by_id: Optional[int] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    days_approved: Optional[Decimal] = None
    remaining_days: Decimal
    expiry_date: Optional[date] = None
    credit_status: str
    balance_transaction_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Ledger & Audit Schemas
# ============================================================================

class LeaveBalanceTransactionResponse(BaseModel):
    """Ledger transaction record showing balance mutation history."""

    id: int
    balance_id: int
    leave_request_id: Optional[int] = None
    comp_off_claim_id: Optional[int] = None
    transaction_type: str
    amount: Decimal
    accrual_period: Optional[str] = None
    balance_before: Decimal
    balance_after: Decimal
    created_by_id: int
    created_by_name: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeaveAuditLogResponse(BaseModel):
    """Audit log entry for leave management events."""

    id: int
    entity_type: str
    entity_id: int
    action: str
    actor_id: int
    actor_name: Optional[str] = None
    ip_address: Optional[str] = None
    previous_state: Optional[str] = None
    new_state: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
