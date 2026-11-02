"""PostgreSQL Concurrency & Isolation Integration Test Suite for Leave Management.

Phase 2.11A: Validates PostgreSQL-specific constraints, advisory locks,
pessimistic row locks, partial unique indexes, FIFO consumption, and atomic rollbacks.

STRICT ISOLATION GUARD:
This test suite requires an ephemeral test database created and verified by
`scripts/run_isolated_pg_tests.py`.
If EPHEMERAL_PG_TEST_DB_URL or EPHEMERAL_PG_RUN_TOKEN is missing or violates
the ephemeral naming pattern, pytest safely skips this entire module.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import re
import threading
from typing import Generator
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

# Fail-closed guard: skip entire module if not running inside verified ephemeral harness
EPHEMERAL_DB_URL = os.environ.get("EPHEMERAL_PG_TEST_DB_URL")
RUN_TOKEN = os.environ.get("EPHEMERAL_PG_RUN_TOKEN")

if not EPHEMERAL_DB_URL or not RUN_TOKEN:
    pytest.skip(
        "Skipping PostgreSQL concurrency integration tests: EPHEMERAL_PG_TEST_DB_URL "
        "and EPHEMERAL_PG_RUN_TOKEN must be explicitly set by the ephemeral test harness.",
        allow_module_level=True,
    )

# Validate database name inside URL
_parsed_url = urlparse(EPHEMERAL_DB_URL)
_db_name = _parsed_url.path.lstrip("/")
if not re.match(r"^company_portal_test_ephemeral_[0-9a-f]{8}$", _db_name):
    pytest.fail(
        f"CRITICAL SAFETY VIOLATION: Test database '{_db_name}' does not match "
        f"the ephemeral naming pattern 'company_portal_test_ephemeral_<uuid8>'! Aborting.",
        pytrace=False,
    )

# Defense-in-depth: Unconditionally pin DATABASE_URL to the validated ephemeral database URL
# and invalidate settings cache so any app code, session factories, or Alembic migrations
# strictly target EPHEMERAL_DB_URL instead of defaulting to .env or development databases.
os.environ["DATABASE_URL"] = EPHEMERAL_DB_URL
from app.core.config import get_settings
get_settings.cache_clear()

from alembic.config import Config
from alembic import command

from app.core.config import get_settings
from app.db.base import Base
from app.models.employee import Employee, UserRole
from app.models.leave import (
    CompOffClaim,
    CompOffClaimStatus,
    CompOffCreditStatus,
    LeaveAuditLog,
    LeaveBalance,
    LeaveBalanceTransaction,
    LeaveCategory,
    LeaveCategoryCode,
    LeaveRequest,
    LeaveRequestStatus,
    TransactionType,
)
from app.services.leave import (
    acquire_employee_advisory_lock,
    approve_leave_request,
    consume_compoff_credits_fifo,
    expire_compoff_credits,
    get_or_create_leave_balance,
    reject_leave_request,
    submit_leave_request,
)


# ============================================================================
# Pytest Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def pg_engine():
    """Create a thread-safe QueuePool engine targeting the ephemeral database."""
    engine = create_engine(
        EPHEMERAL_DB_URL,
        poolclass=QueuePool,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30,
    )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(pg_engine):
    """Provide a thread-safe session factory for concurrency tests."""
    return sessionmaker(bind=pg_engine, autocommit=False, autoflush=False)


@pytest.fixture
def db_session(session_factory) -> Generator[Session, None, None]:
    """Provide an isolated database session per test with automatic rollback."""
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seed_data(db_session: Session):
    """Seed baseline roles, employees, and leave categories."""
    # 1. Employee (Requester)
    emp1 = db_session.execute(
        select(Employee).where(Employee.email == "requester@example.com")
    ).scalar_one_or_none()
    if not emp1:
        emp1 = Employee(
            employee_code="EMP-TEST-001",
            name="Jane Requester",
            email="requester@example.com",
            department="Engineering",
            password_hash="$2b$12$DUMMY_HASH_VALUE_FOR_TESTS_ONLY",
            role=UserRole.EMPLOYEE.value,
            is_active=True,
        )
        db_session.add(emp1)

    # 2. Employee (Reviewer / HR)
    emp2 = db_session.execute(
        select(Employee).where(Employee.email == "hr_reviewer@example.com")
    ).scalar_one_or_none()
    if not emp2:
        emp2 = Employee(
            employee_code="EMP-TEST-002",
            name="Arthur Reviewer",
            email="hr_reviewer@example.com",
            department="Engineering",
            password_hash="$2b$12$DUMMY_HASH_VALUE_FOR_TESTS_ONLY",
            role=UserRole.HR.value,
            is_active=True,
        )
        db_session.add(emp2)

    # 3. Standard Leave Categories
    for code, name, quota in [
        (LeaveCategoryCode.EARNED.value, "Earned Leave", Decimal("15.0")),
        (LeaveCategoryCode.CASUAL.value, "Casual Leave", Decimal("10.0")),
        (LeaveCategoryCode.SICK.value, "Sick Leave", Decimal("10.0")),
        (LeaveCategoryCode.COMP_OFF.value, "Compensatory Off", Decimal("0.0")),
        (LeaveCategoryCode.UNPAID.value, "Leave Without Pay", Decimal("0.0")),
    ]:
        existing_cat = db_session.execute(
            select(LeaveCategory).where(LeaveCategory.code == code)
        ).scalar_one_or_none()
        if not existing_cat:
            cat = LeaveCategory(
                code=code,
                name=name,
                annual_quota=quota,
                is_paid=(code != LeaveCategoryCode.UNPAID.value),
                is_active=True,
            )
            db_session.add(cat)

    db_session.commit()
    db_session.refresh(emp1)
    db_session.refresh(emp2)
    return {"employee": emp1, "reviewer": emp2}


# ============================================================================
# PG-01: Alembic Migration Lifecycle (Upgrade / Downgrade / Types)
# ============================================================================

def test_pg_01_migration_lifecycle(pg_engine):
    """PG-01: Verify Alembic migration upgrade/downgrade and column types on ephemeral DB."""
    alembic_cfg = Config("alembic.ini")
    escaped_ephemeral_url = EPHEMERAL_DB_URL.replace("%%", "%").replace("%", "%%")
    alembic_cfg.set_main_option("sqlalchemy.url", escaped_ephemeral_url)

    with pg_engine.connect() as conn:
        # Step 1: Upgrade to predecessor revision c2c3927fd40e (Attendance)
        command.upgrade(alembic_cfg, "c2c3927fd40e")

        # Step 2: Verify leave tables do NOT exist yet
        tables_res = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'leave_%'
        """)).scalars().all()
        assert len(tables_res) == 0, f"Expected 0 leave tables before e8b104ad5c91, found: {tables_res}"

        # Step 3: Upgrade to Leave Management revision e8b104ad5c91
        command.upgrade(alembic_cfg, "e8b104ad5c91")

        # Step 4: Verify 7 leave tables exist
        expected_tables = {
            "leave_categories",
            "company_holidays",
            "leave_balances",
            "leave_requests",
            "comp_off_claims",
            "leave_balance_transactions",
            "leave_audit_logs",
        }
        actual_tables = set(conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name IN (
                'leave_categories', 'company_holidays', 'leave_balances',
                'leave_requests', 'comp_off_claims', 'leave_balance_transactions',
                'leave_audit_logs'
            )
        """)).scalars().all())
        assert expected_tables.issubset(actual_tables), f"Missing tables: {expected_tables - actual_tables}"

        # Step 5: Verify numeric column precision and scale
        col_res = conn.execute(text("""
            SELECT numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_name = 'leave_balances' AND column_name = 'entitled_days'
        """)).one()
        assert col_res.numeric_precision == 4
        assert col_res.numeric_scale == 1

        # Step 6: Test downgrade back to c2c3927fd40e
        command.downgrade(alembic_cfg, "c2c3927fd40e")
        tables_after_downgrade = set(conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name IN (
                'leave_categories', 'company_holidays', 'leave_balances',
                'leave_requests', 'comp_off_claims', 'leave_balance_transactions',
                'leave_audit_logs'
            )
        """)).scalars().all())
        assert len(tables_after_downgrade) == 0, "Leave tables were not dropped on downgrade!"

        # Step 7: Restore to head for subsequent tests
        command.upgrade(alembic_cfg, "head")


# ============================================================================
# PG-02: PostgreSQL Table CHECK Constraints
# ============================================================================

def test_pg_02_check_constraints(db_session: Session, seed_data):
    """PG-02: Verify PostgreSQL CHECK constraints enforced at database layer."""
    emp = seed_data["employee"]
    rev = seed_data["reviewer"]
    cat = db_session.execute(
        select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.EARNED.value)
    ).scalar_one()

    # 1. Anti-self-approval CHECK constraint (ck_leave_request_anti_self_approval)
    with pytest.raises(IntegrityError) as exc_self_approve:
        self_approve_sql = text("""
            INSERT INTO leave_requests (
                employee_id, category_id, start_date, end_date, total_days,
                reason, status, reviewed_by_id, is_half_day, created_at, updated_at
            ) VALUES (
                :emp_id, :cat_id, '2026-11-02', '2026-11-02', 1.0,
                'Self approve test', 'APPROVED', :emp_id, FALSE, NOW(), NOW()
            );
        """)
        db_session.execute(self_approve_sql, {"emp_id": emp.id, "cat_id": cat.id})
        db_session.commit()
    db_session.rollback()
    assert "ck_leave_request_anti_self_approval" in str(exc_self_approve.value)

    # 2. Date order CHECK constraint (ck_leave_request_dates_order)
    with pytest.raises(IntegrityError) as exc_date_order:
        invalid_dates_sql = text("""
            INSERT INTO leave_requests (
                employee_id, category_id, start_date, end_date, total_days,
                reason, status, is_half_day, created_at, updated_at
            ) VALUES (
                :emp_id, :cat_id, '2026-11-10', '2026-11-05', 1.0,
                'Invalid dates test', 'PENDING', FALSE, NOW(), NOW()
            );
        """)
        db_session.execute(invalid_dates_sql, {"emp_id": emp.id, "cat_id": cat.id})
        db_session.commit()
    db_session.rollback()
    assert "ck_leave_request_dates_order" in str(exc_date_order.value)

    # 3. Available balance non-negative CHECK constraint (ck_balance_available_non_negative)
    with pytest.raises(IntegrityError) as exc_balance:
        invalid_bal_sql = text("""
            INSERT INTO leave_balances (
                employee_id, category_id, year, entitled_days, carried_forward_days,
                accrued_days, used_days, reserved_days, created_at, updated_at
            ) VALUES (
                :emp_id, :cat_id, 2026, 5.0, 0.0, 0.0, 0.0, 10.0, NOW(), NOW()
            );
        """)
        db_session.execute(invalid_bal_sql, {"emp_id": emp.id, "cat_id": cat.id})
        db_session.commit()
    db_session.rollback()
    assert "ck_balance_available_non_negative" in str(exc_balance.value)

    # 4. Comp-off days claimed CHECK constraint (ck_comp_off_days_claimed: 0.5 or 1.0)
    with pytest.raises(IntegrityError) as exc_compoff:
        invalid_claimed_sql = text("""
            INSERT INTO comp_off_claims (
                employee_id, work_date, days_claimed, reason, status,
                credit_status, remaining_days, created_at, updated_at
            ) VALUES (
                :emp_id, '2026-10-25', 1.5, 'Invalid days claimed', 'PENDING',
                'PENDING', 0.0, NOW(), NOW()
            );
        """)
        db_session.execute(invalid_claimed_sql, {"emp_id": emp.id})
        db_session.commit()
    db_session.rollback()
    assert "ck_comp_off_days_claimed" in str(exc_compoff.value)


# ============================================================================
# PG-03: Partial Unique Index - Single Day Leaves
# ============================================================================

def test_pg_03_partial_unique_index_full_day_single(db_session: Session, seed_data):
    """PG-03: Verify uq_leave_request_full_day_single partial unique index."""
    emp = seed_data["employee"]
    cat = db_session.execute(
        select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.EARNED.value)
    ).scalar_one()

    target_date = date(2026, 11, 16)

    # Insert first single-day full-day pending request
    req1 = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=target_date,
        end_date=target_date,
        is_half_day=False,
        half_day_period=None,
        total_days=Decimal("1.0"),
        reason="First request",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(req1)
    db_session.commit()

    # Attempting duplicate pending single-day request on same date must violate partial index
    req2 = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=target_date,
        end_date=target_date,
        is_half_day=False,
        half_day_period=None,
        total_days=Decimal("1.0"),
        reason="Duplicate collision",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(req2)
    with pytest.raises(IntegrityError) as exc_dup:
        db_session.commit()
    db_session.rollback()
    assert "uq_leave_request_full_day_single" in str(exc_dup.value)

    # Cancel first request
    db_session.query(LeaveRequest).filter(LeaveRequest.id == req1.id).update(
        {"status": LeaveRequestStatus.CANCELLED.value}
    )
    db_session.commit()

    # Re-inserting request on target date now succeeds (since status is CANCELLED)
    req3 = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=target_date,
        end_date=target_date,
        is_half_day=False,
        half_day_period=None,
        total_days=Decimal("1.0"),
        reason="Re-submission after cancel",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(req3)
    db_session.commit()
    assert req3.id is not None


# ============================================================================
# PG-04: Partial Unique Index - Half Day Slots
# ============================================================================

def test_pg_04_partial_unique_index_half_day_slot(db_session: Session, seed_data):
    """PG-04: Verify uq_leave_request_half_day_slot partial unique index."""
    emp = seed_data["employee"]
    cat = db_session.execute(
        select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.CASUAL.value)
    ).scalar_one()

    target_date = date(2026, 11, 17)

    # 1. Insert morning slot
    half_morning = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=target_date,
        end_date=target_date,
        is_half_day=True,
        half_day_period="morning",
        total_days=Decimal("0.5"),
        reason="Morning slot",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(half_morning)
    db_session.commit()

    # 2. Attempt duplicate morning slot -> violates partial index
    dup_morning = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=target_date,
        end_date=target_date,
        is_half_day=True,
        half_day_period="morning",
        total_days=Decimal("0.5"),
        reason="Duplicate morning slot",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(dup_morning)
    with pytest.raises(IntegrityError) as exc_dup_slot:
        db_session.commit()
    db_session.rollback()
    assert "uq_leave_request_half_day_slot" in str(exc_dup_slot.value)

    # 3. Afternoon slot on same date succeeds!
    half_afternoon = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=target_date,
        end_date=target_date,
        is_half_day=True,
        half_day_period="afternoon",
        total_days=Decimal("0.5"),
        reason="Afternoon slot",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(half_afternoon)
    db_session.commit()
    assert half_afternoon.id is not None


# ============================================================================
# PG-05: Partial Unique Index - Comp-Off Claim Idempotency
# ============================================================================

def test_pg_05_comp_off_claim_idempotency_index(db_session: Session, seed_data):
    """PG-05: Verify uq_comp_off_claim_emp_date partial unique index."""
    emp = seed_data["employee"]
    work_date = date(2026, 10, 24)  # Saturday extra work

    claim1 = CompOffClaim(
        employee_id=emp.id,
        work_date=work_date,
        days_claimed=Decimal("1.0"),
        reason="Server maintenance",
        status=CompOffClaimStatus.PENDING.value,
        credit_status=CompOffCreditStatus.PENDING.value,
        remaining_days=Decimal("0.0"),
    )
    db_session.add(claim1)
    db_session.commit()

    # Duplicate claim on same work date violates partial index
    claim2 = CompOffClaim(
        employee_id=emp.id,
        work_date=work_date,
        days_claimed=Decimal("1.0"),
        reason="Duplicate claim attempt",
        status=CompOffClaimStatus.PENDING.value,
        credit_status=CompOffCreditStatus.PENDING.value,
        remaining_days=Decimal("0.0"),
    )
    db_session.add(claim2)
    with pytest.raises(IntegrityError) as exc_claim:
        db_session.commit()
    db_session.rollback()
    assert "uq_comp_off_claim_emp_date" in str(exc_claim.value)


# ============================================================================
# PG-06: Advisory Lock Concurrency Serialization
# ============================================================================

def test_pg_06_advisory_lock_concurrency_serialization(session_factory, seed_data):
    """PG-06: Verify pg_advisory_xact_lock serializes simultaneous submissions."""
    emp = seed_data["employee"]

    # Seed balance with exactly 1.0 available day
    with session_factory() as s:
        cat = s.execute(
            select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.SICK.value)
        ).scalar_one()
        bal = get_or_create_leave_balance(s, emp.id, cat.id, 2026)
        bal.entitled_days = Decimal("1.0")
        bal.reserved_days = Decimal("0.0")
        bal.used_days = Decimal("0.0")
        s.commit()

    barrier = threading.Barrier(2)
    results = [None, None]
    exceptions = [None, None]

    def submit_worker(worker_idx: int, leave_date: date):
        with session_factory() as s:
            try:
                barrier.wait(timeout=5)
                # Attempt to submit 1.0 day leave request
                res = submit_leave_request(
                    db=s,
                    employee_id=emp.id,
                    category_code=LeaveCategoryCode.SICK.value,
                    start_date=leave_date,
                    end_date=leave_date,
                    is_half_day=False,
                    half_day_period=None,
                    reason=f"Concurrent submit worker {worker_idx}",
                )
                results[worker_idx] = res
            except Exception as e:
                exceptions[worker_idx] = e

    t1 = threading.Thread(target=submit_worker, args=(0, date(2026, 12, 1)))
    t2 = threading.Thread(target=submit_worker, args=(1, date(2026, 12, 2)))

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Exactly one submission must succeed; the other must fail with HTTP 400 Insufficient balance
    succeeded = sum(1 for r in results if r is not None)
    failed = sum(1 for e in exceptions if e is not None)

    assert succeeded == 1, f"Expected exactly 1 success, got {succeeded}. Results: {results}"
    assert failed == 1, f"Expected exactly 1 failure, got {failed}. Exceptions: {exceptions}"

    failed_exc = [e for e in exceptions if e is not None][0]
    assert isinstance(failed_exc, HTTPException)
    assert failed_exc.status_code == 400
    assert "Insufficient leave balance" in failed_exc.detail

    # Verify balance in database: reserved_days must be exactly 1.0, not 2.0
    with session_factory() as s:
        cat = s.execute(
            select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.SICK.value)
        ).scalar_one()
        bal = get_or_create_leave_balance(s, emp.id, cat.id, 2026)
        assert bal.reserved_days == Decimal("1.0")
        assert bal.available_days == Decimal("0.0")


# ============================================================================
# PG-07: Pessimistic Row Lock (Approve vs Reject Race)
# ============================================================================

def test_pg_07_row_lock_approval_rejection_race(session_factory, seed_data):
    """PG-07: Verify SELECT ... FOR UPDATE serializes concurrent review decisions."""
    emp = seed_data["employee"]
    reviewer = seed_data["reviewer"]

    # Create a pending leave request
    with session_factory() as s:
        cat = s.execute(
            select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.EARNED.value)
        ).scalar_one()
        bal = get_or_create_leave_balance(s, emp.id, cat.id, 2026)
        bal.entitled_days = Decimal("10.0")
        bal.reserved_days = Decimal("0.0")
        bal.used_days = Decimal("0.0")
        s.commit()

        req_res = submit_leave_request(
            db=s,
            employee_id=emp.id,
            category_code=LeaveCategoryCode.EARNED.value,
            start_date=date(2026, 12, 10),
            end_date=date(2026, 12, 10),
            is_half_day=False,
            half_day_period=None,
            reason="Row lock test",
        )
        req_id = req_res.id

    barrier = threading.Barrier(2)
    review_results = [None, None]
    review_exceptions = [None, None]

    def approve_worker():
        with session_factory() as s:
            try:
                barrier.wait(timeout=5)
                res = approve_leave_request(
                    db=s,
                    request_id=req_id,
                    reviewer_id=reviewer.id,
                    review_notes="Approved in race",
                )
                review_results[0] = res
            except Exception as e:
                review_exceptions[0] = e

    def reject_worker():
        with session_factory() as s:
            try:
                barrier.wait(timeout=5)
                res = reject_leave_request(
                    db=s,
                    request_id=req_id,
                    reviewer_id=reviewer.id,
                    review_notes="Rejected in race",
                )
                review_results[1] = res
            except Exception as e:
                review_exceptions[1] = e

    t_app = threading.Thread(target=approve_worker)
    t_rej = threading.Thread(target=reject_worker)

    t_app.start()
    t_rej.start()
    t_app.join(timeout=10)
    t_rej.join(timeout=10)

    # Exactly one must succeed and one must fail with 400 or 409
    succeeded = sum(1 for r in review_results if r is not None)
    failed = sum(1 for e in review_exceptions if e is not None)

    assert succeeded == 1, f"Expected 1 winner in row-lock race, got {succeeded}"
    assert failed == 1, f"Expected 1 loser in row-lock race, got {failed}"

    loser_exc = [e for e in review_exceptions if e is not None][0]
    assert isinstance(loser_exc, HTTPException)
    assert loser_exc.status_code in (400, 409)

    # Final DB check: leave request status is definitive and ledger has exactly one finalizing TX
    with session_factory() as s:
        final_req = s.get(LeaveRequest, req_id)
        assert final_req.status in (LeaveRequestStatus.APPROVED.value, LeaveRequestStatus.REJECTED.value)

        tx_count = s.execute(
            select(sa.func.count(LeaveBalanceTransaction.id)).where(
                LeaveBalanceTransaction.leave_request_id == req_id,
                LeaveBalanceTransaction.transaction_type.in_([
                    TransactionType.COMMIT.value,
                    TransactionType.RELEASE.value,
                ]),
            )
        ).scalar_one()
        assert tx_count == 1, f"Expected exactly 1 final ledger entry, found {tx_count}"


# ============================================================================
# PG-08: Transaction Ledger Idempotency Indexes
# ============================================================================

def test_pg_08_transaction_ledger_idempotency(db_session: Session, seed_data):
    """PG-08: Verify uq_tx_request_type and uq_tx_compoff_claim partial indexes."""
    emp = seed_data["employee"]
    cat = db_session.execute(
        select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.EARNED.value)
    ).scalar_one()
    bal = get_or_create_leave_balance(db_session, emp.id, cat.id, 2026)

    # 1. Create a dummy leave request
    req = LeaveRequest(
        employee_id=emp.id,
        category_id=cat.id,
        start_date=date(2026, 12, 15),
        end_date=date(2026, 12, 15),
        is_half_day=False,
        half_day_period=None,
        total_days=Decimal("1.0"),
        reason="Ledger idempotency test",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(req)
    db_session.flush()

    # 2. First RESERVE transaction succeeds
    tx1 = LeaveBalanceTransaction(
        balance_id=bal.id,
        leave_request_id=req.id,
        transaction_type=TransactionType.RESERVE.value,
        amount=Decimal("1.0"),
        balance_before=Decimal("10.0"),
        balance_after=Decimal("9.0"),
        created_by_id=emp.id,
    )
    db_session.add(tx1)
    db_session.commit()

    # 3. Duplicate RESERVE transaction for same request must violate uq_tx_request_type
    tx2 = LeaveBalanceTransaction(
        balance_id=bal.id,
        leave_request_id=req.id,
        transaction_type=TransactionType.RESERVE.value,
        amount=Decimal("1.0"),
        balance_before=Decimal("9.0"),
        balance_after=Decimal("8.0"),
        created_by_id=emp.id,
    )
    db_session.add(tx2)
    with pytest.raises(IntegrityError) as exc_tx_dup:
        db_session.commit()
    db_session.rollback()
    assert "uq_tx_request_type" in str(exc_tx_dup.value)


# ============================================================================
# PG-09: Comp-Off FIFO Consumption and Expiry
# ============================================================================

def test_pg_09_compoff_fifo_consumption_and_expiry(db_session: Session, seed_data):
    """PG-09: Verify Comp-Off FIFO consumption ordering and 90-day expiry under PostgreSQL."""
    emp = seed_data["employee"]

    # Seed 2 approved claims: Older credit expires sooner
    c1 = CompOffClaim(
        employee_id=emp.id,
        work_date=date(2026, 8, 1),
        days_claimed=Decimal("1.0"),
        days_approved=Decimal("1.0"),
        remaining_days=Decimal("1.0"),
        expiry_date=date(2026, 10, 30),  # Expired relative to 2026-10-31
        status=CompOffClaimStatus.APPROVED.value,
        credit_status=CompOffCreditStatus.ACTIVE.value,
        reason="Emergency sprint",
    )
    c2 = CompOffClaim(
        employee_id=emp.id,
        work_date=date(2026, 9, 1),
        days_claimed=Decimal("1.0"),
        days_approved=Decimal("1.0"),
        remaining_days=Decimal("1.0"),
        expiry_date=date(2026, 11, 30),  # Active
        status=CompOffClaimStatus.APPROVED.value,
        credit_status=CompOffCreditStatus.ACTIVE.value,
        reason="Server migration",
    )
    db_session.add_all([c1, c2])
    db_session.commit()

    # 1. FIFO consumption of 1.0 day should drain the oldest credit (c1)
    consumed = consume_compoff_credits_fifo(
        db=db_session,
        employee_id=emp.id,
        days_to_consume=Decimal("1.0"),
        leave_date=date(2026, 10, 15),
    )
    db_session.commit()

    assert len(consumed) == 1
    assert consumed[0].id == c1.id

    db_session.refresh(c1)
    db_session.refresh(c2)
    assert c1.remaining_days == Decimal("0.0")
    assert c1.credit_status == CompOffCreditStatus.EXHAUSTED.value
    assert c2.remaining_days == Decimal("1.0")
    assert c2.credit_status == CompOffCreditStatus.ACTIVE.value

    # 2. Expiry test: c2 will expire if reference date advances past its expiry date
    expired_count = expire_compoff_credits(db=db_session, as_of_date=date(2026, 12, 1))
    db_session.commit()

    assert expired_count >= 1
    db_session.refresh(c2)
    assert c2.credit_status == CompOffCreditStatus.EXPIRED.value


# ============================================================================
# PG-10: Atomic Rollback and Invariant Safety
# ============================================================================

def test_pg_10_transaction_rollback_and_invariant_safety(session_factory, seed_data):
    """PG-10: Verify mid-mutation exception completely reverts state without leaks."""
    emp = seed_data["employee"]

    with session_factory() as s:
        cat = s.execute(
            select(LeaveCategory).where(LeaveCategory.code == LeaveCategoryCode.CASUAL.value)
        ).scalar_one()
        category_id = cat.id
        bal = get_or_create_leave_balance(s, emp.id, category_id, 2026)
        bal.entitled_days = Decimal("5.0")
        bal.reserved_days = Decimal("0.0")
        bal.used_days = Decimal("0.0")
        s.commit()

    # Simulate transaction that modifies balance but fails prior to commit
    with session_factory() as s:
        acquire_employee_advisory_lock(s, emp.id)
        bal = s.execute(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == emp.id,
                LeaveBalance.category_id == category_id,
                LeaveBalance.year == 2026,
            ).with_for_update()
        ).scalar_one()

        bal.reserved_days += Decimal("2.0")
        s.flush()

        # Simulated crash / unhandled error
        s.rollback()

    # Verify no persistent changes
    with session_factory() as s:
        bal_after = s.execute(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == emp.id,
                LeaveBalance.category_id == category_id,
                LeaveBalance.year == 2026,
            )
        ).scalar_one()
        assert bal_after.reserved_days == Decimal("0.0")
        assert bal_after.available_days == Decimal("5.0")
