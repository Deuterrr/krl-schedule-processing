"""Integration tests for PDF stitching, CSV loading, exporters, validation, and CLI."""

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from krl_schedule.cli import app
from krl_schedule.extract.pdf_extractor import extract_sections_from_tables
from krl_schedule.models import LineDataset, StopStatus, StopTime
from krl_schedule.pipeline import build_datasets, transform_section
from krl_schedule.stations import StationRegistry
from krl_schedule.validate import validate_datasets

FIXTURES_DIR = Path(__file__).parent / "fixtures"
runner = CliRunner()


def test_multipage_and_multiline_table_stitching() -> None:
    registry = StationRegistry.from_json()

    # Simulate a combined multi-line GAPEKA PDF:
    # Page 1 & 2 = Cikarang (repeated 2-row merged header + continuation page),
    # Page 3 = Tanjung Priok (distinct station header starts a new section).
    tables = [
        [
            ["NO.", "NOMOR KA", "RELASI", "STASIUN", None, None, "KETERANGAN"],
            [None, None, None, "CKR", "BKS", "MRI", None],
            ["1", "5027B", "Ckr-Mri", "04:12", "4:42", "05:11", ""],
        ],
        [
            ["2", "5029B", "Ckr-Mri", "04:34", "4:56", "05:24", ""],
        ],
        [
            ["NO.", "NOMOR KA", "RELASI", "TPK", "AC", "KPB", "JAKK", "KETERANGAN"],
            ["1", "2203", "Tpk-Jakk", "05:30", "05:38", "05:42", "05:46", ""],
        ],
    ]

    sections = extract_sections_from_tables(tables, "gapeka_2026.pdf", registry)
    assert len(sections) == 2

    cikarang, priok = sections
    assert (cikarang.line, cikarang.direction) == ("cikarang", "from_cikarang")
    assert len(cikarang.rows) == 2
    assert (priok.line, priok.direction) == ("tanjungpriok", "from_tanjungpriok")
    assert len(priok.rows) == 1


def test_end_to_end_cli_build_validate_and_sqlite_query(tmp_path: Path) -> None:
    out_dir = tmp_path / "output"

    build_res = runner.invoke(
        app,
        [
            "build",
            "--input",
            str(FIXTURES_DIR),
            "--output",
            str(out_dir),
            "--formats",
            "json,sqlite",
            "--dump-raw-csv",
            "--strict",
        ],
    )
    assert build_res.exit_code == 0, build_res.stdout

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "2.0.0"
    assert len(manifest["datasets"]) == 2
    assert (out_dir / "_debug" / "cikarang_from_cikarang.csv").exists()

    basoetta = LineDataset.model_validate(
        json.loads(
            (out_dir / "soekarnohatta_from_soekarnohatta.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert basoetta.trains[0].stops[0].code == "BST"
    assert basoetta.trains[0].stops[0].name == "Bandara Soekarno-Hatta"
    assert basoetta.trains[0].stops[0].time == "05:57"

    # Verify SQLite query works for downstream schedule lookups.
    with sqlite3.connect(out_dir / "krl_schedule.db") as conn:
        rows = conn.execute(
            """
            SELECT t.train_number, s.departure_time
            FROM trains t
            JOIN stop_times s ON s.train_id = t.id
            WHERE s.station_code = 'MRI' AND s.status = 'stop'
            ORDER BY s.minutes_since_midnight ASC
            """
        ).fetchall()
    assert rows[0] == ("5701B", "04:15")
    assert any(train_no == "5599F" and dep == "23:55" for train_no, dep in rows)

    val_res = runner.invoke(app, ["validate", str(out_dir)])
    assert val_res.exit_code == 0, val_res.stdout

    inspect_res = runner.invoke(
        app,
        ["inspect", str(out_dir / "cikarang_from_cikarang.json"), "--limit", "3"],
    )
    assert inspect_res.exit_code == 0, inspect_res.stdout
    assert "5505B" in inspect_res.stdout


def test_validator_flags_unmapped_station_and_non_monotonic_time() -> None:
    registry = StationRegistry.from_json()
    datasets, _ = build_datasets(FIXTURES_DIR / "cikarang_sample.csv", registry)
    dataset = datasets[0]

    # Inject a backwards clock jump and an unknown station code to verify detection.
    broken = dataset.model_copy(deep=True)
    broken.trains[1].stops = [
        StopTime(code="CKR", name="Cikarang", time="05:30", status=StopStatus.STOP),
        StopTime(code="XYZ", name="Unknown", time="05:10", status=StopStatus.STOP),
    ]

    report = validate_datasets([broken], registry)
    codes = {issue.code for issue in report.issues}
    assert report.is_valid is False
    assert "UNMAPPED_STOP_STATION" in codes
    assert "NON_MONOTONIC_TIME" in codes


def test_transform_section_handles_pass_through_and_overnight() -> None:
    registry = StationRegistry.from_json()
    tables = [
        [
            ["NO", "NOMOR KA", "RELASI", "CKR", "BKS", "MRI", "JAKK", "KETERANGAN"],
            [
                "1",
                "5505B - 5506",
                "Ckr-Mri-Kpb - Kpb-Pse-Bks",
                "23:45",
                "23:58",
                "00:15",
                "Ls",
                "Full Racket",
            ],
        ]
    ]
    section = extract_sections_from_tables(tables, "sample.pdf", registry)[0]
    dataset = transform_section(section, registry)
    report = validate_datasets([dataset], registry)
    assert report.is_valid is True
    assert dataset.trains[0].stops[-1].status == StopStatus.PASS


def test_mid_table_stacked_and_side_by_side_gapeka_2026_layouts() -> None:
    registry = StationRegistry.from_json()

    # Stacked directions inside a single table (Lintas Nambo GAPEKA 2026 layout).
    stacked = [
        [
            ["NO.", "NOMOR KA", "RELASI", "STASIUN", None, None],
            [None, None, None, "NMO", "CBN", "JAKK"],
            ["1", "1501", "Nmo - Jakk", "04:55", "05:06", "06:24"],
            ["NO.", "NOMOR KA", "RELASI", "STASIUN", None, None],
            [None, None, None, "JAKK", "CBN", "NMO"],
            ["1", "1500", "Jakk - Nmo", "06:40", "07:55", "08:06"],
        ]
    ]
    nambo_sections = extract_sections_from_tables(stacked, "nambo.pdf", registry)
    assert [(s.line, s.direction, len(s.rows)) for s in nambo_sections] == [
        ("nambo", "from_nambo", 1),
        ("nambo", "to_nambo", 1),
    ]

    # Side-by-side dual tables on one row (Tanjung Priok GAPEKA 2026 layout).
    side_by_side = [
        [
            [
                "",
                "NO.",
                "NOMOR KA",
                "RELASI",
                "STASIUN",
                None,
                "KETERANGAN",
                "",
                "NO.",
                "NOMOR KA",
                "RELASI",
                "STASIUN",
                None,
                "KETERANGAN",
            ],
            [
                None,
                None,
                None,
                None,
                "JAKK",
                "TPK",
                None,
                None,
                None,
                None,
                None,
                "TPK",
                "JAKK",
                None,
            ],
            [
                "",
                "1",
                "2200",
                "Jakk - Tpk",
                "05:00",
                "05:17",
                "",
                "",
                "1",
                "2203",
                "Tpk - Jakk",
                "05:30",
                "05:48",
                "",
            ],
        ]
    ]
    priok_sections = extract_sections_from_tables(side_by_side, "priok.pdf", registry)
    assert [(s.line, s.direction, s.rows[0].train_number) for s in priok_sections] == [
        ("tanjungpriok", "to_tanjungpriok", "2200"),
        ("tanjungpriok", "from_tanjungpriok", "2203"),
    ]

    # Basoetta 2-column merged NOMOR KA + trailing empty column past KETERANGAN.
    basoetta = [
        [
            [
                None,
                "NO.",
                "NOMOR KA",
                None,
                "RELASI",
                "STASIUN",
                None,
                "KETERANGAN",
                None,
            ],
            [
                None,
                None,
                None,
                None,
                None,
                "BANDARA\nSOEKARNO-HATTA",
                "MANGGARAI",
                None,
                None,
            ],
            [
                "",
                "1",
                "807F",
                "808F",
                "BST-DU-MRI",
                "06:27",
                "07:13",
                "Batal Sabtu, Minggu, dan Libur\nNasional",
                None,
            ],
        ]
    ]
    basoetta_section = extract_sections_from_tables(basoetta, "basoetta.pdf", registry)[
        0
    ]
    basoetta_ds = transform_section(basoetta_section, registry)
    assert basoetta_ds.trains[0].train_number == "807F-808F"
    assert basoetta_ds.trains[0].remarks.operates_on_holidays is False


def test_process_logging_emits_info_and_verbose_debug(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    out_dir = tmp_path / "output"

    with caplog.at_level(logging.DEBUG, logger="krl_schedule"):
        res_info = runner.invoke(
            app,
            [
                "build",
                "--input",
                str(FIXTURES_DIR),
                "--output",
                str(out_dir),
                "--formats",
                "json,sqlite",
                "--dump-raw-csv",
            ],
        )
    assert res_info.exit_code == 0, res_info.stdout
    info_messages = [rec.getMessage() for rec in caplog.records]
    assert any("Discovered 2 input file(s)" in msg for msg in info_messages)
    assert any("Reading CSV cikarang_sample.csv" in msg for msg in info_messages)
    assert any("Transformed cikarang/from_cikarang:" in msg for msg in info_messages)
    assert any(
        "Wrote 2 JSON dataset(s) + manifest.json" in msg for msg in info_messages
    )
    assert any("Wrote SQLite database" in msg for msg in info_messages)
    assert any("Validation finished:" in msg for msg in info_messages)
    assert all(rec.levelno >= logging.INFO for rec in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="krl_schedule"):
        res_verbose = runner.invoke(
            app,
            [
                "build",
                "--input",
                str(FIXTURES_DIR),
                "--output",
                str(out_dir),
                "--formats",
                "json",
                "--dump-raw-csv",
                "--verbose",
            ],
        )
    assert res_verbose.exit_code == 0, res_verbose.stdout
    debug_messages = [
        rec.getMessage() for rec in caplog.records if rec.levelno == logging.DEBUG
    ]
    assert any("Dumped raw CSV" in msg for msg in debug_messages)
    assert any("Wrote cikarang_from_cikarang.json" in msg for msg in debug_messages)
