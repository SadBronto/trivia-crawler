#!/usr/bin/env python
"""Run the Goose's Quizzes first-party adapter (Playwright-based) and ingest.

Usage:
    python scripts/import_goosesquizzes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import goosesquizzes
from crawler.db import get_session, init_db
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()

    listings = goosesquizzes.run()
    print(f"\n[GoosesQuizzes] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=goosesquizzes.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=goosesquizzes.BASE_URL,
            adapter_name="goosesquizzes",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[GoosesQuizzes] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
