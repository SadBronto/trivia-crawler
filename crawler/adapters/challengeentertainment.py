"""
First-party adapter for Challenge Entertainment (challengeentertainment.com).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100) - their
own show finder, not a directory. Also independently confirmed by the
TriviaNearMe directory data already in this DB (their `companies/
challenge-entertainment` page claims 183 venues - a real example of the
spec's Phase 11 self-expansion idea: a directory naming a company you
didn't have an adapter for yet, later closed with a real one).

How this was found: the "Find a Game" page (/find-a-game/, redirects to
challengeentertainment.com) runs a custom WordPress plugin (ce-events)
that POSTs to admin-ajax.php with action=filter_shows and (optionally)
filters - game/day/frequency/state/venue search/geo radius/map bounds.
Unlike Geeks Who Drink, calling it with NO filters at all does not fall
back to some generic subset - it returns the complete national dataset
directly (confirmed: matches the "951 shows found" the live page itself
reports with zero filters applied). So this adapter makes exactly ONE
request, no tiling/pagination/redundancy needed at all.

The response is a rendered HTML fragment of `.ntl-card` divs, one per
(venue, game/day) combination - a venue running two different game
nights shows up as two cards sharing one address, matching this
project's venue/event split exactly. Each card has a clean, reliable
`.ntl-card-address` ("street, City, ST" - no zip) and `.ntl-card-schedule`
("Tuesdays, 6:30 pm").

Not implemented: the "CANCELLED THIS WEEK" flag visible on some cards.
That's a single-occurrence skip (this week only), not a real signal
about whether the recurring night still exists, so it's not something
this adapter's confidence scoring should react to - noted here so it's
clear that omission was deliberate, not missed.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://challengeentertainment.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
SOURCE_NAME = "Challenge Entertainment"

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"\b(1st|2nd|3rd|4th|5th)\b")


def _to_24h(hh: str, mm: str, meridiem: str) -> str:
    hour = int(hh)
    if meridiem.lower() == "pm" and hour != 12:
        hour += 12
    if meridiem.lower() == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{mm}"


def _parse_schedule(text: str) -> tuple[str | None, str | None, Frequency]:
    """'Tuesdays, 6:30 pm' -> ('Tuesday', '18:30', WEEKLY).
    '1st & 3rd Wednesday, 7:00 pm' -> ('Wednesday', '19:00', BIWEEKLY).
    """
    day_of_week = None
    day_m = re.search(
        r"(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)s?", text, re.IGNORECASE
    )
    if day_m:
        day_of_week = day_m.group(1).capitalize()

    start_time = None
    t = _TIME_RE.search(text)
    if t:
        start_time = _to_24h(t.group(1), t.group(2), t.group(3))

    ordinals = _ORDINAL_RE.findall(text)
    if len(ordinals) >= 2:
        frequency = Frequency.BIWEEKLY
    elif len(ordinals) == 1:
        frequency = Frequency.MONTHLY
    else:
        frequency = Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN

    return day_of_week, start_time, frequency


def parse_response(html_text: str) -> list[RawListing]:
    soup = BeautifulSoup(html_text, "lxml")
    listings: list[RawListing] = []

    for card in soup.select("div.ntl-card"):
        try:
            venue_name_tag = card.select_one(".ntl-card-venue")
            if not venue_name_tag:
                continue
            venue_name = venue_name_tag.get_text(strip=True)

            game_type_tag = card.select_one(".ntl-card-title")
            game_type = game_type_tag.get_text(strip=True) if game_type_tag else None

            address_tag = card.select_one(".ntl-card-address")
            street = city = region = None
            if address_tag:
                parts = [p.strip() for p in address_tag.get_text(strip=True).split(",") if p.strip()]
                if len(parts) >= 3:
                    street = ", ".join(parts[:-2])
                    city = parts[-2]
                    region = parts[-1]
                elif len(parts) == 2:
                    street, city = parts
                elif len(parts) == 1:
                    city = parts[0]

            schedule_tag = card.select_one(".ntl-card-schedule")
            day_of_week = start_time = None
            frequency = Frequency.UNKNOWN
            if schedule_tag:
                day_of_week, start_time, frequency = _parse_schedule(schedule_tag.get_text(strip=True))

            permalink = card.get("data-permalink") or BASE_URL

            listings.append(
                RawListing(
                    venue_name=venue_name,
                    street_address=street,
                    city=city,
                    region=region,
                    country="United States",
                    day_of_week=day_of_week,
                    start_time=start_time,
                    frequency=frequency,
                    notes=f"{game_type} night." if game_type else None,
                    provider_name="Challenge Entertainment",
                    provider_website=BASE_URL,
                    source_url=permalink,
                    raw_snapshot={
                        "venue_key": card.get("data-venue-key"),
                        "game_type": game_type,
                    },
                )
            )
        except Exception as exc:
            print(f"  [warn] failed to parse a Challenge Entertainment card: {exc}")
            continue

    return listings


def run(session: PoliteSession) -> list[RawListing]:
    resp = session.post(AJAX_URL, data={"action": "filter_shows"})
    if resp is None:
        print("[ChallengeEntertainment] request failed")
        return []
    listings = parse_response(resp.text)
    print(f"[ChallengeEntertainment] {len(listings)} listings parsed from a single unfiltered request")
    return listings
