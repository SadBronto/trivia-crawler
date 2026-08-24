#!/usr/bin/env python
"""Run the TriviaNearMe directory importer and ingest results into the DB.

Usage:
    python scripts/import_trivianearme.py                # full run, all cities
    python scripts/import_trivianearme.py --limit 5       # smoke test, first 5 cities
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.importers import trivianearme
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only crawl the first N city pages (for testing)")
    args = parser.parse_args()

    init_db()
    http = PoliteSession()

    listings = trivianearme.run(http, limit_cities=args.limit)
    print(f"\n[TriviaNearMe] {len(listings)} total listings scraped, ingesting...")

    new_count = 0
    with get_session() as session:
        source = get_or_create_source(
            session,
            name=trivianearme.SOURCE_NAME,
            source_type=SourceType.DIRECTORY,
            base_url=trivianearme.BASE_URL,
            adapter_name="trivianearme",
        )
        for listing in listings:
            ingest_listing(session, source, listing)
            new_count += 1

    print(f"[TriviaNearMe] done - {new_count} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
