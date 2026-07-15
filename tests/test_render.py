"""Background helpers: brightness scaling, cover fitting, duration probe."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image

from rhr2mp4.render.frame import apply_brightness, fit_cover
from rhr2mp4.render.video import _probe_duration_s


class TestBrightness(unittest.TestCase):
    def _gray(self, value: int) -> Image.Image:
        return Image.new("RGB", (4, 4), (value, value, value))

    def test_identity(self):
        img = self._gray(120)
        self.assertIs(apply_brightness(img, 1.0), img)

    def test_darken(self):
        out = apply_brightness(self._gray(100), 0.5)
        self.assertEqual(out.getpixel((0, 0)), (50, 50, 50))

    def test_brighten_clips_at_white(self):
        out = apply_brightness(self._gray(200), 2.0)
        self.assertEqual(out.getpixel((0, 0)), (255, 255, 255))

    def test_zero_is_black(self):
        out = apply_brightness(self._gray(200), 0.0)
        self.assertEqual(out.getpixel((0, 0)), (0, 0, 0))


class TestFitCover(unittest.TestCase):
    def test_wide_source_into_tall_target(self):
        out = fit_cover(Image.new("RGB", (200, 100)), 50, 100)
        self.assertEqual(out.size, (50, 100))

    def test_exact_fit(self):
        out = fit_cover(Image.new("RGB", (64, 64)), 64, 64)
        self.assertEqual(out.size, (64, 64))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "needs ffmpeg + ffprobe on PATH")
class TestProbeDuration(unittest.TestCase):
    def test_probe(self):
        path = tempfile.mktemp(suffix=".mp4")
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "color=black:s=64x64:d=2:r=10", path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertAlmostEqual(_probe_duration_s(path), 2.0, delta=0.2)

    def test_missing_file(self):
        self.assertEqual(_probe_duration_s("/nonexistent/nope.mp4"), 0.0)


class TestElementOffsets(unittest.TestCase):
    def _ctx(self, offsets=None):
        from rhr2mp4.render.frame import build_context
        return build_context(640, 360, None, element_offsets=offsets)

    def test_rects_cover_all_movable_elements(self):
        from rhr2mp4.render.frame import MOVABLE_ELEMENTS, element_rects
        rects = element_rects(self._ctx())
        self.assertEqual(set(rects), set(MOVABLE_ELEMENTS))

    def test_offsets_shift_rects(self):
        from rhr2mp4.render.frame import element_rects
        base = element_rects(self._ctx())["title"]
        moved = element_rects(self._ctx({"title": (0.1, 0.2)}))["title"]
        self.assertAlmostEqual(moved[0] - base[0], 0.1 * 640, delta=1)
        self.assertAlmostEqual(moved[1] - base[1], 0.2 * 360, delta=1)

    def test_draw_frame_with_offsets(self):
        import numpy as np
        from rhr2mp4.formats.rhm import MapMetadata
        from rhr2mp4.render.frame import draw_frame
        from rhr2mp4.sim.timeline import Timeline
        from test_sim import make_replay
        from rhr2mp4.formats.rhr import Frame

        replay = make_replay([Frame(ms=0, x=0, y=0, health=1.0, important=False),
                              Frame(ms=5000, x=1, y=1, health=1.0, important=False)])
        meta = MapMetadata(0, "", "", "Song", [], "Song", 5000, 0, "", 0.0)
        tl = Timeline([], [], replay)
        plain = np.asarray(draw_frame(self._ctx(), tl.state_at(1000), meta, replay), float)
        moved = np.asarray(draw_frame(self._ctx({"title": (0.0, 0.4)}), tl.state_at(1000), meta, replay), float)
        # The title strip at the top must be emptier once the block moves away.
        top_strip = slice(0, int(360 * 0.1))
        self.assertGreater(plain[top_strip].mean(), moved[top_strip].mean())


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg on PATH")
class TestSegmentEncoderFallback(unittest.TestCase):
    """A hardware encoder that probes fine can still fail once every worker
    opens a session at the same time; the segment worker must retry with the
    software fallback and surface ffmpeg's stderr when everything fails."""

    def _run_segment(self, encode_args):
        from rhr2mp4.formats.rhm import MapMetadata
        from rhr2mp4.formats.rhr import Frame
        from rhr2mp4.render import video as V
        from test_sim import make_replay

        replay = make_replay([Frame(ms=0, x=0, y=0, health=1.0, important=False),
                              Frame(ms=500, x=1, y=1, health=1.0, important=False)])
        meta = MapMetadata(0, "", "", "Song", [], "Song", 500, 0, "", 0.0)
        seg_path = tempfile.mktemp(suffix=".mp4")
        self.addCleanup(lambda: os.path.exists(seg_path) and os.unlink(seg_path))

        class Queue:
            def put(self, item):
                pass

        V._render_segment(
            0, [0.0, 100.0, 200.0], 128, 128, 30, encode_args,
            None, [], [], replay, meta, seg_path, Queue(),
            1.0, 1.0, None, True, None, False, False, 1.0, "",
            True, None, True, 1.0, True, None,
            0.0, "filter", 0, 100.0 / 3, None, None, 0.4, 0.0, None,
            None, 1.0,
        )
        return seg_path

    def test_broken_encoder_falls_back_to_software(self):
        from rhr2mp4.render.video import _build_encode_args
        good, _ = _build_encode_args("h264", "none", "fast", 0.0, "filter")
        broken = dict(good, encoder="nonexistent_encoder", fallback=good)
        seg = self._run_segment(broken)
        self.assertGreater(os.path.getsize(seg), 0)

    def test_failure_surfaces_ffmpeg_stderr(self):
        from rhr2mp4.render.video import _build_encode_args
        good, _ = _build_encode_args("h264", "none", "fast", 0.0, "filter")
        broken = dict(good, encoder="nonexistent_encoder")
        with self.assertRaises(RuntimeError) as cm:
            self._run_segment(broken)
        self.assertIn("nonexistent_encoder", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
