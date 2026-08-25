"""
PAUSED / KNOWN INCOMPLETE (2026-08-24): geekswhodrink.com sits behind
Cloudflare bot protection, and a burst of manual testing (probing bbox
sizes to find where server-side filtering actually kicks in - see below)
tripped it mid-session ("Sorry, you have been blocked"). Stopped rather
than try to route around an active block. What's below is real and
verified working through the polite, rate-limited client for small
queries - it is NOT yet safe to run as a full 50-state crawl, because the
geographic tiling this code does is too coarse (see caveat below) and a
finer-grained version would mean many more requests than tested so far.
Before running this for real: re-verify from a fresh session, slow the
pace down further, and shrink the tile size (see docstring below for why).

First-party adapter for Geeks Who Drink (geekswhodrink.com).

This is their own live venue locator, not a directory - every listing is
tagged SourceType.PROVIDER_SCHEDULE (score 100), and includes GWD's own
sub-brands (Classic, Small Batch, Jeopardy! Bar League, Bingo), all run
by the same parent company.

How this was reverse-engineered (2026-08-24): the "Find a Game" page
(/venues/) loads a Backbone.js widget (wp-content/plugins/motherbrain/)
that queries WordPress's admin-ajax.php with action=mb_display_mapped_events
and a lat/lon bounding box, returning a server-rendered HTML fragment (not
JSON) of venue cards. robots.txt explicitly allows /wp-admin/admin-ajax.php
even though the rest of /wp-admin/ is disallowed - this is clearly meant
to be called by client-side JS, which is exactly what we're doing, just
without a browser. The `nonce` param is a WordPress security nonce, not a
secret - it comes back embedded in the plain HTML of any page load
(window.WP_URL.gwdNonce) and works from a fresh, cookie-less request.

A single nationwide bounding box caps out at exactly 1024 results (a
server-side limit, not the real count) - so this tiles by US state
(crawler/us_states.py) to get full coverage.

IMPORTANT caveat found during testing: the server only actually filters
by the bounds[...] params for a SMALL box (confirmed working: a ~0.4deg
box around Seattle correctly returned only nearby venues). Anything
state-sized or larger silently ignores the bounds and falls back to
returning the same generic ~900-1024 "starter set" regardless of where
you queried - state-level tiling as currently written does NOT give real
per-state coverage, it just wastes 50 requests re-fetching the same
blob. Getting genuine full-US coverage needs a much finer grid (roughly
degree-or-smaller tiles, likely low hundreds to ~1000 tiles for the
continental US) with podio-ID dedup across tiles, and a correspondingly
more conservative rate limit given Cloudflare's presence. Not implemented
yet - see the PAUSED note above.

Known caveat: GWD renders one timestamp format as a precise ISO-8601
instant in UTC (<time data-time="...">) with no per-venue timezone
information anywhere in the response. Converting that to a correct local
start_time requires knowing the venue's timezone, which this adapter
doesn't attempt (see the project's deferred geocoding pass) - so those
rows store the UTC clock time verbatim with timezone="UTC" tagged
explicitly, rather than silently guessing wrong. The other format GWD
uses ("2nd Wednesday at 6:30 pm", already-local plain text for monthly/
twice-monthly games) is used as-is with timezone left unknown.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing
from crawler.us_states import US_STATE_BOUNDS

BASE_URL = "https://www.geekswhodrink.com"
VENUES_PAGE_URL = f"{BASE_URL}/venues/"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
SOURCE_NAME = "Geeks Who Drink"

BRAND_LABELS = {
    "gwd": "GWD Classic",
    "sbt": "Small Batch Trivia",
    "jbl": "Jeopardy! Bar League",
    "bng": "GWD Bingo",
}

_ORDINAL_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"\b(1st|2nd|3rd|4th|5th)\b")


def _get_nonce(session: PoliteSession) -> str | None:
    resp = session.get(VENUES_PAGE_URL)
    if resp is None:
        return None
    m = re.search(r'"gwdNonce":"([a-f0-9]+)"', resp.text)
    return m.group(1) if m else None


def _query_bounds(
    session: PoliteSession, nonce: str, north: float, south: float, west: float, east: float
) -> bytes | None:
    params = {
        "action": "mb_display_mapped_events",
        "bounds[northLat]": north,
        "bounds[southLat]": south,
        "bounds[westLong]": west,
        "bounds[eastLong]": east,
        "days": "",
        "brands": "",
        "nonce": nonce,
        "search": "",
        "startLat": (north + south) / 2,
        "startLong": (west + east) / 2,
        "searchInit": "true",
        "hasAll": "true",
    }
    resp = session.get(AJAX_URL, params=params)
    return resp.content if resp is not None else None


def _parse_time(block) -> tuple[str | None, str | None, Frequency]:
    """Returns (start_time 24h "HH:MM" or None, timezone or None, frequency)."""
    time_moment = block.select_one("span.time-moment")
    if time_moment and time_moment.get("data-time"):
        # ISO instant, UTC (see module docstring caveat).
        m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})", time_moment["data-time"])
        if m:
            return f"{m.group(1)}:{m.group(2)}", "UTC", Frequency.WEEKLY

    time_tag = block.select_one("time")
    text = time_tag.get_text(" ", strip=True) if time_tag else ""
    t = _ORDINAL_TIME_RE.search(text)
    start_time = None
    if t:
        hour, minute, meridiem = int(t.group(1)), t.group(2), t.group(3).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        start_time = f"{hour:02d}:{minute}"

    ordinals = _ORDINAL_RE.findall(text)
    if len(ordinals) >= 2:
        frequency = Frequency.BIWEEKLY  # e.g. "1st & 3rd Tuesday" - twice a month
    elif len(ordinals) == 1:
        frequency = Frequency.MONTHLY
    else:
        frequency = Frequency.WEEKLY

    return start_time, None, frequency


def parse_fragment(html: bytes, state_code: str) -> list[RawListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[RawListing] = []

    for block in soup.select("a.quizBlock-venue"):
        try:
            venue_name = block.get("data-title", "").strip()
            if not venue_name:
                continue

            lat = block.get("data-lat")
            lon = block.get("data-lon")
            latitude = float(lat) if lat else None
            longitude = float(lon) if lon else None

            raw_address = block.get("data-address", "")
            city_state_p = block.select_one("p")
            city = region = None
            if city_state_p:
                m = re.match(r"(?P<city>.+?),\s*(?P<region>[A-Za-z]{2})$", city_state_p.get_text(strip=True))
                if m:
                    city, region = m.group("city"), m.group("region")

            street = raw_address
            if region:
                street = re.sub(r",\s*" + re.escape(region) + r"\s+[\w-]+$", "", street).strip()
            if city and street.endswith(city):
                street = street[: -len(city)].strip()

            day_of_week = block.get("data-day") or None
            start_time, tz, frequency = _parse_time(block)

            brand_code = (block.get("data-brand") or "").lower()
            brand_label = BRAND_LABELS.get(brand_code, brand_code or None)

            podio_id = block.get("data-podio")
            detail_url = block.get("href") or VENUES_PAGE_URL

            listings.append(
                RawListing(
                    venue_name=venue_name,
                    street_address=street or None,
                    city=city,
                    region=region,
                    country="United States",
                    latitude=latitude,
                    longitude=longitude,
                    day_of_week=day_of_week,
                    start_time=start_time,
                    timezone=tz,
                    frequency=frequency,
                    notes=f"{brand_label} game." if brand_label else None,
                    provider_name="Geeks Who Drink",
                    provider_website=BASE_URL,
                    source_url=detail_url,
                    raw_snapshot={
                        "state_queried": state_code,
                        "podio_id": podio_id,
                        "brand_code": brand_code,
                        "raw_address": raw_address,
                    },
                )
            )
        except Exception as exc:
            print(f"  [warn] failed to parse a GWD venue block ({state_code}): {exc}")
            continue

    return listings


def run(session: PoliteSession, limit_states: int | None = None) -> list[RawListing]:
    nonce = _get_nonce(session)
    if not nonce:
        print("[GeeksWhoDrink] could not obtain a nonce - aborting")
        return []
    print(f"[GeeksWhoDrink] nonce acquired: {nonce}")

    states = list(US_STATE_BOUNDS.items())
    if limit_states:
        states = states[:limit_states]

    seen_podio_ids: set[str] = set()
    all_listings: list[RawListing] = []
    for i, (code, (south, north, west, east)) in enumerate(states, 1):
        html = _query_bounds(session, nonce, north, south, west, east)
        if html is None:
            print(f"  [{i}/{len(states)}] {code}: request failed")
            continue
        listings = parse_fragment(html, code)
        # A venue near a state border can come back from more than one
        # state query (deliberate bbox overlap) - dedup here on GWD's own
        # podio ID before it ever reaches the pipeline, so one physical
        # listing doesn't get scored as two independent observations.
        fresh = []
        for listing in listings:
            pid = listing.raw_snapshot.get("podio_id")
            if pid and pid in seen_podio_ids:
                continue
            if pid:
                seen_podio_ids.add(pid)
            fresh.append(listing)
        all_listings.extend(fresh)
        print(f"  [{i}/{len(states)}] {code}: {len(listings)} found, {len(fresh)} new")

    return all_listings
