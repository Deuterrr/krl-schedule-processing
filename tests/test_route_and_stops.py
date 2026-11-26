"""Unit tests for station alias resolution, route expansion, and stop extraction."""

from krl_schedule.models import StopStatus
from krl_schedule.stations import StationRegistry
from krl_schedule.transform.route import parse_route
from krl_schedule.transform.stops import build_stops


def test_station_registry_resolves_soekarnohatta_uppercase_headers() -> None:
    registry = StationRegistry.from_json()

    assert registry.resolve_code("BANDARA SOEKARNO-HATTA") == "BST"
    assert registry.resolve_name("BANDARA SOEKARNO-HATTA") == "Bandara Soekarno-Hatta"
    assert registry.resolve_code("BNI CITY") == "SUDB"
    assert registry.resolve_name("BNI CITY") == "BNI City"
    assert registry.resolve_code("PDRG") == "PDRG"
    assert registry.resolve_code("JTK") == "JTK"
    assert registry.resolve_code("JMU") == "JMG"
    assert registry.resolve_code("THI") == "TTI"
    assert registry.resolve_code("GRG") == "GGL"
    assert registry.resolve_code("Rangkasbitung\n(RK)") == "RK"
    assert registry.resolve_code("Merak\n(MER)") == "MER"
    assert registry.resolve_code("WONOKROMO (WR)") == "WO"
    assert registry.resolve_name("WONOKROMO (WR)") == "Wonokromo"


def test_parse_route_builds_label_and_structured_route() -> None:
    registry = StationRegistry.from_json()

    label, route = parse_route("Jakk-Kpb-Pse-Ckr", registry)
    assert label == "Jakarta Kota - Cikarang via Kampung Bandan & Pasar Senen"
    assert route.origin == "JAKK"
    assert route.destination == "CKR"
    assert route.via == ["KPB", "PSE"]

    simple_label, simple_route = parse_route("Dp-Jakk", registry)
    assert simple_label == "Depok - Jakarta Kota"
    assert simple_route.origin == "DP"
    assert simple_route.destination == "JAKK"
    assert simple_route.via == []

    endash_label, endash_route = parse_route(
        "Babat–Surabaya Pasar Turi–Sidoarjo", registry
    )
    assert endash_label == "Babat - Sidoarjo via Surabaya Pasar Turi"
    assert endash_route.origin == "BBT"
    assert endash_route.destination == "SDA"
    assert endash_route.via == ["SBI"]

    via_label, via_route = parse_route(
        "Babat-Surabaya Pasar Turi-Mojokerto (via Sepanjang)", registry
    )
    assert via_label == "Babat - Mojokerto via Surabaya Pasar Turi & Sepanjang"
    assert via_route.origin == "BBT"
    assert via_route.destination == "MR"
    assert via_route.via == ["SBI", "SPJ"]


def test_build_stops_preserves_ls_and_handles_empty_flag() -> None:
    registry = StationRegistry.from_json()
    cells = [
        ("CKR", "4:28"),
        ("TLM", ""),
        ("GMR", "Ls"),
        ("BANDARA SOEKARNO-HATTA", "05:57"),
    ]

    compact = build_stops(cells, registry, include_empty=False)
    assert [(s.code, s.name, s.time, s.status) for s in compact] == [
        ("CKR", "Cikarang", "04:28", StopStatus.STOP),
        ("GMR", "Gambir", None, StopStatus.PASS),
        ("BST", "Bandara Soekarno-Hatta", "05:57", StopStatus.STOP),
    ]

    full = build_stops(cells, registry, include_empty=True)
    assert len(full) == 4
    assert full[1].code == "TLM"
    assert full[1].status == StopStatus.NONE
