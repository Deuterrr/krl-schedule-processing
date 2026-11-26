"""Command-line interface for building, validating, and inspecting KRL schedules."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from krl_schedule.export import export_json, export_sqlite
from krl_schedule.models import LineDataset, StopStatus, ValidationReport
from krl_schedule.pipeline import build_datasets
from krl_schedule.stations import DEFAULT_STATIONS_PATH, StationRegistry
from krl_schedule.validate import validate_datasets, validate_output_dir

app = typer.Typer(
    name="krl-schedule",
    help="Convert GAPEKA KRL timetable PDFs and CSVs into JSON and SQLite.",
    no_args_is_help=True,
)
console = Console()
# Use explicit package name so `python -m krl_schedule.cli` still routes logs here.
logger = logging.getLogger("krl_schedule.cli")


def _setup_logging(verbose: bool = False) -> None:
    """Attach a concise stderr handler scoped to krl_schedule loggers."""
    # Scope the handler to krl_schedule so pdfminer internals stay quiet,
    # while leaving propagation enabled for pytest caplog assertions.
    pkg_logger = logging.getLogger("krl_schedule")
    pkg_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not pkg_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        pkg_logger.addHandler(handler)


def _render_validation_report(report: ValidationReport) -> None:
    """Display a concise summary and issue breakdown using Rich tables."""
    console.print(
        f"[bold]Validation Summary:[/bold] {report.datasets_checked} dataset(s), "
        f"{report.trains_checked} train(s), "
        f"[red]{report.error_count} error(s)[/red], "
        f"[yellow]{report.warning_count} warning(s)[/yellow]"
    )

    if not report.issues:
        return

    table = Table(title="Validation Issues", show_lines=False)
    table.add_column("Severity", style="bold")
    table.add_column("Dataset")
    table.add_column("Train")
    table.add_column("Code")
    table.add_column("Message")

    for issue in report.issues[:50]:
        color = "red" if issue.severity == "error" else "yellow"
        table.add_row(
            f"[{color}]{issue.severity.upper()}[/{color}]",
            issue.dataset,
            issue.train_number or "-",
            issue.code,
            issue.message,
        )

    console.print(table)
    if len(report.issues) > 50:
        console.print(f"... and {len(report.issues) - 50} more issue(s).")


@app.command()
def build(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            "-i",
            help="Path to a GAPEKA PDF/CSV file or directory containing inputs.",
        ),
    ] = Path("data/raw"),
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Directory where generated JSON and/or SQLite outputs are written.",
        ),
    ] = Path("data/output"),
    formats: Annotated[
        str,
        typer.Option(
            "--formats",
            "-f",
            help="Comma-separated export formats: json, sqlite.",
        ),
    ] = "json,sqlite",
    stations_path: Annotated[
        Path,
        typer.Option(
            "--stations",
            help="Path to canonical stations.json registry.",
        ),
    ] = DEFAULT_STATIONS_PATH,
    include_empty_stops: Annotated[
        bool,
        typer.Option(
            "--include-empty-stops",
            help="Retain blank non-served station columns with status='none'.",
        ),
    ] = False,
    dump_raw_csv: Annotated[
        bool,
        typer.Option(
            "--dump-raw-csv",
            help="Write raw extracted tables to <output>/_debug/ for inspection.",
        ),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit with code 1 if validation reports any error.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed per-page and per-file debug logs.",
        ),
    ] = False,
) -> None:
    """Extract, transform, validate, and export GAPEKA schedules."""
    _setup_logging(verbose=verbose)

    selected_formats = {
        fmt.strip().lower() for fmt in formats.split(",") if fmt.strip()
    }
    invalid_formats = selected_formats - {"json", "sqlite"}
    if invalid_formats:
        console.print(
            f"[red]Unsupported format(s):[/red] {', '.join(sorted(invalid_formats))}"
        )
        raise typer.Exit(code=2)

    registry = StationRegistry.from_json(stations_path)
    logger.info("Loaded %d stations from %s", len(registry.all_stations), stations_path)
    debug_dir = output_dir / "_debug" if dump_raw_csv else None

    datasets, source_hashes = build_datasets(
        input_path,
        registry,
        include_empty_stops=include_empty_stops,
        dump_raw_dir=debug_dir,
    )

    if not datasets:
        console.print(
            f"[yellow]No timetable tables found under {input_path}.[/yellow] "
            "Drop official GAPEKA PDF(s) or CSV(s) into data/raw/ and retry."
        )
        raise typer.Exit(code=1)

    if "json" in selected_formats:
        export_json(datasets, output_dir, source_hashes)
        console.print(
            f"[green]Exported {len(datasets)} JSON dataset(s)[/green] -> {output_dir}"
        )

    if "sqlite" in selected_formats:
        db_path = export_sqlite(datasets, registry, output_dir / "krl_schedule.db")
        console.print(f"[green]Exported SQLite database[/green] -> {db_path}")

    report = validate_datasets(datasets, registry)
    _render_validation_report(report)

    if strict and not report.is_valid:
        raise typer.Exit(code=1)


@app.command()
def validate(
    output_dir: Annotated[
        Path,
        typer.Argument(help="Directory containing exported per-line JSON files."),
    ] = Path("data/output"),
    stations_path: Annotated[
        Path,
        typer.Option("--stations", help="Path to canonical stations.json registry."),
    ] = DEFAULT_STATIONS_PATH,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit with code 1 when validation errors exist."),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show detailed debug logs."),
    ] = False,
) -> None:
    """Validate exported KRL JSON timetable files against schema and domain rules."""
    _setup_logging(verbose=verbose)
    registry = StationRegistry.from_json(stations_path)
    logger.info("Loaded %d stations from %s", len(registry.all_stations), stations_path)
    report = validate_output_dir(output_dir, registry)
    _render_validation_report(report)

    if strict and not report.is_valid:
        raise typer.Exit(code=1)


@app.command()
def inspect(
    json_file: Annotated[
        Path,
        typer.Argument(help="Path to an exported per-line JSON schedule file."),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of train rows to display."),
    ] = 10,
) -> None:
    """Preview an exported JSON timetable in a formatted terminal table."""
    if not json_file.exists():
        console.print(f"[red]File not found:[/red] {json_file}")
        raise typer.Exit(code=1)

    dataset = LineDataset.model_validate(
        json.loads(json_file.read_text(encoding="utf-8"))
    )
    table = Table(
        title=f"{dataset.line} / {dataset.direction} ({len(dataset.trains)} trains)",
        show_lines=False,
    )
    table.add_column("No", justify="right")
    table.add_column("Train", style="cyan")
    table.add_column("Route")
    table.add_column("Type", style="magenta")
    table.add_column("First Stop")
    table.add_column("Last Stop")
    table.add_column("Stops", justify="right")

    for train in dataset.trains[:limit]:
        scheduled = [s for s in train.stops if s.status == StopStatus.STOP and s.time]
        first = f"{scheduled[0].code} {scheduled[0].time}" if scheduled else "-"
        last = f"{scheduled[-1].code} {scheduled[-1].time}" if scheduled else "-"
        table.add_row(
            str(train.no),
            train.train_number,
            train.route_label,
            train.remarks.type.value,
            first,
            last,
            str(len(scheduled)),
        )

    console.print(table)


if __name__ == "__main__":
    app()
