"""Employee SQLAlchemy ORM model for Company Portal."""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.attendance import AttendanceRecord
    from app.models.leave import CompOffClaim, LeaveBalance, LeaveRequest
    from app.models.session import Session


class UserRole(str, enum.Enum):
    """Allowed user roles within the organization."""

    EMPLOYEE = "employee"
    HR = "hr"
    ADMIN = "admin"


class Employee(Base):
    """Employee database model representing all company staff accounts."""

    __tablename__ = "employees"

    __table_args__ = (
        CheckConstraint(
            "role IN ('employee', 'hr', 'admin')",
            name="check_valid_employee_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default=UserRole.EMPLOYEE.value, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sessions: Mapped[List["Session"]] = relationship(
        "Session",
        back_populates="employee",
        cascade="all, delete-orphan",
    )
    attendance_records: Mapped[List["AttendanceRecord"]] = relationship(
        "AttendanceRecord",
        foreign_keys="AttendanceRecord.employee_id",
        back_populates="employee",
        cascade="all, delete-orphan",
    )
    leave_balances: Mapped[List["LeaveBalance"]] = relationship(
        "LeaveBalance",
        foreign_keys="LeaveBalance.employee_id",
        back_populates="employee",
    )
    leave_requests: Mapped[List["LeaveRequest"]] = relationship(
        "LeaveRequest",
        foreign_keys="LeaveRequest.employee_id",
        back_populates="employee",
    )
    comp_off_claims: Mapped[List["CompOffClaim"]] = relationship(
        "CompOffClaim",
        foreign_keys="CompOffClaim.employee_id",
        back_populates="employee",
    )

    def __repr__(self) -> str:
        return f"<Employee id={self.id} code={self.employee_code} email={self.email} role={self.role}>"
