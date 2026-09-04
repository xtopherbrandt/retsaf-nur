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
  ``tests/fixtures/wrist_ppg_run.fit``.
- ``session.avg_stress`` -- Garmin's proprietary stress score
  ("stress score" is a named §2.3.6 quarantine item), confirmed
  non-null (19) in ``tests/fixtures/sample_health_snapshot.fit``.
  Profile-resolved (``session`` field 195), not an ``unknown_NNN``
  guess. (Previously also cited against
  ``tests/fixtures/T024_chest_strap_HRV.fit``, deleted per T034 item 7
  -- it carried zero ``hrv`` messages despite its name and was
  referenced by no test.)

Additional vendor-derived fields can be added to
``_VENDOR_DERIVED_FIELDS`` below once their semantics are confirmed
against real fixture data -- never guessed from an ``unknown_NNN``
field name alone.

Known gaps, each verified rather than assumed:

- **VO2max estimate, Training Status, Training Readiness, Body
  Battery, Performance Condition, Race Predictor, recovery time** --
  no fixture in ``tests/fixtures/`` carries any of them non-null.
  ``fitdecode``'s profile does define ``max_met_data.vo2_max``
  (message 229), but no fixture contains a ``max_met_data`` message.
  ``session.training_stress_score`` (field 35) is profile-defined and
  present but null in every fixture. Garmin's ``device_info.battery_*``
  fields are the device's own hardware battery, not "Body Battery" --
  a false match, deliberately not added. These stay out of scope until
  a real fixture supplies one, the same external-fixture dependency
  already tracked for T022/T024.
- **``record.current_stress``** (profile field 116; non-null at 13.0 /
  9.0 in the two fixtures above) is vendor-derived and *is* correctly
  kept out of the canonical schema, but is **not** written to the
  sidecar. It is a per-record time series, and ``extract()`` returns a
  flat ``{field_name: value}`` dict keyed to the session, so storing
  it here would silently keep only the last record's value. Giving it
  a home needs a per-sample sidecar shape, which F003's schema does
  not have -- tracked as a follow-up rather than solved with a lossy
  write.
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
        "avg_stress",
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
