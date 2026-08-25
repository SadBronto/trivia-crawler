"""
First-party adapter for Goose's Quizzes (goosesquizzes.com) - Edinburgh
and Glasgow. Third Playwright-based adapter in this project (Wix site,
same reasoning as sphinxuk.py: the real markup is buried in Wix's
internal serialized state, not worth reverse-engineering when rendering
+ reading plain text is reliable and simple).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100).

No street address is available anywhere on this page - just venue name,
time, and city (Edinburgh/Glasgow only, no borough/neighbourhood detail).
Left blank rather than guessed; still enough to identify and geocode by
name+city later if wanted.

Layout, once rendered to plain text: day-of-week headers (ALL CAPS,
plus one "OTHER QUIZZES" bucket for irregular-schedule venues), then a
repeating block per venue ending in the literal text "Answer Form" (a
link label - a genuinely reliable block delimiter, more consistent than
trying to count blank lines). Each block is: venue name, time, city,
then zero or more freeform note lines - some of which describe real
non-weekly patterns ("Runs the last Sunday of every month", "Fortnightly
on Fridays") that get parsed into frequency where the wording is
unambiguous, defaulting to WEEKLY otherwise (matches the page's own
framing - it's titled "regular quizzes" and irregular ones are the
stated exception, not the norm).
"""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://www.goosesquizzes.com"
MAP_URL = f"{BASE_URL}/map"
SOURCE_NAME = "Goose's Quizzes"

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_DAY_HEADER_RE = re.compile(r"^(" + "|".join(d.upper() for d in DAY_NAMES) + r")$")
_TIME_RE = re.compile(r"^(\d{1,2})[:.]?(\d{2})?\s*(am|pm)$", re.IGNORECASE)
_CITY_RE = re.compile(r"^(Edinburgh|Glasgow)$", re.IGNORECASE)
_FORTNIGHTLY_DAY_RE = re.compile(r"Fortnightly on (" + "|".join(DAY_NAMES) + r")", re.IGNORECASE)
_MONTHLY_RE = re.compile(r"(first|second|third|last) \w+ of (?:the|every) month", re.IGNORECASE)


def _to_24h(hour: str, minute: str | None, meridiem: str) -> str:
    h = int(hour)
    m = minute or "00"
    if meridiem.lower() == "pm" and h != 12:
        h += 12
    if meridiem.lower() == "am" and h == 12:
        h = 0
    return f"{h:02d}:{m}"


def _parse_block(lines: list[str], current_day: str | None) -> RawListing | None:
    substantive = [l for l in lines if l and l != "​"]
    if not substantive:
        return None

    venue_name = substantive[0]
    start_time = None
    city = None
    notes_lines = []

    for line in substantive[1:]:
        t = _TIME_RE.match(line)
        if t and start_time is None:
            start_time = _to_24h(t.group(1), t.group(2), t.group(3))
            continue
        c = _CITY_RE.match(line)
        if c and city is None:
            city = c.group(1).capitalize()
            continue
        notes_lines.append(line)

    notes = " ".join(notes_lines) or None

    day = current_day
    frequency = Frequency.WEEKLY
    if notes:
        fortnight_m = _FORTNIGHTLY_DAY_RE.search(notes)
        if fortnight_m:
            day = fortnight_m.group(1).capitalize()
            frequency = Frequency.BIWEEKLY
        elif _MONTHLY_RE.search(notes):
            frequency = Frequency.MONTHLY

    if not venue_name:
        return None

    return RawListing(
        venue_name=venue_name,
        city=city,
        country="United Kingdom",
        day_of_week=day,
        start_time=start_time,
        frequency=frequency,
        notes=notes,
        provider_name="Goose's Quizzes",
        provider_website=BASE_URL,
        source_url=MAP_URL,
        raw_snapshot={"raw_block": substantive},
    )


def parse_body_text(text: str) -> list[RawListing]:
    lines = [l.strip() for l in text.split("\n")]
    listings: list[RawListing] = []
    current_day: str | None = None
    block: list[str] = []

    for line in lines:
        if _DAY_HEADER_RE.match(line):
            current_day = line.capitalize()
            block = []  # discard anything accumulated before this header (nav/intro text on the first one)
            continue
        if line == "OTHER QUIZZES":
            current_day = None
            block = []
            continue
        if line == "Answer Form":
            listing = _parse_block(block, current_day)
            if listing:
                listings.append(listing)
            block = []
            continue
        block.append(line)

    return listings


def run() -> list[RawListing]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(MAP_URL, wait_until="networkidle", timeout=30000)
        text = page.inner_text("body")
        browser.close()

    listings = parse_body_text(text)
    print(f"[GoosesQuizzes] {len(listings)} listings parsed")
    return listings
