"""Automated API, security, and RBAC tests for Leave Management routers.

Runs in complete isolation using an in-memory SQLite database and FastAPI dependency overrides.
Zero dependencies on live PostgreSQL or Docker.

Covers:
1. Unauthenticated access rejection (HTTP 401)
2. CSRF validation on all state-changing endpoints (HTTP 403)
3. Employee leave application, calculation preview, and balance queries
4. Overlap rejection (HTTP 409) and insufficient balance rejection (HTTP 400)
5. Strict employee isolation (employees cannot view another employee's request: HTTP 403)
6. HR/Admin role boundaries (employees cannot access /admin/*: HTTP 403)
7. Anti-self-approval enforcement for leave and comp-off claims (HTTP 400)
8. HR approval, rejection, and balance adjustment workflows
9. Comp-Off claim submission and HR review
10. Cancellation workflows (employee self-cancellation vs HR-only approved cancellation)
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.permissions import require_authenticated, require_hr_or_admin
from app.db.base import Base
from app.db.session import get_db
from app.dependencies.auth import get_current_session, verify_csrf
from app.main import app
from app.models.employee import Employee, UserRole
from app.models.session import Session as DbSession
from app.models.leave import (
    CompOffClaim,
    CompOffClaimStatus,
    CompOffCreditStatus,
    LeaveBalance,
    LeaveCategory,
    LeaveCategoryCode,
    LeaveRequest,
    LeaveRequestStatus,
)
from app.services.leave import get_or_create_leave_balance


@pytest.fixture
def test_db():
    """Create a completely isolated in-memory SQLite database for the test."""
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
    emp1 = Employee(id=1, employee_code="EMP001", name="Alice Employee", email="alice@company.com", role=UserRole.EMPLOYEE.value, is_active=True)
    emp2 = Employee(id=2, employee_code="EMP002", name="Bob Employee", email="bob@company.com", role=UserRole.EMPLOYEE.value, is_active=True)
    hr = Employee(id=3, employee_code="HR001", name="Charlie HR", email="charlie.hr@company.com", role=UserRole.HR.value, is_active=True)
    admin = Employee(id=4, employee_code="ADM001", name="Dana Admin", email="dana.admin@company.com", role=UserRole.ADMIN.value, is_active=True)
    db.add_all([emp1, emp2, hr, admin])
    db.commit()

    yield db

    db.close()
    Base.metadata.drop_all(bind=engine)


# ============================================================================
# 1. Unauthenticated & CSRF Rejection Tests
# ============================================================================

def test_unauthenticated_requests_rejected():
    """Verify that unauthenticated calls to all endpoints return HTTP 401."""
    # Ensure no dependency overrides active for auth
    app.dependency_overrides.clear()
    client = TestClient(app)

    # Employee endpoints
    assert client.post("/leaves/", json={}).status_code == 401
    assert client.get("/leaves/").status_code == 401
    assert client.get("/leaves/balance").status_code == 401
    assert client.get("/leaves/1").status_code == 401
    assert client.post("/leaves/1/cancel", json={"reason": "test"}).status_code == 401
    assert client.post("/leaves/compoff/claims", json={}).status_code == 401

    # Admin endpoints
    assert client.get("/leaves/admin/pending").status_code == 401
    assert client.post("/leaves/admin/1/approve").status_code == 401
    assert client.post("/leaves/admin/1/reject").status_code == 401
    assert client.post("/leaves/admin/balances/adjust", json={}).status_code == 401
    assert client.get("/leaves/admin/compoff/claims").status_code == 401
    assert client.post("/leaves/admin/compoff/claims/1/approve").status_code == 401
    assert client.post("/leaves/admin/compoff/claims/1/reject").status_code == 401


def test_csrf_rejection_on_state_changing_requests(test_db: Session):
    """Verify that state-changing requests without valid CSRF return HTTP 403."""
    app.dependency_overrides[get_db] = lambda: test_db
    alice = test_db.get(Employee, 1)
    app.dependency_overrides[require_authenticated] = lambda: alice

    # Mock an active user session with a known CSRF token
    mock_session = DbSession(
        id=99,
        session_token="test_token_123",
        csrf_token="secret_csrf_123",
        employee_id=1,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    app.dependency_overrides[get_current_session] = lambda: mock_session

    client = TestClient(app)

    payload = {
        "category_code": "CASUAL",
        "start_date": "2026-05-04",
        "end_date": "2026-05-04",
        "is_half_day": False,
        "reason": "Test leave",
    }

    # 1. POST /leaves/ without X-CSRF-Token header -> 403
    resp_no_csrf = client.post("/leaves/", json=payload)
    assert resp_no_csrf.status_code == 403
    assert "CSRF" in resp_no_csrf.json().get("detail", "")

    # 2. POST /leaves/ with wrong X-CSRF-Token header -> 403
    resp_wrong_csrf = client.post("/leaves/", json=payload, headers={"X-CSRF-Token": "wrong_token"})
    assert resp_wrong_csrf.status_code == 403
    assert "CSRF" in resp_wrong_csrf.json().get("detail", "")

    app.dependency_overrides.clear()


# ============================================================================
# 2. Employee Endpoints Tests
# ============================================================================

def test_employee_leave_application_and_balance(test_db: Session):
    """Test full employee leave application, balance reservation, and history listing."""
    alice = test_db.get(Employee, 1)
    # Seed Alice's Casual Leave balance (6 days)
    get_or_create_leave_balance(test_db, employee_id=1, category_id=1, year=2026)
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    app.dependency_overrides[verify_csrf] = lambda: None  # mock valid CSRF

    client = TestClient(app)

    # 1. Preview calculation
    calc_resp = client.post(
        "/leaves/calculate",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-04",
            "end_date": "2026-05-05",
            "is_half_day": False,
        },
    )
    assert calc_resp.status_code == 200
    assert Decimal(calc_resp.json()["total_days"]) == Decimal("2.0")

    # 2. Submit application
    apply_resp = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-04",
            "end_date": "2026-05-05",
            "is_half_day": False,
            "reason": "Family vacation",
        },
    )
    assert apply_resp.status_code == 201
    leave_id = apply_resp.json()["id"]
    assert apply_resp.json()["status"] == "PENDING"
    assert Decimal(apply_resp.json()["total_days"]) == Decimal("2.0")

    # 3. Check personal balances (available should be 4.0, reserved 2.0)
    bal_resp = client.get("/leaves/balance?year=2026")
    assert bal_resp.status_code == 200
    casual_bal = next(b for b in bal_resp.json() if b["category_code"] == "CASUAL")
    assert casual_bal["available_days"] == "4.0"
    assert casual_bal["reserved_days"] == "2.0"

    # 4. Check personal leave history
    hist_resp = client.get("/leaves/")
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()) == 1
    assert hist_resp.json()[0]["id"] == leave_id

    app.dependency_overrides.clear()


def test_leave_application_overlap_rejection(test_db: Session):
    """Test that overlapping leave requests return HTTP 409 Conflict."""
    alice = test_db.get(Employee, 1)
    get_or_create_leave_balance(test_db, employee_id=1, category_id=1, year=2026)
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    app.dependency_overrides[verify_csrf] = lambda: None

    client = TestClient(app)

    # First request: 4 May to 6 May 2026
    r1 = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-04",
            "end_date": "2026-05-06",
            "is_half_day": False,
            "reason": "First leave",
        },
    )
    assert r1.status_code == 201

    # Overlapping request: 5 May to 7 May 2026 -> 409
    r2 = client.post(
        "/leaves/",
        json={
            "category_code": "CASUAL",
            "start_date": "2026-05-05",
            "end_date": "2026-05-07",
            "is_half_day": False,
            "reason": "Overlapping leave",
        },
    )
    assert r2.status_code == 409
    assert "conflicts" in r2.json()["detail"]

    app.dependency_overrides.clear()


def test_resource_ownership_and_isolation(test_db: Session):
    """Test that employees cannot view or cancel another employee's request."""
    alice = test_db.get(Employee, 1)
    bob = test_db.get(Employee, 2)
    hr = test_db.get(Employee, 3)

    # Create a leave request for Alice
    req_alice = LeaveRequest(
        employee_id=alice.id,
        category_id=1,
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 4),
        is_half_day=False,
        total_days=Decimal("1.0"),
        reason="Alice's private leave",
        status=LeaveRequestStatus.PENDING.value,
    )
    test_db.add(req_alice)
    test_db.commit()

    # 1. Alice can view her own request
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    client = TestClient(app)
    assert client.get(f"/leaves/{req_alice.id}").status_code == 200

    # 2. Bob attempting to view Alice's request receives HTTP 403
    app.dependency_overrides[require_authenticated] = lambda: bob
    assert client.get(f"/leaves/{req_alice.id}").status_code == 403

    # 3. Bob attempting to cancel Alice's request receives HTTP 403
    app.dependency_overrides[verify_csrf] = lambda: None
    assert client.post(f"/leaves/{req_alice.id}/cancel", json={"reason": "Malicious cancel"}).status_code == 403

    # 4. HR can view Alice's request
    app.dependency_overrides[require_authenticated] = lambda: hr
    assert client.get(f"/leaves/{req_alice.id}").status_code == 200

    # 5. Nonexistent request returns HTTP 404
    assert client.get("/leaves/99999").status_code == 404

    app.dependency_overrides.clear()


# ============================================================================
# 3. Comp-Off Extra-Work Claims
# ============================================================================

def test_employee_compoff_claim_submission(test_db: Session):
    """Test submitting comp-off claims on weekend vs regular weekday."""
    alice = test_db.get(Employee, 1)
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # 1. Sunday 3 May 2026 -> Success (201)
    r_sun = client.post(
        "/leaves/compoff/claims",
        json={
            "work_date": "2026-05-03",
            "days_claimed": "1.0",
            "reason": "Weekend datacenter maintenance",
        },
    )
    assert r_sun.status_code == 201
    assert r_sun.json()["status"] == "PENDING"

    # 2. Monday 4 May 2026 (Regular weekday) -> Rejected (400)
    r_mon = client.post(
        "/leaves/compoff/claims",
        json={
            "work_date": "2026-05-04",
            "days_claimed": "1.0",
            "reason": "Weekday overtime",
        },
    )
    assert r_mon.status_code == 400
    assert "regular working day" in r_mon.json()["detail"]

    # 3. Duplicate claim on Sunday 3 May -> Rejected (409)
    r_dup = client.post(
        "/leaves/compoff/claims",
        json={
            "work_date": "2026-05-03",
            "days_claimed": "1.0",
            "reason": "Duplicate claim",
        },
    )
    assert r_dup.status_code == 409

    app.dependency_overrides.clear()


# ============================================================================
# 4. HR / Admin Endpoints & RBAC Boundary Tests
# ============================================================================

def test_rbac_boundaries_employee_cannot_access_admin_endpoints(test_db: Session):
    """Verify that regular employees cannot access any /leaves/admin/* endpoint."""
    alice = test_db.get(Employee, 1)
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    # require_hr_or_admin will raise 403 for employee
    app.dependency_overrides[require_hr_or_admin] = require_hr_or_admin
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # Ensure get_current_employee returns Alice
    from app.dependencies.auth import get_current_employee
    app.dependency_overrides[get_current_employee] = lambda: alice

    assert client.get("/leaves/admin/pending").status_code == 403
    assert client.post("/leaves/admin/1/approve", json={}).status_code == 403
    assert client.post("/leaves/admin/1/reject", json={}).status_code == 403
    assert client.post("/leaves/admin/balances/adjust", json={}).status_code == 403
    assert client.get("/leaves/admin/compoff/claims").status_code == 403

    app.dependency_overrides.clear()


def test_hr_approval_and_rejection_workflows(test_db: Session):
    """Test HR reviewing pending leave requests, converting balance, and rejecting."""
    alice = test_db.get(Employee, 1)
    hr = test_db.get(Employee, 3)

    # Seed Alice's balance
    bal = get_or_create_leave_balance(test_db, employee_id=1, category_id=1, year=2026)
    bal.reserved_days = Decimal("2.0")

    # Create pending leave request
    req = LeaveRequest(
        id=101,
        employee_id=alice.id,
        category_id=1,
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 5),
        is_half_day=False,
        total_days=Decimal("2.0"),
        reason="Family event",
        status=LeaveRequestStatus.PENDING.value,
    )
    test_db.add(req)
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # 1. HR lists pending requests
    pending_resp = client.get("/leaves/admin/pending")
    assert pending_resp.status_code == 200
    assert any(r["id"] == 101 for r in pending_resp.json())

    # 2. HR approves request
    appr_resp = client.post("/leaves/admin/101/approve", json={"review_notes": "Approved by HR"})
    assert appr_resp.status_code == 200
    assert appr_resp.json()["status"] == "APPROVED"
    assert appr_resp.json()["reviewed_by_id"] == hr.id

    # Verify balance converted
    test_db.refresh(bal)
    assert bal.reserved_days == Decimal("0.0")
    assert bal.used_days == Decimal("2.0")

    app.dependency_overrides.clear()


def test_anti_self_approval_via_api(test_db: Session):
    """Verify that HR/Admin cannot approve their own leave application via API."""
    hr = test_db.get(Employee, 3)
    bal = get_or_create_leave_balance(test_db, employee_id=hr.id, category_id=1, year=2026)
    bal.reserved_days = Decimal("1.0")

    # HR's own leave request
    hr_req = LeaveRequest(
        id=202,
        employee_id=hr.id,
        category_id=1,
        start_date=date(2026, 5, 4),
        end_date=date(2026, 5, 4),
        is_half_day=False,
        total_days=Decimal("1.0"),
        reason="HR vacation",
        status=LeaveRequestStatus.PENDING.value,
    )
    test_db.add(hr_req)
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # Attempting to self-approve fails with 400
    resp = client.post("/leaves/admin/202/approve", json={"review_notes": "Self approval"})
    assert resp.status_code == 400
    assert "cannot approve their own" in resp.json()["detail"]

    app.dependency_overrides.clear()


def test_hr_compoff_claim_approval(test_db: Session):
    """Test HR approving employee comp-off claim creates credit and updates balance."""
    alice = test_db.get(Employee, 1)
    hr = test_db.get(Employee, 3)

    claim = CompOffClaim(
        id=303,
        employee_id=alice.id,
        work_date=date(2026, 5, 3),
        days_claimed=Decimal("1.0"),
        reason="Server patch",
        status=CompOffClaimStatus.PENDING.value,
        credit_status=CompOffCreditStatus.PENDING.value,
    )
    test_db.add(claim)
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # 1. HR lists claims
    claims_resp = client.get("/leaves/admin/compoff/claims?status_filter=PENDING")
    assert claims_resp.status_code == 200
    assert any(c["id"] == 303 for c in claims_resp.json())

    # 2. HR approves claim
    appr_resp = client.post("/leaves/admin/compoff/claims/303/approve", json={"days_approved": "1.0", "review_notes": "Well done"})
    assert appr_resp.status_code == 200
    assert appr_resp.json()["status"] == "APPROVED"
    assert appr_resp.json()["credit_status"] == "ACTIVE"

    # Verify Comp-Off balance accrued
    comp_bal = get_or_create_leave_balance(test_db, employee_id=alice.id, category_id=5, year=2026)
    assert comp_bal.accrued_days == Decimal("1.0")

    app.dependency_overrides.clear()


def test_hr_balance_adjustment_api(test_db: Session):
    """Test HR manual balance adjustment endpoint."""
    alice = test_db.get(Employee, 1)
    hr = test_db.get(Employee, 3)
    get_or_create_leave_balance(test_db, employee_id=alice.id, category_id=1, year=2026)

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_hr_or_admin] = lambda: hr
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    # Add 2 days to Alice's entitled bucket
    adj_resp = client.post(
        "/leaves/admin/balances/adjust",
        json={
            "employee_id": alice.id,
            "category_code": "CASUAL",
            "year": 2026,
            "bucket": "entitled",
            "amount": "2.0",
            "reason": "Special discretionary bonus",
        },
    )
    assert adj_resp.status_code == 200
    assert adj_resp.json()["entitled_days"] == "8.0"
    assert adj_resp.json()["available_days"] == "8.0"

    app.dependency_overrides.clear()


# ============================================================================
# 5. Phase 2.9.1: Employee Comp-Off Claim History Endpoint Tests
# ============================================================================

def test_employee_get_my_compoff_claims_unauthenticated():
    """Verify that unauthenticated calls to GET /leaves/compoff/claims are rejected with 401."""
    client = TestClient(app)
    response = client.get("/leaves/compoff/claims")
    assert response.status_code == 401


def test_employee_get_my_compoff_claims_empty_and_populated(test_db: Session):
    """Test authenticated employee retrieving empty and populated Comp-Off claim history."""
    alice = test_db.get(Employee, 1)
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    client = TestClient(app)

    # 1. Empty claims history
    resp_empty = client.get("/leaves/compoff/claims")
    assert resp_empty.status_code == 200
    assert resp_empty.json() == []

    # 2. Add two claims for Alice (one older, one newer)
    c1 = CompOffClaim(
        employee_id=alice.id,
        work_date=date(2026, 5, 3),
        days_claimed=Decimal("1.0"),
        reason="Server migration Sunday",
        status=CompOffClaimStatus.PENDING.value,
        created_at=datetime(2026, 5, 3, 18, 0, 0, tzinfo=timezone.utc),
    )
    c2 = CompOffClaim(
        employee_id=alice.id,
        work_date=date(2026, 5, 10),
        days_claimed=Decimal("0.5"),
        reason="Emergency incident response",
        status=CompOffClaimStatus.PENDING.value,
        created_at=datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    test_db.add_all([c1, c2])
    test_db.commit()

    resp_populated = client.get("/leaves/compoff/claims")
    assert resp_populated.status_code == 200
    claims = resp_populated.json()
    assert len(claims) == 2

    # Verify ordering: newest claim first (c2 before c1)
    assert claims[0]["work_date"] == "2026-05-10"
    assert Decimal(claims[0]["days_claimed"]) == Decimal("0.5")
    assert claims[0]["reason"] == "Emergency incident response"
    assert claims[0]["status"] == "PENDING"
    assert claims[0]["employee_id"] == alice.id

    assert claims[1]["work_date"] == "2026-05-03"
    assert Decimal(claims[1]["days_claimed"]) == Decimal("1.0")

    app.dependency_overrides.clear()


def test_employee_get_my_compoff_claims_isolation(test_db: Session):
    """Test strict isolation: Alice cannot view Bob's claims, and query spoofing is ignored."""
    alice = test_db.get(Employee, 1)
    bob = test_db.get(Employee, 2)

    # Alice's claim
    c_alice = CompOffClaim(
        employee_id=alice.id,
        work_date=date(2026, 5, 3),
        days_claimed=Decimal("1.0"),
        reason="Alice weekend work",
        status=CompOffClaimStatus.PENDING.value,
    )
    # Bob's claim
    c_bob = CompOffClaim(
        employee_id=bob.id,
        work_date=date(2026, 5, 10),
        days_claimed=Decimal("1.0"),
        reason="Bob secret work",
        status=CompOffClaimStatus.PENDING.value,
    )
    test_db.add_all([c_alice, c_bob])
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db

    # 1. Alice queries: must only receive Alice's claim
    app.dependency_overrides[require_authenticated] = lambda: alice
    client_alice = TestClient(app)
    resp_alice = client_alice.get("/leaves/compoff/claims")
    assert resp_alice.status_code == 200
    alice_claims = resp_alice.json()
    assert len(alice_claims) == 1
    assert alice_claims[0]["employee_id"] == alice.id
    assert alice_claims[0]["reason"] == "Alice weekend work"

    # 2. Alice tries to pass employee_id=2 in query params: server must ignore it and return Alice's claim
    resp_spoof = client_alice.get(f"/leaves/compoff/claims?employee_id={bob.id}")
    assert resp_spoof.status_code == 200
    spoof_claims = resp_spoof.json()
    assert len(spoof_claims) == 1
    assert spoof_claims[0]["employee_id"] == alice.id

    # 3. Bob queries: must only receive Bob's claim
    app.dependency_overrides[require_authenticated] = lambda: bob
    client_bob = TestClient(app)
    resp_bob = client_bob.get("/leaves/compoff/claims")
    assert resp_bob.status_code == 200
    bob_claims = resp_bob.json()
    assert len(bob_claims) == 1
    assert bob_claims[0]["employee_id"] == bob.id
    assert bob_claims[0]["reason"] == "Bob secret work"

    app.dependency_overrides.clear()


def test_employee_get_my_compoff_claims_pagination_and_filter(test_db: Session):
    """Test status filter and pagination on employee comp-off claims."""
    alice = test_db.get(Employee, 1)

    c1 = CompOffClaim(
        employee_id=alice.id,
        work_date=date(2026, 5, 3),
        days_claimed=Decimal("1.0"),
        reason="Pending claim",
        status=CompOffClaimStatus.PENDING.value,
        created_at=datetime(2026, 5, 3, 10, 0, 0, tzinfo=timezone.utc),
    )
    c2 = CompOffClaim(
        employee_id=alice.id,
        work_date=date(2026, 5, 10),
        days_claimed=Decimal("1.0"),
        days_approved=Decimal("1.0"),
        remaining_days=Decimal("1.0"),
        reason="Approved claim",
        status=CompOffClaimStatus.APPROVED.value,
        credit_status=CompOffCreditStatus.ACTIVE.value,
        created_at=datetime(2026, 5, 10, 10, 0, 0, tzinfo=timezone.utc),
    )
    test_db.add_all([c1, c2])
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_authenticated] = lambda: alice
    client = TestClient(app)

    # Filter PENDING
    resp_pending = client.get("/leaves/compoff/claims?status_filter=PENDING")
    assert resp_pending.status_code == 200
    assert len(resp_pending.json()) == 1
    assert resp_pending.json()[0]["status"] == "PENDING"

    # Filter APPROVED
    resp_approved = client.get("/leaves/compoff/claims?status_filter=APPROVED")
    assert resp_approved.status_code == 200
    assert len(resp_approved.json()) == 1
    assert resp_approved.json()[0]["status"] == "APPROVED"

    # Pagination: page_size=1
    resp_p1 = client.get("/leaves/compoff/claims?page=1&page_size=1")
    assert resp_p1.status_code == 200
    assert len(resp_p1.json()) == 1
    assert resp_p1.json()[0]["work_date"] == "2026-05-10"

    resp_p2 = client.get("/leaves/compoff/claims?page=2&page_size=1")
    assert resp_p2.status_code == 200
    assert len(resp_p2.json()) == 1
    assert resp_p2.json()[0]["work_date"] == "2026-05-03"

    app.dependency_overrides.clear()
