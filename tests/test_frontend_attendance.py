"""Automated integration tests for Stage E Attendance Management frontend."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_dashboard_renders_attendance_sections_and_modals():
    """Verify that GET /dashboard includes the attendance components and correction dialog."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text

    # Sections
    assert 'id="attendanceSection"' in html
    assert 'id="attendanceAdminSection"' in html

    # Live clock & Shift card elements
    assert 'id="istLiveClock"' in html
    assert 'id="todayWorkDateText"' in html
    assert 'id="todayStatusBadge"' in html
    assert 'id="todayCheckInTime"' in html
    assert 'id="todayCheckOutTime"' in html
    assert 'id="todayDayType"' in html
    assert 'id="todayDuration"' in html
    assert 'id="punchAlert"' in html
    assert 'id="punchNotesInput"' in html
    assert 'id="btnCheckIn"' in html
    assert 'id="btnCheckOut"' in html

    # Personal History Elements
    assert 'id="myAttStartDate"' in html
    assert 'id="myAttEndDate"' in html
    assert 'id="btnFilterMyAtt"' in html
    assert 'id="btnResetMyAtt"' in html
    assert 'id="myAttendanceTableBody"' in html
    assert 'id="myAttPaginationInfo"' in html
    assert 'id="myAttPrevBtn"' in html
    assert 'id="myAttNextBtn"' in html

    # Company Attendance Management Elements
    assert 'id="admAttEmpId"' in html
    assert 'id="admAttDept"' in html
    assert 'id="admAttStartDate"' in html
    assert 'id="admAttEndDate"' in html
    assert 'id="admAttStatus"' in html
    assert 'id="btnFilterAdmAtt"' in html
    assert 'id="btnResetAdmAtt"' in html
    assert 'id="companyAttendanceTableBody"' in html
    assert 'id="companyAttPaginationInfo"' in html
    assert 'id="companyAttPrevBtn"' in html
    assert 'id="companyAttNextBtn"' in html

    # Audited Correction Modal
    assert 'id="correctAttendanceModal"' in html
    assert 'id="correctAttendanceForm"' in html
    assert 'id="correctRecordId"' in html
    assert 'id="correctModalEmpName"' in html
    assert 'id="correctModalWorkDate"' in html
    assert 'id="correctCheckInInput"' in html
    assert 'id="correctCheckOutInput"' in html
    assert 'id="correctStatusSelect"' in html
    assert 'id="correctReasonInput"' in html
    assert 'id="correctNotesInput"' in html
    assert 'id="btnSubmitCorrection"' in html


def test_portal_css_contains_attendance_classes():
    """Verify portal.css contains required styling for live clock, punch actions, and badges."""
    response = client.get("/static/css/portal.css")
    assert response.status_code == 200
    css = response.text

    assert ".live-clock-card" in css
    assert ".live-clock-time" in css
    assert ".btn-punch-in" in css
    assert ".btn-punch-out" in css
    assert ".status-present" in css
    assert ".status-incomplete" in css
    assert ".status-half_day" in css
    assert ".status-absent" in css
    assert ".badge-weekend" in css
    assert ".badge-corrected" in css


def test_portal_js_contains_attendance_functions_and_listeners():
    """Verify portal.js contains attendance controllers, IST clock, punch logic, and audit modals."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text

    assert "startIstClock" in js
    assert "formatDuration" in js
    assert "toIstDatetimeLocal" in js
    assert "loadTodayAttendance" in js
    assert "setupAttendancePunchListeners" in js
    assert "loadMyAttendanceHistory" in js
    assert "setupMyAttendanceFilters" in js
    assert "loadCompanyAttendance" in js
    assert "setupCompanyAttendanceFilters" in js
    assert "openCorrectionModalFromData" in js
    assert "setupCorrectionForm" in js
    assert "attendanceSection" in js
    assert "attendanceAdminSection" in js
