"""Builds the per-instant render state: active notes (with approach/hit/miss
animation progress), interpolated cursor position, and running combo/accuracy.

The .rhr format doesn't preserve the exact approach-rate/spawn-distance the
replay was recorded with (see formats/rhr.py), so `Timeline` takes them as
configurable parameters (surfaced in the GUI) rather than baking in a single
default -- pick whatever makes the video read the way you want.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from ..formats.rhm import Note
from ..formats.rhr import Replay
from .hitreg import NoteResult

# Defaults matching Rhythia's own open-source map visualizer (rhythia.com's
# Map preview sketch): a note spawns 15 cells away and the whole approach
# takes exactly 550ms, i.e. ~27.27 cells/second. The units are the game's
# own (same as the ApproachRate/SpawnDistance settings in skin/game
# configs), and both remain exposed via render_video()/the GUI so a run can
# be re-rendered with the player's personal settings.
DEFAULT_SPAWN_DISTANCE = 15.0
DEFAULT_APPROACH_RATE = 15.0 / 0.55  # cells per second -> 550ms approach

# The official visualizer keys note alpha directly to approach progress
# (alpha = progress * 400 clamped to 200, out of 255): the note fades in
# over the first half of the approach and caps at ~78% opacity, never
# reaching full white until the hit flash... it simply disappears at the
# hit moment.
NOTE_ALPHA_RAMP = 400.0 / 255.0
NOTE_ALPHA_MAX = 200.0 / 255.0

# A missed note isn't recolored or flashed in the game -- it just keeps
# flying at the same speed past the hit plane (towards/past the camera)
# while fading out.
MISS_FADE_MS = 200.0

# How long the hit burst effect (expanding outline/fragments in
# frame._draw_hit_effects) lasts after a note is hit.
HIT_EFFECT_MS = 280.0

POST_WINDOW_MS = max(MISS_FADE_MS, HIT_EFFECT_MS)

# How long a hit/miss stays visible on the hit-error (timing) bar.
ERROR_BAR_WINDOW_MS = 3000.0

# GHOST mod: the note fades *out* well before its hit moment -- the player
# clicks from memory. Fade starts about a third of the way in and the note
# is fully invisible shortly past the midpoint of the approach.
GHOST_FADE_START = 0.35  # approach progress where the fade-out begins
GHOST_FADE_END = 0.62  # fully invisible from here on

# CHAOS mod: each note swings along a pronounced curved path while
# approaching -- veering off to one side and arcing back to land exactly on
# its lane at the hit moment. Amplitude in cells; direction and phase vary
# per note.
CHAOS_AMPLITUDE = 0.9
CHAOS_SWINGS = 1.5  # half-oscillations completed over one approach


@dataclass
class NoteRenderState:
    note: Note
    depth: float
    opacity: float
    kind: str  # "approaching" | "hit" | "miss"
    burst: float = 0.0
    # Position of this note in the map's note order -- used to cycle skin
    # colorset colors per note, the way the game does.
    index: int = 0
    # Scene-cell offset from the note's lane (CHAOS mod wobble).
    offset_x: float = 0.0
    offset_y: float = 0.0
    # Approach progress: 0 at spawn, 1 at the hit moment (1 for hit/miss
    # states) -- drives the optional spawn pop-in animation in frame.py.
    progress: float = 1.0


@dataclass
class TimelineState:
    time_ms: float
    cursor_xy: tuple[float, float]
    notes: list[NoteRenderState] = field(default_factory=list)
    combo: int = 0
    accuracy_pct: float = 100.0
    hits_so_far: int = 0
    misses_so_far: int = 0
    resolved_count: int = 0
    total_notes: int = 0
    health_pct: float = 100.0
    last_miss_ms: float | None = None
    # Hits within the last HIT_EFFECT_MS, for the hit burst effect
    # (kind="hit", burst = progress 0..1 through the effect).
    recent_hits: list[NoteRenderState] = field(default_factory=list)
    # Notes resolved within ERROR_BAR_WINDOW_MS, for the hit-error bar:
    # (age_ms, offset_ms) with offset None for misses. Offset is
    # hit time - note time, so negative = early, positive = late.
    recent_errors: list[tuple[float, float | None]] = field(default_factory=list)


class Timeline:
    def __init__(
        self,
        notes: list[Note],
        results: list[NoteResult],
        replay: Replay,
        spawn_distance: float = DEFAULT_SPAWN_DISTANCE,
        approach_rate: float = DEFAULT_APPROACH_RATE,
        ghost: bool = False,
        chaos: bool = False,
    ):
        assert len(notes) == len(results)
        self.ghost = ghost
        self.chaos = chaos
        self.notes = notes
        self.results = results
        self.replay = replay
        self.times = [n.time_ms for n in notes]

        self.spawn_distance = spawn_distance
        self.approach_rate = approach_rate
        # Matches the Rhythia-family convention: approach time is how long
        # before its timestamp a note spawns, derived from how far away it
        # spawns and how fast it travels (distance / rate).
        self.approach_time_ms = (spawn_distance / approach_rate * 1000.0) if approach_rate > 0 else 0.0

        cum_hits = [0] * (len(notes) + 1)
        cum_misses = [0] * (len(notes) + 1)
        combo_arr = [0] * (len(notes) + 1)
        last_miss_arr: list[float | None] = [None] * (len(notes) + 1)
        combo = 0
        last_miss: float | None = None
        for i, res in enumerate(results):
            cum_hits[i + 1] = cum_hits[i] + (1 if res.hit else 0)
            cum_misses[i + 1] = cum_misses[i] + (0 if res.hit else 1)
            combo = combo + 1 if res.hit else 0
            combo_arr[i + 1] = combo
            if not res.hit:
                last_miss = notes[i].time_ms
            last_miss_arr[i + 1] = last_miss

        self._cum_hits = cum_hits
        self._cum_misses = cum_misses
        self._combo_arr = combo_arr
        self._last_miss_arr = last_miss_arr

        # Every resolved note as (moment it resolved, timing offset), sorted,
        # for the hit-error bar: a hit resolves at its recorded hit frame
        # (offset = hit - note time); a miss at the note's own timestamp
        # (offset None).
        error_events: list[tuple[float, float | None]] = []
        for note, res in zip(notes, results):
            if res.hit and res.hit_ms is not None:
                error_events.append((res.hit_ms, res.hit_ms - note.time_ms))
            elif not res.hit:
                error_events.append((note.time_ms, None))
        error_events.sort(key=lambda e: e[0])
        self._error_events = error_events
        self._error_times = [e[0] for e in error_events]

    @property
    def length_ms(self) -> float:
        return self.replay.length_ms

    def _note_state(self, note: Note, result: NoteResult, t: float, index: int = 0) -> NoteRenderState | None:
        spawn_t = note.time_ms - self.approach_time_ms

        if t < spawn_t:
            return None

        if t <= note.time_ms:
            progress = (t - spawn_t) / self.approach_time_ms if self.approach_time_ms > 0 else 1.0
            depth = self.spawn_distance * (1.0 - progress)
            # Official visualizer alpha curve: fades in over the first half
            # of the approach and caps at ~78% opacity.
            opacity = min(progress * NOTE_ALPHA_RAMP, NOTE_ALPHA_MAX)

            if self.ghost:
                fade = (GHOST_FADE_END - progress) / (GHOST_FADE_END - GHOST_FADE_START)
                opacity *= max(0.0, min(1.0, fade))
                if opacity <= 0.0:
                    return None

            offset_x = offset_y = 0.0
            if self.chaos:
                # Per-note direction/phase so simultaneous notes swing
                # apart instead of in formation (golden-angle spacing).
                phase = index * 2.399963
                # A swing keyed to approach progress (not wall time) so
                # every note traces a full, visible arc into its lane:
                # swings out, curves back, lands centered (decay -> 0).
                decay = 1.0 - progress
                swing = math.sin(progress * math.pi * CHAOS_SWINGS + phase)
                amp = CHAOS_AMPLITUDE * decay
                offset_x = amp * swing * math.cos(phase * 1.7)
                offset_y = amp * swing * math.sin(phase * 1.7)

            return NoteRenderState(
                note=note, depth=depth, opacity=opacity, kind="approaching",
                offset_x=offset_x, offset_y=offset_y, progress=progress,
            )

        # Past the hit moment: a hit note disappears instantly (the game
        # draws no burst); a missed one keeps travelling at the same speed
        # past the hit plane while fading out.
        if result.hit:
            return None

        dt = t - note.time_ms
        if dt > MISS_FADE_MS:
            return None
        burst = dt / MISS_FADE_MS
        depth = -self.spawn_distance * (dt / self.approach_time_ms) if self.approach_time_ms > 0 else 0.0
        return NoteRenderState(note=note, depth=depth, opacity=NOTE_ALPHA_MAX * (1.0 - burst), kind="miss", burst=burst)

    def state_at(self, t: float) -> TimelineState:
        idx = bisect.bisect_right(self.times, t)
        hits_so_far = self._cum_hits[idx]
        misses_so_far = self._cum_misses[idx]
        combo = self._combo_arr[idx]
        accuracy = 100.0 * hits_so_far / idx if idx > 0 else 100.0

        lo = bisect.bisect_left(self.times, t - POST_WINDOW_MS)
        hi = bisect.bisect_right(self.times, t + self.approach_time_ms)

        active: list[NoteRenderState] = []
        recent_hits: list[NoteRenderState] = []
        for i in range(lo, hi):
            note, result = self.notes[i], self.results[i]
            st = self._note_state(note, result, t, index=i)
            if st is not None:
                st.index = i
                active.append(st)
            if result.hit and 0.0 < t - note.time_ms <= HIT_EFFECT_MS:
                recent_hits.append(NoteRenderState(
                    note=note, depth=0.0, opacity=1.0, kind="hit",
                    burst=(t - note.time_ms) / HIT_EFFECT_MS, index=i,
                ))

        cursor_xy = self.replay.cursor_position_at(t)

        err_lo = bisect.bisect_left(self._error_times, t - ERROR_BAR_WINDOW_MS)
        err_hi = bisect.bisect_right(self._error_times, t)
        recent_errors = [(t - when, offset) for when, offset in self._error_events[err_lo:err_hi]]

        return TimelineState(
            time_ms=t,
            cursor_xy=cursor_xy,
            notes=active,
            combo=combo,
            accuracy_pct=accuracy,
            hits_so_far=hits_so_far,
            misses_so_far=misses_so_far,
            resolved_count=idx,
            total_notes=len(self.notes),
            # The replay records the player's real health per tick (see
            # formats/rhr.py) -- no approximation needed.
            health_pct=self.replay.health_at(t) * 100.0,
            last_miss_ms=self._last_miss_arr[idx],
            recent_hits=recent_hits,
            recent_errors=recent_errors,
        )
