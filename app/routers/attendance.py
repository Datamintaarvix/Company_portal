"""Attendance API router for employee self-service and HR/Admin management."""

from datetime import date
from typing import Optional
from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import require_authenticated, require_hr_or_admin
from app.db.session import get_db
from app.dependencies.auth import verify_csrf
from app.models.employee import Employee
from app.schemas.attendance import (
    AttendanceCorrectionRequest,
    AttendanceResponse,
    CheckInRequest,
    CheckOutRequest,
    PaginatedAttendanceResponse,
    TodayAttendanceResponse,
)
from app.services.attendance import (
    check_in_employee,
    check_out_employee,
    correct_attendance_record,
    get_company_attendance_report,
    get_employee_attendance_history,
    get_today_status,
)

router = APIRouter(prefix="/attendance", tags=["Attendance Management"])


@router.post(
    "/check-in",
    response_model=AttendanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Employee Check-in",
    responses={
        201: {"description": "Check-in successful."},
        400: {"description": "Bad request."},
        401: {"description": "Unauthenticated or inactive account."},
        403: {"description": "CSRF token missing or invalid."},
        409: {"description": "Already checked in for today."},
    },
)
async def check_in(
    payload: Optional[CheckInRequest] = Body(None),
    current_employee: Employee = Depends(require_authenticated),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> AttendanceResponse:
    """Record daily check-in for the currently authenticated employee.

    Identity is derived strictly from the active session.
    Work date is computed in Asia/Kolkata timezone.
    Enforces maximum of one check-in per employee per workday.
    """
    notes = payload.notes if payload else None
    return check_in_employee(db=db, employee=current_employee, notes=notes)


@router.post(
    "/check-out",
    response_model=AttendanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Employee Check-out",
    responses={
        200: {"description": "Check-out successful."},
        400: {"description": "No active check-in for today or invalid timestamp ordering."},
        401: {"description": "Unauthenticated or inactive account."},
        403: {"description": "CSRF token missing or invalid."},
        409: {"description": "Already checked out for today."},
    },
)
async def check_out(
    payload: Optional[CheckOutRequest] = Body(None),
    current_employee: Employee = Depends(require_authenticated),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> AttendanceResponse:
    """Record daily check-out for the currently authenticated employee.

    Requires an existing check-in for the current workday.
    Rejects duplicate check-outs or checkout-before-checkin.
    """
    notes = payload.notes if payload else None
    return check_out_employee(db=db, employee=current_employee, notes=notes)


@router.get(
    "/today",
    response_model=TodayAttendanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Today's Shift Status",
    responses={
        200: {"description": "Today's attendance status and punch state."},
        401: {"description": "Unauthenticated or inactive account."},
    },
)
async def get_today(
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> TodayAttendanceResponse:
    """Retrieve today's shift and punch status for the authenticated employee."""
    return get_today_status(db=db, employee=current_employee)


@router.get(
    "/my",
    response_model=PaginatedAttendanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Personal Attendance History",
    responses={
        200: {"description": "Paginated personal attendance records."},
        400: {"description": "Invalid date range parameters."},
        401: {"description": "Unauthenticated or inactive account."},
    },
)
async def get_my_history(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    start_date: Optional[date] = Query(None, description="Filter on or after start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Filter on or before end date (YYYY-MM-DD)"),
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> PaginatedAttendanceResponse:
    """Retrieve personal attendance records for the authenticated employee.

    Strictly isolated: employees can only view their own attendance records.
    """
    return get_employee_attendance_history(
        db=db,
        employee=current_employee,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "",
    response_model=PaginatedAttendanceResponse,
    status_code=status.HTTP_200_OK,
    summary="List Company Attendance (HR & Admin)",
    responses={
        200: {"description": "Paginated company-wide attendance report."},
        400: {"description": "Invalid filter parameters."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: regular employees cannot access company attendance."},
    },
)
async def list_company_attendance(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    employee_id: Optional[int] = Query(None, description="Filter by employee ID"),
    department: Optional[str] = Query(None, description="Filter by employee department"),
    start_date: Optional[date] = Query(None, description="Filter on or after start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Filter on or before end date (YYYY-MM-DD)"),
    status: Optional[str] = Query(None, description="Filter by status (present, incomplete, half_day, absent)"),
    _: Employee = Depends(require_hr_or_admin),
    db: Session = Depends(get_db),
) -> PaginatedAttendanceResponse:
    """Retrieve company-wide attendance records with filtering and pagination.

    Restricted strictly to HR and Admin roles.
    """
    return get_company_attendance_report(
        db=db,
        page=page,
        page_size=page_size,
        employee_id=employee_id,
        department=department,
        start_date=start_date,
        end_date=end_date,
        status_filter=status,
    )


@router.patch(
    "/{id}/correct",
    response_model=AttendanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Audited Attendance Correction (HR & Admin)",
    responses={
        200: {"description": "Attendance record corrected and audited successfully."},
        400: {"description": "Validation error (missing reason, invalid timestamps, etc.)."},
        401: {"description": "Unauthenticated."},
        403: {"description": "Forbidden: insufficient permissions or invalid CSRF token."},
        404: {"description": "Attendance record not found."},
    },
)
async def correct_attendance(
    id: int = Path(..., description="ID of the attendance record to correct"),
    payload: AttendanceCorrectionRequest = ...,
    current_user: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> AttendanceResponse:
    """Manually correct an attendance record with a mandatory audit log entry.

    Restricted to HR and Admin roles.
    Creates an immutable audit log entry capturing modifying actor, timestamp,
    justification reason, and before/after values.
    """
    return correct_attendance_record(
        db=db,
        record_id=id,
        actor=current_user,
        payload=payload,
    )
