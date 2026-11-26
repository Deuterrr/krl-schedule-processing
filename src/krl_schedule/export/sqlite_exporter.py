"""Export validated KRL schedules into an indexed, queryable SQLite database."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from krl_schedule.models import LineDataset, StopStatus
from krl_schedule.stations import StationRegistry
from krl_schedule.transform.normalizer import adjust_overnight_minutes

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE stations (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lines_json TEXT NOT NULL
);

CREATE TABLE trains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    no INTEGER NOT NULL,
    train_number TEXT NOT NULL,
    line TEXT NOT NULL,
    direction TEXT NOT NULL,
    route_raw TEXT NOT NULL,
    route_label TEXT NOT NULL,
    origin_code TEXT NOT NULL,
    destination_code TEXT NOT NULL,
    via_json TEXT NOT NULL,
    remarks_raw TEXT NOT NULL,
    remarks_type TEXT NOT NULL,
    remarks_note TEXT NOT NULL,
    operates_on_holidays INTEGER NOT NULL,
    next_train_number TEXT NOT NULL,
    next_route TEXT NOT NULL,
    source_file TEXT NOT NULL
);

CREATE TABLE stop_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    train_id INTEGER NOT NULL REFERENCES trains(id),
    stop_sequence INTEGER NOT NULL,
    station_code TEXT NOT NULL,
    station_name TEXT NOT NULL,
    departure_time TEXT,
    minutes_since_midnight INTEGER,
    status TEXT NOT NULL
);

CREATE INDEX idx_trains_line_direction ON trains(line, direction);
CREATE INDEX idx_trains_number ON trains(train_number);
CREATE INDEX idx_stop_times_station_minutes
    ON stop_times(station_code, minutes_since_midnight);
CREATE INDEX idx_stop_times_train_seq ON stop_times(train_id, stop_sequence);
"""


def export_sqlite(
    datasets: list[LineDataset],
    registry: StationRegistry,
    db_path: Path,
) -> Path:
    """Build a fresh SQLite schedule database with stations, trains, and stop_times.

    Args:
        datasets: Validated LineDataset objects to insert.
        registry: Canonical station registry populating the stations table.
        db_path: Destination SQLite file path (overwritten cleanly if present).

    Returns:
        Path to the generated SQLite database file.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    train_count = 0
    stop_count = 0
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)

        stations = registry.all_stations
        conn.executemany(
            "INSERT INTO stations (code, name, lines_json) VALUES (?, ?, ?)",
            [
                (station.code, station.name, json.dumps(station.lines))
                for station in stations
            ],
        )

        for dataset in datasets:
            for train in dataset.trains:
                train_count += 1
                cursor = conn.execute(
                    """
                    INSERT INTO trains (
                        no, train_number, line, direction, route_raw, route_label,
                        origin_code, destination_code, via_json,
                        remarks_raw, remarks_type, remarks_note,
                        operates_on_holidays, next_train_number, next_route,
                        source_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        train.no,
                        train.train_number,
                        train.line,
                        train.direction,
                        train.route_raw,
                        train.route_label,
                        train.route.origin,
                        train.route.destination,
                        json.dumps(train.route.via),
                        train.remarks.raw,
                        train.remarks.type.value,
                        train.remarks.note,
                        1 if train.remarks.operates_on_holidays else 0,
                        train.remarks.next_train_number,
                        train.remarks.next_route,
                        dataset.source_file,
                    ),
                )
                train_id = cursor.lastrowid

                # Precompute overnight-adjusted minutes for scheduled stops so SQL
                # range queries stay accurate past 00:00.
                stop_hhmm = [
                    stop.time
                    for stop in train.stops
                    if stop.status == StopStatus.STOP and stop.time is not None
                ]
                adjusted_iter = iter(adjust_overnight_minutes(stop_hhmm))

                stop_rows = []
                for seq, stop in enumerate(train.stops, start=1):
                    minutes = (
                        next(adjusted_iter)
                        if stop.status == StopStatus.STOP and stop.time is not None
                        else None
                    )
                    stop_rows.append(
                        (
                            train_id,
                            seq,
                            stop.code,
                            stop.name,
                            stop.time,
                            minutes,
                            stop.status.value,
                        )
                    )

                stop_count += len(stop_rows)
                conn.executemany(
                    """
                    INSERT INTO stop_times (
                        train_id, stop_sequence, station_code, station_name,
                        departure_time, minutes_since_midnight, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    stop_rows,
                )

        conn.commit()

    logger.info(
        "Wrote SQLite database (%d stations, %d trains, %d stop_times) -> %s",
        len(stations),
        train_count,
        stop_count,
        db_path,
    )
    return db_path
