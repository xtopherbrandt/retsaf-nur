"""Normalized graded speed over contiguous blocks of device speed (F013 AC6, spec/03 section 3.3.3).

NGP is the fourth-power mean of a 30 s trailing rolling mean of graded speed:
the Normalized Power construction applied to speed, so a surging run reads
harder than a steady run of the same average. Its per-record series is
**``record.speed * g``**, the device's own speed, and never ``v_actual`` or a
distance difference, on the premise that the device absorbs GPS-acquisition
and tunnel-exit distance jumps. On three real runs it did: device speed stayed
continuous through ``strap_run_hrv``'s 25.4 m and 40.2 m one-second distance
jumps (speed near 3.0 m/s), through ``hilly_run_8k_fr945``'s 11.7 m jump
during GPS acquisition, 10 s after the start (speed near 2.0 m/s), and through
``hilly_long_run_17k_fr945``'s 8.6 m jump 69 s after the start (speed near
2.3 m/s). A jump is a 1 s step whose distance exceeds speed times dt by over
5 m (F014 reference section 3, Shipyard data dir). What no
real run shows yet is a device-speed spike: the corpus's one spike, 24.027 m/s
on 44 records, is in ``strap_hrv_sample_run``, a resting sample, not a run
(IDEA-124). Nothing in this module reads ``distance``, ``s`` or ``v_actual``.

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


def record_block_ids(tb: TimeBase) -> list[int | None]:
    """The NGP block each record's sample belongs to, numbered 0, 1, 2, ... in record order.

    One entry per record of ``tb``. ``None`` marks a record that is left out of
    the series: its speed is absent, or it is reached by a dt = 0 segment. A
    break segment or an absent speed closes the current block, and the next
    present speed opens the next one; a dt = 0 arrival closes nothing. Only
    blocks that hold a sample take a number, so the ids are contiguous and
    match the order ``ngp`` pools them in. The session transform's per-segment
    stream reads this so its ``block_id`` is NGP's numbering, not a second one.
    """
    ids: list[int | None] = []
    block = 0  # the id the next present speed joins
    holds_sample = False  # whether block ``block`` has a sample yet
    for j, record in enumerate(tb.records):
        if j > 0:
            arriving = tb.segments[j - 1]
            if arriving.is_break:
                if holds_sample:
                    block += 1
                    holds_sample = False
            elif arriving.dt == 0.0:
                ids.append(None)
                continue
        if record.speed is None:
            if holds_sample:
                block += 1
                holds_sample = False
            ids.append(None)
            continue
        ids.append(block)
        holds_sample = True
    return ids


def _blocks(tb: TimeBase, g_per_record: Sequence[float]) -> list[list[float]]:
    """Cut the graded device-speed series into the contiguous blocks ``record_block_ids`` numbers.

    Empty blocks are never numbered, so none is emitted.
    """
    blocks: list[list[float]] = []
    for j, block_id in enumerate(record_block_ids(tb)):
        if block_id is None:
            continue
        if block_id == len(blocks):
            blocks.append([])
        blocks[block_id].append(tb.records[j].speed * g_per_record[j])
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
            f"g_per_record has {len(g_per_record)} entries for {len(tb.records)} records; "
            "one per record is needed"
        )
    fourth_powers = [m**4 for block in _blocks(tb, g_per_record) for m in _window_means(block)]
    if not fourth_powers:
        return Feature(None, "no_30s_block")
    pooled = sum(fourth_powers) / len(fourth_powers)
    if pooled == 0.0:
        return Feature(None, "no_motion")
    return Feature(pooled**0.25, None)
