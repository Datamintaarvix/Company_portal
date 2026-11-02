"""Backend unit tests for Stage A Attendance and Correction models and timezone utilities."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.core.timezone import (
    ensure_utc,
    format_ist_date,
    format_ist_time,
    get_company_tz,
    get_current_work_date,
    is_weekend_day,
)
from app.db.base import Base
from app.models.attendance import AttendanceCorrectionLog, AttendanceRecord, AttendanceStatus
from app.models.employee import Employee


def test_attendance_table_metadata():
    """Verify that attendance_records table is properly configured in SQLAlchemy metadata."""
    table = Base.metadata.tables.get("attendance_records")
    assert table is not None

    # Verify column existence and attributes
    columns = table.columns
    assert "id" in columns
    assert "employee_id" in columns
    assert "work_date" in columns
    assert "check_in_at" in columns
    assert "check_out_at" in columns
    assert "status" in columns
    assert "is_weekend" in columns
    assert "is_corrected" in columns
    assert "shift_code" in columns
    assert "notes" in columns
    assert "created_at" in columns
    assert "updated_at" in columns

    # Verify nullability
    assert columns["employee_id"].nullable is False
    assert columns["work_date"].nullable is False
    assert columns["check_in_at"].nullable is False
    assert columns["check_out_at"].nullable is True
    assert columns["status"].nullable is False
    assert columns["is_weekend"].nullable is False
    assert columns["is_corrected"].nullable is False
    assert columns["shift_code"].nullable is False

    # Verify unique constraint on employee_id and work_date
    unique_constraints = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    emp_date_constraint = next(
        (c for c in unique_constraints if set(col.name for col in c.columns) == {"employee_id", "work_date"}),
        None,
    )
    assert emp_date_constraint is not None
    assert emp_date_constraint.name == "uq_attendance_employee_work_date"

    # Verify check constraints
    check_constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    constraint_names = {c.name for c in check_constraints}
    assert "check_attendance_checkout_after_checkin" in constraint_names
    assert "check_valid_attendance_status" in constraint_names

    # Verify foreign key constraint
    fk = list(columns["employee_id"].foreign_keys)[0]
    assert fk.target_fullname == "employees.id"
    assert fk.ondelete.upper() == "CASCADE"


def test_attendance_correction_logs_metadata():
    """Verify that attendance_correction_logs audit table is properly configured in metadata."""
    table = Base.metadata.tables.get("attendance_correction_logs")
    assert table is not None

    columns = table.columns
    assert "id" in columns
    assert "attendance_record_id" in columns
    assert "corrected_by_id" in columns
    assert "corrected_at" in columns
    assert "reason" in columns
    assert "previous_check_in_at" in columns
    assert "previous_check_out_at" in columns
    assert "previous_status" in columns
    assert "new_check_in_at" in columns
    assert "new_check_out_at" in columns
    assert "new_status" in columns

    # Verify foreign keys
    rec_fk = list(columns["attendance_record_id"].foreign_keys)[0]
    assert rec_fk.target_fullname == "attendance_records.id"
    assert rec_fk.ondelete.upper() == "CASCADE"

    corr_fk = list(columns["corrected_by_id"].foreign_keys)[0]
    assert corr_fk.target_fullname == "employees.id"
    assert corr_fk.ondelete.upper() == "SET NULL"


def test_attendance_relationships():
    """Verify model relationships between Employee, AttendanceRecord, and AttendanceCorrectionLog."""
    assert hasattr(Employee, "attendance_records")
    assert hasattr(AttendanceRecord, "employee")
    assert hasattr(AttendanceRecord, "corrections")
    assert hasattr(AttendanceCorrectionLog, "attendance_record")
    assert hasattr(AttendanceCorrectionLog, "corrector")


def test_attendance_status_enum():
    """Verify AttendanceStatus enum values."""
    assert AttendanceStatus.PRESENT.value == "present"
    assert AttendanceStatus.INCOMPLETE.value == "incomplete"
    assert AttendanceStatus.HALF_DAY.value == "half_day"
    assert AttendanceStatus.ABSENT.value == "absent"


def test_company_timezone_configuration():
    """Verify company timezone is Asia/Kolkata."""
    tz = get_company_tz()
    assert tz.key == "Asia/Kolkata"


def test_is_weekend_day():
    """Verify weekend detection (Saturday/Sunday)."""
    # 2026-10-31 is Saturday
    assert is_weekend_day(date(2026, 10, 31)) is True
    # 2026-11-01 is Sunday
    assert is_weekend_day(date(2026, 11, 1)) is True
    # 2026-10-29 is Thursday
    assert is_weekend_day(date(2026, 10, 29)) is False
    # 2026-10-30 is Friday
    assert is_weekend_day(date(2026, 10, 30)) is False


def test_format_ist_time_conversion():
    """Verify UTC datetime is correctly converted and formatted as IST string."""
    # 04:00 UTC == 09:30 AM IST (+05:30)
    utc_dt = datetime(2026, 10, 29, 4, 0, 0, tzinfo=timezone.utc)
    formatted = format_ist_time(utc_dt)
    assert formatted == "09:30 AM IST"

    # None handling
    assert format_ist_time(None) is None


def test_midnight_boundary_work_date_conversion():
    """Verify that late UTC hours correctly map to the next calendar date in IST."""
    # 18:45 UTC on 2026-10-29 is 00:15 IST on 2026-10-30 (+05:30)
    late_utc = datetime(2026, 10, 29, 18, 45, 0, tzinfo=timezone.utc)
    ist_tz = ZoneInfo("Asia/Kolkata")
    ist_date = late_utc.astimezone(ist_tz).date()
    assert ist_date == date(2026, 10, 30)


def test_ensure_utc():
    """Verify naive and aware datetimes are converted to UTC."""
    # Naive assumed to be in company timezone
    naive = datetime(2026, 10, 29, 9, 30, 0)
    utc_res = ensure_utc(naive)
    assert utc_res.tzinfo == timezone.utc
    assert utc_res.hour == 4
    assert utc_res.minute == 0
