"""
One-time repair pass for city/region/country data corruption discovered live
on triviabyme.com (2026-08-25): several source adapters (Inquizition,
TriviaNearMe, Question One for the UK; Fame Trivia for Australia; Quiz
Meisters for a single Australian venue) put the wrong text into the wrong
field - a UK postcode or the street address itself into `city`, "UK" (a
country, not a region) into `region`, a schedule note like "(2nd Tuesday
each month)" appended to a city name, a state code ("QLD") into `country`.

Rather than write increasingly specific regexes to parse each adapter's
particular way of getting this wrong, this derives city/region/country
directly from each venue's own latitude/longitude via Mapbox's reverse
geocoding API - authoritative, adapter-agnostic, and correct regardless of
what the scraped text said. Every venue already has coordinates from the
forward-geocoding step, so this is trustworthy for the same reason that
step was: it's real geographic data, not scraped/parsed free text.

Only touches venues behind at least one confidence>=60 event (the ones that
actually reach the TBM sheet export) and that have coordinates. Caches by
rounded (lat, lon) so venues sharing a location (multiple sources observing
the same real place) cost one API call, not N.

Run with MAPBOX_ACCESS_TOKEN set. Safe to re-run - it's idempotent (same
coordinates always resolve to the same place names, so a second run is a
no-op other than the API calls themselves).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.geocode import mapbox_reverse_geocode  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trivia.db"
WORKERS = 6  # comfortably under Mapbox's free-tier ~600 req/min cap

# Mapbox returns US states as full names ("Texas"), but TWC's own sign-up
# form (Form Responses 1) has always used 2-letter codes via a dropdown -
# that's the one part of this whole mess that was never actually broken.
# Converting the crawler side to match TWC's existing convention (not the
# other way around) is what keeps "TX" and "Texas" from becoming a *new*
# duplicate-region bug the moment this repair runs.
US_STATE_TO_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "District of Columbia": "DC", "Puerto Rico": "PR",
}


def normalize_region(region: str | None, country: str | None) -> str | None:
    if not region:
        return region
    if country == "United States":
        return US_STATE_TO_ABBR.get(region, region)
    return region


def fetch_targets(conn: sqlite3.Connection) -> list[tuple[int, float, float, str, str, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT v.id, v.latitude, v.longitude, v.city, v.region, v.country
        FROM venues v
        JOIN trivia_events te ON te.venue_id = v.id
        WHERE te.confidence_score >= 60
          AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
        """
    )
    return cur.fetchall()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    targets = fetch_targets(conn)
    print(f"[repair_locations] {len(targets)} venues to check.")

    # Cache reverse-geocode results by rounded coordinate so venues at (or
    # very near) the same real place only cost one API call.
    coord_cache: dict[tuple[float, float], dict | None] = {}

    def lookup(lat: float, lon: float) -> dict | None:
        key = (round(lat, 4), round(lon, 4))
        if key not in coord_cache:
            for attempt in range(3):
                try:
                    coord_cache[key] = mapbox_reverse_geocode(lat, lon)
                    break
                except Exception as exc:  # network hiccup - retry a couple times
                    if attempt == 2:
                        print(f"[repair_locations] lookup failed for {key}: {exc}")
                        coord_cache[key] = None
                    else:
                        time.sleep(1)
        return coord_cache[key]

    updates: list[tuple[str, str, str, int]] = []  # (city, region, country, venue_id)
    changed = 0
    unresolved = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(lookup, lat, lon): (venue_id, lat, lon, city, region, country)
            for venue_id, lat, lon, city, region, country in targets
        }
        for i, future in enumerate(as_completed(futures), 1):
            venue_id, lat, lon, old_city, old_region, old_country = futures[future]
            result = future.result()
            if not result or not result.get("city"):
                unresolved += 1
                continue
            new_city = result.get("city")
            new_country = result.get("country") or old_country
            new_region = normalize_region(result.get("region"), new_country)  # can be None
            if (new_city, new_region or "", new_country or "") != (
                old_city or "",
                old_region or "",
                old_country or "",
            ):
                changed += 1
            updates.append((new_city, new_region, new_country, venue_id))
            if i % 250 == 0:
                print(f"[repair_locations] {i}/{len(targets)} resolved...")

    print(f"[repair_locations] Done resolving. {changed} venues will change, {unresolved} unresolved (left untouched).")

    cur = conn.cursor()
    cur.executemany(
        "UPDATE venues SET city = ?, region = ?, country = ? WHERE id = ?",
        updates,
    )
    conn.commit()
    print(f"[repair_locations] Wrote {len(updates)} venue rows.")
    conn.close()


if __name__ == "__main__":
    main()
