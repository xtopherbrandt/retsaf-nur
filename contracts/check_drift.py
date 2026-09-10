#!/usr/bin/env python3
"""Contract drift check: target OpenAPI (contracts/openapi.yaml) vs. as-built.

Fails CI when the backend's live surface has drifted away from the endpoints the
target contract marks `x-readiness: implemented`. It checks the things that are a
genuine breaking contradiction — the operation exists, keeps its HTTP method,
still advertises the documented success status(es), and still accepts the same
required request shape — and it deliberately does NOT enforce the target's richer
*response* schemas, because the contract intentionally runs ahead of the backend
there (e.g. GET /sessions/{id} is still an untyped dict, errors are still
FastAPI-default). Those gaps are the tracked backlog, not drift; they're printed
as advisory notes, never as failures.

As-built source, in order of preference:
  1. --as-built <file.json|url>   (a saved /openapi.json, or a running server's)
  2. otherwise: import runcoach_api.main:app and call app.openapi() in-process.

Usage (from repo root):
  uv run python contracts/check_drift.py
  uv run python contracts/check_drift.py --as-built http://127.0.0.1:8000/openapi.json
  uv run python contracts/check_drift.py --target contracts/openapi.yaml -v

Exit code 0 = no drift, 1 = drift found, 2 = could not run the check.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
IMPLEMENTED = "implemented"


# --------------------------------------------------------------------------- io
def load_target(path: Path) -> dict[str, Any]:
    try:
        import yaml  # PyYAML — add to the dev dependency group (see workflow)
    except ModuleNotFoundError:
        sys.exit("error: PyYAML is required to read the target contract (pip install pyyaml)")
    with path.open() as fh:
        return yaml.safe_load(fh)


def load_as_built(source: str | None) -> dict[str, Any]:
    """Return the as-built OpenAPI document."""
    if source:
        if source.startswith(("http://", "https://")):
            with urllib.request.urlopen(source, timeout=15) as resp:  # noqa: S310
                return json.load(resp)
        return json.loads(Path(source).read_text())
    # Default: generate it in-process from the app, so no server/config is needed.
    try:
        from runcoach_api.main import app
    except Exception as exc:  # noqa: BLE001
        sys.exit(
            "error: could not import runcoach_api.main:app to generate the as-built "
            f"schema ({exc}).\n       Run from the repo root with the workspace "
            "installed (`uv run ...`), or pass --as-built <openapi.json|url>."
        )
    return app.openapi()


# --------------------------------------------------------------- contract model
def implemented_operations(target: dict[str, Any]) -> list[tuple[str, str, dict]]:
    """(path, method, operation) for every operation marked implemented."""
    ops = []
    for path, item in (target.get("paths") or {}).items():
        for method, op in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            if op.get("x-readiness") == IMPLEMENTED:
                ops.append((path, method.lower(), op))
    return ops


def success_codes(op: dict[str, Any]) -> set[str]:
    return {str(c) for c in (op.get("responses") or {}) if str(c).startswith("2")}


def _resolve(schema: Any, doc: dict[str, Any]) -> dict[str, Any]:
    """Follow a chain of local $ref pointers to the concrete schema object.

    FastAPI emits a multipart requestBody as a $ref to a generated component
    (e.g. Body_create_session_sessions_post) rather than inline properties, so
    the checker must dereference before reading properties/required.
    """
    seen: set[str] = set()
    while isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/") or ref in seen:
            return {}
        seen.add(ref)
        node: Any = doc
        for part in ref[2:].split("/"):
            if not isinstance(node, dict):
                return {}
            node = node.get(part, {})
        schema = node
    return schema if isinstance(schema, dict) else {}


def multipart_props(op: dict[str, Any], doc: dict[str, Any]) -> tuple[dict, set[str]]:
    """(properties, required-set) for a multipart/form-data requestBody, or ({}, set())."""
    body = (op.get("requestBody") or {}).get("content", {}).get("multipart/form-data", {})
    schema = _resolve(body.get("schema", {}), doc)
    return schema.get("properties", {}) or {}, set(schema.get("required", []) or [])


def required_query_params(op: dict[str, Any]) -> set[str]:
    return {
        p["name"]
        for p in (op.get("parameters") or [])
        if p.get("in") == "query" and p.get("required")
    }


# ---------------------------------------------------------------------- compare
def check(target: dict[str, Any], built: dict[str, Any], verbose: bool) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    notes: list[str] = []
    built_paths = built.get("paths") or {}

    implemented = implemented_operations(target)
    if not implemented:
        failures.append("no operations are marked `x-readiness: implemented` in the target — nothing to check")
        return failures, notes

    for path, method, op in implemented:
        where = f"{method.upper()} {path}"

        built_item = built_paths.get(path)
        if built_item is None:
            failures.append(f"{where}: path is missing from the as-built API")
            continue
        built_op = built_item.get(method)
        if built_op is None:
            failures.append(f"{where}: method not present in the as-built API (drifted or renamed)")
            continue

        # 1) success status codes the contract promises must still be served
        want = success_codes(op)
        have = {str(c) for c in (built_op.get("responses") or {})}
        missing_status = want - have
        if missing_status:
            failures.append(
                f"{where}: as-built no longer advertises success status "
                f"{sorted(missing_status)} (has {sorted(c for c in have if c.startswith('2'))})"
            )

        # 2) required request shape must not have tightened/changed under us
        t_props, t_required = multipart_props(op, target)
        if t_props:
            b_props, _ = multipart_props(built_op, built)
            for prop in t_props:
                if prop not in b_props:
                    sev = "required" if prop in t_required else "optional"
                    failures.append(
                        f"{where}: multipart field '{prop}' ({sev} in contract) is gone from the as-built body"
                    )
            for prop in b_props:
                if prop not in t_props:
                    notes.append(f"{where}: as-built has an undocumented multipart field '{prop}' (add to contract?)")

        t_req_q = required_query_params(op)
        b_q = {
            p["name"]
            for p in (built_op.get("parameters") or [])
            if p.get("in") == "query"
        }
        for q in t_req_q - b_q:
            failures.append(f"{where}: required query param '{q}' is missing from the as-built API")

        # 3) advisory only: response-body typing the backend hasn't caught up to
        t_has_schema = _op_has_response_schema(op)
        b_has_schema = _op_has_response_schema(built_op)
        if t_has_schema and not b_has_schema:
            notes.append(
                f"{where}: contract types the response but as-built does not (untyped) — tracked backlog, not drift"
            )

        if verbose:
            print(f"  ok  {where}")

    # 4) coverage: an implemented-looking endpoint the contract never captured
    documented = {(p, m) for p, m, _ in implemented}
    for path, item in built_paths.items():
        for method, bop in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(bop, dict):
                continue
            if (path, method.lower()) in documented:
                continue
            # Only flag it if the target doesn't describe this path/method at all.
            t_item = (target.get("paths") or {}).get(path, {})
            if method.lower() not in t_item:
                notes.append(
                    f"{method.upper()} {path}: present in as-built but absent from the contract "
                    "(new endpoint? add it, marked with x-readiness)"
                )

    return failures, notes


def _op_has_response_schema(op: dict[str, Any]) -> bool:
    for code, resp in (op.get("responses") or {}).items():
        if not str(code).startswith("2"):
            continue
        for media in (resp.get("content") or {}).values():
            schema = media.get("schema") or {}
            # A real schema = a $ref or a typed/propertied object, not `{}`.
            if schema.get("$ref") or schema.get("type") or schema.get("properties"):
                return True
    return False


# ------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Contract drift check (target OpenAPI vs. as-built).")
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(__file__).resolve().parent / "openapi.yaml",
        help="Path to the target contract (default: contracts/openapi.yaml next to this script).",
    )
    parser.add_argument(
        "--as-built",
        default=None,
        help="A saved /openapi.json file or a URL. Omit to import the app and generate it in-process.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print each checked operation.")
    args = parser.parse_args(argv)

    # The report uses "—", "·", "✗" and "✔". A Windows console defaults stdout to
    # cp1252, which cannot encode the check mark: the verdict line itself then
    # raises UnicodeEncodeError and the script exits 1 *after* finding no drift
    # (T091, 2026-09-09). Never let the rendering decide the exit code.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if not args.target.exists():
        print(f"error: target contract not found at {args.target}", file=sys.stderr)
        return 2

    target = load_target(args.target)
    built = load_as_built(args.as_built)

    print(f"Contract drift check — target: {args.target.name}")
    print(f"  target version: {target.get('info', {}).get('version', '?')}")
    print(f"  as-built title: {built.get('info', {}).get('title', '?')}\n")

    failures, notes = check(target, built, args.verbose)

    if notes:
        print("Notes (advisory, not failures):")
        for n in notes:
            print(f"  · {n}")
        print()

    if failures:
        print("DRIFT DETECTED — the as-built API contradicts the contract's implemented endpoints:")
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n{len(failures)} drift issue(s). Reconcile contracts/openapi.yaml with the backend.")
        return 1

    print("No drift: every implemented endpoint matches the as-built API. ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
