#!/usr/bin/env python
"""Run every importer, geocode anything new, then retire events that have
stopped appearing at their source. This is what the weekly GitHub Actions
crawl runs (see .github/workflows/crawl.yml); it also works locally.

Why this exists: the workflow used to run only the TriviaNearMe importer,
and that one has been getting HTTP 403 from GitHub's servers and silently
ingesting zero listings - so nothing was actually being refreshed. This
runner makes that kind of failure visible instead of silent:

  - Each importer runs in its own subprocess with a timeout. One source
    failing or being blocked never stops the others.
  - After each importer we count how many events it actually re-confirmed.
    An importer that exits cleanly but confirmed nothing is reported as
    FAILED, not as a success.
  - Everything is summarized (and written to the GitHub Actions job
    summary when running there). The process exits non-zero only if NO
    source refreshed anything, so a total outage turns the run red and
    GitHub emails the repo owner, while one flaky source doesn't.

Sweep rule (see sweep_missing_events): an event whose primary source ran
successfully but no longer lists it is counted as a missed check; after
STALE_AFTER_MISSES consecutive misses it becomes STALE and drops out of the
published exports. If the source later lists it again it comes back ACTIVE
automatically (pipeline.ingest_listing does that). A source whose run only
re-confirmed a small fraction of its events is treated as a partial/failed
scrape and is NOT swept, so a transient outage can never mass-retire events.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update

from crawler.db import get_session, init_db
from crawler.models import EventStatus, Source, TriviaEvent

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

# First-party schedules first (they're the data TriviaByMe publishes), the
# directory last since it's the one that blocks datacenter IPs.
IMPORTERS = [
    "import_djtrivia.py",
    "import_challengeentertainment.py",
    "import_geekswhodrink.py",
    "import_kingtrivia.py",
    "import_lastcalltrivia.py",
    "import_triviamafia.py",
    "import_quizmeisters.py",
    "import_questionone.py",
    "import_fametrivia.py",
    "import_inquizition.py",
    "import_weeklyquiz.py",
    "import_sphinxuk.py",
    "import_pubquizcompany.py",
    "import_goosesquizzes.py",
    "import_trivianearme.py",
]

IMPORTER_TIMEOUT_SECONDS = 45 * 60
STALE_AFTER_MISSES = 2          # weekly runs -> two missed weeks
MIN_REFRESH_FRACTION_FOR_SWEEP = 0.7


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def count_refreshed_since(since: datetime) -> int:
    with get_session() as session:
        return session.execute(
            select(func.count()).select_from(TriviaEvent).where(TriviaEvent.date_last_confirmed >= since)
        ).scalar_one()


def run_importer(script: str) -> dict:
    started = utcnow()
    t0 = time.time()
    result = {"script": script, "ok": False, "refreshed": 0, "seconds": 0, "note": ""}
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=IMPORTER_TIMEOUT_SECONDS,
        )
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
        if proc.returncode != 0:
            result["note"] = f"exit {proc.returncode}: {tail[-300:]}"
        result["refreshed"] = count_refreshed_since(started)
        if proc.returncode == 0 and result["refreshed"] > 0:
            result["ok"] = True
        elif proc.returncode == 0:
            blocked = "HTTP 403" in (proc.stdout + proc.stderr)
            result["note"] = ("blocked (HTTP 403) - " if blocked else "confirmed 0 events - ") + tail[-200:].replace("\n", " | ")
    except subprocess.TimeoutExpired:
        result["refreshed"] = count_refreshed_since(started)
        result["note"] = f"timed out after {IMPORTER_TIMEOUT_SECONDS // 60} min"
    result["seconds"] = round(time.time() - t0)
    return result


def run_geocoding() -> str:
    # The Census pass is free and keyless; the Mapbox pass needs the token.
    flag = [] if os.environ.get("MAPBOX_ACCESS_TOKEN") else ["--census-only"]
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "geocode_venues.py"), *flag],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30 * 60,
    )
    return f"geocode exit {proc.returncode}" + ("" if flag == [] else " (census only - no Mapbox token set)")


def sweep_missing_events(run_started: datetime) -> list[str]:
    lines: list[str] = []
    with get_session() as session:
        for source in session.execute(select(Source)).scalars():
            base = (TriviaEvent.source_id == source.id, TriviaEvent.status != EventStatus.REMOVED)
            total = session.execute(select(func.count()).select_from(TriviaEvent).where(*base)).scalar_one()
            if total == 0:
                continue
            seen = session.execute(
                select(func.count()).select_from(TriviaEvent).where(*base, TriviaEvent.date_last_confirmed >= run_started)
            ).scalar_one()
            if seen == 0:
                lines.append(f"{source.name}: not refreshed this run - sweep skipped")
                continue
            if seen / total < MIN_REFRESH_FRACTION_FOR_SWEEP:
                lines.append(f"{source.name}: only {seen}/{total} confirmed (partial run) - sweep skipped")
                continue
            missed = (*base, TriviaEvent.date_last_confirmed < run_started)
            session.execute(
                update(TriviaEvent).where(*missed)
                .values(consecutive_failed_checks=TriviaEvent.consecutive_failed_checks + 1)
            )
            went_stale = session.execute(
                update(TriviaEvent)
                .where(*missed, TriviaEvent.consecutive_failed_checks >= STALE_AFTER_MISSES,
                       TriviaEvent.status != EventStatus.STALE)
                .values(status=EventStatus.STALE)
            ).rowcount
            lines.append(f"{source.name}: {seen}/{total} confirmed, {total - seen} missed, {went_stale} newly stale")
    return lines


def main() -> int:
    init_db()
    run_started = utcnow()
    results = []
    for script in IMPORTERS:
        if not (SCRIPTS / script).exists():
            continue
        print(f"=== {script}", flush=True)
        r = run_importer(script)
        print(f"    {'OK ' if r['ok'] else 'FAIL'} refreshed={r['refreshed']} in {r['seconds']}s {r['note']}", flush=True)
        results.append(r)

    ok_count = sum(1 for r in results if r["ok"])
    geocode_note = run_geocoding() if ok_count else "geocoding skipped (nothing refreshed)"
    sweep_lines = sweep_missing_events(run_started) if ok_count else ["sweep skipped (nothing refreshed)"]

    summary = ["## Crawl summary", "", f"{ok_count} of {len(results)} sources refreshed.", "",
               "| Source script | Result | Events confirmed | Seconds | Note |", "|---|---|---|---|---|"]
    for r in results:
        summary.append(f"| {r['script']} | {'ok' if r['ok'] else 'FAILED'} | {r['refreshed']} | {r['seconds']} | {r['note'][:160]} |")
    summary += ["", f"Geocoding: {geocode_note}", "", "Sweep:", *[f"- {l}" for l in sweep_lines]]
    text = "\n".join(summary)
    print("\n" + text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
