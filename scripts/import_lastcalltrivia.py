#!/usr/bin/env python
"""Run the Last Call Trivia first-party adapter and ingest results.

Usage:
    python scripts/import_lastcalltrivia.py                # full run, all ~242 venues
    python scripts/import_lastcalltrivia.py --limit 5       # smoke test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import lastcalltrivia
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only crawl the first N venue pages (for testing)")
    args = parser.parse_args()

    init_db()
    http = PoliteSession()

    listings = lastcalltrivia.run(http, limit=args.limit)
    print(f"\n[LastCallTrivia] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=lastcalltrivia.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=lastcalltrivia.BASE_URL,
            adapter_name="lastcalltrivia",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[LastCallTrivia] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
