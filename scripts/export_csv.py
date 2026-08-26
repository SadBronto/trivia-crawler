#!/usr/bin/env python
"""Export the current DB to a spreadsheet-friendly CSV in exports/.

Two files are written:
  - exports/trivia_events_all.csv     every event, every status - this is
    the one meant for copy-pasting wholesale into a Google Sheet. It
    already carries `source_name` and `confidence_score` per row, so
    filtering which rows actually show up on a given site's map is a
    decision made where the data is *displayed* (a Sheet filter/view, or
    the site's own query), not something this export pre-decides.
  - exports/trivia_events_verified.csv  the same data pre-filtered to
    confidence_score >= 60 and status=active, kept around for a quick
    look at "what's already strong" without opening a spreadsheet.

This script only ever reads the local crawler DB - it never writes to
TWC's, TriviaByMe's, or anyone else's Sheet.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from crawler.db import get_session
from crawler.models import EventStatus, TriviaEvent
from crawler.scoring import PUBLISH_THRESHOLD

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_DIR = REPO_ROOT / "exports"

FIELDNAMES = [
    "event_id",
    "venue_name",
    "street_address",
    "city",
    "region",
    "country",
    "latitude",
    "longitude",
    "day_of_week",
    "start_time",
    "timezone",
    "frequency",
    "provider_name",
    "phone",
    "venue_website",
    "notes",
    "source_name",
    "source_url",
    "date_first_discovered",
    "date_last_confirmed",
    "confidence_score",
    "status",
]


def _row(event: TriviaEvent) -> dict:
    venue = event.venue
    return {
        "event_id": event.id,
        "venue_name": venue.name,
        "street_address": venue.street_address,
        "city": venue.city,
        "region": venue.region,
        "country": venue.country,
        "latitude": venue.latitude,
        "longitude": venue.longitude,
        "day_of_week": event.day_of_week,
        "start_time": event.start_time,
        "timezone": event.timezone,
        "frequency": event.frequency.value if event.frequency else "",
        "provider_name": event.provider.name if event.provider else event.raw_provider_name,
        "phone": venue.phone,
        "venue_website": venue.website,
        "notes": event.notes,
        "source_name": event.source.name if event.source else "",
        "source_url": event.source_url,
        "date_first_discovered": event.date_first_discovered,
        "date_last_confirmed": event.date_last_confirmed,
        "confidence_score": event.confidence_score,
        "status": event.status.value if event.status else "",
    }


def main() -> None:
    EXPORTS_DIR.mkdir(exist_ok=True)
    with get_session() as session:
        stmt = select(TriviaEvent).options(
            selectinload(TriviaEvent.venue),
            selectinload(TriviaEvent.provider),
            selectinload(TriviaEvent.source),
        )
        events = session.execute(stmt).scalars().all()
        rows = [_row(event) for event in events]

    all_path = EXPORTS_DIR / "trivia_events_all.csv"
    verified_path = EXPORTS_DIR / "trivia_events_verified.csv"

    with all_path.open("w", newline="", encoding="utf-8") as f_all, verified_path.open(
        "w", newline="", encoding="utf-8"
    ) as f_verified:
        writer_all = csv.DictWriter(f_all, fieldnames=FIELDNAMES)
        writer_verified = csv.DictWriter(f_verified, fieldnames=FIELDNAMES)
        writer_all.writeheader()
        writer_verified.writeheader()

        verified_count = 0
        for row in rows:
            writer_all.writerow(row)
            if row["confidence_score"] >= PUBLISH_THRESHOLD and row["status"] == EventStatus.ACTIVE.value:
                writer_verified.writerow(row)
                verified_count += 1

    print(f"Wrote {len(rows)} rows -> {all_path}")
    print(f"Wrote {verified_count} rows -> {verified_path}")


if __name__ == "__main__":
    main()
