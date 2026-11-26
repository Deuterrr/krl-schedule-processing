"""Text and timetable clock normalization helpers for GAPEKA schedule cells."""

from __future__ import annotations

import re

_TIME_PATTERN = re.compile(r"^(?:lanjut\s+)?(\d{1,2})[:.](\d{2})$", re.IGNORECASE)
_CID_PLACEHOLDER_PATTERN = re.compile(r"^\(cid:\d+\)$")
_PASS_THROUGH_TOKENS = frozenset({"LS", "LANGSUNG"})
_EMPTY_MARKERS = frozenset({"", "-", "--", "—", "–", "LANJUT"})


def normalize_whitespace(value: str | None) -> str:
    """Collapse newlines, tabs, and repeated spaces into single ASCII spaces."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def is_pass_through(raw: str | None) -> bool:
    """Return True when a cell marks a pass-through station ('Ls' / Langsung)."""
    token = normalize_whitespace(raw).upper()
    return token in _PASS_THROUGH_TOKENS


def is_empty_cell(raw: str | None) -> bool:
    """Return True when a timetable cell is blank, dashed, or an unmapped PDF glyph."""
    cleaned = normalize_whitespace(raw)
    return cleaned.upper() in _EMPTY_MARKERS or bool(
        _CID_PLACEHOLDER_PATTERN.match(cleaned)
    )


def normalize_time(raw: str | None) -> str | None:
    """Normalize a raw timetable cell into strict zero-padded HH:MM format.

    Args:
        raw: Raw cell string such as "4:05", "04:28", "04.28", "Ls", or blank.

    Returns:
        Zero-padded "HH:MM" string, or None when the cell is empty or pass-through.

    Raises:
        ValueError: If a non-empty, non-pass-through cell is not a valid 24-hour time.
    """
    cleaned = normalize_whitespace(raw)
    if is_empty_cell(cleaned) or cleaned.upper() in _PASS_THROUGH_TOKENS:
        return None

    match = _TIME_PATTERN.match(cleaned)
    if not match:
        raise ValueError(f"Malformed timetable time value: {raw!r}")

    hour = int(match.group(1))
    minute = int(match.group(2))

    # Some Indonesian timetables represent midnight departures as 24:xx.
    # Normalize 24:xx to 00:xx so all published clock strings stay within 00:00-23:59.
    if hour == 24 and 0 <= minute <= 59:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Out-of-range timetable time value: {raw!r}")

    # Zero-pad single-digit hours (e.g., "4:05" -> "04:05") from raw GAPEKA cells.
    # Downstream clients rely on strict HH:MM lexicographic ordering for schedules.
    return f"{hour:02d}:{minute:02d}"


def time_to_minutes(hhmm: str) -> int:
    """Convert a strict HH:MM time string into integer minutes since 00:00."""
    hour_str, minute_str = hhmm.split(":", 1)
    return int(hour_str) * 60 + int(minute_str)


def adjust_overnight_minutes(times: list[str]) -> list[int]:
    """Convert ordered trip HH:MM strings to monotonic minutes, accounting for midnight.

    Args:
        times: Ordered HH:MM departure/arrival times along a single train trip.

    Returns:
        Minutes since service day start, adding 1440 after a late-night rollover
        (e.g., 23:52 followed by 00:04 -> 1432, 1444).
    """
    if not times:
        return []

    adjusted: list[int] = []
    day_offset = 0
    prev_raw = time_to_minutes(times[0])
    adjusted.append(prev_raw)

    for hhmm in times[1:]:
        current_raw = time_to_minutes(hhmm)

        # Treat a sharp drop from late evening (>= 21:00) into early morning (<= 03:59)
        # as a legitimate midnight rollover rather than a backwards-time error.
        if prev_raw >= 21 * 60 and current_raw <= 4 * 60 and day_offset == 0:
            day_offset = 24 * 60

        adjusted.append(current_raw + day_offset)
        prev_raw = current_raw

    return adjusted
