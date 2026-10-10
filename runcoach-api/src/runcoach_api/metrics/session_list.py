"""The session list's pure rules (F019): the session name.

**Pure, by design**, on the pattern of ``metrics/gap.py``: it imports neither
``config`` nor ``db`` nor ``fastapi``. ``ingestion.mapping`` calls
``session_name`` at upload to fill ``sessions.name``, and the list's read side
calls it again for a session stored before that column existed (whose saved
workout name is unknown, so only the profile name stored in
``context.provenance.sport_profile_name`` can apply), so the whitespace rule
is written once and the two paths cannot disagree.

**The name** (F019 AC2; user rulings 2026-10-08 and 2026-10-09) is the FIT
``workout.wkt_name``, else ``session.sport_profile_name``. A source that is
absent, empty or whitespace-only (``str.isspace``) falls through to the next;
when neither has one the name is ``None``. A name that is not blank is kept
verbatim, unstripped: the athlete typed it, and a leading space or a doubled
one is theirs to see. Only ``str.isspace`` whitespace is blank, so a
zero-width space (U+200B, a format character, not whitespace) is a name. The
uploaded file's name is never a source (user ruling 2026-10-08).
"""

from __future__ import annotations


def session_name(workout_name: str | None, profile_name: str | None) -> str | None:
    """The first of ``workout_name``, ``profile_name`` that is a non-blank string; else ``None``.

    ``fitdecode`` decodes a FIT string field to a ``str`` or ``None``. A value
    of any other type is not a name and falls through like an absent one: the
    undeclared-capture note already records a non-string claimed profile name
    exactly as read (``hrv_classification``), and this rule must not refuse a
    file that path accepts.
    """
    for value in (workout_name, profile_name):
        if isinstance(value, str) and value and not value.isspace():
            return value
    return None
