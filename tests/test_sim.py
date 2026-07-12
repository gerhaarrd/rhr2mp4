"""Hit matching, timeline state and highlight picking on synthetic data."""

from __future__ import annotations

import unittest

from rhr2mp4.formats.rhm import Note
from rhr2mp4.formats.rhr import Frame, Replay
from rhr2mp4.sim.highlight import find_highlight
from rhr2mp4.sim.hitreg import match_hits
from rhr2mp4.sim.timeline import Timeline


def make_replay(frames: list[Frame], fail_time: int = -1) -> Replay:
    return Replay(
        version=20260517, timestamp_ticks=0, username="t", map_legacy_id="",
        map_online_id=0, start_from=0, mode="", passed=fail_time < 0, mods="[]",
        spin=False, speed=1.0, total_score=0, accuracy_pct=100.0,
        hits=0, misses=0, points=0.0, fail_time=fail_time, beatmap_hash="",
        frames=frames,
    )


class TestHitreg(unittest.TestCase):
    def test_hits_and_misses(self):
        notes = [Note(100, 0, 0), Note(200, 1, 1), Note(300, 2, 2)]
        frames = [
            Frame(ms=95, x=0, y=0, health=1.0, important=True),   # hit note 1 (early)
            Frame(ms=150, x=1, y=1, health=1.0, important=False),
            Frame(ms=310, x=2, y=2, health=1.0, important=True),  # hit note 3 (late)
        ]
        results = match_hits(notes, frames)
        self.assertEqual([r.hit for r in results], [True, False, True])
        self.assertEqual(results[0].hit_ms, 95)
        self.assertEqual(results[2].hit_ms, 310)


class TestTimelineErrors(unittest.TestCase):
    def test_recent_errors(self):
        notes = [Note(1000, 0, 0), Note(2000, 1, 1)]
        frames = [
            Frame(ms=0, x=0, y=0, health=1.0, important=False),
            Frame(ms=990, x=0, y=0, health=1.0, important=True),  # -10ms early
            Frame(ms=3000, x=1, y=1, health=1.0, important=False),
        ]
        results = match_hits(notes, frames)
        tl = Timeline(notes, results, make_replay(frames))
        state = tl.state_at(2500)
        # Hit at 990 (offset -10) and the miss at 2000 (offset None).
        self.assertEqual(len(state.recent_errors), 2)
        offsets = sorted((o for _, o in state.recent_errors if o is not None))
        self.assertEqual(offsets, [-10])
        self.assertIn(None, [o for _, o in state.recent_errors])
        # Both fall out of the window eventually.
        self.assertEqual(tl.state_at(9000).recent_errors, [])


class TestHighlight(unittest.TestCase):
    def _frames(self, length_ms: int, dip_at: int | None = None) -> list[Frame]:
        frames = []
        for ms in range(0, length_ms + 1, 100):
            health = 1.0
            if dip_at is not None and dip_at <= ms < dip_at + 500:
                health = 0.15
            frames.append(Frame(ms=ms, x=0, y=0, health=health, important=False))
        return frames

    def test_short_replay_returns_whole(self):
        replay = make_replay(self._frames(8000))
        self.assertEqual(find_highlight([1000, 2000], replay, duration_ms=20000), (0.0, 8000.0))

    def test_prefers_dense_section(self):
        # Sparse notes everywhere, a dense burst at 60-70s.
        note_times = list(range(0, 120001, 2000)) + list(range(60000, 70001, 100))
        replay = make_replay(self._frames(120000))
        start, end = find_highlight(note_times, replay, duration_ms=20000)
        self.assertEqual(end - start, 20000)
        # The burst midpoint must fall inside the window.
        self.assertLess(start, 65000)
        self.assertGreater(end, 65000)

    def test_fail_beats_density(self):
        note_times = list(range(0, 120001, 2000)) + list(range(20000, 30001, 100))
        replay = make_replay(self._frames(120000, dip_at=100000), fail_time=100500)
        start, end = find_highlight(note_times, replay, duration_ms=20000)
        self.assertLessEqual(start, 100500)
        self.assertGreaterEqual(end, 100500)


if __name__ == "__main__":
    unittest.main()
