"""Tests for the ``runcoach profile show`` and ``runcoach profile set`` commands (F016 AC3, AC7).

Only the CLI's own responsibilities are covered: which request each command sends, and how it renders
the API's ``GET /me`` shape. Server validation (the 422 cases) is the API's; here a 422 is a mocked
response the CLI must report and exit 1 on.

**The mocked body is copied from the API's shape pin**, ``runcoach-api/tests/test_me_route.py``:
the per-field entry keys and the all-null ``missing`` entry are its ``ENTRY_KEYS`` / ``MISSING_ENTRY``
and ``MISSING_ANCHOR``; the shadowed ``max_hr_bpm`` entry (188, ``fit``, entered 195) is
``test_get_me_shows_a_shadowed_entry``; the two ``order_conflict`` anchors (resting 190 above max 188,
threshold 169 served at version 1, ``sex`` missing) are
``test_get_me_serves_an_order_conflict_anchor_with_its_reason``; the settings keys and default units are
``test_get_me_on_an_empty_database_reads_every_field_missing``. One body combines those pins (the
order conflict's max HR anchor carries the shadowed entry's 195), with the keys and nulls as the pins
serve them. When that pin changes, copy the change here.

**The mocked body is checked against the contract.** ``test_the_mocked_body_matches_the_contract``
reads ``contracts/openapi.yaml``'s ``Athlete`` and every component it refers to, and checks
``ME_BODY``'s keys, types, nulls and enum values against them, so a contract change to the ``/me``
shape goes red here rather than leaving a silent render gap. The CLI has no YAML dependency, so the
reader is a small line reader for the contract's component layout; it fails on a line it cannot read
rather than skipping it.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli.main import app

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("prewritten_config")

SESSION_ID = "0b6f3c2e-5d1a-4f7e-9c8b-2a1d3e4f5a6b"
START_TIME = "2026-09-20T07:15:03+00:00"
SET_AT = "2026-10-06T18:02:11+00:00"

# test_me_route.py MISSING_ENTRY / MISSING_ANCHOR.
MISSING_ENTRY = {
    "value": None,
    "unavailable": "missing",
    "source": None,
    "session_id": None,
    "recorded_at": None,
    "entered_value": None,
}
MISSING_ANCHOR = {**MISSING_ENTRY, "version": None}


def _entered(value):
    return {
        "value": value,
        "unavailable": None,
        "source": "entered",
        "session_id": None,
        "recorded_at": SET_AT,
        "entered_value": value,
    }


# test_get_me_shows_a_shadowed_entry: the file's 188 is effective, the entered 195 is shadowed.
SHADOWED_MAX = {
    "value": 188,
    "unavailable": None,
    "source": "fit",
    "session_id": SESSION_ID,
    "recorded_at": START_TIME,
    "entered_value": 195,
}

ME_BODY = {
    "id": "athlete-1",
    "display_name": None,
    "units": {"distance": "km", "pace": "min_per_km", "temperature": "c"},
    "created_at": "2026-10-01T09:00:00+00:00",
    "sex": copy.deepcopy(MISSING_ENTRY),
    "birth_date": _entered("1980-05-01"),
    "body_mass_kg": copy.deepcopy(MISSING_ENTRY),
    "height_cm": copy.deepcopy(MISSING_ENTRY),
    "resting_hr_bpm": _entered(190),
    "max_hr_bpm": SHADOWED_MAX,
    "threshold_hr_bpm": _entered(169),
    "anchors": {
        # test_get_me_serves_an_order_conflict_anchor_with_its_reason.
        "resting_hr_bpm": {**MISSING_ANCHOR, "unavailable": "order_conflict", "entered_value": 190},
        "max_hr_bpm": {**MISSING_ANCHOR, "unavailable": "order_conflict", "entered_value": 195},
        "threshold_hr_bpm": {**_entered(169), "version": 1},
        "sex": copy.deepcopy(MISSING_ANCHOR),
    },
}


# ---------------------------------------------------------------------------
# the mocked body against the contract
# ---------------------------------------------------------------------------

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
_REF = re.compile(r'\$ref: "#/components/schemas/(\w+)"')
_TYPE = re.compile(r"\btype: (\w+)")
_NULLABLE = re.compile(r"\bnullable: (true|false)\b")
_ENUM = re.compile(r"\benum: \[([^\]]*)\]")
_DESCRIPTION = re.compile(r'description: "(?:[^"\\]|\\.)*"|description: >.*$')
_PY_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
}


def _component_block(lines: list[str], name: str) -> list[str]:
    """The lines of ``components.schemas.<name>``: from its 4-space header to the next one."""
    header = f"    {name}:"
    starts = [i for i, ln in enumerate(lines) if ln.rstrip() == header]
    assert len(starts) == 1, f"{name}: {len(starts)} headers in the contract"
    block = []
    for ln in lines[starts[0] + 1 :]:
        if ln.strip() and len(ln) - len(ln.lstrip(" ")) <= 4:
            break
        block.append(ln)
    return block


def _read_property(text: str, where: str) -> dict:
    text = _DESCRIPTION.sub("", text)
    spec: dict = {"nullable": False}
    if m := _REF.search(text):
        spec["ref"] = m.group(1)
    if m := _TYPE.search(text):
        spec["type"] = m.group(1)
    if m := _NULLABLE.search(text):
        spec["nullable"] = m.group(1) == "true"
    if m := _ENUM.search(text):
        spec["enum"] = [v.strip() for v in m.group(1).split(",")]
    assert "ref" in spec or spec.get("type") in _PY_TYPES, f"{where}: cannot read {text.strip()!r}"
    return spec


def _read_component(lines: list[str], name: str) -> tuple[set[str], dict[str, dict]]:
    """``(required, {property: {type, nullable, enum, ref}})`` for one object component."""
    block = _component_block(lines, name)
    required: set[str] = set()
    props: dict[str, dict] = {}
    in_props = False
    current: tuple[str, list[str]] | None = None
    for ln in block:
        indent = len(ln) - len(ln.lstrip(" "))
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if indent == 6:
            in_props = stripped == "properties:"
            if stripped.startswith("required: ["):
                required = {v.strip() for v in stripped[len("required: [") : -1].split(",")}
            continue
        if not in_props or indent < 8:
            continue
        if indent == 8:
            key, _, rest = stripped.partition(":")
            if rest.strip():
                props[key] = _read_property(rest, f"{name}.{key}")
                current = None
            else:
                current = (key, [])
                props[key] = {}
        elif indent == 10 and current is not None:
            current[1].append(stripped)
            props[current[0]] = _read_property(", ".join(current[1]), f"{name}.{current[0]}")
    assert props, f"{name}: no properties read"
    assert all(props.values()), f"{name}: a block property with no keys: {props}"
    return required, props


def _contract_problems(body: object, name: str, lines: list[str], where: str = "") -> list[str]:
    """Every way ``body`` departs from component ``name``: keys, nulls, types and enum values."""
    required, props = _read_component(lines, name)
    if not isinstance(body, dict):
        return [f"{where or name}: not an object"]
    problems = [f"{where}.{k}: not in {name}" for k in sorted(set(body) - set(props))]
    problems += [f"{where}.{k}: absent, {name} declares it" for k in sorted(set(props) - set(body))]
    problems += [f"{where}.{k}: required by {name}, absent" for k in sorted(required - set(body))]
    for key, spec in props.items():
        if key not in body:
            continue
        value, at = body[key], f"{where}.{key}"
        if value is None:
            if not spec["nullable"]:
                problems.append(f"{at}: null, {name} does not allow it")
        elif "ref" in spec:
            problems += _contract_problems(value, spec["ref"], lines, at)
        elif isinstance(value, bool) and spec["type"] != "boolean":
            problems.append(f"{at}: a boolean, {name} says {spec['type']}")
        elif not isinstance(value, _PY_TYPES[spec["type"]]):
            problems.append(f"{at}: {type(value).__name__}, {name} says {spec['type']}")
        elif "enum" in spec and value not in spec["enum"]:
            problems.append(f"{at}: {value!r} not in {spec['enum']}")
    return problems


def _contract_lines() -> list[str]:
    return CONTRACT.read_text(encoding="utf-8").splitlines()


def test_the_mocked_body_matches_the_contract() -> None:
    problems = _contract_problems(ME_BODY, "Athlete", _contract_lines())
    print(problems)
    assert problems == []


def _with(path: tuple[str, ...], value=None, *, drop: bool = False, add: str | None = None) -> dict:
    body = copy.deepcopy(ME_BODY)
    target = body
    for key in path[:-1]:
        target = target[key]
    if drop:
        del target[path[-1]]
    elif add is not None:
        target[path[-1]][add] = None
    else:
        target[path[-1]] = value
    return body


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (_with(("anchors", "sex", "unavailable"), "bogus"), ".anchors.sex.unavailable: 'bogus' not in"),
        (_with(("anchors", "threshold_hr_bpm", "version"), "1"), ".anchors.threshold_hr_bpm.version: str"),
        (_with(("units", "distance"), None), ".units.distance: null"),
        (_with(("resting_hr_bpm", "value"), True), ".resting_hr_bpm.value: a boolean"),
        (_with(("birth_date", "source"), drop=True), ".birth_date.source: absent"),
        (_with(("height_cm",), add="unit"), ".height_cm.unit: not in ProfileNumberEntry"),
    ],
    ids=["enum", "type", "null", "bool-as-int", "missing-key", "extra-key"],
)
def test_the_contract_check_reports_a_departure(body, expected) -> None:
    """The check itself can fail: each departure from the contract is named."""
    problems = _contract_problems(body, "Athlete", _contract_lines())
    print(problems)
    assert any(p.startswith(expected) for p in problems), problems


def _line(output: str, prefix: str) -> str:
    """The one output line that starts with ``prefix`` (after indentation)."""
    hits = [ln for ln in output.splitlines() if ln.strip().startswith(prefix)]
    assert len(hits) == 1, (prefix, output)
    return hits[0]


def _section(output: str, heading: str) -> str:
    lines = output.splitlines()
    start = lines.index(heading)
    return "\n".join(lines[start + 1 :])


def _profile_part(output: str) -> str:
    lines = output.splitlines()
    return "\n".join(lines[: lines.index("Anchors:")])


class _Recorder:
    """A MockTransport handler that records every request and answers with one response."""

    def __init__(self, status: int = 200, body=None, text: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = ME_BODY if body is None else body
        self.text = text

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.text is not None:
            return httpx.Response(self.status, text=self.text)
        return httpx.Response(self.status, json=self.body)

    def sent_json(self):
        assert len(self.requests) == 1, self.requests
        return json.loads(self.requests[0].content)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_profile_show_prints_a_shadowed_entry(install_mock_client) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(app, ["profile", "show"])

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in rec.requests] == [("GET", "/me")]
    line = _line(_profile_part(result.stdout), "max_hr_bpm:")
    print(f"  {line}")
    assert "188" in line and "source fit" in line and SESSION_ID in line, line
    assert "shadows entered 195" in line, line


def test_profile_show_prints_an_entered_value_without_a_shadow(install_mock_client) -> None:
    install_mock_client(_Recorder())

    result = runner.invoke(app, ["profile", "show"])

    line = _line(_profile_part(result.stdout), "birth_date:")
    assert "1980-05-01" in line and "source entered" in line, line
    assert "shadows" not in line, line
    assert "unavailable (missing)" in _line(_profile_part(result.stdout), "sex:")


def test_profile_show_names_order_conflict_and_missing_anchors(install_mock_client) -> None:
    install_mock_client(_Recorder())

    result = runner.invoke(app, ["profile", "show"])

    assert result.exit_code == 0, result.output
    anchors = _section(result.stdout, "Anchors:")
    print(anchors)
    assert "unavailable (order_conflict)" in _line(anchors, "resting_hr_bpm:")
    assert "unavailable (order_conflict)" in _line(anchors, "max_hr_bpm:")
    assert "unavailable (missing)" in _line(anchors, "sex:")
    served = _line(anchors, "threshold_hr_bpm:")
    assert "169" in served and "version 1" in served and "unavailable" not in served, served


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


def test_profile_set_sends_exactly_the_given_fields_with_hr_as_integers(install_mock_client) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(
        app, ["profile", "set", "--max-hr", "188", "--birth-date", "1980-05-01", "--body-mass-kg", "70.5"]
    )

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in rec.requests] == [("PATCH", "/me")]
    sent = rec.sent_json()
    print(f"  sent={sent}")
    assert sent == {"max_hr_bpm": 188, "birth_date": "1980-05-01", "body_mass_kg": 70.5}
    assert type(sent["max_hr_bpm"]) is int
    # The returned profile is rendered, as show renders it.
    assert "shadows entered 195" in _line(_profile_part(result.stdout), "max_hr_bpm:")


def test_profile_set_sends_every_field_under_its_api_name(install_mock_client) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(
        app,
        [
            "profile", "set",
            "--sex", "unspecified",
            "--birth-date", "1980-05-01",
            "--body-mass-kg", "70.5",
            "--height-cm", "178",
            "--resting-hr", "47",
            "--max-hr", "188",
            "--threshold-hr", "169",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    sent = rec.sent_json()
    assert sent == {
        "sex": "unspecified",
        "birth_date": "1980-05-01",
        "body_mass_kg": 70.5,
        "height_cm": 178.0,
        "resting_hr_bpm": 47,
        "max_hr_bpm": 188,
        "threshold_hr_bpm": 169,
    }
    for field in ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm"):
        assert type(sent[field]) is int, field


def test_profile_set_refuses_a_fractional_hr_without_a_request(install_mock_client) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(app, ["profile", "set", "--max-hr", "188.5"])

    assert result.exit_code == 2, result.output
    assert "'188.5' is not a valid int" in result.stderr, result.stderr
    assert rec.requests == []


@pytest.mark.parametrize("option", ["--body-mass-kg", "--height-cm"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
def test_profile_set_refuses_a_non_finite_measure_without_a_request(
    install_mock_client, option, value
) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(app, ["profile", "set", option, value])

    print(f"  exit={result.exit_code} stderr={result.stderr!a}")
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 2, result.output
    assert "Error" in result.stderr and "finite" in result.stderr, result.stderr
    assert option in result.stderr, result.stderr
    assert rec.requests == []


def test_profile_set_clear_sends_null(install_mock_client) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(app, ["profile", "set", "--clear", "max_hr_bpm"])

    assert result.exit_code == 0, result.output
    sent = rec.sent_json()
    print(f"  sent={sent}")
    assert sent == {"max_hr_bpm": None}


def test_profile_set_clear_combines_with_a_value_for_another_field(install_mock_client) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(
        app, ["profile", "set", "--clear", "sex", "--clear", "height_cm", "--resting-hr", "47"]
    )

    assert result.exit_code == 0, result.output
    assert rec.sent_json() == {"sex": None, "height_cm": None, "resting_hr_bpm": 47}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--clear", "weight_kg"], "'weight_kg' is not a profile field"),
        (["--clear", "max_hr_bpm", "--max-hr", "188"], "'max_hr_bpm' is both set and cleared"),
        ([], "give at least one field to set or --clear"),
    ],
    ids=["unknown-field", "set-and-clear-one-field", "nothing-given"],
)
def test_profile_set_refuses_a_bad_request_without_sending_it(install_mock_client, args, message) -> None:
    rec = _Recorder()
    install_mock_client(rec)

    result = runner.invoke(app, ["profile", "set", *args])

    assert result.exit_code == 2, result.output
    assert message in " ".join(result.stderr.split()), result.stderr
    assert rec.requests == []


# ---------------------------------------------------------------------------
# failures: stderr and exit 1
# ---------------------------------------------------------------------------

COMMANDS = {"show": ["profile", "show"], "set": ["profile", "set", "--max-hr", "188"]}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_profile_unreachable_api_prints_to_stderr_and_exits_1(install_mock_client, command) -> None:
    def refusing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_mock_client(refusing_handler)

    result = runner.invoke(app, COMMANDS[command])

    assert result.exit_code == 1
    assert "could not reach API at http://example.test" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_profile_non2xx_prints_to_stderr_and_exits_1(install_mock_client, command) -> None:
    install_mock_client(_Recorder(status=500, text="anchor version log out of step"))

    result = runner.invoke(app, COMMANDS[command])

    assert result.exit_code == 1
    assert "API returned 500" in result.stderr
    assert "anchor version log out of step" in result.stderr
    assert result.stdout == ""


def test_profile_set_422_body_prints_to_stderr_and_exits_1(install_mock_client) -> None:
    detail = {
        "detail": [
            {"loc": ["body", "max_hr_bpm"], "msg": "must be a positive integer", "type": "value_error"}
        ]
    }
    install_mock_client(_Recorder(status=422, body=detail))

    result = runner.invoke(app, ["profile", "set", "--max-hr", "0"])

    assert result.exit_code == 1
    assert "API returned 422" in result.stderr
    assert "must be a positive integer" in result.stderr
    assert result.stdout == ""


def test_profile_show_an_unreadable_body_prints_to_stderr_and_exits_1(install_mock_client) -> None:
    install_mock_client(_Recorder(status=200, text="not json"))

    result = runner.invoke(app, ["profile", "show"])

    assert result.exit_code == 1
    assert "could not be understood" in result.stderr
    assert result.stdout == ""
