"""Extract and stitch multi-page GAPEKA timetable grids from official PDFs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from krl_schedule.extract.common import (
    RawTableRow,
    RawTableSection,
    infer_line_direction,
)
from krl_schedule.stations import StationRegistry
from krl_schedule.transform.normalizer import normalize_whitespace

logger = logging.getLogger(__name__)

# GAPEKA timetable grids use solid vector rules for every cell boundary.
# Tight text_y_tolerance prevents pdfplumber from interleaving stacked 2-line headers
# (e.g., "PURWAKARTA\n(PWK)") on dense 30+ column regional sheets.
GAPEKA_LINE_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "text_y_tolerance": 1.0,
    "text_x_tolerance": 1.5,
}

_SUBHEADER_TOKENS = frozenset({"DATANG", "BERANGKAT", "STASIUN", "WAKTU"})


@dataclass(frozen=True)
class _ParsedHeader:
    """Column layout detected from a 1-row or 2-row merged GAPEKA table header."""

    header_height: int
    table_width: int
    no_idx: int
    train_indices: list[int]
    route_idx: int
    station_indices: list[int]
    station_headers: list[str]
    remarks_idx: int | None
    title_hint: str = ""


def _clean_row(raw_row: list[str | None]) -> list[str]:
    return [normalize_whitespace(cell) for cell in raw_row]


def _is_no_header(token: str) -> bool:
    norm = token.upper().replace(".", "").strip()
    return norm == "NO"


def _is_train_header(token: str) -> bool:
    norm = token.upper().replace("_", " ").strip()
    return norm in {"NOMOR KA", "NO KA", "KA", "NOMOR", "TRAIN NUMBER"}


def _is_route_header(token: str) -> bool:
    return token.upper().strip() in {"RELASI", "ROUTE"}


def _is_remarks_header(token: str) -> bool:
    norm = token.upper().strip()
    return norm in {"KETERANGAN", "KET", "KET.", "REMARKS"}


def _is_title_banner(row: list[str]) -> bool:
    non_empty = [cell for cell in row if cell]
    if not non_empty:
        return False
    first_upper = non_empty[0].upper()
    return first_upper.startswith(("JADWAL ", "TIMETABLE "))


def _extract_title_hint(
    rows: list[list[str]], row_idx: int, start_col: int, end_col: int
) -> str:
    """Grab an adjacent title banner above row_idx within [start_col, end_col)."""
    if row_idx <= 0:
        return ""
    prev_row = rows[row_idx - 1]
    slice_cells = [
        prev_row[i]
        for i in range(start_col, min(end_col, len(prev_row)))
        if prev_row[i]
    ]
    if slice_cells:
        return slice_cells[0]
    for cell in prev_row:
        if cell.upper().startswith(("JADWAL ", "TIMETABLE ")):
            return cell
    return ""


def _detect_headers_at_row(
    rows: list[list[str]],
    row_idx: int,
) -> tuple[list[_ParsedHeader], int]:
    """Detect one or more horizontal GAPEKA header blocks starting at row_idx.

    Supports single-table sheets, vertically stacked mid-table direction splits
    (e.g., Lintas Nambo, Merak), and side-by-side dual tables (e.g., Tanjung Priok).
    """
    row = rows[row_idx]
    no_positions = [i for i, cell in enumerate(row) if _is_no_header(cell)]
    if not no_positions:
        return [], 0

    headers: list[_ParsedHeader] = []
    max_height = 0

    for pos_idx, no_idx in enumerate(no_positions):
        block_end = (
            no_positions[pos_idx + 1] if pos_idx + 1 < len(no_positions) else len(row)
        )

        train_idx = next(
            (i for i in range(no_idx + 1, block_end) if _is_train_header(row[i])),
            None,
        )
        route_idx = next(
            (i for i in range(no_idx + 1, block_end) if _is_route_header(row[i])),
            None,
        )
        if train_idx is None or route_idx is None or route_idx <= train_idx:
            continue

        # Basoetta merges "NOMOR KA" across two train-number columns (e.g. 809 | 810).
        # Only absorb blank cells directly after train_idx so "NAMA KA" stays excluded.
        train_indices = [train_idx]
        cursor = train_idx + 1
        while cursor < route_idx and not row[cursor]:
            train_indices.append(cursor)
            cursor += 1

        remarks_idx = next(
            (i for i in range(route_idx + 1, block_end) if _is_remarks_header(row[i])),
            None,
        )
        station_end = remarks_idx if remarks_idx is not None else block_end
        candidate_indices = list(range(route_idx + 1, station_end))
        if not candidate_indices:
            continue

        title_hint = _extract_title_hint(rows, row_idx, no_idx, block_end)

        # Standard 2-row merged GAPEKA PDF layout: station codes sit on row_idx + 1.
        next_row = rows[row_idx + 1] if row_idx + 1 < len(rows) else None
        if next_row is not None and len(next_row) > route_idx + 1:
            next_end = min(station_end, len(next_row))
            if (
                remarks_idx is None
                and len(next_row) > block_end
                and pos_idx + 1 == len(no_positions)
            ):
                next_end = len(next_row)

            next_candidates = list(range(route_idx + 1, next_end))
            next_meta_blank = not next_row[no_idx] and not next_row[train_idx]
            station_indices = [i for i in next_candidates if next_row[i]]

            if next_meta_blank and station_indices:
                station_headers = [next_row[i] for i in station_indices]
                headers.append(
                    _ParsedHeader(
                        header_height=2,
                        table_width=len(row),
                        no_idx=no_idx,
                        train_indices=train_indices,
                        route_idx=route_idx,
                        station_indices=station_indices,
                        station_headers=station_headers,
                        remarks_idx=remarks_idx,
                        title_hint=title_hint,
                    )
                )
                max_height = max(max_height, 2)
                continue

        # Fallback: single-row flat header layout.
        station_indices = [i for i in candidate_indices if row[i]]
        if station_indices:
            station_headers = [row[i] for i in station_indices]
            headers.append(
                _ParsedHeader(
                    header_height=1,
                    table_width=len(row),
                    no_idx=no_idx,
                    train_indices=train_indices,
                    route_idx=route_idx,
                    station_indices=station_indices,
                    station_headers=station_headers,
                    remarks_idx=remarks_idx,
                    title_hint=title_hint,
                )
            )
            max_height = max(max_height, 1)

    return headers, max_height


def _is_data_row(row: list[str], header: _ParsedHeader) -> bool:
    """Filter out blank lines, title banners, and sub-header rows (Datang/Berangkat)."""
    required_col = max(header.no_idx, header.route_idx, *header.train_indices)
    if len(row) <= required_col:
        return False

    if _is_title_banner(row):
        return False

    no_val = row[header.no_idx]
    train_parts = [row[i] for i in header.train_indices if row[i]]
    route_val = row[header.route_idx]

    if _is_no_header(no_val) or any(_is_train_header(p) for p in train_parts):
        return False

    station_vals = [row[i] for i in header.station_indices if i < len(row) and row[i]]
    if not train_parts and not route_val:
        return False

    # Reject regional sub-header rows like "Datang | Berangkat" or repeated headers.
    if any(val.upper() in _SUBHEADER_TOKENS for val in station_vals):
        return False

    return bool(train_parts or route_val or station_vals)


def _row_to_raw(row: list[str], header: _ParsedHeader) -> RawTableRow:
    train_parts = [row[i] for i in header.train_indices if i < len(row) and row[i]]
    train_number = "-".join(train_parts)

    station_vals = [row[i] if i < len(row) else "" for i in header.station_indices]
    remarks_val = (
        row[header.remarks_idx]
        if header.remarks_idx is not None and header.remarks_idx < len(row)
        else ""
    )
    return RawTableRow(
        no=row[header.no_idx],
        train_number=train_number,
        route_raw=row[header.route_idx],
        station_values=station_vals,
        remarks=remarks_val,
    )


def extract_sections_from_tables(
    tables: list[list[list[str | None]]],
    source_file: str,
    registry: StationRegistry,
) -> list[RawTableSection]:
    """Stitch extracted page tables into per-line/direction RawTableSection objects.

    Args:
        tables: Sequence of raw cell matrices extracted across PDF pages.
        source_file: Display filename of the source PDF for traceability.
        registry: StationRegistry used to resolve headers and infer line/direction.

    Returns:
        List of stitched RawTableSection instances with repeated headers removed.
    """
    source_stem = Path(source_file).stem
    sections: list[RawTableSection] = []
    sections_by_signature: dict[tuple[str, ...], RawTableSection] = {}
    active_blocks: list[tuple[RawTableSection, _ParsedHeader]] = []

    for raw_table in tables:
        cleaned_rows = [_clean_row(row) for row in raw_table if row]
        if not cleaned_rows:
            continue

        # Reset active blocks when a new page introduces a title banner or changes
        # column width (prevents transposed regional tables bleeding into prior pages).
        if active_blocks and (
            _is_title_banner(cleaned_rows[0])
            or len(cleaned_rows[0]) != active_blocks[0][1].table_width
        ):
            active_blocks = []

        row_idx = 0
        while row_idx < len(cleaned_rows):
            if _is_title_banner(cleaned_rows[row_idx]):
                active_blocks = []
                row_idx += 1
                continue

            headers, header_height = _detect_headers_at_row(cleaned_rows, row_idx)
            if headers:
                new_blocks: list[tuple[RawTableSection, _ParsedHeader]] = []
                for header in headers:
                    signature = tuple(
                        registry.resolve_code(token) for token in header.station_headers
                    )
                    existing = sections_by_signature.get(signature)
                    if existing is not None:
                        section = existing
                    else:
                        line, direction = infer_line_direction(
                            header.station_headers,
                            source_stem,
                            registry,
                            title_hint=header.title_hint,
                        )
                        section = RawTableSection(
                            line=line,
                            direction=direction,
                            source_file=source_file,
                            station_headers=header.station_headers,
                        )
                        sections.append(section)
                        sections_by_signature[signature] = section
                    new_blocks.append((section, header))

                active_blocks = new_blocks
                row_idx += header_height
                continue

            if active_blocks:
                row = cleaned_rows[row_idx]
                for section, header in active_blocks:
                    if _is_data_row(row, header):
                        section.rows.append(_row_to_raw(row, header))

            row_idx += 1

    return [section for section in sections if section.rows]


def extract_pdf_sections(
    pdf_path: Path,
    registry: StationRegistry,
) -> list[RawTableSection]:
    """Extract all timetable sections from an official GAPEKA PDF file.

    Args:
        pdf_path: Local path to a single-line or combined multi-line GAPEKA PDF.
        registry: Canonical station registry for resolving column headers.

    Returns:
        List of stitched RawTableSection objects found in the PDF.

    Raises:
        FileNotFoundError: If pdf_path does not exist locally.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"GAPEKA PDF not found: {pdf_path}")

    extracted_tables: list[list[list[str | None]]] = []
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        logger.info("Reading PDF %s (%d page(s))", pdf_path.name, total_pages)
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables(GAPEKA_LINE_TABLE_SETTINGS)
            if not tables:
                # Fall back to pdfplumber defaults when a page uses shaded rows
                # without explicit vector grid borders.
                logger.debug(
                    "%s page %d/%d: no ruled tables, falling back to default extractor",
                    pdf_path.name,
                    page_num,
                    total_pages,
                )
                tables = page.extract_tables()
            non_empty = [table for table in tables if table]
            logger.debug(
                "%s page %d/%d: extracted %d table(s)",
                pdf_path.name,
                page_num,
                total_pages,
                len(non_empty),
            )
            extracted_tables.extend(non_empty)

    sections = extract_sections_from_tables(extracted_tables, pdf_path.name, registry)
    logger.info(
        "Stitched %d table(s) into %d section(s) from %s",
        len(extracted_tables),
        len(sections),
        pdf_path.name,
    )
    return sections
