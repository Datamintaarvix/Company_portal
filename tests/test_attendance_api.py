"""Automated API and security tests for Stage D Attendance Management.

Covers:
- Unauthenticated access rejection (HTTP 401)
- CSRF validation on all state-changing endpoints (check-in, check-out, correct)
- Successful check-in and check-out flows
- Duplicate check-in prevention (HTTP 409)
- Duplicate check-out prevention (HTTP 409)
- Check-out without check-in rejection (HTTP 400)
- Employee isolation (employees cannot see other records in /my or /today)
- RBAC boundary enforcement (employees cannot list company attendance or correct records: HTTP 403)
- HR and Admin authorized access to company reports and correction endpoints
- Date filtering, pagination, and validation (start_date > end_date -> 400)
- Status filter validation (invalid status -> 400)
- Incomplete shift classification for unclosed past workdays
- Audited corrections: mandatory reason, before/after snapshots, history preservation
- Invalid timestamp ordering rejection (checkout < checkin -> 400)
- Response secrecy (no password hashes or secrets exposed)
"""

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.core.timezone import get_current_work_date
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.attendance import (
    AttendanceCorrectionLog,
    AttendanceRecord,
    AttendanceStatus,
)
from app.models.employee import Employee, UserRole
from app.models.session import Session as DbSession
from app.services.rate_limiter import login_rate_limiter

client = TestClient(app)
settings = get_settings()

TEST_PASSWORD = "StrongSecurePassword123!"
ATT_ADMIN_EMAIL = "att.admin@company.com"
ATT_HR_EMAIL = "att.hr@company.com"
ATT_EMP1_EMAIL = "att.emp1@company.com"
ATT_EMP2_EMAIL = "att.emp2@company.com"
ALL_ATT_EMAILS = [ATT_ADMIN_EMAIL, ATT_HR_EMAIL, ATT_EMP1_EMAIL, ATT_EMP2_EMAIL]


# ============================================================================
# Phase 2.11B: Fail-Closed Test Database Target Validation & Isolation
# ============================================================================

FORBIDDEN_DB_NAMES = frozenset({
    "company_portal",         # dev database
    "company_portal_dev",     # dev alias
    "company_portal_staging", # staging database
    "company_portal_prod",    # prod database
    "postgres",               # default administrative database
    "template1",
})

ALLOWED_DB_NAMES = frozenset({"company_portal_test"})
ALLOWED_DB_PATTERN = re.compile(r"^company_portal_test_.*$")
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def get_isolated_test_db_url() -> tuple[URL, str, str]:
    """Resolve and strictly validate the connection URL for company_portal_pg_test.

    Returns:
        tuple[URL, str, str]: (validated_url_object, expected_user, expected_db)
    """
    # FAIL-CLOSED REQUIREMENT: Require explicitly configured test database role. Do not assume the role name.
    expected_user = (os.environ.get("POSTGRES_TEST_USER") or "").strip()
    if not expected_user:
        pytest.fail(
            "CRITICAL ISOLATION REFUSAL: Configured test database user is missing! "
            "The environment variable POSTGRES_TEST_USER must be explicitly set to the expected test role. "
            "Role cannot be assumed.",
            pytrace=False,
        )

    raw_url = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("RBAC_TEST_DB_URL")
        or os.environ.get("EPHEMERAL_PG_TEST_DB_URL")
    )

    if raw_url:
        parsed = make_url(raw_url)
    else:
        test_password = os.environ.get("POSTGRES_TEST_PASSWORD")
        test_host = os.environ.get("POSTGRES_TEST_HOST", "127.0.0.1")
        test_port = os.environ.get("POSTGRES_TEST_PORT", "5433")
        test_db = os.environ.get("POSTGRES_TEST_DB", "company_portal_test")
        if test_password:
            parsed = URL.create(
                drivername="postgresql+psycopg",
                username=expected_user,
                password=test_password,
                host=test_host,
                port=int(test_port),
                database=test_db,
            )
        else:
            parsed = None

    # FAIL-CLOSED SAFEGUARD 1: Missing configuration must NEVER silently fall back to dev database (port 5432)
    if not parsed:
        pytest.fail(
            "CRITICAL ISOLATION REFUSAL: Isolated test database connection is not configured! "
            "Set TEST_DATABASE_URL (or POSTGRES_TEST_PASSWORD) targeting port 5433. "
            "Silent fallback to development database (port 5432) is strictly prohibited.",
            pytrace=False,
        )

    # FAIL-CLOSED SAFEGUARD 2: Port MUST be 5433 (company_portal_pg_test container)
    if parsed.port != 5433:
        pytest.fail(
            f"CRITICAL ISOLATION VIOLATION: Test database MUST target host port 5433 "
            f"(company_portal_pg_test container). Attempted target port is {parsed.port}! "
            f"Execution refused to protect development (5432) and staging environments.",
            pytrace=False,
        )

    # FAIL-CLOSED SAFEGUARD 3: Host must be local
    if parsed.host not in ALLOWED_HOSTS:
        pytest.fail(
            f"CRITICAL ISOLATION VIOLATION: Test database host must be localhost/127.0.0.1, got '{parsed.host}'.",
            pytrace=False,
        )

    # FAIL-CLOSED SAFEGUARD 4: URL user must match configured role exactly (missing username fails closed)
    if parsed.username != expected_user:
        pytest.fail(
            f"CRITICAL ISOLATION VIOLATION: Database URL user '{parsed.username}' does not match "
            f"the configured expected test user '{expected_user}' (POSTGRES_TEST_USER).",
            pytrace=False,
        )

    # FAIL-CLOSED SAFEGUARD 5: Reject development, staging, production, postgres, and unexpected database names
    db_name = (parsed.database or "").lower()
    if db_name in FORBIDDEN_DB_NAMES:
        pytest.fail(
            f"CRITICAL ISOLATION VIOLATION: Database '{db_name}' is in the forbidden protected database list! "
            f"Development and staging databases can never be targeted by tests.",
            pytrace=False,
        )

    if db_name not in ALLOWED_DB_NAMES and not ALLOWED_DB_PATTERN.match(db_name):
        pytest.fail(
            f"CRITICAL ISOLATION VIOLATION: Database name '{db_name}' is not an authorized test database! "
            f"Only 'company_portal_test' or names matching 'company_portal_test_*' are permitted.",
            pytrace=False,
        )

    # Ensure psycopg driver is specified
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+psycopg")

    # Ensure password is preserved internally without exposing or printing
    return parsed, expected_user, db_name


def verify_test_database_identity(engine, expected_user: str, expected_db: str) -> None:
    """Validate host, port, database name, and PostgreSQL server identity before any database write."""
    url = engine.url

    # 1. Engine host is localhost
    if url.host not in ALLOWED_HOSTS:
        raise RuntimeError(f"SAFETY ABORT: Engine host '{url.host}' is not localhost/127.0.0.1!")

    # 2. Engine port is exactly 5433
    if url.port != 5433:
        raise RuntimeError(f"SAFETY ABORT: Engine port is {url.port}, expected exactly 5433!")

    # 3. Engine database name is an allowed dedicated test database
    engine_db = (url.database or "").lower()
    if engine_db in FORBIDDEN_DB_NAMES:
        raise RuntimeError(f"SAFETY ABORT: Refusing to operate on forbidden database '{engine_db}'!")
    if engine_db not in ALLOWED_DB_NAMES and not ALLOWED_DB_PATTERN.match(engine_db):
        raise RuntimeError(
            f"SAFETY ABORT: Engine database '{engine_db}' is not an authorized test database! "
            f"Only 'company_portal_test' or names matching 'company_portal_test_*' are permitted."
        )

    # Execute identity check against live database
    with engine.connect() as conn:
        row = conn.execute(text("SELECT current_database(), current_user, version()")).one()
        curr_db, curr_user, version_str = row[0], row[1], row[2]

        # 4. Live current_database() exactly matches the configured database name
        if curr_db.lower() != expected_db.lower():
            raise RuntimeError(
                f"SAFETY ABORT: Live database '{curr_db}' does not match expected configured database '{expected_db}'!"
            )
        if curr_db.lower() in FORBIDDEN_DB_NAMES:
            raise RuntimeError(f"SAFETY ABORT: Connected live database is '{curr_db}', which is forbidden!")
        if curr_db.lower() not in ALLOWED_DB_NAMES and not ALLOWED_DB_PATTERN.match(curr_db.lower()):
            raise RuntimeError(f"SAFETY ABORT: Connected live database '{curr_db}' is not an authorized test database!")

        # 5. Live current_user exactly matches the configured expected role
        if curr_user != expected_user:
            raise RuntimeError(
                f"SAFETY ABORT: Connected live user '{curr_user}' does not match expected configured role '{expected_user}'!"
            )

        # 6. Server is PostgreSQL
        if "PostgreSQL" not in version_str:
            raise RuntimeError(f"SAFETY ABORT: Target server is not PostgreSQL! Version: {version_str}")


# Initialize isolated test engine targeting only port 5433
TEST_DB_URL, EXPECTED_USER, EXPECTED_DB = get_isolated_test_db_url()
test_engine = create_engine(
    TEST_DB_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
)
verify_test_database_identity(test_engine, expected_user=EXPECTED_USER, expected_db=EXPECTED_DB)
TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


@pytest.fixture(scope="session", autouse=True)
def init_test_db_schema():
    """Ensure test database identity is verified and schema tables are created once per test session."""
    try:
        verify_test_database_identity(test_engine, expected_user=EXPECTED_USER, expected_db=EXPECTED_DB)
        Base.metadata.create_all(bind=test_engine)
        yield
    finally:
        test_engine.dispose()


@pytest.fixture(autouse=True)
def override_db_dependency():
    """Ensure all FastAPI route handlers use the isolated test database on port 5433."""
    def _get_test_db() -> Generator[Session, None, None]:
        db: Session = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_test_db
    try:
        yield
    finally:
        if previous_override is not None:
            app.dependency_overrides[get_db] = previous_override
        else:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def setup_attendance_users(init_test_db_schema):
    """Setup isolated test accounts for Admin, HR, and Employees with clean attendance records."""
    login_rate_limiter.clear()
    db = TestSessionLocal()
    try:
        # Pre-clean
        test_emp_ids = db.scalars(
            select(Employee.id).where(Employee.email.in_(ALL_ATT_EMAILS))
        ).all()

        if test_emp_ids:
            # Delete logs, records, sessions
            db.query(AttendanceCorrectionLog).filter(
                AttendanceCorrectionLog.attendance_record_id.in_(
                    select(AttendanceRecord.id).where(AttendanceRecord.employee_id.in_(test_emp_ids))
                )
            ).delete(synchronize_session=False)
            db.query(AttendanceRecord).filter(
                AttendanceRecord.employee_id.in_(test_emp_ids)
            ).delete(synchronize_session=False)
            db.query(DbSession).filter(
                DbSession.employee_id.in_(test_emp_ids)
            ).delete(synchronize_session=False)
            db.query(Employee).filter(
                Employee.id.in_(test_emp_ids)
            ).delete(synchronize_session=False)
            db.commit()

        # Seed users
        admin_user = Employee(
            employee_code="ATT-ADM-001",
            name="Attendance Admin",
            email=ATT_ADMIN_EMAIL,
            department="IT",
            role=UserRole.ADMIN.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        hr_user = Employee(
            employee_code="ATT-HR-001",
            name="Attendance HR",
            email=ATT_HR_EMAIL,
            department="People Ops",
            role=UserRole.HR.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        emp1_user = Employee(
            employee_code="ATT-EMP-001",
            name="Employee One",
            email=ATT_EMP1_EMAIL,
            department="Engineering",
            role=UserRole.EMPLOYEE.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        emp2_user = Employee(
            employee_code="ATT-EMP-002",
            name="Employee Two",
            email=ATT_EMP2_EMAIL,
            department="Design",
            role=UserRole.EMPLOYEE.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )

        db.add_all([admin_user, hr_user, emp1_user, emp2_user])
        db.commit()

        user_ids = {
            "admin_id": admin_user.id,
            "hr_id": hr_user.id,
            "emp1_id": emp1_user.id,
            "emp2_id": emp2_user.id,
        }
    finally:
        db.close()

    try:
        yield user_ids
    finally:
        # Teardown (always executes, even if assertions failed)
        login_rate_limiter.clear()
        db = TestSessionLocal()
        try:
            teardown_ids = db.scalars(
                select(Employee.id).where(Employee.email.in_(ALL_ATT_EMAILS))
            ).all()
            if teardown_ids:
                db.query(AttendanceCorrectionLog).filter(
                    AttendanceCorrectionLog.attendance_record_id.in_(
                        select(AttendanceRecord.id).where(AttendanceRecord.employee_id.in_(teardown_ids))
                    )
                ).delete(synchronize_session=False)
                db.query(AttendanceRecord).filter(
                    AttendanceRecord.employee_id.in_(teardown_ids)
                ).delete(synchronize_session=False)
                db.query(DbSession).filter(
                    DbSession.employee_id.in_(teardown_ids)
                ).delete(synchronize_session=False)
                db.query(Employee).filter(
                    Employee.id.in_(teardown_ids)
                ).delete(synchronize_session=False)
                db.commit()
        finally:
            db.close()


def _login(email: str, password: str = TEST_PASSWORD) -> tuple[TestClient, str]:
    """Helper to authenticate and return a client equipped with session cookie and CSRF token."""
    tc = TestClient(app)
    resp = tc.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.text}"
    csrf_token = resp.cookies[settings.CSRF_COOKIE_NAME]
    return tc, csrf_token


# 1. Unauthenticated endpoints rejected with HTTP 401
def test_unauthenticated_attendance_endpoints_rejected():
    """Verify all attendance endpoints require valid authentication."""
    anon = TestClient(app)

    assert anon.post("/attendance/check-in").status_code == 401
    assert anon.post("/attendance/check-out").status_code == 401
    assert anon.get("/attendance/today").status_code == 401
    assert anon.get("/attendance/my").status_code == 401
    assert anon.get("/attendance").status_code == 401
    assert anon.patch("/attendance/1/correct", json={"reason": "test"}).status_code == 401


# 2. CSRF protection on state-changing endpoints
def test_csrf_protection_on_attendance_endpoints(setup_attendance_users):
    """Verify state-changing endpoints reject requests missing or having invalid CSRF tokens."""
    emp_client, csrf = _login(ATT_EMP1_EMAIL)

    # Missing CSRF header
    resp_no_csrf = emp_client.post("/attendance/check-in", json={})
    assert resp_no_csrf.status_code == 403
    assert "CSRF" in resp_no_csrf.json()["detail"]

    # Invalid CSRF header
    resp_bad_csrf = emp_client.post(
        "/attendance/check-in",
        headers={"X-CSRF-Token": "invalid_csrf_token_value"},
        json={},
    )
    assert resp_bad_csrf.status_code == 403

    # Check-out missing CSRF
    resp_co_no_csrf = emp_client.post("/attendance/check-out", json={})
    assert resp_co_no_csrf.status_code == 403

    # Correction missing CSRF (Admin)
    admin_client, _ = _login(ATT_ADMIN_EMAIL)
    resp_corr_no_csrf = admin_client.patch(
        "/attendance/1/correct",
        json={"reason": "Audit justification"},
    )
    assert resp_corr_no_csrf.status_code == 403


# 3. Successful check-in and check-out flow
def test_successful_check_in_and_check_out_flow(setup_attendance_users):
    """Verify complete self-service daily punch cycle."""
    emp_client, csrf = _login(ATT_EMP1_EMAIL)

    # 1. Before check-in: /attendance/today
    today_resp = emp_client.get("/attendance/today")
    assert today_resp.status_code == 200
    data = today_resp.json()
    assert data["has_checked_in"] is False
    assert data["has_checked_out"] is False
    assert data["record"] is None
    assert data["server_time_ist"] is not None

    # 2. Check-in
    checkin_resp = emp_client.post(
        "/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"notes": "Starting workday"},
    )
    assert checkin_resp.status_code == 201
    record = checkin_resp.json()
    assert record["status"] == "present"
    assert record["is_corrected"] is False
    assert record["check_in_at"] is not None
    assert record["check_in_ist"] is not None
    assert record["check_out_at"] is None
    assert record["employee_name"] == "Employee One"

    # 3. Mid-day check: /attendance/today
    mid_resp = emp_client.get("/attendance/today")
    assert mid_resp.status_code == 200
    mid_data = mid_resp.json()
    assert mid_data["has_checked_in"] is True
    assert mid_data["has_checked_out"] is False
    assert mid_data["current_status"] == "present"
    assert mid_data["record"]["notes"] == "Starting workday"

    # 4. Check-out
    checkout_resp = emp_client.post(
        "/attendance/check-out",
        headers={"X-CSRF-Token": csrf},
        json={"notes": "Work completed"},
    )
    assert checkout_resp.status_code == 200
    updated_record = checkout_resp.json()
    assert updated_record["check_out_at"] is not None
    assert updated_record["check_out_ist"] is not None
    assert "Starting workday" in updated_record["notes"]
    assert "Work completed" in updated_record["notes"]

    # 5. After check-out: /attendance/today
    final_resp = emp_client.get("/attendance/today")
    assert final_resp.status_code == 200
    final_data = final_resp.json()
    assert final_data["has_checked_in"] is True
    assert final_data["has_checked_out"] is True


# 4. Duplicate check-in rejected with HTTP 409
def test_duplicate_check_in_rejected(setup_attendance_users):
    """Verify an employee cannot check in twice on the same workday."""
    emp_client, csrf = _login(ATT_EMP1_EMAIL)

    resp1 = emp_client.post("/attendance/check-in", headers={"X-CSRF-Token": csrf}, json={})
    assert resp1.status_code == 201

    resp2 = emp_client.post("/attendance/check-in", headers={"X-CSRF-Token": csrf}, json={})
    assert resp2.status_code == 409
    assert "already checked in" in resp2.json()["detail"]


# 5. Check-out without check-in rejected with HTTP 400
def test_checkout_without_checkin_rejected(setup_attendance_users):
    """Verify attempting to check out without an active check-in returns HTTP 400."""
    emp2_client, csrf = _login(ATT_EMP2_EMAIL)

    resp = emp2_client.post("/attendance/check-out", headers={"X-CSRF-Token": csrf}, json={})
    assert resp.status_code == 400
    assert "active check-in" in resp.json()["detail"]


# 6. Duplicate check-out rejected with HTTP 409
def test_duplicate_checkout_rejected(setup_attendance_users):
    """Verify an employee cannot check out more than once per workday."""
    emp_client, csrf = _login(ATT_EMP1_EMAIL)

    emp_client.post("/attendance/check-in", headers={"X-CSRF-Token": csrf}, json={})
    co1 = emp_client.post("/attendance/check-out", headers={"X-CSRF-Token": csrf}, json={})
    assert co1.status_code == 200

    co2 = emp_client.post("/attendance/check-out", headers={"X-CSRF-Token": csrf}, json={})
    assert co2.status_code == 409
    assert "already checked out" in co2.json()["detail"]


# 7. Employee isolation and RBAC boundary enforcement
def test_employee_isolation_and_rbac_boundaries(setup_attendance_users):
    """Verify employees can only see their own attendance and cannot access HR/Admin endpoints."""
    emp1_client, csrf1 = _login(ATT_EMP1_EMAIL)
    emp2_client, csrf2 = _login(ATT_EMP2_EMAIL)

    # Emp 1 checks in
    checkin_resp = emp1_client.post("/attendance/check-in", headers={"X-CSRF-Token": csrf1}, json={})
    record_id = checkin_resp.json()["id"]

    # Emp 2 checks today: should be blank
    emp2_today = emp2_client.get("/attendance/today").json()
    assert emp2_today["has_checked_in"] is False
    assert emp2_today["record"] is None

    # Emp 2 checks history: should be empty
    emp2_history = emp2_client.get("/attendance/my").json()
    assert emp2_history["total"] == 0
    assert len(emp2_history["items"]) == 0

    # Emp 1 cannot view company-wide attendance
    emp1_company = emp1_client.get("/attendance")
    assert emp1_company.status_code == 403

    # Emp 1 cannot self-correct their attendance
    emp1_correct = emp1_client.patch(
        f"/attendance/{record_id}/correct",
        headers={"X-CSRF-Token": csrf1},
        json={"reason": "Unauthorized self-correction"},
    )
    assert emp1_correct.status_code == 403


# 8. HR and Admin can access company attendance reports
def test_hr_and_admin_can_access_company_attendance(setup_attendance_users):
    """Verify HR and Admin roles are authorized to view company attendance."""
    emp_client, csrf = _login(ATT_EMP1_EMAIL)
    emp_client.post("/attendance/check-in", headers={"X-CSRF-Token": csrf}, json={})

    hr_client, _ = _login(ATT_HR_EMAIL)
    hr_resp = hr_client.get("/attendance")
    assert hr_resp.status_code == 200
    assert hr_resp.json()["total"] >= 1

    admin_client, _ = _login(ATT_ADMIN_EMAIL)
    admin_resp = admin_client.get("/attendance")
    assert admin_resp.status_code == 200
    assert admin_resp.json()["total"] >= 1


# 9. Company attendance filtering and pagination
def test_company_attendance_filtering_and_pagination(setup_attendance_users):
    """Verify filtering by employee_id, department, date range, and status."""
    db = TestSessionLocal()
    emp1_id = setup_attendance_users["emp1_id"]
    emp2_id = setup_attendance_users["emp2_id"]

    try:
        # Seed past records
        today = get_current_work_date()
        past_date1 = today - timedelta(days=2)
        past_date2 = today - timedelta(days=3)

        rec1 = AttendanceRecord(
            employee_id=emp1_id,
            work_date=past_date1,
            check_in_at=datetime.now(timezone.utc) - timedelta(days=2, hours=8),
            check_out_at=datetime.now(timezone.utc) - timedelta(days=2),
            status="present",
            is_weekend=False,
            is_corrected=False,
            shift_code="GENERAL",
        )
        rec2 = AttendanceRecord(
            employee_id=emp2_id,
            work_date=past_date2,
            check_in_at=datetime.now(timezone.utc) - timedelta(days=3, hours=8),
            check_out_at=datetime.now(timezone.utc) - timedelta(days=3),
            status="half_day",
            is_weekend=False,
            is_corrected=False,
            shift_code="GENERAL",
        )
        db.add_all([rec1, rec2])
        db.commit()
    finally:
        db.close()

    hr_client, _ = _login(ATT_HR_EMAIL)

    # Filter by employee_id
    resp_emp1 = hr_client.get(f"/attendance?employee_id={emp1_id}")
    assert resp_emp1.status_code == 200
    assert all(r["employee_id"] == emp1_id for r in resp_emp1.json()["items"])

    # Filter by department
    resp_dept = hr_client.get("/attendance?department=Engineering")
    assert resp_dept.status_code == 200
    assert any(r["department"] == "Engineering" for r in resp_dept.json()["items"])

    # Filter by status
    resp_status = hr_client.get("/attendance?status=half_day")
    assert resp_status.status_code == 200
    assert all(r["status"] == "half_day" for r in resp_status.json()["items"])

    # Filter by date range
    resp_date = hr_client.get(f"/attendance?start_date={past_date2.isoformat()}&end_date={past_date1.isoformat()}")
    assert resp_date.status_code == 200
    assert resp_date.json()["total"] >= 2

    # Invalid date range: start_date > end_date -> 400
    resp_inv_date = hr_client.get(f"/attendance?start_date={today.isoformat()}&end_date={past_date1.isoformat()}")
    assert resp_inv_date.status_code == 400
    assert "start_date" in resp_inv_date.json()["detail"]

    # Invalid status -> 400
    resp_inv_status = hr_client.get("/attendance?status=invalid_status")
    assert resp_inv_status.status_code == 400

    # Pagination: page_size=1
    resp_paged = hr_client.get("/attendance?page=1&page_size=1")
    assert resp_paged.status_code == 200
    pdata = resp_paged.json()
    assert pdata["page_size"] == 1
    assert len(pdata["items"]) == 1
    assert pdata["total_pages"] >= 2


# 10. Incomplete shift classification
def test_incomplete_shift_classification(setup_attendance_users):
    """Verify unclosed past attendance records are classified as incomplete without data distortion."""
    db = TestSessionLocal()
    emp1_id = setup_attendance_users["emp1_id"]
    past_date = get_current_work_date() - timedelta(days=1)

    try:
        unclosed_rec = AttendanceRecord(
            employee_id=emp1_id,
            work_date=past_date,
            check_in_at=datetime.now(timezone.utc) - timedelta(days=1, hours=8),
            check_out_at=None,
            status="present",
            is_weekend=False,
            is_corrected=False,
            shift_code="GENERAL",
        )
        db.add(unclosed_rec)
        db.commit()
        rec_id = unclosed_rec.id
    finally:
        db.close()

    emp_client, _ = _login(ATT_EMP1_EMAIL)
    history = emp_client.get("/attendance/my").json()
    matched = next((item for item in history["items"] if item["id"] == rec_id), None)
    assert matched is not None
    assert matched["status"] == "incomplete"
    assert matched["check_out_at"] is None
    assert matched["is_corrected"] is False


# 11. Audited attendance correction by HR and Admin
def test_audited_attendance_correction_workflow(setup_attendance_users):
    """Verify HR/Admin can correct records with a mandatory reason and immutable audit trail."""
    db = TestSessionLocal()
    emp1_id = setup_attendance_users["emp1_id"]
    past_date = get_current_work_date() - timedelta(days=2)

    original_in = datetime.now(timezone.utc) - timedelta(days=2, hours=9)
    try:
        rec = AttendanceRecord(
            employee_id=emp1_id,
            work_date=past_date,
            check_in_at=original_in,
            check_out_at=None,
            status="incomplete",
            is_weekend=False,
            is_corrected=False,
            shift_code="GENERAL",
        )
        db.add(rec)
        db.commit()
        record_id = rec.id
    finally:
        db.close()

    hr_client, hr_csrf = _login(ATT_HR_EMAIL)
    fixed_out = original_in + timedelta(hours=8)

    # HR applies first correction
    corr1 = hr_client.patch(
        f"/attendance/{record_id}/correct",
        headers={"X-CSRF-Token": hr_csrf},
        json={
            "reason": "Employee forgot to punch out; confirmed with manager",
            "check_out_at": fixed_out.isoformat(),
            "status": "present",
        },
    )
    assert corr1.status_code == 200
    res1 = corr1.json()
    assert res1["is_corrected"] is True
    assert res1["status"] == "present"
    assert len(res1["corrections"]) == 1
    assert res1["corrections"][0]["reason"] == "Employee forgot to punch out; confirmed with manager"
    assert res1["corrections"][0]["previous_status"] == "incomplete"
    assert res1["corrections"][0]["new_status"] == "present"

    # Admin applies second correction (history preserved)
    admin_client, admin_csrf = _login(ATT_ADMIN_EMAIL)
    corr2 = admin_client.patch(
        f"/attendance/{record_id}/correct",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "reason": "Adjusted status to half day per leave policy",
            "status": "half_day",
        },
    )
    assert corr2.status_code == 200
    res2 = corr2.json()
    assert res2["status"] == "half_day"
    assert len(res2["corrections"]) == 2
    # Verify prior audit entry was not overwritten
    reasons = [c["reason"] for c in res2["corrections"]]
    assert "Employee forgot to punch out; confirmed with manager" in reasons
    assert "Adjusted status to half day per leave policy" in reasons


# 12. Correction validation rules
def test_correction_validation_rules(setup_attendance_users):
    """Verify validation: empty reason, invalid status, invalid timestamp ordering, non-existent record."""
    admin_client, csrf = _login(ATT_ADMIN_EMAIL)
    emp_client, emp_csrf = _login(ATT_EMP1_EMAIL)

    checkin = emp_client.post("/attendance/check-in", headers={"X-CSRF-Token": emp_csrf}, json={}).json()
    rec_id = checkin["id"]

    # Reason too short / whitespace only -> 400
    resp_short = admin_client.patch(
        f"/attendance/{rec_id}/correct",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "  a  "},
    )
    assert resp_short.status_code == 422 or resp_short.status_code == 400

    # Non-existent record -> 404
    resp_404 = admin_client.patch(
        "/attendance/99999999/correct",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Valid reason for missing record"},
    )
    assert resp_404.status_code == 404

    # Invalid status value -> 422 or 400
    resp_bad_stat = admin_client.patch(
        f"/attendance/{rec_id}/correct",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Valid reason", "status": "unknown_status"},
    )
    assert resp_bad_stat.status_code in (400, 422)

    # Invalid timestamp ordering: checkout earlier than checkin -> 400
    bad_checkout = datetime.now(timezone.utc) - timedelta(hours=10)
    resp_bad_order = admin_client.patch(
        f"/attendance/{rec_id}/correct",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Chronological error test", "check_out_at": bad_checkout.isoformat()},
    )
    assert resp_bad_order.status_code == 400
    assert "Checkout timestamp cannot be earlier than check-in" in resp_bad_order.json()["detail"]


# 13. Responses never expose password hashes or session tokens
def test_attendance_responses_never_expose_secrets(setup_attendance_users):
    """Verify attendance responses never leak sensitive secrets or tokens."""
    emp_client, csrf = _login(ATT_EMP1_EMAIL)
    resp = emp_client.post("/attendance/check-in", headers={"X-CSRF-Token": csrf}, json={})
    content = resp.text

    assert "password_hash" not in content
    assert "argon2" not in content
    assert "session_token" not in content
    assert "csrf_token" not in content


# 14. Concurrent duplicate check-in race conditions are handled safely
def test_concurrent_checkin_race_condition(setup_attendance_users):
    """Verify concurrent check-in requests for the same employee result in exactly one 201 and 409 for all others."""
    import concurrent.futures

    emp_client, csrf = _login(ATT_EMP1_EMAIL)
    cookie_val = emp_client.cookies[settings.SESSION_COOKIE_NAME]

    results = []

    def make_punch():
        tc = TestClient(app)
        tc.cookies.set(settings.SESSION_COOKIE_NAME, cookie_val)
        return tc.post("/attendance/check-in", headers={"X-CSRF-Token": csrf}, json={})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_punch) for _ in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result().status_code)

    assert results.count(201) == 1, f"Expected exactly one 201 Created, got: {results}"
    assert results.count(409) == 4, f"Expected four 409 Conflict, got: {results}"
