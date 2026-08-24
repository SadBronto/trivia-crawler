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
