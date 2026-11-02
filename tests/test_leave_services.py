"""Isolated unit tests for Leave Management schemas, domain calculations, and service layer.

Runs in complete isolation using an in-memory SQLite database (zero PostgreSQL or Docker dependency).
Tests:
1. Date and half-day Pydantic validation
2. Sandwich calculation matrix (including single days, weekends, holidays, half-days)
3. Overlap matrix (full-day vs full-day, full-day vs half-day, morning vs afternoon)
4. Leave submission and balance reservation
5. Approval, rejection, and anti-self-approval
6. Cancellation and refund workflows
7. Comp-Off claim, FIFO consumption, and 90-day expiry
8. Monthly Earned Leave accrual idempotency
9. HR balance adjustment and ledger snapshot integrity
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.employee import Employee, UserRole
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
    CompOffClaimCreateRequest,
    CompOffClaimReviewRequest,
    LeaveApplyRequest,
    LeaveBalanceAdjustmentRequest,
    LeaveCalculationRequest,
    LeaveReviewRequest,
)
from app.services.leave import (
    adjust_leave_balance,
    approve_leave_request,
    calculate_leave_days,
    cancel_leave_request,
    check_leave_conflicts,
    consume_compoff_credits_fifo,
    expire_compoff_credits,
    get_or_create_leave_balance,
    prorate_annual_entitlements,
    reject_leave_request,
    review_compoff_claim,
    run_monthly_earned_accrual,
    submit_compoff_claim,
    submit_leave_request,
)


@pytest.fixture
def db_session():
    """Create an isolated in-memory SQLite database session with all tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()

    # Seed leave categories
    categories = [
        LeaveCategory(code="CASUAL", name="Casual Leave", annual_quota=Decimal("6.0"), is_paid=True, max_carry_forward=Decimal("0.0")),
        LeaveCategory(code="SICK", name="Sick Leave", annual_quota=Decimal("6.0"), is_paid=True, max_carry_forward=Decimal("0.0")),
        LeaveCategory(code="EARNED", name="Earned Leave", annual_quota=Decimal("12.0"), is_paid=True, max_carry_forward=Decimal("12.0"), accrual_rate_monthly=Decimal("1.0")),
        LeaveCategory(code="UNPAID", name="Loss of Pay", annual_quota=Decimal("0.0"), is_paid=False, max_carry_forward=Decimal("0.0")),
        LeaveCategory(code="COMP_OFF", name="Compensatory Off", annual_quota=Decimal("0.0"), is_paid=True, max_carry_forward=Decimal("0.0")),
        LeaveCategory(code="SABBATICAL", name="Sabbatical Leave", annual_quota=Decimal("0.0"), is_paid=False, max_carry_forward=Decimal("0.0")),
    ]
    session.add_all(categories)

    # Seed test employees
    emp = Employee(
        id=1,
        employee_code="EMP001",
        name="John Employee",
        email="john@company.com",
        role=UserRole.EMPLOYEE.value,
        is_active=True,
    )
    hr = Employee(
        id=2,
        employee_code="HR001",
        name="Jane HR",
        email="jane.hr@company.com",
        role=UserRole.HR.value,
        is_active=True,
    )
    session.add_all([emp, hr])
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


# ============================================================================
# 1. Schema Validation Tests
# ============================================================================

def test_leave_apply_schema_validation():
    """Test strict validation on LeaveApplyRequest."""
    # Valid full-day request
    req = LeaveApplyRequest(
        category_code=LeaveCategoryCode.CASUAL,
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        reason="Family vacation",
    )
    assert req.start_date == date(2026, 5, 4)

    # End date before start date rejected
    with pytest.raises(ValidationError):
        LeaveApplyRequest(
            category_code=LeaveCategoryCode.CASUAL,
            start_date=date(2026, 5, 5),
            end_date=date(2026, 5, 4),
            reason="Invalid dates",
        )

    # Half-day with different start/end date rejected
    with pytest.raises(ValidationError):
        LeaveApplyRequest(
            category_code=LeaveCategoryCode.CASUAL,
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 5),
            is_half_day=True,
            half_day_period=HalfDayPeriod.MORNING,
            reason="Invalid half day",
        )

    # Half-day without period rejected
    with pytest.raises(ValidationError):
        LeaveApplyRequest(
            category_code=LeaveCategoryCode.CASUAL,
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 4),
            is_half_day=True,
            half_day_period=None,
            reason="Missing period",
        )

    # Full-day with period rejected
    with pytest.raises(ValidationError):
        LeaveApplyRequest(
            category_code=LeaveCategoryCode.CASUAL,
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 4),
            is_half_day=False,
            half_day_period=HalfDayPeriod.MORNING,
            reason="Full day with period",
        )

    # Too short reason rejected
    with pytest.raises(ValidationError):
        LeaveApplyRequest(
            category_code=LeaveCategoryCode.CASUAL,
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 4),
            reason="  ",
        )


def test_compoff_claim_schema_validation():
    """Test validation on CompOffClaimCreateRequest."""
    # Valid claim
    claim = CompOffClaimCreateRequest(
        work_date=date(2026, 5, 3),
        days_claimed=Decimal("1.0"),
        reason="Production incident support on Sunday",
    )
    assert claim.days_claimed == Decimal("1.0")

    # Invalid days_claimed (e.g. 1.5) rejected
    with pytest.raises(ValidationError):
        CompOffClaimCreateRequest(
            work_date=date(2026, 5, 3),
            days_claimed=Decimal("1.5"),
            reason="Invalid amount",
        )


# ============================================================================
# 2. Sandwich Calculation Tests
# ============================================================================

def test_sandwich_calculation_single_days(db_session: Session):
    """Test that single leave days adjacent to weekends/holidays are NOT penalized."""
    # Single Friday (8 May 2026 is Friday)
    res_fri = calculate_leave_days(db_session, start_date=date(2026, 5, 8), end_date=date(2026, 5, 8))
    assert res_fri.total_days == Decimal("1.0")
    assert res_fri.sandwiched_weekend_days == 0

    # Single Monday (11 May 2026 is Monday)
    res_mon = calculate_leave_days(db_session, start_date=date(2026, 5, 11), end_date=date(2026, 5, 11))
    assert res_mon.total_days == Decimal("1.0")
    assert res_mon.sandwiched_weekend_days == 0


def test_sandwich_calculation_weekend_span(db_session: Session):
    """Test that Friday to Monday continuous request sandwiches the weekend (4.0 days)."""
    # 8 May 2026 (Fri) to 11 May 2026 (Mon)
    res = calculate_leave_days(db_session, start_date=date(2026, 5, 8), end_date=date(2026, 5, 11))
    assert res.total_days == Decimal("4.0")
    assert res.working_days == Decimal("2.0")
    assert res.sandwiched_weekend_days == 2


def test_sandwich_calculation_public_holiday(db_session: Session):
    """Test that a continuous request bookending a public holiday counts the holiday."""
    # Seed Thursday 14 May 2026 as holiday
    holiday = CompanyHoliday(holiday_date=date(2026, 5, 14), name="Ascension Day", year=2026)
    db_session.add(holiday)
    db_session.commit()

    # Wednesday 13 May to Friday 15 May (3 days total)
    res = calculate_leave_days(db_session, start_date=date(2026, 5, 13), end_date=date(2026, 5, 15))
    assert res.total_days == Decimal("3.0")
    assert res.working_days == Decimal("2.0")
    assert res.sandwiched_holiday_days == 1


def test_sandwich_calculation_half_day_exemption(db_session: Session):
    """Test that half-day leave on Friday never triggers sandwich penalties."""
    res = calculate_leave_days(
        db_session,
        start_date=date(2026, 5, 8),
        end_date=date(2026, 5, 8),
        is_half_day=True,
        half_day_period="afternoon",
    )
    assert res.total_days == Decimal("0.5")
    assert res.sandwiched_weekend_days == 0


def test_calculation_zero_working_days_rejected(db_session: Session):
    """Test that requesting leave entirely on a weekend raises HTTP 400."""
    # Saturday 9 May to Sunday 10 May 2026
    with pytest.raises(HTTPException) as exc:
        calculate_leave_days(db_session, start_date=date(2026, 5, 9), end_date=date(2026, 5, 10))
    assert exc.value.status_code == 400


# ============================================================================
# 3. Overlap Collision Matrix Tests
# ============================================================================

def test_overlap_matrix_full_day_collisions(db_session: Session):
    """Test full-day vs full-day and full-day vs half-day collisions."""
    emp_id = 1
    # Submit Request 1: 4 May to 6 May 2026
    req1 = LeaveRequest(
        employee_id=emp_id,
        category_id=1,
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 6),
        is_half_day=False,
        total_days=Decimal("3.0"),
        reason="Existing leave",
        status=LeaveRequestStatus.APPROVED.value,
    )
    db_session.add(req1)
    db_session.commit()

    # 1. Full-day overlapping 5 May to 7 May -> Collision
    conflict1 = check_leave_conflicts(
        db_session,
        employee_id=emp_id,
        start_date=date(2026, 5, 5),
        end_date=date(2026, 5, 7),
        is_half_day=False,
        half_day_period=None,
    )
    assert conflict1 is not None
    assert conflict1.id == req1.id

    # 2. Half-day on 5 May -> Collision with existing full-day
    conflict2 = check_leave_conflicts(
        db_session,
        employee_id=emp_id,
        start_date=date(2026, 5, 5),
        end_date=date(2026, 5, 5),
        is_half_day=True,
        half_day_period="morning",
    )
    assert conflict2 is not None


def test_overlap_matrix_half_day_slots(db_session: Session):
    """Test that complementary half-days on the same date are permitted, but identical are blocked."""
    emp_id = 1
    # Existing morning half-day on 18 May 2026
    req_morn = LeaveRequest(
        employee_id=emp_id,
        category_id=1,
        start_date=date(2026, 5, 18),
        end_date=date(2026, 5, 18),
        is_half_day=True,
        half_day_period="morning",
        total_days=Decimal("0.5"),
        reason="Morning doctor visit",
        status=LeaveRequestStatus.PENDING.value,
    )
    db_session.add(req_morn)
    db_session.commit()

    # 1. Afternoon on same date -> ALLOWED (No collision)
    conflict_aft = check_leave_conflicts(
        db_session,
        employee_id=emp_id,
        start_date=date(2026, 5, 18),
        end_date=date(2026, 5, 18),
        is_half_day=True,
        half_day_period="afternoon",
    )
    assert conflict_aft is None

    # 2. Second morning on same date -> CONFLICT
    conflict_morn2 = check_leave_conflicts(
        db_session,
        employee_id=emp_id,
        start_date=date(2026, 5, 18),
        end_date=date(2026, 5, 18),
        is_half_day=True,
        half_day_period="morning",
    )
    assert conflict_morn2 is not None


# ============================================================================
# 4. Leave Submission & Balance Reservation Tests
# ============================================================================

def test_submit_leave_reserves_balance(db_session: Session):
    """Test that submitting leave reserves days and writes RESERVE transaction."""
    emp_id = 1
    # Seed 6 days Casual Leave balance
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)
    assert bal.available_days == Decimal("6.0")
    assert bal.reserved_days == Decimal("0.0")

    # Submit 2 days leave (Monday 4 May to Tuesday 5 May)
    resp = submit_leave_request(
        db=db_session,
        employee_id=emp_id,
        category_code="CASUAL",
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        half_day_period=None,
        reason="Personal work",
    )
    assert resp.status == "PENDING"
    assert resp.total_days == Decimal("2.0")

    # Verify balance mutation
    db_session.refresh(bal)
    assert bal.reserved_days == Decimal("2.0")
    assert bal.available_days == Decimal("4.0")

    # Verify ledger entry
    tx = db_session.execute(
        select(LeaveBalanceTransaction).where(LeaveBalanceTransaction.leave_request_id == resp.id)
    ).scalar_one()
    assert tx.transaction_type == TransactionType.RESERVE.value
    assert tx.amount == Decimal("2.0")
    assert tx.balance_before == Decimal("6.0")
    assert tx.balance_after == Decimal("4.0")

    # Verify audit log
    audit = db_session.execute(
        select(LeaveAuditLog).where(LeaveAuditLog.entity_id == resp.id, LeaveAuditLog.action == "SUBMITTED")
    ).scalar_one()
    assert audit.actor_id == emp_id


def test_submit_insufficient_balance_rejected(db_session: Session):
    """Test that requesting more leave than available fails with HTTP 400."""
    emp_id = 1
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)
    bal.entitled_days = Decimal("1.0")
    db_session.commit()

    # Attempt to request 2 days with only 1 day available
    with pytest.raises(HTTPException) as exc:
        submit_leave_request(
            db=db_session,
            employee_id=emp_id,
            category_code="CASUAL",
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 5),
            is_half_day=False,
            half_day_period=None,
            reason="Exceeds balance",
        )
    assert exc.value.status_code == 400
    assert "Insufficient leave balance" in exc.value.detail


# ============================================================================
# 5. Approval, Rejection & Anti-Self-Approval Tests
# ============================================================================

def test_approve_leave_converts_reservation_to_used(db_session: Session):
    """Test that approval moves reserved days to used days and creates COMMIT transaction."""
    emp_id = 1
    hr_id = 2
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)

    # Submit 2 days leave
    resp = submit_leave_request(
        db=db_session,
        employee_id=emp_id,
        category_code="CASUAL",
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        half_day_period=None,
        reason="Family event",
    )

    # HR approves
    appr_resp = approve_leave_request(
        db=db_session,
        request_id=resp.id,
        reviewer_id=hr_id,
        review_notes="Approved by HR",
    )
    assert appr_resp.status == "APPROVED"
    assert appr_resp.reviewed_by_id == hr_id

    # Verify balance
    db_session.refresh(bal)
    assert bal.reserved_days == Decimal("0.0")
    assert bal.used_days == Decimal("2.0")
    assert bal.available_days == Decimal("4.0")

    # Verify COMMIT transaction
    commit_tx = db_session.execute(
        select(LeaveBalanceTransaction).where(
            LeaveBalanceTransaction.leave_request_id == resp.id,
            LeaveBalanceTransaction.transaction_type == TransactionType.COMMIT.value,
        )
    ).scalar_one()
    assert commit_tx.amount == Decimal("2.0")


def test_reject_leave_releases_reservation(db_session: Session):
    """Test that rejection releases reserved days back to available balance."""
    emp_id = 1
    hr_id = 2
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)

    resp = submit_leave_request(
        db=db_session,
        employee_id=emp_id,
        category_code="CASUAL",
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        half_day_period=None,
        reason="Vacation",
    )

    # HR rejects
    rej_resp = reject_leave_request(
        db=db_session,
        request_id=resp.id,
        reviewer_id=hr_id,
        review_notes="Critical delivery ongoing",
    )
    assert rej_resp.status == "REJECTED"

    # Verify balance is restored
    db_session.refresh(bal)
    assert bal.reserved_days == Decimal("0.0")
    assert bal.used_days == Decimal("0.0")
    assert bal.available_days == Decimal("6.0")


def test_anti_self_approval_enforced(db_session: Session):
    """Test that an employee cannot approve or reject their own leave."""
    emp_id = 1
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)

    resp = submit_leave_request(
        db=db_session,
        employee_id=emp_id,
        category_code="CASUAL",
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 4),
        is_half_day=False,
        half_day_period=None,
        reason="Self approval test",
    )

    # Employee attempting to approve their own request fails
    with pytest.raises(HTTPException) as exc:
        approve_leave_request(db_session, request_id=resp.id, reviewer_id=emp_id)
    assert exc.value.status_code == 400
    assert "cannot approve their own" in exc.value.detail


# ============================================================================
# 6. Cancellation & Refund Tests
# ============================================================================

def test_cancel_pending_leave(db_session: Session):
    """Test that employee cancelling pending request releases reserved balance."""
    emp_id = 1
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)

    resp = submit_leave_request(
        db=db_session,
        employee_id=emp_id,
        category_code="CASUAL",
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        half_day_period=None,
        reason="Plans changed",
    )

    cancel_resp = cancel_leave_request(
        db=db_session,
        request_id=resp.id,
        actor_id=emp_id,
        reason="Meeting cancelled",
        is_hr_or_admin=False,
    )
    assert cancel_resp.status == "CANCELLED"

    db_session.refresh(bal)
    assert bal.reserved_days == Decimal("0.0")
    assert bal.available_days == Decimal("6.0")


def test_cancel_approved_leave_by_hr_refunds_balance(db_session: Session):
    """Test that HR cancelling an approved request refunds used days."""
    emp_id = 1
    hr_id = 2
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)

    resp = submit_leave_request(
        db=db_session,
        employee_id=emp_id,
        category_code="CASUAL",
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        half_day_period=None,
        reason="Conference",
    )
    approve_leave_request(db_session, request_id=resp.id, reviewer_id=hr_id)

    # Employee cannot cancel approved leave directly
    with pytest.raises(HTTPException) as exc:
        cancel_leave_request(db_session, request_id=resp.id, actor_id=emp_id, reason="Changed mind", is_hr_or_admin=False)
    assert exc.value.status_code == 403

    # HR cancels approved leave -> balance refunded
    cancel_resp = cancel_leave_request(
        db=db_session,
        request_id=resp.id,
        actor_id=hr_id,
        reason="Conference rescheduled",
        is_hr_or_admin=True,
    )
    assert cancel_resp.status == "CANCELLED"

    db_session.refresh(bal)
    assert bal.used_days == Decimal("0.0")
    assert bal.available_days == Decimal("6.0")

    # Verify REFUND transaction
    refund_tx = db_session.execute(
        select(LeaveBalanceTransaction).where(
            LeaveBalanceTransaction.leave_request_id == resp.id,
            LeaveBalanceTransaction.transaction_type == TransactionType.REFUND.value,
        )
    ).scalar_one()
    assert refund_tx.amount == Decimal("2.0")


# ============================================================================
# 7. Comp-Off Claim, FIFO Consumption & 90-Day Expiry Tests
# ============================================================================

def test_compoff_claim_approval_and_credit(db_session: Session):
    """Test Comp-Off claim on weekend, HR approval, and credit creation."""
    emp_id = 1
    hr_id = 2
    sunday = date(2026, 5, 3)

    # Submit claim on Sunday
    claim_resp = submit_compoff_claim(
        db=db_session,
        employee_id=emp_id,
        work_date=sunday,
        days_claimed=Decimal("1.0"),
        reason="Server migration",
    )
    assert claim_resp.status == "PENDING"

    # HR approves
    approved = review_compoff_claim(
        db=db_session,
        claim_id=claim_resp.id,
        reviewer_id=hr_id,
        is_approve=True,
        days_approved=Decimal("1.0"),
        review_notes="Approved",
    )
    assert approved.status == "APPROVED"
    assert approved.credit_status == "ACTIVE"
    assert approved.remaining_days == Decimal("1.0")
    assert approved.expiry_date == sunday + timedelta(days=90)

    # Verify Comp-Off balance
    comp_bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=5, year=2026)
    assert comp_bal.accrued_days == Decimal("1.0")


def test_compoff_fifo_consumption(db_session: Session):
    """Test FIFO consumption of Comp-Off credits based on earliest expiry date."""
    emp_id = 1
    # Create Claim 1 expiring sooner (work_date: 1 March 2026 -> expiry: 30 May 2026)
    c1 = CompOffClaim(
        employee_id=emp_id,
        work_date=date(2026, 3, 1),
        days_claimed=Decimal("1.0"),
        days_approved=Decimal("1.0"),
        remaining_days=Decimal("1.0"),
        expiry_date=date(2026, 5, 30),
        reason="Incident 1",
        status=CompOffClaimStatus.APPROVED.value,
        credit_status=CompOffCreditStatus.ACTIVE.value,
    )
    # Create Claim 2 expiring later (work_date: 15 March 2026 -> expiry: 13 June 2026)
    c2 = CompOffClaim(
        employee_id=emp_id,
        work_date=date(2026, 3, 15),
        days_claimed=Decimal("1.0"),
        days_approved=Decimal("1.0"),
        remaining_days=Decimal("1.0"),
        expiry_date=date(2026, 6, 13),
        reason="Incident 2",
        status=CompOffClaimStatus.APPROVED.value,
        credit_status=CompOffCreditStatus.ACTIVE.value,
    )
    db_session.add_all([c1, c2])
    db_session.commit()

    # Consume 1.5 days on 10 May 2026
    consumed = consume_compoff_credits_fifo(
        db=db_session,
        employee_id=emp_id,
        days_to_consume=Decimal("1.5"),
        leave_date=date(2026, 5, 10),
    )
    assert len(consumed) == 2

    # Claim 1 should be completely exhausted (0.0 remaining)
    db_session.refresh(c1)
    assert c1.remaining_days == Decimal("0.0")
    assert c1.credit_status == CompOffCreditStatus.EXHAUSTED.value

    # Claim 2 should be partially exhausted (0.5 remaining)
    db_session.refresh(c2)
    assert c2.remaining_days == Decimal("0.5")
    assert c2.credit_status == CompOffCreditStatus.PARTIALLY_EXHAUSTED.value


def test_compoff_90_day_expiry_job(db_session: Session):
    """Test that unconsumed credits past 90 days are expired and deducted from accrued balance."""
    emp_id = 1
    # Comp-Off balance with 1.0 accrued
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=5, year=2026)
    bal.accrued_days = Decimal("1.0")

    # Expired claim: work date 1 Jan 2026, expiry 1 April 2026
    c = CompOffClaim(
        employee_id=emp_id,
        work_date=date(2026, 1, 1),
        days_claimed=Decimal("1.0"),
        days_approved=Decimal("1.0"),
        remaining_days=Decimal("1.0"),
        expiry_date=date(2026, 4, 1),
        reason="Old holiday work",
        status=CompOffClaimStatus.APPROVED.value,
        credit_status=CompOffCreditStatus.ACTIVE.value,
    )
    db_session.add(c)
    db_session.commit()

    # Run expiry job as of 15 May 2026
    expired_count = expire_compoff_credits(db_session, as_of_date=date(2026, 5, 15))
    assert expired_count == 1

    db_session.refresh(c)
    assert c.credit_status == CompOffCreditStatus.EXPIRED.value
    assert c.remaining_days == Decimal("0.0")

    db_session.refresh(bal)
    assert bal.accrued_days == Decimal("0.0")

    # Verify COMPOFF_EXPIRY transaction
    tx = db_session.execute(
        select(LeaveBalanceTransaction).where(
            LeaveBalanceTransaction.comp_off_claim_id == c.id,
            LeaveBalanceTransaction.transaction_type == TransactionType.COMPOFF_EXPIRY.value,
        )
    ).scalar_one()
    assert tx.amount == Decimal("1.0")


# ============================================================================
# 8. Monthly Accrual & Idempotency Tests
# ============================================================================

def test_monthly_earned_leave_accrual_idempotency(db_session: Session):
    """Test monthly 1.0 day accrual and prevention of duplicate runs."""
    hr_id = 2

    # Run accrual for April 2026
    count1, skipped1 = run_monthly_earned_accrual(db_session, year=2026, month=4, actor_id=hr_id)
    assert count1 == 2  # 2 active employees
    assert len(skipped1) == 0

    # Verify balance for employee 1
    bal = get_or_create_leave_balance(db_session, employee_id=1, category_id=3, year=2026)
    assert bal.accrued_days == Decimal("1.0")

    # Run accrual again for April 2026 -> should skip idempotently
    count2, skipped2 = run_monthly_earned_accrual(db_session, year=2026, month=4, actor_id=hr_id)
    assert count2 == 0
    assert len(skipped2) == 2  # Both skipped

    # Balance must remain exactly 1.0
    db_session.refresh(bal)
    assert bal.accrued_days == Decimal("1.0")


# ============================================================================
# 9. HR Balance Adjustment & Ledger Integrity Tests
# ============================================================================

def test_hr_balance_adjustment(db_session: Session):
    """Test manual HR balance adjustment creates ADJUSTMENT transaction and reconciles."""
    emp_id = 1
    hr_id = 2
    bal = get_or_create_leave_balance(db_session, employee_id=emp_id, category_id=1, year=2026)
    assert bal.available_days == Decimal("6.0")

    # Add 2.0 days adjustment to entitled bucket
    req = LeaveBalanceAdjustmentRequest(
        employee_id=emp_id,
        category_code=LeaveCategoryCode.CASUAL,
        year=2026,
        bucket="entitled",
        amount=Decimal("2.0"),
        reason="Special performance reward days",
    )
    adj_resp = adjust_leave_balance(db_session, req=req, hr_actor_id=hr_id)
    assert adj_resp.entitled_days == Decimal("8.0")
    assert adj_resp.available_days == Decimal("8.0")

    # Verify ADJUSTMENT transaction
    tx = db_session.execute(
        select(LeaveBalanceTransaction).where(
            LeaveBalanceTransaction.balance_id == bal.id,
            LeaveBalanceTransaction.transaction_type == TransactionType.ADJUSTMENT.value,
        )
    ).scalar_one()
    assert tx.amount == Decimal("2.0")
    assert tx.balance_before == Decimal("6.0")
    assert tx.balance_after == Decimal("8.0")
    assert tx.created_by_id == hr_id
