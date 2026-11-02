"""Authentication API router for Company Employee Portal.

Handles employee login, session validation (current-user), and secure logout with
CSRF verification and rate limiting.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import (
    clear_auth_cookies,
    get_current_employee,
    get_current_session,
    set_auth_cookies,
    verify_csrf,
)
from app.models.employee import Employee
from app.models.session import Session as DbSession
from app.schemas.auth import (
    EmployeeProfileResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
)
from app.services.auth import (
    authenticate_employee,
    create_employee_session,
    invalidate_session,
)
from app.services.rate_limiter import login_rate_limiter

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _get_client_ip(request: Request) -> str:
    """Safely obtain client IP address for rate limiting."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Employee Login",
    responses={
        200: {"description": "Authentication successful; session cookies established."},
        401: {"description": "Invalid credentials or inactive account."},
        429: {"description": "Too many failed login attempts."},
    },
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Authenticate an employee using email and password.

    Sets an HttpOnly server-side session cookie and a CSRF cookie on success.
    Protects against brute force attacks with account- and IP-aware rate limiting.
    """
    client_ip = _get_client_ip(request)

    # Check login rate limit
    if login_rate_limiter.is_rate_limited(client_ip, credentials.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    try:
        employee = authenticate_employee(db, credentials.email, credentials.password)
    except HTTPException:
        login_rate_limiter.record_failed_attempt(client_ip, credentials.email)
        raise

    # Reset rate limiting attempts on successful authentication
    login_rate_limiter.reset_attempts(client_ip, credentials.email)

    user_agent = request.headers.get("user-agent")
    session = create_employee_session(
        db=db,
        employee_id=employee.id,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    set_auth_cookies(response, session)

    return LoginResponse(
        message="Login successful",
        user=EmployeeProfileResponse.model_validate(employee),
    )


@router.get(
    "/me",
    response_model=EmployeeProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Authenticated Employee Profile",
    responses={
        200: {"description": "Current employee profile details."},
        401: {"description": "Missing, expired, or invalid session, or disabled account."},
    },
)
async def get_me(
    current_employee: Employee = Depends(get_current_employee),
) -> EmployeeProfileResponse:
    """Retrieve the profile of the currently logged-in employee from the server-side session."""
    return EmployeeProfileResponse.model_validate(current_employee)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Employee Logout",
    responses={
        200: {"description": "Session invalidated and cookies cleared successfully."},
        401: {"description": "Not authenticated."},
        403: {"description": "CSRF token missing or invalid."},
    },
)
async def logout(
    response: Response,
    current_session: DbSession = Depends(get_current_session),
    _: None = Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> LogoutResponse:
    """Terminate the current server-side session and clear authentication cookies.

    Requires CSRF protection via the X-CSRF-Token header.
    """
    invalidate_session(db, current_session.session_token)
    clear_auth_cookies(response)

    return LogoutResponse(message="Logged out successfully")
