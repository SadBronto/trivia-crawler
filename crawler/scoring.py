"""Phase 8 confidence scoring - one place, so every adapter/importer agrees
on what a given kind of evidence is worth.
"""

from crawler.models import SourceType

# Base score contributed by a single observation, keyed by where it came
# from. An adapter can nudge this up/down slightly for a specific
# observation (e.g. a directory listing with a suspiciously old-looking
# "last updated" date), but should stay close to these anchors.
SCORE_BY_SOURCE_TYPE: dict[SourceType, int] = {
    SourceType.PROVIDER_SCHEDULE: 100,
    SourceType.VENUE_WEBSITE: 95,
    SourceType.VENUE_CALENDAR: 90,
    SourceType.SOCIAL_POST: 75,
    SourceType.EVENT_PLATFORM: 60,
    SourceType.DIRECTORY: 40,
    SourceType.SEARCH_RESULT: 20,
}

# A TriviaEvent needs at least this combined confidence before it's fit to
# publish anywhere. Import freely below this - just tag status=CANDIDATE.
PUBLISH_THRESHOLD = 60


def score_for_source_type(source_type: SourceType) -> int:
    return SCORE_BY_SOURCE_TYPE.get(source_type, 20)


def combine_confidence(observation_scores: list[int]) -> int:
    """Combine multiple independent observations of the same event.

    Not a simple max (independent corroboration should count for
    something) or a simple sum (two directory scrapes of the same listing
    shouldn't outscore one first-party confirmation). Takes the strongest
    single observation, then adds a diminishing bonus per additional
    corroborating observation, capped at 100.
    """
    if not observation_scores:
        return 0
    scores = sorted(observation_scores, reverse=True)
    total = scores[0]
    bonus_pool = 100 - total
    for extra in scores[1:]:
        if bonus_pool <= 0:
            break
        bump = min(bonus_pool, extra // 4)  # each extra source is worth 1/4 its own score, diminishing
        total += bump
        bonus_pool -= bump
    return min(100, total)
