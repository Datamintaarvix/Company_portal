"""Comprehensive automated test suite for Stage 3 Secure Authentication.

Tests cover:
1. Successful login with valid credentials
2. Invalid password rejection with generic error
3. Unknown email rejection with generic error
4. Inactive employee login rejection
5. Missing session on /auth/me (HTTP 401)
6. Valid session on /auth/me (safe profile fields returned)
7. Expired or invalid session rejection (HTTP 401)
8. Successful logout and server-side session invalidation
9. Disabled account after login rejection (HTTP 401)
10. Login rate limiting after repeated failed attempts (HTTP 429)
11. CSRF protection on state-changing cookie-authenticated requests (HTTP 403)
12. Verification that responses never leak password hashes or session secrets
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import get_password_hash, verify_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.employee import Employee, UserRole
from app.models.session import Session as DbSession
from app.schemas.auth import EmployeeCreate
from app.services.auth import provision_employee
from app.services.rate_limiter import login_rate_limiter

client = TestClient(app)
settings = get_settings()

TEST_PASSWORD = "StrongSecurePassword123!"
ADMIN_EMAIL = "test.admin@company.com"
EMP_EMAIL = "test.emp@company.com"
INACTIVE_EMAIL = "test.inactive@company.com"


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
def setup_teardown_test_data(init_test_db_schema):
    """Ensure clean test state before and after each test."""
    test_emails = [ADMIN_EMAIL, EMP_EMAIL, INACTIVE_EMAIL, "duplicate.test@company.com"]
    login_rate_limiter.clear()

    # Pre-test cleanup and seed
    db = TestSessionLocal()
    try:
        db.query(DbSession).filter(
            DbSession.employee_id.in_(
                select(Employee.id).where(Employee.email.in_(test_emails))
            )
        ).delete(synchronize_session=False)
        db.query(Employee).filter(Employee.email.in_(test_emails)).delete(synchronize_session=False)
        db.commit()

        # Seed test users
        admin_emp = Employee(
            employee_code="ADM-TEST-001",
            name="Admin Tester",
            email=ADMIN_EMAIL,
            department="IT Administration",
            role=UserRole.ADMIN.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        normal_emp = Employee(
            employee_code="EMP-TEST-001",
            name="Employee Tester",
            email=EMP_EMAIL,
            department="Engineering",
            role=UserRole.EMPLOYEE.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=True,
        )
        inactive_emp = Employee(
            employee_code="EMP-TEST-002",
            name="Inactive Tester",
            email=INACTIVE_EMAIL,
            department="Operations",
            role=UserRole.EMPLOYEE.value,
            password_hash=get_password_hash(TEST_PASSWORD),
            is_active=False,
        )
        db.add_all([admin_emp, normal_emp, inactive_emp])
        db.commit()
    finally:
        db.close()

    try:
        yield
    finally:
        # Post-test cleanup (always executes, even if assertions failed)
        login_rate_limiter.clear()
        db = TestSessionLocal()
        try:
            db.query(DbSession).filter(
                DbSession.employee_id.in_(
                    select(Employee.id).where(Employee.email.in_(test_emails))
                )
            ).delete(synchronize_session=False)
            db.query(Employee).filter(Employee.email.in_(test_emails)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


# 1. Successful login with valid credentials
def test_successful_login_with_valid_credentials():
    """Verify that submitting valid credentials establishes session and CSRF cookies."""
    response = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Login successful"
    assert "user" in data
    assert data["user"]["email"] == EMP_EMAIL
    assert data["user"]["name"] == "Employee Tester"
    assert data["user"]["role"] == "employee"

    # Verify cookies in response
    assert settings.SESSION_COOKIE_NAME in response.cookies
    assert settings.CSRF_COOKIE_NAME in response.cookies

    # Verify session token is persisted in database
    session_token = response.cookies[settings.SESSION_COOKIE_NAME]
    db = TestSessionLocal()
    try:
        db_session = db.execute(
            select(DbSession).where(DbSession.session_token == session_token)
        ).scalar_one_or_none()
        assert db_session is not None
        assert db_session.employee.email == EMP_EMAIL
        assert db_session.csrf_token == response.cookies[settings.CSRF_COOKIE_NAME]
        assert db_session.expires_at > datetime.now(timezone.utc)
    finally:
        db.close()


# 2. Invalid password
def test_login_invalid_password_returns_generic_error():
    """Verify that an incorrect password produces a 401 with a generic error message."""
    response = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": "WrongPassword123!"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert settings.SESSION_COOKIE_NAME not in response.cookies


# 3. Unknown email
def test_login_unknown_email_returns_generic_error():
    """Verify that an unrecognized email produces the same generic error (preventing enumeration)."""
    response = client.post(
        "/auth/login",
        json={"email": "nonexistent@company.com", "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert settings.SESSION_COOKIE_NAME not in response.cookies


# 4. Inactive employee login
def test_inactive_employee_login_is_rejected():
    """Verify that inactive employees cannot authenticate even with correct password."""
    response = client.post(
        "/auth/login",
        json={"email": INACTIVE_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    assert "inactive" in response.json()["detail"].lower()
    assert settings.SESSION_COOKIE_NAME not in response.cookies


# 5. Missing session on /auth/me
def test_get_me_without_session_cookie_returns_401():
    """Verify that /auth/me rejects unauthenticated requests with HTTP 401."""
    # Ensure fresh client without cookies
    clean_client = TestClient(app)
    response = clean_client.get("/auth/me")
    assert response.status_code == 401
    assert "detail" in response.json()


# 6. Valid session on /auth/me
def test_get_me_with_valid_session_returns_profile():
    """Verify that /auth/me returns the authenticated employee profile."""
    # Login to acquire session cookies
    login_resp = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200

    # Request /auth/me (client retains session cookies)
    me_resp = client.get("/auth/me")
    assert me_resp.status_code == 200
    data = me_resp.json()
    assert data["email"] == EMP_EMAIL
    assert data["employee_code"] == "EMP-TEST-001"
    assert data["name"] == "Employee Tester"
    assert data["department"] == "Engineering"
    assert data["role"] == "employee"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


# 7. Expired or invalid session
def test_get_me_with_invalid_or_expired_session_returns_401():
    """Verify that invalid or expired session tokens are rejected with 401."""
    # Test completely bogus session token
    bad_client = TestClient(app)
    bad_client.cookies.set(settings.SESSION_COOKIE_NAME, "completely-bogus-token-12345")
    response = bad_client.get("/auth/me")
    assert response.status_code == 401

    # Test expired session in database
    login_resp = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    session_token = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    # Artificially expire the session in the database
    db = TestSessionLocal()
    try:
        db_session = db.execute(
            select(DbSession).where(DbSession.session_token == session_token)
        ).scalar_one()
        db_session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()

    # Now request /auth/me with the expired session
    expired_resp = client.get("/auth/me")
    assert expired_resp.status_code == 401


# 8. Successful logout and session invalidation
def test_successful_logout_and_session_invalidation():
    """Verify that logout invalidates server-side session and clears cookies."""
    login_resp = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200
    session_token = login_resp.cookies[settings.SESSION_COOKIE_NAME]
    csrf_token = login_resp.cookies[settings.CSRF_COOKIE_NAME]

    # Perform logout providing CSRF token header
    logout_resp = client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out successfully"

    # Verify session record is removed from database
    db = TestSessionLocal()
    try:
        db_session = db.execute(
            select(DbSession).where(DbSession.session_token == session_token)
        ).scalar_one_or_none()
        assert db_session is None
    finally:
        db.close()

    # Verify subsequent /auth/me returns 401
    me_resp = client.get("/auth/me")
    assert me_resp.status_code == 401


# 9. Disabled account after login
def test_disabled_account_after_login_rejected():
    """Verify that if an account is disabled after session creation, subsequent requests fail with 401."""
    login_resp = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200

    # Deactivate the employee account in database
    db = TestSessionLocal()
    try:
        employee = db.execute(
            select(Employee).where(Employee.email == EMP_EMAIL)
        ).scalar_one()
        employee.is_active = False
        db.commit()
    finally:
        db.close()

    # Access /auth/me
    me_resp = client.get("/auth/me")
    assert me_resp.status_code == 401
    assert "disabled" in me_resp.json()["detail"].lower() or "inactive" in me_resp.json()["detail"].lower()


# 10. Login rate limiting
def test_login_rate_limiting():
    """Verify that repeated failed login attempts trigger HTTP 429 Too Many Requests."""
    rate_client = TestClient(app)
    max_attempts = settings.RATE_LIMIT_LOGIN_MAX_ATTEMPTS

    # Trigger max_attempts failed attempts
    for _ in range(max_attempts):
        resp = rate_client.post(
            "/auth/login",
            json={"email": EMP_EMAIL, "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    # Next attempt must trigger rate limiter HTTP 429
    blocked_resp = rate_client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    assert blocked_resp.status_code == 429
    assert "too many" in blocked_resp.json()["detail"].lower()


# 11. CSRF protection
def test_csrf_protection_on_logout():
    """Verify that cookie-authenticated POST /auth/logout requires valid CSRF token."""
    login_resp = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200
    valid_csrf = login_resp.cookies[settings.CSRF_COOKIE_NAME]

    # Attempt logout with missing CSRF header -> 403 Forbidden
    missing_csrf_resp = client.post("/auth/logout")
    assert missing_csrf_resp.status_code == 403
    assert "csrf" in missing_csrf_resp.json()["detail"].lower()

    # Attempt logout with forged/wrong CSRF header -> 403 Forbidden
    invalid_csrf_resp = client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": "tampered-token-value"},
    )
    assert invalid_csrf_resp.status_code == 403
    assert "csrf" in invalid_csrf_resp.json()["detail"].lower()

    # Attempt logout with valid CSRF header -> 200 OK
    valid_csrf_resp = client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": valid_csrf},
    )
    assert valid_csrf_resp.status_code == 200


# 12. Ensuring responses never expose password hashes or session secrets
def test_responses_never_expose_secrets_or_hashes():
    """Verify that JSON response bodies from auth endpoints never expose password hashes or tokens."""
    # Login response
    login_resp = client.post(
        "/auth/login",
        json={"email": EMP_EMAIL, "password": TEST_PASSWORD},
    )
    login_text = login_resp.text.lower()
    session_token = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    assert "password_hash" not in login_text
    assert "$argon2" not in login_text
    assert session_token not in login_resp.text

    # /auth/me response
    me_resp = client.get("/auth/me")
    me_text = me_resp.text.lower()
    assert "password_hash" not in me_text
    assert "$argon2" not in me_text
    assert session_token not in me_resp.text

    # /auth/logout response
    csrf_token = login_resp.cookies[settings.CSRF_COOKIE_NAME]
    logout_resp = client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    logout_text = logout_resp.text.lower()
    assert "password_hash" not in logout_text
    assert "$argon2" not in logout_text
    assert session_token not in logout_resp.text


# Provisioning unit tests
def test_provisioning_employee_and_constraints():
    """Verify provisioning validation: uniqueness, password complexity, role constraint."""
    db = TestSessionLocal()
    code = "EMP-PROV-001"
    email = "provisioned.user@company.com"

    try:
        # 1. Successful provision
        data = EmployeeCreate(
            employee_code=code,
            name="Provisioned User",
            email=email,
            department="Finance",
            role="employee",
            password="ValidPassword123!",
        )
        emp = provision_employee(db, data)
        assert emp.id is not None
        assert emp.password_hash.startswith("$argon2")
        assert verify_password("ValidPassword123!", emp.password_hash)

        # 2. Duplicate email rejected
        with pytest.raises(ValueError, match="already exists"):
            provision_employee(
                db,
                EmployeeCreate(
                    employee_code="EMP-PROV-002",
                    name="Another",
                    email=email,
                    role="employee",
                    password="ValidPassword123!",
                ),
            )

        # 3. Duplicate code rejected
        with pytest.raises(ValueError, match="already exists"):
            provision_employee(
                db,
                EmployeeCreate(
                    employee_code=code,
                    name="Another",
                    email="another@company.com",
                    role="employee",
                    password="ValidPassword123!",
                ),
            )

        # 4. Invalid role rejected
        with pytest.raises(ValueError, match="Invalid role"):
            provision_employee(
                db,
                EmployeeCreate(
                    employee_code="EMP-PROV-003",
                    name="Another",
                    email="another2@company.com",
                    role="superadmin",
                    password="ValidPassword123!",
                ),
            )

        # 5. Weak password rejected
        with pytest.raises(ValueError, match="at least 8 characters"):
            provision_employee(
                db,
                EmployeeCreate(
                    employee_code="EMP-PROV-004",
                    name="Another",
                    email="another3@company.com",
                    role="employee",
                    password="short",
                ),
            )

    finally:
        db.query(Employee).filter(Employee.email.in_([email, "another@company.com", "another2@company.com", "another3@company.com"])).delete(synchronize_session=False)
        db.commit()
        db.close()
