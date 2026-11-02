"""In-memory rate limiter for login attempts.

Protects against brute-force attacks by tracking failed authentication attempts
per client IP and targeted email account over a configurable sliding time window.
"""

from collections import defaultdict
from datetime import datetime, timezone
import threading
from typing import Optional

from app.core.config import get_settings


class LoginRateLimiter:
    """Thread-safe rate limiter tracking failed login attempts."""

    def __init__(self, max_attempts: Optional[int] = None, window_seconds: Optional[int] = None) -> None:
        self._settings = get_settings()
        self.max_attempts = max_attempts or self._settings.RATE_LIMIT_LOGIN_MAX_ATTEMPTS
        self.window_seconds = window_seconds or self._settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS
        self._lock = threading.Lock()
        # Key: (ip, email.lower()) -> list of attempt UTC timestamps
        self._account_attempts: dict[str, list[float]] = defaultdict(list)
        # Key: ip -> list of attempt UTC timestamps
        self._ip_attempts: dict[str, list[float]] = defaultdict(list)

    def _clean_old_entries(self, timestamps: list[float], now: float) -> list[float]:
        cutoff = now - self.window_seconds
        return [t for t in timestamps if t > cutoff]

    def _make_key(self, ip: str, email: str) -> str:
        return f"{ip.strip()}:{email.strip().lower()}"

    def is_rate_limited(self, ip: str, email: str) -> bool:
        """Check if either the (IP + email) pair or the IP has exceeded allowed attempts."""
        now = datetime.now(timezone.utc).timestamp()
        key = self._make_key(ip, email)

        with self._lock:
            # Clean and check account key
            self._account_attempts[key] = self._clean_old_entries(self._account_attempts[key], now)
            if len(self._account_attempts[key]) >= self.max_attempts:
                return True

            # Clean and check IP key (allow 3x max attempts across multiple accounts from same IP before full IP lock)
            ip_key = ip.strip()
            self._ip_attempts[ip_key] = self._clean_old_entries(self._ip_attempts[ip_key], now)
            if len(self._ip_attempts[ip_key]) >= self.max_attempts * 3:
                return True

            return False

    def record_failed_attempt(self, ip: str, email: str) -> None:
        """Record a failed login attempt for both (IP + email) and the IP."""
        now = datetime.now(timezone.utc).timestamp()
        key = self._make_key(ip, email)
        ip_key = ip.strip()

        with self._lock:
            self._account_attempts[key] = self._clean_old_entries(self._account_attempts[key], now)
            self._account_attempts[key].append(now)

            self._ip_attempts[ip_key] = self._clean_old_entries(self._ip_attempts[ip_key], now)
            self._ip_attempts[ip_key].append(now)

    def reset_attempts(self, ip: str, email: str) -> None:
        """Reset failed attempt counters upon successful login."""
        key = self._make_key(ip, email)
        with self._lock:
            if key in self._account_attempts:
                del self._account_attempts[key]

    def clear(self) -> None:
        """Clear all tracking state (used in automated testing)."""
        with self._lock:
            self._account_attempts.clear()
            self._ip_attempts.clear()


# Global rate limiter instance
login_rate_limiter = LoginRateLimiter()
