"""Vendor-derived inference values -> quarantine sidecar extraction.

This module maintains its own independent registry of known
vendor-derived (FIT message name, field name) pairs -- ``mapping.py``
never reads this module, and this module never reads ``mapping.py``,
so the "vendor-derived values never appear in the canonical schema"
guarantee holds by construction (the two modules are disjoint), not
because of any filtering step here.

Currently registered:

- ``session.training_load_peak`` -- Garmin's proprietary Training Load
  metric (part of its Training Status feature), confirmed present with
  a real non-null value (128.06...) in ``tests/fixtures/sample_run.fit``.
- ``session.total_training_effect`` / ``total_anaerobic_training_effect``
  -- Garmin's proprietary Training Effect scores, confirmed present
  with real non-null values (2.8 / 0.0) in
  ``tests/fixtures/chest_strap_run.fit``.

Additional vendor-derived fields can be added to
``_VENDOR_DERIVED_FIELDS`` below once their semantics are confirmed
against real fixture data -- never guessed from an ``unknown_NNN``
field name alone. Notably, none of this repo's real fixtures carry a
non-null VO2max estimate, Training Status, Training Readiness, Body
Battery, Performance Condition, Race Predictor, recovery time, or
stress score field (checked via a one-off ``fitdecode`` inspection
across every fixture in ``tests/fixtures/`` before writing this) --
Garmin's ``device_info.battery_*`` fields are the device's own
hardware battery, not "Body Battery", and were confirmed as a false
match, not added. These remain out of scope until a real fixture
supplies one, the same external-fixture-dependency pattern already
tracked for T022/T024.
"""

from __future__ import annotations

import fitdecode

# Maps a FIT message name to the vendor-derived field names known to
# appear on it. Extend this as additional vendor-derived fields are
# confirmed (see module docstring) -- never remove the requirement
# that a field's semantics be confirmed against real fixture data
# before it's added here.
_VENDOR_DERIVED_FIELDS: dict[str, tuple[str, ...]] = {
    "session": (
        "training_load_peak",
        "total_training_effect",
        "total_anaerobic_training_effect",
    ),
}


def extract(messages: list[fitdecode.FitDataMessage]) -> dict[str, str]:
    """Pull known vendor-derived inference values out of decoded FIT messages.

    Returns a flat ``{field_name: str(value)}`` dict -- the shape
    ``db.persist()``'s ``quarantine_values`` parameter already expects
    (see ``db._insert_quarantine_sidecar``). Only fields present with a
    non-``None`` value are included.
    """
    result: dict[str, str] = {}

    for message in messages:
        field_names = _VENDOR_DERIVED_FIELDS.get(message.name)
        if not field_names:
            continue

        for field_name in field_names:
            if not message.has_field(field_name):
                continue
            value = message.get_value(field_name, fallback=None)
            if value is None:
                continue
            result[field_name] = str(value)

    return result
