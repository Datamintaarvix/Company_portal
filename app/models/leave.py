"""Leave management database models for Company Portal.

Defines the seven core Leave Management models:
1. LeaveCategory
2. CompanyHoliday
3. LeaveBalance
4. LeaveRequest
5. CompOffClaim
6. LeaveBalanceTransaction
7. LeaveAuditLog
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.employee import Employee


class LeaveCategoryCode(str, enum.Enum):
    """Recognized leave category codes."""

    CASUAL = "CASUAL"
    SICK = "SICK"
    EARNED = "EARNED"
    UNPAID = "UNPAID"
    COMP_OFF = "COMP_OFF"
    SABBATICAL = "SABBATICAL"


class LeaveRequestStatus(str, enum.Enum):
    """Operational lifecycle statuses for leave requests."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class HalfDayPeriod(str, enum.Enum):
    """Allowed half-day periods."""

    MORNING = "morning"
    AFTERNOON = "afternoon"


class CompOffClaimStatus(str, enum.Enum):
    """Statuses for employee compensatory off claims."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class CompOffCreditStatus(str, enum.Enum):
    """Statuses for individual compensatory off credit lifecycle."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PARTIALLY_EXHAUSTED = "PARTIALLY_EXHAUSTED"
    EXHAUSTED = "EXHAUSTED"
    EXPIRED = "EXPIRED"


class TransactionType(str, enum.Enum):
    """All supported balance transaction ledger operation types."""

    RESERVE = "RESERVE"
    COMMIT = "COMMIT"
    RELEASE = "RELEASE"
    REFUND = "REFUND"
    ACCRUAL = "ACCRUAL"
    CARRY_FORWARD = "CARRY_FORWARD"
    ADJUSTMENT = "ADJUSTMENT"
    COMPOFF_CREDIT = "COMPOFF_CREDIT"
    COMPOFF_EXPIRY = "COMPOFF_EXPIRY"


class LeaveCategory(Base):
    """Leave category configuration defining policies, quotas, and rules."""

    __tablename__ = "leave_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    annual_quota: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_carry_forward: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    accrual_rate_monthly: Mapped[Decimal] = mapped_column(Numeric(3, 1), default=Decimal("0.0"), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    balances: Mapped[List["LeaveBalance"]] = relationship("LeaveBalance", back_populates="category")
    requests: Mapped[List["LeaveRequest"]] = relationship("LeaveRequest", back_populates="category")

    def __repr__(self) -> str:
        return f"<LeaveCategory id={self.id} code={self.code} quota={self.annual_quota}>"


class CompanyHoliday(Base):
    """Company observed public and corporate holidays."""

    __tablename__ = "company_holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holiday_date: Mapped[date] = mapped_column(Date, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<CompanyHoliday id={self.id} date={self.holiday_date} name={self.name}>"


class LeaveBalance(Base):
    """Annual leave balance ledger per employee and leave category."""

    __tablename__ = "leave_balances"

    __table_args__ = (
        UniqueConstraint("employee_id", "category_id", "year", name="uq_leave_balance_emp_cat_year"),
        CheckConstraint(
            "entitled_days >= 0 AND carried_forward_days >= 0 AND accrued_days >= 0 AND used_days >= 0 AND reserved_days >= 0",
            name="ck_balance_buckets_non_negative",
        ),
        CheckConstraint(
            "(entitled_days + carried_forward_days + accrued_days) >= (used_days + reserved_days)",
            name="ck_balance_available_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    entitled_days: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    carried_forward_days: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    accrued_days: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    used_days: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    reserved_days: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id], back_populates="leave_balances")
    category: Mapped["LeaveCategory"] = relationship("LeaveCategory", back_populates="balances")
    transactions: Mapped[List["LeaveBalanceTransaction"]] = relationship("LeaveBalanceTransaction", back_populates="balance")

    @property
    def total_credited_days(self) -> Decimal:
        """Total credited days across entitlements, carry forwards, and accruals."""
        return self.entitled_days + self.carried_forward_days + self.accrued_days

    @property
    def available_days(self) -> Decimal:
        """Net available days that can be requested."""
        return self.total_credited_days - (self.used_days + self.reserved_days)

    def __repr__(self) -> str:
        return (
            f"<LeaveBalance id={self.id} emp={self.employee_id} cat={self.category_id} "
            f"year={self.year} available={self.available_days}>"
        )


class LeaveRequest(Base):
    """Leave request submitted by an employee with review tracking."""

    __tablename__ = "leave_requests"

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_leave_request_dates_order"),
        CheckConstraint("total_days > 0", name="ck_leave_request_total_days_positive"),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')",
            name="ck_leave_request_valid_status",
        ),
        CheckConstraint(
            "reviewed_by_id IS NULL OR reviewed_by_id != employee_id",
            name="ck_leave_request_anti_self_approval",
        ),
        CheckConstraint(
            "(is_half_day = FALSE AND half_day_period IS NULL AND total_days >= 1.0) OR "
            "(is_half_day = TRUE AND half_day_period IN ('morning', 'afternoon') AND total_days = 0.5 AND start_date = end_date)",
            name="ck_leave_request_half_day_shape",
        ),
        Index(
            "uq_leave_request_full_day_single",
            "employee_id",
            "start_date",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'APPROVED') AND is_half_day = FALSE AND start_date = end_date"),
        ),
        Index(
            "uq_leave_request_half_day_slot",
            "employee_id",
            "start_date",
            "half_day_period",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'APPROVED') AND is_half_day = TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    is_half_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    half_day_period: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    total_days: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=LeaveRequestStatus.PENDING.value, nullable=False, index=True
    )
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id], back_populates="leave_requests")
    reviewer: Mapped[Optional["Employee"]] = relationship("Employee", foreign_keys=[reviewed_by_id])
    category: Mapped["LeaveCategory"] = relationship("LeaveCategory", back_populates="requests")
    balance_transactions: Mapped[List["LeaveBalanceTransaction"]] = relationship(
        "LeaveBalanceTransaction", back_populates="leave_request"
    )

    def __repr__(self) -> str:
        return (
            f"<LeaveRequest id={self.id} emp={self.employee_id} dates={self.start_date}..{self.end_date} "
            f"days={self.total_days} status={self.status}>"
        )


class CompOffClaim(Base):
    """Employee claim for compensatory off credit earned from extra work on non-working days."""

    __tablename__ = "comp_off_claims"

    __table_args__ = (
        CheckConstraint("days_claimed IN (0.5, 1.0)", name="ck_comp_off_days_claimed"),
        CheckConstraint("days_approved IS NULL OR days_approved IN (0.5, 1.0)", name="ck_comp_off_days_approved"),
        CheckConstraint("remaining_days >= 0", name="ck_comp_off_remaining_non_negative"),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')",
            name="ck_comp_off_valid_status",
        ),
        CheckConstraint(
            "credit_status IN ('PENDING', 'ACTIVE', 'PARTIALLY_EXHAUSTED', 'EXHAUSTED', 'EXPIRED')",
            name="ck_comp_off_valid_credit_status",
        ),
        CheckConstraint(
            "reviewed_by_id IS NULL OR reviewed_by_id != employee_id",
            name="ck_comp_off_anti_self_approval",
        ),
        Index(
            "uq_comp_off_claim_emp_date",
            "employee_id",
            "work_date",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'APPROVED')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    days_claimed: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=CompOffClaimStatus.PENDING.value, nullable=False, index=True
    )
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    days_approved: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 1), nullable=True)
    remaining_days: Mapped[Decimal] = mapped_column(Numeric(3, 1), default=Decimal("0.0"), nullable=False)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    credit_status: Mapped[str] = mapped_column(
        String(20), default=CompOffCreditStatus.PENDING.value, nullable=False, index=True
    )

    balance_transaction_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(
            "leave_balance_transactions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_comp_off_claims_balance_tx",
        ),
        unique=True,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id], back_populates="comp_off_claims")
    reviewer: Mapped[Optional["Employee"]] = relationship("Employee", foreign_keys=[reviewed_by_id])
    balance_transaction: Mapped[Optional["LeaveBalanceTransaction"]] = relationship(
        "LeaveBalanceTransaction",
        foreign_keys=[balance_transaction_id],
        post_update=True,
    )

    def __repr__(self) -> str:
        return (
            f"<CompOffClaim id={self.id} emp={self.employee_id} date={self.work_date} "
            f"claimed={self.days_claimed} status={self.status} remaining={self.remaining_days}>"
        )


class LeaveBalanceTransaction(Base):
    """Immutable balance ledger recording every change to employee leave balances."""

    __tablename__ = "leave_balance_transactions"

    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('RESERVE', 'COMMIT', 'RELEASE', 'REFUND', 'ACCRUAL', 'CARRY_FORWARD', 'ADJUSTMENT', 'COMPOFF_CREDIT', 'COMPOFF_EXPIRY')",
            name="ck_tx_valid_type",
        ),
        Index(
            "uq_tx_request_type",
            "leave_request_id",
            "transaction_type",
            unique=True,
            postgresql_where=text("leave_request_id IS NOT NULL"),
        ),
        Index(
            "uq_tx_balance_accrual_period",
            "balance_id",
            "transaction_type",
            "accrual_period",
            unique=True,
            postgresql_where=text("accrual_period IS NOT NULL"),
        ),
        Index(
            "uq_tx_compoff_claim",
            "comp_off_claim_id",
            "transaction_type",
            unique=True,
            postgresql_where=text("comp_off_claim_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    balance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_balances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    leave_request_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("leave_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    comp_off_claim_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("comp_off_claims.id", ondelete="SET NULL"), nullable=True, index=True
    )

    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    accrual_period: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)

    balance_before: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    balance: Mapped["LeaveBalance"] = relationship("LeaveBalance", back_populates="transactions")
    leave_request: Mapped[Optional["LeaveRequest"]] = relationship("LeaveRequest", back_populates="balance_transactions")
    comp_off_claim: Mapped[Optional["CompOffClaim"]] = relationship("CompOffClaim", foreign_keys=[comp_off_claim_id])
    created_by: Mapped["Employee"] = relationship("Employee", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return (
            f"<LeaveBalanceTransaction id={self.id} balance={self.balance_id} type={self.transaction_type} "
            f"amount={self.amount} ({self.balance_before} -> {self.balance_after})>"
        )


class LeaveAuditLog(Base):
    """Immutable audit trail for all leave management domain events."""

    __tablename__ = "leave_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    previous_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actor: Mapped["Employee"] = relationship("Employee", foreign_keys=[actor_id])

    def __repr__(self) -> str:
        return f"<LeaveAuditLog id={self.id} entity={self.entity_type}:{self.entity_id} action={self.action} actor={self.actor_id}>"
