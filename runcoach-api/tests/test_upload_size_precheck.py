"""M1 (sprint-002 review): a request whose declared ``Content-Length``
already exceeds the upload cap must be rejected before the body is
ever read off the wire -- not after Starlette's multipart parser has
already buffered the whole thing to a ``SpooledTemporaryFile``.

``create_session``'s own ``file.file.read(MAX_UPLOAD_BYTES + 1)`` guard
runs too late to close that: by the time the route body executes,
FastAPI has already resolved the ``UploadFile`` dependency, which means
the full body has already been consumed and spooled to disk (see
``starlette/formparsers.py``'s ``MultiPartParser`` -- confirmed by
reading the vendored source under ``.venv/``: ``max_part_size`` caps an
individual part's size, never the total file part size).

These tests exercise ``ContentLengthLimitMiddleware`` directly as a
plain ASGI callable (no pytest-asyncio configured in this workspace --
see pyproject.toml -- so each test drives its own event loop via
``asyncio.run``), proving:

- an oversized declared Content-Length is rejected with 413 and the
  inner app (which is what would trigger multipart buffering) is never
  invoked, and the body is never read via ``receive()``;
- a Content-Length within the cap passes through to the inner app;
- a request with no Content-Length header at all (e.g. chunked
  transfer-encoding) also passes through -- this is the accepted
  residual gap recorded in F003's Decision Log (2026-09-03, "M1 upload
  buffering"), not something this middleware claims to close.
"""

from __future__ import annotations

import asyncio

from runcoach_api.main import (
    MAX_UPLOAD_BYTES,
    ContentLengthLimitMiddleware,
    _MULTIPART_FRAMING_ALLOWANCE_BYTES,
)


def _run(coro):
    return asyncio.run(coro)


def _make_scope(content_length: str | None) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))
    return {"type": "http", "headers": headers, "method": "POST", "path": "/sessions"}


def test_oversized_content_length_is_rejected_before_inner_app_runs() -> None:
    inner_app_calls: list = []

    async def inner_app(scope, receive, send) -> None:
        inner_app_calls.append(scope)

    async def receive():
        raise AssertionError(
            "body must never be read once the declared Content-Length exceeds the cap"
        )

    sent: list = []

    async def send(message) -> None:
        sent.append(message)

    middleware = ContentLengthLimitMiddleware(inner_app, max_bytes=MAX_UPLOAD_BYTES)
    over_the_framing_allowance = MAX_UPLOAD_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1
    scope = _make_scope(str(over_the_framing_allowance))

    _run(middleware(scope, receive, send))

    assert inner_app_calls == []
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    body_message = next(m for m in sent if m["type"] == "http.response.body")
    assert str(MAX_UPLOAD_BYTES) in body_message["body"].decode()


def test_content_length_within_multipart_framing_allowance_reaches_inner_app() -> None:
    """A declared Content-Length a few bytes over MAX_UPLOAD_BYTES is
    real multipart framing overhead (boundary markers, part headers),
    not an oversized file -- create_session's own exact
    file.file.read(MAX_UPLOAD_BYTES + 1) check is what makes the final
    call on the file's own byte count. The precheck must not pre-empt
    that with a coarser, request-level cap."""
    inner_app_calls: list = []

    async def inner_app(scope, receive, send) -> None:
        inner_app_calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list = []

    async def send(message) -> None:
        sent.append(message)

    middleware = ContentLengthLimitMiddleware(inner_app, max_bytes=MAX_UPLOAD_BYTES)
    scope = _make_scope(str(MAX_UPLOAD_BYTES + 200))  # typical multipart framing overhead

    _run(middleware(scope, receive, send))

    assert len(inner_app_calls) == 1


def test_content_length_within_limit_reaches_inner_app() -> None:
    inner_app_calls: list = []

    async def inner_app(scope, receive, send) -> None:
        inner_app_calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list = []

    async def send(message) -> None:
        sent.append(message)

    middleware = ContentLengthLimitMiddleware(inner_app, max_bytes=MAX_UPLOAD_BYTES)
    scope = _make_scope(str(MAX_UPLOAD_BYTES - 1))

    _run(middleware(scope, receive, send))

    assert len(inner_app_calls) == 1


def test_missing_content_length_passes_through_as_accepted_residual_gap() -> None:
    """Chunked transfer-encoding carries no Content-Length header; this
    middleware cannot reject on a header that isn't there. Documented,
    not silently dropped -- see F003 Decision Log."""
    inner_app_calls: list = []

    async def inner_app(scope, receive, send) -> None:
        inner_app_calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list = []

    async def send(message) -> None:
        sent.append(message)

    middleware = ContentLengthLimitMiddleware(inner_app, max_bytes=MAX_UPLOAD_BYTES)
    scope = _make_scope(None)

    _run(middleware(scope, receive, send))

    assert len(inner_app_calls) == 1
