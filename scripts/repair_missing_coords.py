"""
Second repair pass, following scripts/repair_locations.py. That pass
reverse-geocoded every venue that already HAD coordinates but wrong text.
This one covers the other half: venues with NO coordinates at all (mainly
Sphinx UK and The Pub Quiz Company - their adapters were never able to
capture lat/lng from the source pages) whose city text is otherwise fine
("Liverpool", "Manchester", etc.) but whose region always ends up blank
as a result - which is why they all landed in a country's "Other" bucket
on triviabyme.com even though most of them have a perfectly good, real
city name that obviously belongs under a real region (real complaint hit
live: "why is London listed in Other? You KNOW that's in England").

Two-hop geocode per distinct (city, country): forward-geocode "city,
country" to get coordinates, then reverse-geocode those coordinates the
same way repair_locations.py does, so the result is derived the same
consistent way either direction - not just "good enough to have SOME
coordinates" but the actual canonical city/region/country Mapbox agrees
on for that place.

Run with MAPBOX_ACCESS_TOKEN set. Safe to re-run.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.geocode import mapbox_geocode, mapbox_reverse_geocode  # noqa: E402
from crawler.normalize import canonical_country  # noqa: E402
from scripts.repair_locations import normalize_region  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trivia.db"


def fetch_targets(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT v.id, v.city, v.country
        FROM venues v
        JOIN trivia_events te ON te.venue_id = v.id
        WHERE te.confidence_score >= 60
          AND (v.latitude IS NULL OR v.longitude IS NULL)
          AND v.city IS NOT NULL AND v.city != ''
        """
    )
    return cur.fetchall()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    targets = fetch_targets(conn)
    print(f"[repair_missing_coords] {len(targets)} venues to check.")

    cache: dict[tuple[str, str], dict | None] = {}
    updates = []
    changed = 0
    unresolved = 0

    for i, (venue_id, city, country) in enumerate(targets, 1):
        canon_country = canonical_country(country) or ""
        cache_key = (city.strip().lower(), canon_country.lower())
        if cache_key not in cache:
            query = f"{city}, {canon_country}" if canon_country else city
            coords = None
            for attempt in range(3):
                try:
                    coords = mapbox_geocode(query)
                    break
                except Exception as exc:
                    if attempt == 2:
                        print(f"[repair_missing_coords] forward geocode failed for {query!r}: {exc}")
                    else:
                        time.sleep(1)
            if not coords:
                cache[cache_key] = None
            else:
                lat, lon = coords
                result = None
                for attempt in range(3):
                    try:
                        result = mapbox_reverse_geocode(lat, lon)
                        break
                    except Exception as exc:
                        if attempt == 2:
                            print(f"[repair_missing_coords] reverse geocode failed for {(lat, lon)}: {exc}")
                        else:
                            time.sleep(1)
                if result:
                    result = {**result, "lat": lat, "lon": lon}
                cache[cache_key] = result

        result = cache[cache_key]
        if not result or not result.get("city"):
            unresolved += 1
            continue

        new_country = result.get("country") or canon_country
        new_region = normalize_region(result.get("region"), new_country)
        updates.append((result["city"], new_region, new_country, result["lat"], result["lon"], venue_id))
        changed += 1
        if i % 50 == 0:
            print(f"[repair_missing_coords] {i}/{len(targets)} resolved...")

    print(f"[repair_missing_coords] Done. {changed} venues will get coordinates+region, {unresolved} unresolved (left untouched).")

    cur = conn.cursor()
    cur.executemany(
        "UPDATE venues SET city = ?, region = ?, country = ?, latitude = ?, longitude = ? WHERE id = ?",
        updates,
    )
    conn.commit()
    print(f"[repair_missing_coords] Wrote {len(updates)} venue rows.")
    conn.close()


if __name__ == "__main__":
    main()
