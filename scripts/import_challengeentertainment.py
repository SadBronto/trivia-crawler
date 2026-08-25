#!/usr/bin/env python
"""Run the Challenge Entertainment first-party adapter and ingest results.

A single unfiltered request returns their whole national dataset - no
--limit flag needed, there's nothing to page through.

Usage:
    python scripts/import_challengeentertainment.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import challengeentertainment
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()
    http = PoliteSession()

    listings = challengeentertainment.run(http)
    print(f"\n[ChallengeEntertainment] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=challengeentertainment.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=challengeentertainment.BASE_URL,
            adapter_name="challengeentertainment",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[ChallengeEntertainment] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
