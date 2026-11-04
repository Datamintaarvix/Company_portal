"""Tests for database connectivity, session lifecycle, and Employee model constraints."""

import os
import re
from typing import Generator
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.session import check_db_connection, get_db
from app.models.employee import Employee, UserRole


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


def test_db_connection_utility(monkeypatch):
    """Verify that check_db_connection successfully pings the isolated PostgreSQL test database."""
    monkeypatch.setattr("app.db.session.engine", test_engine)
    assert check_db_connection() is True


def test_get_db_session_lifecycle(monkeypatch):
    """Verify that get_db yields an active session and closes it afterwards using isolated sessionmaker."""
    monkeypatch.setattr("app.db.session.SessionLocal", TestSessionLocal)
    generator = get_db()
    db = next(generator)
    try:
        assert db.is_active is True
        # Calling next finishes the generator and executes the finally: block
        with pytest.raises(StopIteration):
            next(generator)
    finally:
        db.close()


def test_employee_creation_and_defaults():
    """Verify that an Employee record can be persisted and default attributes are assigned."""
    db = TestSessionLocal()
    unique_code = "EMP-TEST-001"
    unique_email = "test.employee001@example.com"

    try:
        # Clean up any leftover test data
        db.query(Employee).filter(Employee.email == unique_email).delete()
        db.commit()

        employee = Employee(
            employee_code=unique_code,
            name="Alice Test",
            email=unique_email,
            department="Engineering",
            role=UserRole.EMPLOYEE.value,
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)

        assert employee.id is not None
        assert employee.employee_code == unique_code
        assert employee.name == "Alice Test"
        assert employee.email == unique_email
        assert employee.department == "Engineering"
        assert employee.role == "employee"
        assert employee.is_active is True
        assert employee.created_at is not None
        assert employee.updated_at is not None
        assert employee.password_hash is None
        assert employee.date_of_joining is None

    finally:
        # Clean up after test
        try:
            db.query(Employee).filter(Employee.email == unique_email).delete()
            db.commit()
        finally:
            db.close()


def test_employee_date_of_joining_persistence():
    """Verify that date_of_joining is persisted and retrieved as a python date object."""
    from datetime import date
    db = TestSessionLocal()
    code = "EMP-TEST-DOJ-DB"
    email = "doj.db@example.com"
    test_date = date(2026, 1, 10)

    try:
        db.query(Employee).filter(Employee.email == email).delete()
        db.commit()

        emp = Employee(
            employee_code=code,
            name="DOJ DB Tester",
            email=email,
            role=UserRole.EMPLOYEE.value,
            date_of_joining=test_date,
        )
        db.add(emp)
        db.commit()
        db.refresh(emp)

        assert emp.id is not None
        assert emp.date_of_joining == test_date

    finally:
        try:
            db.query(Employee).filter(Employee.email == email).delete()
            db.commit()
        finally:
            db.close()


def test_employee_unique_email_constraint():
    """Verify that duplicate email addresses are rejected by database constraint."""
    db = TestSessionLocal()
    code_a = "EMP-TEST-UNIQ-1"
    code_b = "EMP-TEST-UNIQ-2"
    shared_email = "duplicate@example.com"

    try:
        db.query(Employee).filter(Employee.email == shared_email).delete()
        db.commit()

        emp1 = Employee(
            employee_code=code_a,
            name="User One",
            email=shared_email,
            role="employee",
        )
        db.add(emp1)
        db.commit()

        emp2 = Employee(
            employee_code=code_b,
            name="User Two",
            email=shared_email,
            role="employee",
        )
        db.add(emp2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    finally:
        try:
            db.query(Employee).filter(Employee.email == shared_email).delete()
            db.commit()
        finally:
            db.close()


def test_employee_role_check_constraint():
    """Verify that invalid role values are rejected by the check_valid_employee_role constraint."""
    db = TestSessionLocal()
    unique_code = "EMP-INVALID-ROLE"
    unique_email = "invalid.role@example.com"

    try:
        emp = Employee(
            employee_code=unique_code,
            name="Invalid Role User",
            email=unique_email,
            role="unauthorized_role",  # Not employee, hr, or admin
        )
        db.add(emp)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    finally:
        try:
            db.query(Employee).filter(Employee.email == unique_email).delete()
            db.commit()
        finally:
            db.close()
