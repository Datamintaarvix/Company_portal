"""Automated integration tests for Phase 2.8.2 Leave Management frontend foundation.

Verifies section presence, semantic HTML, accessible IDs, balance card placeholders,
table columns, CSS styling, and role-based navigation hooks in static assets.
"""

import re
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_dashboard_renders_leave_sections():
    """Verify that GET /dashboard renders both employee and HR/Admin leave sections."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Section roots
    assert 'id="leavesSection"' in html
    assert 'id="leavesAdminSection"' in html

    # Accessible section headings
    assert 'id="leaveSectionHeading"' in html
    assert "Leave Management" in html
    assert 'aria-labelledby="leaveSectionHeading"' in html

    assert 'id="leaveAdminHeading"' in html
    assert "Pending Leave Approvals" in html
    assert 'aria-labelledby="leaveAdminHeading"' in html


def test_leave_section_balance_cards():
    """Verify that the 4 balance-card placeholders (CL, SL, EL, Comp-Off) are rendered."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # 4 Card Containers
    assert 'id="cardCL"' in html
    assert 'id="cardSL"' in html
    assert 'id="cardEL"' in html
    assert 'id="cardCO"' in html

    # Category Titles and Codes
    assert "Casual Leave" in html
    assert "CL" in html
    assert "Sick Leave" in html
    assert "SL" in html
    assert "Earned Leave" in html
    assert "EL" in html
    assert "Comp-Off" in html
    assert "CO" in html

    # Value Placeholders
    assert 'id="balValCL"' in html
    assert 'id="balValSL"' in html
    assert 'id="balValEL"' in html
    assert 'id="balValCO"' in html

    # Metadata Placeholders (Used and Reserved)
    assert 'id="usedValCL"' in html
    assert 'id="resValCL"' in html
    assert 'id="usedValSL"' in html
    assert 'id="resValSL"' in html
    assert 'id="usedValEL"' in html
    assert 'id="resValEL"' in html
    assert 'id="usedValCO"' in html
    assert 'id="resValCO"' in html


def test_leave_section_apply_button_and_table():
    """Verify employee leave actions and requests table structure."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Action button
    assert 'id="btnApplyLeave"' in html
    assert "Apply for Leave" in html
    assert 'aria-label="Apply for time off"' in html

    # Table columns: Category, Dates, Days, Applied On, Status, Actions
    assert "My Leave Requests" in html
    assert 'aria-label="My Leave Requests History"' in html
    table_slice = html[html.find('aria-label="My Leave Requests History"'):]
    header_slice = table_slice[:table_slice.find('</thead>')]
    assert "Category" in header_slice
    assert "Dates" in header_slice
    assert "Days" in header_slice
    assert "Applied On" in header_slice
    assert "Status" in header_slice
    assert "Actions" in header_slice

    # Empty/loading state tbody
    assert 'id="myLeavesTableBody"' in html
    assert "No leave requests found" in html

    # Pagination controls
    assert 'id="myLeavesPagination"' in html
    assert 'id="myLeavesPrevBtn"' in html
    assert 'id="myLeavesNextBtn"' in html


def test_leave_admin_section_table():
    """Verify HR/Admin pending approvals table structure."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    assert 'aria-label="Pending Leave Approvals"' in html
    table_slice = html[html.find('aria-label="Pending Leave Approvals"'):]
    header_slice = table_slice[:table_slice.find('</thead>')]
    assert "Employee" in header_slice
    assert "Category" in header_slice
    assert "Dates" in header_slice
    assert "Days" in header_slice
    assert "Applied On" in header_slice
    assert "Actions" in header_slice

    # Empty/loading state tbody
    assert 'id="pendingLeavesTableBody"' in html
    assert "No pending leave requests awaiting approval" in html

    # Pagination controls
    assert 'id="pendingLeavesPagination"' in html
    assert 'id="pendingLeavesPrevBtn"' in html
    assert 'id="pendingLeavesNextBtn"' in html


def test_portal_css_contains_leave_foundation_styles():
    """Verify that portal.css includes leave balance cards and status pill classes."""
    response = client.get("/static/css/portal.css")
    assert response.status_code == 200
    css = response.text

    # Grid & Cards
    assert ".leave-balance-grid" in css
    assert ".balance-card" in css
    assert ".balance-card-header" in css
    assert ".balance-card-title" in css
    assert ".balance-card-code" in css
    assert ".balance-card-value" in css
    assert ".balance-card-unit" in css
    assert ".balance-card-meta" in css

    # Leave status pills
    assert ".status-pending" in css
    assert ".status-approved" in css
    assert ".status-rejected" in css
    assert ".status-cancelled" in css


def test_portal_js_role_based_navigation():
    """Verify portal.js contains role-based navigation hooks for My Leaves and Leave Approvals."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Target attributes and titles
    assert 'data-target="leavesSection"' in js
    assert "My Leaves" in js
    assert 'data-target="leavesAdminSection"' in js
    assert "Leave Approvals" in js

    # Role gating verification:
    # 1. leavesSection is defined in personal nav block (all roles)
    personal_block = js[js.find('// Employee Navigation'):js.find('// HR Navigation')]
    assert 'data-target="leavesSection"' in personal_block
    assert 'data-target="leavesAdminSection"' not in personal_block

    # 2. leavesAdminSection is defined inside HR navigation check
    hr_block = js[js.find('// HR Navigation'):js.find('// Admin Navigation')]
    assert "role === 'hr' || role === 'admin'" in hr_block
    assert 'data-target="leavesAdminSection"' in hr_block


def test_security_no_inline_event_handlers_in_leave_sections():
    """Verify that no inline JS handlers (e.g. onclick) or script tags are inside the leave sections."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Extract leavesSection
    start_emp = html.find('id="leavesSection"')
    end_emp = html.find('</section>', start_emp)
    emp_section_html = html[start_emp:end_emp]

    assert "onclick" not in emp_section_html.lower()
    assert "onload" not in emp_section_html.lower()
    assert "<script" not in emp_section_html.lower()

    # Extract leavesAdminSection
    start_adm = html.find('id="leavesAdminSection"')
    end_adm = html.find('</section>', start_adm)
    adm_section_html = html[start_adm:end_adm]

    assert "onclick" not in adm_section_html.lower()
    assert "onload" not in adm_section_html.lower()
    assert "<script" not in adm_section_html.lower()


# ============================================================================
# Phase 2.8.3 Tests: Employee Leave Dashboard API Integration
# ============================================================================

def test_apply_leave_modal_elements_and_accessibility():
    """Verify that dashboard.html renders the apply leave modal with all required controls."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Modal container and accessibility attributes
    assert 'id="applyLeaveModal"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="applyLeaveModalTitle"' in html
    assert 'id="applyLeaveModalTitle"' in html

    # Alert container
    assert 'id="applyLeaveAlert"' in html

    # Form controls
    assert 'id="applyLeaveForm"' in html
    assert 'id="leaveCategorySelect"' in html
    assert 'value="CASUAL"' in html
    assert 'value="SICK"' in html
    assert 'value="EARNED"' in html
    assert 'value="COMP_OFF"' in html
    assert 'value="UNPAID"' in html
    assert 'value="SABBATICAL"' in html

    # Date pickers
    assert 'id="leaveStartDate"' in html
    assert 'id="leaveEndDate"' in html

    # Half-day controls
    assert 'id="leaveIsHalfDay"' in html
    assert 'id="halfDayPeriodGroup"' in html
    assert 'id="leaveHalfDayPeriod"' in html
    assert 'value="morning"' in html
    assert 'value="afternoon"' in html

    # Reason and Preview
    assert 'id="leaveReason"' in html
    assert 'id="leavePreviewContainer"' in html

    # Action buttons
    assert 'id="closeApplyLeaveModalBtn"' in html
    assert 'id="btnCancelApplyLeave"' in html
    assert 'id="btnSubmitLeave"' in html

    # Security: No inline handlers or scripts inside modal
    start_modal = html.find('id="applyLeaveModal"')
    end_modal = html.find('</form>', start_modal)
    modal_slice = html[start_modal:end_modal]
    assert "onclick" not in modal_slice.lower()
    assert "<script" not in modal_slice.lower()


def test_portal_css_contains_calculation_preview_styles():
    """Verify portal.css contains classes for calculation preview box and breakdown notes."""
    response = client.get("/static/css/portal.css")
    assert response.status_code == 200
    css = response.text

    assert ".leave-calc-box" in css
    assert ".leave-calc-title" in css
    assert ".leave-calc-metric" in css
    assert ".leave-calc-details" in css
    assert ".leave-calc-notes" in css


def test_portal_js_contains_leave_api_functions_and_controllers():
    """Verify portal.js defines leave balance, history, preview, and submission controllers."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Core controller functions
    assert "loadLeaveBalances" in js
    assert "renderLeaveBalances" in js
    assert "renderLeaveBalancesError" in js
    assert "loadMyLeavesHistory" in js
    assert "handleCancelLeave" in js
    assert "debouncePreviewCalculation" in js
    assert "setupLeaveControllers" in js

    # Called on DOMContentLoaded
    assert "setupLeaveControllers()" in js


def test_portal_js_uses_actual_api_endpoints_and_methods():
    """Verify portal.js dispatches to the exact Phase 2.7 REST API endpoints."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # 1. Balance endpoint
    assert "apiFetch('/leaves/balance')" in js or 'apiFetch("/leaves/balance")' in js

    # 2. History endpoint
    assert "/leaves/?" in js

    # 3. Calculation preview endpoint
    assert "/leaves/calculate" in js

    # 4. Submission endpoint
    assert "apiFetch('/leaves/'" in js or 'apiFetch("/leaves/"' in js

    # 5. Cancellation endpoint
    assert "/leaves/${leaveId}/cancel" in js


def test_portal_js_uses_actual_pydantic_schema_fields():
    """Verify portal.js uses the exact backend schema field names."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # LeaveBalanceResponse fields
    assert "available_days" in js
    assert "used_days" in js
    assert "reserved_days" in js
    assert "category_code" in js

    # LeaveApplyRequest fields
    assert "start_date" in js
    assert "end_date" in js
    assert "is_half_day" in js
    assert "half_day_period" in js
    assert "reason" in js

    # LeaveCalculationResponse fields
    assert "total_days" in js
    assert "calendar_days" in js
    assert "working_days" in js
    assert "sandwiched_weekend_days" in js
    assert "sandwiched_holiday_days" in js
    assert "breakdown_notes" in js


def test_portal_js_concurrency_race_condition_and_confirmation_safeguards():
    """Verify portal.js implements monotonic preview request tracking and cancellation prompt."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Out-of-order preview response rejection
    assert "activeCalculationSeq" in js
    assert "currentSeq !== activeCalculationSeq" in js

    # Confirmation before cancellation
    assert "window.confirm" in js
    assert "window.prompt" in js

    # Validation guard prevents submission if calculation is invalid
    assert "isCurrentCalculationValid" in js


# ============================================================================
# Phase 2.8.4 Tests: HR/Admin Leave Management UI & API Integration
# ============================================================================

def test_admin_modals_elements_and_accessibility():
    """Verify that dashboard.html renders the 3 HR/Admin management modals with accessibility attributes."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # 1. Review Leave Modal
    assert 'id="reviewLeaveModal"' in html
    assert 'aria-labelledby="reviewLeaveModalTitle"' in html
    assert 'id="reviewLeaveModalTitle"' in html
    assert 'id="revLeaveId"' in html
    assert 'id="revLeaveEmpId"' in html
    assert 'id="selfApprovalWarning"' in html
    assert 'id="revEmpName"' in html
    assert 'id="revEmpCode"' in html
    assert 'id="revCategory"' in html
    assert 'id="revDates"' in html
    assert 'id="revTotalDays"' in html
    assert 'id="revReason"' in html
    assert 'id="reviewNotesInput"' in html
    assert 'id="btnApproveLeave"' in html
    assert 'id="btnRejectLeave"' in html
    assert 'id="closeReviewLeaveModalBtn"' in html
    assert 'id="btnCancelReviewLeave"' in html

    # 2. Review Comp-Off Modal
    assert 'id="reviewCompoffModal"' in html
    assert 'aria-labelledby="reviewCompoffModalTitle"' in html
    assert 'id="reviewCompoffModalTitle"' in html
    assert 'id="revClaimId"' in html
    assert 'id="revClaimEmpId"' in html
    assert 'id="coSelfApprovalWarning"' in html
    assert 'id="coRevEmpName"' in html
    assert 'id="coRevEmpCode"' in html
    assert 'id="coRevWorkDate"' in html
    assert 'id="coRevDaysClaimed"' in html
    assert 'id="coRevReason"' in html
    assert 'id="coDaysApprovedSelect"' in html
    assert 'id="coReviewNotesInput"' in html
    assert 'id="btnApproveCompoff"' in html
    assert 'id="btnRejectCompoff"' in html
    assert 'id="closeReviewCompoffModalBtn"' in html
    assert 'id="btnCancelReviewCompoff"' in html

    # 3. Adjust Balance Modal
    assert 'id="adjustBalanceModal"' in html
    assert 'aria-labelledby="adjustBalanceModalTitle"' in html
    assert 'id="adjustBalanceModalTitle"' in html
    assert 'id="adjustBalanceForm"' in html
    assert 'id="adjEmployeeSelect"' in html
    assert 'id="adjCategorySelect"' in html
    assert 'id="adjBucketSelect"' in html
    assert 'value="entitled"' in html
    assert 'value="carried_forward"' in html
    assert 'value="accrued"' in html
    assert 'id="adjYearInput"' in html
    assert 'id="adjAmountInput"' in html
    assert 'id="adjReasonInput"' in html
    assert 'id="btnSubmitAdjustBalance"' in html
    assert 'id="closeAdjustBalanceModalBtn"' in html
    assert 'id="btnCancelAdjustBalance"' in html

    # Security: No inline scripts or onclicks in any of the 3 admin modals
    for modal_id in ("reviewLeaveModal", "reviewCompoffModal", "adjustBalanceModal"):
        start_idx = html.find(f'id="{modal_id}"')
        end_idx = html.find('</div>\n  </div>', start_idx)
        modal_block = html[start_idx:end_idx]
        assert "onclick" not in modal_block.lower()
        assert "<script" not in modal_block.lower()


def test_admin_leave_queues_and_buttons():
    """Verify leavesAdminSection contains action buttons, queue tables, and pagination controls."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Header buttons & Alerts
    assert 'id="btnOpenAdjustBalanceModal"' in html
    assert 'id="btnRefreshAdminLeaves"' in html
    assert 'id="leaveAdminAlert"' in html

    # Pending Leave Table
    assert 'id="pendingLeavesTableBody"' in html
    assert 'id="pendingLeavesPagination"' in html

    # Comp-Off Claims Management Table
    assert "Compensatory Off Extra-Work Claims" in html
    assert 'aria-label="Comp-Off Extra-Work Claims"' in html
    assert 'id="adminCompoffTableBody"' in html
    assert 'id="adminCompoffPagination"' in html
    assert 'id="adminCompoffPrevBtn"' in html
    assert 'id="adminCompoffNextBtn"' in html


def test_admin_controllers_in_portal_js():
    """Verify portal.js defines all required HR/Admin queue, review, and adjustment controllers."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Functions
    assert "loadAdminPendingLeaves" in js
    assert "openReviewLeaveModal" in js
    assert "handleLeaveReviewDecision" in js
    assert "loadAdminCompoffClaims" in js
    assert "openReviewCompoffModal" in js
    assert "handleCompoffReviewDecision" in js
    assert "loadEmployeeOptionsForAdjustment" in js
    assert "setupAdminLeaveControllers" in js

    # Navigation & Lifecycle wiring
    assert "setupAdminLeaveControllers()" in js
    assert "loadAdminPendingLeaves()" in js
    assert "loadAdminCompoffClaims()" in js


def test_admin_api_endpoints_and_payloads_in_portal_js():
    """Verify portal.js connects to exact Phase 2.7 HR/Admin endpoints and schemas."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Endpoints
    assert "/leaves/admin/pending" in js
    assert "/leaves/admin/${leaveId}/approve" in js
    assert "/leaves/admin/${leaveId}/reject" in js
    assert "/leaves/admin/compoff/claims" in js
    assert "/leaves/admin/compoff/claims/${claimId}/approve" in js
    assert "/leaves/admin/compoff/claims/${claimId}/reject" in js
    assert "/leaves/admin/balances/adjust" in js
    assert "/employees?limit=100" in js

    # Adjustment payload fields matching LeaveBalanceAdjustmentRequest
    assert "employee_id: parseInt" in js
    assert "category_code: catCode" in js
    assert "bucket: bucket" in js
    assert "year: parseInt" in js
    assert "amount: amount" in js


def test_admin_anti_self_approval_and_validation_safeguards():
    """Verify portal.js enforces anti-self-approval, mandatory reasons, and duplicate protection."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Anti-self approval and review safeguards
    assert "rec.employee_id === currentUser.id" in js
    assert "c.employee_id === currentUser.id" in js
    assert "Anti-Self-Approval violation" in js
    assert "Anti-Self-Review violation" in js

    # Confirmation dialogues
    assert 'Are you sure you want to ${actionText} leave application' in js or 'Are you sure you want to' in js
    assert 'Confirm administrative adjustment?' in js

    # Mandatory rejection reason length checks (min 3 chars)
    assert "notes.length < 3" in js

    # Mandatory balance adjustment reason check (min 5 chars)
    assert "reasonVal.length < 5" in js

    # Duplicate submission protection (spinner toggle)
    assert "btnApprove.disabled = true" in js
    assert "submitBtn.disabled = true" in js


# ============================================================================
# Phase 2.8.5 Tests: Employee Comp-Off Claim Submission UI & History
# ============================================================================

def test_claim_compoff_modal_elements_and_accessibility():
    """Verify that dashboard.html renders the employee Comp-Off claim modal with accessible controls."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Modal container and accessibility attributes
    assert 'id="claimCompoffModal"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="claimCompoffModalTitle"' in html
    assert 'id="claimCompoffModalTitle"' in html
    assert "Claim Compensatory Off" in html

    # Alert container
    assert 'id="claimCompoffAlert"' in html

    # Form controls
    assert 'id="claimCompoffForm"' in html
    assert 'id="claimWorkDate"' in html
    assert 'id="claimDaysSelect"' in html
    assert 'value="1.0"' in html
    assert 'value="0.5"' in html
    assert 'id="claimReasonInput"' in html

    # Action buttons
    assert 'id="closeClaimCompoffModalBtn"' in html
    assert 'id="btnCancelClaimCompoff"' in html
    assert 'id="btnSubmitClaimCompoff"' in html
    assert 'id="btnSubmitClaimText"' in html
    assert 'id="btnSubmitClaimSpinner"' in html

    # Security: No inline scripts or onclick handlers
    start_modal = html.find('id="claimCompoffModal"')
    end_modal = html.find('</form>', start_modal)
    modal_slice = html[start_modal:end_modal]
    assert "onclick" not in modal_slice.lower()
    assert "<script" not in modal_slice.lower()


def test_my_compoff_claims_table_structure():
    """Verify leavesSection contains Claim Comp-Off button and My Comp-Off Claims history table."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Header button
    assert 'id="btnClaimCompoff"' in html
    assert "Claim Comp-Off" in html
    assert 'aria-label="Claim compensatory off"' in html

    # Table structure
    assert "My Comp-Off Claims" in html
    assert 'aria-label="My Comp-Off Claims History"' in html
    table_slice = html[html.find('aria-label="My Comp-Off Claims History"'):]
    header_slice = table_slice[:table_slice.find('</thead>')]
    assert "Extra-Work Date" in header_slice
    assert "Days Claimed" in header_slice
    assert "Days Credited" in header_slice
    assert "Reason" in header_slice
    assert "Status" in header_slice
    assert "Review Remarks" in header_slice

    # Empty state placeholder
    assert 'id="myCompoffTableBody"' in html
    assert "No compensatory off claims submitted" in html

    # Refresh and pagination
    assert 'id="btnRefreshMyCompoff"' in html
    assert 'id="myCompoffPagination"' in html
    assert 'id="myCompoffPaginationInfo"' in html


def test_compoff_claim_controllers_in_portal_js():
    """Verify portal.js defines employee claim loaders, renderers, and event handlers."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Core functions
    assert "loadMyCompoffClaims" in js
    assert "renderMyCompoffClaims" in js
    assert "setupCompoffClaimControllers" in js

    # Direct GET /leaves/compoff/claims invocation
    assert "apiFetch('/leaves/compoff/claims')" in js

    # Lifecycle and navigation wiring
    assert "setupCompoffClaimControllers()" in js
    assert "loadMyCompoffClaims()" in js


def test_compoff_claim_api_endpoint_and_payload_alignment():
    """Verify portal.js connects to POST /leaves/compoff/claims with exact schema fields."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Endpoint
    assert "/leaves/compoff/claims" in js

    # Payload matching CompOffClaimCreateRequest
    assert "work_date: workDate" in js
    assert "days_claimed: daysClaimed" in js
    assert "reason: reason" in js

    # Safe client payload: never trust client for ID or status
    claim_section = js[js.find('function setupCompoffClaimControllers'):js.find('let adminPendingLeavesState')]
    assert "employee_id:" not in claim_section
    assert "reviewer_id:" not in claim_section
    assert "status:" not in claim_section


def test_compoff_claim_validation_and_safeguards():
    """Verify portal.js enforces date bounds, days validation, duplicate prevention, and escaping."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    # Future date check
    assert "workDate > todayIso" in js
    assert "Cannot claim compensatory off for future work dates" in js

    # Days claimed validation
    assert "daysClaimed !== 0.5 && daysClaimed !== 1.0" in js

    # Min reason length check
    assert "reason.length < 3" in js

    # Double submit prevention
    assert "submitBtn.disabled = true" in js
    assert "submitSpinner.style.display = 'inline-block'" in js

    # XSS prevention via escapeHtml
    assert "escapeHtml(c.work_date)" in js
    assert "escapeHtml(c.reason)" in js
