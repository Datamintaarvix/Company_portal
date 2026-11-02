"""SQLAlchemy database models for employee attendance tracking and audited corrections."""

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.employee import Employee


class AttendanceStatus(str, enum.Enum):
    """Allowed attendance operational statuses."""

    PRESENT = "present"        # Regular completed or in-progress shift
    INCOMPLETE = "incomplete"  # Past shift where employee failed to check out
    HALF_DAY = "half_day"      # Short shift or partial day
    ABSENT = "absent"          # Recorded absence


class AttendanceRecord(Base):
    """Daily attendance record tracking check-in and check-out per employee workday.

    Work_date represents the calendar day in company timezone (Asia/Kolkata).
    Enforces exactly one attendance record per employee per local workday at the database level.
    """

    __tablename__ = "attendance_records"

    __table_args__ = (
        # Database-level unique constraint preventing duplicate check-ins on the same workday
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_employee_work_date"),
        # Chronological validity: checkout must never precede checkin
        CheckConstraint(
            "check_out_at IS NULL OR check_out_at >= check_in_at",
            name="check_attendance_checkout_after_checkin",
        ),
        # Valid status constraint
        CheckConstraint(
            "status IN ('present', 'incomplete', 'half_day', 'absent')",
            name="check_valid_attendance_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=AttendanceStatus.PRESENT.value, nullable=False)
    is_weekend: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_corrected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shift_code: Mapped[str] = mapped_column(String(20), default="GENERAL", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="attendance_records",
    )
    corrections: Mapped[List["AttendanceCorrectionLog"]] = relationship(
        "AttendanceCorrectionLog",
        back_populates="attendance_record",
        cascade="all, delete-orphan",
        order_by="desc(AttendanceCorrectionLog.corrected_at)",
    )

    def __repr__(self) -> str:
        return f"<AttendanceRecord id={self.id} emp={self.employee_id} date={self.work_date} status={self.status}>"


class AttendanceCorrectionLog(Base):
    """Immutable audit trail of all manual attendance corrections made by HR or Admin.

    Captures the modifying actor, exact timestamp, justification reason, and before/after values.
    """

    __tablename__ = "attendance_correction_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    attendance_record_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    corrected_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)

    # Before values
    previous_check_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_check_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # After values
    new_check_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    new_check_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Relationships
    attendance_record: Mapped["AttendanceRecord"] = relationship(
        "AttendanceRecord",
        back_populates="corrections",
    )
    corrector: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[corrected_by_id],
    )

    def __repr__(self) -> str:
        return f"<AttendanceCorrectionLog id={self.id} record={self.attendance_record_id} by={self.corrected_by_id}>"
