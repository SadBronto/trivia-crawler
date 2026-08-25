#!/usr/bin/env python
"""Run the Geeks Who Drink first-party adapter and ingest results.

Usage:
    python scripts/import_geekswhodrink.py                # default: 4 redundant queries
    python scripts/import_geekswhodrink.py --limit 1       # smoke test, single query

Runs at a slower, dedicated pace (GWD_DELAY_SECONDS below) than the
project's general default, since this site has already shown it's
watching for unusual traffic. See crawler/adapters/geekswhodrink.py's
module docstring for why this makes only a handful of requests total
(real geo-filtering on this endpoint turned out to be unreliable, not
worth chasing with more live requests).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import geekswhodrink
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing

GWD_DELAY_SECONDS = 4.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=4, help="Number of redundant queries to make (default 4)")
    args = parser.parse_args()

    init_db()
    http = PoliteSession(delay_seconds=GWD_DELAY_SECONDS)

    listings = geekswhodrink.run(http, num_queries=args.limit)
    print(f"\n[GeeksWhoDrink] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=geekswhodrink.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=geekswhodrink.BASE_URL,
            adapter_name="geekswhodrink",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[GeeksWhoDrink] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
