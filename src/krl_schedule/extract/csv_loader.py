"""Fallback CSV timetable loader supporting both legacy GAPEKA and flat CSV layouts."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from krl_schedule.extract.common import RawTableSection
from krl_schedule.extract.pdf_extractor import extract_sections_from_tables
from krl_schedule.stations import StationRegistry

logger = logging.getLogger(__name__)


def load_csv_section(
    csv_path: Path,
    registry: StationRegistry,
) -> list[RawTableSection]:
    """Load a timetable CSV file into normalized RawTableSection structures.

    Supports both legacy 3-row GAPEKA CSV exports (title row + metadata header +
    station codes row) and flat single-header CSV files.

    Args:
        csv_path: Local path to a KRL timetable CSV file.
        registry: Canonical station registry for resolving column headers.

    Returns:
        List of extracted RawTableSection objects (typically one per CSV file).

    Raises:
        FileNotFoundError: If csv_path does not exist locally.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Timetable CSV not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        matrix: list[list[str | None]] = [list(row) for row in csv.reader(handle)]

    logger.info("Reading CSV %s (%d row(s))", csv_path.name, len(matrix))
    sections = extract_sections_from_tables([matrix], csv_path.name, registry)
    logger.info("Extracted %d section(s) from %s", len(sections), csv_path.name)
    return sections
