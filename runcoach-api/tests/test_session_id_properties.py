"""T050: property-based tests for ``mapping.derive_session_id`` (spec §2.2.1,
F003 Configuration).

The third target IDEA-005 (sprint-002 retro) names, and the one with
the sharpest invariant. ``derive_session_id`` hashes
``(source_device, start_time)`` into the opaque, stable ``session_id``
that spec §2.2.1 requires and that the ``UNIQUE (source_device,
start_time)`` constraint on ``sessions`` dedups against. It carries a
separator byte for one reason only: to stop ``("ab", "c")`` and
``("a", "bc")`` -- two genuinely different activities -- from hashing
to the same id.

**Why one hand-picked pair is not enough.** The determinism this file
guards was won in T032 (commit ``8f7e488``), which fixed the
local-first rebuild path: before it, every ``session_id`` changed when
the database was rebuilt from the FIT corpus, orphaning the decision
log (spec/09) that references them. T032 also left behind exactly one
collision test --
``test_session_id_determinism.py::test_derivation_does_not_conflate_device_and_time_boundary``,
the single pair ``("ab", "c")`` vs ``("a", "bc")``. The separator's
purpose is a *class* of collisions (every way of splitting one string
at two different points), and a single instance of a class is thin
proof for it: a separator dropped from the join still passes that one
assertion if the test author reruns it against a device string that
happens to end where the timestamp begins... it does not, in fact --
but it passes for any *other* single pair one might have picked, and
nothing here says which pair was picked deliberately. Generated splits
close that.

**What is pinned as a literal, and why.** Two design constants are
restated below rather than imported from the module under test:
``_SESSION_ID_HEX_LENGTH`` (32, written as ``_ID_LENGTH``) and
``_SESSION_ID_FIELD_SEPARATOR`` (the NUL byte, written as
``_SEPARATOR``). Importing them would make these tests agree with
whatever the module currently says -- a digest truncated to 8 hex
characters would still "pass" a shape test that asked the module how
long its own output is. Matching ``test_quality_gates_properties.py``
(T049) and ``test_rr_reconstruction_properties.py`` (T048), the
constants are pinned here as literals.

**The domain, and why the collision properties respect it.** The
module's own comment records the precondition that makes NUL a safe
separator: "NUL cannot occur in either an ISO-8601 timestamp or a
device string built from FIT profile values". Injectivity genuinely
*does* lapse outside that domain -- ``("a\\x00b", "c")`` and
``("a", "b\\x00c")`` join to the same bytes -- so the collision
properties below draw from a NUL-free alphabet, exactly the domain the
design claims. Determinism and shape, which carry no such
precondition, are asserted over the full unicode space *including* the
separator byte, per the task's first invariant. That split is the
design's own boundary, not a weakened generator: no bound anywhere
below exists to make a property easier to satisfy, and there is no
``assume()`` in this file.

``derandomize=True`` throughout, matching T048 and T049: all gating in
this repo is a manual ``uv run pytest``, and a property test that fails
only under some seeds on some machines is worse than no test at all.

These tests call ``derive_session_id`` directly -- a pure function,
never through the DB or ``TestClient``. The ``isolated_data_dir``
autouse fixture builds one database per *pytest test*, but hypothesis
runs a test body many times inside that one test, so a DB-touching
property would have every example share one database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from runcoach_api.ingestion.mapping import derive_session_id

# _SESSION_ID_HEX_LENGTH: the digest is truncated to this many hex
# characters. Pinned as a literal so a change to the truncation is a
# test failure rather than a silently-agreed-with edit.
_ID_LENGTH = 32

# The full lowercase hex alphabet. The id must be drawn from exactly
# this set -- uppercase would break nothing today but would make two
# recompute paths disagree the moment one of them normalised.
_HEX_DIGITS = frozenset("0123456789abcdef")

# _SESSION_ID_FIELD_SEPARATOR. The byte whose presence in the join is
# the entire collision guarantee, and whose absence from the input
# domain is the precondition that guarantee rests on.
_SEPARATOR = "\x00"

# Real values the two fields take, used as @example anchors: device
# strings as ``_build_source_device`` assembles them from FIT profile
# values, and ``start_time`` in the canonical ISO-8601 form stored on
# ``sessions.start_time``.
_REAL_DEVICE = "garmin-forerunner_965-3441234567"
_REAL_START_TIME = "2026-08-14T06:12:03+00:00"

# The unrestricted input space: any text at all, separator byte and
# lone surrogates aside (a surrogate cannot be UTF-8 encoded, so it is
# outside the function's ``str`` domain in the same way ``bytes`` is).
# Used for determinism and shape, which hold unconditionally.
_ANY_TEXT = st.text(
    alphabet=st.characters(exclude_categories=("Cs",)),
    min_size=0,
    max_size=64,
)

# The real input domain: the same space minus the separator byte, per
# the module's recorded precondition. Used for the collision
# properties, which are only claimed inside it.
_DOMAIN_TEXT = st.text(
    alphabet=st.characters(exclude_categories=("Cs",), exclude_characters=_SEPARATOR),
    min_size=0,
    max_size=64,
)

_SETTINGS = settings(
    derandomize=True,
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Property 1 -- determinism
# ---------------------------------------------------------------------------


@_SETTINGS
@given(device=_ANY_TEXT, start_time=_ANY_TEXT)
# The degenerate and boundary shapes, pinned so they run on every
# invocation rather than only when the search happens to reach them
# (there is no CI in this repo).
@example(device="", start_time="")
@example(device=_REAL_DEVICE, start_time=_REAL_START_TIME)
@example(device="", start_time=_REAL_START_TIME)
@example(device=_REAL_DEVICE, start_time="")
# Inputs carrying the separator byte itself -- the case the task names
# as the one a hand-written test is least likely to think of.
@example(device=_SEPARATOR, start_time=_SEPARATOR)
@example(device="ab" + _SEPARATOR + "cd", start_time=_SEPARATOR * 3)
@example(device=_SEPARATOR + _REAL_DEVICE, start_time=_REAL_START_TIME + _SEPARATOR)
# Non-ASCII: a device name is user-facing text and has arrived as such.
@example(device="Ωμέγα-λ", start_time="2026-08-14T06:12:03+00:00")
@example(device="é́", start_time="\U0001f3c3")
def test_derivation_is_deterministic(device: str, start_time: str) -> None:
    """The same tuple always derives the same id, for *any* pair of
    strings -- empty, unicode, or carrying the separator byte.

    The strings are rebuilt character-by-character before the second
    call rather than passed through again, so an id derived from
    anything but the characters themselves has a chance to diverge.
    """
    rebuilt_device = "".join(character for character in device)
    rebuilt_start_time = "".join(character for character in start_time)

    first = derive_session_id(device, start_time)
    second = derive_session_id(rebuilt_device, rebuilt_start_time)
    third = derive_session_id(device, start_time)

    assert first == second == third


def test_derivation_is_stable_across_processes() -> None:
    """The id is the same in a fresh interpreter under a different hash
    seed -- the property T032 (commit ``8f7e488``) actually bought.

    In-process repetition cannot see a derivation that mixed in
    anything process-scoped: Python randomises ``hash()`` for ``str``
    per process by default, so a ``hash()``-based id is perfectly
    stable within one run and different on every rebuild. That is
    precisely the failure T032 closed -- ``session_id`` changing when
    the database is rebuilt from the FIT corpus, orphaning the decision
    log (spec/09) that references it -- so it is worth a real
    subprocess rather than an assumption.
    """
    cases = [
        (_REAL_DEVICE, _REAL_START_TIME),
        ("", ""),
        ("ab", "c"),
        ("Ωμέγα-λ", "2026-08-14T06:12:03+00:00"),
    ]
    script = (
        "import json,sys;"
        "from runcoach_api.ingestion.mapping import derive_session_id;"
        "print(json.dumps([derive_session_id(d, t)"
        " for d, t in json.loads(sys.argv[1])]))"
    )
    payload = json.dumps(cases)

    expected = [derive_session_id(device, time) for device, time in cases]

    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, "-c", script, payload],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert json.loads(completed.stdout) == expected, (
            f"session ids differ under PYTHONHASHSEED={seed}: {completed.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Property 2 -- output shape
# ---------------------------------------------------------------------------


@_SETTINGS
@given(device=_ANY_TEXT, start_time=_ANY_TEXT)
@example(device="", start_time="")
@example(device=_REAL_DEVICE, start_time=_REAL_START_TIME)
@example(device=_SEPARATOR, start_time=_SEPARATOR)
@example(device="x" * 64, start_time="y" * 64)
@example(device="\U0001f3c3", start_time="é")
def test_id_is_always_thirty_two_lowercase_hex_characters(
    device: str, start_time: str
) -> None:
    """Shape is part of the contract: ``session_id`` is a fixed-width
    opaque token, and callers (URLs, the decision log, the CLI) render
    it verbatim.

    No specific digest value is asserted anywhere in this file -- that
    would pin the algorithm and turn any future change into a false
    failure. Width and alphabet are the parts that are promised.
    """
    session_id = derive_session_id(device, start_time)

    assert isinstance(session_id, str)
    assert len(session_id) == _ID_LENGTH
    assert set(session_id) <= _HEX_DIGITS
    assert session_id == session_id.lower()


# ---------------------------------------------------------------------------
# Property 3 -- the collision class the separator exists to stop
# ---------------------------------------------------------------------------


@st.composite
def _two_splits_of_one_string(draw) -> tuple[str, int, int]:
    """Draw one string and two *different* points at which to cut it.

    Both bounds are computed from the drawn string, so no example is
    ever rejected and no ``assume()`` is needed: the string is drawn at
    least one character long, which guarantees at least two distinct
    split points (0..len inclusive), and the second point is drawn from
    the range above the first.
    """
    joined = draw(
        st.text(
            alphabet=st.characters(
                exclude_categories=("Cs",), exclude_characters=_SEPARATOR
            ),
            min_size=1,
            max_size=48,
        )
    )
    first = draw(st.integers(min_value=0, max_value=len(joined) - 1))
    second = draw(st.integers(min_value=first + 1, max_value=len(joined)))
    return joined, first, second


@_SETTINGS
@given(split=_two_splits_of_one_string())
# The pair T032 hand-picked, now one member of the generated class.
@example(split=("abc", 1, 2))
@example(split=("ab", 0, 1))
@example(split=("ab", 1, 2))
# A real device/timestamp pair, cut either side of its true boundary.
@example(
    split=(
        _REAL_DEVICE + _REAL_START_TIME,
        len(_REAL_DEVICE) - 1,
        len(_REAL_DEVICE),
    )
)
@example(split=("aaaaaaaa", 3, 5))  # a run of one character: no cut is special
@example(split=("Ωμέγα2026", 2, 6))  # multi-byte characters either side of the cut
def test_two_splits_of_the_same_string_never_collide(
    split: tuple[str, int, int],
) -> None:
    """The whole class the separator exists to stop: any two ways of
    cutting a single string into ``(device, start_time)`` are different
    activities and must derive different ids, even though their naive
    concatenations are byte-identical.

    The premise is asserted, not assumed -- the two tuples really do
    concatenate to the same string, so a separator dropped from the
    join makes the two inputs literally identical and the property
    fails on every example rather than on a lucky one.
    """
    joined, first, second = split
    device_a, time_a = joined[:first], joined[first:]
    device_b, time_b = joined[:second], joined[second:]

    # The premise: naive concatenation *would* collide here.
    assert device_a + time_a == device_b + time_b == joined
    assert (device_a, time_a) != (device_b, time_b)

    assert derive_session_id(device_a, time_a) != derive_session_id(device_b, time_b)


# ---------------------------------------------------------------------------
# Property 4 -- both components reach the id
# ---------------------------------------------------------------------------


@_SETTINGS
@given(
    device=_DOMAIN_TEXT,
    start_time=_DOMAIN_TEXT,
    replacements=st.lists(_DOMAIN_TEXT, min_size=2, max_size=2, unique=True),
    change_device=st.booleans(),
)
@example(
    device=_REAL_DEVICE,
    start_time=_REAL_START_TIME,
    replacements=["2026-08-14T06:12:03+00:00", "2026-08-14T06:12:04+00:00"],
    change_device=False,
)
@example(
    device=_REAL_DEVICE,
    start_time=_REAL_START_TIME,
    replacements=["garmin-forerunner_965-3441234567", "garmin-fenix8-3441234567"],
    change_device=True,
)
@example(
    device="d",
    start_time="t",
    replacements=["", "\U0001f3c3"],
    change_device=False,
)
def test_changing_exactly_one_component_changes_the_id(
    device: str,
    start_time: str,
    replacements: list[str],
    change_device: bool,
) -> None:
    """Two tuples that differ in exactly one field derive different ids.

    This is aimed at the *component structure* rather than at the hash:
    it is what fails if either field stops reaching the digest. Drop
    ``start_time`` from the join and every ``change_device=False``
    example collides; drop ``source_device`` and every
    ``change_device=True`` example collides. Two same-device sessions
    minutes apart, and one athlete's two watches recording the same
    morning, are both real shapes in this corpus -- T047 depends on
    same-day resting-HRV readings staying distinct, and a merge here is
    silent.

    The two replacement values are drawn as a ``unique=True`` pair, so
    they always differ and no example is ever discarded.
    """
    original, alternative = replacements
    if change_device:
        first = derive_session_id(original, start_time)
        second = derive_session_id(alternative, start_time)
    else:
        first = derive_session_id(device, original)
        second = derive_session_id(device, alternative)

    assert first != second


# ---------------------------------------------------------------------------
# Property 5 -- the components are ordered, not a set
# ---------------------------------------------------------------------------


@_SETTINGS
@given(pair=st.lists(_DOMAIN_TEXT, min_size=2, max_size=2, unique=True))
@example(pair=[_REAL_DEVICE, _REAL_START_TIME])
@example(pair=["", "x"])
@example(pair=["ab", "c"])
def test_the_two_components_are_not_interchangeable(pair: list[str]) -> None:
    """``derive_session_id(a, b)`` and ``derive_session_id(b, a)``
    differ for any ``a != b``.

    ``(device, time)`` is an ordered pair, not a set: the two fields
    are separately meaningful and the UNIQUE constraint compares them
    column-wise. A derivation that sorted or otherwise normalised the
    components before hashing would still be deterministic, still the
    right shape, and still separator-protected -- and would map a
    genuinely impossible-to-confuse pair of tuples onto one id. Nothing
    else in this file would notice.
    """
    first, second = pair

    assert derive_session_id(first, second) != derive_session_id(second, first)
