"""
First-party adapter for Quiz Meisters (quizmeisters.com) - the largest
Australia-wide source targeted so far (spec: "national coverage across
all Australian states and territories").

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100).

How this was found: the "Find a Show" widget on their Webflow homepage
is a third-party store-locator SaaS embed (StoreRocket -
cdn.storerocket.io), identified by a `storerocket-search-{accountId}`
CSS class on the widget's root div. StoreRocket's own JS library
(cdn.storerocket.io/js/search/v1.js) builds its API calls from
`{baseUrl}/user/{accountId}/...` - reverse-engineered the exact shape by
reading that file rather than guessing, since a wrong guess
(api.storerocket.io instead of the correct storerocket.io) initially
failed silently. `.../user/{accountId}/locations` returns the complete
dataset in one request: 237 locations, no pagination, no auth beyond the
public account ID that's already embedded in the page's own HTML.

Real lat/lng, structured address, real IANA timezone per venue, and
their own multi-brand game types (Trivia/Bingo/Survey Says - matching
the category dropdown seen on the live "Find a Show" widget) all present
directly. Day/time isn't in a dedicated field though - it's a custom
"fields" array per location with free-text values like "Thursday 7:00pm"
(field name = game type), parsed here with a simple day+time regex.
Multi-night venues (confirmed: a handful run both Trivia and Survey Says
on different nights) show up as multiple fields, mapped 1:1 to this
project's one-RawListing-per-night pattern.
"""

from __future__ import annotations

import re

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://quizmeisters.com"
STOREROCKET_ACCOUNT_ID = "kDJ3BbK4mn"
API_URL = f"https://storerocket.io/api/user/{STOREROCKET_ACCOUNT_ID}/locations"
SOURCE_NAME = "Quiz Meisters"

_DAY_TIME_RE = re.compile(
    r"(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)s?\s+(\d{1,2})[:.]?(\d{1,2})?\s*(am|pm)",
    re.IGNORECASE,
)

COUNTRY_CODE_MAP = {"AU": "Australia", "NZ": "New Zealand"}


def _to_24h(hour: str, minute: str | None, meridiem: str) -> str:
    h = int(hour)
    m = f"{int(minute):02d}" if minute else "00"  # source data has typos like "7:0pm" - zero-pad rather than emit invalid "19:0"
    if meridiem.lower() == "pm" and h != 12:
        h += 12
    if meridiem.lower() == "am" and h == 12:
        h = 0
    return f"{h:02d}:{m}"


def _location_to_listings(loc: dict) -> list[RawListing]:
    venue_name = (loc.get("name") or "").strip()
    if not venue_name:
        return []

    country = COUNTRY_CODE_MAP.get(loc.get("country"), loc.get("country"))
    lat, lng = loc.get("lat"), loc.get("lng")

    fields = loc.get("fields") or []
    if not fields:
        return [
            RawListing(
                venue_name=venue_name,
                street_address=loc.get("address_line_1") or None,
                city=loc.get("city") or None,
                region=loc.get("state") or None,
                country=country,
                latitude=float(lat) if lat else None,
                longitude=float(lng) if lng else None,
                phone=loc.get("phone") or None,
                venue_website=loc.get("url") or None,
                timezone=loc.get("timezone") or None,
                frequency=Frequency.UNKNOWN,
                provider_name="Quiz Meisters",
                provider_website=BASE_URL,
                source_url=loc.get("url"),
                raw_snapshot={"storerocket_id": loc.get("id")},
            )
        ]

    listings = []
    for field in fields:
        game_type = field.get("name")
        value = field.get("pivot_field_value") or ""
        m = _DAY_TIME_RE.search(value)
        day_of_week = start_time = None
        if m:
            day_of_week = m.group(1).capitalize()
            start_time = _to_24h(m.group(2), m.group(3), m.group(4))

        listings.append(
            RawListing(
                venue_name=venue_name,
                street_address=loc.get("address_line_1") or None,
                city=loc.get("city") or None,
                region=loc.get("state") or None,
                country=country,
                latitude=float(lat) if lat else None,
                longitude=float(lng) if lng else None,
                phone=loc.get("phone") or None,
                venue_website=loc.get("url") or None,
                day_of_week=day_of_week,
                start_time=start_time,
                timezone=loc.get("timezone") or None,
                frequency=Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN,
                notes=f"{game_type} night." if game_type and game_type != "Trivia" else None,
                provider_name="Quiz Meisters",
                provider_website=BASE_URL,
                source_url=loc.get("url"),
                raw_snapshot={"storerocket_id": loc.get("id"), "raw_field_value": value},
            )
        )
    return listings


def run(session: PoliteSession) -> list[RawListing]:
    resp = session.get(API_URL)
    if resp is None:
        print("[QuizMeisters] request failed")
        return []
    data = resp.json()
    locations = (data.get("results") or {}).get("locations", [])
    print(f"[QuizMeisters] {len(locations)} locations found")

    all_listings: list[RawListing] = []
    for loc in locations:
        all_listings.extend(_location_to_listings(loc))
    return all_listings
