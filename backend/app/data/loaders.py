"""Loaders for Waypoint's bundled static data (`backend/data/`).

Slice 2 ships `iata_country.csv` (airport IATA -> ISO-2 country) and
`iata_city.csv` (airport IATA -> display city). These replace Slice 1's
hardcoded DEMO_IATA map: country/city now travel on the wire instead of
being duplicated in the frontend. Slice 3 adds `transit_hubs.yaml` +
`passport_index.csv` loaders here, per 03-program-design.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

# loaders.py -> app/data -> app -> backend (the directory holding `data/`).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _BACKEND_ROOT / "data"


def _load_csv(filename: str, key_col: str, value_col: str) -> dict[str, str]:
    """Read a two-column CSV into a dict, skipping blank/malformed rows."""
    out: dict[str, str] = {}
    with open(DATA_DIR / filename, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row.get(key_col) or "").strip().upper()
            value = (row.get(value_col) or "").strip()
            if key and value:
                out[key] = value
    return out


@lru_cache(maxsize=1)
def load_iata_country() -> dict[str, str]:
    """Airport IATA -> ISO-2 country. Feeds `Offer.layovers()`."""
    return _load_csv("iata_country.csv", "iata", "iso2")


@lru_cache(maxsize=1)
def load_iata_city() -> dict[str, str]:
    """Airport IATA -> display city name. Carried on `Layover.city`."""
    return _load_csv("iata_city.csv", "iata", "city")


@lru_cache(maxsize=1)
def load_iso3_to_iso2() -> dict[str, str]:
    """MRZ ISO-3 nationality -> ISO-2 (curated). FAIL-CLOSED: unmapped
    ISO-3 codes return None from the caller — never free text."""
    return _load_csv("iso3_to_iso2.csv", "iso3", "iso2")
