"""
RESUMED (2026-08-25): the 2026-08-24 Cloudflare block cleared on its own
within about a day (a rate-limit-style hold, not a permanent ban) -
verified with a single polite request before touching anything else.

Rewritten to abandon bounding-box tiling entirely. It looked promising in
one early test (a small box around Seattle returned only 34 nearby
venues), but re-running that *exact same request* today returned the
generic ~1000-result nationwide blob instead - proof the server's geo
filtering is inconsistent/unreliable, not a clean function of box size
that can be tuned around. Chasing the "right" box size further would mean
more live requests against a site that's already shown it's watching for
unusual traffic, for a mechanism that doesn't reliably work anyway.

What actually is reliable: hasAll=true consistently returns a large
(~900-1024 row) result set that spans 30+ US states - i.e. it's already
close to their whole nationwide inventory, not a tiny/fixed subset. So
this now just calls the endpoint a handful of times (different arbitrary
start points, for redundancy against any variance between calls, not
because location changes what comes back) and dedups by GWD's own podio
ID, instead of pretending real geographic tiling is happening.

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

Known limitation accepted for now: because there's no reliable way to
filter by location, this adapter cannot promise *complete* nationwide
coverage the way the King Trivia adapter's true unbounded pagination
does - only "whatever the endpoint's default result set contains," which
observed testing shows is broad (30+ states) but capped around ~1000
rows. Good enough to run now; revisit if GWD's own site changes.

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
from crawler.us_cities import US_METRO_CENTERS

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
    session: PoliteSession, nonce: str, lat: float, lon: float
) -> bytes | None:
    # bounds[...] is included because the endpoint returns nothing at all
    # without it (confirmed), not because it reliably filters (confirmed
    # it doesn't, reliably) - see module docstring. A generous box is
    # honest about what's actually happening: this is not a real
    # geographic query, just satisfying a required parameter.
    params = {
        "action": "mb_display_mapped_events",
        "bounds[northLat]": lat + 5,
        "bounds[southLat]": lat - 5,
        "bounds[westLong]": lon - 5,
        "bounds[eastLong]": lon + 5,
        "days": "",
        "brands": "",
        "nonce": nonce,
        "search": "",
        "startLat": lat,
        "startLong": lon,
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


def parse_fragment(html: bytes, tile_label: str) -> list[RawListing]:
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
            # NOT block.select_one("p") - the brand-price label
            # (<p class="quizBlock__brand">Small Batch</p>) comes first
            # in document order and would win that selector, silently
            # leaving city/region unparsed for almost every row (caught
            # by checking real ingested data: 871/934 rows had no region
            # before this fix). The city/state line lives specifically in
            # quizBlock__content.
            city_state_p = block.select_one(".quizBlock__content p")
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
                        "tile_queried": tile_label,
                        "podio_id": podio_id,
                        "brand_code": brand_code,
                        "raw_address": raw_address,
                    },
                )
            )
        except Exception as exc:
            print(f"  [warn] failed to parse a GWD venue block ({tile_label}): {exc}")
            continue

    return listings


def run(session: PoliteSession, num_queries: int = 4) -> list[RawListing]:
    """Calls the endpoint `num_queries` times from different arbitrary
    points (see module docstring - this is redundancy against possible
    variance between calls, not real geographic tiling, since geo
    filtering was found to be unreliable). Dedups by GWD's own podio ID.
    """
    nonce = _get_nonce(session)
    if not nonce:
        print("[GeeksWhoDrink] could not obtain a nonce - aborting")
        return []
    print(f"[GeeksWhoDrink] nonce acquired: {nonce}")

    centers = US_METRO_CENTERS[:num_queries]

    seen_podio_ids: set[str] = set()
    all_listings: list[RawListing] = []
    for i, (name, lat, lon) in enumerate(centers, 1):
        html = _query_bounds(session, nonce, lat, lon)
        if html is None:
            print(f"  [{i}/{len(centers)}] {name}: request failed")
            continue
        listings = parse_fragment(html, name)
        fresh = []
        for listing in listings:
            pid = listing.raw_snapshot.get("podio_id")
            if pid and pid in seen_podio_ids:
                continue
            if pid:
                seen_podio_ids.add(pid)
            fresh.append(listing)
        all_listings.extend(fresh)
        print(f"  [{i}/{len(centers)}] {name}: {len(listings)} found, {len(fresh)} new")

    return all_listings
