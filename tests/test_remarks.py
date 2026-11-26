"""Unit tests for GAPEKA operational remarks parsing and Full Racket trip linkage."""

from krl_schedule.models import RemarkType
from krl_schedule.transform.remarks import parse_remarks


def test_single_row_full_racket_splits_primary_and_next_leg() -> None:
    result = parse_remarks(
        raw_remarks="Full Racket",
        raw_train_number="5505B - 5506",
        raw_route="Ckr-Mri-Kpb - Kpb-Pse-Bks",
    )
    assert result.train_number == "5505B"
    assert result.route_raw == "Ckr-Mri-Kpb"
    assert result.consumed_next_row is False
    assert result.remarks.type == RemarkType.FULL_RACKET
    assert result.remarks.next_train_number == "5506"
    assert result.remarks.next_route == "Kpb-Pse-Bks"
    assert result.remarks.operates_on_holidays is True


def test_adjacent_row_full_racket_consumes_next_row() -> None:
    result = parse_remarks(
        raw_remarks="Full Racket",
        raw_train_number="6001",
        raw_route="Bks-Pse-Kpb",
        next_train_number="6002",
        next_route="Kpb-Mri-Ckr",
    )
    assert result.train_number == "6001"
    assert result.consumed_next_row is True
    assert result.remarks.next_train_number == "6002"
    assert result.remarks.next_route == "Kpb-Mri-Ckr"


def test_soekarnohatta_hyphenated_train_number_is_not_split() -> None:
    result = parse_remarks(
        raw_remarks="",
        raw_train_number="803A-804A",
        raw_route="BST-DU-MRI",
    )
    assert result.train_number == "803A-804A"
    assert result.route_raw == "BST-DU-MRI"
    assert result.remarks.type == RemarkType.REGULAR
    assert result.remarks.next_train_number == ""


def test_holiday_cancellation_sets_operates_on_holidays_false() -> None:
    result = parse_remarks(
        raw_remarks="Sabtu/Minggu/Libur Nasional Batal",
        raw_train_number="1153F",
        raw_route="Dp-Jakk",
    )
    assert result.remarks.type == RemarkType.HOLIDAY_CANCELLED
    assert result.remarks.operates_on_holidays is False
    assert result.remarks.note == "Cancelled on weekends and national holidays"

    basoetta_result = parse_remarks(
        raw_remarks="Batal Sabtu, Minggu, dan Libur\nNasional",
        raw_train_number="807F-808F",
        raw_route="BST-DU-MRI",
    )
    assert basoetta_result.remarks.type == RemarkType.HOLIDAY_CANCELLED
    assert basoetta_result.remarks.operates_on_holidays is False


def test_partial_service_note_is_classified() -> None:
    result = parse_remarks(
        raw_remarks="Mulai melayani pengguna dari St. Sudirman",
        raw_train_number="5701B",
        raw_route="Sud-Kpb",
    )
    assert result.remarks.type == RemarkType.PARTIAL_SERVICE
    assert result.remarks.note == "Mulai melayani pengguna dari St. Sudirman"
    assert result.remarks.operates_on_holidays is True
