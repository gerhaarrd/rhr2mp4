"""Detection and application of Rhythia gameplay mods recorded in a replay.

The replay's `mods` field is a JSON array of mod ids (confirmed against the
game's own mod icon resources): "mod_hardrock", "mod_mirror", "mod_ghost",
"mod_chaos".

Mirror's axis (horizontal / vertical / both) is a game-side setting that is
NOT stored in the replay, so it is recovered empirically: the cursor path
follows the *mirrored* note positions, so trying each axis flip and keeping
the one that minimizes cursor-to-note distance at the hit moments
reconstructs the axis exactly (on a real mirrored replay the best flip
scores ~0.2 cells mean distance vs ~1.3+ for the alternatives).
"""

from __future__ import annotations

import json
import math

from ..formats.rhm import Note
from ..formats.rhr import Replay
from .hitreg import match_hits

# Approximation: hardrock enlarges the play area around the unchanged note
# grid (an HR replay's cursor was observed past the normal +/-1.5-cell
# bound). The exact factor isn't recoverable from the replay, so the border
# is widened ~11% from the standard 1.5 to 1.667 cells.
HR_PLAYFIELD_EXTENT = 1.667
DEFAULT_PLAYFIELD_EXTENT = 1.5


def parse_mods(mods_str: str) -> set[str]:
    try:
        parsed = json.loads(mods_str or "[]")
    except (ValueError, TypeError):
        return set()
    return {str(m) for m in parsed} if isinstance(parsed, list) else set()


def detect_mirror(notes: list[Note], replay: Replay) -> tuple[bool, bool]:
    """Returns (flip_x, flip_y): the axis flip whose mirrored note positions
    best match where the cursor actually was at each hit."""
    results = match_hits(notes, replay.frames)
    best = (False, False)
    best_dist = math.inf
    for flip_x in (False, True):
        for flip_y in (False, True):
            total = 0.0
            n = 0
            for note, res in zip(notes, results):
                if not res.hit or res.hit_ms is None:
                    continue
                cx, cy = replay.cursor_position_at(res.hit_ms)
                x = 2.0 - note.x if flip_x else note.x
                y = 2.0 - note.y if flip_y else note.y
                # note grid -> scene cells (see frame.note_to_scene)
                total += math.hypot(cx - (x - 1.0), cy - (1.0 - y))
                n += 1
            if n and total / n < best_dist:
                best_dist = total / n
                best = (flip_x, flip_y)
    return best


def apply_mirror(notes: list[Note], flip_x: bool, flip_y: bool) -> list[Note]:
    if not flip_x and not flip_y:
        return notes
    return [
        Note(
            time_ms=n.time_ms,
            x=2.0 - n.x if flip_x else n.x,
            y=2.0 - n.y if flip_y else n.y,
        )
        for n in notes
    ]


def resolve_mods(notes: list[Note], replay: Replay):
    """Applies every mod recorded in the replay. Returns
    (notes, ghost, chaos, playfield_extent, mods_label) where `notes` is
    the (possibly mirrored) note list the run was actually played against
    and `mods_label` uses the game's short mod codes, e.g. "HR MR GH"
    ("" without mods): HR hardrock, MR/MY horizontal/vertical mirror,
    GH ghost, CH chaos. Shown next to the grade in the rendered HUD."""
    mods = parse_mods(replay.mods)
    parts = []

    extent = DEFAULT_PLAYFIELD_EXTENT
    if "mod_hardrock" in mods:
        extent = HR_PLAYFIELD_EXTENT
        parts.append("HR")

    if "mod_mirror" in mods:
        flip_x, flip_y = detect_mirror(notes, replay)
        notes = apply_mirror(notes, flip_x, flip_y)
        if flip_x:
            parts.append("MR")
        if flip_y:
            parts.append("MY")

    ghost = "mod_ghost" in mods
    if ghost:
        parts.append("GH")

    chaos = "mod_chaos" in mods
    if chaos:
        parts.append("CH")

    return notes, ghost, chaos, extent, " ".join(parts)
