"""F011 T200 (S11): build ``tests/data/research00_census.csv`` -- every site of every keyed old meaning.

**A one-time tool.** It was run once, at the post-F008 tree (``78de5b4``), before any site was edited,
and its output is committed. The committed CSV is the record, not this script: the inventory it reads
lives in the data dir and changes (T211 turns the inventory's C14 quotations into pointers), and the
site tasks (T202-T214) rewrite the very text it greps, so a later run would differ. Never re-run it to
"refresh" the census; the gate (``test_research00_downstream.py``) checks the committed rows both ways.

It was re-run once more, by T219 at ``6050724`` (no site edited yet), when the gate began reading a
``.py`` file with its comment markers removed: the only rows that changed were ``hrv_trend.py``'s
``grep`` rows (three new hits, three excerpts widened or re-cut off a ``#:``), all F009's.

Run from the main checkout (the inventory is read by its absolute data-dir path)::

    uv run --package runcoach-api python runcoach-api/tests/support/build_research00_census.py

The census has the columns ``key,path,excerpt,source``. ``source`` is:

- ``grep``: a key's current pattern hits the site: the gate's own ``scan()``, honouring ``KEY_ROOTS``,
  outside quotation (S3). A hit sheltered by an F009 exception (S4, ``hrv_trend.py``) is a site too.
- ``inventory``: no pattern reaches it, and either the inventory's "Code:"/"Code/tests:" line for the
  key's C-item cites it, or F011 AC2 names it (``MANUAL_ROWS``), or a review found a site task's own
  rewrite still stating the old meaning (``LATER_ROWS``: that text postdates the tree the builder runs
  on, so it is read at the commit that wrote it).
- ``loose``: no pattern reaches it; the key's distinctive nouns (``LOOSE_NOUNS``) do, and
  ``LOOSE_RESOLUTIONS`` resolves the hit as a site.
- ``narrowed``: correct prose that a T218 narrowing stopped matching (``NARROWED_FROM`` in the gate).
  It is a record of the narrowing, not a site.

Rows are limited to S2's roots, plus the one test-comment row ``runcoach-api/tests/
test_hrv_no_regression_gate.py`` (C05, S6). Every candidate the builder drops -- an inventory citation
outside the roots or stating no keyed phrase, a loose hit resolved out -- is printed with its reason.
Each excerpt is the file's raw text (whitespace collapsed to one space, so the CSV holds one row per
line), long enough that its ``normalize()``d form occurs exactly once in its file.
"""

import csv
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS = REPO_ROOT / "runcoach-api" / "tests"
CENSUS_PATH = TESTS / "data" / "research00_census.csv"
DATA_DIR = Path("C:/Users/xtoph/.claude/plugins/data/shipyard-acendas/projects/fe09bb9f181d")
INVENTORY = DATA_DIR / "spec" / "references" / "research00-rewrite-inventory.md"
#: The commit the inventory's line numbers refer to (its header: "at commit 4e47d0e").
INVENTORY_COMMIT = "4e47d0e"
HEADER = ["key", "path", "excerpt", "source"]
SOURCES = ("grep", "inventory", "loose", "narrowed")

#: The one row outside S2's roots (S6): the no_regression gate's comment quoting "reopens" (C05).
TEST_COMMENT_ROW = "runcoach-api/tests/test_hrv_no_regression_gate.py"
HRV_TREND = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load("research00_downstream_gate", TESTS / "test_research00_downstream.py")
OLD_MEANINGS = GATE.OLD_MEANINGS
normalize = GATE.normalize


# --------------------------------------------------------------------------------------------------
# The loose grep: each key's distinctive nouns, over normalize()d text.
# --------------------------------------------------------------------------------------------------

#: Looser than each key's pattern: the nouns that carry the old meaning, so a paraphrase the pattern
#: misses still surfaces. Keys that share one decision and one wording share one entry, filed under
#: the first key (C06-hrv-25/PRIN-15, C08/PRIN-16, C24/PRIN-08-C24, C33/PRIN-12-C33, the C19 family,
#: C31/DOC-06); a resolution names the key its row is filed under. ``KEY_ROOTS`` applies: the T-07
#: entry reaches the rule files only (S12).
LOOSE_NOUNS = {
    "AUT-02-C23-override-outside-autonomy": r"safety (?:override|pathway)",
    "AUT-04-C20-two-purposes-only": r"two purposes|two directions above|nothing more",
    "C01-withhold-not-judgeable-only": r"not judgeable but holds",
    "C02-withhold-against-selected": r"judged-week day of the selected",
    "C03-return-is-free": r"\bis free\b|free return|return is free|return free",
    "C04-hole-at-least": r"hole of at least|at least gapresetdays",
    "C05-gate02-worse-rate-reopens": r"re-?open(?:s|ed|ing)?\b|hysteresis",
    "C06-gate01-one-exception": r"one (?:named, )?counted exception",
    "C06-hrv-25-accepted-cost": r"quality-first promotes",
    "C08-reg11-readiness-gate-down-weights": r"down-weights|tolerates freely",
    "C09-residual-carried-to-idea-071": r"idea-071\]\]s sprint",
    "C10-lone-candidate-never-struck":
        r"(?:its|their) own reference|never struck|lone (?:judgeable |established )?(?:candidate|dataset)",
    "C10-recency-only-rule-that-acts": r"only one that acts|not a race but a partition",
    "C12-same-baseline-window": r"same (?:\[d-66, d-7\] )?baseline window",
    "C13-era-clip-becomes-hole-clip": r"clipped, unreported",
    "C14-tier-change-collapses-baseline": r"collapses the baseline",
    "C15-tier-change-called-re-establishment": r"re-establish\w*",
    "C16-hrv21-reads-below-that-band": r"reads? below (?:that|its own|its) band|reading below its own band",
    "C17-hrv24-read-on-last": r"(?:read on|used) last",
    "C18-no-tier-from-resolver": r"no tier at all",
    "C19-hrv-04-reduced-confidence": r"reduced confidence|confidence (?:weight|discount)|source-tier tag",
    "C21-dec01-bonus-section": r"bonus section|bonus",
    "C24-arch06-ignores-by-default": r"ignores? (?:\w+ )?by default",
    "C26-cold01-hrv-input": r"resting hr, hrv",
    "C27-in-activity-hrv-not-computed-at-all": r"in-activity hrv",
    "C27-lt1-picked-up-without-amendment": r"picked up only under",
    "C28-lt1-surrogate-refinement": r"lt1 surrogate",
    "C30-ctl-rise-row-deferred": r"deliberately deferred|row is (?:deliberately |intentionally )?deferred",
    "C31-ind01-remains-tunable": r"tunable per athlete",
    "C32-band-without-floor": r"0\.5\s*(?:[·×x]\s*)?(?:the )?sd\b",
    "C33-hrv-17-tolerance-not-published": r"not published in thresholds",
    "C37-gate03-remeasured-not-cited": r"re-measured rather than cited|re-measure it",
    "DOC-06-C31-every-number-tunable": r"every number here",
    "DOC-09-C38-superseded-text-left-standing": r"left standing as the history",
    "GOAL-02-C22-goal-contract-two-fields": r"goal contract",
    "HRV-01-R13-four-tier-hierarchy": r"four-tier|four tiers",
    "HRV-11-per-day-collapse-unspecified": r"per-day collapse|only the later is kept",
    "HRV-40-R13-now-sustaining-tier": r"now-sustaining",
    "PRIN-05-C06-conservative-wins-unscoped": r"more conservative reading wins",
    "PRIN-08-C25-rule-file-short-list": r"performance condition",
    "PRIN-12-R13-withheld-response-stays-reproducible": r"reproducible by hand",
    "PRIN-14-C07-weak-evidence-only": r"weak evidence",
    "T07-acwr-band": r"(?:acwr|tsb|ctl|form|tolerance|18, 44\])[^.;]{0,60}\bband\b|\bband\b[^.;]{0,30}(?:acwr|tsb)",
}


# --------------------------------------------------------------------------------------------------
# Resolutions. Each loose hit that no row already covers is matched by the first entry naming its
# loose key (or "*") and its path whose fragment overlaps the hit or lies on the hit's line; a
# fragment of None matches every such hit in the file. An ``_in`` entry's fragment becomes the row's
# excerpt (as the file's raw text), filed under ``key`` (default: the loose key).
# --------------------------------------------------------------------------------------------------

S02 = "specification/spec/02-canonical-data-schema-ingestion.md"
S03 = "specification/spec/03-derived-metric-formulas.md"
S04 = "specification/spec/04-physiological-state-model.md"
S05 = "specification/spec/05-training-plan-generation.md"
S06 = "specification/spec/06-adaptation-logic.md"
S07 = "specification/spec/07-recovery-and-taper.md"
S08 = "specification/spec/08-conversational-coach-interface.md"
OUTLINE = "specification/spec_outline.md"
DEVPLAN = "specification/spec_development_plan.md"
DEC01 = "specification/decisions/01-conversational-coach-interface.md"
R02 = "specification/research/02-wearable-data-garmin.md"
R05 = "specification/research/05-data-to-adaptation.md"
OPENAPI = "contracts/openapi.yaml"
SCHEMAS = "runcoach-api/src/runcoach_api/schemas.py"
MAIN = "runcoach-api/src/runcoach_api/main.py"
RULE = ".claude/rules/project-domain-and-spec-fidelity.md"
F006 = "spec-mirror/features/F006-per-tier-hrv-datasets.md"
F006DM = "spec-mirror/references/F006-dataset-model.md"


def _in(loose_key, path, fragment, reason, key=None):
    return ("in", loose_key, path, fragment, reason, key or loose_key)


def _out(loose_key, path, fragment, reason):
    return ("out", loose_key, path, fragment, reason, None)


#: Reasons used more than once.
F009_OWNS = ("hrv_trend.py is F009's (F011 Not in scope: every metrics/hrv_trend.py edit; S4): F009 AC5 "
             "fixes its sites and deletes their EXCEPTIONS, so F011's gate cannot hold an unsheltered row "
             "there -- listed for F009 in IDEA-105")
F012_REPRODUCIBLE = ("'reproducible by hand' stated without the OPEN exceptions is the key F012 adds (S13 as "
                     "split 2026-09-27); T202/T205/T209/T210 leave these sites to F012")
UNRELATED = "the noun in another sense; no old meaning stated"

LOOSE_RESOLUTIONS = (
    # ---- in: sites no pattern reaches ------------------------------------------------------------
    # C22/C23: the goal contract as two fields; the safety pathway as a matter the system does not own.
    _in("GOAL-02-C22-goal-contract-two-fields", S06, "the goal contract (target pace and race date",
        "C22: names the goal contract as two fields (GOAL-02: goal_pace_target, race_date, distance_m)"),
    _in("GOAL-02-C22-goal-contract-two-fields", S06, "the athlete supplies a new target pace or race date",
        "C22: a goal-contract change listed as pace or date only (GOAL-02 has three fields; GOAL-05)"),
    _in("AUT-02-C23-override-outside-autonomy", S06,
        "and the safety pathway (a stop-and-escalate or seek-assessment instruction is athlete-facing",
        "C23: Section 6 disowns the stop-and-escalate instruction itself; AUT-08 has the system issue it "
        "and AUT-02 leaves the athlete only the clinical action (named at spec/06:13 by F011)"),
    # C03: a return is free.
    _in("C03-return-is-free", F006DM, "which a free return contradicts",
        "C03: asserts a free return (HRV-34 and the C03 resolution: a return can be skipped, withhold and "
        "be clipped)"),
    _in("C03-return-is-free", F006DM, "making a return free contradicts it",
        "C03: asserts a free return, as at :428"),
    # C04: the hole clip at "at least" gap_reset_days.
    _in("C04-hole-at-least", F006DM, "at an internal hole of at least GAP_RESET_DAYS",
        "C04: 'at least' (HRV-37: more than 21, so 21 does not clip); §14 is live (S1)"),
    # C05: hysteresis deferred / reopened.
    _in("C05-gate02-worse-rate-reopens", F006DM, "Explicit hysteresis deferred, now with a trigger it can actually fire",
        "C05: hysteresis still deferred with a live trigger (GATE-02: decided, no hysteresis); §12 is "
        "swept (S1: F006-dataset-model is not a SECTION_RECORD_FILES file)"),
    # C06: one counted exception.
    _in("C06-gate01-one-exception", F006, "save AC21's one counted exception",
        "C06: one counted exception (GATE-04/PRIN-15: the listed exceptions, three today)"),
    _in("C06-gate01-one-exception", F006DM, "save AC21's one counted exception",
        "C06: one counted exception, as in the feature file"),
    # C08/PRIN-16: silence as the direction §1.7 tolerates freely.
    _in("C08-reg11-readiness-gate-down-weights", S03,
        "also turn silent (hrv_normal → hrv_unavailable), the direction research/00 §1.7 tolerates freely",
        "C08: silence as the direction §1.7 tolerates freely (the pattern misses 'research/00' inserted)",
        key="PRIN-16-C08-silence-tolerated-freely"),
    _in("C08-reg11-readiness-gate-down-weights", S03,
        "outgoing week (hrv_normal → hrv_unavailable, the direction research/00 §1.7 tolerates freely",
        "C08: the same claim, the second site in the paragraph", key="PRIN-16-C08-silence-tolerated-freely"),
    # C10: "a lone ... is its own reference" (the T117 wording).
    _in("C10-lone-candidate-never-struck", F006DM, "so a lone established dataset is its own reference and is never skipped (AC7)",
        "C10: the T117 'its own reference' form that F006 AC7 repeats (IDEA-090); HRV-51 states the rule "
        "without it (T209 names :70)"),
    # C13: the era clip become the hole clip.
    _in("C13-era-clip-becomes-hole-clip", S03,
        "the same cross-tier question now decides only the reported tier_change reset",
        "C13: the cross-tier question left deciding only the report, as if the era clip became the hole "
        "clip (HRV-37 and HRV-40 are two clips; the era clip is unconditional)"),
    # C14 and C15: the tier change as a re-establishment that collapses the baseline.
    _in("C14-tier-change-collapses-baseline", S03, "a re-establishment collapses the baseline deliberately",
        "C14: 'every baseline re-establishment this section performs' includes the tier change (:236 'both "
        "re-establishment rules'); FIG-01: only the coverage-gap reset collapses a band"),
    _in("C15-tier-change-called-re-establishment", S03, "reachable after every baseline re-establishment this section performs",
        "C15: the tier change counted among the re-establishments (T-16: re-establishment names only the "
        "reset after a coverage gap)"),
    _in("C15-tier-change-called-re-establishment", S03, "both re-establishment rules are exactly what they were",
        "C15: 'both re-establishment rules' (T-16)"),
    _in("C15-tier-change-called-re-establishment", S03, "both re-establishment rules are unchanged",
        "C15: 'both re-establishment rules' (T-16)"),
    _in("C15-tier-change-called-re-establishment", S03, "not on the re-establishment rule's previous-window clause",
        "C15: the era rule called the re-establishment rule (T-16)"),
    _in("C15-tier-change-called-re-establishment", S03, "A re-establishment (adopts or abandons, above) is asserted only when",
        "C15: the tier change called a re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", S03, "is never a re-establishment in either direction",
        "C15: a tier change called a re-establishment, as a rule in force (T-16)"),
    _in("C15-tier-change-called-re-establishment", S03, "while the re-establishment is reported only when the density tolerance holds",
        "C15: the reported tier_change reset called the re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", S03, "the boundary this section's re-establishment clause finds",
        "C15: the era clause called the re-establishment clause (T-16)"),
    _in("C15-tier-change-called-re-establishment", OPENAPI, "the day the current baseline era began, when the re-establishment is reported",
        "C15: reset_on (coverage_gap or tier_change) called the re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", OPENAPI, "why the re-establishment is reported",
        "C15: reset_reason's two values called the re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", OPENAPI, "this dataset's own reported re-establishment day",
        "C15: the per-dataset reset_on called the re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", OPENAPI, "why this dataset's re-establishment is reported",
        "C15: the per-dataset reset_reason called the re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", SCHEMAS, "local day the current baseline era began, when the re-establishment is reported",
        "C15: the schemas.py copy of openapi's reset_on (T-16; AC5: the two change together)"),
    _in("C15-tier-change-called-re-establishment", SCHEMAS, "why the baseline was re-established",
        "C15: a sustained source-tier change listed as a re-establishment (T-16)"),
    _in("C15-tier-change-called-re-establishment", SCHEMAS, "this dataset's own reported re-establishment day",
        "C15: the schemas.py copy of openapi's per-dataset reset_on (T-16)"),
    _in("C15-tier-change-called-re-establishment", SCHEMAS, "why this dataset's re-establishment is reported",
        "C15: the schemas.py copy of openapi's per-dataset reset_reason (T-16)"),
    # C17: the fallback as "read on last" only.
    _in("C17-hrv24-read-on-last", S03, "the tier with min_baseline_readings that the athlete was read on last holds it",
        "C17: the fallback as 'read on last' alone (HRV-59 has three clauses)"),
    _in("C17-hrv24-read-on-last", S03, "presents the dataset the athlete used last",
        "C17: the fallback as 'used last' alone (HRV-59)"),
    _in("C17-hrv24-read-on-last", F006, "populated from the dataset the athlete used last",
        "C17: AC9's fallback as 'used last' alone (HRV-59)"),
    _in("C17-hrv24-read-on-last", F006DM, "populated from the dataset the athlete used last",
        "C17: §14's fallback as 'used last' alone (HRV-59); §14 is live (S1)"),
    # C19: a per-tier confidence weight or discount applied or admitted at.
    _in("C19-hrv-04-reduced-confidence", S02, "reads this to apply the tier's confidence weight",
        "C19: the trend applies a per-tier confidence weight (HRV-54: none is computed in Section 3)"),
    _in("C19-hrv-04-reduced-confidence", S02, "carry a confidence discount below the chest-strap tier",
        "C19: the numeric tiers carry a confidence discount (HRV-04: reduced fidelity, an ordinal rank; "
        "T203 names :212)"),
    _in("C19-hrv-04-reduced-confidence", S02, "reading hrv_source_tier to apply the per-tier confidence weight",
        "C19: Section 3 applies a per-tier confidence weight (HRV-54)"),
    _in("C19-hrv-04-reduced-confidence", S03, "The per-tier confidence weight at which §3.7.1 admits the numeric tiers",
        "C19: §3.7.1 said to admit the numeric tiers at a weight (HRV-04: never at a numeric weight)"),
    _in("C19-hrv-04-reduced-confidence", S03, "admitted at an explicit confidence discount",
        "C19: the numeric tiers admitted at a confidence discount (HRV-04)"),
    _in("C19-hrv-04-reduced-confidence", S03, "confidence weights are shipped defaults",
        "C19: per-tier confidence weights said to ship (HRV-54: none is computed)"),
    _in("C19-hrv-04-reduced-confidence", R02, "(a) it carries a confidence discount",
        "C19: Tiers 2-3 carry a confidence discount (HRV-04; T206 names :168)"),
    _in("C19-hrv-04-reduced-confidence", OPENAPI, "the numeric per-tier confidence weight at which §3.7.1 admits the numeric tiers",
        "C19: §3.7.1 said to admit the numeric tiers at a weight (HRV-04)"),
    _in("C19-hrv-04-reduced-confidence", SCHEMAS, "the numeric per-tier confidence weight at which 3.7.1 admits the numeric tiers",
        "C19: the schemas.py copy of the openapi sentence (HRV-04; AC5)"),
    _in("C19-hrv-04-reduced-confidence", F006DM, "the numeric per-tier weight §3.7.1 defines",
        "C19: §3.7.1 said to define a numeric per-tier weight (HRV-04)"),
    # C27: in-activity HRV never computed, unscoped.
    _in("C27-in-activity-hrv-not-computed-at-all", S02, "in-activity HRV is never computed",
        "C27: unscoped (HRV-05: not in v1 or for any readiness input; LT1-02 amends it)"),
    _in("C27-in-activity-hrv-not-computed-at-all", S02, "in-activity HRV (never computed at all",
        "C27: unscoped, as at :80"),
    # C30: the CTL-rise register row deferred.
    _in("C30-ctl-rise-row-deferred", DEVPLAN, "register row is intentionally deferred until real field data",
        "C30: the register row said deferred (REG-19 carries it, marked PROVISIONAL)"),
    _in("C30-ctl-rise-row-deferred", DEVPLAN, "register row is intentionally deferred until field data refines",
        "C30: the same, in the back-port list"),
    # C31/DOC-06: every number tunable per athlete, unscoped.
    _in("C31-ind01-remains-tunable", S03, "Every numeric constant below is a default flagged as a heuristic, tunable per athlete as data accumulates",
        "C31: every constant tunable, unscoped (DOC-06/IND-04: only under IND-01, invariants excepted)",
        key="DOC-06-C31-every-number-tunable"),
    _in("C31-ind01-remains-tunable", S04, "Every numeric constant below is a default flagged as a heuristic, tunable per athlete as data accumulates",
        "C31: as spec/03's preamble", key="DOC-06-C31-every-number-tunable"),
    _in("C31-ind01-remains-tunable", S05, "Every numeric constant below is a default flagged as a heuristic, tunable per athlete as data accumulates",
        "C31: as spec/03's preamble", key="DOC-06-C31-every-number-tunable"),
    _in("C31-ind01-remains-tunable", S06, "Every numeric constant below is a default flagged as a heuristic, tunable per athlete as data accumulates",
        "C31: as spec/03's preamble", key="DOC-06-C31-every-number-tunable"),
    _in("C31-ind01-remains-tunable", S07, "Every numeric constant below is a default flagged as a heuristic, tunable per athlete as data accumulates",
        "C31: as spec/03's preamble", key="DOC-06-C31-every-number-tunable"),
    _in("C31-ind01-remains-tunable", R05, "Every threshold given as a number is a default flagged as a heuristic, tunable per athlete as data accumulates",
        "C31: every threshold tunable, unscoped (DOC-06/IND-04)", key="DOC-06-C31-every-number-tunable"),
    # C32: the band floor called a Section 3 heuristic below the register.
    _in("C32-band-without-floor", S03, "a Section 3 heuristic default like every other constant here",
        "C32: the floor held as a Section 3 heuristic below the register (HRV-07 now carries it; T202 "
        "names :240)"),
    # C37: the tolerance re-measured.
    _in("C37-gate03-remeasured-not-cited", F006DM, "AC19/AC21 must re-measure it",
        "C37: the re-measurement promised (GATE-03: 28 rests on HRV-16 alone until one is recorded)"),
    _in("C37-gate03-remeasured-not-cited", F006DM, "Do not cite it as transferring; AC19/AC21 re-measure it",
        "C37: as at :331, in §13"),
    # DOC-09: superseded text left standing as history.
    _in("DOC-09-C38-superseded-text-left-standing", S03, "they are left standing as the history of the reported reset",
        "C38: superseded text left standing inline as history (DOC-09)"),
    # PRIN-05: conservative-wins unscoped.
    _in("PRIN-05-C06-conservative-wins-unscoped", S06, "or when the state estimate is low-confidence — the more conservative reading wins",
        "C06: conservative-wins unscoped, as research/00 §1.4 read (PRIN-05: never between two HRV datasets)"),
    # PRIN-08-C25: the five-item quarantine list.
    _in("PRIN-08-C25-rule-file-short-list", RULE, "Body Battery, Training Readiness, VO2max estimate, and Performance Condition are display-only",
        "C25: the guardrail's list omits Training Status and the HRV Status classification (T207 :25)"),
    _in("PRIN-08-C25-rule-file-short-list", OUTLINE, "VO2max, Training Status, Training Readiness, Body Battery, Performance Condition — ingested",
        "C25: the rule file's five-item quarantine list, omitting the HRV Status classification (PRIN-08)"),
    _in("PRIN-08-C25-rule-file-short-list", R05, "VO2max estimate, Training Status/Readiness, Body Battery, Performance Condition",
        "C25: the quarantined namespace listed without the HRV Status classification (PRIN-08)"),
    # PRIN-14: up-regulation on weak evidence, which §1.7 forbids.
    _in("PRIN-14-C07-weak-evidence-only", S03, "reading a genuinely suppressed week as normal (up-regulation on weak evidence",
        "C07: the pattern's claim with 'research/00' inserted (PRIN-14: the forbidden direction, T-24)"),
    _in("PRIN-14-C07-weak-evidence-only", S03, "on both sides — up-regulation on weak evidence, which research/00 §1.7 forbids",
        "C07: the same claim, the second site in the paragraph"),
    _in("PRIN-14-C07-weak-evidence-only", S06, "would be up-regulation on weak evidence, the one direction research/00 §1.7 forbids",
        "C07: the same claim (T205 leaves :74's broad 'withhold' to F012, not this clause)"),
    # T-07: ACWR's range called a band, in the rule file (S12).
    _in("T07-acwr-band", RULE, "the ACWR band, taper duration",
        "T-07: ACWR's range called a band in the rule file (S12)"),

    # ---- out -------------------------------------------------------------------------------------
    # Rows removed after the census commit (T200 wave-4 review): the gate's frozen CENSUS_REMOVED
    # records each with its reason (S11: a row cannot be dropped silently).
    *(_out(key, path, excerpt, f"removed (CENSUS_REMOVED): {reason}")
      for key, path, excerpt, reason in GATE.CENSUS_REMOVED),
    _out("*", HRV_TREND, None, F009_OWNS),
    _out("PRIN-12-R13-withheld-response-stays-reproducible", "*", None, F012_REPRODUCIBLE),
    _out("AUT-02-C23-override-outside-autonomy", "*", None,
         "the safety pathway named as athlete-facing because the athlete must act on it, or the override "
         "as the system's top rung: AUT-02's exception and AUT-08, not C23's 'override outside autonomy'"),
    _out("AUT-04-C20-two-purposes-only", "*", None,
         "'nothing more' of ACWR's context flag or of the LLM's translation role (AUT-05), not chat's "
         "purposes"),
    _out("C03-return-is-free", "*", None, UNRELATED),
    _out("C05-gate02-worse-rate-reopens", F006, "Decided 2026-09-21, no hysteresis",
         "states the decision taken (GATE-02); the 'reopens' meta-mention beside it is a grep row"),
    _out("C05-gate02-worse-rate-reopens", F006DM, "It cannot be hysteresis'd away",
         "argues against hysteresis; states no deferral"),
    _out("C05-gate02-worse-rate-reopens", F006DM, None, "'re-opened'/'re-opening' of other rules; " + UNRELATED),
    _out("C05-gate02-worse-rate-reopens", ".claude/rules/project-research00-edits.md", None, UNRELATED),
    _out("C05-gate02-worse-rate-reopens", "runcoach-api/src/runcoach_api/ingestion/hrv_classification.py", None,
         UNRELATED),
    _out("C08-reg11-readiness-gate-down-weights", "runcoach-api/src/runcoach_api/ingestion/quality_gates.py",
         None, UNRELATED),
    _out("C10-lone-candidate-never-struck", "runcoach-api/src/runcoach_api/ingestion/rr_reconstruction.py",
         None, UNRELATED),
    _out("C10-lone-candidate-never-struck", "*", None,
         "HRV-51's consequence (a lone established dataset holds the maximum, so is never skipped), a "
         "'no longer its own reference' cost statement, or a dated account of the T164 defect; none "
         "states the T117 lone-candidate rule"),
    _out("C15-tier-change-called-re-establishment", S03, "rather than re-establishing one",
         "correct: a source change re-establishes nothing (HRV-34)"),
    _out("C15-tier-change-called-re-establishment", S03, "not the re-establishment of a baseline, since none is destroyed",
         "correct (HRV-34)"),
    _out("C15-tier-change-called-re-establishment", S03, "an empty week begins no re-establishment",
         "HRV-42's old wording, which F012 keys (S13); T202 leaves it"),
    _out("C15-tier-change-called-re-establishment", S03, "read as a re-establishment",
         "a dated clarification describing the behaviour before a fix, not a rule in force"),
    _out("C15-tier-change-called-re-establishment", S03, "silenced its re-establishment for the whole era",
         "the 'because' clause describing the pre-fix defect, not a rule in force"),
    _out("C15-tier-change-called-re-establishment", S03, "degraded-and-re-established baseline",
         "the baseline re-established after a coverage gap: T-16's own sense"),
    _out("C15-tier-change-called-re-establishment", S03, "What is re-established after a silence",
         "re-establishment after a silence (the coverage gap): T-16's own sense"),
    _out("C15-tier-change-called-re-establishment", S03, "nothing is re-established on a fall in either case",
         "correct (HRV-34)"),
    _out("C15-tier-change-called-re-establishment", S02, None, "correct: nothing is re-established on a fall"),
    _out("C15-tier-change-called-re-establishment", F006DM, "stays unimplemented",
         "a cost record naming spec/03's device/firmware clause as unimplemented; the clause is spec/03's"),
    _out("C15-tier-change-called-re-establishment", F006DM, None,
         "reports what §3.7.3's clause said as the reason to amend it (its 'free return' is a C03 row)"),
    _out("C16-hrv21-reads-below-that-band", "*", None,
         "HRV-25's exposure (hrv_normal while another dataset reads below its own band) or HRV-22's empty "
         "dissent; neither defines disagreement"),
    # spec/02:191's "its reduced confidence is a property of the tier, applied at Section 6" was resolved
    # out here as deferring the weight to Section 6; the wave-7 review found it names a per-tier
    # confidence, and it is a MANUAL_ROWS row now, which covers the hit.
    _out("C19-hrv-04-reduced-confidence", R02, None,
         "research evidence for the weight Section 6's readiness fusion owns: HRV-04 defers the weight, it "
         "does not abolish it"),
    _out("C19-hrv-04-reduced-confidence", "*", None,
         "correct: no confidence weight is computed in Section 3, weighting is deferred to Section 6 "
         "(HRV-54), or the fidelity rank is not a weight"),
    _out("C26-cold01-hrv-input", "*", None, "resting HR and HRV as objective streams, not cold-start inputs"),
    _out("C27-in-activity-hrv-not-computed-at-all", R02, None,
         "research reasoning about in-run HRV, not the rule"),
    _out("C30-ctl-rise-row-deferred", S06, None, UNRELATED),
    _out("C31-ind01-remains-tunable", "*", None,
         "a named default's tunability, which IND-04 keeps under IND-01; C31's old meaning is the universal "
         "'every number'"),
    _out("C32-band-without-floor", S03, "band = mean_baseline(ln rMSSD) ± 0.5 · SD(ln rMSSD)",
         "§3.7.3's formula, whose floor the same section states at :240 (a row there)"),
    _out("C32-band-without-floor", "specification/research/04-coaching-periodization.md", None,
         "describes practice ('commonly ... ±0.5×SD-style'), not the system's band"),
    _out("GOAL-02-C22-goal-contract-two-fields", "*", None,
         "names the goal contract without listing its fields"),
    _out("HRV-11-per-day-collapse-unspecified", F006, "per-day collapse still applies within a dataset",
         "AC4's heading; the 'later' clause is a manual row"),
    _out("PRIN-05-C06-conservative-wins-unscoped", "*", None,
         "scoped to subjective against objective signals, which PRIN-05 keeps"),
    _out("PRIN-08-C25-rule-file-short-list", "*", None,
         "a list that names the HRV Status classification, ends 'and the rest', or describes one metric "
         "in research; not the quarantine list"),
    _out("PRIN-14-C07-weak-evidence-only", "*", None,
         "'a single reading is weak evidence' (PRIN-06, trends), not the §1.7 claim"),
)


# --------------------------------------------------------------------------------------------------
# MANUAL_ROWS: F011 AC2's named sites that no pattern reaches (S11 as amended 2026-09-27).
# --------------------------------------------------------------------------------------------------

#: ``(key, path, fragment, reason)``; the fragment is located in the file and its raw text is the
#: excerpt. Source ``inventory``: F011 AC2's site list, not a pattern, names each one.
MANUAL_ROWS = (
    ("AUT-04-C20-two-purposes-only", DEC01, "Its role is the two directions above and nothing more",
     ("AC2 names decisions/01:33 (C20): chat limited to probe and constraints (AUT-04 adds injury, "
      "subjective reports and plan-change requests)")),
    ("GOAL-02-C22-goal-contract-two-fields", DEC01, "the athlete owns only the goal contract (target pace, race date)",
     "AC2 names decisions/01:33 (C22): the goal contract as two fields (GOAL-02)"),
    ("AUT-02-C23-override-outside-autonomy", DEC01, "and the safety pathway, neither of which the system decides unilaterally",
     "AC2 names decisions/01:33 (C23): the system does decide the safety override unilaterally (AUT-08)"),
    ("C28-lt1-surrogate-refinement", S05, "refined toward the athlete's own data where an LT1 surrogate is identifiable",
     ("AC2 names spec/05:124 (C28, S8): §5.4.2 states only the fixed fraction; the surrogate moves to "
      "future-directions (LT1-01, LT1-03)")),
    ("C28-lt1-surrogate-refinement", S05, "Where the athlete's own data identifies an LT1 surrogate above the noise",
     "AC2 names spec/05:124 (C28, S8): the paragraph's second surrogate sentence"),
    ("GOAL-02-C22-goal-contract-two-fields", S06, "The declared target pace and race date are the athlete's",
     "AC2 names spec/06:242 (C22)"),
    ("GOAL-02-C22-goal-contract-two-fields", S08, "The goal contract — the declared target pace and race date — is the athlete's",
     "AC2 names spec/08:88 (C22): the paraphrase drops the pattern's second 'the'"),
    ("C32-band-without-floor", R05, "(default ± 0.5 × SD(ln rMSSD), the sample standard deviation",
     "AC2 names research/05:81 (C32): the band without its floor (HRV-07)"),
    ("C32-band-without-floor", R05, "Default: ± 0.5·SD(ln rMSSD) — the sample SD",
     "AC2 names research/05:219 (C32)"),
    ("C32-band-without-floor", S02, "the SWC band the trend already uses — ±0.5·SD(ln rMSSD), the sample SD",
     "AC2 names spec/02:212 (C32): the band without its floor"),
    ("C10-lone-candidate-never-struck", S03, "so a lone candidate is never struck",
     ("AC2 names spec/03 §3.7.3 (C10, IDEA-090): the T117 clause; the excerpt stops before F010's 'gains "
      "no key' clause (S4)")),
    ("C12-same-baseline-window", S03, "over the same baseline window and judged week",
     "T200's list names spec/03:238 (C12): HRV-12 counts establishment in the dataset's own clipped window"),
    ("HRV-11-per-day-collapse-unspecified", F006, "only the later is kept",
     "AC2 names F006 AC4 (IDEA-079): HRV-11 keeps the earliest capture"),
    ("C10-lone-candidate-never-struck", F006, "so a lone established dataset is its own reference and is never skipped",
     ("AC2 names F006 AC7 (C10, IDEA-090): the T117 'its own reference' form; HRV-51 states the rule "
      "without it")),
    ("C05-gate02-worse-rate-reopens", F006, "a worse rate triggers the deferred hysteresis decision",
     "AC2 names F006 AC23 (C05): GATE-02 records the decision taken, no hysteresis"),
    ("C16-hrv21-reads-below-that-band", F006DM, "a judgeable dataset reading below its own band is listed in disagreed_with even when its own verdict is withheld",
     ("AC2 names F006-dataset-model §14's disagreed_with sentence (T167): HRV-21 (other side, either "
      "direction) and HRV-22 (empty when unavailable)")),
    # F011 wave-6 review (S11/S15): three sites no pattern reaches, pinned so the old wording coming
    # back turns the gate red. Added after the census commit; the rows name the pre-work text.
    ("C06-gate01-one-exception", F006, "except the one deferred rate carried as a named, counted exception",
     ("F006 AC21 (C06): one counted exception; GATE-04 saves only the exceptions PRIN-15 lists, three "
      "today (F005-parity and DEFERRED_EXCEPTION under IDEA-087, HRV-25's population under IDEA-099)")),
    ("C19-hrv-04-reduced-confidence", R02,
     "These are the studies the confidence weight for the numeric tiers should rest on",
     ("research/02:113 (C19): a confidence weight for the numeric tiers; HRV-04 admits them at reduced "
      "fidelity, an ordinal rank, never a numeric per-tier confidence weight (swept by T206)")),
    ("C19-hrv-03-tag-and-confidence", R02, "should accept whichever is present and tag its tier/confidence",
     ("research/02:206 (C19): a per-source tier and confidence tag; HRV-04 has a tier rank and no "
      "per-tier confidence")),
    # F011 wave-7 review (S11/S15): C19 sites in spec/02 and spec/03 that state a per-tier confidence,
    # which HRV-04 rules out (reduced fidelity, an ordinal rank that selection reads, never a numeric
    # confidence weight; any weight is Section 6's). The rows name the pre-work text.
    ("C19-hrv-04-reduced-confidence", S03, "the reduced-confidence fallback",
     ("spec/03:215 (C19): the numeric-rMSSD tiers' heading names a reduced-confidence fallback; HRV-04 "
      "admits them at reduced fidelity, an ordinal rank")),
    ("C19-hrv-04-reduced-confidence", S03, "Its lower confidence (per the validation caveats",
     ("spec/03:215 (C19): the numeric tiers' lower confidence as a property applied where §3.7.4 says; "
      "§3.7.4 states reduced fidelity and no confidence weight (HRV-04)")),
    ("C19-hrv-04-reduced-confidence", S02, "whose confidence is set by its tier weight instead",
     ("spec/02:76 (C19): rr_valid_fraction's row gives the numeric wrist tier a tier weight; HRV-04 "
      "admits it at an ordinal rank, never a numeric per-tier confidence weight")),
    ("C19-hrv-04-reduced-confidence", S02,
     "its reduced confidence is a property of the tier, applied at Section 6's readiness fusion",
     ("spec/02:191 (C19): the numeric tiers' reduced confidence applied at Section 6; HRV-04 names "
      "reduced fidelity, with any numeric weight deferred to Section 6's readiness fusion")),
    ("C19-hrv-03-tag-and-confidence", S02, "at that tier's confidence, falling to a numeric",
     ("spec/02:216 (C19): the verdict taken at its tier's confidence; the trend emits it with the source "
      "tier attached and no per-tier confidence (HRV-04, spec §3.7.4)")),
    # F011 sprint-008 review, iteration 1 (S11/S14): sites no pattern reaches that state retired
    # vocabulary or an old rule. The rows name the pre-work text.
    ("C19-hrv-04-reduced-confidence", F006DM,
     "and degrades — at reduced confidence — to a device-computed numeric resting rMSSD",
     ("F006-dataset-model:100 (C19): quotes spec/03 §3.7 as degrading at reduced confidence; §3.7 admits "
      "the numeric tiers at reduced fidelity, an ordinal rank in selection (HRV-04)")),
    ("C17-hrv24-read-on-last", F006DM, "**AC9 keeps a presentation fallback** (F005's rule 3):",
     "F006-dataset-model:450 (C17): the fallback as F005's rule 3, retired vocabulary (T-32); HRV-59 states it"),
    ("HRV-01-R13-four-tier-hierarchy", R02, "a genuinely different and higher tier than HRV Status",
     "research/02:161 (R13): HRV Status as a lower tier; it is quarantined and never a tier (HRV-01, T-05)"),
    ("C18-no-tier-from-resolver", OPENAPI, "no_band (a tier resolved, but its baseline holds fewer than two",
     "openapi no_band (C18): 'a tier resolved', retired resolver vocabulary (T-32, HRV-44)"),
    ("C18-no-tier-from-resolver", SCHEMAS,
     "resolved, but its baseline holds fewer than two readings, so no band exists",
     "schemas no_band (C18): 'a tier resolved', retired resolver vocabulary (T-32, HRV-44)"),
    ("C18-no-tier-from-resolver", SCHEMAS, "stays sustained by a tier other than the resolved one",
     ("schemas reset_reason (C18): the lifetime against 'the resolved one'; clause (b) compares the reported "
      "dataset's own tier (HRV-38, T-32)")),
    ("C10-recency-only-rule-that-acts", OPENAPI, "judgeable one was skipped as stale",
     "openapi selected_dataset (C10): 'skipped as stale'; the recency gate skips, stale is only the enum value (T-04)"),
    ("C10-recency-only-rule-that-acts", SCHEMAS, "judgeable one was skipped as stale",
     "schemas selected_dataset (C10): 'skipped as stale' (T-04)"),
    ("C10-recency-only-rule-that-acts", OPENAPI,
     "the recency gate skipped it as stale, so `baseline`/`band` come from a",
     "openapi selected_reason (C10): 'skipped it as stale' (T-04)"),
    ("C10-recency-only-rule-that-acts", SCHEMAS,
     "skipped it as stale, so `baseline`/`band` come from a lower-fidelity instrument",
     "schemas selected_reason (C10): 'skipped it as stale' (T-04)"),
    ("HRV-40-R13-now-sustaining-tier", OPENAPI,
     "not any one of them alone: the presented dataset's tier still holds min_baseline_readings",
     ("openapi reset_reason (R13): liveness on the presented dataset's tier; the report is the reported "
      "dataset's, selected or else presented (T-02, HRV-38 clause (a))")),
    ("HRV-40-R13-now-sustaining-tier", SCHEMAS,
     "presented dataset's tier still holds min_baseline_readings distinct days",
     "schemas reset_reason (R13): as the openapi copy (T-02, HRV-38 clause (a))"),
    ("HRV-40-R13-now-sustaining-tier", SCHEMAS, "was also not in use in the judged week [date-6, date]",
     ("schemas reset_on (R13): reported only when the other tier is not in use in the judged week; the "
      "week half is fewer than 3 stray days (HRV-78)")),
    ("C19-hrv-04-reduced-confidence", S02, "**Confidence and the anti-mixing rule.**",
     "spec/02:212 (C19): the heading names confidence; fidelity is the only quality term Section 3 applies (T-21)"),
    ("T07-ctl-rise-band", S06, "Ramp-rate CTL-rise band — fixed and provisionally ratified",
     "spec/06:277 (T-07): the CTL-rise interval is a range (REG-19); band is the HRV SWC band's"),
    ("T07-ctl-rise-band", S06, "now ships a concrete weekly-CTL-rise band",
     "spec/06:277 (T-07): as above, the item's second naming"),
    ("T07-ctl-rise-band", S06, "working band ~+3–7",
     "spec/06:277 (T-07): the +3-7 interval is REG-19's range"),
    ("T07-ctl-rise-band", DEVPLAN, "Ramp-rate CTL-rise band (Section 6, §6.2.2)",
     "spec_development_plan:83 (T-07): the CTL-rise range (REG-19)"),
)

#: ``(key, path, fragment, commit, reason)``: a site a site task rewrote and a later review found still
#: stating an old meaning. The builder runs on the pre-work tree, where that text does not exist yet, so
#: the fragment is located in the file as it stands at ``commit`` (the task's commit) and the row is
#: added after every other step. Source ``inventory``, as for ``MANUAL_ROWS``.
LATER_ROWS = (
    ("C03-return-is-free", S02,
     "a dataset the athlete established before is selected again once it is judgeable and not skipped by the recency gate",
     "7e00d9b",
     ("spec/02:212 (C03), T214's rewrite: re-selection once judgeable and not skipped, without HRV-68's "
      "'by the fidelity order of HRV-14 (selection)', so a returning lower-fidelity dataset reads as "
      "taking the verdict")),
    # F011 sprint-008 review, iteration 1: T210's and T208's rewrites cite the fallback as F005's rule 3,
    # retired vocabulary (T-32); HRV-24 and HRV-59 state it.
    ("C17-hrv24-read-on-last", OPENAPI, "(AC9, F005's rule 3 retained; research/00 HRV-24, HRV-59)", "c4c33d2",
     "openapi selected_dataset (C17), T210's rewrite: the fallback cited as F005's rule 3 (T-32)"),
    ("C17-hrv24-read-on-last", SCHEMAS, "the presentation fallback names (AC9, F005's rule 3 retained;", "c4c33d2",
     "schemas selected_dataset (C17), T210's rewrite: the fallback cited as F005's rule 3 (T-32)"),
    ("C17-hrv24-read-on-last", F006, "(F005's rule 3, retained; `research/00` HRV-24, HRV-59)", "05e6605",
     "F006 AC9 (C17), T208's rewrite: the fallback cited as F005's rule 3 (T-32)"),
)


# --------------------------------------------------------------------------------------------------
# The inventory's "Code:" and "Code/tests:" lines.
# --------------------------------------------------------------------------------------------------

#: The inventory's short file names, as its citation conventions define them.
INVENTORY_PATHS = {
    "hrv_trend.py": HRV_TREND,
    "main.py": MAIN,
    "test_hrv_no_regression_gate.py": TEST_COMMENT_ROW,
}

_ITEM = re.compile(r"^\*\*(C\d+)\. ")
_CODE_LINE = re.compile(r"^- Code(?:/tests)?: ")
#: A backticked citation: ``file.py:<lines>`` or a bare ``:<lines>`` (the last-named file's).
_CITATION = re.compile(r"`(?:([\w./-]+\.(?:py|md|ya?ml)):|:)(\d[\d, -]*)`")


def _ranges(lines: list[int]) -> str:
    """``[1, 2, 3, 7]`` as ``1-3,7``."""
    parts, start = [], None
    for i, n in enumerate(lines):
        start = n if start is None else start
        if i + 1 == len(lines) or lines[i + 1] != n + 1:
            parts.append(f"{start}-{n}" if n != start else f"{n}")
            start = None
    return ",".join(parts)


def inventory_citations(inventory: Path = INVENTORY) -> list[tuple[str, str, list[int], str]]:
    """``(C-number, short file name, cited line numbers, the Code line)`` for each file citation on a
    "Code:"/"Code/tests:" line of the inventory's Section 2. A bare ``:n`` citation belongs to the file
    named before it on the line."""
    citations = []
    item = None
    for line in inventory.read_text(encoding="utf-8").splitlines():
        head = _ITEM.match(line)
        if head:
            item = head.group(1)
        if item is None or not _CODE_LINE.match(line):
            continue
        current = None
        for name, numbers in _CITATION.findall(line):
            if name:
                current = name
            if current is None or not numbers:
                continue
            lines = []
            for part in numbers.split(","):
                part = part.strip()
                if not part:
                    continue
                a, _, b = part.partition("-")
                lines += list(range(int(a), int(b or a) + 1))
            citations.append((item, current, lines, line))
    return citations


def keys_of_item(item: str) -> list[str]:
    """The ``OLD_MEANINGS`` keys an inventory C-item gave rise to: named for it, or decided by it."""
    return [k for k, m in OLD_MEANINGS.items()
            if re.search(rf"(?:^|-){item}(?:-|$)", k) or re.match(rf"{item}\b", m.decision)]


def _git_text(path: str, commit: str) -> str:
    """``path`` as it stands at ``commit``. Run in a ``git archive`` of the pre-work tree, set
    ``GIT_DIR`` to the checkout's ``.git``."""
    shown = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=REPO_ROOT, capture_output=True,
                           text=True, encoding="utf-8", check=True)
    return shown.stdout


def _git_lines(path: str, commit: str = INVENTORY_COMMIT) -> list[str]:
    return _git_text(path, commit).splitlines()


# --------------------------------------------------------------------------------------------------
# Building.
# --------------------------------------------------------------------------------------------------


class File:
    """One file's text as the gate reads it (``GATE.gate_source``: a ``.py``, ``.yaml`` or ``.yml``
    file's comment markers and a ``.md`` file's blockquote markers removed, T219 and the sprint-008 F011
    review) and its normalized form, with the map between them. ``raw`` keeps every newline, so
    line numbers are the file's, and an excerpt drawn from it matches the gate's text."""

    def __init__(self, rel: str, repo_root: Path = REPO_ROOT, text: str | None = None):
        self.rel = rel
        source = (repo_root / rel).read_text(encoding="utf-8") if text is None else text
        self.raw, _to_raw = GATE.gate_source(rel, source)
        self.text, self.offsets = GATE.normalize_with_offsets(self.raw)

    def raw_span(self, start: int, end: int) -> tuple[int, int]:
        return self.offsets[start], self.offsets[end - 1] + 1

    def line_of(self, norm_start: int) -> int:
        return self.raw.count("\n", 0, self.offsets[norm_start]) + 1

    def excerpt(self, raw_start: int, raw_end: int) -> str:
        return " ".join(self.raw[raw_start:raw_end].split())

    def count(self, excerpt: str) -> int:
        return self.text.count(normalize(excerpt))

    def locate(self, fragment: str) -> tuple[int, int]:
        """The normalized span of ``fragment``'s one occurrence; raises unless there is exactly one."""
        needle = normalize(fragment)
        found = [m.start() for m in re.finditer(re.escape(needle), self.text)]
        if len(found) != 1:
            raise SystemExit(f"{self.rel}: fragment {fragment!r} occurs {len(found)} times")
        return found[0], found[0] + len(needle)

    def unique_excerpt(self, start: int, end: int) -> str:
        """The raw text of the hit at normalized ``[start, end)``, widened a word at a time on each side
        until its normalized form occurs once in the file."""
        a, b = self.raw_span(start, end)
        while True:
            excerpt = self.excerpt(a, b)
            if self.count(excerpt) == 1:
                return excerpt
            if a == 0 and b == len(self.raw):
                raise SystemExit(f"{self.rel}: no unique excerpt around {self.raw[a:b]!r}")
            a = max(0, self.raw.rfind(" ", 0, max(0, a - 1)) + 1) if a > 0 else 0
            nxt = self.raw.find(" ", b + 1)
            b = len(self.raw) if nxt == -1 else nxt


class Census:
    def __init__(self):
        self.rows: list[dict[str, str]] = []
        self.log: list[str] = []
        self.files: dict[str, File] = {}
        #: ``LATER_ROWS``' files at their commits, by ``(key, path, normalized excerpt)``.
        self.later: dict[tuple[str, str, str], File] = {}

    def file(self, rel: str) -> File:
        if rel not in self.files:
            self.files[rel] = File(rel)
        return self.files[rel]

    def add(self, key: str, path: str, excerpt: str, source: str) -> bool:
        row = {"key": key, "path": path, "excerpt": excerpt, "source": source}
        if any((r["key"], r["path"], normalize(r["excerpt"])) == (key, path, normalize(excerpt))
               for r in self.rows):
            return False
        self.rows.append(row)
        return True

    def spans(self, path: str) -> list[tuple[int, int, dict]]:
        """The normalized span of each row's excerpt in ``path``."""
        f = self.file(path)
        return [(*f.locate(r["excerpt"]), r) for r in self.rows if r["path"] == path]


def _family(key: str) -> str:
    """The decision a key rests on: its C-number, or its first decision token."""
    named = re.search(r"(?:^|-)(C\d+)(?:-|$)", key)
    return named.group(1) if named else OLD_MEANINGS[key].decision.split()[0]


def _matching(f: File, key: str, start: int, end: int, line: int):
    """The resolutions for one loose hit, best first: those whose fragment overlaps it, then those whose
    fragment lies on its line, then those with no fragment (every such hit in the file), each group in
    ``LOOSE_RESOLUTIONS`` order. The first is the verdict."""
    overlap, same_line, whole = [], [], []
    for i, entry in enumerate(LOOSE_RESOLUTIONS):
        _verdict, loose_key, rpath, fragment, _reason, _row_key = entry
        if loose_key not in ("*", key) or rpath not in ("*", f.rel):
            continue
        if fragment is None:
            whole.append((i, entry))
            continue
        needle = normalize(fragment)
        spans = [(m.start(), m.start() + len(needle)) for m in re.finditer(re.escape(needle), f.text)]
        if any(a < end and start < b for a, b in spans):
            overlap.append((i, entry))
        elif any(f.line_of(a) == line for a, _b in spans):
            same_line.append((i, entry))
    return overlap + same_line + whole


def build() -> Census:
    census = Census()

    # 1. grep: every unquoted hit of every key's current pattern.
    hits = [h for h in GATE.scan() if not h.quoted]
    for hit in hits:
        f = census.file(hit.path)
        census.add(hit.key, hit.path, f.unique_excerpt(hit.start, hit.end), "grep")
    quoted = [h for h in GATE.scan() if h.quoted]
    for hit in quoted:
        census.log.append(f"grep out: {hit.path}:{hit.line} {hit.key} {hit.matched!r} -- quotation (S3)")

    # 2. narrowed: every site T218's narrowing stopped matching.
    for hit in GATE.narrowed_extras():
        f = census.file(hit.path)
        census.add(hit.key, hit.path, f.unique_excerpt(hit.start, hit.end), "narrowed")

    # 3. manual: F011 AC2's named sites no pattern reaches.
    for key, path, fragment, reason in MANUAL_ROWS:
        f = census.file(path)
        start, end = f.locate(fragment)
        census.add(key, path, f.excerpt(*f.raw_span(start, end)), "inventory")
        census.log.append(f"manual in: {path}:{f.line_of(start)} {key} -- {reason}")

    # 4. inventory: the "Code:"/"Code/tests:" citations.
    for item, name, lines, _line in inventory_citations():
        where = f"{item} {name}:{_ranges(lines)}"
        path = INVENTORY_PATHS.get(name)
        if path is None:
            raise SystemExit(f"inventory cites {name}, which INVENTORY_PATHS does not map")
        if path.startswith("runcoach-api/tests/") and path != TEST_COMMENT_ROW:
            census.log.append(f"inventory out: {where} -- runcoach-api/tests/ is outside S2's roots")
            continue
        keys = keys_of_item(item)
        if not keys:
            census.log.append(f"inventory out: {where} -- {item} has no OLD_MEANINGS key")
            continue
        text_at = _git_lines(path)
        cited = [(n, text_at[n - 1]) for n in lines]
        found = [(k, n, raw) for n, raw in cited for k in keys if k in LOOSE_NOUNS
                 and re.search(LOOSE_NOUNS[k], normalize(raw))]
        if not found:
            census.log.append(f"inventory out: {where} -- the cited lines state no keyed phrase of "
                              f"{keys}: they are the code the item describes")
            continue
        for key, n, raw in found:
            stripped = re.sub(r"^\s*(?:#:?|\"\"\"|\*)?\s*", "", raw).strip()
            if path != TEST_COMMENT_ROW:
                census.log.append(f"inventory: {item} {name}:{n} {key} {stripped[:60]!r} -- an S2 root, so "
                                  f"the loose grep reaches it and resolves it below")
                continue
            # Outside the roots neither grep reaches the line, so the key's own pattern stands in for
            # the grep: S6 names the one comment that quotes the old meaning.
            if not re.search(OLD_MEANINGS[key].pattern, normalize(raw)):
                census.log.append(f"inventory out: {item} {name}:{n} {key} {stripped[:60]!r} -- outside the "
                                  f"roots and the key's pattern misses it (a loose noun only)")
                continue
            f = census.file(path)
            f.locate(stripped)
            census.add(key, path, " ".join(stripped.split()), "inventory")
            census.log.append(f"inventory in: {item} {name}:{n} {key} -- the one test-comment row (S6)")

    # 5. loose: each key's distinctive nouns.
    used = set()
    unresolved = []
    for key, path, start, end, line, is_quoted in loose_hits():
        where = f"{path}:{line} {key}"
        if is_quoted:
            census.log.append(f"loose out: {where} -- quotation (S3)")
            continue
        covered = [r for a, b, r in census.spans(path) if a < end and start < b
                   and _family(r["key"]) == _family(key)]
        if covered:
            census.log.append(f"loose out: {where} -- a {covered[0]['source']} row covers it")
            continue
        f = census.file(path)
        for i, (verdict, loose_key, rpath, fragment, reason, row_key) in _matching(f, key, start, end, line):
            used.add(i)
            if verdict == "out":
                census.log.append(f"loose out: {where} -- {reason}")
            else:
                a, b = f.locate(fragment)
                census.add(row_key, path, f.excerpt(*f.raw_span(a, b)), "loose")
                census.log.append(f"loose in: {where} -> {row_key} -- {reason}")
            break
        else:
            unresolved.append(f"{where} {f.raw[f.offsets[start]:f.offsets[end - 1] + 1]!r}")
    stale = [LOOSE_RESOLUTIONS[i][1:4] for i in range(len(LOOSE_RESOLUTIONS)) if i not in used]
    if unresolved or stale:
        raise SystemExit("unresolved loose hits:\n  " + "\n  ".join(unresolved)
                         + f"\nresolutions that matched no loose hit: {stale}")

    # 6. later: a site task's own rewrite a review found stating an old meaning, read at its commit.
    for key, path, fragment, commit, reason in LATER_ROWS:
        f = File(path, text=_git_text(path, commit))
        start, end = f.locate(fragment)
        excerpt = f.excerpt(*f.raw_span(start, end))
        census.add(key, path, excerpt, "inventory")
        census.later[(key, path, normalize(excerpt))] = f
        census.log.append(f"later in: {path}:{f.line_of(start)}@{commit} {key} -- {reason}")
    return census


def loose_hits(repo_root: Path = REPO_ROOT, nouns=None):
    """Each loose-noun match over the live files (S2), outside section records (S1), as
    ``(key, path, norm_start, norm_end, line, quoted)``; ``KEY_ROOTS`` applies."""
    nouns = LOOSE_NOUNS if nouns is None else nouns
    found = []
    for path in (p for paths in GATE.live_files(repo_root).values() for p in paths):
        raw = (repo_root / path).read_text(encoding="utf-8")
        text, offsets = GATE.gate_text(path, raw)
        records = GATE.record_ranges(path, raw)
        spans = GATE.quote_spans(raw) if path.endswith(".md") else None
        for key, regex in nouns.items():
            if not GATE.key_reaches(key, path):
                continue
            for match in re.finditer(regex, text):
                start, end = match.start(), match.end()
                if any(a <= offsets[start] < b for a, b in records):
                    continue
                quoted = spans is not None and GATE.hit_is_quoted(raw, start, end, offsets, spans)
                found.append((key, path, start, end, raw.count("\n", 0, offsets[start]) + 1, quoted))
    return found


def check(census: Census) -> list[str]:
    """Every row: a real key and source, a file inside S2's roots (or the test-comment row) and an
    excerpt whose normalized form occurs exactly once there."""
    live = {p for paths in GATE.live_files().values() for p in paths}
    errors = []
    for row in census.rows:
        where = f"{row['path']} {row['key']} {row['excerpt'][:50]!r}"
        if row["key"] not in OLD_MEANINGS:
            errors.append(f"{where}: not an OLD_MEANINGS key")
        if row["source"] not in SOURCES:
            errors.append(f"{where}: source {row['source']!r}")
        if row["path"] not in live and row["path"] != TEST_COMMENT_ROW:
            errors.append(f"{where}: outside S2's roots")
        else:
            f = census.later.get((row["key"], row["path"], normalize(row["excerpt"]))) or census.file(row["path"])
            if f.count(row["excerpt"]) != 1:
                errors.append(f"{where}: excerpt occurs {f.count(row['excerpt'])} times")
        if "\n" in row["excerpt"] or "\r" in row["excerpt"]:
            errors.append(f"{where}: excerpt spans lines")
    return errors


def append_pending(rows) -> list[str]:
    """S15 for the census: each ``inventory`` or ``loose`` row whose ``(path, key)`` no pending file
    covers yet is appended, as ``path,<full OLD_MEANINGS key>``, to its owner's
    ``research00_pending/<id>.csv`` (T199's ``OWNERSHIP``), creating the file if needed. Otherwise the
    row would be red on main rather than pending. Append only: no existing row moves."""
    pending = GATE.read_pending()
    appended = []
    for row in rows:
        if row["source"] not in ("inventory", "loose"):
            continue
        path, key = row["path"], row["key"]
        if any((path, key) in covered or (path, "*") in covered for covered in pending.values()):
            continue
        owners = GATE.owners_of(path, key)
        if len(owners) != 1:
            raise SystemExit(f"{path},{key}: OWNERSHIP names {sorted(owners) or 'no owner'}, not one")
        owner = owners.pop()
        target = GATE.PENDING_DIR / f"{owner}.csv"
        if not target.exists():
            target.write_text("path,key\n", encoding="utf-8", newline="")
        with target.open("a", encoding="utf-8", newline="") as handle:
            handle.write(f"{path},{key}\n")
        pending.setdefault(owner, []).append((path, key))
        appended.append(f"{owner}.csv: {path},{key}")
    return appended


def main() -> int:
    if not INVENTORY.is_file():
        print(f"no inventory at {INVENTORY}: run from the main checkout", file=sys.stderr)
        return 1
    census = build()
    for line in census.log:
        print(line)
    errors = check(census)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    order = {s: i for i, s in enumerate(SOURCES)}
    rows = sorted(census.rows, key=lambda r: (order[r["source"]], r["path"], r["key"]))
    with CENSUS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    counts = {s: sum(r["source"] == s for r in rows) for s in SOURCES}
    print(f"wrote {len(rows)} rows to {CENSUS_PATH.relative_to(REPO_ROOT).as_posix()}: {counts}")
    for line in append_pending(rows):
        print(f"pending appended: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
