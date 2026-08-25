"""
First-party adapter for Question One (questionone.com).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100) - their
own venue data, not a directory. Covers Greater London/UK plus Melbourne
and Sydney, Australia - a UK-priority pick that turned out to double as
partial Australia coverage for free.

By far the cleanest data source found in this project: WordPress exposes
a custom "venues" post type through its standard REST API
(/wp-json/wp/v2/venues) with full Advanced Custom Fields (ACF) data -
structured address (street/city/postcode/country), real lat/lng, phone,
website, and a `dates` array that already supports more than one night
per venue natively (not something this adapter has to infer). robots.txt
is a stock WordPress file (only /wp-admin/ disallowed) - none of this is
gated.

No pagination trickery needed: the API's own X-WP-Total/X-WP-TotalPages
headers give an exact page count - this just walks pages at per_page=100
until exhausted.
"""

from __future__ import annotations

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://questionone.com"
API_URL = f"{BASE_URL}/wp-json/wp/v2/venues"
SOURCE_NAME = "Question One"
PAGE_SIZE = 100

DAY_NAME_MAP = {
    "sunday": "Sunday",
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
}


def _venue_to_listings(venue: dict) -> list[RawListing]:
    acf = venue.get("acf") or {}
    location = acf.get("location") or {}
    venue_name = (location.get("name") or venue.get("title", {}).get("rendered") or "").strip()
    if not venue_name:
        return []

    street_parts = [p for p in (location.get("street_number"), location.get("street_name")) if p]
    street = " ".join(str(p) for p in street_parts) or None

    country = location.get("country")
    lat = location.get("lat")
    lng = location.get("lng")

    details = (acf.get("details") or "").strip() or None

    dates = acf.get("dates") or []
    if not dates:
        return [
            RawListing(
                venue_name=venue_name,
                street_address=street,
                city=location.get("city") or None,
                region=location.get("state") or None,
                country=country,
                latitude=float(lat) if lat is not None else None,
                longitude=float(lng) if lng is not None else None,
                phone=acf.get("phone") or None,
                venue_website=acf.get("website") or None,
                frequency=Frequency.UNKNOWN,
                notes=details,
                provider_name="Question One",
                provider_website=BASE_URL,
                source_url=venue.get("link"),
                raw_snapshot={"wp_id": venue.get("id")},
            )
        ]

    listings = []
    for d in dates:
        day_raw = (d.get("day") or "").lower()
        day_of_week = DAY_NAME_MAP.get(day_raw)
        time_raw = d.get("time")  # "19:30:00"
        start_time = time_raw[:5] if time_raw else None

        listings.append(
            RawListing(
                venue_name=venue_name,
                street_address=street,
                city=location.get("city") or None,
                region=location.get("state") or None,
                country=country,
                latitude=float(lat) if lat is not None else None,
                longitude=float(lng) if lng is not None else None,
                phone=acf.get("phone") or None,
                venue_website=acf.get("website") or None,
                day_of_week=day_of_week,
                start_time=start_time,
                frequency=Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN,
                notes=details,
                provider_name="Question One",
                provider_website=BASE_URL,
                source_url=venue.get("link"),
                raw_snapshot={"wp_id": venue.get("id")},
            )
        )
    return listings


def run(session: PoliteSession) -> list[RawListing]:
    all_listings: list[RawListing] = []
    page = 1
    while True:
        resp = session.get(API_URL, params={"per_page": PAGE_SIZE, "page": page})
        if resp is None:
            break
        venues = resp.json()
        if not venues:
            break
        for v in venues:
            all_listings.extend(_venue_to_listings(v))
        total_pages = int(resp.headers.get("X-WP-TotalPages", page))
        print(f"  [page {page}/{total_pages}] {len(venues)} venues -> {len(all_listings)} listings so far")
        if page >= total_pages:
            break
        page += 1

    return all_listings
