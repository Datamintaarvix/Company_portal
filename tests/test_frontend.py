"""Automated tests for Stage 5 Frontend views, static asset serving, and UI security."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_login_page_renders_cleanly():
    """Verify that GET /login returns 200 and includes essential UI and accessibility elements."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Brand & Title
    assert "Company Employee Portal" in html
    assert "Sign In" in html
    assert "brand-badge" in html
    assert "<title>DATAMINT AARVIX | Enterprise HRMS</title>" in html
    assert "/static/img/datamint-aarvix-logo.jpg" in html
    assert "login-brand-logo" in html

    # Accessible form controls
    assert 'id="loginForm"' in html
    assert 'id="emailInput"' in html
    assert 'type="email"' in html
    assert 'id="passwordInput"' in html
    assert 'type="password"' in html
    assert 'id="togglePasswordBtn"' in html
    assert 'id="loginBtn"' in html
    assert 'id="alertContainer"' in html

    # Static asset links
    assert '/static/css/portal.css' in html
    assert '/static/js/portal.js' in html


def test_dashboard_page_renders_shell():
    """Verify that GET /dashboard returns 200 and includes the application shell and modals."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Application shell
    assert "<title>DATAMINT AARVIX | Enterprise HRMS</title>" in html
    assert "/static/img/datamint-aarvix-logo.jpg" in html
    assert "sidebar-brand-logo" in html
    assert 'id="dashboardRoot"' in html
    assert 'id="appSidebar"' in html
    assert 'id="sidebarNavContainer"' in html
    assert 'id="logoutBtn"' in html
    assert 'id="headerUserName"' in html

    # Content sections
    assert 'id="profileSection"' in html
    assert 'id="accountSection"' in html
    assert 'id="directorySection"' in html
    assert 'id="provisionSection"' in html
    assert 'id="adminOverviewSection"' in html
    assert 'id="attendanceSection"' in html
    assert 'id="attendanceAdminSection"' in html

    # Attendance UI elements
    assert 'id="istLiveClock"' in html
    assert 'id="btnCheckIn"' in html
    assert 'id="btnCheckOut"' in html
    assert 'id="myAttendanceTableBody"' in html
    assert 'id="companyAttendanceTableBody"' in html

    # Modals
    assert 'id="detailModal"' in html
    assert 'id="editEmployeeModal"' in html
    assert 'id="roleModal"' in html
    assert 'id="statusModal"' in html
    assert 'id="correctAttendanceModal"' in html

    # Phase 3.1A Joining Date elements
    assert 'id="provJoiningDateInput"' in html
    assert 'id="profJoiningDate"' in html
    assert 'id="detailEmpJoiningDate"' in html


def test_portal_redirect():
    """Verify that GET /portal redirects to /dashboard."""
    response = client.get("/portal", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"


def test_static_css_served_correctly():
    """Verify that the portal stylesheet is served with proper content type and styles."""
    response = client.get("/static/css/portal.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    css = response.text
    assert "--primary" in css
    assert ".login-card" in css
    assert ".app-sidebar" in css
    assert ".live-clock-card" in css
    assert ".btn-punch-in" in css
    assert ".btn-punch-out" in css
    assert ".status-present" in css
    assert ".status-incomplete" in css
    assert ".login-brand-logo" in css
    assert ".sidebar-brand-logo" in css
    assert "aspect-ratio" in css


def test_static_js_served_correctly():
    """Verify that the portal JavaScript is served with proper content type and functions."""
    response = client.get("/static/js/portal.js")
    assert response.status_code == 200
    js = response.text
    assert "apiFetch" in js
    assert "getCookie" in js
    assert "getCsrfCookieName" in js
    assert 'meta[name="csrf-cookie-name"]' in js
    assert "initLoginPage" in js
    assert "initDashboard" in js
    assert "loadTodayAttendance" in js
    assert "loadMyAttendanceHistory" in js
    assert "loadCompanyAttendance" in js
    assert "setupCorrectionForm" in js
    assert "startIstClock" in js
    assert "provJoiningDateInput" in js
    assert "date_of_joining" in js


def test_csrf_cookie_name_rendered_in_templates(monkeypatch):
    """Verify that /login and /dashboard dynamically render the configured CSRF cookie name.

    Tests development ('csrf_token'), staging ('csrf_token_staging'), and
    production ('csrf_token_prod') cookie configurations.
    """
    import app.routers.views as views_mod

    # 1. Development default configuration
    monkeypatch.setattr(views_mod.settings, "CSRF_COOKIE_NAME", "csrf_token")
    login_resp = client.get("/login")
    dash_resp = client.get("/dashboard")
    assert '<meta name="csrf-cookie-name" content="csrf_token">' in login_resp.text
    assert '<meta name="csrf-cookie-name" content="csrf_token">' in dash_resp.text

    # 2. Staging configuration
    monkeypatch.setattr(views_mod.settings, "CSRF_COOKIE_NAME", "csrf_token_staging")
    login_resp = client.get("/login")
    dash_resp = client.get("/dashboard")
    assert '<meta name="csrf-cookie-name" content="csrf_token_staging">' in login_resp.text
    assert '<meta name="csrf-cookie-name" content="csrf_token_staging">' in dash_resp.text

    # 3. Production configuration
    monkeypatch.setattr(views_mod.settings, "CSRF_COOKIE_NAME", "csrf_token_prod")
    login_resp = client.get("/login")
    dash_resp = client.get("/dashboard")
    assert '<meta name="csrf-cookie-name" content="csrf_token_prod">' in login_resp.text
    assert '<meta name="csrf-cookie-name" content="csrf_token_prod">' in dash_resp.text


def test_no_credentials_or_hashes_exposed_in_templates_or_assets():
    """Ensure static files and templates contain no hardcoded passwords, tokens, or hashes."""
    for path in ["/login", "/dashboard", "/static/css/portal.css", "/static/js/portal.js"]:
        content = client.get(path).text.lower()
        assert "password_hash" not in content
        assert "$argon2" not in content
        assert "secret_key" not in content
        assert "postgres_password" not in content


def test_static_logo_image_served():
    """Verify that the official DATAMINT AARVIX company logo is served with correct headers."""
    response = client.get("/static/img/datamint-aarvix-logo.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("image/jpeg", "image/jpg")
    assert len(response.content) > 10000
