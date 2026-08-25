"""
First-party adapter for Weekly Quiz (weeklyquiz.co.uk).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100).

Standard WordPress "The Events Calendar" plugin, exposed at its normal
REST endpoint (/wp-json/tribe/events/v1/events) - no reverse-engineering
needed. Unlike SpeedQuizzing, this genuinely is a recurring-events
calendar: each venue's quiz is a recurring event series that the plugin
auto-generates forward instances for (confirmed: 764 total event
*instances* collapse to a much smaller number of unique venues once
deduped - the same venue appears many times, once per upcoming week),
and event descriptions consistently narrate the pattern explicitly
("Every Tuesday the ... hosts a quiz..."). So this dedupes to one
RawListing per venue (using the nearest upcoming instance's day/time),
tagged Frequency.WEEKLY, rather than importing all 764 near-duplicate
future dates.

Each event instance carries a full nested `venue` object - address,
city, country, postcode, phone, real lat/lng, all in one response, no
follow-up requests needed per venue.
"""

from __future__ import annotations

import html

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://weeklyquiz.co.uk"
API_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
SOURCE_NAME = "Weekly Quiz"
PAGE_SIZE = 50

DAY_NUM_TO_NAME = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _event_to_listing(event: dict) -> RawListing | None:
    venue = event.get("venue")
    if not isinstance(venue, dict) or not venue.get("venue"):
        return None

    start_date = event.get("start_date")  # "2026-08-25 19:00:00"
    day_of_week = start_time = None
    if start_date:
        from datetime import datetime

        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d %H:%M:%S")
            day_of_week = DAY_NUM_TO_NAME[dt.weekday()]
            start_time = dt.strftime("%H:%M")
        except ValueError:
            pass

    return RawListing(
        venue_name=html.unescape(venue.get("venue", "")).strip(),
        street_address=venue.get("address") or None,
        city=venue.get("city") or None,
        country=venue.get("country") or None,
        latitude=venue.get("geo_lat"),
        longitude=venue.get("geo_lng"),
        phone=venue.get("phone") or None,
        day_of_week=day_of_week,
        start_time=start_time,
        timezone=event.get("timezone") or None,
        frequency=Frequency.WEEKLY,
        provider_name="Weekly Quiz",
        provider_website=BASE_URL,
        source_url=venue.get("url") or event.get("url"),
        raw_snapshot={"venue_slug": venue.get("slug"), "event_id": event.get("id")},
    )


def run(session: PoliteSession) -> list[RawListing]:
    seen_venue_slugs: set[str] = set()
    all_listings: list[RawListing] = []
    page = 1
    while True:
        resp = session.get(API_URL, params={"per_page": PAGE_SIZE, "page": page})
        if resp is None:
            break
        data = resp.json()
        events = data.get("events", [])
        if not events:
            break

        for event in events:
            venue = event.get("venue")
            slug = venue.get("slug") if isinstance(venue, dict) else None
            if slug and slug in seen_venue_slugs:
                continue
            listing = _event_to_listing(event)
            if listing:
                if slug:
                    seen_venue_slugs.add(slug)
                all_listings.append(listing)

        total_pages = data.get("total_pages", page)
        print(f"  [page {page}/{total_pages}] {len(events)} event instances -> {len(all_listings)} unique venues so far")
        if page >= total_pages:
            break
        page += 1

    return all_listings
