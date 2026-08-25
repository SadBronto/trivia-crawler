#!/usr/bin/env python
"""Run the Geeks Who Drink first-party adapter and ingest results.

Usage:
    python scripts/import_geekswhodrink.py                # full run, all 50 states + DC
    python scripts/import_geekswhodrink.py --limit 1       # smoke test, one state
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only query the first N states (for testing)")
    args = parser.parse_args()

    init_db()
    http = PoliteSession()

    listings = geekswhodrink.run(http, limit_states=args.limit)
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
