"""Normalization helpers used for deduplication (Phase 9).

Kept deliberately simple and dependency-free for this first slice: lowercase,
strip punctuation/common suffixes, collapse whitespace. Good enough to
catch "O'Malley's Irish Pub" vs "O'Malleys Irish Pub & Restaurant"-style
variants; not a full fuzzy-matching engine. Revisit if false-negatives
show up in real data.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Trailing business-type words that don't help identify a *specific*
# venue and just add noise to the comparison.
_NOISE_SUFFIXES = (
    "restaurant",
    "bar and grill",
    "bar & grill",
    "pub",
    "tavern",
    "brewery",
    "brewing company",
    "brewing co",
    "grill",
    "bar",
)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    for suffix in _NOISE_SUFFIXES:
        if text.endswith(" " + suffix):
            text = text[: -(len(suffix) + 1)].strip()
    return text


def normalize_address(street_address: str | None, city: str | None, region: str | None) -> str:
    parts = [p for p in (street_address, city, region) if p]
    joined = ", ".join(parts)
    return normalize_name(joined)


# Canonical spelling for a country across the inconsistent strings
# different adapters send in for "the same real country" - hit live as
# duplicate country groups on triviabyme.com's homepage (US/USA/United
# States all showing separately; one Quiz Meisters venue had a state code
# "QLD" in the country field entirely - a store-locator data glitch on
# their end, not something an alias table catches, but this at least
# doesn't propagate the string uncorrected). Mirrors
# trivia-events-shared/index.js's canonicalCountry() on the display side -
# this is the ingest-time twin, so bad data is normalized once, going in,
# rather than relying on every downstream consumer to redo the same
# defense.
COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "united states": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "gb": "United Kingdom",
    "great britain": "United Kingdom", "united kingdom": "United Kingdom",
    "au": "Australia", "australia": "Australia",
}

# US states as a full-name -> 2-letter-code map, since every existing
# convention in this project (TWC's own sign-up form, every adapter that
# already got this right) uses the 2-letter code - this exists purely to
# catch an adapter that (like Mapbox reverse geocoding, or some future
# scraped source) hands back a full state name instead.
US_STATE_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC", "puerto rico": "PR",
}

# Australian state/territory abbreviations vs full names - hit live as
# separate region groups for VIC/Victoria, NSW/New South Wales, SA/South
# Australia. Canonicalized to the full name (not the abbreviation) for the
# same reason trivia-events-shared's canonicalState() does: Western
# Australia's abbreviation is also "WA", identical to Washington state's,
# so abbreviating both the same way would collide two entirely different
# real places under one region key.
AU_STATE_ALIASES = {
    "vic": "Victoria", "victoria": "Victoria",
    "nsw": "New South Wales", "new south wales": "New South Wales",
    "sa": "South Australia", "south australia": "South Australia",
    "wa": "Western Australia", "western australia": "Western Australia",
    "qld": "Queensland", "queensland": "Queensland",
    "tas": "Tasmania", "tasmania": "Tasmania",
    "act": "Australian Capital Territory", "australian capital territory": "Australian Capital Territory",
    "nt": "Northern Territory", "northern territory": "Northern Territory",
}


def canonical_country(raw: str | None) -> str | None:
    if not raw:
        return raw
    key = raw.strip().lower()
    return COUNTRY_ALIASES.get(key, raw.strip())


def canonical_region(raw: str | None, country: str | None) -> str | None:
    """Normalize a region/state value once its country is known. Only acts
    on the two conventions this project actually has (US 2-letter codes, AU
    full names) - anything else (UK counties, Canadian provinces, etc.)
    passes through unchanged, same as trivia-events-shared's canonicalState().
    """
    if not raw:
        return raw
    resolved_country = canonical_country(country)
    key = raw.strip().lower()
    if resolved_country == "United States":
        return US_STATE_TO_ABBR.get(key, raw.strip())
    if resolved_country == "Australia":
        return AU_STATE_ALIASES.get(key, raw.strip())
    return raw.strip()
