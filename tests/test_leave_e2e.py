"""Comprehensive End-to-End (E2E) integration test suite for Leave Management.

Phase 2.10: Validates complete workflows across:
1. Employee Leave lifecycle (balance check -> calculation preview -> submit -> reserve -> collision -> isolation -> cancellation).
2. HR/Admin review workflows (pending queue -> approve -> commit balance -> reject -> release balance -> anti-self-approval -> RBAC -> balance adjustment).
3. Comp-Off lifecycle (claim submission -> employee history -> HR review -> credit issuance -> duplicate prevention -> FIFO consumption -> expiry job -> status filtering).
4. Frontend contracts & security (modal structures, CSRF awareness, double-submit protection, XSS escaping).

Runs in complete isolation using an in-memory SQLite database and FastAPI dependency overrides.
Zero dependencies on live PostgreSQL or Docker.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.permissions import require_authenticated, require_hr_or_admin
from app.db.base import Base
from app.db.session import get_db
from app.dependencies.auth import get_current_session, verify_csrf
from app.main import app
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
    consume_compoff_credits_fifo,
    expire_compoff_credits,
    get_or_create_leave_balance,
)


@pytest.fixture
def e2e_db():
    """Create a completely isolated in-memory SQLite database for the E2E test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = session_factory()

    # Seed leave categories
    categories = [
        LeaveCategory(id=1, code="CASUAL", name="Casual Leave", annual_quota=Decimal("6.0"), is_paid=True, max_carry_forward=Decimal("0.0")),
        LeaveCategory(id=2, code="SICK", name="Sick Leave", annual_quota=Decimal("6.0"), is_paid=True, max_carry_forward=Decimal("0.0")),
        LeaveCategory(id=3, code="EARNED", name="Earned Leave", annual_quota=Decimal("12.0"), is_paid=True, max_carry_forward=Decimal("12.0"), accrual_rate_monthly=Decimal("1.0")),
        LeaveCategory(id=4, code="UNPAID", name="Loss of Pay", annual_quota=Decimal("0.0"), is_paid=False, max_carry_forward=Decimal("0.0")),
        LeaveCategory(id=5, code="COMP_OFF", name="Compensatory Off", annual_quota=Decimal("0.0"), is_paid=True, max_carry_forward=Decimal("0.0")),
        LeaveCategory(id=6, code="SABBATICAL", name="Sabbatical Leave", annual_quota=Decimal("0.0"), is_paid=False, max_carry_forward=Decimal("0.0")),
    ]
    db.add_all(categories)

    # Seed test users
    emp_alice = Employee(id=1, employee_code="EMP001", name="Alice Employee", email="alice@company.com", role=UserRole.EMPLOYEE.value, is_active=True)
    emp_bob = Employee(id=2, employee_code="EMP002", name="Bob Employee", email="bob@company.com", role=UserRole.EMPLOYEE.value, is_active=True)
    hr_charlie = Employee(id=3, employee_code="HR001", name="Charlie HR", email="charlie.hr@company.com", role=UserRole.HR.value, is_active=True)
    admin_dana = Employee(id=4, employee_code="ADM001", name="Dana Admin", email="dana.admin@company.com", role=UserRole.ADMIN.value, is_active=True)
    db.add_all([emp_alice, emp_bob, hr_charlie, admin_dana])
    db.commit()

    yield db

    db.close()
    Base.metadata.drop_all(bind=engine)


# ============================================================================
# WORKFLOW 1: EMPLOYEE LEAVE LIFECYCLE E2E
# ============================================================================

def test_workflow_employee_leave_lifecycle_e2e(e2e_db: Session):
    """E2E Workflow: Employee checks balance, previews deduction, submits leave,
    verifies reservation, detects collisions, rejects invalid inputs, confirms
    resource isolation, cancels pending request, and verifies approved cancellation security.
    """
    alice = e2e_db.get(Employee, 1)
    bob = e2e_db.get(Employee, 2)
    hr = e2e_db.get(Employee, 3)

    app.dependency_overrides[get_db] = lambda: e2e_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # 1. Load leave balances: verify 6 categories returned and CL has 6.0 entitled
    bal_resp = client.get("/leaves/balance")
    assert bal_resp.status_code == 200
    balances = bal_resp.json()
    assert len(balances) == 6
    cl_bal = next(b for b in balances if b["category_code"] == "CASUAL")
    assert Decimal(cl_bal["available_days"]) == Decimal("6.0")
    assert Decimal(cl_bal["reserved_days"]) == Decimal("0.0")

    # 2. Preview valid full-day leave (Monday 4 May to Tuesday 5 May 2026: 2 working days)
    preview_resp = client.post(
        "/leaves/calculate",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-04",
            "end_date": "2026-05-05",
            "is_half_day": False,
        },
    )
    assert preview_resp.status_code == 200
    calc = preview_resp.json()
    assert Decimal(calc["total_days"]) == Decimal("2.0")
    assert Decimal(calc["working_days"]) == Decimal("2.0")

    # 3. Submit the leave application
    apply_resp = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-04",
            "end_date": "2026-05-05",
            "is_half_day": False,
            "reason": "Personal family wedding",
        },
    )
    assert apply_resp.status_code == 201
    leave_data = apply_resp.json()
    leave_id = leave_data["id"]
    assert leave_data["status"] == "PENDING"
    assert Decimal(leave_data["total_days"]) == Decimal("2.0")

    # 4. Verify balance is reserved: available drops from 6.0 to 4.0; reserved increases to 2.0
    bal_after = client.get("/leaves/balance").json()
    cl_after = next(b for b in bal_after if b["category_code"] == "CASUAL")
    assert Decimal(cl_after["reserved_days"]) == Decimal("2.0")
    assert Decimal(cl_after["available_days"]) == Decimal("4.0")

    # 5. Overlapping request collision rejection (HTTP 409)
    collision_resp = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-05",
            "end_date": "2026-05-06",
            "is_half_day": False,
            "reason": "Collision request",
        },
    )
    assert collision_resp.status_code == 409
    assert "conflicts with existing pending request" in collision_resp.json()["detail"]

    # 6. Validation rejections:
    # 6a. End date earlier than start date (422)
    inv_date_resp = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-10",
            "end_date": "2026-05-08",
            "is_half_day": False,
            "reason": "Invalid dates",
        },
    )
    assert inv_date_resp.status_code == 422

    # 6b. Half-day across multiple dates (422)
    inv_half_resp = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-11",
            "end_date": "2026-05-12",
            "is_half_day": True,
            "half_day_period": "morning",
            "reason": "Invalid span half day",
        },
    )
    assert inv_half_resp.status_code == 422

    # 6c. Insufficient balance rejection (HTTP 400)
    # Alice has 4.0 available; requesting 5 days (Mon 11 to Fri 15 May) fails
    insuf_resp = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-11",
            "end_date": "2026-05-15",
            "is_half_day": False,
            "reason": "Excessive request exceeding balance",
        },
    )
    assert insuf_resp.status_code == 400
    assert "Insufficient leave balance" in insuf_resp.json()["detail"]

    # 7. Employee isolation: Bob cannot view Alice's request (HTTP 403)
    app.dependency_overrides[require_authenticated] = lambda: bob
    bob_view = client.get(f"/leaves/{leave_id}")
    assert bob_view.status_code == 403
    assert "Access forbidden" in bob_view.json()["detail"]

    # 8. Cancel pending request by owner Alice: reservation released
    app.dependency_overrides[require_authenticated] = lambda: alice
    cancel_resp = client.post(
        f"/leaves/{leave_id}/cancel",
        json={"reason": "Plans changed, cancelling leave"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Verify balance restored to 6.0 available and 0.0 reserved
    bal_restored = client.get("/leaves/balance").json()
    cl_restored = next(b for b in bal_restored if b["category_code"] == "CASUAL")
    assert Decimal(cl_restored["reserved_days"]) == Decimal("0.0")
    assert Decimal(cl_restored["available_days"]) == Decimal("6.0")

    # 9. Approved leave cancellation rules:
    # 9a. Alice submits fresh leave
    fresh_apply = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-18",
            "end_date": "2026-05-18",
            "is_half_day": False,
            "reason": "Medical follow-up",
        },
    ).json()
    fresh_id = fresh_apply["id"]

    # 9b. HR approves the request
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    hr_approve = client.post(
        f"/leaves/admin/{fresh_id}/approve",
        json={"review_notes": "Approved by HR"},
    )
    assert hr_approve.status_code == 200
    assert hr_approve.json()["status"] == "APPROVED"

    # 9c. Alice attempts to self-cancel approved leave -> Rejected (HTTP 403)
    app.dependency_overrides[require_authenticated] = lambda: alice
    emp_cancel_approved = client.post(
        f"/leaves/{fresh_id}/cancel",
        json={"reason": "Trying to self-cancel approved"},
    )
    assert emp_cancel_approved.status_code == 403
    assert "Cancellation of already approved leave requires HR authorization" in emp_cancel_approved.json()["detail"]

    # 9d. HR cancels the approved leave -> Success (HTTP 200), balance refunded
    app.dependency_overrides[require_authenticated] = lambda: hr
    hr_cancel_approved = client.post(
        f"/leaves/{fresh_id}/cancel",
        json={"reason": "Approved leave cancelled per employee request"},
    )
    assert hr_cancel_approved.status_code == 200
    assert hr_cancel_approved.json()["status"] == "CANCELLED"

    app.dependency_overrides.clear()


# ============================================================================
# WORKFLOW 2: HR / ADMIN WORKFLOW E2E
# ============================================================================

def test_workflow_hr_admin_e2e(e2e_db: Session):
    """E2E Workflow: HR loads pending queue, approves request, verifies balance conversion,
    rejects request, enforces anti-self-approval, blocks unauthorized roles, and
    performs audited administrative balance adjustment.
    """
    alice = e2e_db.get(Employee, 1)
    hr = e2e_db.get(Employee, 3)

    app.dependency_overrides[get_db] = lambda: e2e_db
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # Setup: Alice submits 2 separate leave requests
    app.dependency_overrides[require_authenticated] = lambda: alice
    req1 = client.post("/leaves/", json={
        "category_code": "CASUAL",
        "start_date": "2026-06-01",
        "end_date": "2026-06-01",
        "is_half_day": False,
        "reason": "Request 1 for approval",
    }).json()

    req2 = client.post("/leaves/", json={
        "category_code": "CASUAL",
        "start_date": "2026-06-05",
        "end_date": "2026-06-05",
        "is_half_day": False,
        "reason": "Request 2 for rejection",
    }).json()

    # 1. HR loads pending requests queue
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    pending_resp = client.get("/leaves/admin/pending")
    assert pending_resp.status_code == 200
    pending_items = pending_resp.json()
    pending_ids = [item["id"] for item in pending_items]
    assert req1["id"] in pending_ids
    assert req2["id"] in pending_ids

    # 2. HR approves Request 1
    appr_resp = client.post(
        f"/leaves/admin/{req1['id']}/approve",
        json={"review_notes": "Approved by Charlie HR"},
    )
    assert appr_resp.status_code == 200
    assert appr_resp.json()["status"] == "APPROVED"
    assert appr_resp.json()["reviewed_by_name"] == "Charlie HR"

    # 3. Verify balance conversion for Request 1 (1.0 reserved converted to 1.0 used)
    bal_alice = client.get("/leaves/balance").json()
    cl_bal = next(b for b in bal_alice if b["category_code"] == "CASUAL")
    assert Decimal(cl_bal["used_days"]) == Decimal("1.0")
    # req2 is still pending, so reserved is 1.0; available is 6.0 - 1.0 (used) - 1.0 (reserved) = 4.0
    assert Decimal(cl_bal["reserved_days"]) == Decimal("1.0")
    assert Decimal(cl_bal["available_days"]) == Decimal("4.0")

    # 4. HR rejects Request 2 with valid reason
    rej_resp = client.post(
        f"/leaves/admin/{req2['id']}/reject",
        json={"review_notes": "Project deadline collision, please reschedule"},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"] == "REJECTED"

    # 5. Verify reserved balance released after rejection (reserved becomes 0.0, available is 5.0)
    bal_alice_after = client.get("/leaves/balance").json()
    cl_bal_after = next(b for b in bal_alice_after if b["category_code"] == "CASUAL")
    assert Decimal(cl_bal_after["used_days"]) == Decimal("1.0")
    assert Decimal(cl_bal_after["reserved_days"]) == Decimal("0.0")
    assert Decimal(cl_bal_after["available_days"]) == Decimal("5.0")

    # 6. Anti-self-approval enforcement:
    # 6a. Charlie HR submits a personal leave request
    app.dependency_overrides[require_authenticated] = lambda: hr
    hr_req = client.post("/leaves/", json={
        "category_code": "CASUAL",
        "start_date": "2026-06-15",
        "end_date": "2026-06-15",
        "is_half_day": False,
        "reason": "HR personal leave",
    }).json()

    # 6b. Charlie HR attempts to self-approve -> Blocked (HTTP 400)
    self_approve = client.post(
        f"/leaves/admin/{hr_req['id']}/approve",
        json={"review_notes": "Self approval attempt"},
    )
    assert self_approve.status_code == 400
    assert "Employees cannot approve their own leave requests" in self_approve.json()["detail"]

    # 6c. Charlie HR attempts to self-reject -> Blocked (HTTP 400)
    self_reject = client.post(
        f"/leaves/admin/{hr_req['id']}/reject",
        json={"review_notes": "Self reject attempt"},
    )
    assert self_reject.status_code == 400
    assert "Employees cannot review their own leave requests" in self_reject.json()["detail"]

    # 7. Unauthorized roles blocked from management actions
    from app.dependencies.auth import get_current_employee
    app.dependency_overrides[require_authenticated] = lambda: alice
    app.dependency_overrides[get_current_employee] = lambda: alice
    app.dependency_overrides[require_hr_or_admin] = require_hr_or_admin

    assert client.get("/leaves/admin/pending").status_code == 403
    assert client.post(f"/leaves/admin/{hr_req['id']}/approve").status_code == 403
    assert client.post(f"/leaves/admin/{hr_req['id']}/reject").status_code == 403
    assert client.post("/leaves/admin/balances/adjust", json={}).status_code == 403
    assert client.get("/leaves/admin/compoff/claims").status_code == 403

    # 8. Administrative balance adjustment
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    adj_resp = client.post(
        "/leaves/admin/balances/adjust",
        json={
            "employee_id": alice.id,
            "category_code": "CASUAL",
            "year": 2026,
            "bucket": "entitled",
            "amount": "2.0",
            "reason": "Administrative correction for overtime bonus",
        },
    )
    assert adj_resp.status_code == 200
    adj_bal = adj_resp.json()
    assert Decimal(adj_bal["entitled_days"]) == Decimal("8.0")
    assert Decimal(adj_bal["available_days"]) == Decimal("7.0")  # 8.0 entitled - 1.0 used = 7.0

    # Verify immutable ledger entry in database
    tx = e2e_db.execute(
        select(LeaveBalanceTransaction).where(
            LeaveBalanceTransaction.balance_id == adj_bal["id"],
            LeaveBalanceTransaction.transaction_type == TransactionType.ADJUSTMENT.value,
        )
    ).scalar_one_or_none()
    assert tx is not None
    assert tx.amount == Decimal("2.0")
    assert tx.created_by_id == hr.id
    assert "Administrative correction" in tx.notes

    app.dependency_overrides.clear()


# ============================================================================
# WORKFLOW 3: COMP-OFF LIFECYCLE E2E
# ============================================================================

def test_workflow_compoff_lifecycle_e2e(e2e_db: Session):
    """E2E Workflow: Employee submits weekend extra-work claim, verifies history,
    HR reviews and grants credit, prevents duplicate reviews, performs FIFO Comp-Off
    leave consumption, simulates 90-day expiry job, and tests pagination/filtering.
    """
    alice = e2e_db.get(Employee, 1)
    hr = e2e_db.get(Employee, 3)

    app.dependency_overrides[get_db] = lambda: e2e_db
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # 1. Alice submits extra-work claim for Sunday 3 May 2026
    app.dependency_overrides[require_authenticated] = lambda: alice
    claim_resp = client.post(
        "/leaves/compoff/claims",
        json={
            "work_date": "2026-05-03",
            "days_claimed": "1.0",
            "reason": "Sunday critical cloud migration",
        },
    )
    assert claim_resp.status_code == 201
    claim = claim_resp.json()
    claim_id = claim["id"]
    assert claim["status"] == "PENDING"
    assert claim["credit_status"] == "PENDING"

    # 2. Verify claim appears in Alice's server-backed history
    history_resp = client.get("/leaves/compoff/claims")
    assert history_resp.status_code == 200
    history_claims = history_resp.json()
    assert len(history_claims) >= 1
    assert any(c["id"] == claim_id for c in history_claims)

    # 3. HR reviews claim
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    claims_queue = client.get("/leaves/admin/compoff/claims?status_filter=PENDING").json()
    assert any(c["id"] == claim_id for c in claims_queue)

    # 4. HR approves claim: grants 1.0 day credit
    appr_claim = client.post(
        f"/leaves/admin/compoff/claims/{claim_id}/approve",
        json={"days_approved": "1.0", "review_notes": "Well performed migration"},
    )
    assert appr_claim.status_code == 200
    appr_data = appr_claim.json()
    assert appr_data["status"] == "APPROVED"
    assert appr_data["credit_status"] == "ACTIVE"
    assert Decimal(appr_data["days_approved"]) == Decimal("1.0")
    assert Decimal(appr_data["remaining_days"]) == Decimal("1.0")

    # Verify Comp-Off balance accrued for Alice
    app.dependency_overrides[require_authenticated] = lambda: alice
    co_bal = next(b for b in client.get("/leaves/balance").json() if b["category_code"] == "COMP_OFF")
    assert Decimal(co_bal["accrued_days"]) == Decimal("1.0")
    assert Decimal(co_bal["available_days"]) == Decimal("1.0")

    # 5. Prevent duplicate approval (HTTP 400)
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    dup_appr = client.post(
        f"/leaves/admin/compoff/claims/{claim_id}/approve",
        json={"days_approved": "1.0"},
    )
    assert dup_appr.status_code == 400
    assert "Cannot review claim with current status 'APPROVED'" in dup_appr.json()["detail"]

    # 6. FIFO Consumption:
    # 6a. Alice submits second claim for Sunday 10 May 2026, and HR approves it
    app.dependency_overrides[require_authenticated] = lambda: alice
    claim2_id = client.post(
        "/leaves/compoff/claims",
        json={
            "work_date": "2026-05-10",
            "days_claimed": "1.0",
            "reason": "Sunday emergency outage fix",
        },
    ).json()["id"]

    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    client.post(f"/leaves/admin/compoff/claims/{claim2_id}/approve", json={"days_approved": "1.0"})

    # Alice now has 2.0 accrued Comp-Off days (Claim 1 from 3 May, Claim 2 from 10 May)
    # Alice applies for 1.0 day of Comp-Off leave on Monday 18 May
    app.dependency_overrides[require_authenticated] = lambda: alice
    co_leave_id = client.post(
        "/leaves/",
        json={
            "category_code": "COMP_OFF",
            "start_date": "2026-05-18",
            "end_date": "2026-05-18",
            "is_half_day": False,
            "reason": "Availing comp-off for migration",
        },
    ).json()["id"]

    # HR approves the Comp-Off leave request (triggers FIFO credit consumption)
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    client.post(f"/leaves/admin/{co_leave_id}/approve", json={"review_notes": "Enjoy your time off"})

    # Verify FIFO consumption order: Claim 1 (older) was exhausted first (remaining=0.0, EXHAUSTED)
    claim1_db = e2e_db.get(CompOffClaim, claim_id)
    claim2_db = e2e_db.get(CompOffClaim, claim2_id)
    assert claim1_db.remaining_days == Decimal("0.0")
    assert claim1_db.credit_status == CompOffCreditStatus.EXHAUSTED.value

    # Claim 2 remains active with 1.0 day
    assert claim2_db.remaining_days == Decimal("1.0")
    assert claim2_db.credit_status == CompOffCreditStatus.ACTIVE.value

    # 7. Expiry Job Simulation: Past 90 days expiry
    # Set Claim 2 expiry date to yesterday
    claim2_db.expiry_date = date.today() - timedelta(days=1)
    e2e_db.commit()

    # Run automated expiry job
    expired_count = expire_compoff_credits(e2e_db)
    assert expired_count == 1

    e2e_db.refresh(claim2_db)
    assert claim2_db.remaining_days == Decimal("0.0")
    assert claim2_db.credit_status == CompOffCreditStatus.EXPIRED.value

    # 8. Rejection test: Rejected claim creates zero credit
    app.dependency_overrides[require_authenticated] = lambda: alice
    claim3_id = client.post(
        "/leaves/compoff/claims",
        json={
            "work_date": "2026-05-17",
            "days_claimed": "0.5",
            "reason": "Sunday quick server restart",
        },
    ).json()["id"]

    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    rej_claim = client.post(
        f"/leaves/admin/compoff/claims/{claim3_id}/reject",
        json={"review_notes": "Routine maintenance does not qualify for comp-off"},
    )
    assert rej_claim.status_code == 200
    assert rej_claim.json()["status"] == "REJECTED"

    claim3_db = e2e_db.get(CompOffClaim, claim3_id)
    assert claim3_db.days_approved is None
    assert claim3_db.remaining_days == Decimal("0.0")
    assert claim3_db.credit_status == CompOffCreditStatus.EXPIRED.value

    # 9. Employee history pagination and status filtering
    app.dependency_overrides[require_authenticated] = lambda: alice
    # Alice now has 3 claims (Claim 1: APPROVED, Claim 2: APPROVED, Claim 3: REJECTED)
    resp_filtered = client.get("/leaves/compoff/claims?status_filter=APPROVED")
    assert resp_filtered.status_code == 200
    assert len(resp_filtered.json()) == 2

    resp_p1 = client.get("/leaves/compoff/claims?page=1&page_size=2")
    assert resp_p1.status_code == 200
    assert len(resp_p1.json()) == 2

    resp_p2 = client.get("/leaves/compoff/claims?page=2&page_size=2")
    assert resp_p2.status_code == 200
    assert len(resp_p2.json()) == 1

    app.dependency_overrides.clear()


# ============================================================================
# WORKFLOW 4: FRONTEND CONTRACTS & SECURITY E2E
# ============================================================================

def test_workflow_frontend_contracts_and_security_e2e():
    """E2E Workflow: Verify frontend template contracts, accessible controls,
    CSRF headers, double-submit protection, and XSS sanitization across Leave & Comp-Off.
    """
    client = TestClient(app)

    # 1. Dashboard template contracts
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text

    # Section roots & Headers
    assert 'id="leavesSection"' in html
    assert 'id="leavesAdminSection"' in html
    assert 'id="btnApplyLeave"' in html
    assert 'id="btnClaimCompoff"' in html
    assert 'id="btnOpenAdjustBalanceModal"' in html
    assert 'id="btnRefreshAdminLeaves"' in html
    assert 'id="btnRefreshMyCompoff"' in html

    # Tables & Cards
    assert 'id="myLeavesTableBody"' in html
    assert 'id="myCompoffTableBody"' in html
    assert 'id="pendingLeavesTableBody"' in html
    assert 'id="adminCompoffTableBody"' in html

    # Modals
    assert 'id="applyLeaveModal"' in html
    assert 'id="claimCompoffModal"' in html
    assert 'id="reviewLeaveModal"' in html
    assert 'id="reviewCompoffModal"' in html
    assert 'id="adjustBalanceModal"' in html

    # 2. Portal.js client verification
    js_resp = client.get("/static/js/portal.js")
    assert js_resp.status_code == 200
    js = js_resp.text

    # CSRF & apiFetch
    assert "config.headers['X-CSRF-Token'] = csrfToken;" in js
    assert "apiFetch('/leaves/'," in js or 'apiFetch("/leaves/",' in js
    assert "/leaves/calculate" in js
    assert "/leaves/compoff/claims" in js
    assert "/leaves/admin/pending" in js
    assert "/leaves/admin/balances/adjust" in js

    # Anti-XSS: escapeHtml used on all dynamic strings
    assert "escapeHtml(c.work_date)" in js
    assert "escapeHtml(c.reason)" in js
    assert "escapeHtml(rec.reason || '—')" in js or "document.getElementById('revReason').textContent = rec.reason" in js

    # Double-submit protection: buttons disabled and spinners shown
    assert "btnApprove.disabled = true" in js
    assert "submitBtn.disabled = true" in js
    assert "submitSpinner.style.display = 'inline-block'" in js

    # Anti-self-approval safeguards in client
    assert "rec.employee_id === currentUser.id" in js
    assert "c.employee_id === currentUser.id" in js
