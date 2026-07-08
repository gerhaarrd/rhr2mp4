"""Mixes hit/miss sounds into the map audio at each real hit timestamp
(from sim.hitreg, which recovers the exact in-replay hit moment -- not just
the note's nominal time), so the rendered video sounds like actual gameplay
rather than just the backing track. Music and overlay volumes are scaled
independently in the PCM domain; when a skin ships no hit sound the app's
bundled default (assets/hitsound.wav) is used instead.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import numpy as np

from ..paths import ffmpeg_exe

SAMPLE_RATE = 44100
CHANNELS = 2


def _decode_to_pcm(data: bytes, ext_hint: str) -> np.ndarray:
    with tempfile.TemporaryDirectory(prefix="rhr2mp4_audio_") as td:
        src_path = os.path.join(td, f"in.{ext_hint}")
        with open(src_path, "wb") as f:
            f.write(data)

        cmd = [
            ffmpeg_exe() or "ffmpeg", "-y", "-i", src_path,
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "pipe:1",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg decode failed:\n{proc.stderr.decode(errors='replace')}")

        samples = np.frombuffer(proc.stdout, dtype=np.int16)
        return samples.reshape(-1, CHANNELS)


def _write_wav(path: str, samples: np.ndarray) -> None:
    import wave

    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(samples.astype(np.int16).tobytes())


def default_hit_sound() -> bytes | None:
    """The hit sound bundled with the app (rhr2mp4/assets/hitsound.wav),
    used whenever no skin is loaded or the loaded skin ships no hitSound/*.
    Returns None if the asset is missing (e.g. a stripped-down build)."""
    from ..paths import asset_path

    try:
        with open(asset_path("hitsound.wav"), "rb") as f:
            return f.read()
    except OSError:
        return None


def extract_music_snippet(
    music_bytes: bytes,
    music_ext: str,
    start_ms: float,
    duration_ms: float,
    tempo: float = 1.0,
) -> bytes:
    """WAV bytes of `duration_ms` of playback starting at song time
    `start_ms`, sped up by `tempo` (a 1.45x replay consumes 1.45s of song
    per output second). Decodes only the needed window, so it's fast enough
    for the interactive preview."""
    with tempfile.TemporaryDirectory(prefix="rhr2mp4_snippet_") as td:
        src_path = os.path.join(td, f"in.{music_ext}")
        with open(src_path, "wb") as f:
            f.write(music_bytes)

        cmd = [ffmpeg_exe() or "ffmpeg", "-y",
               "-ss", f"{max(0.0, start_ms) / 1000.0:.3f}",
               "-t", f"{duration_ms * tempo / 1000.0:.3f}",
               "-i", src_path]
        if tempo != 1.0:
            # atempo only accepts 0.5..100 per instance; chain for slower.
            factors = []
            t = tempo
            while t < 0.5:
                factors.append(0.5)
                t /= 0.5
            factors.append(t)
            cmd += ["-filter:a", ",".join(f"atempo={f:.6f}" for f in factors)]
        cmd += ["-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-f", "wav", "pipe:1"]

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg snippet failed:\n{proc.stderr.decode(errors='replace')}")
        return proc.stdout


def mix_audio(
    music_bytes: bytes,
    music_ext: str,
    music_volume_pct: float = 100.0,
    overlays: list[tuple[bytes, list[float], float]] | None = None,
) -> bytes:
    """Returns WAV bytes: the music scaled to `music_volume_pct`, with each
    overlay `(sound_bytes, times_ms, volume_pct)` mixed in at its timestamps
    (hit sounds at real hit moments, miss sounds at note times)."""
    music = _decode_to_pcm(music_bytes, music_ext).astype(np.int32)
    mixed = (music * max(0.0, music_volume_pct) / 100.0).astype(np.int32)
    n_total = len(mixed)

    for sound_bytes, times_ms, volume_pct in overlays or []:
        gain = max(0.0, volume_pct) / 100.0
        if not sound_bytes or gain <= 0.0:
            continue
        sound = (_decode_to_pcm(sound_bytes, "wav").astype(np.int32) * gain).astype(np.int32)
        n_sound = len(sound)
        for t_ms in times_ms:
            start = int(t_ms / 1000.0 * SAMPLE_RATE)
            if start < 0 or start >= n_total:
                continue
            end = min(start + n_sound, n_total)
            length = end - start
            if length <= 0:
                continue
            mixed[start:end] += sound[:length]

    np.clip(mixed, -32768, 32767, out=mixed)

    with tempfile.TemporaryDirectory(prefix="rhr2mp4_audio_out_") as td:
        out_path = os.path.join(td, "mixed.wav")
        _write_wav(out_path, mixed)
        with open(out_path, "rb") as f:
            return f.read()
