"""Parser for Sound Space Plus .sspm map files (versions 1 and 2).

Rhythia grew out of Sound Space Plus and imports .sspm maps directly, so
supporting them here means a replay can be rendered against the original
map file without converting it to .rhm first. Both formats share the same
note model: a time in ms plus (x, y) on the 0..2 grid (y = 0 is the top
row; "quantum" notes use float coordinates off the grid). Parsing returns
the same `Map` dataclass as formats/rhm.py, so the rest of the pipeline
doesn't care which format the map came from.

Layout follows the community spec (basils-garden/types, sspm/v1.md and
sspm/v2.md), validated byte-for-byte against real files. All values are
little-endian.

v1 ("SS+m", u16 version = 1, u16 reserved):
    map id / map name / creators : newline-terminated UTF-8 strings
    last note ms   : u32
    note count     : u32
    difficulty     : u8 (0..5 = N/A, Easy, Medium, Hard, Logic, Tasukete)
    cover          : u8 type (0 = none, 2 = png) + u64 length + bytes
    audio          : u8 type (0 = none, 1 = stored) + u64 length + bytes
    notes          : note count x { u32 ms; u8 quantum; x, y as u8 pairs
                     (quantum = 0) or f32 pairs (quantum = 1) }

v2 ("SS+m", u16 version = 2, u32 reserved):
    sha1 of marker data : 20 bytes
    last marker ms / note count / marker count : u32 each
    difficulty     : u8
    star rating    : u16
    has audio / has cover / requires mod : u8 each
    5 x (offset u64, length u64) pointers: custom data, audio, cover,
        marker definitions, markers
    map id / map name / song name : u16-length-prefixed UTF-8 strings
    mapper count   : u16, then that many strings
    custom data    : u16 field count x { name string; u8 type; value }
                     (the "difficulty_name" string field is used in-game)
    marker definitions : u8 count x { name string; u8 value count;
                     that many u8 type ids; u8 0x00 terminator }
    markers        : { u32 ms; u8 definition index; values } -- notes are
                     the markers whose definition is named "ssp_note",
                     holding a single position value (type 0x07: u8 quantum
                     flag, then u8 or f32 coordinate pairs, same as v1)
"""

from __future__ import annotations

import struct

from .rhm import Map, MapMetadata, Note

SIGNATURE = b"SS+m"


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def bytes(self, n: int) -> bytes:
        v = self.data[self.pos:self.pos + n]
        if len(v) != n:
            raise ValueError(f"sspm: unexpected EOF reading {n} bytes at {self.pos}")
        self.pos += n
        return v

    def u8(self) -> int:
        return self.bytes(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.bytes(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.bytes(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.bytes(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.bytes(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.bytes(8))[0]

    def string16(self) -> str:
        return self.bytes(self.u16()).decode("utf-8", errors="replace")

    def line(self) -> str:
        """v1 newline-terminated string."""
        end = self.data.find(b"\x0a", self.pos)
        if end < 0:
            raise ValueError("sspm: unterminated v1 string")
        v = self.data[self.pos:end].decode("utf-8", errors="replace")
        self.pos = end + 1
        return v

    def position(self) -> tuple[float, float]:
        """Data type 0x07: quantum flag + (u8, u8) or (f32, f32)."""
        if self.u8():
            return (self.f32(), self.f32())
        return (float(self.u8()), float(self.u8()))

    def skip_value(self, type_id: int) -> None:
        """Consumes one marker value of the given data type."""
        if type_id in (0x01,):
            self.pos += 1
        elif type_id == 0x02:
            self.pos += 2
        elif type_id in (0x03, 0x05):
            self.pos += 4
        elif type_id in (0x04, 0x06):
            self.pos += 8
        elif type_id == 0x07:
            self.position()
        elif type_id in (0x08, 0x09):
            self.pos += self.u16()
        elif type_id in (0x0A, 0x0B):
            self.pos += self.u32()
        elif type_id == 0x0C:
            sub = self.u8()
            for _ in range(self.u16()):
                self.skip_value(sub)
        elif type_id != 0x00:
            raise ValueError(f"sspm: unknown data type 0x{type_id:02x}")


def _metadata(map_id: str, title: str, song_name: str, mappers: list[str],
              duration_ms: int, difficulty: int, custom_difficulty_name: str = "",
              star_rating: float = 0.0) -> MapMetadata:
    # .sspm has no Rhythia OnlineId; the map id doubles as the LegacyId,
    # which is exactly what replays reference for imported maps.
    return MapMetadata(
        online_id=0,
        online_status="",
        legacy_id=map_id,
        song_name=song_name,
        mappers=mappers,
        title=title,
        duration_ms=duration_ms,
        difficulty=difficulty,
        custom_difficulty_name=custom_difficulty_name,
        star_rating=star_rating,
    )


def _parse_v1(r: _Reader, with_media: bool) -> Map:
    map_id = r.line()
    map_name = r.line()
    creators = r.line()
    last_ms = r.u32()
    note_count = r.u32()
    difficulty = r.u8()

    cover_bytes = b""
    cover_type = r.u8()
    if cover_type != 0x00:
        data = r.bytes(r.u64())
        if cover_type == 0x02:  # 0x01 was a deprecated raw-pixel format
            cover_bytes = data

    audio_bytes = b""
    if r.u8() != 0x00:
        audio_bytes = r.bytes(r.u64())

    notes: list[Note] = []
    for _ in range(note_count):
        ms = r.u32()
        x, y = r.position()
        notes.append(Note(time_ms=ms, x=x, y=y))
    notes.sort(key=lambda n: n.time_ms)

    mappers = [m.strip() for m in creators.replace(" & ", ", ").split(",") if m.strip()]
    metadata = _metadata(map_id, map_name, map_name, mappers, last_ms, difficulty)
    if not with_media:
        audio_bytes = cover_bytes = b""
    return Map(metadata=metadata, notes=notes, audio_bytes=audio_bytes, cover_bytes=cover_bytes)


def _parse_v2(r: _Reader, with_media: bool) -> Map:
    data = r.data
    r.bytes(20)  # sha1 of the marker data (unverified; often zeroed)
    last_ms = r.u32()
    r.u32()  # note count (recomputed from the markers below)
    r.u32()  # total marker count
    difficulty = r.u8()
    star_rating = float(r.u16())
    r.bytes(3)  # has audio / has cover / requires mod flags

    offsets = [(r.u64(), r.u64()) for _ in range(5)]
    (custom_off, custom_len), (audio_off, audio_len), (cover_off, cover_len), \
        (defs_off, defs_len), (markers_off, markers_len) = offsets

    map_id = r.string16()
    map_name = r.string16()
    song_name = r.string16()
    mappers = [r.string16() for _ in range(r.u16())]

    custom_difficulty_name = ""
    if custom_len:
        c = _Reader(data[custom_off:custom_off + custom_len])
        for _ in range(c.u16()):
            name = c.string16()
            type_id = c.u8()
            if name == "difficulty_name" and type_id == 0x09:
                custom_difficulty_name = c.string16()
            else:
                c.skip_value(type_id)

    # Marker definitions: which value types each marker kind carries. Notes
    # are the "ssp_note" markers; everything else is skipped by size.
    d = _Reader(data[defs_off:defs_off + defs_len])
    definitions: list[tuple[str, list[int]]] = []
    for _ in range(d.u8()):
        name = d.string16()
        types = [d.u8() for _ in range(d.u8())]
        if d.u8() != 0x00:
            raise ValueError("sspm: malformed marker definition")
        definitions.append((name, types))

    notes: list[Note] = []
    m = _Reader(data[markers_off:markers_off + markers_len])
    while m.pos < len(m.data):
        ms = m.u32()
        def_index = m.u8()
        if def_index >= len(definitions):
            raise ValueError(f"sspm: marker references unknown definition {def_index}")
        name, types = definitions[def_index]
        if name == "ssp_note" and types == [0x07]:
            x, y = m.position()
            notes.append(Note(time_ms=ms, x=x, y=y))
        else:
            for t in types:
                m.skip_value(t)
    notes.sort(key=lambda n: n.time_ms)

    audio_bytes = data[audio_off:audio_off + audio_len] if (with_media and audio_len) else b""
    cover_bytes = data[cover_off:cover_off + cover_len] if (with_media and cover_len) else b""

    metadata = _metadata(map_id, map_name, song_name or map_name, mappers,
                         last_ms, difficulty, custom_difficulty_name, star_rating)
    return Map(metadata=metadata, notes=notes, audio_bytes=audio_bytes, cover_bytes=cover_bytes)


def _parse(data: bytes, with_media: bool) -> Map:
    r = _Reader(data)
    if r.bytes(4) != SIGNATURE:
        raise ValueError("sspm: not an SS+m file (bad signature)")
    version = r.u16()
    if version == 1:
        r.bytes(2)  # reserved
        return _parse_v1(r, with_media)
    if version == 2:
        r.bytes(4)  # reserved
        return _parse_v2(r, with_media)
    raise ValueError(f"sspm: unsupported version {version}")


def load(path: str) -> Map:
    with open(path, "rb") as f:
        return _parse(f.read(), with_media=True)


def read_meta(path: str) -> Map:
    """The map without its audio/cover payloads -- used by the auto-detect
    scan (formats/locate.py), which only needs ids, names and note counts.
    (v2 metadata lives in the header so this is cheap; v1 files still have
    to be read through since the note stream sits after the media blobs.)"""
    with open(path, "rb") as f:
        return _parse(f.read(), with_media=False)
