"""Timezone and workday utilities for Company Portal.

Standardizes on Asia/Kolkata (IST) for operational workday boundaries,
while maintaining timezone-aware UTC timestamps in database storage.
"""

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def get_company_tz() -> ZoneInfo:
    """Return the ZoneInfo instance for the configured company timezone."""
    settings = get_settings()
    return ZoneInfo(settings.COMPANY_TIMEZONE)


def get_current_work_date() -> date:
    """Determine the current calendar workday date in company timezone (Asia/Kolkata).

    Converts the current UTC instant to company local time and returns the date.
    Correctly handles midnight transitions.
    """
    return datetime.now(timezone.utc).astimezone(get_company_tz()).date()


def is_weekend_day(target_date: date) -> bool:
    """Determine if a given date is a weekend (Saturday or Sunday)."""
    # 5 is Saturday, 6 is Sunday
    return target_date.weekday() in (5, 6)


def format_ist_time(dt: Optional[datetime], format_pattern: str = "%I:%M %p IST") -> Optional[str]:
    """Convert a UTC or timezone-aware datetime to formatted IST string."""
    if not dt:
        return None
    # Ensure timezone awareness
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_company_tz()).strftime(format_pattern)


def format_ist_date(target_date: Optional[date], format_pattern: str = "%d %b %Y") -> Optional[str]:
    """Format a date object as a human-friendly string (e.g. 29 Oct 2026)."""
    if not target_date:
        return None
    return target_date.strftime(format_pattern)


def ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware and converted to UTC."""
    if dt.tzinfo is None:
        # Naive datetimes are assumed to be in company timezone
        dt = dt.replace(tzinfo=get_company_tz())
    return dt.astimezone(timezone.utc)
