#!/usr/bin/env python
"""Run the Sphinx UK first-party adapter (Playwright-based) and ingest.

Usage:
    python scripts/import_sphinxuk.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.adapters import sphinxuk
from crawler.db import get_session, init_db
from crawler.models import SourceType
from crawler.pipeline import get_or_create_source, ingest_listing


def main() -> None:
    init_db()

    listings = sphinxuk.run()
    print(f"\n[SphinxUK] {len(listings)} total listings scraped, ingesting...")

    with get_session() as session:
        source = get_or_create_source(
            session,
            name=sphinxuk.SOURCE_NAME,
            source_type=SourceType.PROVIDER_SCHEDULE,
            base_url=sphinxuk.BASE_URL,
            adapter_name="sphinxuk",
        )
        for listing in listings:
            ingest_listing(session, source, listing)

    print(f"[SphinxUK] done - {len(listings)} listings ingested (see data/trivia.db)")


if __name__ == "__main__":
    main()
