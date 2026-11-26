"""Post-build timetable auditor for station mappings, monotonic times, and dupes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from krl_schedule.models import (
    LineDataset,
    StopStatus,
    ValidationIssue,
    ValidationReport,
)
from krl_schedule.stations import StationRegistry
from krl_schedule.transform.normalizer import adjust_overnight_minutes

logger = logging.getLogger(__name__)


def validate_datasets(
    datasets: list[LineDataset],
    registry: StationRegistry,
) -> ValidationReport:
    """Audit transformed KRL datasets for structural and chronological anomalies.

    Args:
        datasets: Transformed LineDataset objects to inspect.
        registry: Canonical station registry used to flag unmapped station codes.

    Returns:
        ValidationReport aggregating errors and warnings across all datasets.
    """
    logger.info("Validating %d dataset(s)...", len(datasets))
    report = ValidationReport(datasets_checked=len(datasets))

    for dataset in datasets:
        dataset_label = f"{dataset.line}/{dataset.direction}"
        seen_train_numbers: set[str] = set()
        prev_no: int | None = None

        for train in dataset.trains:
            report.trains_checked += 1

            if not train.train_number:
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        dataset=dataset_label,
                        code="MISSING_TRAIN_NUMBER",
                        message=f"Row no={train.no} is missing a train number.",
                    )
                )
            elif train.train_number in seen_train_numbers:
                report.issues.append(
                    ValidationIssue(
                        severity="warning",
                        dataset=dataset_label,
                        train_number=train.train_number,
                        code="DUPLICATE_TRAIN_NUMBER",
                        message=(
                            f"Duplicate train_number {train.train_number!r} "
                            f"within {dataset_label}."
                        ),
                    )
                )
            else:
                seen_train_numbers.add(train.train_number)

            if prev_no is not None and train.no <= prev_no:
                report.issues.append(
                    ValidationIssue(
                        severity="warning",
                        dataset=dataset_label,
                        train_number=train.train_number,
                        code="NON_INCREASING_NO",
                        message=(
                            f"Row number {train.no} does not increase after {prev_no}."
                        ),
                    )
                )
            prev_no = train.no

            route_codes = [
                code
                for code in [
                    train.route.origin,
                    *train.route.via,
                    train.route.destination,
                ]
                if code
            ]
            for code in route_codes:
                if not registry.is_known(code):
                    report.issues.append(
                        ValidationIssue(
                            severity="error",
                            dataset=dataset_label,
                            train_number=train.train_number,
                            code="UNMAPPED_ROUTE_STATION",
                            message=(
                                f"Unmapped station code {code!r} "
                                f"in route {train.route_raw!r}."
                            ),
                        )
                    )

            scheduled_times: list[str] = []
            for stop in train.stops:
                if not registry.is_known(stop.code):
                    report.issues.append(
                        ValidationIssue(
                            severity="error",
                            dataset=dataset_label,
                            train_number=train.train_number,
                            code="UNMAPPED_STOP_STATION",
                            message=f"Unmapped stop station header/code {stop.code!r}.",
                        )
                    )
                if stop.status == StopStatus.STOP and stop.time is not None:
                    scheduled_times.append(stop.time)

            if not scheduled_times:
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        dataset=dataset_label,
                        train_number=train.train_number,
                        code="EMPTY_STOPS",
                        message="Train has zero scheduled stop times.",
                    )
                )
                continue

            # Compare overnight-adjusted minutes so 23:55 -> 00:05 passes while
            # genuine extraction typos (e.g., 05:30 -> 05:10) fail loudly.
            adjusted_minutes = adjust_overnight_minutes(scheduled_times)
            for idx in range(1, len(adjusted_minutes)):
                if adjusted_minutes[idx] < adjusted_minutes[idx - 1]:
                    report.issues.append(
                        ValidationIssue(
                            severity="error",
                            dataset=dataset_label,
                            train_number=train.train_number,
                            code="NON_MONOTONIC_TIME",
                            message=(
                                f"Stop time regressed from {scheduled_times[idx - 1]} "
                                f"to {scheduled_times[idx]}."
                            ),
                        )
                    )
                    break

    logger.info(
        "Validation finished: %d dataset(s), %d train(s), %d error(s), %d warning(s)",
        report.datasets_checked,
        report.trains_checked,
        report.error_count,
        report.warning_count,
    )
    return report


def validate_output_dir(
    output_dir: Path,
    registry: StationRegistry,
) -> ValidationReport:
    """Load exported per-line JSON files from output_dir and run full validation."""
    if not output_dir.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    json_files = sorted(
        path for path in output_dir.glob("*.json") if path.name != "manifest.json"
    )
    logger.info("Loaded %d JSON dataset file(s) from %s", len(json_files), output_dir)
    datasets = [
        LineDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in json_files
    ]
    return validate_datasets(datasets, registry)
