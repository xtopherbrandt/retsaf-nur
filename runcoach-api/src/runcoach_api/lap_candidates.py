"""The laps candidates decision (F025 reference section 5 step 2): one answer per Connect item.

Pure: no store, configuration or web import. ``db.read_lap_candidate_rows`` reads the candidate
rows; this module only decides, with ``race_tags``' start-second lookup
(``race_tags.index_by_start_second`` and ``race_tags.at_start_second``), so the two operations
cannot match a start second differently.

An *item* is a mapping with ``connect_activity_id`` and ``start_time`` (UTC, ISO 8601, whole
seconds, ``Z`` or ``+00:00``); any other key is not read. A *candidate row* is a mapping with
``session_id``, ``start_time`` (as stored, ``+00:00``) and ``laps_read_at``: a stored session with
sport ``running`` whose ``activity_tag`` is not one of ``race_tags.RESTING_CAPTURE_TAGS``, the rows
F023's matching reads.

The rule: an item's candidates are the rows at its start second. None: ``not_stored``. Exactly one:
``matched``, with its ``session_id`` and ``laps_read_at``. Two or more (two devices recording the
same start): ``ambiguous``. There is no distance or duration agreement, unlike F023's: the attach
endpoint's identity check on the file proves the match (user ruling, 2026-10-16), so a run edited in
Connect still gets its laps. Nothing is written, so two items at one second both get that second's
answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from runcoach_api import race_tags

MATCHED = "matched"
NOT_STORED = "not_stored"
AMBIGUOUS = "ambiguous"
OUTCOMES = (MATCHED, NOT_STORED, AMBIGUOUS)


def decide(items: Sequence[Mapping], candidate_rows: Iterable[Mapping]) -> list[dict]:
    """One answer per item, in request order: ``connect_activity_id``, ``outcome``,
    ``session_id`` and ``laps_read_at`` (both null unless ``matched``)."""
    index = race_tags.index_by_start_second(candidate_rows)
    answers: list[dict] = []
    for item in items:
        rows = race_tags.at_start_second(index, item["start_time"])
        answer = {
            "connect_activity_id": item["connect_activity_id"],
            "outcome": NOT_STORED,
            "session_id": None,
            "laps_read_at": None,
        }
        if len(rows) == 1:
            answer.update(
                outcome=MATCHED, session_id=rows[0]["session_id"], laps_read_at=rows[0]["laps_read_at"]
            )
        elif rows:
            answer["outcome"] = AMBIGUOUS
        answers.append(answer)
    return answers
