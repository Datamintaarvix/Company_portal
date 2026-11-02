"""Automated security tests for Stage 4 Role-Based Access Control (RBAC).

Covers:
- Unauthenticated access rejection (HTTP 401)
- Employee self-access (/employees/me, /employees/{id})
- Regular employee boundary enforcement (cannot view others, cannot list directory, cannot alter status/role/department)
- HR permissions and boundary enforcement (directory listing, viewing records, modifying allowed fields, account provisioning)
- HR admin-creation restriction (HTTP 403 when attempting to create admin)
- Admin privilege enforcement (role modification, status activation/deactivation, account provisioning)
- Admin protection against self-demotion and self-deactivation (HTTP 400)
- Account deactivation immediate session revocation (sessions deleted from PostgreSQL)
- Deactivated user rejection on subsequent requests (HTTP 401)
- Response secrecy (no password hashes or session tokens leaked)
- Mass assignment prevention
"""

import os
import re
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.employee import Employee, UserRole
from app.models.session import Session as DbSession
from app.services.rate_limiter import login_rate_limiter

client = TestClient(app)
settings = get_settings()

TEST_PASSWORD = "StrongSecurePassword123!"
ADMIN_EMAIL = "rbac.admin@company.com"
HR_EMAIL = "rbac.hr@company.com"
EMP_EMAIL = "rbac.emp@company.com"
TARGET_EMAIL = "rbac.target@company.com"
ALL_TEST_EMAILS = [ADMIN_EMAIL, HR_EMAIL, EMP_EMAIL, TARGET_EMAIL, "rbac.created@company.com"]


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
    verify_test_database_identity(test_engine, expected_user=EXPECTED_USER, expected_db=EXPECTED_DB)
    Base.metadata.create_all(bind=test_engine)
    yield


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
def setup_rbac_users(init_test_db_schema):
    """Setup isolated test accounts for Admin, HR, Employee, and Target Employee."""
    login_rate_limiter.clear()
    db = TestSessionLocal()
    try:
        # Pre-clean
        db.query(DbSession).filter(
            DbSession.employee_id.in_(
                select(Employee.id).where(Employee.email.in_(ALL_TEST_EMAILS))
            )
        ).delete(synchronize_session=False)
        db.query(Employee).filter(Employee.email.in_(ALL_TEST_EMAILS)).delete(synchronize_session=False)
        db.commit()

        # Seed users
        admin_user = Employee(
            employee_code="RBAC-ADM-001",
            name="RBAC Admin",
            email=ADMIN_EMAIL,
            department="IT Administration",
            role=UserRole.ADMIN.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        hr_user = Employee(
            employee_code="RBAC-HR-001",
            name="RBAC HR",
            email=HR_EMAIL,
            department="Human Resources",
            role=UserRole.HR.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        emp_user = Employee(
            employee_code="RBAC-EMP-001",
            name="RBAC Employee",
            email=EMP_EMAIL,
            department="Engineering",
            role=UserRole.EMPLOYEE.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        target_user = Employee(
            employee_code="RBAC-EMP-002",
            name="Target Employee",
            email=TARGET_EMAIL,
            department="Finance",
            role=UserRole.EMPLOYEE.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )

        db.add_all([admin_user, hr_user, emp_user, target_user])
        db.commit()

        # Cache IDs
        admin_id = admin_user.id
        hr_id = hr_user.id
        emp_id = emp_user.id
        target_id = target_user.id
    finally:
        db.close()

    yield {
        "admin_id": admin_id,
        "hr_id": hr_id,
        "emp_id": emp_id,
        "target_id": target_id,
    }

    # Post-clean (always executes, even if assertions failed)
    db = TestSessionLocal()
    login_rate_limiter.clear()
    try:
        db.query(DbSession).filter(
            DbSession.employee_id.in_(
                select(Employee.id).where(Employee.email.in_(ALL_TEST_EMAILS))
            )
        ).delete(synchronize_session=False)
        db.query(Employee).filter(Employee.email.in_(ALL_TEST_EMAILS)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _login_as(email: str, password: str = TEST_PASSWORD) -> tuple[TestClient, str]:
    """Helper to authenticate a user and return configured client and CSRF token."""
    tc = TestClient(app)
    resp = tc.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    csrf_token = resp.cookies[settings.CSRF_COOKIE_NAME]
    return tc, csrf_token


# 1. Unauthenticated users cannot access protected endpoints
def test_unauthenticated_requests_rejected(setup_rbac_users):
    """Verify unauthenticated requests to protected endpoints return 401."""
    clean_client = TestClient(app)
    target_id = setup_rbac_users["target_id"]

    assert clean_client.get("/employees/me").status_code == 401
    assert clean_client.get("/employees").status_code == 401
    assert clean_client.get(f"/employees/{target_id}").status_code == 401
    assert clean_client.patch(f"/employees/{target_id}", json={"name": "New"}).status_code == 401
    assert clean_client.patch(f"/admin/employees/{target_id}/role", json={"role": "hr"}).status_code == 401
    assert clean_client.patch(f"/admin/employees/{target_id}/status", json={"is_active": False}).status_code == 401
    assert clean_client.post("/hr/employees", json={"name": "A"}).status_code == 401


# 2. Employees can access their own profile
def test_employee_can_access_own_profile(setup_rbac_users):
    """Verify regular employee can access /employees/me and their own record via /employees/{id}."""
    emp_client, _ = _login_as(EMP_EMAIL)
    emp_id = setup_rbac_users["emp_id"]

    # /employees/me
    resp_me = emp_client.get("/employees/me")
    assert resp_me.status_code == 200
    assert resp_me.json()["email"] == EMP_EMAIL
    assert resp_me.json()["role"] == "employee"

    # /employees/{own_id}
    resp_own = emp_client.get(f"/employees/{emp_id}")
    assert resp_own.status_code == 200
    assert resp_own.json()["id"] == emp_id
    assert resp_own.json()["name"] == "RBAC Employee"


# 3. Employees cannot access unauthorized employee records
def test_employee_cannot_access_other_records(setup_rbac_users):
    """Verify regular employee receives 403 when requesting other employee records."""
    emp_client, _ = _login_as(EMP_EMAIL)
    target_id = setup_rbac_users["target_id"]

    resp = emp_client.get(f"/employees/{target_id}")
    assert resp.status_code == 403
    assert "forbidden" in resp.json()["detail"].lower()


# 4. Employees cannot list all employees
def test_employee_cannot_list_directory():
    """Verify regular employees cannot access the directory listing endpoint."""
    emp_client, _ = _login_as(EMP_EMAIL)

    resp = emp_client.get("/employees")
    assert resp.status_code == 403
    assert "forbidden" in resp.json()["detail"].lower()


# 5. Employees cannot change roles or status
def test_employee_cannot_access_admin_endpoints(setup_rbac_users):
    """Verify regular employees cannot access administrative role or status management."""
    emp_client, csrf = _login_as(EMP_EMAIL)
    target_id = setup_rbac_users["target_id"]

    # Role change attempt
    resp_role = emp_client.patch(
        f"/admin/employees/{target_id}/role",
        json={"role": "admin"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_role.status_code == 403

    # Status change attempt
    resp_status = emp_client.patch(
        f"/admin/employees/{target_id}/status",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_status.status_code == 403


# 6. Employees can only update self-service fields on their own profile
def test_employee_self_service_update_boundaries(setup_rbac_users):
    """Verify employee can update own name, but cannot update department or other records."""
    emp_client, csrf = _login_as(EMP_EMAIL)
    emp_id = setup_rbac_users["emp_id"]
    target_id = setup_rbac_users["target_id"]

    # Allowed: update own name
    resp_ok = emp_client.patch(
        f"/employees/{emp_id}",
        json={"name": "RBAC Employee Updated"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["name"] == "RBAC Employee Updated"

    # Forbidden: update own department
    resp_dept = emp_client.patch(
        f"/employees/{emp_id}",
        json={"department": "Executive Management"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_dept.status_code == 403

    # Forbidden: update someone else's record
    resp_other = emp_client.patch(
        f"/employees/{target_id}",
        json={"name": "Hacked Name"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_other.status_code == 403


# 7. HR can access directory and view/update records
def test_hr_can_access_directory_and_permitted_fields(setup_rbac_users):
    """Verify HR can list employees, view any record, and update name and department."""
    hr_client, csrf = _login_as(HR_EMAIL)
    target_id = setup_rbac_users["target_id"]

    # List directory
    resp_list = hr_client.get("/employees")
    assert resp_list.status_code == 200
    data = resp_list.json()
    assert "items" in data
    assert data["total"] >= 4

    # View target record
    resp_view = hr_client.get(f"/employees/{target_id}")
    assert resp_view.status_code == 200
    assert resp_view.json()["email"] == TARGET_EMAIL

    # Update name and department
    resp_update = hr_client.patch(
        f"/employees/{target_id}",
        json={"name": "Target Updated by HR", "department": "Accounting"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_update.status_code == 200
    assert resp_update.json()["name"] == "Target Updated by HR"
    assert resp_update.json()["department"] == "Accounting"


# 8. HR cannot access admin endpoints
def test_hr_cannot_access_admin_endpoints(setup_rbac_users):
    """Verify HR cannot update roles or account status."""
    hr_client, csrf = _login_as(HR_EMAIL)
    target_id = setup_rbac_users["target_id"]

    resp_role = hr_client.patch(
        f"/admin/employees/{target_id}/role",
        json={"role": "admin"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_role.status_code == 403

    resp_status = hr_client.patch(
        f"/admin/employees/{target_id}/status",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_status.status_code == 403


# 9. HR cannot create admin accounts, but can create employees
def test_hr_provisioning_restrictions(setup_rbac_users):
    """Verify HR can create employees, but is rejected with 403 when attempting to create an admin."""
    hr_client, csrf = _login_as(HR_EMAIL)

    # Attempt to create an admin -> 403 Forbidden
    resp_admin = hr_client.post(
        "/hr/employees",
        json={
            "employee_code": "PROV-ADMIN-ERR",
            "name": "Escalated User",
            "email": "rbac.created@company.com",
            "department": "IT",
            "role": "admin",
            "password": TEST_PASSWORD,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_admin.status_code == 403
    assert "not authorized" in resp_admin.json()["detail"].lower()

    # Allowed: create regular employee -> 201 Created
    resp_emp = hr_client.post(
        "/hr/employees",
        json={
            "employee_code": "PROV-EMP-OK",
            "name": "New Provisioned Staff",
            "email": "rbac.created@company.com",
            "department": "Operations",
            "role": "employee",
            "password": TEST_PASSWORD,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_emp.status_code == 201
    assert resp_emp.json()["email"] == "rbac.created@company.com"
    assert resp_emp.json()["role"] == "employee"


# 10. Admin can manage roles and status
def test_admin_role_and_status_management(setup_rbac_users):
    """Verify Admin can change employee roles and activate/deactivate accounts."""
    admin_client, csrf = _login_as(ADMIN_EMAIL)
    target_id = setup_rbac_users["target_id"]

    # Promote target to HR
    resp_role = admin_client.patch(
        f"/admin/employees/{target_id}/role",
        json={"role": "hr"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_role.status_code == 200
    assert resp_role.json()["role"] == "hr"

    # Deactivate target
    resp_status = admin_client.patch(
        f"/admin/employees/{target_id}/status",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_status.status_code == 200
    assert resp_status.json()["is_active"] is False

    # Reactivate target
    resp_reactivate = admin_client.patch(
        f"/admin/employees/{target_id}/status",
        json={"is_active": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_reactivate.status_code == 200
    assert resp_reactivate.json()["is_active"] is True


# 11. Admin self-protection against demotion and deactivation
def test_admin_self_protection(setup_rbac_users):
    """Verify administrator cannot accidentally demote or deactivate their own account."""
    admin_client, csrf = _login_as(ADMIN_EMAIL)
    admin_id = setup_rbac_users["admin_id"]

    # Attempt self-demotion
    resp_demote = admin_client.patch(
        f"/admin/employees/{admin_id}/role",
        json={"role": "employee"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_demote.status_code == 400
    assert "cannot demote" in resp_demote.json()["detail"].lower()

    # Attempt self-deactivation
    resp_deact = admin_client.patch(
        f"/admin/employees/{admin_id}/status",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_deact.status_code == 400
    assert "cannot deactivate" in resp_deact.json()["detail"].lower()


# 12. Invalid roles are rejected
def test_invalid_role_assignment_rejected(setup_rbac_users):
    """Verify assigning an invalid role returns validation error."""
    admin_client, csrf = _login_as(ADMIN_EMAIL)
    target_id = setup_rbac_users["target_id"]

    resp = admin_client.patch(
        f"/admin/employees/{target_id}/role",
        json={"role": "super_root_user"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


# 13. Deactivation revokes existing sessions immediately
def test_deactivation_revokes_sessions(setup_rbac_users):
    """Verify deactivating an account deletes its sessions and blocks subsequent requests."""
    # Target logs in and acquires session
    target_client, _ = _login_as(TARGET_EMAIL)
    target_id = setup_rbac_users["target_id"]

    # Target can currently access /employees/me
    assert target_client.get("/employees/me").status_code == 200

    # Admin deactivates target
    admin_client, admin_csrf = _login_as(ADMIN_EMAIL)
    deact_resp = admin_client.patch(
        f"/admin/employees/{target_id}/status",
        json={"is_active": False},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert deact_resp.status_code == 200

    # Verify session was deleted from database
    db = TestSessionLocal()
    try:
        sessions = db.scalars(
            select(DbSession).where(DbSession.employee_id == target_id)
        ).all()
        assert len(sessions) == 0
    finally:
        db.close()

    # Target's subsequent request with existing cookie is rejected with 401
    post_deact_resp = target_client.get("/employees/me")
    assert post_deact_resp.status_code == 401


# 14. Responses never expose password hashes or session tokens
def test_responses_never_expose_secrets(setup_rbac_users):
    """Verify directory and single employee responses never leak password_hash or session details."""
    admin_client, _ = _login_as(ADMIN_EMAIL)
    target_id = setup_rbac_users["target_id"]

    # Directory
    dir_resp = admin_client.get("/employees")
    dir_text = dir_resp.text.lower()
    assert "password_hash" not in dir_text
    assert "$argon2" not in dir_text
    assert "session_token" not in dir_text
    assert "csrf_token" not in dir_text

    # Single employee
    single_resp = admin_client.get(f"/employees/{target_id}")
    single_text = single_resp.text.lower()
    assert "password_hash" not in single_text
    assert "$argon2" not in single_text
    assert "session_token" not in single_text
