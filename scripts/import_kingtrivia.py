#!/usr/bin/env python
"""Run the King Trivia first-party adapter and ingest results.

Usage:
    python scripts/import_kingtrivia.py                 # full run, all metro centers
    python scripts/import_kingtrivia.py --limit 1        # smoke test, one metro
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import kingtrivia
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only query the first N metro centers (for testing)")
    parser.add_argument(
        "--all-centers",
        action="store_true",
        help="Query all ~110 metro centers instead of the default 7-city redundancy set (see adapter docstring - not expected to find more, just extra insurance)",
    )
    args = parser.parse_args()

    init_db()
    http = PoliteSession()

    listings = kingtrivia.run(http, limit_centers=args.limit, all_centers=args.all_centers)
    print(f"\n[KingTrivia] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=kingtrivia.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=kingtrivia.BASE_URL,
            adapter_name="kingtrivia",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[KingTrivia] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
