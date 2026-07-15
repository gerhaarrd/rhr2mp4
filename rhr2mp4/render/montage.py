"""Automatic highlight reel: renders the best moment of several replays and
joins the clips with styled crossfade transitions (ffmpeg xfade/acrossfade).

Each item is rendered as a normal clip through render_video (so every visual
option -- skin, colors, HUD layout, effects -- applies to montage clips too),
then the finished mp4 clips are chained in a single xfade filter graph and
re-encoded once. All clips must share the same resolution/fps, which
render_montage guarantees by rendering them itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

from ..formats.rhm import Map
from ..formats.rhr import Replay
from .video import ENCODERS, _ffmpeg, _probe_duration_s, render_video

# The xfade transitions exposed to users (ffmpeg supports many more; these
# read well on gameplay footage).
TRANSITIONS = ("fade", "wipeleft", "wiperight", "slideleft", "slideright",
               "circleopen", "circleclose", "dissolve", "pixelize", "radial",
               "hblur", "smoothleft", "smoothright")


@dataclass
class MontageItem:
    map_: Map
    replay: Replay
    clip_start_ms: float
    clip_end_ms: float


def render_montage(
    items: list[MontageItem],
    output_path: str,
    *,
    transition: str = "fade",
    transition_s: float = 0.7,
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
    quality: str = "fast",
    video_codec: str = "h264",
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    render_kwargs: dict | None = None,
) -> None:
    """Renders every item's clip and joins them into `output_path` (.mp4).

    `render_kwargs` is forwarded to render_video for each clip (skin, colors,
    HUD, effects...). Progress is reported over the summed frame counts of
    all clips; the final join pass is quick by comparison."""
    if not items:
        raise ValueError("montage needs at least one clip")
    if transition not in TRANSITIONS:
        raise ValueError(f"unknown transition {transition!r}, choose one of {list(TRANSITIONS)}")

    kwargs = dict(render_kwargs or {})
    for banned in ("clip_start_ms", "clip_end_ms", "width", "height", "fps",
                   "quality", "video_codec", "progress_cb", "cancel_cb", "intro_enabled"):
        kwargs.pop(banned, None)

    # Frame totals per clip, for one aggregated progress bar.
    def _clip_frames(item: MontageItem) -> int:
        speed = item.replay.speed if item.replay.speed and item.replay.speed > 0 else 1.0
        return max(1, int((item.clip_end_ms - item.clip_start_ms) / speed / 1000.0 * fps) + 1)

    totals = [_clip_frames(it) for it in items]
    grand_total = sum(totals)

    with tempfile.TemporaryDirectory(prefix="rhr2mp4_montage_") as td:
        clip_paths: list[str] = []
        done_before = 0
        for i, item in enumerate(items):
            if cancel_cb is not None and cancel_cb():
                return
            clip_path = os.path.join(td, f"clip_{i:03d}.mp4")

            def _clip_progress(done: int, total: int, _base=done_before):
                if progress_cb:
                    progress_cb(min(_base + done, grand_total), grand_total)

            render_video(
                item.map_, item.replay, clip_path,
                width=width, height=height, fps=fps, quality=quality,
                video_codec=video_codec,
                clip_start_ms=item.clip_start_ms, clip_end_ms=item.clip_end_ms,
                progress_cb=_clip_progress, cancel_cb=cancel_cb,
                **kwargs,
            )
            if cancel_cb is not None and cancel_cb():
                return
            clip_paths.append(clip_path)
            done_before += totals[i]

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if len(clip_paths) == 1:
            shutil.copyfile(clip_paths[0], output_path)
            return

        # Maps can ship without audio; a clip rendered from one has no audio
        # stream, and one missing stream breaks the whole acrossfade chain.
        has_audio = all(bool(it.map_.audio_bytes) for it in items)
        _join_with_transitions(clip_paths, output_path, transition, transition_s,
                               quality, video_codec, has_audio)


def _join_with_transitions(clip_paths: list[str], output_path: str,
                           transition: str, transition_s: float,
                           quality: str, video_codec: str, has_audio: bool) -> None:
    durations = [_probe_duration_s(p) for p in clip_paths]
    # A transition eats transition_s off the junction; clips shorter than
    # two transitions would produce negative offsets, so clamp per junction.
    parts: list[str] = []
    if has_audio:
        # Normalize the audio rates first: acrossfade requires matching
        # formats and different maps' songs ship at different sample rates.
        for i in range(len(clip_paths)):
            parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}n]")

    last_v = "[0:v]"
    last_a = "[a0n]"
    cum = durations[0]
    for i in range(1, len(clip_paths)):
        ts = max(0.1, min(transition_s, durations[i - 1] / 2, durations[i] / 2))
        offset = max(0.0, cum - ts)
        parts.append(f"{last_v}[{i}:v]xfade=transition={transition}:"
                     f"duration={ts:.3f}:offset={offset:.3f}[v{i}]")
        if has_audio:
            parts.append(f"{last_a}[a{i}n]acrossfade=d={ts:.3f}[ac{i}]")
            last_a = f"[ac{i}]"
        last_v = f"[v{i}]"
        cum = offset + durations[i]

    parts.append(f"{last_v}format=yuv420p[vout]")

    enc_name, _, _, enc_quality = ENCODERS[(video_codec, "none")]
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    for p in clip_paths:
        cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", last_a]
    cmd += ["-c:v", enc_name, *enc_quality[quality]]
    if video_codec == "hevc":
        cmd += ["-tag:v", "hvc1"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", output_path]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"montage join failed:\n{proc.stderr.decode(errors='replace')}")
