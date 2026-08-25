"""
First-party adapter for The Pub Quiz Company (thepubquizcompany.com).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100).

A genuinely small, single-page operation (Brighton/Sussex/London area) -
plain server-rendered HTML on one page (/our-quizzes), no JS, no API.
Each entry text explicitly says "Every {Day} starting at {time}",
confirming real recurring weekly nights (not one-off bookings like
SpeedQuizzing). One online-only listing is skipped - it has no physical
venue address, so it isn't a "trivia night" in this project's schema
sense (venue-anchored).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://thepubquizcompany.com"
QUIZZES_URL = f"{BASE_URL}/our-quizzes"
SOURCE_NAME = "The Pub Quiz Company"

_SCHEDULE_RE = re.compile(
    r"Every (Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday) starting at (\d{1,2}):?(\d{2})?\s*(am|pm)",
    re.IGNORECASE,
)


def _to_24h(hour: str, minute: str | None, meridiem: str) -> str:
    h = int(hour)
    m = minute or "00"
    if meridiem.lower() == "pm" and h != 12:
        h += 12
    if meridiem.lower() == "am" and h == 12:
        h = 0
    return f"{h:02d}:{m}"


def parse_page(html_text: str) -> list[RawListing]:
    soup = BeautifulSoup(html_text, "lxml")
    listings: list[RawListing] = []

    for h3 in soup.select("h3"):
        link = h3.find("a")
        if not link:
            continue
        venue_name = link.get_text(strip=True)
        if not venue_name or venue_name.lower().startswith("online quiz"):
            continue

        content = h3.find_parent().find_next_sibling(class_="entry-content") or h3.find_parent().parent.select_one(
            ".entry-content"
        )
        if not content:
            continue
        paragraphs = [p.get_text(strip=True) for p in content.select("p")]
        if len(paragraphs) < 2:
            continue

        schedule_text, address_text = paragraphs[0], paragraphs[1]
        if address_text.lower().startswith("http"):
            continue  # the online-quiz entry has a URL here instead of an address

        m = _SCHEDULE_RE.search(schedule_text)
        day_of_week = start_time = None
        if m:
            day_of_week = m.group(1).capitalize()
            start_time = _to_24h(m.group(2), m.group(3), m.group(4))

        parts = [p.strip() for p in address_text.split(",") if p.strip()]
        street = ", ".join(parts[:-2]) if len(parts) >= 3 else (parts[0] if parts else None)
        city = parts[-2] if len(parts) >= 2 else None

        listings.append(
            RawListing(
                venue_name=venue_name.split(",")[0].strip(),
                street_address=street,
                city=city,
                country="United Kingdom",
                day_of_week=day_of_week,
                start_time=start_time,
                frequency=Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN,
                provider_name="The Pub Quiz Company",
                provider_website=BASE_URL,
                source_url=QUIZZES_URL,
                raw_snapshot={"schedule_text": schedule_text, "address_text": address_text},
            )
        )

    return listings


def run(session: PoliteSession) -> list[RawListing]:
    resp = session.get(QUIZZES_URL)
    if resp is None:
        print("[PubQuizCompany] request failed")
        return []
    listings = parse_page(resp.text)
    print(f"[PubQuizCompany] {len(listings)} listings parsed")
    return listings
