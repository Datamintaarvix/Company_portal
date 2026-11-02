"""Authentication and session dependencies for FastAPI route handlers."""

from typing import Optional
from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import verify_csrf_token
from app.db.session import get_db
from app.models.employee import Employee
from app.models.session import Session as DbSession
from app.services.auth import get_active_session

settings = get_settings()


def get_session_token_from_cookie(request: Request) -> Optional[str]:
    """Extract session token from request cookies."""
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
) -> DbSession:
    """Validate and return the active server-side database session.

    Raises:
        HTTP 401: If session cookie is missing, session does not exist, or session has expired.
    """
    token = get_session_token_from_cookie(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
        )

    db_session = get_active_session(db, token)
    if not db_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    return db_session


def get_current_employee(
    db_session: DbSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Employee:
    """Load and return the authenticated employee associated with the active session.

    Always fetches the current employee record fresh from PostgreSQL to ensure
    disabled or deactivated accounts are rejected immediately.

    Raises:
        HTTP 401: If user cannot be found or the account has been deactivated.
    """
    employee = db.execute(
        select(Employee).where(Employee.id == db_session.employee_id)
    ).scalar_one_or_none()

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Employee account not found",
        )

    if not employee.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive or disabled",
        )

    return employee


def verify_csrf(
    request: Request,
    db_session: DbSession = Depends(get_current_session),
    x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token"),
) -> None:
    """Verify CSRF token for state-changing cookie-authenticated requests.

    Raises:
        HTTP 403: If X-CSRF-Token header is missing or does not match the session's CSRF token.
    """
    if not x_csrf_token or not verify_csrf_token(x_csrf_token, db_session.csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )


def set_auth_cookies(response: Response, session: DbSession) -> None:
    """Set secure session and CSRF cookies on the outgoing response."""
    max_age = settings.SESSION_EXPIRE_MINUTES * 60

    # Opaque session ID cookie: HttpOnly to prevent JavaScript access
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session.session_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        domain=None,
        secure=settings.is_cookie_secure,
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )

    # CSRF token cookie: readable by frontend to include in X-CSRF-Token headers
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=session.csrf_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        domain=None,
        secure=settings.is_cookie_secure,
        httponly=False,
        samesite=settings.COOKIE_SAMESITE,
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear session and CSRF cookies on logout."""
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        secure=settings.is_cookie_secure,
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )
    response.delete_cookie(
        key=settings.CSRF_COOKIE_NAME,
        path="/",
        secure=settings.is_cookie_secure,
        httponly=False,
        samesite=settings.COOKIE_SAMESITE,
    )
