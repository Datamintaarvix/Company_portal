"""Attendance service layer encapsulating business logic, concurrency safety, and audit logs."""

from datetime import date, datetime, timezone
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.timezone import (
    ensure_utc,
    format_ist_date,
    format_ist_time,
    get_current_work_date,
    is_weekend_day,
)
from app.models.attendance import (
    AttendanceCorrectionLog,
    AttendanceRecord,
    AttendanceStatus,
)
from app.models.employee import Employee
from app.schemas.attendance import (
    AttendanceCorrectionLogResponse,
    AttendanceCorrectionRequest,
    AttendanceResponse,
    PaginatedAttendanceResponse,
    TodayAttendanceResponse,
)


def record_to_response(record: AttendanceRecord) -> AttendanceResponse:
    """Transform an AttendanceRecord ORM instance into an enriched AttendanceResponse."""
    corrections_list = []
    if record.corrections:
        for c in record.corrections:
            corrector_name = c.corrector.name if c.corrector else None
            corrections_list.append(
                AttendanceCorrectionLogResponse(
                    id=c.id,
                    attendance_record_id=c.attendance_record_id,
                    corrected_by_id=c.corrected_by_id,
                    corrected_by_name=corrector_name,
                    corrected_at=c.corrected_at,
                    corrected_at_ist=format_ist_time(c.corrected_at),
                    reason=c.reason,
                    previous_check_in_at=c.previous_check_in_at,
                    previous_check_out_at=c.previous_check_out_at,
                    previous_status=c.previous_status,
                    new_check_in_at=c.new_check_in_at,
                    new_check_out_at=c.new_check_out_at,
                    new_status=c.new_status,
                )
            )

    emp = record.employee
    return AttendanceResponse(
        id=record.id,
        employee_id=record.employee_id,
        employee_name=emp.name if emp else None,
        employee_code=emp.employee_code if emp else None,
        department=emp.department if emp else None,
        work_date=record.work_date,
        work_date_formatted=format_ist_date(record.work_date),
        check_in_at=record.check_in_at,
        check_in_ist=format_ist_time(record.check_in_at),
        check_out_at=record.check_out_at,
        check_out_ist=format_ist_time(record.check_out_at),
        status=record.status,
        is_weekend=record.is_weekend,
        is_corrected=record.is_corrected,
        shift_code=record.shift_code,
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        corrections=corrections_list,
    )


def classify_incomplete_records(db: Session, employee_id: Optional[int] = None) -> None:
    """Classify past attendance records with missing checkouts as incomplete."""
    today = get_current_work_date()
    stmt = (
        update(AttendanceRecord)
        .where(
            AttendanceRecord.work_date < today,
            AttendanceRecord.check_out_at.is_(None),
            AttendanceRecord.status == AttendanceStatus.PRESENT.value,
        )
    )
    if employee_id is not None:
        stmt = stmt.where(AttendanceRecord.employee_id == employee_id)
    stmt = stmt.values(status=AttendanceStatus.INCOMPLETE.value)
    db.execute(stmt)
    db.commit()


def check_in_employee(
    db: Session,
    employee: Employee,
    notes: Optional[str] = None,
) -> AttendanceResponse:
    """Handle employee daily check-in with duplicate prevention and concurrency safety."""
    work_date = get_current_work_date()

    # Pre-check for duplicate check-in today
    existing = db.execute(
        select(AttendanceRecord).where(
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.work_date == work_date,
        )
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee is already checked in for today",
        )

    now_utc = datetime.now(timezone.utc)
    is_weekend = is_weekend_day(work_date)

    record = AttendanceRecord(
        employee_id=employee.id,
        work_date=work_date,
        check_in_at=now_utc,
        check_out_at=None,
        status=AttendanceStatus.PRESENT.value,
        is_weekend=is_weekend,
        is_corrected=False,
        shift_code="GENERAL",
        notes=notes.strip() if notes else None,
    )

    try:
        db.add(record)
        db.commit()
        db.refresh(record)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee is already checked in for today",
        )

    # Attach employee for response formatting
    record.employee = employee
    return record_to_response(record)


def check_out_employee(
    db: Session,
    employee: Employee,
    notes: Optional[str] = None,
) -> AttendanceResponse:
    """Handle employee daily check-out with validation and row-level locking."""
    work_date = get_current_work_date()

    # Row-level lock on today's attendance record
    record = db.execute(
        select(AttendanceRecord)
        .where(
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.work_date == work_date,
        )
        .options(
            selectinload(AttendanceRecord.employee),
            selectinload(AttendanceRecord.corrections).selectinload(AttendanceCorrectionLog.corrector),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot check out without an active check-in for today",
        )

    if record.check_out_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee is already checked out for today",
        )

    now_utc = datetime.now(timezone.utc)

    # Chronological validation
    if now_utc < record.check_in_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Checkout timestamp cannot be earlier than check-in timestamp",
        )

    record.check_out_at = now_utc
    if notes and notes.strip():
        if record.notes:
            record.notes = f"{record.notes} | {notes.strip()}"
        else:
            record.notes = notes.strip()

    db.commit()
    db.refresh(record)
    return record_to_response(record)


def get_today_status(db: Session, employee: Employee) -> TodayAttendanceResponse:
    """Retrieve today's shift and punch status for the given employee."""
    work_date = get_current_work_date()
    is_weekend = is_weekend_day(work_date)
    now_ist = format_ist_time(datetime.now(timezone.utc)) or ""

    record = db.execute(
        select(AttendanceRecord)
        .where(
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.work_date == work_date,
        )
        .options(
            selectinload(AttendanceRecord.employee),
            selectinload(AttendanceRecord.corrections).selectinload(AttendanceCorrectionLog.corrector),
        )
    ).scalar_one_or_none()

    if not record:
        return TodayAttendanceResponse(
            work_date=work_date,
            is_weekend=is_weekend,
            has_checked_in=False,
            has_checked_out=False,
            current_status=None,
            record=None,
            server_time_ist=now_ist,
        )

    return TodayAttendanceResponse(
        work_date=work_date,
        is_weekend=is_weekend,
        has_checked_in=True,
        has_checked_out=record.check_out_at is not None,
        current_status=record.status,
        record=record_to_response(record),
        server_time_ist=now_ist,
    )


def get_employee_attendance_history(
    db: Session,
    employee: Employee,
    page: int = 1,
    page_size: int = 20,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> PaginatedAttendanceResponse:
    """Retrieve personal attendance records for an employee with pagination and date range filtering."""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date cannot be after end_date",
        )

    classify_incomplete_records(db, employee_id=employee.id)

    query = select(AttendanceRecord).where(AttendanceRecord.employee_id == employee.id)

    if start_date:
        query = query.where(AttendanceRecord.work_date >= start_date)
    if end_date:
        query = query.where(AttendanceRecord.work_date <= end_date)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    records = db.scalars(
        query.options(
            selectinload(AttendanceRecord.employee),
            selectinload(AttendanceRecord.corrections).selectinload(AttendanceCorrectionLog.corrector),
        )
        .order_by(AttendanceRecord.work_date.desc(), AttendanceRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [record_to_response(r) for r in records]

    return PaginatedAttendanceResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def get_company_attendance_report(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    employee_id: Optional[int] = None,
    department: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status_filter: Optional[str] = None,
) -> PaginatedAttendanceResponse:
    """Retrieve company-wide attendance records for HR/Admin with multi-criteria filtering."""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date cannot be after end_date",
        )

    if status_filter:
        clean_status = status_filter.strip().lower()
        allowed_statuses = {s.value for s in AttendanceStatus}
        if clean_status not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid attendance status '{status_filter}'. Allowed: {', '.join(sorted(allowed_statuses))}",
            )
    else:
        clean_status = None

    classify_incomplete_records(db)

    query = select(AttendanceRecord).join(Employee, AttendanceRecord.employee_id == Employee.id)

    if employee_id:
        query = query.where(AttendanceRecord.employee_id == employee_id)
    if department and department.strip():
        query = query.where(Employee.department.ilike(f"%{department.strip()}%"))
    if start_date:
        query = query.where(AttendanceRecord.work_date >= start_date)
    if end_date:
        query = query.where(AttendanceRecord.work_date <= end_date)
    if clean_status:
        query = query.where(AttendanceRecord.status == clean_status)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    records = db.scalars(
        query.options(
            selectinload(AttendanceRecord.employee),
            selectinload(AttendanceRecord.corrections).selectinload(AttendanceCorrectionLog.corrector),
        )
        .order_by(AttendanceRecord.work_date.desc(), AttendanceRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [record_to_response(r) for r in records]

    return PaginatedAttendanceResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def correct_attendance_record(
    db: Session,
    record_id: int,
    actor: Employee,
    payload: AttendanceCorrectionRequest,
) -> AttendanceResponse:
    """Apply an audited correction to an attendance record and persist an immutable log."""
    record = db.execute(
        select(AttendanceRecord)
        .where(AttendanceRecord.id == record_id)
        .options(
            selectinload(AttendanceRecord.employee),
            selectinload(AttendanceRecord.corrections).selectinload(AttendanceCorrectionLog.corrector),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attendance record with ID {record_id} not found",
        )

    clean_reason = payload.reason.strip()
    if len(clean_reason) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Correction reason must be at least 3 characters",
        )

    # Before snapshot
    prev_check_in_at = record.check_in_at
    prev_check_out_at = record.check_out_at
    prev_status = record.status

    # After timestamps
    new_check_in_at = ensure_utc(payload.check_in_at) if payload.check_in_at is not None else record.check_in_at
    new_check_out_at = ensure_utc(payload.check_out_at) if payload.check_out_at is not None else record.check_out_at
    new_status = payload.status if payload.status is not None else record.status

    # Validate chronological ordering
    if new_check_out_at is not None and new_check_out_at < new_check_in_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Checkout timestamp cannot be earlier than check-in timestamp",
        )

    # Create immutable audit log entry
    audit_entry = AttendanceCorrectionLog(
        attendance_record_id=record.id,
        corrected_by_id=actor.id,
        reason=clean_reason,
        previous_check_in_at=prev_check_in_at,
        previous_check_out_at=prev_check_out_at,
        previous_status=prev_status,
        new_check_in_at=new_check_in_at,
        new_check_out_at=new_check_out_at,
        new_status=new_status,
    )
    db.add(audit_entry)

    # Apply updates to record
    record.check_in_at = new_check_in_at
    record.check_out_at = new_check_out_at
    record.status = new_status
    record.is_corrected = True
    if payload.notes is not None:
        record.notes = payload.notes.strip() if payload.notes else None

    db.commit()

    # Re-fetch with loaded relationships for response
    updated_record = db.execute(
        select(AttendanceRecord)
        .where(AttendanceRecord.id == record_id)
        .options(
            selectinload(AttendanceRecord.employee),
            selectinload(AttendanceRecord.corrections).selectinload(AttendanceCorrectionLog.corrector),
        )
    ).scalar_one()

    return record_to_response(updated_record)
