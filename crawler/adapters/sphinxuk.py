"""
First-party adapter for Sphinx UK (sphinxuk.com) - a Wix site, second
Playwright-based adapter in this project.

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100). Page
text explicitly states: "All of the events listed here run every week
unless otherwise stated" - genuine recurring data, like the confirmed
sources, not booking instances like SpeedQuizzing.

Why Playwright over a plain request: the page's real HTML embeds all
this text inside Wix's internal serialized component state (a deeply
nested, obfuscated JSON blob with inconsistent escaping between
elements - confirmed by testing: regex-matching the "Monday" vs
"Tuesday" day headers required two different escaping patterns even
though they're rendered identically). Not a reliable format to parse
directly. Rendering with Playwright and reading the plain visible text
(`page.inner_text("body")`) sidesteps that entirely and is far more
robust than fighting Wix's internal format.

Format once rendered is plain text grouped by day heading, then a
space-joined run of "{time} - {venue}, {street}, {city}, {postcode}"
entries with no other delimiter - parsed here by finding each time
pattern as the split point between entries. Address parsing is
best-effort (UK format varies: some entries omit a clear city segment,
run city+postcode together, etc.) - venue name, day, and time are solid
for every entry either way.

A handful of entries carry a trailing symbol character (✈︎ ① ② ☞ ⚅ ¥)
tied to a "non-weekly symbols" legend on the page (every other week,
term-time only, first/second/last week of the month, etc.) - the exact
symbol-to-meaning pairing isn't reliably extractable from plain text
(the legend renders as a table, lost in text form), so this adapter
only distinguishes "has a symbol" (tagged Frequency.IRREGULAR, symbol
kept in notes) from "no symbol" (Frequency.WEEKLY) rather than guessing
which specific irregular pattern applies.
"""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://sphinxuk.com"
QUIZZES_URL = f"{BASE_URL}/quizzes"
SOURCE_NAME = "Sphinx UK"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_HEADER_RE = re.compile(r"^(" + "|".join(DAY_NAMES) + r")$", re.IGNORECASE)
_ENTRY_RE = re.compile(r"(\d{1,2}[:.]\d{2}\s*(?:am|pm))\s*-\s*(.*?)(?=\d{1,2}[:.]\d{2}\s*(?:am|pm)\s*-|$)", re.IGNORECASE)
_UK_POSTCODE_RE = re.compile(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\s*$")
_SYMBOL_CHARS = "✈①②☞⚅¥"


def _to_24h(raw: str) -> str | None:
    m = re.match(r"(\d{1,2})[:.](\d{2})\s*(am|pm)", raw.strip(), re.IGNORECASE)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), m.group(2), m.group(3).lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _parse_entry(entry_text: str, day: str) -> RawListing | None:
    text = entry_text.strip()
    has_symbol = any(ch in text for ch in _SYMBOL_CHARS)
    symbol = next((ch for ch in _SYMBOL_CHARS if ch in text), None)
    for ch in _SYMBOL_CHARS:
        text = text.replace(ch, "")

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None
    venue_name = parts[0]

    street = city = None
    if len(parts) >= 3:
        street = parts[1]
        last = parts[-1]
        city = _UK_POSTCODE_RE.sub("", last).strip()
        if not city:
            city = parts[-2]
    elif len(parts) == 2:
        last = parts[1]
        city = _UK_POSTCODE_RE.sub("", last).strip() or None

    return RawListing(
        venue_name=venue_name,
        street_address=street,
        city=city or None,
        country="United Kingdom",
        day_of_week=day,
        frequency=Frequency.IRREGULAR if has_symbol else Frequency.WEEKLY,
        notes=f"Marked with symbol '{symbol}' on the site - see their non-weekly-symbols legend (not a standard weekly night)." if has_symbol else None,
        provider_name="Sphinx UK",
        provider_website=BASE_URL,
        source_url=QUIZZES_URL,
        raw_snapshot={"raw_entry": entry_text},
    )


def parse_body_text(text: str) -> list[RawListing]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    listings: list[RawListing] = []
    current_day: str | None = None

    for line in lines:
        day_match = _DAY_HEADER_RE.match(line)
        if day_match:
            current_day = day_match.group(1).capitalize()
            continue
        if current_day is None:
            continue

        for time_m in _ENTRY_RE.finditer(line):
            time_raw, rest = time_m.group(1), time_m.group(2)
            listing = _parse_entry(rest, current_day)
            if listing:
                listing.start_time = _to_24h(time_raw)
                listings.append(listing)

    return listings


def run() -> list[RawListing]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(QUIZZES_URL, wait_until="networkidle", timeout=30000)
        text = page.inner_text("body")
        browser.close()

    listings = parse_body_text(text)
    print(f"[SphinxUK] {len(listings)} listings parsed")
    return listings
