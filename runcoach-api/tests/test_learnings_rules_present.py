"""The two learnings rules on fixture evidence exist and keep their shape (F014 AC7).

``.claude/rules/learnings/`` is loaded into every agent session through its ``paths:`` frontmatter. A
rule file without that frontmatter is never loaded, and one without its ``Origin:`` line loses the IDEA
that records why it exists. Each file is also pinned to the phrase that carries its rule:

- ``cite-only-real-activity-fixtures-as-proof.md`` (IDEA-128) reads the "run proof" column of the
  fixture provenance table, ``runcoach-api/tests/fixtures/README.md``;
- ``name-the-recording-mode-population.md`` (IDEA-127) names the three recording-mode populations a
  feature reading per-record time, distance, speed or altitude must place in its Negative Class.
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
        ("run proof", "runcoach-api/tests/fixtures/README.md", "walk only"),
    ),
    (
        "name-the-recording-mode-population.md",
        "IDEA-127",
        (
            "recording-mode",
            "1 Hz with pauses",
            "moving dropouts over 5 s",
            "not observed on runs in the corpus (see the provenance table)",
        ),
    ),
)

_FRONTMATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.DOTALL)


def _flat(text: str) -> str:
    """Collapse whitespace so a phrase wrapped across lines is still found."""
    return " ".join(text.split())


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
