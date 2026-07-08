"""Parser for Rhythia .rhr replay files.

Field layout and semantics come from the official `parseRhr` implementation
shipped in rhythia.com's own web bundle (the site parses .rhr client-side
for its "Ghost" replay overlay), so this is no longer reverse-engineered
guesswork. The format is versioned by an int32 date-number header with these
cutoffs (constants named as in the official source):

    V_NEGATE_Y     = 20260118  (before this, frame y is stored negated)
    V_EXTENDED     = 20260125  (adds passed/mods/spin/speed/totalScore)
    V_FAILTIME     = 20260222  (adds failTime)
    V_INT32_TIME   = 20260510  (frame time becomes int32; float32 before)
    V_BEATMAP_HASH = 20260517  (adds beatmapHash)

Cross-checked against yo-ru/rhrParse (C++ parser/encoder for the same
format), which additionally documents the fields the web parser skips: the
int64 after the version is the play *timestamp* (.NET DateTime ticks), and
the fourth float of each frame is the player's *health* (0..1) at that tick.

Layout (little-endian; "string" = 7-bit-varint length prefix + utf-8):
    version        : int32 (date-number, e.g. 20260517)
    timestamp      : int64 (.NET DateTime ticks, 100ns since 0001-01-01 UTC)
    player_name    : string
    map_legacy_id  : string (official parser reads and discards; we keep it
                     -- it matches the .rhm's LegacyId and is useful for
                     sanity-checking the replay/map pairing)
    map_id         : int32
    start_from     : int32
    mode           : string (e.g. "online_profile")
    if version >= V_EXTENDED:
        passed     : bool (byte)
        mods       : string (JSON-ish array, e.g. "[]")
        spin       : bool (byte)
        speed      : float32 (0 treated as 1.0)
        total_score: int64
    accuracy_pct   : float32
    hits           : int32
    misses         : int32
    points         : float32
    if version >= V_FAILTIME:
        fail_time  : int32 (-1 = did not fail)
    if version >= V_BEATMAP_HASH:
        beatmap_hash : string
    frame_count    : int32
    frames         : frame_count records:
        ms         : int32 if version >= V_INT32_TIME else trunc(float32)
        x          : float32
        y          : float32 (negated when version < V_NEGATE_Y)
        health     : float32 (player health 0..1 at this tick)
        important  : uint8 (nonzero on hit-marker frames)
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

V_NEGATE_Y = 20260118
V_EXTENDED = 20260125
V_FAILTIME = 20260222
V_INT32_TIME = 20260510
V_BEATMAP_HASH = 20260517


@dataclass
class Frame:
    ms: int
    x: float
    y: float
    health: float  # 0..1
    important: bool


@dataclass
class Replay:
    version: int
    timestamp_ticks: int  # .NET DateTime ticks (100ns since 0001-01-01 UTC)
    username: str
    map_legacy_id: str
    map_online_id: int
    start_from: int
    mode: str
    passed: bool
    mods: str
    spin: bool
    speed: float
    total_score: int
    accuracy_pct: float
    hits: int
    misses: int
    points: float
    fail_time: int
    beatmap_hash: str
    frames: list[Frame] = field(default_factory=list)

    @property
    def note_count_from_stats(self) -> int:
        return self.hits + self.misses

    @property
    def failed(self) -> bool:
        return self.fail_time >= 0

    @property
    def date_played(self):
        """Play date as a datetime (UTC), decoded from the .NET ticks."""
        from datetime import datetime, timedelta, timezone

        if self.timestamp_ticks <= 0:
            return None
        return datetime(1, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=self.timestamp_ticks / 10)

    @property
    def length_ms(self) -> float:
        return self.frames[-1].ms if self.frames else 0.0

    def health_at(self, ms: float) -> float:
        """Player health (0..1) at an arbitrary time, interpolated between
        the recorded per-tick values."""
        frames = self.frames
        if not frames:
            return 1.0
        if ms <= frames[0].ms:
            return frames[0].health
        if ms >= frames[-1].ms:
            return frames[-1].health

        lo, hi = 0, len(frames) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if frames[mid].ms <= ms:
                lo = mid
            else:
                hi = mid

        a, b = frames[lo], frames[hi]
        if b.ms == a.ms:
            return a.health
        t = (ms - a.ms) / (b.ms - a.ms)
        return a.health + (b.health - a.health) * t

    def cursor_position_at(self, ms: float) -> tuple[float, float]:
        """Linearly interpolate cursor (x, y) at an arbitrary time in ms."""
        frames = self.frames
        if not frames:
            return (1.0, -1.0)
        if ms <= frames[0].ms:
            return (frames[0].x, frames[0].y)
        if ms >= frames[-1].ms:
            return (frames[-1].x, frames[-1].y)

        lo, hi = 0, len(frames) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if frames[mid].ms <= ms:
                lo = mid
            else:
                hi = mid

        a, b = frames[lo], frames[hi]
        if b.ms == a.ms:
            return (a.x, a.y)
        t = (ms - a.ms) / (b.ms - a.ms)
        return (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def bytes(self, n: int) -> bytes:
        v = self.data[self.pos:self.pos + n]
        if len(v) != n:
            raise ValueError(f"rhr: unexpected EOF reading {n} bytes at {self.pos}")
        self.pos += n
        return v

    def u8(self) -> int:
        return self.bytes(1)[0]

    def boolean(self) -> bool:
        return self.u8() != 0

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def string(self) -> str:
        # 7-bit varint length prefix, as in the official parser (a plain
        # 1-byte prefix only coincidentally worked for strings < 128 bytes).
        length = 0
        shift = 0
        while True:
            b = self.u8()
            length |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
            if shift >= 35:
                raise ValueError("rhr: bad string length")
        return self.bytes(length).decode("utf-8")


def load(path: str) -> Replay:
    with open(path, "rb") as f:
        data = f.read()

    r = _Reader(data)

    version = r.i32()
    timestamp_ticks = r.i64()

    username = r.string()
    map_legacy_id = r.string()
    map_id = r.i32()
    start_from = r.i32()
    mode = r.string()

    passed = True
    mods = "[]"
    spin = False
    speed = 0.0
    total_score = 0
    if version >= V_EXTENDED:
        passed = r.boolean()
        mods = r.string()
        spin = r.boolean()
        speed = r.f32()
        total_score = r.i64()

    accuracy_pct = r.f32()
    hits = r.i32()
    misses = r.i32()
    points = r.f32()

    fail_time = -1
    if version >= V_FAILTIME:
        fail_time = r.i32()

    beatmap_hash = ""
    if version >= V_BEATMAP_HASH:
        beatmap_hash = r.string()

    frame_count = r.i32()
    if frame_count < 0:
        raise ValueError("rhr: bad frame count")

    int32_time = version >= V_INT32_TIME
    negate_y = version < V_NEGATE_Y

    frames: list[Frame] = []
    for _ in range(frame_count):
        ms = r.i32() if int32_time else math.trunc(r.f32())
        x = r.f32()
        y = r.f32()
        if negate_y:
            y = -y
        health = r.f32()
        important = r.u8() != 0
        frames.append(Frame(ms=ms, x=x, y=y, health=health, important=important))

    return Replay(
        version=version,
        timestamp_ticks=timestamp_ticks,
        username=username,
        map_legacy_id=map_legacy_id,
        map_online_id=map_id,
        start_from=start_from,
        mode=mode,
        passed=passed,
        mods=mods,
        spin=spin,
        speed=speed if speed > 0 else 1.0,
        total_score=total_score,
        accuracy_pct=accuracy_pct,
        hits=hits,
        misses=misses,
        points=points,
        fail_time=fail_time,
        beatmap_hash=beatmap_hash,
        frames=frames,
    )
