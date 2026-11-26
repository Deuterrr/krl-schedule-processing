"""Unit tests for timetable text and HH:MM clock normalization helpers."""

import pytest

from krl_schedule.transform.normalizer import (
    adjust_overnight_minutes,
    is_pass_through,
    normalize_time,
    normalize_whitespace,
    time_to_minutes,
)


def test_normalize_whitespace_collapses_newlines_and_tabs() -> None:
    assert normalize_whitespace("  Full\n\t Racket  ") == "Full Racket"
    assert normalize_whitespace(None) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4:05", "04:05"),
        ("04:28", "04:28"),
        ("5.07", "05:07"),
        ("23:59", "23:59"),
        ("24:05", "00:05"),
        ("", None),
        ("  ", None),
        ("-", None),
        ("Ls", None),
        ("ls", None),
        (None, None),
    ],
)
def test_normalize_time_valid_and_empty_values(
    raw: str | None, expected: str | None
) -> None:
    assert normalize_time(raw) == expected


@pytest.mark.parametrize("raw", ["25:00", "12:60", "invalid", "4:5"])
def test_normalize_time_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_time(raw)


def test_is_pass_through_detects_ls_variants() -> None:
    assert is_pass_through("Ls") is True
    assert is_pass_through("  LANGSUNG ") is True
    assert is_pass_through("04:28") is False
    assert is_pass_through("") is False


def test_adjust_overnight_minutes_handles_midnight_rollover() -> None:
    times = ["23:48", "23:55", "00:02", "00:18"]
    adjusted = adjust_overnight_minutes(times)
    assert adjusted == [1428, 1435, 1442, 1458]
    assert time_to_minutes("04:05") == 245
