# Trivia Crawler

Builds a database of trivia nights (venue, address, day/time, host company,
confidence, source) by scraping trivia companies and trivia directories,
then re-checks them over time so stale listings age out automatically.

This is a standalone data-gathering project - it does not publish
anywhere. Output is a CSV you can hand off manually (e.g. into
TriviaByMe's spreadsheet) whenever you're ready.

## Why SQLite, not Postgres

No server to install, no cost, nothing that needs to be "running" in the
background. `data/trivia.db` is just a file - it's the crawler's memory
between runs (what's been seen, when it was last confirmed, when it's due
for a re-check). The schema (`crawler/models.py`) avoids any SQLite-only
features on purpose, so moving to a real Postgres database later, if this
ever needs to run at bigger scale, is a straightforward export/import, not
a rewrite.

## Setup

```bash
python -m venv venv
./venv/Scripts/pip install -r requirements.txt   # Windows
# source venv/bin/activate && pip install -r requirements.txt   # Mac/Linux
```

## Usage

```bash
# Scrape TriviaNearMe.net (a directory - results are imported as
# unverified "candidate" listings, never auto-published, see Data model
# below) and ingest into data/trivia.db
./venv/Scripts/python scripts/import_trivianearme.py

# Smoke-test against just the first few city pages instead of all ~2,350
./venv/Scripts/python scripts/import_trivianearme.py --limit 5

# Export the current database to CSV
./venv/Scripts/python scripts/export_csv.py
```

Exports land in `exports/`:
- `trivia_events_all.csv` - every row, every confidence level
- `trivia_events_verified.csv` - only rows at/above the publish-confidence
  threshold (see Data model)

## Data model (`crawler/models.py`)

- **Venue** and **TriviaEvent** are separate tables - one venue can have
  trivia on more than one night, run by more than one company.
- **Provider** is the trivia company/host. Unrecognized company names
  encountered during scraping become new Provider rows automatically
  (self-expansion, for when dedicated adapters get built later).
- **EventObservation** is an append-only evidence trail - every time a
  source sees a listing (an import, a re-check), one row gets added here.
  A TriviaEvent's `confidence_score` is derived from all its observations
  combined (`crawler/scoring.py`), not a single guess.
- Nothing is ever hard-deleted. A `TriviaEvent.status` moves through
  `candidate -> active -> stale -> removed` as re-checks confirm it or
  fail to. A trivia night that comes back after a gap is recognized, not
  re-created.

## Confidence scoring

Every observation is scored by where it came from
(`crawler/scoring.py::SCORE_BY_SOURCE_TYPE`):

| Source type | Score |
|---|---|
| Provider's own schedule page | 100 |
| Venue's own website | 95 |
| Venue calendar (future dated instances) | 90 |
| Recent social post | 75 |
| Event platform (Eventbrite, etc.) | 60 |
| Directory (TriviaNearMe, etc.) | 40 |
| Old/stale search result | 20 |

Multiple independent observations of the same event combine (strongest
score plus a diminishing bonus per corroborating source, capped at 100) -
see `combine_confidence()`. `PUBLISH_THRESHOLD = 60` is the bar a
TriviaEvent needs to clear before `export_csv.py` includes it in the
"verified" file.

## Deduplication

Venue matching (`crawler/pipeline.py::_find_or_create_venue`) tries, in
order: exact match on normalized street address, then same normalized
name within 100m if both have coordinates, then creates a new venue. Name
normalization (`crawler/normalize.py`) lowercases, strips punctuation, and
drops noise suffixes like "Bar & Grill" so close variants collapse
together.

## Adding a new source

1. Write a function under `crawler/importers/` (directories) or
   `crawler/adapters/` (first-party provider sites) that yields
   `crawler.pipeline.RawListing` objects - see
   `crawler/importers/trivianearme.py` for a complete example.
2. Never write to the DB directly - always call
   `crawler.pipeline.ingest_listing()` per listing, so scoring/dedup logic
   stays in one place.
3. Use `crawler.http_client.PoliteSession` for any plain HTTP source (it
   rate-limits, retries, and respects robots.txt). JS-heavy sources will
   need Playwright instead - not wired up yet, but the dependency is
   already in `requirements.txt`.

## Running for free (GitHub Actions)

See `.github/workflows/crawl.yml`. GitHub Actions runners are thrown away
after every run, so the workflow commits the updated `data/trivia.db` and
`exports/*.csv` back into the repo at the end of each run - that's what
lets the "next check date" / staleness logic carry over between scheduled
runs without paying for a server.
