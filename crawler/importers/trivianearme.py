"""
Directory importer for TriviaNearMe.net (Phase 4).

Everything this pulls in is tagged SourceType.DIRECTORY and lands as
status=CANDIDATE (never auto-published) per the spec: directories are a
bootstrap source, not a trusted one.

Strategy: TriviaNearMe's sitemap.xml lists every city page directly
(confirmed ~2,353 of them), and each city page is a single server-rendered
HTML document listing every venue in that city with no pagination
(confirmed against /spokane - 15 venues, no "load more"/page 2 controls).
That means we don't need to touch the ~9,659 individual venue detail pages
at all - the city pages already contain every field we need per listing,
in far fewer requests. If TriviaNearMe ever changes their city pages to
paginate, this importer will need a follow-the-next-link loop added.

robots.txt (checked 2026-08-24) allows all of /, only disallows auth/admin/
search/form-submission paths - none of which this importer touches.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler.http_client import PoliteSession
from crawler.models import Frequency, SourceType
from crawler.pipeline import RawListing

BASE_URL = "https://trivianearme.net"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
SOURCE_NAME = "TriviaNearMe"

# Non-city top-level paths seen in the sitemap that must not be treated as
# city slugs (site nav pages, tools, account pages).
_NON_CITY_SLUGS = {
    "",
    "blog",
    "join",
    "sign-in",
    "sign-up",
    "partners",
    "question-writer",
    "stats",
    "team-names",
    "trivia-questions",
    "trivia-quizzes",
    "trivia-team-name-generator",
    "trivia-team-names",
    "group-chat-names",
    "fantasy-football-team-names",
    "tonight",
    "corrections",
}
_NON_CITY_PREFIXES = ("venues", "companies", "states", "countries", "trivia-near-me")

_GMAPS_LATLNG_RE = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")

# Countries seen in TriviaNearMe addresses as an explicit trailing
# ", Country" segment (US addresses don't carry one - country is inferred
# as US downstream when this comes back empty and a US-shaped state/zip
# was found).
_COUNTRY_NAMES = {"australia", "new zealand", "canada", "united kingdom", "ireland"}

# Strips a trailing postal code off a "STATE 12345" / "ACT 2611"-style
# chunk, leaving just the state/province/territory name behind.
_TRAILING_POSTAL_RE = re.compile(r"^(?P<region>.+?)\s+(?P<postal>[A-Za-z0-9]{3,10})$")

# "City STATE 12345" all run together with no comma (AU/NZ addresses
# often write it this way) - splits into city + the state/postal remainder.
_CITY_REGION_RUN_TOGETHER_RE = re.compile(r"^(?P<city>.+?)\s+(?P<rest>[A-Z]{2,3}\s+[A-Za-z0-9]{3,10})$")


def discover_city_urls(session: PoliteSession) -> list[str]:
    """Pull every city-page URL out of the sitemap rather than guessing at
    one - the sitemap is authoritative and self-updating as TriviaNearMe
    adds coverage.
    """
    resp = session.get(SITEMAP_URL)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.content, "xml")
    urls = [loc.text.strip() for loc in soup.find_all("loc")]

    city_urls = []
    for url in urls:
        path = url[len(BASE_URL):].strip("/")
        if not path or "/" in path:
            continue  # skip homepage and anything with a sub-path (venues/companies/states/etc.)
        if path in _NON_CITY_SLUGS or path.startswith(_NON_CITY_PREFIXES):
            continue
        city_urls.append(url)
    return city_urls


def _strip_postal(chunk: str) -> str:
    m = _TRAILING_POSTAL_RE.match(chunk)
    return m.group("region").strip() if m else chunk


def _parse_address(
    address_text: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """'6314 N Ash St, Spokane, WA 99208' -> (street, city, region, country).
    Also handles the AU/NZ shape '6 Parkinson St, Weston ACT 2611, Australia'
    where city/state/postal run together with no comma. Best-effort - this
    is directory data, gets re-verified before it's ever trusted, so an
    imperfect split just means a slightly messy candidate row, not a wrong
    published fact.
    """
    parts = [p.strip() for p in address_text.split(",") if p.strip()]

    country = None
    if parts and parts[-1].lower() in _COUNTRY_NAMES:
        country = parts[-1]
        parts = parts[:-1]

    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        region = _strip_postal(parts[-1])
        return street or None, city, region, country

    if len(parts) == 2:
        run_together = _CITY_REGION_RUN_TOGETHER_RE.match(parts[1])
        if run_together:
            return parts[0], run_together.group("city"), _strip_postal(run_together.group("rest")), country
        return parts[0], parts[1], None, country

    if len(parts) == 1:
        return None, parts[0], None, country

    return None, None, None, country


_HOSTED_BY_TEXT_RE = re.compile(r"[Hh]osted by\s+@?([A-Z][\w&']*(?:\s+[A-Z][\w&']*){0,3})")


def _parse_day_time(text: str) -> tuple[str | None, str | None]:
    """'Tuesday\\n at 7:00 PM' -> ('Tuesday', '19:00')."""
    text = " ".join(text.split())
    match = re.match(r"([A-Za-z]+)(?:\s+at\s+(.+))?", text)
    if not match:
        return None, None
    day = match.group(1)
    time_str = match.group(2)
    start_time = None
    if time_str:
        t = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_str.strip(), re.IGNORECASE)
        if t:
            hour, minute, meridiem = int(t.group(1)), t.group(2), t.group(3).upper()
            if meridiem == "PM" and hour != 12:
                hour += 12
            if meridiem == "AM" and hour == 12:
                hour = 0
            start_time = f"{hour:02d}:{minute}"
    return day, start_time


def parse_city_page(html: bytes, page_url: str) -> list[RawListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[RawListing] = []

    for entry in soup.select('div[id^="entry_"]'):
        try:
            name_tag = entry.select_one("h3")
            if not name_tag:
                continue
            venue_name = name_tag.get_text(strip=True)

            detail_link = entry.select_one('a[data-turbo-frame="_top"]')
            detail_url = urljoin(page_url, detail_link["href"]) if detail_link and detail_link.get("href") else page_url

            info_paragraphs = entry.select("p.mt-1.text-sm.text-gray-600")
            address_text = None
            provider_name = None
            provider_href = None
            for p in info_paragraphs:
                text = p.get_text(strip=True)
                if text.startswith("Hosted by"):
                    company_link = p.select_one("a")
                    if company_link:
                        provider_name = company_link.get_text(strip=True)
                        provider_href = company_link.get("href")
                elif address_text is None:
                    address_text = text

            street, city, region, country = (None, None, None, None)
            if address_text:
                street, city, region, country = _parse_address(address_text)

            phone_tag = entry.select_one('a[href^="tel:"]')
            phone = phone_tag.get_text(strip=True) if phone_tag else None

            latitude = longitude = None
            map_link = entry.find("a", string=re.compile(r"\s*Map\s*"))
            if map_link and map_link.get("href"):
                m = _GMAPS_LATLNG_RE.search(map_link["href"])
                if m:
                    latitude, longitude = float(m.group(1)), float(m.group(2))

            website_link = entry.find("a", string=re.compile(r"\s*Website\s*"))
            venue_website = website_link.get("href") if website_link else None

            desc_tag = entry.select_one("div.text-gray-600.text-sm p")
            notes = desc_tag.get_text(strip=True) if desc_tag else None

            # Some entries have no separate "Hosted by <link>" line (the
            # company doesn't have a /companies/ page on this site) but
            # still name their host in the free-text description, e.g.
            # "Hosted by @KingTrivia. Trivia Tuesday from 7-9pm." Fall
            # back to pulling the name out of that text so we don't lose
            # the provider link entirely - just without a website URL,
            # which Phase 11 self-expansion would need to go find later.
            if not provider_name and notes:
                m = _HOSTED_BY_TEXT_RE.search(notes)
                if m:
                    provider_name = m.group(1).strip()

            day_of_week = start_time = None
            calendar_icon = entry.select_one("i.fa-calendar")
            if calendar_icon:
                span = calendar_icon.find_parent("div").select_one("span.font-medium")
                if span:
                    day_of_week, start_time = _parse_day_time(span.get_text(" ", strip=True))

            listings.append(
                RawListing(
                    venue_name=venue_name,
                    street_address=street,
                    city=city,
                    region=region,
                    country=country,  # None for US addresses (no trailing country segment) - fine, geocoding fills gaps
                    latitude=latitude,
                    longitude=longitude,
                    phone=phone,
                    venue_website=venue_website,
                    day_of_week=day_of_week,
                    start_time=start_time,
                    frequency=Frequency.WEEKLY if day_of_week else Frequency.UNKNOWN,
                    notes=notes,
                    provider_name=provider_name,
                    provider_website=urljoin(BASE_URL, provider_href) if provider_href else None,
                    source_url=detail_url,
                    raw_snapshot={"page_url": page_url, "address_text": address_text},
                )
            )
        except Exception as exc:  # one bad card should never abort the whole page
            print(f"  [warn] failed to parse an entry on {page_url}: {exc}")
            continue

    return listings


def run(session: PoliteSession, limit_cities: int | None = None) -> list[RawListing]:
    """Fetch and parse every (or, if limit_cities is set, the first N)
    city page. Returns the full flat list of RawListing - caller is
    responsible for opening a DB session and calling ingest_listing().
    """
    city_urls = discover_city_urls(session)
    print(f"[TriviaNearMe] {len(city_urls)} city pages found in sitemap")
    if limit_cities:
        city_urls = city_urls[:limit_cities]

    all_listings: list[RawListing] = []
    for i, url in enumerate(city_urls, 1):
        resp = session.get(url)
        if resp is None:
            continue
        listings = parse_city_page(resp.content, url)
        all_listings.extend(listings)
        print(f"  [{i}/{len(city_urls)}] {url} -> {len(listings)} listings")

    return all_listings
