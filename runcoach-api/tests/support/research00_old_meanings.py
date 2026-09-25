"""Old meanings of research/00 that the F008 rewrite changed, as literals the sweep searches for.

F008 (R4). Each ``OldMeaning`` names one superseded statement: ``pattern`` is a regex over
``normalize()``d text, ``example`` is verbatim old text, ``source`` is ``path:line@4e47d0e``, and
``decision`` is the C-number (or T-number, or ``HRV-11``) that changed it. The endpoint walk holds this
file out by name (``SCAN_EXCLUDED_LITERALS``) on the same ground as ``withdrawn_phrasings.py``: it is a
file of literals the scans search for, not history. Its public names are exactly the four below;
``test_research00_traceability.py`` asserts that from the AST.
"""

import unicodedata as _unicodedata
from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class OldMeaning:
    pattern: str
    example: str
    source: str
    decision: str


OLD_MEANINGS: dict[str, OldMeaning] = {}

EXCEPTIONS: tuple = ()

_DROPPED = str.maketrans("", "", "\"'`*_“”‘’")

_TYPOGRAPHY = {"≥": ">=", "≤": "<=", "—": "--", "–": "--", "−": "-"}


def normalize(text: str) -> str:
    """NFKC first, then ``_flat`` (test_hrv_unavailable_causes.py): quotes (curly ones too),
    backticks, ``*`` and ``_`` dropped, typography folded to ASCII, whitespace collapsed, casefolded."""
    folded = _unicodedata.normalize("NFKC", text).translate(_DROPPED)
    for symbol, ascii_form in _TYPOGRAPHY.items():
        folded = folded.replace(symbol, ascii_form)
    return " ".join(folded.split()).casefold()
