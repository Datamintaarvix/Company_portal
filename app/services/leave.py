"""Leave management service layer encapsulating business logic, calculations, and transactional workflows."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Set, Tuple
from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.timezone import (
    ensure_utc,
    format_ist_date,
    format_ist_time,
    get_current_work_date,
    is_weekend_day,
)
from app.models.employee import Employee
from app.models.leave import (
    CompanyHoliday,
    CompOffClaim,
    CompOffClaimStatus,
    CompOffCreditStatus,
    HalfDayPeriod,
    LeaveAuditLog,
    LeaveBalance,
    LeaveBalanceTransaction,
    LeaveCategory,
    LeaveCategoryCode,
    LeaveRequest,
    LeaveRequestStatus,
    TransactionType,
)
from app.schemas.leave import (
    CompOffClaimResponse,
    LeaveBalanceAdjustmentRequest,
    LeaveBalanceResponse,
    LeaveBalanceTransactionResponse,
    LeaveCalculationResponse,
    LeaveRequestResponse,
)


# ============================================================================
# Advisory Lock Helper
# ============================================================================

def acquire_employee_advisory_lock(db: Session, employee_id: int) -> None:
    """Acquire an exclusive transaction-scoped advisory lock for the employee in PostgreSQL.

    Serializes concurrent leave submissions and modifications for this employee.
    Safely skips on non-PostgreSQL dialects (such as SQLite during unit tests).
    """
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:emp_id)"), {"emp_id": employee_id})


# ============================================================================
# Holiday & Calendar Helper Functions
# ============================================================================

def get_holiday_dates(db: Session, start_date: date, end_date: date) -> Set[date]:
    """Retrieve all company holiday dates falling within the given inclusive date range."""
    holidays = db.execute(
        select(CompanyHoliday.holiday_date).where(
            CompanyHoliday.holiday_date >= start_date,
            CompanyHoliday.holiday_date <= end_date,
        )
    ).scalars().all()
    return set(holidays)


def is_non_working_day(target_date: date, holidays: Set[date]) -> bool:
    """Check if a date is either a weekend (Sat/Sun) or a company observed holiday."""
    return is_weekend_day(target_date) or (target_date in holidays)


# ============================================================================
# Leave Calculation Engine (Sandwich Rule Enforcement)
# ============================================================================

def calculate_leave_days(
    db: Session,
    start_date: date,
    end_date: date,
    is_half_day: bool = False,
    half_day_period: Optional[str] = None,
) -> LeaveCalculationResponse:
    """Calculate the exact number of leave days charged according to Leave Policy v1.0.

    Rules:
    - Half-day: strictly 0.5 day; never triggers sandwich rule.
    - Single day: if working day -> 1.0 day charged. No sandwich penalty.
    - Continuous multi-day request:
      - Weekends/holidays between working leave days are counted (sandwiched).
      - Weekends/holidays at the trailing boundaries are not counted.
    """
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date cannot precede start date.",
        )

    # 1. Half-day calculation
    if is_half_day:
        if start_date != end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Half-day leave must start and end on the same calendar date.",
            )
        if not half_day_period or half_day_period not in (HalfDayPeriod.MORNING.value, HalfDayPeriod.AFTERNOON.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="half_day_period must be 'morning' or 'afternoon' for half-day leave.",
            )

        holidays = get_holiday_dates(db, start_date, end_date)
        if is_non_working_day(start_date, holidays):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{format_ist_date(start_date)} is a non-working day (weekend/holiday).",
            )

        return LeaveCalculationResponse(
            total_days=Decimal("0.5"),
            calendar_days=1,
            working_days=Decimal("0.5"),
            weekend_days=0,
            holiday_days=0,
            sandwiched_weekend_days=0,
            sandwiched_holiday_days=0,
            breakdown_notes=["Half-day leave charged at 0.5 day (no sandwich deduction)."],
        )

    # 2. Multi-day or full-day single day calculation
    holidays = get_holiday_dates(db, start_date, end_date)
    calendar_days_count = (end_date - start_date).days + 1

    calendar_days: List[date] = [start_date + timedelta(days=i) for i in range(calendar_days_count)]
    working_days_in_span: List[date] = [d for d in calendar_days if not is_non_working_day(d, holidays)]

    if not working_days_in_span:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The requested leave range contains zero working days.",
        )

    # Single working day span (e.g. Friday only, Monday only, or single day bounding weekend)
    if len(working_days_in_span) == 1:
        single_day = working_days_in_span[0]
        return LeaveCalculationResponse(
            total_days=Decimal("1.0"),
            calendar_days=calendar_days_count,
            working_days=Decimal("1.0"),
            weekend_days=sum(1 for d in calendar_days if is_weekend_day(d)),
            holiday_days=sum(1 for d in calendar_days if d in holidays and not is_weekend_day(d)),
            sandwiched_weekend_days=0,
            sandwiched_holiday_days=0,
            breakdown_notes=[f"Single working day on {format_ist_date(single_day)} charged at 1.0 day (no sandwich penalty)."],
        )

    # Multi-working day continuous request: Sandwich Rule applies
    first_working = working_days_in_span[0]
    last_working = working_days_in_span[-1]

    # Intervening days between first working day and last working day
    intervening_days = [
        d for d in calendar_days
        if first_working <= d <= last_working
    ]

    sandwiched_weekends = [d for d in intervening_days if is_weekend_day(d)]
    sandwiched_holidays = [d for d in intervening_days if d in holidays and not is_weekend_day(d)]
    working_days_counted = [d for d in intervening_days if not is_non_working_day(d, holidays)]

    total_charged_days = Decimal(len(intervening_days))
    notes = [
        f"Continuous leave from {format_ist_date(first_working)} to {format_ist_date(last_working)}.",
        f"Working days: {len(working_days_counted)}.",
    ]
    if sandwiched_weekends:
        notes.append(f"Sandwiched weekend days counted: {len(sandwiched_weekends)}.")
    if sandwiched_holidays:
        notes.append(f"Sandwiched holiday days counted: {len(sandwiched_holidays)}.")

    return LeaveCalculationResponse(
        total_days=total_charged_days,
        calendar_days=calendar_days_count,
        working_days=Decimal(len(working_days_counted)),
        weekend_days=sum(1 for d in calendar_days if is_weekend_day(d)),
        holiday_days=sum(1 for d in calendar_days if d in holidays and not is_weekend_day(d)),
        sandwiched_weekend_days=len(sandwiched_weekends),
        sandwiched_holiday_days=len(sandwiched_holidays),
        breakdown_notes=notes,
    )


# ============================================================================
# Overlap Collision Detection
# ============================================================================

def check_leave_conflicts(
    db: Session,
    employee_id: int,
    start_date: date,
    end_date: date,
    is_half_day: bool,
    half_day_period: Optional[str],
    exclude_request_id: Optional[int] = None,
) -> Optional[LeaveRequest]:
    """Check if the proposed leave request conflicts with any existing active requests.

    Matrix:
    - Full-day vs Full-day: Collision (Conflict)
    - Full-day vs Half-day: Collision (Conflict)
    - Half-day vs Half-day:
      - Different dates: No collision
      - Same date + same period: Collision (Conflict)
      - Same date + complementary periods (morning vs afternoon): Allowed!
    """
    stmt = (
        select(LeaveRequest)
        .where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status.in_([LeaveRequestStatus.PENDING.value, LeaveRequestStatus.APPROVED.value]),
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        )
    )
    if exclude_request_id is not None:
        stmt = stmt.where(LeaveRequest.id != exclude_request_id)

    # Use row-level lock if in PostgreSQL
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    existing_requests = db.execute(stmt).scalars().all()

    for existing in existing_requests:
        # Determine overlapping dates between proposed and existing
        overlap_start = max(start_date, existing.start_date)
        overlap_end = min(end_date, existing.end_date)

        current = overlap_start
        while current <= overlap_end:
            # Check collision on this date
            if not is_half_day or not existing.is_half_day:
                # If either is full-day on this date -> collision!
                return existing

            # Both are half-days on this date
            if start_date == end_date == existing.start_date == existing.end_date:
                if half_day_period == existing.half_day_period:
                    # Same half-day period -> collision!
                    return existing

            current += timedelta(days=1)

    return None


# ============================================================================
# Leave Balance Query & Locking Helpers
# ============================================================================

def get_or_create_leave_balance(
    db: Session,
    employee_id: int,
    category_id: int,
    year: int,
    for_update: bool = False,
) -> LeaveBalance:
    """Retrieve or initialize a leave balance row for the given employee, category, and year."""
    stmt = select(LeaveBalance).where(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.category_id == category_id,
        LeaveBalance.year == year,
    )
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    if for_update and bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    balance = db.execute(stmt).scalar_one_or_none()
    if balance is None:
        category = db.get(LeaveCategory, category_id)
        entitled = category.annual_quota if category else Decimal("0.0")
        balance = LeaveBalance(
            employee_id=employee_id,
            category_id=category_id,
            year=year,
            entitled_days=entitled,
            carried_forward_days=Decimal("0.0"),
            accrued_days=Decimal("0.0"),
            used_days=Decimal("0.0"),
            reserved_days=Decimal("0.0"),
        )
        db.add(balance)
        db.flush()

    return balance


# ============================================================================
# Leave Submission Transaction
# ============================================================================

def submit_leave_request(
    db: Session,
    employee_id: int,
    category_code: str,
    start_date: date,
    end_date: date,
    is_half_day: bool,
    half_day_period: Optional[str],
    reason: str,
) -> LeaveRequestResponse:
    """Submit a leave request within an atomic database transaction.

    Acquires an exclusive advisory lock on the employee, verifies conflicts,
    verifies and reserves leave balance, inserts the request, logs the transaction,
    and writes an immutable audit record.
    """
    # 1. Acquire transaction advisory lock
    acquire_employee_advisory_lock(db, employee_id)

    # 2. Check for conflicts
    conflict = check_leave_conflicts(
        db=db,
        employee_id=employee_id,
        start_date=start_date,
        end_date=end_date,
        is_half_day=is_half_day,
        half_day_period=half_day_period,
    )
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Leave request conflicts with existing {conflict.status.lower()} request #{conflict.id} "
                f"({format_ist_date(conflict.start_date)} to {format_ist_date(conflict.end_date)})."
            ),
        )

    # 3. Calculate leave days
    calc = calculate_leave_days(
        db=db,
        start_date=start_date,
        end_date=end_date,
        is_half_day=is_half_day,
        half_day_period=half_day_period,
    )
    total_days = calc.total_days

    # 4. Fetch category
    category = db.execute(
        select(LeaveCategory).where(LeaveCategory.code == category_code)
    ).scalar_one_or_none()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Leave category '{category_code}' does not exist.",
        )
    if not category.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Leave category '{category.name}' is inactive.",
        )

    # 5. Balance verification & reservation
    leave_year = start_date.year
    balance = None
    balance_tx = None

    if category.code != LeaveCategoryCode.UNPAID.value:
        balance = get_or_create_leave_balance(
            db=db,
            employee_id=employee_id,
            category_id=category.id,
            year=leave_year,
            for_update=True,
        )
        if balance.available_days < total_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Insufficient leave balance for {category.name}. "
                    f"Available: {balance.available_days} day(s), requested: {total_days} day(s)."
                ),
            )

        # Snapshot before/after
        before_snapshot = balance.available_days
        balance.reserved_days += total_days
        after_snapshot = balance.available_days

    # 6. Insert leave request
    leave_req = LeaveRequest(
        employee_id=employee_id,
        category_id=category.id,
        start_date=start_date,
        end_date=end_date,
        is_half_day=is_half_day,
        half_day_period=half_day_period,
        total_days=total_days,
        reason=reason,
        status=LeaveRequestStatus.PENDING.value,
    )
    db.add(leave_req)
    db.flush()

    # 7. Record balance transaction ledger entry
    if balance is not None:
        balance_tx = LeaveBalanceTransaction(
            balance_id=balance.id,
            leave_request_id=leave_req.id,
            transaction_type=TransactionType.RESERVE.value,
            amount=total_days,
            balance_before=before_snapshot,
            balance_after=after_snapshot,
            created_by_id=employee_id,
            notes=f"Reserved {total_days} day(s) for leave request #{leave_req.id}",
        )
        db.add(balance_tx)

    # 8. Record audit log
    audit_log = LeaveAuditLog(
        entity_type="leave_request",
        entity_id=leave_req.id,
        action="SUBMITTED",
        actor_id=employee_id,
        new_state=f"status=PENDING, total_days={total_days}, category={category.code}",
        notes=f"Leave application submitted: {format_ist_date(start_date)} to {format_ist_date(end_date)}",
    )
    db.add(audit_log)
    db.commit()
    db.refresh(leave_req)

    return format_leave_request_response(leave_req)


# ============================================================================
# Leave Approval Transaction
# ============================================================================

def approve_leave_request(
    db: Session,
    request_id: int,
    reviewer_id: int,
    review_notes: Optional[str] = None,
) -> LeaveRequestResponse:
    """Approve a pending leave request with row locking and idempotency.

    Converts reserved days into used days, performs FIFO Comp-Off consumption
    if applicable, and logs the decision. Prevents self-approval.
    """
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    stmt = select(LeaveRequest).where(LeaveRequest.id == request_id)
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    leave_req = db.execute(stmt).scalar_one_or_none()
    if not leave_req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Leave request #{request_id} not found.",
        )

    # Anti-Self-Approval
    if reviewer_id == leave_req.employee_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Employees cannot approve their own leave requests.",
        )

    # State validation
    if leave_req.status != LeaveRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve request with current status '{leave_req.status}'.",
        )

    category = leave_req.category

    # Convert reserved balance into used balance
    if category.code != LeaveCategoryCode.UNPAID.value:
        balance = get_or_create_leave_balance(
            db=db,
            employee_id=leave_req.employee_id,
            category_id=category.id,
            year=leave_req.start_date.year,
            for_update=True,
        )

        before_snapshot = balance.available_days
        balance.reserved_days -= leave_req.total_days
        balance.used_days += leave_req.total_days
        after_snapshot = balance.available_days

        # Record COMMIT transaction
        balance_tx = LeaveBalanceTransaction(
            balance_id=balance.id,
            leave_request_id=leave_req.id,
            transaction_type=TransactionType.COMMIT.value,
            amount=leave_req.total_days,
            balance_before=before_snapshot,
            balance_after=after_snapshot,
            created_by_id=reviewer_id,
            notes=f"Approved leave request #{leave_req.id}: converted {leave_req.total_days} reserved day(s) to used.",
        )
        db.add(balance_tx)

        # FIFO consumption if Comp-Off
        if category.code == LeaveCategoryCode.COMP_OFF.value:
            consume_compoff_credits_fifo(
                db=db,
                employee_id=leave_req.employee_id,
                days_to_consume=leave_req.total_days,
                leave_date=leave_req.start_date,
            )

    # Update request state
    now_utc = datetime.now(timezone.utc)
    leave_req.status = LeaveRequestStatus.APPROVED.value
    leave_req.reviewed_by_id = reviewer_id
    leave_req.reviewed_at = now_utc
    leave_req.review_notes = review_notes

    # Audit log
    audit_log = LeaveAuditLog(
        entity_type="leave_request",
        entity_id=leave_req.id,
        action="APPROVED",
        actor_id=reviewer_id,
        previous_state="status=PENDING",
        new_state=f"status=APPROVED, reviewed_by={reviewer_id}",
        notes=review_notes or "Leave request approved by HR/Manager",
    )
    db.add(audit_log)
    db.commit()
    db.refresh(leave_req)

    return format_leave_request_response(leave_req)


# ============================================================================
# Leave Rejection Transaction
# ============================================================================

def reject_leave_request(
    db: Session,
    request_id: int,
    reviewer_id: int,
    review_notes: Optional[str] = None,
) -> LeaveRequestResponse:
    """Reject a pending leave request, releasing reserved days back to available balance."""
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    stmt = select(LeaveRequest).where(LeaveRequest.id == request_id)
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    leave_req = db.execute(stmt).scalar_one_or_none()
    if not leave_req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Leave request #{request_id} not found.",
        )

    # Anti-Self-Approval
    if reviewer_id == leave_req.employee_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Employees cannot review their own leave requests.",
        )

    if leave_req.status != LeaveRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reject request with current status '{leave_req.status}'.",
        )

    category = leave_req.category

    # Release reserved balance
    if category.code != LeaveCategoryCode.UNPAID.value:
        balance = get_or_create_leave_balance(
            db=db,
            employee_id=leave_req.employee_id,
            category_id=category.id,
            year=leave_req.start_date.year,
            for_update=True,
        )

        before_snapshot = balance.available_days
        balance.reserved_days -= leave_req.total_days
        after_snapshot = balance.available_days

        # Record RELEASE transaction
        balance_tx = LeaveBalanceTransaction(
            balance_id=balance.id,
            leave_request_id=leave_req.id,
            transaction_type=TransactionType.RELEASE.value,
            amount=leave_req.total_days,
            balance_before=before_snapshot,
            balance_after=after_snapshot,
            created_by_id=reviewer_id,
            notes=f"Rejected leave request #{leave_req.id}: released {leave_req.total_days} reserved day(s).",
        )
        db.add(balance_tx)

    # Update request state
    now_utc = datetime.now(timezone.utc)
    leave_req.status = LeaveRequestStatus.REJECTED.value
    leave_req.reviewed_by_id = reviewer_id
    leave_req.reviewed_at = now_utc
    leave_req.review_notes = review_notes

    # Audit log
    audit_log = LeaveAuditLog(
        entity_type="leave_request",
        entity_id=leave_req.id,
        action="REJECTED",
        actor_id=reviewer_id,
        previous_state="status=PENDING",
        new_state=f"status=REJECTED, reviewed_by={reviewer_id}",
        notes=review_notes or "Leave request rejected by HR/Manager",
    )
    db.add(audit_log)
    db.commit()
    db.refresh(leave_req)

    return format_leave_request_response(leave_req)


# ============================================================================
# Leave Cancellation & Refund Transaction
# ============================================================================

def cancel_leave_request(
    db: Session,
    request_id: int,
    actor_id: int,
    reason: str,
    is_hr_or_admin: bool = False,
) -> LeaveRequestResponse:
    """Cancel a leave request.

    - PENDING requests: Employee or HR can cancel; releases reserved days.
    - APPROVED requests: Requires HR authorization; refunds used days.
    """
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    stmt = select(LeaveRequest).where(LeaveRequest.id == request_id)
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    leave_req = db.execute(stmt).scalar_one_or_none()
    if not leave_req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Leave request #{request_id} not found.",
        )

    if leave_req.status in (LeaveRequestStatus.CANCELLED.value, LeaveRequestStatus.REJECTED.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel request with status '{leave_req.status}'.",
        )

    category = leave_req.category

    # Case 1: Cancel a PENDING request (Employee self-cancellation or HR cancellation)
    if leave_req.status == LeaveRequestStatus.PENDING.value:
        if not is_hr_or_admin and actor_id != leave_req.employee_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot cancel another employee's pending leave.",
            )

        if category.code != LeaveCategoryCode.UNPAID.value:
            balance = get_or_create_leave_balance(
                db=db,
                employee_id=leave_req.employee_id,
                category_id=category.id,
                year=leave_req.start_date.year,
                for_update=True,
            )
            before_snapshot = balance.available_days
            balance.reserved_days -= leave_req.total_days
            after_snapshot = balance.available_days

            balance_tx = LeaveBalanceTransaction(
                balance_id=balance.id,
                leave_request_id=leave_req.id,
                transaction_type=TransactionType.RELEASE.value,
                amount=leave_req.total_days,
                balance_before=before_snapshot,
                balance_after=after_snapshot,
                created_by_id=actor_id,
                notes=f"Cancelled pending leave request #{leave_req.id}: released {leave_req.total_days} reserved day(s).",
            )
            db.add(balance_tx)

    # Case 2: Cancel an APPROVED request (Requires HR approval)
    elif leave_req.status == LeaveRequestStatus.APPROVED.value:
        if not is_hr_or_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cancellation of already approved leave requires HR authorization.",
            )

        if category.code != LeaveCategoryCode.UNPAID.value:
            balance = get_or_create_leave_balance(
                db=db,
                employee_id=leave_req.employee_id,
                category_id=category.id,
                year=leave_req.start_date.year,
                for_update=True,
            )
            before_snapshot = balance.available_days
            balance.used_days -= leave_req.total_days
            after_snapshot = balance.available_days

            # Record REFUND transaction
            balance_tx = LeaveBalanceTransaction(
                balance_id=balance.id,
                leave_request_id=leave_req.id,
                transaction_type=TransactionType.REFUND.value,
                amount=leave_req.total_days,
                balance_before=before_snapshot,
                balance_after=after_snapshot,
                created_by_id=actor_id,
                notes=f"Cancelled approved leave request #{leave_req.id}: refunded {leave_req.total_days} used day(s). Reason: {reason}",
            )
            db.add(balance_tx)

    prev_status = leave_req.status
    leave_req.status = LeaveRequestStatus.CANCELLED.value
    leave_req.review_notes = f"{leave_req.review_notes or ''} [Cancelled by #{actor_id}: {reason}]".strip()

    audit_log = LeaveAuditLog(
        entity_type="leave_request",
        entity_id=leave_req.id,
        action="CANCELLED",
        actor_id=actor_id,
        previous_state=f"status={prev_status}",
        new_state="status=CANCELLED",
        notes=f"Cancellation reason: {reason}",
    )
    db.add(audit_log)
    db.commit()
    db.refresh(leave_req)

    return format_leave_request_response(leave_req)


# ============================================================================
# Comp-Off Service (Claims, FIFO Consumption & Expiry)
# ============================================================================

def submit_compoff_claim(
    db: Session,
    employee_id: int,
    work_date: date,
    days_claimed: Decimal,
    reason: str,
) -> CompOffClaimResponse:
    """Submit a compensatory off claim for working on a weekend or public holiday."""
    today = get_current_work_date()
    if work_date > today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot claim compensatory off for future work dates.",
        )

    holidays = get_holiday_dates(db, work_date, work_date)
    if not is_non_working_day(work_date, holidays):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{format_ist_date(work_date)} is a regular working day. Comp-Off claims are only valid for weekends or holidays.",
        )

    # Check for duplicate claim on same work date
    existing = db.execute(
        select(CompOffClaim).where(
            CompOffClaim.employee_id == employee_id,
            CompOffClaim.work_date == work_date,
            CompOffClaim.status.in_([CompOffClaimStatus.PENDING.value, CompOffClaimStatus.APPROVED.value]),
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active Comp-Off claim already exists for work date {format_ist_date(work_date)} (#{existing.id}).",
        )

    claim = CompOffClaim(
        employee_id=employee_id,
        work_date=work_date,
        days_claimed=days_claimed,
        reason=reason,
        status=CompOffClaimStatus.PENDING.value,
        credit_status=CompOffCreditStatus.PENDING.value,
    )
    db.add(claim)
    db.flush()

    audit_log = LeaveAuditLog(
        entity_type="comp_off_claim",
        entity_id=claim.id,
        action="CLAIM_SUBMITTED",
        actor_id=employee_id,
        new_state=f"work_date={work_date}, days_claimed={days_claimed}",
        notes="Comp-off extra work claim submitted",
    )
    db.add(audit_log)
    db.commit()
    db.refresh(claim)

    return format_compoff_claim_response(claim)


def review_compoff_claim(
    db: Session,
    claim_id: int,
    reviewer_id: int,
    is_approve: bool,
    days_approved: Optional[Decimal] = None,
    review_notes: Optional[str] = None,
) -> CompOffClaimResponse:
    """Review an employee Comp-Off claim.

    Upon approval, creates a COMPOFF_CREDIT transaction, credits accrued days
    to the employee's Comp-Off balance, sets 90-day expiry, and transitions credit status to ACTIVE.
    """
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    stmt = select(CompOffClaim).where(CompOffClaim.id == claim_id)
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    claim = db.execute(stmt).scalar_one_or_none()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comp-off claim #{claim_id} not found.",
        )

    if reviewer_id == claim.employee_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Employees cannot review their own compensatory off claims.",
        )

    if claim.status != CompOffClaimStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot review claim with current status '{claim.status}'.",
        )

    now_utc = datetime.now(timezone.utc)

    if is_approve:
        if days_approved not in (Decimal("0.5"), Decimal("1.0")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="days_approved must be 0.5 or 1.0 when approving a claim.",
            )

        expiry_date = claim.work_date + timedelta(days=90)

        # Fetch Comp-Off category
        comp_category = db.execute(
            select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.COMP_OFF.value)
        ).scalar_one_or_none()
        if not comp_category:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Comp-Off leave category configuration not found.",
            )

        balance = get_or_create_leave_balance(
            db=db,
            employee_id=claim.employee_id,
            category_id=comp_category.id,
            year=claim.work_date.year,
            for_update=True,
        )

        before_snapshot = balance.available_days
        balance.accrued_days += days_approved
        after_snapshot = balance.available_days

        # Record COMPOFF_CREDIT transaction
        balance_tx = LeaveBalanceTransaction(
            balance_id=balance.id,
            comp_off_claim_id=claim.id,
            transaction_type=TransactionType.COMPOFF_CREDIT.value,
            amount=days_approved,
            balance_before=before_snapshot,
            balance_after=after_snapshot,
            created_by_id=reviewer_id,
            notes=f"Credited {days_approved} day(s) from approved claim #{claim.id} (work date: {format_ist_date(claim.work_date)})",
        )
        db.add(balance_tx)
        db.flush()

        # Update claim
        claim.status = CompOffClaimStatus.APPROVED.value
        claim.days_approved = days_approved
        claim.remaining_days = days_approved
        claim.expiry_date = expiry_date
        claim.credit_status = CompOffCreditStatus.ACTIVE.value
        claim.reviewed_by_id = reviewer_id
        claim.reviewed_at = now_utc
        claim.review_notes = review_notes
        claim.balance_transaction_id = balance_tx.id

        action = "CLAIM_APPROVED"
        notes = f"Approved {days_approved} days comp-off. Valid until {format_ist_date(expiry_date)}."
    else:
        claim.status = CompOffClaimStatus.REJECTED.value
        claim.credit_status = CompOffCreditStatus.EXPIRED.value
        claim.reviewed_by_id = reviewer_id
        claim.reviewed_at = now_utc
        claim.review_notes = review_notes
        action = "CLAIM_REJECTED"
        notes = review_notes or "Comp-off claim rejected by HR"

    audit_log = LeaveAuditLog(
        entity_type="comp_off_claim",
        entity_id=claim.id,
        action=action,
        actor_id=reviewer_id,
        previous_state="status=PENDING",
        new_state=f"status={claim.status}",
        notes=notes,
    )
    db.add(audit_log)
    db.commit()
    db.refresh(claim)

    return format_compoff_claim_response(claim)


def consume_compoff_credits_fifo(
    db: Session,
    employee_id: int,
    days_to_consume: Decimal,
    leave_date: date,
) -> List[CompOffClaim]:
    """Consume Comp-Off credits First-In, First-Out (FIFO) by earliest expiry date."""
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    stmt = (
        select(CompOffClaim)
        .where(
            CompOffClaim.employee_id == employee_id,
            CompOffClaim.status == CompOffClaimStatus.APPROVED.value,
            CompOffClaim.credit_status.in_([CompOffCreditStatus.ACTIVE.value, CompOffCreditStatus.PARTIALLY_EXHAUSTED.value]),
            CompOffClaim.expiry_date >= leave_date,
            CompOffClaim.remaining_days > Decimal("0.0"),
        )
        .order_by(CompOffClaim.expiry_date.asc(), CompOffClaim.work_date.asc())
    )
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    claims = db.execute(stmt).scalars().all()
    remaining_needed = days_to_consume
    consumed_claims = []

    for claim in claims:
        if remaining_needed <= Decimal("0.0"):
            break

        take = min(claim.remaining_days, remaining_needed)
        claim.remaining_days -= take
        remaining_needed -= take

        if claim.remaining_days == Decimal("0.0"):
            claim.credit_status = CompOffCreditStatus.EXHAUSTED.value
        else:
            claim.credit_status = CompOffCreditStatus.PARTIALLY_EXHAUSTED.value

        consumed_claims.append(claim)

    if remaining_needed > Decimal("0.0"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient active Comp-Off credits available for {format_ist_date(leave_date)}. Missing {remaining_needed} day(s).",
        )

    db.flush()
    return consumed_claims


def expire_compoff_credits(
    db: Session,
    as_of_date: Optional[date] = None,
) -> int:
    """Daily automated job expiring Comp-Off credits past their 90-day validity window.

    Deducts unconsumed remaining days from accrued leave balance and records COMPOFF_EXPIRY.
    """
    check_date = as_of_date or get_current_work_date()
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind

    stmt = (
        select(CompOffClaim)
        .where(
            CompOffClaim.status == CompOffClaimStatus.APPROVED.value,
            CompOffClaim.credit_status.in_([CompOffCreditStatus.ACTIVE.value, CompOffCreditStatus.PARTIALLY_EXHAUSTED.value]),
            CompOffClaim.expiry_date < check_date,
            CompOffClaim.remaining_days > Decimal("0.0"),
        )
    )
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    expired_claims = db.execute(stmt).scalars().all()
    count = 0

    comp_category = db.execute(
        select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.COMP_OFF.value)
    ).scalar_one_or_none()

    for claim in expired_claims:
        lost_days = claim.remaining_days
        claim.remaining_days = Decimal("0.0")
        claim.credit_status = CompOffCreditStatus.EXPIRED.value

        if comp_category:
            balance = get_or_create_leave_balance(
                db=db,
                employee_id=claim.employee_id,
                category_id=comp_category.id,
                year=claim.work_date.year,
                for_update=True,
            )
            before_snapshot = balance.available_days
            balance.accrued_days = max(Decimal("0.0"), balance.accrued_days - lost_days)
            after_snapshot = balance.available_days

            tx = LeaveBalanceTransaction(
                balance_id=balance.id,
                comp_off_claim_id=claim.id,
                transaction_type=TransactionType.COMPOFF_EXPIRY.value,
                amount=lost_days,
                balance_before=before_snapshot,
                balance_after=after_snapshot,
                created_by_id=claim.employee_id,
                notes=f"Expired {lost_days} unconsumed Comp-Off day(s) from claim #{claim.id} (expired on {format_ist_date(claim.expiry_date)})",
            )
            db.add(tx)

        audit_log = LeaveAuditLog(
            entity_type="comp_off_claim",
            entity_id=claim.id,
            action="CREDIT_EXPIRED",
            actor_id=claim.employee_id,
            previous_state=f"remaining_days={lost_days}",
            new_state="credit_status=EXPIRED, remaining_days=0.0",
            notes=f"Comp-off credit expired past 90-day window ({format_ist_date(claim.expiry_date)})",
        )
        db.add(audit_log)
        count += 1

    if count > 0:
        db.commit()

    return count


# ============================================================================
# Monthly Earned Leave Accrual & Onboarding Proration
# ============================================================================

def run_monthly_earned_accrual(
    db: Session,
    year: int,
    month: int,
    actor_id: int,
) -> Tuple[int, List[str]]:
    """Run monthly batch accrual for Earned Leave (1.0 day per completed month).

    Uses transaction ledger unique constraint (balance_id, ACCRUAL, period) for idempotency.
    """
    period_key = f"{year:04d}-{month:02d}"
    earned_category = db.execute(
        select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.EARNED.value)
    ).scalar_one_or_none()

    if not earned_category:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Earned Leave category configuration missing.",
        )

    # Fetch active employees
    employees = db.execute(
        select(Employee).where(Employee.is_active == True)
    ).scalars().all()

    credited_count = 0
    skipped_messages = []

    for emp in employees:
        balance = get_or_create_leave_balance(
            db=db,
            employee_id=emp.id,
            category_id=earned_category.id,
            year=year,
            for_update=True,
        )

        # Check if already accrued for this period
        existing_tx = db.execute(
            select(LeaveBalanceTransaction).where(
                LeaveBalanceTransaction.balance_id == balance.id,
                LeaveBalanceTransaction.transaction_type == TransactionType.ACCRUAL.value,
                LeaveBalanceTransaction.accrual_period == period_key,
            )
        ).scalar_one_or_none()

        if existing_tx:
            skipped_messages.append(f"Employee #{emp.id} ({emp.name}) already received accrual for {period_key}.")
            continue

        before_snapshot = balance.available_days
        balance.accrued_days += Decimal("1.0")
        after_snapshot = balance.available_days

        tx = LeaveBalanceTransaction(
            balance_id=balance.id,
            transaction_type=TransactionType.ACCRUAL.value,
            amount=Decimal("1.0"),
            accrual_period=period_key,
            balance_before=before_snapshot,
            balance_after=after_snapshot,
            created_by_id=actor_id,
            notes=f"Monthly Earned Leave accrual for period {period_key} (+1.0 day)",
        )
        db.add(tx)
        credited_count += 1

    db.commit()
    return credited_count, skipped_messages


def prorate_annual_entitlements(
    db: Session,
    employee_id: int,
    year: int,
    joining_date: date,
    actor_id: int,
) -> List[LeaveBalance]:
    """Prorate Casual Leave and Sick Leave for new employees joining mid-year.

    Rule: CL and SL are prorated by remaining months. Earned Leave starts at 0.0.
    """
    if joining_date.year != year:
        # If employee joined in a prior year, full entitlement applies
        months_remaining = 12
    else:
        # Joining month counts as service
        months_remaining = 13 - joining_date.month

    categories = db.execute(
        select(LeaveCategory).where(LeaveCategory.is_active == True)
    ).scalars().all()

    balances = []
    for cat in categories:
        balance = get_or_create_leave_balance(
            db=db,
            employee_id=employee_id,
            category_id=cat.id,
            year=year,
            for_update=True,
        )

        if cat.code in (LeaveCategoryCode.CASUAL.value, LeaveCategoryCode.SICK.value):
            # Prorated entitlement = round((months_remaining / 12) * quota * 2) / 2
            raw = (Decimal(months_remaining) / Decimal(12)) * cat.annual_quota
            prorated = Decimal(round(float(raw) * 2) / 2).quantize(Decimal("0.1"))
            balance.entitled_days = prorated
        elif cat.code == LeaveCategoryCode.EARNED.value:
            # Earned leave starts at 0.0 entitled; accrues monthly
            balance.entitled_days = Decimal("0.0")

        balances.append(balance)

    db.commit()
    return balances


# ============================================================================
# HR Balance Adjustment
# ============================================================================

def adjust_leave_balance(
    db: Session,
    req: LeaveBalanceAdjustmentRequest,
    hr_actor_id: int,
) -> LeaveBalanceResponse:
    """Perform an administrative balance adjustment with mandatory audit explanation."""
    category = db.execute(
        select(LeaveCategory).where(LeaveCategory.code == req.category_code.value)
    ).scalar_one_or_none()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Leave category '{req.category_code}' not found.",
        )

    balance = get_or_create_leave_balance(
        db=db,
        employee_id=req.employee_id,
        category_id=category.id,
        year=req.year,
        for_update=True,
    )

    before_snapshot = balance.available_days

    # Apply adjustment to designated bucket
    if req.bucket == "entitled":
        if balance.entitled_days + req.amount < Decimal("0.0"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Entitled days cannot be adjusted below zero.")
        balance.entitled_days += req.amount
    elif req.bucket == "carried_forward":
        if balance.carried_forward_days + req.amount < Decimal("0.0"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Carried forward days cannot be adjusted below zero.")
        balance.carried_forward_days += req.amount
    elif req.bucket == "accrued":
        if balance.accrued_days + req.amount < Decimal("0.0"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Accrued days cannot be adjusted below zero.")
        balance.accrued_days += req.amount

    # Invariant: Available days must not become negative
    if balance.available_days < Decimal("0.0"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Adjustment would cause total available balance to drop below zero.",
        )

    after_snapshot = balance.available_days

    tx = LeaveBalanceTransaction(
        balance_id=balance.id,
        transaction_type=TransactionType.ADJUSTMENT.value,
        amount=req.amount,
        balance_before=before_snapshot,
        balance_after=after_snapshot,
        created_by_id=hr_actor_id,
        notes=f"HR Adjustment on {req.bucket} bucket: {req.reason}",
    )
    db.add(tx)

    audit_log = LeaveAuditLog(
        entity_type="leave_balance",
        entity_id=balance.id,
        action="BALANCE_ADJUSTED",
        actor_id=hr_actor_id,
        previous_state=f"available={before_snapshot}",
        new_state=f"available={after_snapshot}, bucket={req.bucket}, delta={req.amount}",
        notes=req.reason,
    )
    db.add(audit_log)
    db.commit()
    db.refresh(balance)

    return format_leave_balance_response(balance)


# ============================================================================
# Response Serialization Helpers
# ============================================================================

def format_leave_request_response(req: LeaveRequest) -> LeaveRequestResponse:
    """Format a LeaveRequest ORM object into a Pydantic response schema."""
    emp = req.employee
    rev = req.reviewer
    cat = req.category

    return LeaveRequestResponse(
        id=req.id,
        employee_id=req.employee_id,
        employee_name=emp.name if emp else None,
        employee_code=emp.employee_code if emp else None,
        category_id=req.category_id,
        category_code=cat.code if cat else "",
        category_name=cat.name if cat else "",
        start_date=req.start_date,
        end_date=req.end_date,
        is_half_day=req.is_half_day,
        half_day_period=req.half_day_period,
        total_days=req.total_days,
        reason=req.reason,
        status=req.status,
        reviewed_by_id=req.reviewed_by_id,
        reviewed_by_name=rev.name if rev else None,
        reviewed_at=req.reviewed_at,
        review_notes=req.review_notes,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


def format_leave_balance_response(bal: LeaveBalance) -> LeaveBalanceResponse:
    """Format a LeaveBalance ORM object into a Pydantic response schema."""
    cat = bal.category
    return LeaveBalanceResponse(
        id=bal.id,
        employee_id=bal.employee_id,
        category_id=bal.category_id,
        category_code=cat.code if cat else "",
        category_name=cat.name if cat else "",
        year=bal.year,
        entitled_days=bal.entitled_days,
        carried_forward_days=bal.carried_forward_days,
        accrued_days=bal.accrued_days,
        used_days=bal.used_days,
        reserved_days=bal.reserved_days,
        total_credited_days=bal.total_credited_days,
        available_days=bal.available_days,
    )


def format_compoff_claim_response(claim: CompOffClaim) -> CompOffClaimResponse:
    """Format a CompOffClaim ORM object into a Pydantic response schema."""
    emp = claim.employee
    rev = claim.reviewer
    return CompOffClaimResponse(
        id=claim.id,
        employee_id=claim.employee_id,
        employee_name=emp.name if emp else None,
        employee_code=emp.employee_code if emp else None,
        work_date=claim.work_date,
        days_claimed=claim.days_claimed,
        days_approved=claim.days_approved,
        remaining_days=claim.remaining_days,
        expiry_date=claim.expiry_date,
        reason=claim.reason,
        status=claim.status,
        credit_status=claim.credit_status,
        reviewed_by_id=claim.reviewed_by_id,
        reviewed_by_name=rev.name if rev else None,
        reviewed_at=claim.reviewed_at,
        review_notes=claim.review_notes,
        balance_transaction_id=claim.balance_transaction_id,
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


# ============================================================================
# Query Helper Functions
# ============================================================================

def get_leave_request_by_id(db: Session, request_id: int) -> Optional[LeaveRequest]:
    """Retrieve a single leave request by ID."""
    return db.get(LeaveRequest, request_id)


def get_employee_leave_requests(
    db: Session,
    employee_id: int,
    year: Optional[int] = None,
    status_filter: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[LeaveRequest]:
    """Retrieve leave requests for a specific employee with optional year and status filters."""
    stmt = select(LeaveRequest).where(LeaveRequest.employee_id == employee_id)
    if year is not None:
        stmt = stmt.where(func.extract("year", LeaveRequest.start_date) == year)
    if status_filter:
        stmt = stmt.where(LeaveRequest.status == status_filter.upper())

    stmt = stmt.order_by(LeaveRequest.created_at.desc()).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_pending_leave_requests(
    db: Session,
    department: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[LeaveRequest]:
    """Retrieve all pending leave requests across the company for HR/Admin review."""
    stmt = select(LeaveRequest).where(LeaveRequest.status == LeaveRequestStatus.PENDING.value)
    if department:
        stmt = stmt.join(Employee, LeaveRequest.employee_id == Employee.id).where(
            Employee.department == department
        )

    stmt = stmt.order_by(LeaveRequest.created_at.asc()).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_employee_balances(
    db: Session,
    employee_id: int,
    year: int,
) -> List[LeaveBalance]:
    """Retrieve all leave balances for an employee in the given year, initializing missing ones."""
    categories = db.execute(
        select(LeaveCategory).where(LeaveCategory.is_active == True).order_by(LeaveCategory.id.asc())
    ).scalars().all()

    balances = []
    for cat in categories:
        bal = get_or_create_leave_balance(
            db=db,
            employee_id=employee_id,
            category_id=cat.id,
            year=year,
            for_update=False,
        )
        balances.append(bal)

    return balances


def get_compoff_claims(
    db: Session,
    status_filter: Optional[str] = None,
    employee_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[CompOffClaim]:
    """Retrieve Comp-Off extra-work claims with optional status and employee filters."""
    stmt = select(CompOffClaim)
    if employee_id is not None:
        stmt = stmt.where(CompOffClaim.employee_id == employee_id)
    if status_filter:
        stmt = stmt.where(CompOffClaim.status == status_filter.upper())

    stmt = stmt.order_by(CompOffClaim.created_at.desc()).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars().all())
