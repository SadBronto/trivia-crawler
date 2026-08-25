"""
First-party adapter for King Trivia (kingtrivia.com).

Every listing here is tagged SourceType.PROVIDER_SCHEDULE (score 100) -
this is their own live venue locator, not a directory.

How this was reverse-engineered (2026-08-24): the "Where to Play" page
posts to WordPress's admin-ajax.php with action=SearchVenues and a plain
lat/lng (no bounding box, no nonce) and pages 12 results at a time,
nearest-first. Tested expecting to need many scattered query centers like
the Geeks Who Drink adapter does (crawler/us_cities.py exists for that) -
turned out unnecessary: there is NO real radius cap. Paginating a single
query center (New York) all the way out returned the entire nationwide
dataset (311 venues spanning 33 states) - a second center (Los Angeles)
added exactly zero new venues. So this queries a handful of
geographically spread centers purely for redundancy (in case some hidden
per-query result cap exists that just hasn't been hit yet), not because
each one is expected to surface a distinct local set. Dedup is by King
Trivia's own venue_id either way, so querying extra centers costs a few
requests, not correctness.

The response is genuinely rich JSON (not a rendered HTML fragment) -
venue_address, city, state, zip, phone, website, Facebook, a full HTML
description, and - notably - `tz_city`, a real IANA timezone name per
venue (e.g. "America/Los_Angeles"). That sidesteps the timezone-guessing
problem the Geeks Who Drink adapter ran into.

`event_day` is a comma-separated list of "{day 1-7} {HH:MM:SS} {game
type}" entries - ONE venue can carry more than one (e.g. trivia Tuesday
*and* Survey Slam Thursday), which is exactly the venue/event split this
project's schema is built around: one RawListing gets emitted per
day-entry, all pointing at the same venue.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing
from crawler.us_cities import US_METRO_CENTERS

BASE_URL = "https://kingtrivia.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
SOURCE_NAME = "King Trivia"
PAGE_SIZE = 12
MAX_PAGES_PER_CENTER = 40  # safety cap - real pagination stops itself via PAGE_SIZE, this just prevents a runaway loop on unexpected server behavior

# No real radius cap was found (see module docstring) - a handful of
# widely-spread centers is redundancy against a hidden per-query cap, not
# a requirement for coverage. Indices into US_METRO_CENTERS chosen for
# geographic spread (NYC, LA, Chicago, Houston, Seattle, Miami, Anchorage).
DEFAULT_CENTER_NAMES = {"New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Seattle, WA", "Miami, FL", "Anchorage, AK"}

DAY_NUM_TO_NAME = {
    "1": "Sunday",
    "2": "Monday",
    "3": "Tuesday",
    "4": "Wednesday",
    "5": "Thursday",
    "6": "Friday",
    "7": "Saturday",
}

GAME_TYPE_LABELS = {
    "trivia": "Pub Quiz",
    "slam": "Survey Slam",
    "bingo": "Music Bingo",
}


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return text or None


def _venue_listings(venue: dict) -> list[RawListing]:
    listings: list[RawListing] = []

    lat = float(venue["venue_lat"]) if venue.get("venue_lat") else None
    lon = float(venue["venue_lng"]) if venue.get("venue_lng") else None
    hiatus = bool(venue.get("hiatus"))
    description = _strip_html(venue.get("venue_description"))

    event_day = venue.get("event_day")
    day_entries = [e.strip() for e in event_day.split(",")] if event_day else [None]

    for entry in day_entries:
        day_of_week = start_time = None
        game_label = None
        if entry:
            parts = entry.split(" ")
            if len(parts) >= 3:
                day_of_week = DAY_NUM_TO_NAME.get(parts[0])
                start_time = parts[1][:5] if len(parts[1]) >= 5 else parts[1]  # "HH:MM:SS" -> "HH:MM"
                game_code = parts[2]
                game_label = GAME_TYPE_LABELS.get(game_code, game_code)

        notes_parts = []
        if game_label:
            notes_parts.append(f"{game_label}.")
        if hiatus:
            notes_parts.append("Currently listed as on hiatus by King Trivia.")
        if description:
            notes_parts.append(description)
        notes = " ".join(notes_parts) or None

        listings.append(
            RawListing(
                venue_name=(venue.get("venue_name") or "").replace("\\", "").strip(),
                street_address=venue.get("venue_address") or None,
                city=venue.get("venue_city") or None,
                region=venue.get("venue_state") or None,
                country="United States",
                latitude=lat,
                longitude=lon,
                phone=venue.get("venue_phone") or None,
                venue_website=venue.get("venue_url") or None,
                day_of_week=day_of_week,
                start_time=start_time,
                timezone=venue.get("tz_city") or None,  # real IANA name, e.g. "America/Los_Angeles"
                frequency=Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN,
                notes=notes,
                provider_name="King Trivia",
                provider_website=BASE_URL,
                source_url=f"{BASE_URL}/venues/{venue.get('venue_slug', '')}/",
                raw_snapshot={
                    "venue_id": venue.get("venue_id"),
                    "game_code": entry.split(" ")[2] if entry and len(entry.split(" ")) >= 3 else None,
                    "hiatus": hiatus,
                },
            )
        )

    return listings


def _search(session: PoliteSession, lat: float, lon: float, page: int) -> list[dict] | None:
    resp = session.post(
        AJAX_URL,
        data={
            "action": "SearchVenues",
            "lat": lat,
            "lng": lon,
            "gametype": "",
            "dayofweek": "",
            "page": page,
        },
    )
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def run(session: PoliteSession, limit_centers: int | None = None, all_centers: bool = False) -> list[RawListing]:
    if all_centers:
        centers = US_METRO_CENTERS
    else:
        centers = [c for c in US_METRO_CENTERS if c[0] in DEFAULT_CENTER_NAMES]
    if limit_centers:
        centers = centers[:limit_centers]

    seen_venue_ids: set[str] = set()
    all_listings: list[RawListing] = []
    for i, (name, lat, lon) in enumerate(centers, 1):
        center_new = 0
        for page in range(1, MAX_PAGES_PER_CENTER + 1):
            venues = _search(session, lat, lon, page)
            if not venues:
                break
            for venue in venues:
                vid = venue.get("venue_id")
                if vid and vid in seen_venue_ids:
                    continue
                if vid:
                    seen_venue_ids.add(vid)
                all_listings.extend(_venue_listings(venue))
                center_new += 1
            if len(venues) < PAGE_SIZE:
                break
        print(f"  [{i}/{len(centers)}] {name}: {center_new} new venues")

    return all_listings
