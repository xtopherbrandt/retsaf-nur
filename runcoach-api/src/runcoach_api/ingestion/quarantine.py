"""Vendor-derived inference values -> quarantine sidecar extraction.

This module maintains its own independent registry of known
vendor-derived (FIT message name, field name) pairs -- ``mapping.py``
never reads this module, and this module never reads ``mapping.py``,
so the "vendor-derived values never appear in the canonical schema"
guarantee holds by construction (the two modules are disjoint), not
because of any filtering step here.

Currently registered: ``session.training_load_peak`` -- Garmin's
proprietary Training Load metric (part of its Training Status
feature), confirmed present with a real non-null value in the
project's real fixture (``tests/fixtures/sample_run.fit``). Additional
vendor-derived fields can be added to ``_VENDOR_DERIVED_FIELDS`` below
once their semantics are confirmed against real fixture data -- never
guessed from an ``unknown_NNN`` field name alone.
"""

from __future__ import annotations

import fitdecode

# Maps a FIT message name to the vendor-derived field names known to
# appear on it. Extend this as additional vendor-derived fields are
# confirmed (see module docstring) -- never remove the requirement
# that a field's semantics be confirmed against real fixture data
# before it's added here.
_VENDOR_DERIVED_FIELDS: dict[str, tuple[str, ...]] = {
    "session": ("training_load_peak",),
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
