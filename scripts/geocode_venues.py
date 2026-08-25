#!/usr/bin/env python
"""Fill in missing lat/lng for venues that have a street address but no
coordinates yet (see crawler/geocode.py for why some sources have real
coordinates already and others - notably TriviaNearMe - don't).

Two-pass strategy:
  1. US Census Bureau batch geocoder for every US-or-unknown-country venue
     with a street address - free, no key, handles thousands per request.
  2. Mapbox (existing TWC token) for (a) explicitly international venues
     the Census API can't handle, and (b) anything the Census batch
     didn't match, one address at a time.

Usage:
    python scripts/geocode_venues.py                 # both passes
    python scripts/geocode_venues.py --census-only
    python scripts/geocode_venues.py --mapbox-only
    python scripts/geocode_venues.py --limit 50       # smoke test
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from crawler.db import get_session, init_db
from crawler.geocode import census_batch_geocode, mapbox_geocode
from crawler.models import Venue

MAPBOX_DELAY_SECONDS = 0.15  # ~6-7 req/sec, comfortably inside Mapbox's free-tier rate limit


def run_census_pass(limit: int | None) -> tuple[int, int]:
    with get_session() as session:
        query = select(Venue).where(
            Venue.latitude.is_(None),
            Venue.street_address.is_not(None),
            (Venue.country.is_(None)) | (Venue.country == "United States"),
        )
        if limit:
            query = query.limit(limit)
        venues = session.execute(query).scalars().all()

        if not venues:
            print("[Census] nothing to geocode")
            return 0, 0

        print(f"[Census] {len(venues)} venues to geocode")
        matched_total = 0
        # Stay comfortably under the 10,000-per-request cap even though
        # our current volume fits in one call - chunking means a hiccup
        # only costs one chunk's retry, not the whole run.
        chunk_size = 5000
        for i in range(0, len(venues), chunk_size):
            chunk = venues[i : i + chunk_size]
            rows = [(v.id, v.street_address, v.city or "", v.region or "") for v in chunk]
            print(f"  submitting batch {i // chunk_size + 1} ({len(rows)} addresses)...")
            results = census_batch_geocode(rows)
            by_id = {v.id: v for v in chunk}
            for venue_id, (lat, lon) in results.items():
                by_id[venue_id].latitude = lat
                by_id[venue_id].longitude = lon
            matched_total += len(results)
            print(f"  batch {i // chunk_size + 1}: {len(results)}/{len(rows)} matched")

        session.commit()
        return len(venues), matched_total


def run_mapbox_pass(limit: int | None) -> tuple[int, int]:
    with get_session() as session:
        query = select(Venue).where(Venue.latitude.is_(None), Venue.street_address.is_not(None))
        if limit:
            query = query.limit(limit)
        venues = session.execute(query).scalars().all()

        if not venues:
            print("[Mapbox] nothing left to geocode")
            return 0, 0

        print(f"[Mapbox] {len(venues)} venues to geocode (international + Census no-matches)")
        matched = 0
        for i, v in enumerate(venues, 1):
            query_str = ", ".join(p for p in (v.street_address, v.city, v.region, v.country) if p)
            result = mapbox_geocode(query_str)
            if result:
                v.latitude, v.longitude = result
                matched += 1
            if i % 100 == 0 or i == len(venues):
                print(f"  [{i}/{len(venues)}] {matched} matched so far")
            time.sleep(MAPBOX_DELAY_SECONDS)

        session.commit()
        return len(venues), matched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census-only", action="store_true")
    parser.add_argument("--mapbox-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Cap venues processed per pass (for testing)")
    args = parser.parse_args()

    init_db()

    if not args.mapbox_only:
        total, matched = run_census_pass(args.limit)
        print(f"[Census] done: {matched}/{total} matched\n")

    if not args.census_only:
        total, matched = run_mapbox_pass(args.limit)
        print(f"[Mapbox] done: {matched}/{total} matched\n")


if __name__ == "__main__":
    main()
