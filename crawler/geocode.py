"""
Geocoding helpers - turns a venue's address text into latitude/longitude.

Two providers, matching the free options actually used:
  - US Census Bureau Batch Geocoder: completely free, no API key, no
    signup. US addresses only. Accepts a whole batch (up to 10,000
    addresses) in one request and returns a CSV of matches - by far the
    fastest and cheapest option for US coverage.
  - Mapbox Geocoding API: same token already configured for the live TWC
    site (public `pk.` token, safe to reuse - see TWC Site's geo-utils.js).
    Used for international addresses the Census API can't handle, and as
    a per-address fallback for anything the Census batch didn't match.

Neither of these talks to any of the trivia-source sites this project
scrapes, so they don't go through crawler/http_client.py's PoliteSession -
that's rate-limiting specifically for being polite to small trivia-company
servers, not a general-purpose HTTP concern.
"""

from __future__ import annotations

import csv
import io
import os
import time

import requests

CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CENSUS_BENCHMARK = "Public_AR_Current"
CENSUS_MAX_BATCH_SIZE = 10_000  # hard limit enforced by the API

MAPBOX_GEOCODE_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json"
# The same public `pk.` token already live in triviawriterscoop.com's
# client-side geo-utils.js - anyone viewing that site's page source can
# already see it, so it's not sensitive. Read from an env var anyway
# (not hardcoded here) purely for hygiene: GitHub's push protection
# pattern-matches "looks like a Mapbox token" without distinguishing the
# public `pk.` prefix from a real secret `sk.` one, so hardcoding it
# trips that check on every push regardless of actual sensitivity.
MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN")


def census_batch_geocode(rows: list[tuple[int, str, str, str]]) -> dict[int, tuple[float, float]]:
    """rows: list of (venue_id, street, city, state). Returns {venue_id: (lat, lon)}
    for everything the Census API matched. Silently omits no-matches/ties -
    caller should fall back to Mapbox for those.
    """
    if not rows:
        return {}
    if len(rows) > CENSUS_MAX_BATCH_SIZE:
        raise ValueError(f"Census batch API caps at {CENSUS_MAX_BATCH_SIZE} addresses per request")

    buf = io.StringIO()
    writer = csv.writer(buf)
    for venue_id, street, city, state in rows:
        writer.writerow([venue_id, street, city, state, ""])  # trailing "" = no ZIP on file

    files = {"addressFile": ("batch.csv", buf.getvalue(), "text/csv")}
    data = {"benchmark": CENSUS_BENCHMARK}

    resp = requests.post(CENSUS_BATCH_URL, files=files, data=data, timeout=300)
    resp.raise_for_status()

    results: dict[int, tuple[float, float]] = {}
    reader = csv.reader(io.StringIO(resp.text))
    for row in reader:
        if len(row) < 6:
            continue
        venue_id_str, _input_addr, match_status, _match_type, _matched_addr, coords = row[:6]
        if match_status != "Match":
            continue
        try:
            lon_str, lat_str = coords.split(",")
            results[int(venue_id_str)] = (float(lat_str), float(lon_str))
        except (ValueError, IndexError):
            continue
    return results


def mapbox_reverse_geocode(latitude: float, longitude: float) -> dict | None:
    """Reverse geocode - coordinates back to place names. Returns
    {"city": ..., "region": ..., "country": ...} (any of these can be None
    if Mapbox's response doesn't carry that level for the point), or None on
    a request failure.

    Used to repair venues whose scraped 'city' field is actually garbage -
    a UK postcode, a bare street address, a schedule note like "(2nd
    Tuesday each month)" - some site-specific adapters extracted the wrong
    text into that field. Rather than trying to regex those inconsistent
    formats back into a real city name, deriving it from coordinates
    (already trusted - every venue here has been forward-geocoded already)
    is both more reliable and adapter-agnostic.
    """
    if not MAPBOX_ACCESS_TOKEN:
        raise RuntimeError(
            "MAPBOX_ACCESS_TOKEN is not set. Export it before running "
            "(the same public pk. token used on triviawriterscoop.com)."
        )
    url = MAPBOX_GEOCODE_URL.format(query=f"{longitude},{latitude}")
    resp = requests.get(
        url,
        params={"access_token": MAPBOX_ACCESS_TOKEN, "types": "place,region,country"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    features = data.get("features") or []
    result: dict[str, str] = {}
    for feature in features:
        place_type = (feature.get("place_type") or [None])[0]
        text = feature.get("text")
        if not text:
            continue
        if place_type == "place" and "city" not in result:
            result["city"] = text
        elif place_type == "region" and "region" not in result:
            result["region"] = text
        elif place_type == "country" and "country" not in result:
            result["country"] = text
    return result or None


def mapbox_geocode(query: str) -> tuple[float, float] | None:
    """Single-address geocode via Mapbox. Returns (lat, lon) or None."""
    if not MAPBOX_ACCESS_TOKEN:
        raise RuntimeError(
            "MAPBOX_ACCESS_TOKEN is not set. Export it before running "
            "(the same public pk. token used on triviawriterscoop.com)."
        )
    url = MAPBOX_GEOCODE_URL.format(query=requests.utils.quote(query))
    resp = requests.get(url, params={"access_token": MAPBOX_ACCESS_TOKEN, "limit": 1}, timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    features = data.get("features") or []
    if not features:
        return None
    lon, lat = features[0]["center"]
    return (lat, lon)
