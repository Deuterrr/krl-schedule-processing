"""Build ordered train stop lists while preserving pass-through (Ls) stations."""

from __future__ import annotations

from collections.abc import Sequence

from krl_schedule.models import StopStatus, StopTime
from krl_schedule.stations import StationRegistry
from krl_schedule.transform.normalizer import (
    is_empty_cell,
    is_pass_through,
    normalize_time,
    normalize_whitespace,
)


def build_stops(
    station_cells: Sequence[tuple[str, str | None]],
    registry: StationRegistry,
    *,
    include_empty: bool = False,
) -> list[StopTime]:
    """Convert ordered (header, cell) pairs into canonical StopTime entries.

    Args:
        station_cells: Sequence of (raw_station_header, raw_cell_value) in table order.
        registry: Canonical station registry resolving codes and uppercase headers.
        include_empty: When True, emit non-served blank columns with status "none".

    Returns:
        Ordered list of StopTime objects preserving original timetable column order.
    """
    stops: list[StopTime] = []

    for header, raw_value in station_cells:
        code = registry.resolve_code(header)
        name = registry.resolve_name(header)

        # Keep "Ls" (Langsung) distinct from blank cells during stop extraction.
        # Collapsing both to empty hides stations the train passes without stopping.
        if is_pass_through(raw_value):
            stops.append(
                StopTime(code=code, name=name, time=None, status=StopStatus.PASS)
            )
            continue

        cleaned = normalize_whitespace(raw_value)
        if is_empty_cell(cleaned):
            if include_empty:
                stops.append(
                    StopTime(code=code, name=name, time=None, status=StopStatus.NONE)
                )
            continue

        normalized_time = normalize_time(cleaned)
        if normalized_time is not None:
            stops.append(
                StopTime(
                    code=code,
                    name=name,
                    time=normalized_time,
                    status=StopStatus.STOP,
                )
            )

    return stops
