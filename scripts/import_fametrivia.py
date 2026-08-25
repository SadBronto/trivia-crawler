#!/usr/bin/env python
"""Run the Fame Trivia first-party adapter and ingest results.

Respects their robots.txt Crawl-Delay: 20 - 8 state pages, ~3 minutes total.

Usage:
    python scripts/import_fametrivia.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import fametrivia
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()
    http = PoliteSession(delay_seconds=fametrivia.CRAWL_DELAY_SECONDS)

    listings = fametrivia.run(http)
    print(f"\n[FameTrivia] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=fametrivia.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=fametrivia.BASE_URL,
            adapter_name="fametrivia",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[FameTrivia] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
