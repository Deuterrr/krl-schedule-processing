"""End-to-end timetable ingestion and transformation pipeline orchestrator."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from krl_schedule.extract import (
    RawTableSection,
    dump_section_csv,
    extract_pdf_sections,
    load_csv_section,
)
from krl_schedule.models import LineDataset, TrainSchedule
from krl_schedule.stations import StationRegistry
from krl_schedule.transform import build_stops, parse_remarks, parse_route

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".csv"})
logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_row_number(raw_no: str, fallback: int) -> int:
    digits = "".join(ch for ch in raw_no if ch.isdigit())
    return int(digits) if digits else fallback


def transform_section(
    section: RawTableSection,
    registry: StationRegistry,
    *,
    include_empty_stops: bool = False,
) -> LineDataset:
    """Transform a raw extracted table section into a validated LineDataset.

    Args:
        section: Stitched raw rows for one KRL line and direction.
        registry: Canonical station registry for code/name resolution.
        include_empty_stops: Include non-served blank columns when True.

    Returns:
        Validated LineDataset containing normalized TrainSchedule records.
    """
    trains: list[TrainSchedule] = []
    skip_next = False

    for idx, row in enumerate(section.rows):
        if skip_next:
            skip_next = False
            continue

        next_row = section.rows[idx + 1] if idx + 1 < len(section.rows) else None
        remarks_result = parse_remarks(
            raw_remarks=row.remarks,
            raw_train_number=row.train_number,
            raw_route=row.route_raw,
            next_train_number=next_row.train_number if next_row else None,
            next_route=next_row.route_raw if next_row else None,
        )
        if remarks_result.consumed_next_row:
            skip_next = True

        route_label, route = parse_route(remarks_result.route_raw, registry)
        station_cells = list(
            zip(section.station_headers, row.station_values, strict=False)
        )
        stops = build_stops(
            station_cells,
            registry,
            include_empty=include_empty_stops,
        )

        train = TrainSchedule(
            no=_parse_row_number(row.no, len(trains) + 1),
            train_number=remarks_result.train_number,
            line=section.line,
            direction=section.direction,
            route_raw=remarks_result.route_raw,
            route_label=route_label,
            route=route,
            remarks=remarks_result.remarks,
            stops=stops,
        )
        trains.append(train)

    logger.info(
        "Transformed %s/%s: %d train(s)",
        section.line,
        section.direction,
        len(trains),
    )
    return LineDataset(
        line=section.line,
        direction=section.direction,
        source_file=section.source_file,
        trains=trains,
    )


def discover_input_files(input_path: Path) -> list[Path]:
    """Collect sorted timetable files (.pdf, .csv) from a file or directory."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported input file type: {input_path.name}")
        return [input_path]

    files = sorted(
        path
        for path in input_path.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    return files


def build_datasets(
    input_path: Path,
    registry: StationRegistry,
    *,
    include_empty_stops: bool = False,
    dump_raw_dir: Path | None = None,
) -> tuple[list[LineDataset], dict[str, str]]:
    """Run extraction and transformation across all timetable files under input_path.

    Args:
        input_path: Path to a GAPEKA PDF/CSV file or a directory of input files.
        registry: Canonical station registry.
        include_empty_stops: Emit blank non-route station entries when True.
        dump_raw_dir: Optional folder where raw extracted CSV tables are written.

    Returns:
        Tuple of (list of LineDataset, mapping of source filename -> SHA-256 digest).
    """
    input_files = discover_input_files(input_path)
    logger.info("Discovered %d input file(s) in %s", len(input_files), input_path)
    source_hashes: dict[str, str] = {}
    sections: list[RawTableSection] = []

    total_files = len(input_files)
    for idx, file_path in enumerate(input_files, start=1):
        logger.info("[%d/%d] Processing %s", idx, total_files, file_path.name)
        source_hashes[file_path.name] = _sha256_file(file_path)
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            extracted = extract_pdf_sections(file_path, registry)
        else:
            extracted = load_csv_section(file_path, registry)

        for section in extracted:
            if dump_raw_dir is not None:
                dumped_path = dump_section_csv(section, dump_raw_dir)
                logger.debug("Dumped raw CSV -> %s", dumped_path)
            sections.append(section)

    datasets = [
        transform_section(section, registry, include_empty_stops=include_empty_stops)
        for section in sections
    ]
    return datasets, source_hashes
