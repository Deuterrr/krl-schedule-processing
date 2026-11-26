"""Shared raw table structures, line/direction inference, and debug CSV dumping."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from krl_schedule.stations import StationRegistry

_FILENAME_DIRECTION_PATTERN = re.compile(r"(from|to)[-_ ]+([a-z0-9]+)", re.IGNORECASE)
_TITLE_PATTERN = re.compile(
    r"commuter\s+line\s+([a-z0-9 ]+?)\s+tujuan\s+([a-z0-9/\- ]+)",
    re.IGNORECASE,
)


@dataclass
class RawTableRow:
    """Single unnormalized timetable row extracted from a PDF grid or CSV file."""

    no: str
    train_number: str
    route_raw: str
    station_values: list[str]
    remarks: str = ""


@dataclass
class RawTableSection:
    """Stitched sequence of raw rows belonging to one KRL line and direction."""

    line: str
    direction: str
    source_file: str
    station_headers: list[str]
    rows: list[RawTableRow] = field(default_factory=list)


def _index_of_any(codes: list[str], candidates: tuple[str, ...]) -> int | None:
    """Return the first index where any candidate code appears in codes."""
    for candidate in candidates:
        if candidate in codes:
            return codes.index(candidate)
    return None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unknown"


def infer_line_direction(
    station_headers: list[str],
    source_stem: str,
    registry: StationRegistry,
    title_hint: str = "",
) -> tuple[str, str]:
    """Infer canonical (line, direction) from resolved station column order.

    Args:
        station_headers: Ordered station header tokens from the extracted table.
        source_stem: Source filename without extension, used as fallback hint.
        registry: Canonical station registry for normalizing header tokens to codes.
        title_hint: Optional table title banner (e.g. "Jadwal Commuter Line ...").

    Returns:
        Tuple of (line, direction) such as ("cikarang", "from_cikarang").
    """
    codes = [registry.resolve_code(header) for header in station_headers]

    # Separate standalone Lintas Nambo sheets (NMO without BOO) from the full Bogor line
    # so both PDFs can coexist in data/raw/ without colliding on bogor_from_bogor.json.
    if "NMO" in codes and "BOO" not in codes and "JAKK" in codes:
        direction = (
            "from_nambo" if codes.index("NMO") < codes.index("JAKK") else "to_nambo"
        )
        return "nambo", direction

    # Check regional Merak corridor before Rangkasbitung because both include RK.
    if any("MER" in code for code in codes) and "RK" in codes:
        mer_idx = next(i for i, c in enumerate(codes) if c == "MER" or "(MER)" in c)
        rk_idx = codes.index("RK")
        direction = "from_merak" if mer_idx < rk_idx else "to_merak"
        return "merak", direction

    # Identify Jabodetabek/Basoetta line and direction from unique corridor endpoints.
    # Combined multi-line GAPEKA PDFs split cleanly without relying on page titles.
    corridors: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
        ("soekarnohatta", ("BST",), ("MRI", "SUDB", "DU", "BOO")),
        ("tanjungpriok", ("TPK",), ("JAKK", "KPB")),
        ("tangerang", ("TNG", "TTI", "PI"), ("DU", "GGL")),
        ("rangkasbitung", ("RK", "MJ", "PRP", "SRP"), ("THB", "PLM")),
        ("cikarang", ("CKR", "TLM", "BKS", "JNG"), ("MRI", "KPB", "AK", "JAKK")),
        ("bogor", ("NMO", "BOO", "DP", "CTA"), ("JAKK", "MRI")),
    ]

    for line, outer_ends, inner_ends in corridors:
        outer_idx = _index_of_any(codes, outer_ends)
        inner_idx = _index_of_any(codes, inner_ends)
        if outer_idx is not None and inner_idx is not None:
            direction = f"from_{line}" if outer_idx < inner_idx else f"to_{line}"
            return line, direction

    # Fallback for regional KCI lines (e.g., Walahar, Garut, Supas, Arjonegoro).
    if title_hint:
        # Stop before the English subtitle ("Timetable of ...") even when pdfplumber
        # collapsed the newline between Indonesian and English header lines.
        id_title = re.split(
            r"\btimetable\b", title_hint, maxsplit=1, flags=re.IGNORECASE
        )[0]
        title_match = _TITLE_PATTERN.search(id_title.strip())
        if title_match:
            line_slug = _slugify(title_match.group(1))
            dest_slug = _slugify(title_match.group(2).split("/")[0])
            return line_slug, f"to_{dest_slug}"

    match = _FILENAME_DIRECTION_PATTERN.search(source_stem)
    if match:
        prefix = match.group(1).lower()
        line_slug = match.group(2).lower()
        return line_slug, f"{prefix}_{line_slug}"

    return "unknown", _slugify(source_stem)


def dump_section_csv(section: RawTableSection, debug_dir: Path) -> Path:
    """Write a raw extracted section to CSV for visual troubleshooting."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    out_path = debug_dir / f"{section.line}_{section.direction}.csv"
    headers = ["NO", "TRAIN_NUMBER", "ROUTE", *section.station_headers, "REMARKS"]

    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in section.rows:
            writer.writerow(
                [
                    row.no,
                    row.train_number,
                    row.route_raw,
                    *row.station_values,
                    row.remarks,
                ]
            )

    return out_path
