"""Canonical KRL station registry and header/code alias resolution."""

from __future__ import annotations

import json
import re
from pathlib import Path

from krl_schedule.models import Station

DEFAULT_STATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.json"
_PAREN_HEADER_PATTERN = re.compile(r"^(.*?)\s*\(([A-Za-z0-9]{2,5})\)\s*$")


def _normalize_token(raw: str) -> str:
    """Normalize station codes and PDF headers for case-insensitive lookup."""
    collapsed = re.sub(r"\s+", " ", raw).strip().upper()
    return collapsed


class StationRegistry:
    """Resolves station codes, names, and irregular PDF headers to canonical entries."""

    def __init__(self, stations: list[Station]) -> None:
        self._stations = stations
        self._by_code: dict[str, Station] = {}
        self._lookup: dict[str, Station] = {}

        for station in stations:
            canonical_code = _normalize_token(station.code)
            self._by_code[canonical_code] = station
            self._lookup[canonical_code] = station
            self._lookup[_normalize_token(station.name)] = station

            # Register both explicit aliases and hyphen-free variants to handle
            # line-wrapped or uppercase headers (e.g., Soekarno-Hatta sheets).
            for alias in station.aliases:
                norm_alias = _normalize_token(alias)
                self._lookup[norm_alias] = station
                self._lookup[norm_alias.replace("-", " ")] = station

    @classmethod
    def from_json(cls, path: Path | None = None) -> StationRegistry:
        """Load the canonical station registry from a JSON file.

        Args:
            path: Optional custom path to stations.json. Defaults to data/stations.json.

        Returns:
            Populated StationRegistry ready for code and alias lookups.
        """
        target = path or DEFAULT_STATIONS_PATH
        raw_items = json.loads(target.read_text(encoding="utf-8"))
        stations = [Station.model_validate(item) for item in raw_items]
        return cls(stations)

    @property
    def all_stations(self) -> list[Station]:
        return list(self._stations)

    def resolve(self, token: str) -> Station | None:
        """Resolve a station code, name, or PDF header alias to a canonical Station."""
        norm = _normalize_token(token)
        if not norm:
            return None

        match = self._lookup.get(norm)
        if match is not None:
            return match

        match = self._lookup.get(norm.replace("-", " "))
        if match is not None:
            return match

        # Regional KCI PDFs format headers as "Station Name (CODE)".
        # Check the station name first so PDF code typos (e.g. "WONOKROMO (WR)")
        # still map to Wonokromo (WO) rather than Waru (WR).
        paren_match = _PAREN_HEADER_PATTERN.match(norm)
        if paren_match:
            name_part = paren_match.group(1).strip()
            code_part = paren_match.group(2).strip()
            if name_part:
                name_hit = self._lookup.get(name_part) or self._lookup.get(
                    name_part.replace("-", " ")
                )
                if name_hit is not None:
                    return name_hit
            return self._lookup.get(code_part)

        return None

    def resolve_code(self, token: str) -> str:
        """Return canonical uppercase station code, or normalized input if unmapped."""
        station = self.resolve(token)
        if station is not None:
            return station.code
        return _normalize_token(token)

    def resolve_name(self, token: str) -> str:
        """Return canonical display name, or trimmed input if unmapped."""
        station = self.resolve(token)
        if station is not None:
            return station.name
        return re.sub(r"\s+", " ", token).strip()

    def is_known(self, token: str) -> bool:
        """Return True when the token matches any registered station code or alias."""
        return self.resolve(token) is not None
