"""Pydantic schemas for employee directory, updates, and role/status operations."""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from app.models.employee import UserRole
from app.schemas.auth import EmployeeProfileResponse


class EmployeeUpdateRequest(BaseModel):
    """Schema for updating employee details.

    Normal employees may only submit updates to 'name'.
    HR and Admin may submit updates to 'name' and 'department'.
    Mass assignment of employee_code, role, or is_active is disallowed.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Full name of the employee")
    department: Optional[str] = Field(None, max_length=100, description="Assigned department")


class EmployeeRoleUpdate(BaseModel):
    """Schema for administrative role assignment."""

    role: str = Field(..., description="Target role: 'employee', 'hr', or 'admin'")

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        clean_role = value.strip().lower()
        valid_roles = {r.value for r in UserRole}
        if clean_role not in valid_roles:
            raise ValueError(f"Invalid role '{value}'. Allowed roles: {', '.join(sorted(valid_roles))}")
        return clean_role


class EmployeeStatusUpdate(BaseModel):
    """Schema for administrative account activation/deactivation."""

    is_active: bool = Field(..., description="Active status of the account")


class PaginatedEmployeesResponse(BaseModel):
    """Paginated list of employee profiles for directory viewing."""

    items: List[EmployeeProfileResponse]
    total: int
    skip: int
    limit: int
