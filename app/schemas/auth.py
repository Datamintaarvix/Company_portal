"""Pydantic schemas for authentication and employee profiles."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    """Schema for employee login submission."""

    email: EmailStr = Field(..., description="Employee corporate email address")
    password: str = Field(..., min_length=1, description="Employee plaintext password")


class LoginResponse(BaseModel):
    """Safe response payload after successful login.

    Session cookies are dispatched via Set-Cookie headers; tokens and secrets
    are never leaked in the JSON response body.
    """

    message: str = "Login successful"
    user: "EmployeeProfileResponse"


class EmployeeProfileResponse(BaseModel):
    """Safe employee profile schema excluding secrets and password hashes."""

    id: int
    employee_code: str
    name: str
    email: str
    department: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Rebuild model to resolve recursive/forward references
LoginResponse.model_rebuild()


class LogoutResponse(BaseModel):
    """Response payload after successful logout."""

    message: str = "Logged out successfully"


class EmployeeCreate(BaseModel):
    """Internal schema for employee provisioning."""

    employee_code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr = Field(..., max_length=255)
    department: Optional[str] = Field(None, max_length=100)
    role: str = Field("employee", description="Role: employee, hr, or admin")
    password: str = Field(..., min_length=8)
