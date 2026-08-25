#!/usr/bin/env python
"""Run the Inquizition first-party adapter (Playwright-based) and ingest.

Usage:
    python scripts/import_inquizition.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import inquizition
from crawler.db import get_session, init_db
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()

    listings = inquizition.run()
    print(f"\n[Inquizition] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=inquizition.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=inquizition.BASE_URL,
            adapter_name="inquizition",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[Inquizition] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
