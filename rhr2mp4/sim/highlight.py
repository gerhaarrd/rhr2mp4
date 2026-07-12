"""Picking the most interesting stretch of a replay automatically.

Scores every candidate window of the requested length and returns the best
one, so "render a 20s highlight" needs no manual scrubbing. A window scores
higher for:
  - note density (the raw intensity of the section);
  - close calls: frames where the player's health dips low and recovers
    (near-deaths are the tensest moments of a run);
  - the fail moment itself, when the run ends in one (weighted so a death
    always beats a merely dense section).

Everything works in song time (the same time base as clip_start/end_ms in
render_video), so callers can feed the result straight into the renderer.
"""

from __future__ import annotations

from ..formats.rhr import Replay

# Relative weights: one near-death ≈ 25 notes, a fail ≈ 100 notes. Densities
# rarely exceed ~15 notes/s, so a fail dominates any 20s window's note score.
NEAR_DEATH_WEIGHT = 25.0
FAIL_WEIGHT = 100.0
NEAR_DEATH_THRESHOLD = 0.3  # health (0..1) considered "almost dead"


def _near_death_times(replay: Replay) -> list[float]:
    """Moments where health crosses below the near-death threshold (one
    event per dip, not per frame, so long agonies don't outscore a fail)."""
    times: list[float] = []
    below = False
    for f in replay.frames:
        if f.health < NEAR_DEATH_THRESHOLD:
            if not below:
                times.append(float(f.ms))
                below = True
        elif f.health >= NEAR_DEATH_THRESHOLD + 0.1:  # hysteresis
            below = False
    return times


def find_highlight(note_times_ms: list[float], replay: Replay,
                   duration_ms: float = 20000.0) -> tuple[float, float]:
    """The best (start_ms, end_ms) window of `duration_ms` in song time.

    `note_times_ms` are the map's note timestamps (sorted). Returns the whole
    replay when it's shorter than the requested window."""
    length = replay.length_ms
    if length <= duration_ms:
        return (0.0, float(length) or duration_ms)

    events: list[tuple[float, float]] = [(t, 1.0) for t in note_times_ms if t <= length]
    events += [(t, NEAR_DEATH_WEIGHT) for t in _near_death_times(replay)]
    if replay.failed:
        events.append((float(replay.fail_time), FAIL_WEIGHT))
    if not events:
        return (0.0, duration_ms)
    events.sort()

    # Slide the window across event positions with two pointers; each
    # window is anchored so it *ends* shortly after an event cluster.
    best_score, best_start = -1.0, 0.0
    lo = 0
    for hi in range(len(events)):
        end = events[hi][0]
        start = end - duration_ms
        while events[lo][0] < start:
            lo += 1
        score = sum(w for _, w in events[lo:hi + 1])
        if score > best_score:
            best_score, best_start = score, start

    # Pad so the window doesn't open exactly on the first note of the burst,
    # then clamp back into the replay.
    pad = min(1500.0, duration_ms * 0.1)
    start = max(0.0, min(best_start + pad, length - duration_ms))
    return (start, start + duration_ms)
