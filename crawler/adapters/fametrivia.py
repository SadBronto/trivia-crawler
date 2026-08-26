"""
First-party adapter for Fame Trivia (fametrivia.com.au) - Australia.

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100).

robots.txt specifies `Crawl-Delay: 20` for all crawlers (no path
disallows) - a real, explicit politeness request, honored here by
constructing a PoliteSession with a 20s delay specifically for this
adapter rather than the project's general default. Only 8 requests
needed (one per state page) so this costs about 3 minutes total, not
worth trying to shortcut.

No JS, no API - each state has its own plain server-rendered page
(/now-showing-{state}/) with one HTML table per day of the week. Row
structure is simple: venue name + suburb (as link text, often with an
external link to the venue's own site), a time, and the individual
host's name. No street address or coordinates anywhere on these pages -
just venue name and suburb, left at that rather than guessed.

A handful of entries have a parenthetical note like "(Last Wed each
month)" or "(First Wed each month)" appended to the venue/suburb text -
parsed into Frequency.MONTHLY when unambiguous, defaulting to WEEKLY
otherwise (the page has no other framing either way, unlike Goose's
Quizzes' "regular quizzes" heading, so this is a plain default, not a
confirmed policy).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://fametrivia.com.au"
SOURCE_NAME = "Fame Trivia"
CRAWL_DELAY_SECONDS = 20.0

STATE_SLUGS = ["nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"]
STATE_NAMES = {
    "nsw": "New South Wales",
    "vic": "Victoria",
    "qld": "Queensland",
    "wa": "Western Australia",
    "sa": "South Australia",
    "tas": "Tasmania",
    "act": "Australian Capital Territory",
    "nt": "Northern Territory",
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_HEADER_RE = re.compile(r"^(" + "|".join(DAY_NAMES) + r")s?$", re.IGNORECASE)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.IGNORECASE)
# Matches ANY parenthetical mentioning "month", not just the spelled-out
# ordinals ("First", "Second") originally assumed here - the site actually
# also uses numeric ones ("2nd Tuesday each month"), which that narrower
# pattern silently failed to match, leaving the note stuck onto the city
# text (real bug hit live: city ended up as "Kalamunda (2nd Tuesday each
# month)"). Broadening to "any parenthetical with 'month' in it" catches
# every phrasing actually seen without having to keep enumerating them.
_MONTHLY_NOTE_RE = re.compile(r"\([^)]*\bmonth\b[^)]*\)", re.IGNORECASE)


def _to_24h(hour: str, minute: str, meridiem: str) -> str:
    h = int(hour)
    if meridiem.upper() == "PM" and h != 12:
        h += 12
    if meridiem.upper() == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{minute}"


def parse_state_page(html_text: str, state_slug: str) -> list[RawListing]:
    soup = BeautifulSoup(html_text, "lxml")
    listings: list[RawListing] = []
    current_day: str | None = None

    for tr in soup.select("table.now-showing-table tr"):
        header_cell = tr.select_one("td.table-header")
        if header_cell:
            text = header_cell.get_text(strip=True)
            m = _DAY_HEADER_RE.match(text)
            if m:
                current_day = m.group(1).capitalize()
            continue

        cells = tr.select("td")
        if len(cells) < 2 or current_day is None:
            continue

        venue_cell = cells[0]
        link = venue_cell.find("a")
        venue_text = (link.get_text(" ", strip=True) if link else venue_cell.get_text(" ", strip=True)).strip()
        venue_website = link.get("href") if link else None

        monthly_note = _MONTHLY_NOTE_RE.search(venue_text)
        frequency = Frequency.MONTHLY if monthly_note else Frequency.WEEKLY
        venue_text_clean = _MONTHLY_NOTE_RE.sub("", venue_text).strip()

        parts = [p.strip() for p in venue_text_clean.split(",")]
        venue_name = parts[0]
        city = parts[1] if len(parts) > 1 else None

        time_text = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        t = _TIME_RE.search(time_text)
        start_time = _to_24h(t.group(1), t.group(2), t.group(3)) if t else None

        host_name = cells[2].get_text(strip=True) if len(cells) > 2 else None

        if not venue_name:
            continue

        listings.append(
            RawListing(
                venue_name=venue_name,
                city=city,
                region=STATE_NAMES.get(state_slug),
                country="Australia",
                venue_website=venue_website,
                day_of_week=current_day,
                start_time=start_time,
                frequency=frequency,
                notes=f"Hosted by {host_name}." if host_name else None,
                provider_name="Fame Trivia",
                provider_website=BASE_URL,
                source_url=f"{BASE_URL}/now-showing-{state_slug}/",
                raw_snapshot={"state": state_slug, "host": host_name},
            )
        )

    return listings


def run(session: PoliteSession) -> list[RawListing]:
    all_listings: list[RawListing] = []
    for i, slug in enumerate(STATE_SLUGS, 1):
        resp = session.get(f"{BASE_URL}/now-showing-{slug}/")
        if resp is None:
            print(f"  [{i}/{len(STATE_SLUGS)}] {slug}: request failed")
            continue
        listings = parse_state_page(resp.text, slug)
        all_listings.extend(listings)
        print(f"  [{i}/{len(STATE_SLUGS)}] {slug}: {len(listings)} listings")

    return all_listings
