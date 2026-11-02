"""Authentication and session management services."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    generate_csrf_token,
    generate_session_token,
    get_password_hash,
    validate_password_strength,
    verify_password,
)
from app.models.employee import Employee, UserRole
from app.models.session import Session as DbSession
from app.schemas.auth import EmployeeCreate

settings = get_settings()


def provision_employee(db: Session, data: EmployeeCreate) -> Employee:
    """Provision a new employee or admin account securely.

    Validates:
    - Role against organization constraints ('employee', 'hr', 'admin')
    - Email uniqueness
    - Employee code uniqueness
    - Password complexity
    Hashes password using Argon2 before storing.
    """
    # Validate role
    valid_roles = {role.value for role in UserRole}
    if data.role.lower() not in valid_roles:
        raise ValueError(f"Invalid role '{data.role}'. Allowed roles: {', '.join(valid_roles)}")

    # Validate password strength
    is_valid_pw, pw_err = validate_password_strength(data.password)
    if not is_valid_pw:
        raise ValueError(pw_err)

    # Check duplicate email
    existing_email = db.execute(select(Employee).where(Employee.email == data.email.lower())).scalar_one_or_none()
    if existing_email:
        raise ValueError(f"An employee with email '{data.email}' already exists.")

    # Check duplicate employee code
    existing_code = db.execute(select(Employee).where(Employee.employee_code == data.employee_code)).scalar_one_or_none()
    if existing_code:
        raise ValueError(f"An employee with code '{data.employee_code}' already exists.")

    hashed_pw = get_password_hash(data.password)

    new_employee = Employee(
        employee_code=data.employee_code.strip(),
        name=data.name.strip(),
        email=data.email.strip().lower(),
        department=data.department.strip() if data.department else None,
        role=data.role.lower(),
        password_hash=hashed_pw,
        is_active=True,
    )
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    return new_employee


def authenticate_employee(db: Session, email: str, password: str) -> Employee:
    """Authenticate an employee by email and password.

    Raises HTTPException 401 with a generic error message on invalid credentials,
    or a specific message if the account is inactive.
    """
    employee = db.execute(select(Employee).where(Employee.email == email.strip().lower())).scalar_one_or_none()

    if not employee or not verify_password(password, employee.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not employee.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive or disabled. Contact administrator.",
        )

    return employee


def create_employee_session(
    db: Session,
    employee_id: int,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> DbSession:
    """Create a persistent server-side session in PostgreSQL with an expiry time."""
    token = generate_session_token()
    csrf_token = generate_csrf_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.SESSION_EXPIRE_MINUTES)

    db_session = DbSession(
        session_token=token,
        csrf_token=csrf_token,
        employee_id=employee_id,
        ip_address=ip_address[:45] if ip_address else None,
        user_agent=user_agent[:255] if user_agent else None,
        expires_at=expires_at,
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session


def get_active_session(db: Session, session_token: str) -> Optional[DbSession]:
    """Retrieve an active, non-expired session from the database."""
    if not session_token:
        return None

    db_session = db.execute(
        select(DbSession).where(DbSession.session_token == session_token)
    ).scalar_one_or_none()

    if not db_session:
        return None

    # Check expiration
    now = datetime.now(timezone.utc)
    if db_session.expires_at <= now:
        db.delete(db_session)
        db.commit()
        return None

    return db_session


def invalidate_session(db: Session, session_token: str) -> bool:
    """Invalidate (delete) a session from the server-side session store."""
    if not session_token:
        return False

    db_session = db.execute(
        select(DbSession).where(DbSession.session_token == session_token)
    ).scalar_one_or_none()

    if db_session:
        db.delete(db_session)
        db.commit()
        return True

    return False
