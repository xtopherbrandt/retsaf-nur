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


OLD_MEANINGS: dict[str, OldMeaning] = {
    'AUT-02-C23-override-outside-autonomy': OldMeaning(
        pattern='safety override and the goal contract share: both sit outside the systems autonomous authority',
        example="This is the boundary the safety override and the goal contract share: both sit outside the system's autonomous authority",
        source='specification/research/00-design-decisions.md:65@4e47d0e',
        decision='C23',
    ),
    'AUT-04-C20-two-purposes-only': OldMeaning(
        pattern='chat serves two purposes only',
        example='Chat serves two purposes only',
        source='specification/research/00-design-decisions.md:57@4e47d0e',
        decision='C20',
    ),
    'C01-withhold-not-judgeable-only': OldMeaning(
        pattern='not judgeable but holds at least minwindowreadings',
        example='a dataset that is **not** judgeable but holds at least `min_window_readings` judged-week days',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C01',
    ),
    'C02-withhold-against-selected': OldMeaning(
        pattern='later than every judged-week day of the selected dataset',
        example='every one later than every judged-week day of the selected dataset',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C02',
    ),
    'C03-return-is-free': OldMeaning(
        pattern='a return to a dataset the athlete established before is free',
        example='a return to a dataset the athlete established before is free',
        source='specification/research/00-design-decisions.md:155@4e47d0e',
        decision='C03',
    ),
    'C04-hole-at-least': OldMeaning(
        pattern='capture hole of at least gapresetdays',
        example='an internal capture hole of at least `gap_reset_days`',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C04',
    ),
    'C05-gate02-worse-rate-reopens': OldMeaning(
        pattern='worse rate reopens',
        example='a worse rate reopens the deferred hysteresis decision',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C05',
    ),
    'C06-gate01-one-exception': OldMeaning(
        pattern='save one named, counted exception',
        example='any worsening blocking release save one named, counted exception',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C06',
    ),
    'C06-hrv-25-accepted-cost': OldMeaning(
        pattern='accepted because quality-first promotes',
        example='Accepted because quality-first promotes the *best available* instrument',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C06',
    ),
    'C08-arch08-silence-tolerated-freely': OldMeaning(
        pattern='the direction §1\\.7 tolerates freely',
        example='Silence is withholding, the direction §1.7 tolerates freely.',
        source='specification/research/00-design-decisions.md:222@4e47d0e',
        decision='C08',
    ),
    'C08-reg11-readiness-gate-down-weights': OldMeaning(
        pattern='readiness gate down-weights',
        example='the readiness gate down-weights, it does not fail',
        source='specification/research/00-design-decisions.md:115@4e47d0e',
        decision='C08',
    ),
    'C09-fig05-idea071-sprint': OldMeaning(
        pattern='carried, unanswered, to \\[\\[idea-071\\]\\]s sprint',
        example="It is carried, unanswered, to [[IDEA-071]]'s sprint, which is re-deriving the rule that produces the silence.",
        source='specification/research/00-design-decisions.md:229@4e47d0e',
        decision='C09',
    ),
    'C09-residual-carried-to-idea-071': OldMeaning(
        pattern='carried whole to \\[\\[idea-071\\]\\]s sprint',
        example="so the question is carried whole to [[IDEA-071]]'s sprint",
        source='specification/research/00-design-decisions.md:222@4e47d0e',
        decision='C09',
    ),
    'C10-lone-candidate-never-struck': OldMeaning(
        pattern='lone candidate is its own reference and is never struck',
        example='a lone candidate is its own reference and is never struck however old it is',
        source='specification/research/00-design-decisions.md:221@4e47d0e',
        decision='C10',
    ),
    'C10-recency-only-rule-that-acts': OldMeaning(
        pattern='this rule is the only one that acts',
        example='there is no gap to report and this rule is the only one that acts',
        source='specification/research/00-design-decisions.md:221@4e47d0e',
        decision='C10',
    ),
    'C12-same-baseline-window': OldMeaning(
        pattern='over the same \\[d-66, d-7\\] baseline window',
        example='built from its own readings alone over the same `[D-66, D-7]` baseline window',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C12',
    ),
    'C13-era-clip-becomes-hole-clip': OldMeaning(
        pattern='re-derived per dataset: a datasets band is clipped, unreported, at an internal capture hole',
        example="re-derived per dataset: a dataset's band is clipped, unreported, at an internal capture hole",
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C13',
    ),
    'C14-tier-change-collapses-baseline': OldMeaning(
        pattern='source-tier change, §3\\.3\\) collapses the baseline',
        example='a baseline re-establishment (a coverage gap or a source-tier change, §3.3) collapses the baseline deliberately',
        source='specification/research/00-design-decisions.md:220@4e47d0e',
        decision='C14',
    ),
    'C15-tier-change-called-re-establishment': OldMeaning(
        pattern='baseline re-establishment the athlete is told about',
        example='the baseline re-establishment the athlete is **told** about',
        source='specification/research/00-design-decisions.md:218@4e47d0e',
        decision='C15',
    ),
    'C16-hrv21-reads-below-that-band': OldMeaning(
        pattern='judged-week mean reads below that band',
        example='whose judged-week mean reads below that band is named as disagreeing, in either direction',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C16 (downstream: spec-mirror/features/F006-per-tier-hrv-datasets.md AC10)',
    ),
    'C17-hrv24-read-on-last': OldMeaning(
        pattern='the dataset the athlete was read on last',
        example='from the dataset the athlete was read on last',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C17 (downstream: runcoach-api/src/runcoach_api/schemas.py:530)',
    ),
    'C18-no-tier-from-resolver': OldMeaning(
        pattern='resolvebaselinetier answer(?:ing|ed) no tier at all',
        example='(`resolve_baseline_tier` answering "no tier at all"',
        source='specification/research/00-design-decisions.md:226@4e47d0e',
        decision='C18 (downstream: runcoach-api/src/runcoach_api/schemas.py:443; hrv_trend.py docstrings, F009)',
    ),
    'C19-hrv-03-tag-and-confidence': OldMeaning(
        pattern='source-tier tag and confidence',
        example='a numeric value with a source-tier tag and confidence',
        source='specification/research/00-design-decisions.md:162@4e47d0e',
        decision='C19',
    ),
    'C19-hrv-04-reduced-confidence': OldMeaning(
        pattern='at reduced confidence',
        example='degrading to a numeric resting rMSSD (Health Snapshot, then Health API overnight) at reduced confidence',
        source='specification/research/00-design-decisions.md:105@4e47d0e',
        decision='C19 (downstream: runcoach-api/src/runcoach_api/schemas.py:298)',
    ),
    'C21-dec01-bonus-section': OldMeaning(
        pattern='its bonus section\\) treats the chat interface as a checkpoint',
        example='`decisions/01` (its "Bonus" section) treats the chat interface as a checkpoint',
        source='specification/research/00-design-decisions.md:243@4e47d0e',
        decision='C21',
    ),
    'C24-arch06-ignores-by-default': OldMeaning(
        pattern='coaching logic ignores by default',
        example='vendor-derived metrics live only in a labeled sidecar the coaching logic ignores by default',
        source='specification/research/00-design-decisions.md:90@4e47d0e',
        decision='C24',
    ),
    'C26-cold01-hrv-input': OldMeaning(
        pattern='inferred from resting hr, hrv, and demographics',
        example='VO2max inferred from resting HR, HRV, and demographics',
        source='specification/research/00-design-decisions.md:132@4e47d0e',
        decision='C26',
    ),
    'C27-in-activity-hrv-not-computed-at-all': OldMeaning(
        pattern='in-activity hrv is not computed at all',
        example='in-activity HRV is not computed at all',
        source='specification/research/00-design-decisions.md:155@4e47d0e',
        decision='C27',
    ),
    'C27-lt1-picked-up-without-amendment': OldMeaning(
        pattern='to be picked up only under the rr-quality gating',
        example='to be picked up only under the RR-quality gating the `spec/02` pipeline already partly enforces',
        source='specification/research/00-design-decisions.md:172@4e47d0e',
        decision='C27',
    ),
    'C28-lt1-surrogate-refinement': OldMeaning(
        pattern='refined toward an lt1 surrogate where one is identifiable',
        example='refined toward an LT1 surrogate where one is identifiable (spec §5.4.2)',
        source='specification/research/00-design-decisions.md:168@4e47d0e',
        decision='C28; F011 makes the design change to spec/05 §5.4.2',
    ),
    'C30-ctl-rise-row-deferred': OldMeaning(
        pattern='register row is deliberately deferred',
        example='its Part 3 register row is deliberately **deferred until field data refines the value**',
        source='specification/research/00-design-decisions.md:241@4e47d0e',
        decision='C30',
    ),
    'C31-ind01-remains-tunable': OldMeaning(
        pattern='remains tunable per athlete as data accumulates',
        example='remains tunable per athlete as data accumulates',
        source='specification/research/00-design-decisions.md:100@4e47d0e',
        decision='C31',
    ),
    'C32-band-without-floor': OldMeaning(
        pattern='±\\s*0\\.5\\s*[·×]\\s*sd\\(ln rmssd\\) smallest-worthwhile-change (?:\\(swc\\) )?band',
        example='7-day rolling ln rMSSD vs a **±0.5·SD(ln rMSSD)** smallest-worthwhile-change band',
        source='specification/research/00-design-decisions.md:105@4e47d0e',
        decision='C32',
    ),
    'C33-hrv-17-tolerance-not-published': OldMeaning(
        pattern='is not published in thresholds',
        example='The constant is **not** published in `thresholds`',
        source='specification/research/00-design-decisions.md:221@4e47d0e',
        decision='C33',
    ),
    'C37-gate03-remeasured-not-cited': OldMeaning(
        pattern='so it is re-measured rather than cited',
        example='does not transfer, so it is re-measured rather than cited',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C37',
    ),
    'DOC-06-C31-every-number-tunable': OldMeaning(
        pattern='every number here is a default flagged as a heuristic, tunable per athlete as data accumulates',
        example='Every number here is a default flagged as a heuristic, tunable per athlete as data accumulates.',
        source='specification/research/00-design-decisions.md:3@4e47d0e',
        decision='C31',
    ),
    'DOC-09-C38-superseded-text-left-standing': OldMeaning(
        pattern='left standing as the history of the (?:single-baseline )?rule rather than rewritten',
        example='which is left standing as the history of the rule rather than rewritten',
        source='specification/research/00-design-decisions.md:221@4e47d0e',
        decision='C38',
    ),
    'GOAL-02-C22-goal-contract-two-fields': OldMeaning(
        pattern='goal contract -- the declared target pace and the race date',
        example="The *goal contract* — the declared target pace and the race date — is the athlete's.",
        source='specification/research/00-design-decisions.md:65@4e47d0e',
        decision='C22',
    ),
    'HRV-11-per-day-collapse-unspecified': OldMeaning(
        pattern='per-day collapse \\(samedaylatercapture\\)',
        example='with per-day collapse (`same_day_later_capture`) and distinct-local-day counting unchanged',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='HRV-11',
    ),
    'PRIN-05-C06-conservative-wins-unscoped': OldMeaning(
        pattern='or when the state estimate is low-confidence, the more conservative reading wins',
        example='or when the state estimate is low-confidence, the **more conservative reading wins**',
        source='specification/research/00-design-decisions.md:37@4e47d0e',
        decision='C06',
    ),
    'PRIN-08-C24-sidecar-ignored-by-default': OldMeaning(
        pattern='sidecar the coaching logic ignores by default',
        example='vendor-derived metrics live only in a labeled sidecar the coaching logic ignores by default',
        source='specification/research/00-design-decisions.md:90@4e47d0e',
        decision='C24',
    ),
    'PRIN-08-C25-rule-file-short-list': OldMeaning(
        pattern='vendor proprietary estimates \\(vo2max, training status, training readiness, body battery, performance condition\\)',
        example='vendor proprietary estimates (VO2max, Training Status, Training Readiness, Body Battery, Performance Condition)',
        source='.claude/rules/project-domain-and-spec-fidelity.md:13@4e47d0e',
        decision="C25 (F011 sweeps the rule file's quarantine list)",
    ),
    'PRIN-10-C19-reduced-confidence': OldMeaning(
        pattern='admitted as an hrv input at reduced confidence',
        example='so it is admitted as an HRV input at reduced confidence',
        source='specification/research/00-design-decisions.md:43@4e47d0e',
        decision='C19 (downstream: schemas.py and the sibling passages F011 sweeps)',
    ),
    'PRIN-12-C33-tolerance-not-published': OldMeaning(
        pattern='constant is not published in thresholds',
        example='The constant is **not** published in `thresholds`',
        source='specification/research/00-design-decisions.md:221@4e47d0e',
        decision='C33',
    ),
    'PRIN-14-C07-weak-evidence-only': OldMeaning(
        pattern='up-regulation on weak evidence, which §1\\.7 forbids',
        example='up-regulation on weak evidence, which §1.7 forbids',
        source='specification/research/00-design-decisions.md:220@4e47d0e',
        decision='C07',
    ),
    'PRIN-15-C06-accepted-as-priced': OldMeaning(
        pattern='accepted because quality-first promotes the best available instrument',
        example='Accepted because quality-first promotes the *best available* instrument',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='C06',
    ),
    'PRIN-16-C08-silence-tolerated-freely': OldMeaning(
        pattern='direction §1\\.7 tolerates freely',
        example='which is down-regulation, the direction §1.7 tolerates freely',
        source='specification/research/00-design-decisions.md:220@4e47d0e',
        decision='C08',
    ),
    'T07-acwr-band': OldMeaning(
        pattern='~0\\.8--1\\.5 band|wide band \\(~0\\.8--1\\.5\\)',
        example='wide band (~0.8–1.5)',
        source='specification/research/00-design-decisions.md:106@4e47d0e',
        decision='T-07; downstream reach is .claude/rules/ only (R9, F011 S12)',
    ),
    'T07-ctl-rise-band': OldMeaning(
        pattern='ctl-rise band',
        example='the Section 6 §6.2.2 **weekly-CTL-rise band** was fixed',
        source='specification/research/00-design-decisions.md:241@4e47d0e',
        decision='T-07; downstream reach is .claude/rules/ only (R9, F011 S12)',
    ),
    'T07-tolerance-band': OldMeaning(
        pattern='\\[18, 44\\] band',
        example='the `[18, 44]` band measured for `recency_tolerance_days`',
        source='specification/research/00-design-decisions.md:230@4e47d0e',
        decision='T-07; downstream reach is .claude/rules/ only (R9, F011 S12)',
    ),
    'T07-tsb-target-form-band': OldMeaning(
        pattern='target form band',
        example='Race-day target form band (TSB)',
        source='specification/research/00-design-decisions.md:120@4e47d0e',
        decision='T-07; downstream reach is .claude/rules/ only (R9, F011 S12)',
    ),
}

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
