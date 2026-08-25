"""
First-party adapter for Trivia Mafia (triviamafia.com).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100) - their
own venue pages, not a directory. Midwest-centered per the spec, matches
what's found here (Minnesota-heavy, plus Iowa/California outliers).

Squarespace site. No JS widget/AJAX endpoint involved - the /locations
page is one big server-rendered text block listing every venue as a
plain link (confirmed: 200 links found, matching a distinctive "link
wrapping a <strong>" formatting pattern used only for the real venue
list, not nav/intro links, so no exclude-list of nav paths was needed).
Each venue then has its own short-slug page (e.g. /8th-st, /331,
/arbeiter) that is ALSO plain server-rendered HTML.

The single most useful field on every venue page turned out to be the
<meta name="description"> tag, which consistently reads like:
    "Play the world's best bar trivia at {venue} in {city}, {ST}
     every {Day} at {Time}! ..."
and, for venues running more than one night (confirmed on a real venue,
Brit's Pub - "on hiatus" on the list page but still has a normal detail
page): "...every Wednesday at 7 PM & Friday at 5 PM!" - so this parses
ALL (day, time) pairs found in that one sentence, not just the first,
the same one-venue-multiple-nights pattern as King Trivia's event_day.

A full street address is available on most (not all) venue pages, but
in an unstructured spot (a "Recent Winners" results block, not a
dedicated address field) - so it's genuinely absent on venues with no
recorded game history yet. Regex-matched when present; otherwise the
listing still gets city/state from the meta description, just no street.

Venue website was deliberately NOT scraped - the raw HTML has enough
third-party domains mixed in near the venue's own site (analytics,
social platforms, an unrelated quiz-tools vendor) that a general
"find a domain-looking string" approach kept picking up noise. Leaving
it blank beats guessing wrong.

robots.txt note (checked 2026-08-25): Squarespace's default robots.txt
blocks a set of named AI-training bots (including "ClaudeBot") by an
explicit User-agent block, separate from the wildcard "*" group this
crawler's own distinct User-Agent falls under (see http_client.py) - the
wildcard group only restricts admin/search/commerce/static/query-param
paths, none of which this adapter touches. This crawler is a distinct,
honestly-identified tool built for the user's own directory-building
project, not Anthropic's ClaudeBot product, so the named block doesn't
apply to it - flagged here for transparency since it's adjacent to the
assistant's own identity, not because it changes what's technically
being requested.
"""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://triviamafia.com"
LOCATIONS_URL = f"{BASE_URL}/locations"
SOURCE_NAME = "Trivia Mafia"

_DAY_TIME_RE = re.compile(
    r"(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+at\s+(\d{1,2}(?::\d{2})?)\s*([AP])\.?M\.?",
    re.IGNORECASE,
)
_CITY_STATE_RE = re.compile(r"\bin\s+([A-Za-z][A-Za-z\s'.-]*?),\s*([A-Z]{2})\b")
_VENUE_NAME_RE = re.compile(r"trivia at (.+?) in ", re.IGNORECASE)
_ADDRESS_RE = re.compile(
    r"\d{1,5}\s+[A-Za-z0-9][\w .'-]*,\s*[A-Za-z][A-Za-z .'-]*,\s*[A-Z]{2}\s*\d{5}"
)


def discover_venue_urls(session: PoliteSession) -> list[tuple[str, str]]:
    """Returns (url, name_from_listing) pairs."""
    resp = session.get(LOCATIONS_URL)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")

    results = []
    for a in soup.select("div[data-sqsp-text-block-content] a"):
        strong = a.find("strong")
        if not strong:
            continue  # the real venue list wraps its link text in <strong>; nav/intro links don't
        href = a.get("href", "")
        if not href or (href.startswith("http") and "triviamafia.com" not in href):
            continue  # skip external links (e.g. the Initials Game Live cross-promo)
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        results.append((url, strong.get_text(strip=True)))
    return results


def _to_24h(time_str: str, meridiem: str) -> str:
    if ":" in time_str:
        hour_str, minute = time_str.split(":")
    else:
        hour_str, minute = time_str, "00"
    hour = int(hour_str)
    if meridiem.upper() == "P" and hour != 12:
        hour += 12
    if meridiem.upper() == "A" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def parse_venue_page(html_text: str, url: str, fallback_name: str) -> list[RawListing]:
    meta_m = re.search(r'<meta name="description" content="([^"]*)"', html_text)
    description = html.unescape(meta_m.group(1)) if meta_m else ""

    venue_name = fallback_name
    name_m = _VENUE_NAME_RE.search(description)
    if name_m:
        venue_name = name_m.group(1).strip()

    city = region = None
    cs_m = _CITY_STATE_RE.search(description)
    if cs_m:
        city, region = cs_m.group(1).strip(), cs_m.group(2)

    street = None
    addr_m = _ADDRESS_RE.search(html_text)
    if addr_m:
        parts = [p.strip() for p in addr_m.group(0).split(",")]
        if len(parts) >= 1:
            street = parts[0]
            # If the address block gave us a cleaner city/state than the
            # description did, prefer it (description sometimes includes
            # a neighborhood modifier like "northeast Minneapolis").
            if len(parts) >= 3 and not city:
                city = parts[1].strip()
                region_m = re.match(r"([A-Z]{2})", parts[2].strip())
                if region_m:
                    region = region_m.group(1)

    day_time_pairs = _DAY_TIME_RE.findall(description)
    if not day_time_pairs:
        return [
            RawListing(
                venue_name=venue_name,
                street_address=street,
                city=city,
                region=region,
                country="United States",
                frequency=Frequency.UNKNOWN,
                provider_name="Trivia Mafia",
                provider_website=BASE_URL,
                source_url=url,
                raw_snapshot={"description": description},
            )
        ]

    listings = []
    for day, time_str, meridiem in day_time_pairs:
        listings.append(
            RawListing(
                venue_name=venue_name,
                street_address=street,
                city=city,
                region=region,
                country="United States",
                day_of_week=day.capitalize(),
                start_time=_to_24h(time_str, meridiem),
                frequency=Frequency.WEEKLY,
                provider_name="Trivia Mafia",
                provider_website=BASE_URL,
                source_url=url,
                raw_snapshot={"description": description},
            )
        )
    return listings


def run(session: PoliteSession, limit: int | None = None) -> list[RawListing]:
    venues = discover_venue_urls(session)
    print(f"[TriviaMafia] {len(venues)} venue links found on /locations")
    if limit:
        venues = venues[:limit]

    all_listings: list[RawListing] = []
    for i, (url, name) in enumerate(venues, 1):
        resp = session.get(url)
        if resp is None:
            print(f"  [{i}/{len(venues)}] {url} -> request failed")
            continue
        listings = parse_venue_page(resp.text, url, name)
        all_listings.extend(listings)
        days = ", ".join(l.day_of_week for l in listings if l.day_of_week) or "no day found"
        print(f"  [{i}/{len(venues)}] {name} -> {days}")

    return all_listings
