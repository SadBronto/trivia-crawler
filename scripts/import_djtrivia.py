#!/usr/bin/env python
"""Run the DJ Trivia first-party adapter and ingest results.

Data-gathering only - see crawler/adapters/djtrivia.py's module docstring.

Usage:
    python scripts/import_djtrivia.py                # full run, all ~110 metro centers
    python scripts/import_djtrivia.py --limit 5       # smoke test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import djtrivia
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only query the first N metro centers (for testing)")
    args = parser.parse_args()

    init_db()
    http = PoliteSession()

    listings = djtrivia.run(http, limit_centers=args.limit)
    print(f"\n[DJTrivia] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=djtrivia.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=djtrivia.BASE_URL,
            adapter_name="djtrivia",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[DJTrivia] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
