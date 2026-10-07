"""The athlete's Garmin profile settings a FIT file carries -> per-session values.

F016 stores, with each session, the seven settings its file carried, as
``spec/references/F016-athlete-profile.md``'s Data Model table maps them:

==========================  ======================================  =========================
stored field                FIT source                              stored as
==========================  ======================================  =========================
``sex``                     ``user_profile.gender``                 raw 0 ``female``, 1 ``male``
``body_mass_kg``            ``user_profile.weight``                 decoded kg (scale 10)
``height_cm``               ``user_profile.height``                 raw whole-cm integer
``resting_hr_bpm``          ``user_profile.resting_heart_rate``     raw integer
``max_hr_bpm``              ``zones_target.max_heart_rate``         raw integer
``threshold_hr_bpm``        ``zones_target.threshold_heart_rate``   raw integer
``garmin_activity_class``   ``user_profile.activity_class``         raw integer
==========================  ======================================  =========================

Rules, each pinned by ``tests/test_profile_values_ingest.py``:

- A FIT invalid value (decoded by ``fitdecode`` as ``None``), a missing
  field and a missing message all store absent (``None``).
- **0 stores absent for the five numeric HR and body fields only.** FIT
  ``gender`` 0 is ``female``, and ``activity_class`` 0 is a valid level, so
  neither is screened by zero. Any ``gender`` other than 0 or 1 is absent.
- ``activity_class`` is the field's ``raw_value``: ``fitdecode`` decodes 100
  as ``'level_max'`` and bit 0x80 as ``'athlete'``, strings that would hide
  the integer.
- ``height_cm`` is the raw integer. FIT scales height by 100 to metres, so
  the decoded value is a float, and ``round(m * 100)`` of it is not the
  stored integer.
- ``body_mass_kg`` is the decoded value when it is a number. FIT weight's
  0xFFFE decodes as the string ``'calculating'`` and stores absent.
- Only the first ``user_profile`` and the first ``zones_target`` message are
  read; a later message of the same type is ignored even where the first
  lacks a field.

The values are stored whatever the session's sport: the running-only rule
for max and threshold HR belongs to whoever resolves the effective profile,
not here. ``extract([])`` returns all seven fields absent.
"""

from __future__ import annotations

import fitdecode

# The seven ``models.Session`` fields this module fills, in storage order.
FIELDS: tuple[str, ...] = (
    "sex",
    "body_mass_kg",
    "height_cm",
    "resting_hr_bpm",
    "max_hr_bpm",
    "threshold_hr_bpm",
    "garmin_activity_class",
)

_SEX_BY_GENDER = {0: "female", 1: "male"}


def _first(messages: list[fitdecode.FitDataMessage], name: str):
    for message in messages:
        if message.name == name:
            return message
    return None


def _raw_int(message, field_name: str) -> int | None:
    """The field's raw integer, or ``None`` when the message, the field or a
    valid integer is missing (a FIT invalid value has a ``None`` raw value)."""
    if message is None:
        return None
    raw = message.get_value(field_name, fallback=None, raw_value=True)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _nonzero(value):
    return None if value == 0 else value


def _body_mass_kg(profile) -> float | None:
    if profile is None:
        return None
    value = profile.get_value("weight", fallback=None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _nonzero(float(value))


def extract(messages: list[fitdecode.FitDataMessage]) -> dict[str, str | int | float | None]:
    """Map a file's first ``user_profile`` and ``zones_target`` to the seven
    stored fields. Every key of ``FIELDS`` is present, ``None`` when absent."""
    profile = _first(messages, "user_profile")
    zones = _first(messages, "zones_target")

    return {
        "sex": _SEX_BY_GENDER.get(_raw_int(profile, "gender")),
        "body_mass_kg": _body_mass_kg(profile),
        "height_cm": _nonzero(_raw_int(profile, "height")),
        "resting_hr_bpm": _nonzero(_raw_int(profile, "resting_heart_rate")),
        "max_hr_bpm": _nonzero(_raw_int(zones, "max_heart_rate")),
        "threshold_hr_bpm": _nonzero(_raw_int(zones, "threshold_heart_rate")),
        "garmin_activity_class": _raw_int(profile, "activity_class"),
    }
