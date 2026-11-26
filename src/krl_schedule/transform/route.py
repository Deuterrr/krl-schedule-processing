"""Transform hyphenated GAPEKA route strings into readable labels and station codes."""

from __future__ import annotations

import re

from krl_schedule.models import RouteInfo
from krl_schedule.stations import StationRegistry
from krl_schedule.transform.normalizer import normalize_whitespace

_DASH_SPLIT_PATTERN = re.compile(r"[-–—]")
_TRAILING_VIA_PATTERN = re.compile(r"\s*\(via\s+([^)]+)\)\s*$", re.IGNORECASE)


def parse_route(
    route_raw: str | None,
    registry: StationRegistry,
) -> tuple[str, RouteInfo]:
    """Build a human-readable route label and structured RouteInfo.

    Args:
        route_raw: Dash-separated station string (e.g., "Ckr-Mri-Kpb" or
            "Babat–Surabaya Pasar Turi–Sidoarjo").
        registry: Canonical station registry used to expand codes/names.

    Returns:
        Tuple of (route_label, RouteInfo). Returns ("", empty RouteInfo) when blank.
    """
    cleaned = normalize_whitespace(route_raw)
    if not cleaned:
        return "", RouteInfo()

    extra_via_token = ""
    via_match = _TRAILING_VIA_PATTERN.search(cleaned)
    if via_match:
        extra_via_token = normalize_whitespace(via_match.group(1))
        cleaned = cleaned[: via_match.start()].strip()

    raw_tokens = [
        part.strip() for part in _DASH_SPLIT_PATTERN.split(cleaned) if part.strip()
    ]
    if not raw_tokens:
        return "", RouteInfo()

    codes = [registry.resolve_code(token) for token in raw_tokens]
    names = [registry.resolve_name(token) for token in raw_tokens]

    if len(codes) == 1:
        return names[0], RouteInfo(origin=codes[0], destination=codes[0], via=[])

    via_codes = list(codes[1:-1])
    via_names = list(names[1:-1])

    if extra_via_token:
        via_codes.append(registry.resolve_code(extra_via_token))
        via_names.append(registry.resolve_name(extra_via_token))

    route = RouteInfo(origin=codes[0], destination=codes[-1], via=via_codes)

    if not via_names:
        return f"{names[0]} - {names[-1]}", route

    via_label = " & ".join(via_names)
    return f"{names[0]} - {names[-1]} via {via_label}", route
