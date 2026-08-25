"""
First-party adapter for Last Call Trivia (lastcalltrivia.com).

Every listing is tagged SourceType.PROVIDER_SCHEDULE (score 100) - this
is their own venue pages, not a directory.

Much simpler than Geeks Who Drink or King Trivia: no JS locator widget,
no AJAX endpoint to reverse-engineer. WordPress with a custom "lct_venue"
post type, one plain server-rendered page per venue, and - usefully -
its own sitemap (lct_venue-sitemap.xml, confirmed 242 venue URLs). robots.txt
allows everything relevant. This just crawls every URL in that sitemap
directly, same shape as the TriviaNearMe directory importer.

Each venue page (Elementor-built, so the markup is verbose, but the
actual data is easy to find) carries:
  - day/time in an icon-list right under the header, identified by the
    calendar-check SVG icon (stable marker even though Elementor's CSS
    classes are otherwise auto-generated hashes)
  - a two-line address in an icon-box ("street" \n "City, ST ZIP")
  - city/state/game-type also encoded as CSS classes on the <div
    data-elementor-type="single-post" ...> element
    (lct_city-X, lct_state-X, lct_game_type-X) - used as a fallback/cross
    -check against the parsed address text, not the primary source

No phone, website, or lat/lng is exposed on these pages (just an embedded
Google Maps iframe built from the address text, no coordinates) - left
blank rather than guessed; the project's deferred geocoding pass would
fill lat/lng in later if wanted.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://lastcalltrivia.com"
SITEMAP_URL = f"{BASE_URL}/lct_venue-sitemap.xml"
SOURCE_NAME = "Last Call Trivia"

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)
_STATE_CLASS_RE = re.compile(r"lct_state-([a-z]+)")
_CITY_CLASS_RE = re.compile(r"lct_city-([a-z0-9-]+)")
_GAME_TYPE_CLASS_RE = re.compile(r"lct_game_type-([a-z0-9-]+)")


def discover_venue_urls(session: PoliteSession) -> list[str]:
    resp = session.get(SITEMAP_URL)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.content, "xml")
    # <loc> appears both for each <url> (the page) and, per Yoast/Rank
    # Math convention, inside nested <image:loc> tags for that page's
    # thumbnail - find_all("loc") returns both, so image URLs (anything
    # under /wp-content/uploads/) show up as bogus "venue pages" unless
    # filtered out here.
    urls = [loc.text.strip() for loc in soup.find_all("loc")]
    return [
        u
        for u in urls
        if u.startswith(f"{BASE_URL}/venues/") and u.rstrip("/") != f"{BASE_URL}/venues"
    ]


def _to_24h(hh: str, mm: str, meridiem: str) -> str:
    hour = int(hh)
    if meridiem.lower() == "pm" and hour != 12:
        hour += 12
    if meridiem.lower() == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{mm}"


def parse_venue_page(html: str, url: str) -> RawListing | None:
    soup = BeautifulSoup(html, "lxml")

    name_tag = soup.select_one("h1.elementor-heading-title")
    if not name_tag:
        return None
    venue_name = name_tag.get_text(strip=True)

    day_of_week = start_time = None
    calendar_icon = soup.select_one("svg.e-fas-calendar-check")
    if calendar_icon:
        icon_list = calendar_icon.find_parent("ul", class_="elementor-icon-list-items")
        if icon_list:
            texts = [
                li.get_text(strip=True)
                for li in icon_list.select("li .elementor-icon-list-text")
            ]
            if texts:
                day_raw = texts[0]
                day_of_week = day_raw[:-1] if day_raw.endswith("s") else day_raw  # "Mondays" -> "Monday"
                for t in texts:
                    m = _TIME_RE.search(t)
                    if m:
                        start_time = _to_24h(m.group(1), m.group(2), m.group(3))
                        break

    street = city = region = None
    address_span = soup.select_one("h3.elementor-icon-box-title span")
    if address_span:
        lines = [l.strip() for l in address_span.get_text("\n").split("\n") if l.strip()]
        if len(lines) >= 2:
            street = lines[0]
            m = re.match(r"(?P<city>.+?),\s*(?P<region>[A-Za-z]{2})\s*(?P<zip>[\d-]*)$", lines[1])
            if m:
                city, region = m.group("city"), m.group("region").upper()
            else:
                city = lines[1]
        elif len(lines) == 1:
            street = lines[0]

    # Cross-check / fallback from the taxonomy CSS classes.
    post_div = soup.select_one('div[data-elementor-type="single-post"]')
    post_classes = " ".join(post_div.get("class", [])) if post_div else ""
    if not region:
        m = _STATE_CLASS_RE.search(post_classes)
        if m:
            region = m.group(1).upper()
    if not city:
        m = _CITY_CLASS_RE.search(post_classes)
        if m:
            city = m.group(1).replace("-", " ").title()
    game_type_m = _GAME_TYPE_CLASS_RE.search(post_classes)
    game_type = game_type_m.group(1).replace("-", " ").title() if game_type_m else None

    return RawListing(
        venue_name=venue_name,
        street_address=street,
        city=city,
        region=region,
        country="United States",
        day_of_week=day_of_week,
        start_time=start_time,
        frequency=Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN,
        notes=f"{game_type} night." if game_type and game_type.lower() != "trivia" else None,
        provider_name="Last Call Trivia",
        provider_website=BASE_URL,
        source_url=url,
        raw_snapshot={"post_classes": post_classes},
    )


def run(session: PoliteSession, limit: int | None = None) -> list[RawListing]:
    urls = discover_venue_urls(session)
    print(f"[LastCallTrivia] {len(urls)} venue pages found in sitemap")
    if limit:
        urls = urls[:limit]

    listings: list[RawListing] = []
    for i, url in enumerate(urls, 1):
        resp = session.get(url)
        if resp is None:
            continue
        listing = parse_venue_page(resp.text, url)
        if listing:
            listings.append(listing)
            print(f"  [{i}/{len(urls)}] {url} -> {listing.venue_name} ({listing.day_of_week} {listing.start_time})")
        else:
            print(f"  [{i}/{len(urls)}] {url} -> failed to parse")

    return listings
