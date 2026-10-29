"""The learnings rules on fixture and release evidence exist and keep their shape (F014 AC7).

``.claude/rules/learnings/`` is loaded into every agent session through its ``paths:`` frontmatter. A
rule file without that frontmatter is never loaded, and one without its ``Origin:`` line loses the IDEA
that records why it exists. Each file is also pinned to the phrases that carry its rule:

- ``cite-only-real-activity-fixtures-as-proof.md`` (IDEA-128) reads the "run proof" column of the
  fixture provenance table, ``runcoach-api/tests/fixtures/README.md``, and keeps its two clauses:
  captures and snapshots are cited for what they are, and a file's name is not its provenance;
- ``name-the-recording-mode-population.md`` (IDEA-127) names the three recording-mode populations a
  feature reading per-record time, distance, speed or altitude must place in its Negative Class
  (1 Hz with pauses, moving dropouts over 5 s, smart recording), and asks for a probe or a dated
  deferral with an owner for each;
- ``changelog-claims-are-tried-on-real-activities.md`` (IDEA-174) asks the release demo to list every
  CHANGELOG sentence that tells the athlete to act or says what a rule includes or excludes, and to
  name the athlete's actual activities each one would change, in a ``## CHANGELOG claims`` section;
- ``re-measure-data-premises-at-planning.md`` (IDEA-179) asks sprint planning to list every spec
  sentence that states a bound or count on the athlete's data, re-measure it against the full census
  or a store copy, and record the query, its printed result and held or false, citing the 240 s rep
  premise as its case;
- ``workout-rulings-show-step-targets.md`` (IDEA-178) asks a question on whether a workout, step or
  rep counts toward an estimate to show, per step, the intensity, the Garmin target and the pace run
  in one table before the question, citing the 2026-10-18 and 2026-10-20 Progression Run rulings;
- ``platform-touching-fixes-pass-on-linux.md`` (IDEA-182) asks a change touching dates and times,
  paths, line endings, shells, encodings or ``os`` / ``platform`` calls to name its tests and pass
  them on Linux through ``run_on_linux.sh``, the Linux run's summary line as the evidence;
- ``release-demo-checks-the-real-config.md`` (IDEA-183) asks the release gate to load the athlete's
  real ``api.toml`` and ``cli.toml`` with ``check_real_config.py`` and to show a failure to the
  athlete before approval;
- ``rulings-on-a-computed-number-show-every-rule-that-reads-it.md`` (IDEA-205) asks a question that
  has the athlete rule on how a number is computed to carry a worked table in the question itself: a
  few input sets, and for each the changed number beside every rule that reads it, citing the three
  sprint-022 rulings on b that were judged on different days;
- ``release-demo-questions-are-asked-before-review.md`` (IDEA-206) asks each item in the release
  demo summary that only the athlete can answer to be put to them with AskUserQuestion when execute
  ends, in plain words with its effect on the served number, and the answer recorded before review
  starts, citing the 2026-06-06 half that waited for the review critic.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEARNINGS = _REPO_ROOT / ".claude" / "rules" / "learnings"

#: (file name, IDEA id in its Origin line, phrases its body must carry)
RULES = (
    (
        "cite-only-real-activity-fixtures-as-proof.md",
        "IDEA-128",
        (
            "run proof",
            "runcoach-api/tests/fixtures/README.md",
            "walk only",
            "Captures and snapshots are cited for what they are",
            "A file's name is not its provenance",
        ),
    ),
    (
        "name-the-recording-mode-population.md",
        "IDEA-127",
        (
            "recording-mode",
            "1 Hz with pauses",
            "moving dropouts over 5 s",
            "smart recording: variable gaps",
            "not observed on runs in the corpus (see the provenance table)",
            "a dated deferral with an owner",
        ),
    ),
    (
        "changelog-claims-are-tried-on-real-activities.md",
        "IDEA-174",
        (
            "CHANGELOG claims",
            "the athlete's actual activities",
        ),
    ),
    (
        "re-measure-data-premises-at-planning.md",
        "IDEA-179",
        ("re-measure", "the full census", "held or false", "240 s"),
    ),
    (
        "workout-rulings-show-step-targets.md",
        "IDEA-178",
        ("Garmin target", "pace run", "one table", "2026-10-18", "2026-10-20"),
    ),
    (
        "platform-touching-fixes-pass-on-linux.md",
        "IDEA-182",
        ("run_on_linux.sh", "summary line", "dates and times", "line endings"),
    ),
    (
        "release-demo-checks-the-real-config.md",
        "IDEA-183",
        ("check_real_config.py", "api.toml", "cli.toml", "before approval"),
    ),
    (
        "rulings-on-a-computed-number-show-every-rule-that-reads-it.md",
        "IDEA-205",
        ("worked table", "every rule that reads it", "in the question itself", "2026-10-24"),
    ),
    (
        "release-demo-questions-are-asked-before-review.md",
        "IDEA-206",
        ("AskUserQuestion", "when execute ends", "before review starts", "2026-06-06"),
    ),
)

_FRONTMATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.DOTALL)


def _flat(text: str) -> str:
    """Collapse whitespace so a phrase wrapped across lines is still found, and drop the ``**``
    bold markers so a phrase is found whether or not the rule emphasises it."""
    return " ".join(text.split()).replace("**", "")


@pytest.mark.parametrize(("name", "idea", "phrases"), RULES, ids=[r[0] for r in RULES])
def test_learnings_rule_exists_with_frontmatter_origin_and_phrase(name, idea, phrases):
    path = _LEARNINGS / name
    assert path.is_file(), f"missing learnings rule: {path}"
    text = path.read_text(encoding="utf-8")

    front = _FRONTMATTER.match(text)
    assert front, f"{name}: no frontmatter block at the top of the file"
    assert re.search(r"^paths:\s*\[", front.group("body"), re.MULTILINE), (
        f"{name}: frontmatter has no paths: list, so the rule is never loaded"
    )

    origin_lines = [line for line in text.splitlines() if "Origin:" in line]
    assert origin_lines, f"{name}: no Origin: line"
    assert any(f"spec/ideas/{idea}-" in line for line in origin_lines), (
        f"{name}: the Origin: line does not name {idea}: {origin_lines}"
    )

    flat = _flat(text)
    for phrase in phrases:
        assert phrase in flat, f"{name}: missing the phrase {phrase!r}"
