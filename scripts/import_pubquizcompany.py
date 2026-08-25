#!/usr/bin/env python
"""Run The Pub Quiz Company first-party adapter and ingest results.

Usage:
    python scripts/import_pubquizcompany.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import pubquizcompany
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()
    http = PoliteSession()

    listings = pubquizcompany.run(http)
    print(f"\n[PubQuizCompany] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=pubquizcompany.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=pubquizcompany.BASE_URL,
            adapter_name="pubquizcompany",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[PubQuizCompany] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
