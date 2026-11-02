"""Role-based access control (RBAC) permission dependencies.

Provides reusable FastAPI dependencies that verify the authenticated caller possesses
the required organizational role (employee, hr, admin). Freshly loads user state
from PostgreSQL to ensure deactivated accounts or changed roles are enforced immediately.
"""

from typing import Iterable, Union
from fastapi import Depends, HTTPException, status

from app.dependencies.auth import get_current_employee
from app.models.employee import Employee, UserRole


class RequireRole:
    """Dependency that enforces role-based access control.

    Rejects unauthenticated requests with HTTP 401 (via get_current_employee),
    rejects inactive accounts with HTTP 401 (via get_current_employee),
    and rejects authenticated callers with insufficient permissions with HTTP 403.
    """

    def __init__(self, *allowed_roles: Union[str, UserRole]) -> None:
        self.allowed_roles = {
            role.value if isinstance(role, UserRole) else str(role).lower()
            for role in allowed_roles
        }

    def __call__(
        self,
        current_employee: Employee = Depends(get_current_employee),
    ) -> Employee:
        if current_employee.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: insufficient role permissions",
            )
        return current_employee


# Pre-configured role dependencies
require_admin = RequireRole(UserRole.ADMIN)
require_hr = RequireRole(UserRole.HR)
require_hr_or_admin = RequireRole(UserRole.HR, UserRole.ADMIN)
require_authenticated = get_current_employee
