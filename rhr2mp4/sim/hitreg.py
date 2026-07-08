"""Matches the replay's per-frame hit flag to individual map notes.

Each cursor frame in a .rhr replay carries a boolean flag that is set exactly
on the frame where a note was hit (validated against the replay's own
hits/misses summary: the count of flagged frames equals the stored hit count,
and every flagged frame falls within ~50ms of a map note). Both notes and
flagged frames are chronological, and flagged frames are a subsequence of the
notes (one flag per hit note, none for misses), so a simple two-pointer walk
recovers hit/miss + hit timing for every note.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..formats.rhm import Note
from ..formats.rhr import Frame

DEFAULT_WINDOW_MS = 80


@dataclass
class NoteResult:
    note: Note
    hit: bool
    hit_ms: float | None


def match_hits(
    notes: list[Note],
    frames: list[Frame],
    window_ms: float = DEFAULT_WINDOW_MS,
) -> list[NoteResult]:
    hit_frames = [f for f in frames if f.important]

    results: list[NoteResult] = []
    hi = 0
    n_hit = len(hit_frames)
    for note in notes:
        hit = False
        hit_ms = None
        if hi < n_hit and abs(hit_frames[hi].ms - note.time_ms) <= window_ms:
            hit = True
            hit_ms = hit_frames[hi].ms
            hi += 1
        results.append(NoteResult(note=note, hit=hit, hit_ms=hit_ms))

    return results
