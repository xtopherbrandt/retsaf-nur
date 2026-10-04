"""Normalized graded speed over contiguous blocks of device speed (F013 AC6, spec/03 section 3.3.3).

NGP is the fourth-power mean of a 30 s trailing rolling mean of graded speed:
the Normalized Power construction applied to speed, so a surging run reads
harder than a steady run of the same average. Its per-record series is
**``record.speed * g``**, the device's own speed, and never ``v_actual`` or a
distance difference. The device has already absorbed GPS-acquisition and
tunnel-exit distance jumps: ``strap_hrv_sample_run`` carries a 52 m jump in
one second while ``speed`` reads 0, and ``v_actual`` over that stride would
have doubled NGP (F013 Decision Log, 2026-10-03). Nothing in this module reads
``distance``, ``s`` or ``v_actual``.

**Blocks.** A rolling window must not span a pause, so the series is cut into
contiguous blocks:

- a **break** segment (``dt > SEGMENT_MAX_DT_S``) ends the block before it and
  starts a new one after it;
- a record whose **speed is absent** ends the block before it and is itself
  left out, so the next present speed starts a new block;
- a record reached by a **dt = 0** segment (the resampler can emit duplicate
  instants) is **dropped** from the series and does **not** split the block.
  It carries no time, so keeping it would put an extra sample into a 30 s
  window; and the time base already makes dt = 0 contribute nothing (ratified
  at sprint-011 planning, 2026-10-04).

Within a block of ``n`` samples the ``NGP_WINDOW``-sample trailing mean yields
``n - NGP_WINDOW + 1`` full windows, so a block of 30 records yields one and
counts; a block of 29 yields none and is not padded. Every full window's mean
is raised to the fourth power, pooled over **all blocks**, averaged, and the
fourth root is taken.

**One sample per record is one sample per second.** The window is counted in
samples, which assumes the 1 s grid inside a block. The ingestion gate
resamples every gap of up to ``SEGMENT_MAX_DT_S`` to 1 s steps, and anything
longer is a break that splits the block, so inside a block the grid holds.

**Unavailable reasons.** ``no_30s_block`` when no block yields a full window,
whatever the speeds are; ``no_motion`` when at least one window is full and
the pooled mean is 0. The full-window check comes first.

**Grade is supplied, not computed.** ``g_per_record[j]`` is the adjustment of
the segment that *ends* at record ``j`` (1.0 for record 0 and for any
ungraded segment); the session transform computes it. The module is therefore
testable with g = 1 and with injected g values.

**Pure, by design.** Nothing here imports ``config``, ``db`` or ``fastapi``,
reads a clock or opens a connection. The pass over the records is O(n): one
running sum per block.
"""

from __future__ import annotations

from collections.abc import Sequence

from runcoach_api.metrics.segments import Feature, TimeBase

# spec/03 section 3.3.3: NGP is taken over a 30 s rolling mean of graded speed, one sample per second.
NGP_WINDOW = 30


def _blocks(tb: TimeBase, g_per_record: Sequence[float]) -> list[list[float]]:
    """Cut the graded device-speed series into contiguous blocks.

    A break segment or an absent speed closes the current block; a record
    reached by a dt = 0 segment is skipped without closing it. Empty blocks are
    not emitted.
    """
    blocks: list[list[float]] = []
    current: list[float] = []
    for j, record in enumerate(tb.records):
        if j > 0:
            arriving = tb.segments[j - 1]
            if arriving.is_break:
                if current:
                    blocks.append(current)
                current = []
            elif arriving.dt == 0.0:
                continue
        if record.speed is None:
            if current:
                blocks.append(current)
            current = []
            continue
        current.append(record.speed * g_per_record[j])
    if current:
        blocks.append(current)
    return blocks


def _window_means(block: Sequence[float]) -> list[float]:
    """The ``NGP_WINDOW``-sample trailing means of one block, by running sum; none for a short block."""
    if len(block) < NGP_WINDOW:
        return []
    running = sum(block[:NGP_WINDOW])
    means = [running / NGP_WINDOW]
    for leaving, entering in zip(block, block[NGP_WINDOW:]):
        running += entering - leaving
        means.append(running / NGP_WINDOW)
    return means


def ngp(tb: TimeBase, g_per_record: Sequence[float]) -> Feature:
    """The normalized graded speed of a session, in m/s, or the reason it has none.

    ``g_per_record`` holds one adjustment per record of ``tb``: the g of the
    segment ending at that record, 1.0 for record 0 and for ungraded segments.
    Raises ``ValueError`` when its length differs from ``len(tb.records)``,
    because a misaligned g would silently grade the wrong strides.
    """
    if len(g_per_record) != len(tb.records):
        raise ValueError(
            f"g_per_record has {len(g_per_record)} entries for {len(tb.records)} records; one per record is needed"
        )
    fourth_powers = [m**4 for block in _blocks(tb, g_per_record) for m in _window_means(block)]
    if not fourth_powers:
        return Feature(None, "no_30s_block")
    pooled = sum(fourth_powers) / len(fourth_powers)
    if pooled == 0.0:
        return Feature(None, "no_motion")
    return Feature(pooled**0.25, None)
