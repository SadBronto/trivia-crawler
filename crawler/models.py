"""
Database schema for the trivia crawler.

Design notes
------------
- Written against plain SQLAlchemy 2.0 ORM types (String, Integer, Float,
  DateTime, etc.) with no SQLite-specific features, so this same file can
  point at PostgreSQL later just by changing the connection URL in db.py.
  No PostGIS types are used - at this data volume (thousands, not millions
  of rows), geo distance for dedup is computed in plain Python (haversine),
  not with a spatial index. See crawler/geo.py.

- Venue and TriviaEvent are kept separate on purpose (per spec): one venue
  can host trivia on more than one night, run by more than one company.

- Nothing is ever hard-deleted. A TriviaEvent's `status` moves through
  candidate -> active -> stale -> removed as re-checks confirm or fail to
  confirm it. Historical rows stay in the table so a night that comes back
  later is recognized, not re-created from scratch.

- EventObservation is the append-only evidence trail: every time some
  source (a directory import, a re-check, a first-party page visit) sees
  a listing, it gets one row here. A TriviaEvent's `confidence_score` is
  derived from its observations, not stored as a one-off guess - Phase 8
  scoring logic reads this table.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceType(str, enum.Enum):
    """Where a piece of data came from - drives the Phase 8 scoring table."""

    PROVIDER_SCHEDULE = "provider_schedule"      # first-party trivia-company site (highest trust)
    VENUE_WEBSITE = "venue_website"               # the venue's own site/calendar
    VENUE_CALENDAR = "venue_calendar"             # venue calendar w/ future dated instances
    SOCIAL_POST = "social_post"                   # recent social confirmation
    EVENT_PLATFORM = "event_platform"             # Eventbrite/Meetup/etc.
    DIRECTORY = "directory"                       # TriviaNearMe, PubTriviaNight, etc.
    SEARCH_RESULT = "search_result"                # old/stale search hit


class EventStatus(str, enum.Enum):
    CANDIDATE = "candidate"   # imported from a directory, not yet verified - never shown publicly
    ACTIVE = "active"         # confidence >= publish threshold, recently confirmed
    STALE = "stale"           # missed one or more re-checks, not yet given up on
    REMOVED = "removed"       # confirmed gone (explicit cancellation/closure) - kept for history


class Frequency(str, enum.Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    IRREGULAR = "irregular"
    UNKNOWN = "unknown"


class Source(Base):
    """A place data comes from: a specific directory, provider site, etc.

    One row per *site*, not per page - e.g. one Source row for
    "TriviaNearMe", reused across every city page it crawls.
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    base_url: Mapped[str] = mapped_column(String(500))
    adapter_name: Mapped[str] = mapped_column(String(200))  # e.g. "TriviaNearMeImporter"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    events: Mapped[list["TriviaEvent"]] = relationship(back_populates="source")
    observations: Mapped[list["EventObservation"]] = relationship(back_populates="source")


class Provider(Base):
    """A trivia company/host (e.g. 'Geeks Who Drink', 'Bent Trivia').

    Discovered either because we built a dedicated adapter for them, or
    because a directory listing named them (Phase 11: self-expansion) -
    `discovered_via` records which.
    """

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(300), nullable=True)
    discovered_via: Mapped[str | None] = mapped_column(String(200), nullable=True)
    has_dedicated_adapter: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    events: Mapped[list["TriviaEvent"]] = relationship(back_populates="provider")

    __table_args__ = (UniqueConstraint("normalized_name", name="uq_provider_normalized_name"),)


class Venue(Base):
    """A physical location. Trivia nights point at this, never the other
    way around - one venue can have multiple TriviaEvent rows.
    """

    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)

    street_address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)  # state/province
    country: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    normalized_address: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["TriviaEvent"]] = relationship(back_populates="venue")


class TriviaEvent(Base):
    """One recurring trivia night at one venue (e.g. 'Tuesdays 7pm').

    A venue with trivia on two different nights, or run by two different
    companies, gets two TriviaEvent rows.
    """

    __tablename__ = "trivia_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    venue: Mapped["Venue"] = relationship(back_populates="events")

    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"), nullable=True, index=True)
    provider: Mapped["Provider | None"] = relationship(back_populates="events")
    # Raw company name string as seen on the source, kept even once/if we
    # resolve it to a real Provider row - useful for debugging bad matches.
    raw_provider_name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    day_of_week: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "Monday".."Sunday"
    start_time: Mapped[str | None] = mapped_column(String(20), nullable=True)   # "19:00" 24h local
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    frequency: Mapped[Frequency] = mapped_column(Enum(Frequency), default=Frequency.UNKNOWN)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Primary/best source for this row (the one that most recently set its
    # confidence). Full evidence trail lives in EventObservation.
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    source: Mapped["Source"] = relationship(back_populates="events")
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    date_first_discovered: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    date_last_confirmed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    consecutive_failed_checks: Mapped[int] = mapped_column(Integer, default=0)

    confidence_score: Mapped[int] = mapped_column(Integer, default=0)  # Phase 8 scale, 0-100
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), default=EventStatus.CANDIDATE, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    observations: Mapped[list["EventObservation"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventObservation(Base):
    """One instance of some source confirming (or asserting) a TriviaEvent.

    Append-only evidence trail. Multiple independent observations of the
    same night (e.g. the directory listing AND the venue's own site both
    say "Tuesday 7pm") are what let confidence climb past the publishing
    threshold, and are what Phase 9 dedup keeps attached to a merged
    record rather than discarding.
    """

    __tablename__ = "event_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    event_id: Mapped[int] = mapped_column(ForeignKey("trivia_events.id"), index=True)
    event: Mapped["TriviaEvent"] = relationship(back_populates="observations")

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    source: Mapped["Source"] = relationship(back_populates="observations")

    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confidence_score: Mapped[int] = mapped_column(Integer)  # score this single observation contributes
    raw_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON of what was scraped
    successful: Mapped[bool] = mapped_column(Boolean, default=True)  # False = re-check failed to confirm
