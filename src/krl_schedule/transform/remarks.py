"""Parse GAPEKA operational remarks and Full Racket trip linkages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from krl_schedule.models import Remark, RemarkType
from krl_schedule.transform.normalizer import normalize_whitespace

# Matches both "Sabtu/Minggu/Libur Nasional Batal" and
# "Batal Sabtu, Minggu, dan Libur Nasional" across official GAPEKA sheets.
_HOLIDAY_CANCEL_PATTERN = re.compile(
    r"(?:(sabtu\s*[/,]\s*minggu|libur\s+nasional).*batal"
    r"|batal.*(sabtu\s*[/,]\s*minggu|libur\s+nasional))",
    re.IGNORECASE,
)
_PARTIAL_SERVICE_PATTERN = re.compile(
    r"^(mulai\s+melayani|hanya\s+melayani|tidak\s+melayani)",
    re.IGNORECASE,
)
_SPLIT_DASH_PATTERN = re.compile(r"\s+[-–—]\s+")
_HOLIDAY_CANCEL_NOTE = "Cancelled on weekends and national holidays"


@dataclass(frozen=True)
class RemarksParseResult:
    """Pure result of parsing one timetable row's remarks and paired train fields."""

    remarks: Remark
    train_number: str
    route_raw: str
    consumed_next_row: bool = False


def parse_remarks(
    raw_remarks: str | None,
    raw_train_number: str | None,
    raw_route: str | None,
    next_train_number: str | None = None,
    next_route: str | None = None,
) -> RemarksParseResult:
    """Parse operational remarks and normalize Full Racket dual-leg identifiers.

    Args:
        raw_remarks: Cell text from the GAPEKA remarks (KETERANGAN) column.
        raw_train_number: Train number cell text (may contain "5505B - 5506").
        raw_route: Route cell text (may contain "Ckr-Mri-Kpb - Kpb-Pse-Bks").
        next_train_number: Adjacent row's train number if present for split-row pairs.
        next_route: Adjacent row's route string if present for split-row pairs.

    Returns:
        RemarksParseResult with uniform Remark schema, primary leg identifiers,
        and a flag indicating whether the adjacent continuation row should be skipped.
    """
    norm_raw = normalize_whitespace(raw_remarks)
    train_number = normalize_whitespace(raw_train_number)
    route_raw = normalize_whitespace(raw_route)
    next_train = normalize_whitespace(next_train_number)
    next_route_norm = normalize_whitespace(next_route)

    operates_on_holidays = not bool(_HOLIDAY_CANCEL_PATTERN.search(norm_raw))
    has_full_racket_label = "full racket" in norm_raw.lower()

    # Single-row Full Racket rows combine both legs with spaced hyphens
    # (e.g., "5505B - 5506" and "Ckr-Mri-Kpb - Kpb-Pse-Bks").
    # Require spaced hyphens so Airport Railink numbers like "803A-804A" stay intact.
    split_trains = _SPLIT_DASH_PATTERN.split(train_number)
    split_routes = _SPLIT_DASH_PATTERN.split(route_raw)
    is_single_row_pair = len(split_trains) == 2 and len(split_routes) == 2

    if has_full_racket_label or is_single_row_pair:
        next_leg_train = ""
        next_leg_route = ""
        consumed_next = False

        if is_single_row_pair:
            train_number = split_trains[0].strip()
            route_raw = split_routes[0].strip()
            next_leg_train = split_trains[1].strip()
            next_leg_route = split_routes[1].strip()
        elif next_train:
            # Legacy multi-row Full Racket tables place the continuation leg on the
            # immediately following row; link it and mark that row consumed.
            next_leg_train = next_train
            next_leg_route = next_route_norm
            consumed_next = True

        note = "" if operates_on_holidays else _HOLIDAY_CANCEL_NOTE

        return RemarksParseResult(
            remarks=Remark(
                raw=norm_raw or "Full Racket",
                type=RemarkType.FULL_RACKET,
                note=note,
                operates_on_holidays=operates_on_holidays,
                next_train_number=next_leg_train,
                next_route=next_leg_route,
            ),
            train_number=train_number,
            route_raw=route_raw,
            consumed_next_row=consumed_next,
        )

    if not operates_on_holidays:
        return RemarksParseResult(
            remarks=Remark(
                raw=norm_raw,
                type=RemarkType.HOLIDAY_CANCELLED,
                note=_HOLIDAY_CANCEL_NOTE,
                operates_on_holidays=False,
            ),
            train_number=train_number,
            route_raw=route_raw,
        )

    if _PARTIAL_SERVICE_PATTERN.search(norm_raw):
        return RemarksParseResult(
            remarks=Remark(
                raw=norm_raw,
                type=RemarkType.PARTIAL_SERVICE,
                note=norm_raw,
                operates_on_holidays=True,
            ),
            train_number=train_number,
            route_raw=route_raw,
        )

    if norm_raw:
        return RemarksParseResult(
            remarks=Remark(
                raw=norm_raw,
                type=RemarkType.OTHER,
                note=norm_raw,
                operates_on_holidays=True,
            ),
            train_number=train_number,
            route_raw=route_raw,
        )

    return RemarksParseResult(
        remarks=Remark(
            raw="",
            type=RemarkType.REGULAR,
            note="",
            operates_on_holidays=True,
        ),
        train_number=train_number,
        route_raw=route_raw,
    )
