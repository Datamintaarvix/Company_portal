"""Employees router for directory listings, self-service profile, and record viewing/updating."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import require_authenticated, require_hr_or_admin
from app.db.session import get_db
from app.dependencies.auth import verify_csrf
from app.models.employee import Employee, UserRole
from app.schemas.auth import EmployeeProfileResponse
from app.schemas.employee import EmployeeUpdateRequest, PaginatedEmployeesResponse

router = APIRouter(prefix="/employees", tags=["Employees"])


@router.get(
    "/me",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Employee Profile",
    responses={
        200: {"description": "Current employee profile details."},
        401: {"description": "Unauthenticated or inactive account."},
    },
)
async def get_my_profile(
    current_employee: Employee = Depends(require_authenticated),
) -> EmployeeProfileResponse:
    """Return the profile for the currently authenticated employee.

    Available to all authenticated roles (employee, hr, admin).
    """
    return EmployeeProfileResponse.model_validate(current_employee)


@router.get(
    "",
    response_model=PaginatedEmployeesResponse,
    status_code=status.HTTP_200_OK,
    summary="List Employees Directory (HR & Admin)",
    responses={
        200: {"description": "Paginated employee directory."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: regular employees cannot list the directory."},
    },
)
async def list_employees(
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Page size limit (max 100)"),
    department: Optional[str] = Query(None, description="Filter by department"),
    role: Optional[str] = Query(None, description="Filter by role"),
    search: Optional[str] = Query(None, description="Search by name, email, or employee code"),
    _: Employee = Depends(require_hr_or_admin),
    db: Session = Depends(get_db),
) -> PaginatedEmployeesResponse:
    """Retrieve a paginated directory of company employees.

    Restricted to HR and Admin roles. Sensitive fields such as password hashes
    and session tokens are excluded.
    """
    query = select(Employee)

    if department:
        query = query.where(Employee.department == department.strip())

    if role:
        query = query.where(Employee.role == role.strip().lower())

    if search:
        search_pattern = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Employee.name).like(search_pattern),
                func.lower(Employee.email).like(search_pattern),
                func.lower(Employee.employee_code).like(search_pattern),
            )
        )

    # Count total matching rows
    count_query = select(func.count()).select_from(query.subquery())
    total_count = db.scalar(count_query) or 0

    # Paginate rows
    results = db.scalars(
        query.order_by(Employee.id.asc()).offset(skip).limit(limit)
    ).all()

    return PaginatedEmployeesResponse(
        items=[EmployeeProfileResponse.model_validate(emp) for emp in results],
        total=total_count,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{employee_id}",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Employee Record by ID",
    responses={
        200: {"description": "Employee record profile."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: regular employees cannot view others' records."},
        404: {"description": "Employee not found."},
    },
)
async def get_employee_by_id(
    employee_id: int,
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> EmployeeProfileResponse:
    """Retrieve an employee record by ID.

    Rules:
    - Employees can only access their own record (employee_id == current_employee.id).
    - HR and Admin can access any employee record.
    """
    # Enforce self-access for regular employees
    if current_employee.role == UserRole.EMPLOYEE.value and current_employee.id != employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you may only view your own employee record",
        )

    employee = db.execute(select(Employee).where(Employee.id == employee_id)).scalar_one_or_none()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with ID {employee_id} not found",
        )

    return EmployeeProfileResponse.model_validate(employee)


@router.patch(
    "/{employee_id}",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Permitted Employee Fields",
    responses={
        200: {"description": "Employee record updated successfully."},
        400: {"description": "Invalid input."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden field or target record."},
        404: {"description": "Employee not found."},
    },
)
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdateRequest,
    current_employee: Employee = Depends(require_authenticated),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> EmployeeProfileResponse:
    """Update permitted fields for an employee.

    Enforces strict field-level RBAC:
    - Regular employee: Can only modify their own record (id matching) and only 'name'.
    - HR / Admin: Can update 'name' and 'department' for employees.
    - Role and status changes must use dedicated admin endpoints.
    """
    # Check if target exists
    target = db.execute(select(Employee).where(Employee.id == employee_id)).scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with ID {employee_id} not found",
        )

    if current_employee.role == UserRole.EMPLOYEE.value:
        if current_employee.id != employee_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: you may only modify your own profile",
            )
        if payload.department is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Employees are not authorized to modify their department",
            )
        if payload.date_of_joining is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Employees are not authorized to modify their date of joining",
            )
        if payload.name is not None:
            target.name = payload.name.strip()
    else:
        # HR or Admin
        if payload.name is not None:
            target.name = payload.name.strip()
        if payload.department is not None:
            target.department = payload.department.strip() if payload.department else None
        if payload.date_of_joining is not None:
            target.date_of_joining = payload.date_of_joining

    db.commit()
    db.refresh(target)
    return EmployeeProfileResponse.model_validate(target)
