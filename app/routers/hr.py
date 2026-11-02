"""HR operations router for employee account provisioning."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import require_hr_or_admin
from app.db.session import get_db
from app.dependencies.auth import verify_csrf
from app.models.employee import Employee, UserRole
from app.schemas.auth import EmployeeCreate, EmployeeProfileResponse
from app.services.auth import provision_employee

router = APIRouter(prefix="/hr", tags=["HR Operations"])


@router.post(
    "/employees",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision Employee Account (HR & Admin)",
    responses={
        201: {"description": "Employee account provisioned successfully."},
        400: {"description": "Validation error (duplicate email, weak password, etc.)."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: regular employees or HR attempting to create an admin."},
    },
)
async def create_employee_account(
    payload: EmployeeCreate,
    current_user: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> EmployeeProfileResponse:
    """Provision a new employee or HR account.

    Accessible to HR and Admin roles.
    Enforces that HR personnel cannot assign the 'admin' role.
    Passwords and hashes are never returned in the response.
    """
    # Enforce role hierarchy: HR cannot create Admins
    if current_user.role == UserRole.HR.value and payload.role.lower() == UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HR personnel are not authorized to create administrator accounts",
        )

    try:
        new_employee = provision_employee(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return EmployeeProfileResponse.model_validate(new_employee)
