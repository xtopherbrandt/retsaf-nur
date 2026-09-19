# spec-mirror — committed copies of the normative feature documents

These are **copies**, not the source of truth. The originals live in the Shipyard data dir
(`spec/features/` and `spec/references/`, reached from a checkout through the gitignored,
machine-local `.shipyard` breadcrumb), and the Shipyard pipelines read them from there by a
fixed path — which is why they could not simply be moved here (T124).

Why they are here at all: the corpus phrasing scan in
`runcoach-api/tests/test_hrv_trend_endpoint.py` and the AST oracle in
`runcoach-api/tests/test_hrv_unavailable_causes.py` read these documents. Until T124 they could
read them only where the breadcrumb existed — one laptop — and skipped everywhere else, including
CI. Now they read the copies, on every machine.

What keeps a copy a copy: `runcoach-api/tests/test_normative_mirror.py` fails when a file here
differs by a byte from its original, and when the data dir holds an `F00X-*.md` document for a
mirrored feature that has no copy here. It walks these directories; it does not list names. It
can only run where the data dir is reachable (`.shipyard`, or `SHIPYARD_DATA_DIR`), and skips
loudly elsewhere — the module docstring says why that is acceptable.

The copies are stored verbatim (`.gitattributes`: `spec-mirror/** -text`), because the originals
are not uniform in line endings and a checkout that converted them would differ from the original
by platform and fail the gate.

Do not edit anything under `features/` or `references/` by hand. Edit the original in the data
dir and recopy; the gate will tell you when the two have parted.
