"""Renders a full replay to an .mp4.

Frame rendering (Pillow drawing) is the actual bottleneck -- a single core
only manages ~70fps at 1080p -- so the timeline is split into contiguous
segments, each rendered *and encoded* by its own worker process into its own
temporary .mp4, then all segments are concatenated (stream copy, no
re-encode) and muxed with the audio extracted from the .rhm.

Earlier attempt: a plain multiprocessing.Pool where workers rendered frames
and sent the raw RGB bytes back to the main process to feed a single ffmpeg.
That didn't scale at all (~65-70fps regardless of worker count) because
returning ~6MB/frame through the pool's result queue (pickling + a pipe into
one process) was the actual bottleneck, not the rendering. Giving each worker
its own local ffmpeg process avoids ever moving raw frame bytes across a
process boundary -- only small, already-encoded segment files need to be
combined at the end.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import shutil
import subprocess
import tempfile
import time
from typing import Callable

from ..formats.rhm import Map, MapMetadata, Note
from ..formats.rhr import Replay
from ..formats.rhs import Skin
from ..paths import ffmpeg_exe
from ..sim.hitreg import NoteResult, match_hits
from ..sim.timeline import DEFAULT_APPROACH_RATE, DEFAULT_SPAWN_DISTANCE


def _ffmpeg() -> str:
    return ffmpeg_exe() or "ffmpeg"

ProgressCallback = Callable[[int, int], None]

# Quality presets trade encode speed for file size/quality ("fast" /
# "balanced" / "quality"). Per-encoder args below map each preset onto that
# encoder's own quality knobs. Frame *drawing* (CPU/Pillow) is usually the
# real bottleneck; hardware encoders mostly free CPU for drawing rather
# than speeding encoding itself up.
PRESETS = ("fast", "balanced", "quality")

# encoder key -> (encoder_name, global_args, vf_args, {preset: codec_args})
_VAAPI_DEV = ["-init_hw_device", "vaapi=va:/dev/dri/renderD128", "-filter_hw_device", "va"]
_QSV_DEV = ["-init_hw_device", "qsv=qs"]

ENCODERS = {
    ("h264", "none"): ("libx264", [], [], {
        "fast": ["-preset", "veryfast", "-crf", "20"],
        "balanced": ["-preset", "fast", "-crf", "19"],
        "quality": ["-preset", "medium", "-crf", "18"],
    }),
    ("h264", "nvenc"): ("h264_nvenc", [], [], {
        "fast": ["-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0"],
        "balanced": ["-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
        "quality": ["-preset", "p6", "-rc", "vbr", "-cq", "18", "-b:v", "0"],
    }),
    ("h264", "vaapi"): ("h264_vaapi", _VAAPI_DEV, ["-vf", "format=nv12,hwupload"], {
        "fast": ["-qp", "22"],
        "balanced": ["-qp", "20"],
        "quality": ["-qp", "18"],
    }),
    ("h264", "qsv"): ("h264_qsv", _QSV_DEV, ["-vf", "format=nv12"], {
        "fast": ["-preset", "veryfast", "-global_quality", "22"],
        "balanced": ["-preset", "fast", "-global_quality", "20"],
        "quality": ["-preset", "medium", "-global_quality", "18"],
    }),
    ("hevc", "none"): ("libx265", [], [], {
        "fast": ["-preset", "veryfast", "-crf", "24"],
        "balanced": ["-preset", "fast", "-crf", "22"],
        "quality": ["-preset", "medium", "-crf", "20"],
    }),
    ("hevc", "nvenc"): ("hevc_nvenc", [], [], {
        "fast": ["-preset", "p4", "-rc", "vbr", "-cq", "24", "-b:v", "0"],
        "balanced": ["-preset", "p5", "-rc", "vbr", "-cq", "22", "-b:v", "0"],
        "quality": ["-preset", "p6", "-rc", "vbr", "-cq", "20", "-b:v", "0"],
    }),
    ("hevc", "vaapi"): ("hevc_vaapi", _VAAPI_DEV, ["-vf", "format=nv12,hwupload"], {
        "fast": ["-qp", "25"],
        "balanced": ["-qp", "23"],
        "quality": ["-qp", "21"],
    }),
    ("hevc", "qsv"): ("hevc_qsv", _QSV_DEV, ["-vf", "format=nv12"], {
        "fast": ["-preset", "veryfast", "-global_quality", "25"],
        "balanced": ["-preset", "fast", "-global_quality", "23"],
        "quality": ["-preset", "medium", "-global_quality", "21"],
    }),
    ("av1", "none"): ("libsvtav1", [], [], {
        "fast": ["-preset", "10", "-crf", "30"],
        "balanced": ["-preset", "8", "-crf", "26"],
        "quality": ["-preset", "6", "-crf", "23"],
    }),
    ("av1", "nvenc"): ("av1_nvenc", [], [], {
        "fast": ["-preset", "p4", "-rc", "vbr", "-cq", "30", "-b:v", "0"],
        "balanced": ["-preset", "p5", "-rc", "vbr", "-cq", "26", "-b:v", "0"],
        "quality": ["-preset", "p6", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
    }),
    ("av1", "vaapi"): ("av1_vaapi", _VAAPI_DEV, ["-vf", "format=nv12,hwupload"], {
        "fast": ["-qp", "30"],
        "balanced": ["-qp", "26"],
        "quality": ["-qp", "23"],
    }),
    ("av1", "qsv"): ("av1_qsv", _QSV_DEV, ["-vf", "format=nv12"], {
        "fast": ["-preset", "veryfast", "-global_quality", "30"],
        "balanced": ["-preset", "fast", "-global_quality", "26"],
        "quality": ["-preset", "medium", "-global_quality", "23"],
    }),
}

# Probe order for hw_accel="auto".
_AUTO_ORDER = ("nvenc", "vaapi", "qsv", "none")


def _probe_encoder(codec: str, hw: str) -> bool:
    """Checks that an encoder actually works on this machine (being listed
    by ffmpeg doesn't imply a usable device/driver: e.g. av1_nvenc exists in
    every build but needs an Ada+ GPU) via a tiny test encode."""
    entry = ENCODERS.get((codec, hw))
    if entry is None:
        return False
    encoder, global_args, vf_args, quality_args = entry
    cmd = [
        _ffmpeg(), "-hide_banner", "-v", "error", *global_args,
        "-f", "lavfi", "-i", "color=black:s=320x240:d=0.2:r=30",
        *vf_args, "-c:v", encoder, *quality_args["fast"],
        "-frames:v", "3", "-f", "null", "-",
    ]
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15).returncode == 0
    except Exception:
        return False


_probe_cache: dict[tuple[str, str], bool] = {}


def encoder_available(codec: str, hw: str) -> bool:
    key = (codec, hw)
    if key not in _probe_cache:
        _probe_cache[key] = _probe_encoder(codec, hw)
    return _probe_cache[key]


def resolve_encoder(codec: str, hw_accel: str) -> tuple[str, str]:
    """Resolves ("hevc", "auto") into a concrete working (hw, encoder_name),
    falling back to software when the requested hardware isn't usable."""
    if hw_accel == "auto":
        for hw in _AUTO_ORDER:
            if encoder_available(codec, hw):
                return hw, ENCODERS[(codec, hw)][0]
        return "none", ENCODERS[(codec, "none")][0]
    if hw_accel != "none" and not encoder_available(codec, hw_accel):
        return "none", ENCODERS[(codec, "none")][0]
    return hw_accel, ENCODERS[(codec, hw_accel)][0]


PROGRESS_REPORT_EVERY = 15  # frames between progress pings from a worker

INTRO_DURATION_S = 2.5


def _resolve_runtime(skin, parallax_enabled, trail_enabled, background_dots_enabled,
                     trail_scale, hit_effects_enabled, note_colors, color_overrides,
                     hud_overrides=None):
    """Resolves the skin and applies every GUI/CLI visual override; shared
    by the segment workers and the intro renderer so they stay in sync.
    `hud_overrides` maps RuntimeSkin flag names (song_info_enabled,
    right_panel_points_enabled, ...) to explicit booleans."""
    from .skin_runtime import resolve as resolve_skin

    runtime_skin = resolve_skin(skin)
    if hud_overrides:
        for key, value in hud_overrides.items():
            setattr(runtime_skin, key, value)
    if not parallax_enabled:
        runtime_skin.parallax = 0.0
    if not trail_enabled:
        runtime_skin.cursor_trail_enabled = False
    runtime_skin.ambient_dots_enabled = background_dots_enabled
    runtime_skin.cursor_trail_scale = trail_scale
    runtime_skin.hit_effects_enabled = hit_effects_enabled
    if note_colors:
        runtime_skin.note_colors = list(note_colors)
    if color_overrides:
        # GUI color presets: override the resolved skin's colors. Applied
        # before build_context so cursor/trail sprite tints pick them up.
        if "note_colors" in color_overrides:
            runtime_skin.note_colors = list(color_overrides["note_colors"])
        if "cursor" in color_overrides:
            runtime_skin.cursor_color = tuple(color_overrides["cursor"])
        if "trail" in color_overrides:
            runtime_skin.cursor_trail_color = tuple(color_overrides["trail"])
        if "border" in color_overrides:
            runtime_skin.border_color = tuple(color_overrides["border"])
    return runtime_skin


def _detect_audio_ext(data: bytes) -> str:
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF":
        return "wav"
    return "mp3"


def default_worker_count() -> int:
    # Each worker is CPU-bound (Python drawing + its own single-threaded
    # ffmpeg encoder). Benchmarking on an 8-core/16-thread machine showed
    # throughput plateauing around 1.3-1.6x realtime somewhere between 4 and
    # 16 workers -- going beyond ~6 workers bought essentially nothing
    # (likely memory-bandwidth/cache contention across processes doing
    # heavy image compositing at once, not a core-count limit). Cap at 6 as
    # a default that captures the gain without spawning more processes than
    # useful.
    n = os.cpu_count() or 4
    return max(1, min(n, 6))


def _render_segment(
    segment_index: int,
    frame_times: list[float],
    width: int,
    height: int,
    fps: int,
    encode_args: dict,
    cover_bytes: bytes,
    notes: list[Note],
    results: list[NoteResult],
    replay: Replay,
    map_meta: MapMetadata,
    segment_path: str,
    progress_queue: mp.Queue,
    spawn_distance: float,
    approach_rate: float,
    skin: Skin | None,
    parallax_enabled: bool,
    note_colors: list[tuple[int, int, int]] | None,
    ghost: bool,
    chaos: bool,
    playfield_extent: float,
    mods_label: str,
    trail_enabled: bool,
    color_overrides: dict | None,
    background_dots_enabled: bool,
    trail_scale: float,
    hit_effects_enabled: bool,
    hud_overrides: dict | None,
    motion_blur: float,
    motion_blur_mode: str,
    warmup_frames: int,
    frame_dt_ms: float,
):
    # Imported inside the worker so a 'spawn'ed process only pays for these
    # (and builds its GL-free Pillow context) once, not at pool-creation time.
    from .frame import build_context, draw_frame, draw_frame_blurred
    from ..sim.timeline import Timeline

    runtime_skin = _resolve_runtime(skin, parallax_enabled, trail_enabled,
                                    background_dots_enabled, trail_scale,
                                    hit_effects_enabled, note_colors, color_overrides,
                                    hud_overrides)
    ctx = build_context(width, height, cover_bytes, runtime_skin, playfield_extent=playfield_extent)
    ctx.mods_label = mods_label
    timeline = Timeline(notes, results, replay, spawn_distance=spawn_distance, approach_rate=approach_rate,
                        ghost=ghost, chaos=chaos)

    cmd = [
        _ffmpeg(), "-y", *encode_args["global"],
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        *encode_args["vf"],
        "-c:v", encode_args["encoder"], *encode_args["codec"],
        *encode_args["tail"],
        segment_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # Motion blur (tmix in the ffmpeg command) mixes each output frame with
    # the previous ones, so a segment's first frames would be less blurred
    # than the same frames rendered mid-segment -- a visible seam at segment
    # joins. Feed a few warm-up frames from just before the segment; the
    # filter chain's trim drops them again after tmix.
    warmup_times = [max(0.0, frame_times[0] - j * frame_dt_ms) for j in range(warmup_frames, 0, -1)]
    subframe_blur = motion_blur if motion_blur > 0 and motion_blur_mode == "subframe" else 0.0

    try:
        for i, t in enumerate(warmup_times + frame_times):
            if subframe_blur > 0:
                img = draw_frame_blurred(ctx, timeline, map_meta, replay, t, frame_dt_ms, subframe_blur)
            else:
                img = draw_frame(ctx, timeline.state_at(t), map_meta, replay)
            proc.stdin.write(img.tobytes())
            done = i + 1 - len(warmup_times)
            if done > 0 and done % PROGRESS_REPORT_EVERY == 0:
                progress_queue.put((segment_index, done))
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        stderr = proc.stderr.read()
        ret = proc.wait()

    progress_queue.put((segment_index, len(frame_times)))

    if ret != 0:
        raise RuntimeError(f"segment {segment_index} ffmpeg failed (code {ret}):\n{stderr.decode(errors='replace')}")


def _render_intro(
    intro_path: str,
    width: int, height: int, fps: int,
    encode_args: dict, enc_vf: list,
    map_: Map, replay: Replay, skin: Skin | None,
    parallax_enabled: bool, trail_enabled: bool, background_dots_enabled: bool,
    trail_scale: float, hit_effects_enabled: bool, hud_overrides: dict | None,
    note_colors, color_overrides, playfield_extent: float,
    cancel_cb,
) -> int:
    """Encodes the 2.5s intro card as its own segment file (placed first in
    the concat list). Runs in the main process -- it's only ~fps*2.5 frames
    of a static image with a fade. Returns the frame count, or -1 when
    cancelled mid-way."""
    import numpy as np

    from .frame import build_context, draw_intro_card, intro_frame_brightness

    runtime_skin = _resolve_runtime(skin, parallax_enabled, trail_enabled,
                                    background_dots_enabled, trail_scale,
                                    hit_effects_enabled, note_colors, color_overrides,
                                    hud_overrides)
    ctx = build_context(width, height, map_.cover_bytes, runtime_skin, playfield_extent=playfield_extent)
    card = np.asarray(draw_intro_card(ctx, map_.metadata, replay, map_.cover_bytes), dtype=np.float32)

    n_frames = max(1, int(INTRO_DURATION_S * fps))
    # The card is static, so the motion-blur chain is pointless here (and its
    # trim would eat frames) -- encode with the encoder's own vf only.
    intro_args = {**encode_args, "vf": list(enc_vf)}
    cmd = [
        _ffmpeg(), "-y", *intro_args["global"],
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        *intro_args["vf"],
        "-c:v", intro_args["encoder"], *intro_args["codec"],
        *intro_args["tail"],
        intro_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    cancelled = False
    try:
        for i in range(n_frames):
            if cancel_cb is not None and cancel_cb():
                cancelled = True
                break
            brightness = intro_frame_brightness(i / n_frames)
            proc.stdin.write((card * brightness).astype(np.uint8).tobytes())
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        stderr = proc.stderr.read()
        ret = proc.wait()

    if cancelled:
        return -1
    if ret != 0:
        raise RuntimeError(f"intro ffmpeg failed (code {ret}):\n{stderr.decode(errors='replace')}")
    return n_frames


def _split_into_segments(frame_times: list[float], num_segments: int) -> list[list[float]]:
    n = len(frame_times)
    num_segments = max(1, min(num_segments, n))
    base, extra = divmod(n, num_segments)
    segments = []
    start = 0
    for i in range(num_segments):
        size = base + (1 if i < extra else 0)
        segments.append(frame_times[start:start + size])
        start += size
    return [s for s in segments if s]


def render_video(
    map_: Map,
    replay: Replay,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
    quality: str = "fast",
    workers: int | None = None,
    spawn_distance: float = DEFAULT_SPAWN_DISTANCE,
    approach_rate: float = DEFAULT_APPROACH_RATE,
    skin: Skin | None = None,
    parallax_enabled: bool = True,
    trail_enabled: bool = True,
    note_colors: list[tuple[int, int, int]] | None = None,
    video_codec: str = "h264",  # "h264" | "hevc" | "av1"
    hw_accel: str = "auto",  # "auto" | "nvenc" | "vaapi" | "qsv" | "none"
    audio_bitrate: str = "192k",
    color_overrides: dict | None = None,
    background_dots_enabled: bool = True,
    trail_scale: float = 1.0,
    motion_blur: float = 0.0,  # 0 = off; 0..1 = blur intensity
    motion_blur_mode: str = "filter",  # "filter" (ffmpeg tmix, free) | "subframe" (4× drawing, physical blur)
    clip_start_ms: float | None = None,  # song-time bounds; None = full replay
    clip_end_ms: float | None = None,
    intro_enabled: bool = False,  # 2.5s cover/stats card before gameplay
    hit_effects_enabled: bool = True,
    music_volume: float = 100.0,  # % applied to the map audio (100 = unchanged)
    hit_sound_volume: float = 100.0,  # % on top of the skin's own HitSoundVolume
    hud_overrides: dict | None = None,  # RuntimeSkin flag name -> bool (hide/show HUD elements)
    progress_cb: ProgressCallback | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> None:
    if ffmpeg_exe() is None:
        raise RuntimeError("ffmpeg not found (neither bundled nor on PATH). Install ffmpeg to render video.")

    if quality not in PRESETS:
        raise ValueError(f"unknown quality preset {quality!r}, choose one of {list(PRESETS)}")
    if video_codec not in ("h264", "hevc", "av1"):
        raise ValueError(f"unknown video codec {video_codec!r}")

    hw, encoder_name = resolve_encoder(video_codec, hw_accel)
    _, enc_global, enc_vf, enc_quality = ENCODERS[(video_codec, hw)]
    tail = []
    if hw == "none":
        # Keep software encoders single-threaded: parallelism comes from the
        # segment workers, and n_workers * n_threads oversubscribes the CPU.
        tail += ["-threads", "1"]
    if not enc_vf:
        tail += ["-pix_fmt", "yuv420p"]
    if video_codec == "hevc":
        # hvc1 tag so the .mp4 plays in Apple/QuickTime players too.
        tail += ["-tag:v", "hvc1"]

    # Fast motion-blur mode: applied by ffmpeg itself (essentially free)
    # instead of drawing sub-frames in Python (the "subframe" mode, which
    # multiplies the real bottleneck, Pillow drawing): tmix blends each
    # frame with the previous 3, weighted so the current frame dominates at
    # low intensity and the blend approaches a uniform 4-frame average at
    # 100%. The trim drops the per-segment warm-up frames (see
    # _render_segment) so segment joins blur identically to mid-segment
    # frames.
    warmup_frames = 0
    vf = list(enc_vf)
    if motion_blur > 0 and motion_blur_mode == "filter":
        d = max(0.05, min(1.0, motion_blur))
        weights = " ".join(f"{d ** e:.4f}" for e in (3, 2, 1, 0))  # oldest -> newest
        warmup_frames = 3
        blur_chain = (
            f"tmix=frames=4:weights='{weights}',"
            f"trim=start_frame={warmup_frames},setpts=PTS-STARTPTS"
        )
        # Blur first, then any encoder-required format/hwupload conversion.
        vf = ["-vf", f"{blur_chain},{enc_vf[1]}" if enc_vf else blur_chain]

    encode_args = {
        "encoder": encoder_name,
        "global": list(enc_global),
        "vf": vf,
        "codec": list(enc_quality[quality]),
        "tail": tail,
    }

    if approach_rate <= 0:
        raise ValueError("approach_rate must be > 0")
    if spawn_distance <= 0:
        raise ValueError("spawn_distance must be > 0")

    # Gameplay mods recorded in the replay: mirroring is applied to the note
    # list itself (axis recovered from the cursor path -- see sim/mods.py);
    # ghost/chaos/hardrock become render flags.
    from ..sim.mods import resolve_mods

    notes, ghost, chaos, playfield_extent, mods_label = resolve_mods(map_.notes, replay)

    results = match_hits(notes, replay.frames)

    # The replay stores the speed modifier it was played at (1.0 = normal),
    # but its frame timestamps -- and the map's note times -- are in *song*
    # time, not wall time (a 1.45x replay of a 31.6s map still spans ~31.6s
    # of timestamps). To reproduce the run as it was actually played, each
    # output frame at wall time r samples the timeline at song time
    # r * speed, and the audio is sped up to match below.
    speed = replay.speed if replay.speed and replay.speed > 0 else 1.0

    # Optional clip: render only [clip_start_ms, clip_end_ms] (song time).
    start_ms = max(0.0, min(clip_start_ms or 0.0, replay.length_ms))
    end_ms = max(0.0, min(clip_end_ms if clip_end_ms is not None else replay.length_ms, replay.length_ms))
    if end_ms <= start_ms:
        raise ValueError("clip end must be after clip start")

    total_frames = max(1, int((end_ms - start_ms) / speed / 1000.0 * fps) + 1)
    frame_dt_ms = 1000.0 / fps * speed  # frame spacing in song time
    frame_times = [start_ms + i * frame_dt_ms for i in range(total_frames)]

    audio_ext = _detect_audio_ext(map_.audio_bytes)
    num_workers = workers or default_worker_count()
    segments = _split_into_segments(frame_times, num_workers)

    audio_bytes = map_.audio_bytes
    from .audio import default_hit_sound, mix_audio

    # The user-facing hit sound volume scales the skin's own HitSoundVolume
    # (100% = exactly what the skin author intended). Skins without a
    # bundled hit sound fall back to the app's default one.
    hit_bytes = skin.hit_sound_bytes if (skin is not None and skin.hit_sound_bytes) else default_hit_sound()
    skin_hit_volume = skin.hit_sound_volume if skin is not None else 100.0
    effective_hit_volume = skin_hit_volume * hit_sound_volume / 100.0

    overlays: list[tuple[bytes, list[float], float]] = []
    if hit_bytes and effective_hit_volume > 0:
        hit_times_ms = [r.hit_ms for r in results if r.hit and r.hit_ms is not None]
        overlays.append((hit_bytes, hit_times_ms, effective_hit_volume))

    if skin is not None and skin.miss_sound_bytes and effective_hit_volume > 0:
        # A miss has no exact "moment" the way a hit does (no frame flag
        # fires for it -- see sim/hitreg.py), so the note's own nominal
        # timestamp is the closest approximation. MissSoundComboThreshold
        # means "only play the miss sound if the combo being broken was at
        # least this long", so quiet/early misses don't spam the track.
        miss_times_ms = []
        combo = 0
        for note, res in zip(notes, results):
            if res.hit:
                combo += 1
            else:
                if combo >= skin.miss_sound_combo_threshold:
                    miss_times_ms.append(note.time_ms)
                combo = 0
        overlays.append((skin.miss_sound_bytes, miss_times_ms, effective_hit_volume))

    if overlays or music_volume != 100.0:
        audio_bytes = mix_audio(audio_bytes, audio_ext, music_volume, overlays)
        audio_ext = "wav"

    with tempfile.TemporaryDirectory(prefix="rhr2mp4_") as td:
        audio_path = os.path.join(td, f"audio.{audio_ext}")
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        segment_paths = [os.path.join(td, f"seg_{i:04d}.mp4") for i in range(len(segments))]

        intro_path = None
        intro_frames = 0
        if intro_enabled:
            intro_path = os.path.join(td, "intro.mp4")
            intro_frames = _render_intro(
                intro_path, width, height, fps, encode_args, enc_vf,
                map_, replay, skin, parallax_enabled, trail_enabled,
                background_dots_enabled, trail_scale, hit_effects_enabled, hud_overrides,
                note_colors, color_overrides, playfield_extent, cancel_cb,
            )
            if intro_frames < 0:
                return  # cancelled during the intro
        total_report = total_frames + intro_frames
        if progress_cb and intro_frames:
            progress_cb(intro_frames, total_report)

        ctx = mp.get_context("spawn")
        # A plain ctx.Queue() can only be handed to workers via inheritance
        # at process-creation time, not passed as a regular call argument
        # (multiprocessing raises "Queue objects should only be shared
        # between processes through inheritance"). A Manager().Queue() is a
        # proxy that *is* picklable as an ordinary argument, at the cost of
        # going through the manager's server process -- fine here since
        # progress messages are infrequent (every PROGRESS_REPORT_EVERY frames).
        manager = ctx.Manager()
        progress_queue = manager.Queue()
        pool = ctx.Pool(processes=len(segments))

        async_results = [
            pool.apply_async(
                _render_segment,
                args=(
                    i, seg, width, height, fps, encode_args,
                    map_.cover_bytes, notes, results, replay, map_.metadata,
                    segment_paths[i], progress_queue, spawn_distance, approach_rate, skin,
                    parallax_enabled, note_colors, ghost, chaos, playfield_extent, mods_label,
                    trail_enabled, color_overrides,
                    background_dots_enabled, trail_scale, hit_effects_enabled, hud_overrides,
                    motion_blur, motion_blur_mode, warmup_frames, frame_dt_ms,
                ),
            )
            for i, seg in enumerate(segments)
        ]
        pool.close()

        try:
            done_per_segment = [0] * len(segments)
            cancelled = False
            while True:
                try:
                    seg_idx, count = progress_queue.get(timeout=0.2)
                    done_per_segment[seg_idx] = count
                    if progress_cb:
                        progress_cb(intro_frames + sum(done_per_segment), total_report)
                except Exception:
                    pass

                if cancel_cb is not None and cancel_cb():
                    cancelled = True
                    break

                if all(r.ready() for r in async_results):
                    # drain any last progress messages
                    while not progress_queue.empty():
                        seg_idx, count = progress_queue.get_nowait()
                        done_per_segment[seg_idx] = count
                    if progress_cb:
                        progress_cb(intro_frames + sum(done_per_segment), total_report)
                    break

            if cancelled:
                pool.terminate()
                pool.join()
                return

            pool.join()
            for r in async_results:
                r.get()  # re-raise any worker exception
        except BaseException:
            pool.terminate()
            pool.join()
            raise
        finally:
            manager.shutdown()

        concat_list_path = os.path.join(td, "concat.txt")
        with open(concat_list_path, "w") as f:
            for p in ([intro_path] if intro_path else []) + segment_paths:
                f.write(f"file '{p}'\n")

        video_only_path = os.path.join(td, "video_only.mp4")
        concat_cmd = [
            _ffmpeg(), "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path, "-c", "copy", video_only_path,
        ]
        proc = subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed:\n{proc.stderr.decode(errors='replace')}")

        if not audio_bytes:
            # Maps can legitimately ship without audio -- output video-only.
            mux_cmd = [
                _ffmpeg(), "-y", "-i", video_only_path,
                "-c:v", "copy", "-movflags", "+faststart", output_path,
            ]
        else:
            # Clip: seek the audio to the clip start. Song time maps 1:1 to
            # the original audio's own timeline (the speed modifier is
            # applied below via asetrate), so no speed division here.
            audio_seek = ["-ss", f"{start_ms / 1000.0:.3f}"] if start_ms > 0 else []
            mux_cmd = [
                _ffmpeg(), "-y",
                "-i", video_only_path,
                *audio_seek, "-i", audio_path,
                "-c:v", "copy",
            ]
            afilters = []
            if abs(speed - 1.0) > 1e-3:
                # Speed the audio up/down the way the game's speed modifier
                # does: resampling (pitch shifts along with tempo), not a
                # pitch-preserving atempo stretch. Normalize to 48kHz first
                # so the asetrate factor is exact regardless of the source's
                # rate.
                afilters.append(f"aresample=48000,asetrate=48000*{speed},aresample=48000")
            if intro_frames > 0:
                # Silence under the intro card; delay applied after the speed
                # change so it's exact in output (wall) time.
                afilters.append(f"adelay={int(intro_frames / fps * 1000)}:all=1")
            if afilters:
                mux_cmd += ["-filter:a", ",".join(afilters)]
            mux_cmd += [
                "-c:a", "aac", "-b:a", audio_bitrate,
                "-shortest",
                "-movflags", "+faststart",
                output_path,
            ]
        proc = subprocess.run(mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg mux failed:\n{proc.stderr.decode(errors='replace')}")
