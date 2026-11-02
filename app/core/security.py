"""Security utilities for password hashing, session tokens, and CSRF protection.

Uses pwdlib with Argon2 for modern, memory-hard, salt-backed password hashing.
Uses secrets module for cryptographically secure random token generation.
"""

import hmac
import secrets
from typing import Optional
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# Initialize Argon2 hasher with pwdlib
_hasher = PasswordHash((Argon2Hasher(),))


def get_password_hash(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Verify a plaintext password against an Argon2 hash.

    Returns False if hashed_password is None or empty, or if verification fails.
    """
    if not hashed_password or not plain_password:
        return False
    try:
        return _hasher.verify(plain_password, hashed_password)
    except Exception:
        return False


def validate_password_strength(password: str) -> tuple[bool, Optional[str]]:
    """Validate password complexity requirements.

    Requirements:
    - Minimum 8 characters length
    - At least one lowercase character
    - At least one uppercase character
    - At least one digit
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit."
    return True, None


def generate_session_token() -> str:
    """Generate a high-entropy cryptographically random session token."""
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    """Generate a high-entropy cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(provided_token: Optional[str], expected_token: Optional[str]) -> bool:
    """Safely compare two CSRF tokens using constant-time comparison."""
    if not provided_token or not expected_token:
        return False
    return hmac.compare_digest(provided_token, expected_token)
