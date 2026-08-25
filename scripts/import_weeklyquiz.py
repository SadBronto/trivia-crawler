#!/usr/bin/env python
"""Run the Weekly Quiz first-party adapter and ingest results.

Usage:
    python scripts/import_weeklyquiz.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import weeklyquiz
from crawler.db import get_session, init_db
from crawler.http_client import PoliteSession
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()
    http = PoliteSession()

    listings = weeklyquiz.run(http)
    print(f"\n[WeeklyQuiz] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=weeklyquiz.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=weeklyquiz.BASE_URL,
            adapter_name="weeklyquiz",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[WeeklyQuiz] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
