"""Pydantic v2 schemas defining the canonical KRL timetable data contract."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RemarkType(StrEnum):
    """Classifies operational remarks parsed from the GAPEKA remarks column."""

    REGULAR = "regular"
    FULL_RACKET = "full_racket"
    HOLIDAY_CANCELLED = "holiday_cancelled"
    PARTIAL_SERVICE = "partial_service"
    OTHER = "other"


class StopStatus(StrEnum):
    """Classifies how a train interacts with a station along its timetable."""

    # Train makes a scheduled stop with a published HH:MM time.
    STOP = "stop"

    # Train passes through without stopping ("Ls" / Langsung, e.g., Gambir).
    PASS = "pass"

    # Station column is empty and not served by this train (optional full-grid mode).
    NONE = "none"


class Station(BaseModel):
    """Canonical station entry loaded from data/stations.json."""

    code: str
    name: str
    lines: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class RouteInfo(BaseModel):
    """Structured origin, destination, and intermediate via station codes."""

    origin: str = ""
    destination: str = ""
    via: list[str] = Field(default_factory=list)


class Remark(BaseModel):
    """Fixed-schema operational metadata for a single train trip."""

    raw: str = ""
    type: RemarkType = RemarkType.REGULAR
    note: str = ""

    # False when the timetable cancels the train on weekends and national holidays.
    # Derived from notes like "Sabtu/Minggu/Libur Nasional Batal".
    operates_on_holidays: bool = True

    # Return-leg train number and raw route for Full Racket loop services.
    # Kept present as empty strings on non-loop trains for uniform JSON keys.
    next_train_number: str = ""
    next_route: str = ""


class StopTime(BaseModel):
    """Single station entry in a train's ordered stop sequence."""

    code: str
    name: str
    time: str | None = None  # Strict HH:MM or None
    status: StopStatus


class TrainSchedule(BaseModel):
    """Normalized timetable record for one KRL Commuter Line train trip."""

    no: int
    train_number: str
    line: str
    direction: str
    route_raw: str
    route_label: str
    route: RouteInfo
    remarks: Remark
    stops: list[StopTime]


class LineDataset(BaseModel):
    """Complete timetable collection for one KRL line and direction."""

    line: str
    direction: str
    source_file: str
    trains: list[TrainSchedule]


class DatasetManifestEntry(BaseModel):
    """Summary metadata for a single exported line/direction JSON file."""

    line: str
    direction: str
    file: str
    train_count: int
    source_file: str


class BuildManifest(BaseModel):
    """Top-level manifest written alongside exported timetable JSON files."""

    schema_version: str = "2.0.0"
    generated_at: datetime
    source_hashes: dict[str, str] = Field(default_factory=dict)  # filename -> SHA-256
    datasets: list[DatasetManifestEntry] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """Single anomaly reported by the post-build timetable auditor."""

    severity: str  # "error" or "warning"
    dataset: str
    train_number: str = ""
    code: str
    message: str


class ValidationReport(BaseModel):
    """Aggregated audit result across all processed line datasets."""

    datasets_checked: int = 0
    trains_checked: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0
