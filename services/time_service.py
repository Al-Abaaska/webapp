"""Timezone helpers for UTC database values and Somalia-facing displays."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


SOMALIA_TIMEZONE = ZoneInfo('Africa/Mogadishu')


def somalia_now():
    """Return the current timezone-aware time in Africa/Mogadishu."""
    return datetime.now(timezone.utc).astimezone(SOMALIA_TIMEZONE)


def as_somalia_time(value):
    """Convert a datetime to Africa/Mogadishu, treating naive database values as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SOMALIA_TIMEZONE)


def format_somalia_time(value, date_format='%Y-%m-%d %H:%M EAT'):
    """Format a UTC datetime for display in Somalia time."""
    local_value = as_somalia_time(value)
    return local_value.strftime(date_format) if local_value else ''


def utc_isoformat(value):
    """Return an unambiguous UTC ISO timestamp for browser-side localization."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
