"""Command-line interface (CLI) for Company Employee Portal administrative tasks.

Provides protected account provisioning for the initial admin account and employees.
Passwords are prompted securely via getpass (never echoed or passed as flags)
and hashed with Argon2id before storage.
"""

import getpass
import sys
from typing import Optional
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.security import validate_password_strength
from app.db.session import SessionLocal
from app.models.employee import UserRole
from app.schemas.auth import EmployeeCreate
from app.services.auth import provision_employee


def _prompt_string(prompt_text: str, default: Optional[str] = None) -> str:
    """Prompt user for a required non-empty string."""
    while True:
        display = f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
        val = input(display).strip()
        if not val and default:
            return default
        if val:
            return val
        print("[-] This field cannot be empty. Please enter a value.")


def _prompt_email(prompt_text: str = "Corporate Email") -> str:
    """Prompt and validate an email address."""
    adapter = TypeAdapter(EmailStr)
    while True:
        val = input(f"{prompt_text}: ").strip()
        try:
            adapter.validate_python(val)
            return val.lower()
        except ValidationError:
            print("[-] Invalid email address format. Example: user@company.com")


def _prompt_password(prompt_text: str = "Password") -> str:
    """Prompt securely for a password without echoing, with confirmation and strength checks."""
    while True:
        pw1 = getpass.getpass(f"{prompt_text}: ")
        if not pw1:
            print("[-] Password cannot be empty.")
            continue

        is_valid, err_msg = validate_password_strength(pw1)
        if not is_valid:
            print(f"[-] Weak password: {err_msg}")
            continue

        pw2 = getpass.getpass(f"Confirm {prompt_text}: ")
        if pw1 != pw2:
            print("[-] Passwords do not match. Please try again.")
            continue

        return pw1


def create_admin_interactive() -> None:
    """Interactive command to provision the initial or additional administrator account."""
    print("\n" + "=" * 55)
    print(" Company Portal — Administrator Account Provisioning")
    print("=" * 55 + "\n")

    emp_code = _prompt_string("Employee Code", default="ADM-001")
    name = _prompt_string("Full Name")
    email = _prompt_email("Corporate Email")
    dept = _prompt_string("Department", default="IT Administration")
    password = _prompt_password("Admin Password")

    db = SessionLocal()
    try:
        data = EmployeeCreate(
            employee_code=emp_code,
            name=name,
            email=email,
            department=dept,
            role=UserRole.ADMIN.value,
            password=password,
        )
        emp = provision_employee(db, data)
        print("\n[+] SUCCESS: Administrator account provisioned successfully.")
        print(f"    - ID:            {emp.id}")
        print(f"    - Employee Code: {emp.employee_code}")
        print(f"    - Name:          {emp.name}")
        print(f"    - Email:         {emp.email}")
        print(f"    - Role:          {emp.role}")
        print(f"    - Department:    {emp.department}")
    except ValueError as exc:
        print(f"\n[-] ERROR: Failed to provision account: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[-] UNEXPECTED ERROR: {exc}")
        sys.exit(1)
    finally:
        db.close()


def create_employee_interactive() -> None:
    """Interactive command to provision an employee or HR account."""
    print("\n" + "=" * 55)
    print(" Company Portal — Staff Account Provisioning")
    print("=" * 55 + "\n")

    emp_code = _prompt_string("Employee Code")
    name = _prompt_string("Full Name")
    email = _prompt_email("Corporate Email")
    dept = _prompt_string("Department", default="Engineering")

    while True:
        role = _prompt_string("Role (employee, hr, admin)", default="employee").lower()
        if role in {r.value for r in UserRole}:
            break
        print("[-] Invalid role. Must be 'employee', 'hr', or 'admin'.")

    password = _prompt_password("Account Password")

    db = SessionLocal()
    try:
        data = EmployeeCreate(
            employee_code=emp_code,
            name=name,
            email=email,
            department=dept,
            role=role,
            password=password,
        )
        emp = provision_employee(db, data)
        print(f"\n[+] SUCCESS: {role.capitalize()} account provisioned successfully.")
        print(f"    - ID:            {emp.id}")
        print(f"    - Employee Code: {emp.employee_code}")
        print(f"    - Name:          {emp.name}")
        print(f"    - Email:         {emp.email}")
        print(f"    - Role:          {emp.role}")
    except ValueError as exc:
        print(f"\n[-] ERROR: Failed to provision account: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[-] UNEXPECTED ERROR: {exc}")
        sys.exit(1)
    finally:
        db.close()


def main() -> None:
    """CLI dispatcher."""
    if len(sys.argv) < 2:
        print("Usage: python -m app.cli [command]")
        print("\nAvailable commands:")
        print("  create-admin      Provision an administrator account interactively")
        print("  create-employee   Provision an employee or HR account interactively")
        sys.exit(1)

    command = sys.argv[1].lower()
    if command == "create-admin":
        create_admin_interactive()
    elif command in ("create-employee", "create-user"):
        create_employee_interactive()
    else:
        print(f"[-] Unknown command '{command}'.")
        print("Available commands: create-admin, create-employee")
        sys.exit(1)


if __name__ == "__main__":
    main()
