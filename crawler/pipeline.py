"""
The universal ingest pipeline.

Every adapter/importer (site-specific scraping code) does its own thing to
extract data, then hands the pipeline a stream of RawListing records in one
standard shape. The pipeline is the one place that knows how to:

  1. find-or-create the Venue (matching by normalized address, falling
     back to name + proximity - Phase 9 dedup)
  2. find-or-create the Provider (Phase 11: unrecognized company names
     become new Provider rows automatically)
  3. find-or-create the TriviaEvent (same venue + day_of_week + provider)
  4. record an EventObservation as evidence
  5. recompute confidence_score/status from all observations (Phase 8)

Adapters should never write to the DB directly - always go through
ingest_listing() so scoring/dedup logic stays in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from crawler.geo import haversine_meters
from crawler.models import (
    EventObservation,
    EventStatus,
    Frequency,
    Provider,
    Source,
    TriviaEvent,
    Venue,
)
from crawler.normalize import canonical_country, canonical_region, normalize_address, normalize_name
from crawler.scoring import combine_confidence, score_for_source_type

# How close two venues have to be (meters) to be considered the same
# place when normalized-address matching doesn't already confirm it.
DEDUP_DISTANCE_METERS = 100

# How long before a TriviaEvent should be re-checked, keyed by the kind of
# source that most recently confirmed it (Phase 10).
RECHECK_INTERVAL_DAYS = {
    "provider_feed": 7,
    "venue_website": 30,
    "directory": 14,       # directories go stale fast (see spec) - check more aggressively
    "failed_once": 30,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RawListing:
    """One trivia-night listing as scraped, before it's matched against
    anything already in the DB. Every adapter/importer produces these.
    """

    venue_name: str
    street_address: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    venue_website: str | None = None

    day_of_week: str | None = None       # "Monday".."Sunday", or None if unknown
    start_time: str | None = None        # "19:00" local 24h, or None
    timezone: str | None = None
    frequency: Frequency = Frequency.UNKNOWN
    notes: str | None = None

    provider_name: str | None = None     # raw company/host name as scraped, if any
    provider_website: str | None = None

    source_url: str | None = None
    raw_snapshot: dict = field(default_factory=dict)  # anything else worth keeping, JSON-able


def _find_or_create_provider(session: Session, listing: RawListing, discovered_via: str) -> Provider | None:
    if not listing.provider_name or not listing.provider_name.strip():
        return None
    norm = normalize_name(listing.provider_name)
    if not norm:
        return None
    provider = session.execute(
        select(Provider).where(Provider.normalized_name == norm)
    ).scalar_one_or_none()
    if provider:
        if listing.provider_website and not provider.website:
            provider.website = listing.provider_website
        return provider
    provider = Provider(
        name=listing.provider_name.strip(),
        normalized_name=norm,
        website=listing.provider_website,
        discovered_via=discovered_via,
    )
    session.add(provider)
    session.flush()
    return provider


def _find_or_create_venue(session: Session, listing: RawListing) -> Venue:
    norm_name = normalize_name(listing.venue_name)
    norm_addr = normalize_address(listing.street_address, listing.city, listing.region)

    # 1. Exact match on normalized address (best signal).
    # Require an actual street address before trusting this match - a
    # normalized_address built from city+region alone (street_address
    # missing) is nowhere near specific enough to identify one venue.
    # Real bug this guards against, caught 2026-08-25: two unrelated
    # Minneapolis venues with no street address on file both normalized
    # to "minneapolis mn" and got silently merged into one Venue row,
    # keeping whichever's name was inserted first while carrying the
    # other's data - not a hypothetical, an actual corrupted row.
    if norm_addr and listing.street_address:
        existing = session.execute(
            select(Venue).where(Venue.normalized_address == norm_addr)
        ).scalar_one_or_none()
        if existing:
            return existing

    # 2. Same normalized name within DEDUP_DISTANCE_METERS, if we have
    #    coordinates for both. Catches the "listed under a slightly
    #    different address string" case.
    if listing.latitude is not None and listing.longitude is not None:
        candidates = session.execute(
            select(Venue).where(Venue.normalized_name == norm_name, Venue.latitude.is_not(None))
        ).scalars()
        for candidate in candidates:
            dist = haversine_meters(
                listing.latitude, listing.longitude, candidate.latitude, candidate.longitude
            )
            if dist <= DEDUP_DISTANCE_METERS:
                return candidate

    # 3. No match - create it. Country/region are canonicalized here, once,
    # at the single point every listing passes through on its way into the
    # DB - not left for every downstream consumer (TWC's and TBM's site
    # generators, this project's own CSV export) to defend against
    # separately. Real bugs this caught live: "US"/"USA"/"United States"
    # each producing a separate country group, "VIC"/"Victoria" and "NSW"/
    # "New South Wales" each producing a separate region group. city is
    # NOT touched here - that corruption (a UK postcode or a street address
    # ending up in the city field) was adapter-specific bad extraction, not
    # a spelling-variant problem this kind of alias table can fix; see
    # scripts/repair_locations.py for how that was actually cleaned up
    # (reverse geocoding from coordinates), and the affected adapters
    # (inquizition.py, fametrivia.py) for the underlying parsing fixes.
    canon_country = canonical_country(listing.country)
    venue = Venue(
        name=listing.venue_name.strip(),
        normalized_name=norm_name,
        street_address=listing.street_address,
        city=listing.city,
        region=canonical_region(listing.region, canon_country),
        country=canon_country,
        normalized_address=norm_addr or None,
        latitude=listing.latitude,
        longitude=listing.longitude,
        phone=listing.phone,
        website=listing.venue_website,
    )
    session.add(venue)
    session.flush()
    return venue


def _find_or_create_event(
    session: Session, venue: Venue, provider: Provider | None, listing: RawListing, source: Source
) -> tuple[TriviaEvent, bool]:
    query = select(TriviaEvent).where(
        TriviaEvent.venue_id == venue.id,
        TriviaEvent.day_of_week == listing.day_of_week,
    )
    if provider is not None:
        query = query.where(TriviaEvent.provider_id == provider.id)
    else:
        query = query.where(TriviaEvent.provider_id.is_(None))

    existing = session.execute(query).scalar_one_or_none()
    if existing:
        return existing, False

    event = TriviaEvent(
        venue=venue,
        provider=provider,
        raw_provider_name=listing.provider_name,
        day_of_week=listing.day_of_week,
        start_time=listing.start_time,
        timezone=listing.timezone,
        frequency=listing.frequency,
        notes=listing.notes,
        source=source,
        source_url=listing.source_url,
        status=EventStatus.CANDIDATE,
    )
    session.add(event)
    session.flush()
    return event, True


def ingest_listing(session: Session, source: Source, listing: RawListing) -> TriviaEvent:
    """The one entry point adapters/importers should call per scraped
    listing. Idempotent - re-running an importer over the same data just
    adds corroborating observations and refreshes date_last_confirmed.
    """
    provider = _find_or_create_provider(session, listing, discovered_via=source.name)
    venue = _find_or_create_venue(session, listing)
    event, is_new = _find_or_create_event(session, venue, provider, listing, source)

    if not is_new:
        # Keep the richer/newer scalar fields if this observation has them
        # and the existing row doesn't (never overwrite a known value with
        # an unknown one).
        event.start_time = event.start_time or listing.start_time
        event.timezone = event.timezone or listing.timezone
        if listing.frequency != Frequency.UNKNOWN:
            event.frequency = listing.frequency
        event.notes = event.notes or listing.notes
        event.source_url = listing.source_url or event.source_url

    obs_score = score_for_source_type(source.source_type)
    observation = EventObservation(
        event=event,
        source=source,
        source_url=listing.source_url,
        confidence_score=obs_score,
        raw_snapshot=_safe_json(listing.raw_snapshot),
        successful=True,
    )
    session.add(observation)

    all_scores = [o.confidence_score for o in event.observations if o.successful] + [obs_score]
    event.confidence_score = combine_confidence(all_scores)
    event.date_last_confirmed = utcnow()
    event.consecutive_failed_checks = 0
    if event.status != EventStatus.REMOVED:
        event.status = (
            EventStatus.ACTIVE if event.confidence_score >= 60 else EventStatus.CANDIDATE
        )

    interval_days = RECHECK_INTERVAL_DAYS.get(
        "directory" if source.source_type.value == "directory" else "venue_website", 30
    )
    event.next_check_at = utcnow() + timedelta(days=interval_days)

    session.flush()
    return event


def _safe_json(data: dict) -> str | None:
    import json

    if not data:
        return None
    try:
        return json.dumps(data, default=str)[:10000]  # cap size, this is debug context not a payload
    except TypeError:
        return None


def get_or_create_source(session: Session, name: str, source_type, base_url: str, adapter_name: str) -> Source:
    existing = session.execute(select(Source).where(Source.name == name)).scalar_one_or_none()
    if existing:
        return existing
    source = Source(name=name, source_type=source_type, base_url=base_url, adapter_name=adapter_name)
    session.add(source)
    session.flush()
    return source
