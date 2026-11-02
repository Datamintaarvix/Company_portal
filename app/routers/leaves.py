"""Leave Management API router for employee self-service and HR/Admin administration."""

from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import require_authenticated, require_hr_or_admin
from app.core.timezone import get_current_work_date
from app.db.session import get_db
from app.dependencies.auth import verify_csrf
from app.models.employee import Employee, UserRole
from app.schemas.leave import (
    CompOffClaimCreateRequest,
    CompOffClaimResponse,
    CompOffClaimReviewRequest,
    LeaveApplyRequest,
    LeaveBalanceAdjustmentRequest,
    LeaveBalanceResponse,
    LeaveCalculationRequest,
    LeaveCalculationResponse,
    LeaveCancelRequest,
    LeaveRequestResponse,
    LeaveReviewRequest,
)
from app.services.leave import (
    adjust_leave_balance,
    approve_leave_request,
    calculate_leave_days,
    cancel_leave_request,
    format_compoff_claim_response,
    format_leave_balance_response,
    format_leave_request_response,
    get_compoff_claims,
    get_employee_balances,
    get_employee_leave_requests,
    get_leave_request_by_id,
    get_pending_leave_requests,
    reject_leave_request,
    review_compoff_claim,
    submit_compoff_claim,
    submit_leave_request,
)

router = APIRouter(prefix="/leaves", tags=["Leave Management"])


# ============================================================================
# Static Employee & Preview Endpoints
# ============================================================================

@router.post(
    "/calculate",
    response_model=LeaveCalculationResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview Leave Day Deduction",
    responses={
        200: {"description": "Calculation breakdown including working days and sandwich rule deductions."},
        400: {"description": "Invalid date range or parameters."},
        401: {"description": "Unauthenticated caller."},
    },
)
async def preview_leave_calculation(
    payload: LeaveCalculationRequest,
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> LeaveCalculationResponse:
    """Calculate and preview the exact leave days that will be deducted for a date range."""
    return calculate_leave_days(
        db=db,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_half_day=payload.is_half_day,
        half_day_period=payload.half_day_period.value if payload.half_day_period else None,
    )


@router.get(
    "/balance",
    response_model=List[LeaveBalanceResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Personal Leave Balances",
    responses={
        200: {"description": "Current leave balance sheet for all active leave categories."},
        401: {"description": "Unauthenticated caller."},
    },
)
async def get_my_balances(
    year: Optional[int] = Query(None, description="Calendar year (defaults to current year)"),
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> List[LeaveBalanceResponse]:
    """Retrieve the authenticated employee's annual leave balances and availability."""
    target_year = year or get_current_work_date().year
    balances = get_employee_balances(db=db, employee_id=current_employee.id, year=target_year)
    return [format_leave_balance_response(b) for b in balances]


@router.post(
    "/compoff/claims",
    response_model=CompOffClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit Comp-Off Extra-Work Claim",
    responses={
        201: {"description": "Comp-Off claim submitted successfully."},
        400: {"description": "Invalid date (future date or regular working day)."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "CSRF token missing or invalid."},
        409: {"description": "Duplicate claim for this work date."},
    },
)
async def create_compoff_claim(
    payload: CompOffClaimCreateRequest,
    current_employee: Employee = Depends(require_authenticated),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> CompOffClaimResponse:
    """Submit an extra-work compensatory off claim for working on a weekend or public holiday."""
    return submit_compoff_claim(
        db=db,
        employee_id=current_employee.id,
        work_date=payload.work_date,
        days_claimed=payload.days_claimed,
        reason=payload.reason,
    )


@router.get(
    "/compoff/claims",
    response_model=List[CompOffClaimResponse],
    status_code=status.HTTP_200_OK,
    summary="Get My Comp-Off Claims",
    responses={
        200: {"description": "List of compensatory off claims for the authenticated employee."},
        401: {"description": "Unauthenticated caller."},
    },
)
async def get_my_compoff_claims(
    status_filter: Optional[str] = Query(None, description="Optional status filter: PENDING, APPROVED, REJECTED"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> List[CompOffClaimResponse]:
    """Retrieve personal compensatory off claims for the currently authenticated employee.

    Derives employee identity exclusively from the authenticated session.
    """
    skip = (page - 1) * page_size
    claims = get_compoff_claims(
        db=db,
        employee_id=current_employee.id,
        status_filter=status_filter,
        skip=skip,
        limit=page_size,
    )
    return [format_compoff_claim_response(c) for c in claims]


# ============================================================================
# HR / Admin Administration Endpoints
# ============================================================================

@router.get(
    "/admin/pending",
    response_model=List[LeaveRequestResponse],
    status_code=status.HTTP_200_OK,
    summary="List Pending Leave Requests (HR/Admin)",
    responses={
        200: {"description": "List of pending leave requests awaiting approval."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions (HR/Admin required)."},
    },
)
async def list_pending_leaves(
    department: Optional[str] = Query(None, description="Optional department filter"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    current_employee: Employee = Depends(require_hr_or_admin),
    db: Session = Depends(get_db),
) -> List[LeaveRequestResponse]:
    """Retrieve all pending leave requests across the company for HR/Manager review."""
    skip = (page - 1) * page_size
    requests = get_pending_leave_requests(db=db, department=department, skip=skip, limit=page_size)
    return [format_leave_request_response(r) for r in requests]


@router.post(
    "/admin/{leave_id}/approve",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Leave Request (HR/Admin)",
    responses={
        200: {"description": "Leave request approved successfully."},
        400: {"description": "Cannot approve own leave or invalid request state."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions or CSRF error."},
        404: {"description": "Leave request not found."},
    },
)
async def approve_leave(
    leave_id: int = Path(..., description="ID of the leave request to approve"),
    payload: Optional[LeaveReviewRequest] = Body(None),
    current_employee: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> LeaveRequestResponse:
    """Approve a pending leave request, converting reserved balance to used balance.

    Strictly prevents self-approval.
    """
    review_notes = payload.review_notes if payload else None
    return approve_leave_request(
        db=db,
        request_id=leave_id,
        reviewer_id=current_employee.id,
        review_notes=review_notes,
    )


@router.post(
    "/admin/{leave_id}/reject",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Reject Leave Request (HR/Admin)",
    responses={
        200: {"description": "Leave request rejected and reserved balance released."},
        400: {"description": "Cannot reject own leave or invalid request state."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions or CSRF error."},
        404: {"description": "Leave request not found."},
    },
)
async def reject_leave(
    leave_id: int = Path(..., description="ID of the leave request to reject"),
    payload: Optional[LeaveReviewRequest] = Body(None),
    current_employee: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> LeaveRequestResponse:
    """Reject a pending leave request, releasing reserved days back to available balance."""
    review_notes = payload.review_notes if payload else None
    return reject_leave_request(
        db=db,
        request_id=leave_id,
        reviewer_id=current_employee.id,
        review_notes=review_notes,
    )


@router.post(
    "/admin/balances/adjust",
    response_model=LeaveBalanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Adjust Employee Leave Balance (HR/Admin)",
    responses={
        200: {"description": "Balance adjustment recorded and reconciled."},
        400: {"description": "Invalid adjustment or negative balance violation."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions or CSRF error."},
        404: {"description": "Employee or leave category not found."},
    },
)
async def adjust_balance(
    payload: LeaveBalanceAdjustmentRequest,
    current_employee: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> LeaveBalanceResponse:
    """Apply an administrative correction to an employee's leave balance with mandatory audit log."""
    return adjust_leave_balance(db=db, req=payload, hr_actor_id=current_employee.id)


@router.get(
    "/admin/compoff/claims",
    response_model=List[CompOffClaimResponse],
    status_code=status.HTTP_200_OK,
    summary="List Comp-Off Claims (HR/Admin)",
    responses={
        200: {"description": "List of employee compensatory off claims."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions (HR/Admin required)."},
    },
)
async def list_compoff_claims(
    status_filter: Optional[str] = Query(None, description="Optional status filter: PENDING, APPROVED, REJECTED"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    current_employee: Employee = Depends(require_hr_or_admin),
    db: Session = Depends(get_db),
) -> List[CompOffClaimResponse]:
    """Retrieve company-wide compensatory off claims for review."""
    skip = (page - 1) * page_size
    claims = get_compoff_claims(db=db, status_filter=status_filter, skip=skip, limit=page_size)
    return [format_compoff_claim_response(c) for c in claims]


@router.post(
    "/admin/compoff/claims/{claim_id}/approve",
    response_model=CompOffClaimResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Comp-Off Claim (HR/Admin)",
    responses={
        200: {"description": "Claim approved, credit created, and balance updated."},
        400: {"description": "Cannot review own claim or invalid state."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions or CSRF error."},
        404: {"description": "Claim not found."},
    },
)
async def approve_compoff_claim(
    claim_id: int = Path(..., description="ID of the Comp-Off claim to approve"),
    payload: Optional[CompOffClaimReviewRequest] = Body(None),
    current_employee: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> CompOffClaimResponse:
    """Approve a compensatory off extra-work claim.

    Strictly prevents self-approval.
    """
    days = payload.days_approved if payload and payload.days_approved else None
    notes = payload.review_notes if payload else None

    # Default to claim's claimed days if days_approved not explicitly provided
    if days is None:
        from app.models.leave import CompOffClaim
        claim = db.get(CompOffClaim, claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim #{claim_id} not found.")
        days = claim.days_claimed

    return review_compoff_claim(
        db=db,
        claim_id=claim_id,
        reviewer_id=current_employee.id,
        is_approve=True,
        days_approved=days,
        review_notes=notes,
    )


@router.post(
    "/admin/compoff/claims/{claim_id}/reject",
    response_model=CompOffClaimResponse,
    status_code=status.HTTP_200_OK,
    summary="Reject Comp-Off Claim (HR/Admin)",
    responses={
        200: {"description": "Claim rejected."},
        400: {"description": "Cannot review own claim or invalid state."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Insufficient permissions or CSRF error."},
        404: {"description": "Claim not found."},
    },
)
async def reject_compoff_claim(
    claim_id: int = Path(..., description="ID of the Comp-Off claim to reject"),
    payload: Optional[CompOffClaimReviewRequest] = Body(None),
    current_employee: Employee = Depends(require_hr_or_admin),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> CompOffClaimResponse:
    """Reject a compensatory off extra-work claim."""
    notes = payload.review_notes if payload else None
    return review_compoff_claim(
        db=db,
        claim_id=claim_id,
        reviewer_id=current_employee.id,
        is_approve=False,
        review_notes=notes,
    )


# ============================================================================
# General Employee Collection & Item Endpoints
# ============================================================================

@router.post(
    "/",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit Leave Application",
    responses={
        201: {"description": "Leave application submitted and balance reserved."},
        400: {"description": "Insufficient balance or invalid dates."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "CSRF token missing or invalid."},
        404: {"description": "Leave category not found."},
        409: {"description": "Overlapping request conflict."},
    },
)
async def apply_for_leave(
    payload: LeaveApplyRequest,
    current_employee: Employee = Depends(require_authenticated),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> LeaveRequestResponse:
    """Submit a new leave application with advisory concurrency locking and balance reservation."""
    return submit_leave_request(
        db=db,
        employee_id=current_employee.id,
        category_code=payload.category_code.value,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_half_day=payload.is_half_day,
        half_day_period=payload.half_day_period.value if payload.half_day_period else None,
        reason=payload.reason,
    )


@router.get(
    "/",
    response_model=List[LeaveRequestResponse],
    status_code=status.HTTP_200_OK,
    summary="Get My Leave Requests History",
    responses={
        200: {"description": "List of personal leave requests."},
        401: {"description": "Unauthenticated caller."},
    },
)
async def get_my_leaves(
    year: Optional[int] = Query(None, description="Optional year filter"),
    status_filter: Optional[str] = Query(None, description="Optional status filter: PENDING, APPROVED, REJECTED, CANCELLED"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> List[LeaveRequestResponse]:
    """Retrieve personal leave applications history for the authenticated employee."""
    skip = (page - 1) * page_size
    requests = get_employee_leave_requests(
        db=db,
        employee_id=current_employee.id,
        year=year,
        status_filter=status_filter,
        skip=skip,
        limit=page_size,
    )
    return [format_leave_request_response(r) for r in requests]


@router.get(
    "/{leave_id}",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Leave Request Details",
    responses={
        200: {"description": "Details of the specified leave request."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Forbidden: Cannot access another employee's request."},
        404: {"description": "Leave request not found."},
    },
)
async def get_leave_details(
    leave_id: int = Path(..., description="ID of the leave request"),
    current_employee: Employee = Depends(require_authenticated),
    db: Session = Depends(get_db),
) -> LeaveRequestResponse:
    """Retrieve details of a leave request with strict resource ownership validation."""
    leave_req = get_leave_request_by_id(db=db, request_id=leave_id)
    if not leave_req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Leave request #{leave_id} not found.",
        )

    # Ownership check: employees can only access their own records; HR/Admin can access all
    if current_employee.role not in (UserRole.HR.value, UserRole.ADMIN.value):
        if leave_req.employee_id != current_employee.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: you can only view your own leave requests.",
            )

    return format_leave_request_response(leave_req)


@router.post(
    "/{leave_id}/cancel",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Leave Request",
    responses={
        200: {"description": "Leave request cancelled."},
        400: {"description": "Cannot cancel already cancelled or rejected request."},
        401: {"description": "Unauthenticated caller."},
        403: {"description": "Forbidden: Requires HR authorization for approved requests."},
        404: {"description": "Leave request not found."},
    },
)
async def cancel_leave(
    leave_id: int = Path(..., description="ID of the leave request to cancel"),
    payload: LeaveCancelRequest = Body(...),
    current_employee: Employee = Depends(require_authenticated),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> LeaveRequestResponse:
    """Cancel a leave request.

    - PENDING: Employee can cancel own request (releases reserved balance).
    - APPROVED: Requires HR authorization (refunds used balance).
    """
    is_hr = current_employee.role in (UserRole.HR.value, UserRole.ADMIN.value)
    return cancel_leave_request(
        db=db,
        request_id=leave_id,
        actor_id=current_employee.id,
        reason=payload.reason,
        is_hr_or_admin=is_hr,
    )
