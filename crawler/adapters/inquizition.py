"""
First-party adapter for Inquizition (inquizition.com) - the project's
first adapter that actually needs Playwright (real browser rendering)
rather than a plain HTTP request.

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100).

Why Playwright: the "Find a Quiz" page (/find-a-quiz/) runs the "Store
Locator Plus" WordPress plugin, which builds its entire dataset
client-side after the page's JS runs - there's no separate JSON endpoint
to call directly (confirmed: no matching XHR/fetch appeared in the
network log; the data only exists after JS executes). Plain HTTP GETs to
this site (even to normal pages, not just wp-json) also return 403 -
some WAF/bot-detection layer, unrelated to robots.txt (which is fully
permissive here) - so this needs an actual rendered browser context
either way, not just "add JS-eval on top of requests."

Once rendered, the data is genuinely easy to get: the plugin exposes a
global function `storeLocatorGetData()` that returns the complete,
already-loaded dataset (confirmed: 138 stores, no pagination, no search
required - it's all there before you type anything into the search box).
Each store has address, phone, email, website, real lat/lng, IANA
timezone, and one `hours_{DayName}` field per night it runs (e.g. a store
with both `hours_Wednesday` and `hours_Thursday` runs two nights) -
mapped 1:1 to this project's one-RawListing-per-night pattern.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://inquizition.com"
FIND_A_QUIZ_URL = f"{BASE_URL}/find-a-quiz/"
SOURCE_NAME = "Inquizition"

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

COUNTRY_CODE_MAP = {
    "GB": "United Kingdom",
    "IE": "Ireland",
    "US": "United States",
    "AU": "Australia",
    "NZ": "New Zealand",
    "CA": "Canada",
}


def _parse_time(raw: str) -> str | None:
    """'7:30pm' -> '19:30'."""
    raw = raw.strip().lower()
    import re

    m = re.match(r"(\d{1,2}):?(\d{2})?\s*(am|pm)", raw)
    if not m:
        return None
    hour = int(m.group(1))
    minute = m.group(2) or "00"
    meridiem = m.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _store_to_listings(store: dict) -> list[RawListing]:
    data = store.get("data") or {}
    venue_name = (store.get("name") or "").strip()
    if not venue_name:
        return []

    address_raw = (data.get("address") or "").replace("\r\n", "\n").strip()
    lines = [l.strip() for l in address_raw.split("\n") if l.strip()]
    street = ", ".join(lines[:-1]) if len(lines) > 1 else None
    city_postcode = lines[-1] if lines else ""
    city = None
    if "," in city_postcode:
        city = city_postcode.split(",")[0].strip()
    elif city_postcode:
        city = city_postcode

    country_code = store.get("country")
    country = COUNTRY_CODE_MAP.get(country_code, country_code)

    lat = data.get("map_lat")
    lng = data.get("map_lng")

    day_hours = {day: data.get(f"hours_{day}") for day in DAY_NAMES if data.get(f"hours_{day}")}
    if not day_hours:
        return [
            RawListing(
                venue_name=venue_name,
                street_address=street,
                city=city,
                country=country,
                latitude=float(lat) if lat is not None else None,
                longitude=float(lng) if lng is not None else None,
                phone=data.get("phone") or None,
                venue_website=data.get("website") or None,
                frequency=Frequency.UNKNOWN,
                provider_name="Inquizition",
                provider_website=BASE_URL,
                source_url=BASE_URL + f"/?store={store.get('storeid', '')}",
                raw_snapshot={"storeid": store.get("storeid"), "timezone": store.get("timezone")},
            )
        ]

    listings = []
    for day, hours_raw in day_hours.items():
        listings.append(
            RawListing(
                venue_name=venue_name,
                street_address=street,
                city=city,
                country=country,
                latitude=float(lat) if lat is not None else None,
                longitude=float(lng) if lng is not None else None,
                phone=data.get("phone") or None,
                venue_website=data.get("website") or None,
                day_of_week=day,
                start_time=_parse_time(hours_raw),
                timezone=store.get("timezone") or None,
                frequency=Frequency.WEEKLY,
                provider_name="Inquizition",
                provider_website=BASE_URL,
                source_url=BASE_URL + f"/?store={store.get('storeid', '')}",
                raw_snapshot={"storeid": store.get("storeid"), "timezone": store.get("timezone")},
            )
        )
    return listings


def run() -> list[RawListing]:
    """No PoliteSession here - this is one page render, not repeated
    requests, so the per-request rate-limiting model doesn't apply the
    same way. Still identifies honestly via a real Chromium UA (default).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FIND_A_QUIZ_URL, wait_until="networkidle", timeout=30000)

        # Dismiss the cookie notice if present - the widget's data loads
        # fine either way, but this matches normal page interaction.
        try:
            page.click("text=Accept", timeout=3000)
        except Exception:
            pass

        page.wait_for_function("typeof storeLocatorGetData === 'function'", timeout=15000)
        data = page.evaluate("storeLocatorGetData()")
        browser.close()

    stores = data.get("stores", [])
    print(f"[Inquizition] {len(stores)} stores rendered")

    all_listings: list[RawListing] = []
    for store in stores:
        all_listings.extend(_store_to_listings(store))
    return all_listings
