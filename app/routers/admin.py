"""Administrative API endpoints for employee role updates and account activation/deactivation."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.permissions import require_admin
from app.db.session import get_db
from app.dependencies.auth import verify_csrf
from app.models.employee import Employee, UserRole
from app.models.session import Session as DbSession
from app.schemas.auth import EmployeeProfileResponse
from app.schemas.employee import EmployeeRoleUpdate, EmployeeStatusUpdate

router = APIRouter(prefix="/admin/employees", tags=["Administration"])


@router.patch(
    "/{employee_id}/role",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Employee Role (Admin Only)",
    responses={
        200: {"description": "Employee role updated successfully."},
        400: {"description": "Self-demotion disallowed or invalid input."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: requires administrator privileges."},
        404: {"description": "Employee not found."},
    },
)
async def update_employee_role(
    employee_id: int,
    payload: EmployeeRoleUpdate,
    current_admin: Employee = Depends(require_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> EmployeeProfileResponse:
    """Update an employee's organizational role.

    Restricted strictly to administrators.
    Prevents self-demotion to avoid administrator lockout.
    """
    target = db.execute(select(Employee).where(Employee.id == employee_id)).scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with ID {employee_id} not found",
        )

    # Prevent accidental self-demotion
    if target.id == current_admin.id and payload.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot demote their own account",
        )

    target.role = payload.role
    db.commit()
    db.refresh(target)
    return EmployeeProfileResponse.model_validate(target)


@router.patch(
    "/{employee_id}/status",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate or Deactivate Employee Account (Admin Only)",
    responses={
        200: {"description": "Account status updated successfully."},
        400: {"description": "Self-deactivation disallowed."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: requires administrator privileges."},
        404: {"description": "Employee not found."},
    },
)
async def update_employee_status(
    employee_id: int,
    payload: EmployeeStatusUpdate,
    current_admin: Employee = Depends(require_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> EmployeeProfileResponse:
    """Activate or deactivate an employee account.

    Restricted strictly to administrators.
    When an account is deactivated:
    - Status is updated to is_active=False.
    - All active sessions for this employee are immediately revoked/deleted in PostgreSQL.
    Prevents self-deactivation to avoid administrator lockout.
    """
    target = db.execute(select(Employee).where(Employee.id == employee_id)).scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with ID {employee_id} not found",
        )

    # Prevent accidental self-deactivation
    if target.id == current_admin.id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot deactivate their own account",
        )

    target.is_active = payload.is_active

    # If deactivating, immediately revoke all server-side sessions
    if not payload.is_active:
        db.execute(delete(DbSession).where(DbSession.employee_id == target.id))

    db.commit()
    db.refresh(target)
    return EmployeeProfileResponse.model_validate(target)
