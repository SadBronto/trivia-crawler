"""
First-party adapter for DJ Trivia (djtrivia.com).

Data-gathering only, per explicit instruction: the user has a professional
disagreement with this company as both a trivia writer and host. Getting
their data into the DB doesn't commit to using it anywhere - that's a
separate decision for later. Tagged SourceType.PROVIDER_SCHEDULE (score
100) like every other first-party adapter; nothing about ingestion status
implies endorsement.

How this was found: the "Find a Location" page (/locations/) has a
classic HTML <form method="get">, not an AJAX widget - submitting it just
reloads the page with ?where=X&distance=Y&day=Z in the URL, and the
server renders the results directly into a `var venues = {...}` JS
object embedded in the page (found via `Object.keys(window)` in a live
browser - venues was sitting right there). That means no endpoint
reverse-engineering was needed at all: just GET /locations/ with query
params and regex the embedded JSON back out.

`where` accepts free-text ("Seattle, WA" tested directly, not just ZIP
codes) - the server geocodes it itself (confirmed via the echoed `Geo`
lat/lng in the response) - so this reuses the same US_METRO_CENTERS list
as the other adapters rather than needing a ZIP code list.

`distance` caps at 50 (miles) in the site's own UI - unlike King Trivia's
endpoint, this one has a real, working radius limit (confirmed: querying
Seattle at 50mi genuinely returns zero venues, not a fallback blob), so
real geographic tiling across many centers is necessary for coverage,
not just redundancy.

No robots.txt exists on this domain (confirmed 404) - nothing stated to
follow either way, so this proceeds under the general polite-crawling
default (identify honestly, rate-limit, respect any future robots.txt).
"""

from __future__ import annotations

import json
import re

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing
from crawler.us_cities import US_METRO_CENTERS

BASE_URL = "https://djtrivia.com"
LOCATIONS_URL = f"{BASE_URL}/locations/"
SOURCE_NAME = "DJ Trivia"
MAX_DISTANCE_MILES = 50

_VENUES_JSON_RE = re.compile(r"var venues\s*=\s*(\{.*?\});", re.DOTALL)

_TIME_RE = re.compile(r"(\d{1,2}):?(\d{2})?\s*([AP])\.?M\.?", re.IGNORECASE)


def _to_24h(time_str: str) -> str | None:
    m = _TIME_RE.search(time_str)
    if not m:
        return None
    hour = int(m.group(1))
    minute = m.group(2) or "00"
    meridiem = m.group(3).upper()
    if meridiem == "P" and hour != 12:
        hour += 12
    if meridiem == "A" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _query(session: PoliteSession, where: str) -> list[dict]:
    resp = session.get(LOCATIONS_URL, params={"where": where, "distance": MAX_DISTANCE_MILES, "day": ""})
    if resp is None:
        return []
    m = _VENUES_JSON_RE.search(resp.text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return data.get("Venues", [])


def _venue_to_listing(v: dict) -> RawListing:
    return RawListing(
        venue_name=v.get("Name", "").strip(),
        street_address=v.get("Address") or None,
        city=v.get("City") or None,
        region=v.get("State") or None,
        country="United States",
        latitude=float(v["Latitude"]) if v.get("Latitude") else None,
        longitude=float(v["Longitude"]) if v.get("Longitude") else None,
        phone=v.get("Phone") or None,
        day_of_week=v.get("Day") or None,
        start_time=_to_24h(v.get("Time", "")) if v.get("Time") else None,
        frequency=Frequency.WEEKLY if v.get("Day") else Frequency.UNKNOWN,
        provider_name="DJ Trivia",
        provider_website=BASE_URL,
        source_url=f"{LOCATIONS_URL}?where={v.get('Zip', '')}",
        raw_snapshot={"venue_id": v.get("ID")},
    )


def run(session: PoliteSession, limit_centers: int | None = None) -> list[RawListing]:
    centers = US_METRO_CENTERS[:limit_centers] if limit_centers else US_METRO_CENTERS

    seen_ids: set = set()
    all_listings: list[RawListing] = []
    for i, (name, _lat, _lon) in enumerate(centers, 1):
        venues = _query(session, name)
        fresh = 0
        for v in venues:
            vid = v.get("ID")
            if vid and vid in seen_ids:
                continue
            if vid:
                seen_ids.add(vid)
            all_listings.append(_venue_to_listing(v))
            fresh += 1
        print(f"  [{i}/{len(centers)}] {name}: {len(venues)} found, {fresh} new")

    return all_listings
