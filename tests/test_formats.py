"""Parser round-trips and map lookup, all on synthetic files.

Run with:  ./.venv/bin/python -m unittest discover tests -v
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest

from rhr2mp4.formats import locate, maps, rhm, rhr, sspm
from rhr2mp4.formats.rhm import Map, MapMetadata, Note


def _varint_string(s: str) -> bytes:
    data = s.encode("utf-8")
    length = len(data)
    out = bytearray()
    while True:
        b = length & 0x7F
        length >>= 7
        out.append(b | (0x80 if length else 0))
        if not length:
            return bytes(out) + data


def build_rhr(version=20260517, username="tester", legacy_id="map-id", online_id=42,
              mods="[]", speed=1.0, accuracy=97.5, hits=39, misses=1,
              fail_time=-1, beatmap_hash="ab" * 32,
              frames=((0, 0.0, 0.0, 1.0, False), (100, 1.0, -0.5, 0.9, True))) -> bytes:
    out = bytearray()
    out += struct.pack("<iq", version, 638_000_000_000_000_000)
    out += _varint_string(username)
    out += _varint_string(legacy_id)
    out += struct.pack("<ii", online_id, 0)
    out += _varint_string("online_profile")
    if version >= rhr.V_EXTENDED:
        out += struct.pack("<B", 1)  # passed
        out += _varint_string(mods)
        out += struct.pack("<Bfq", 0, speed, 123456)
    out += struct.pack("<fiif", accuracy, hits, misses, 55.5)
    if version >= rhr.V_FAILTIME:
        out += struct.pack("<i", fail_time)
    if version >= rhr.V_BEATMAP_HASH:
        out += _varint_string(beatmap_hash)
    out += struct.pack("<i", len(frames))
    for ms, x, y, health, important in frames:
        if version >= rhr.V_INT32_TIME:
            out += struct.pack("<i", ms)
        else:
            out += struct.pack("<f", float(ms))
        y_stored = -y if version < rhr.V_NEGATE_Y else y
        out += struct.pack("<fffB", x, y_stored, health, 1 if important else 0)
    return bytes(out)


def build_sspm_v1(map_id="v1-id", name="V1 Song", creators="a & b", difficulty=3,
                  notes=((500, 0, 2, False), (1000, 1.5, -0.25, True)),
                  audio=b"OggSxx", cover=b"") -> bytes:
    out = bytearray(b"SS+m" + struct.pack("<HH", 1, 0))
    for s in (map_id, name, creators):
        out += s.encode() + b"\x0a"
    out += struct.pack("<IIB", max(n[0] for n in notes), len(notes), difficulty)
    if cover:
        out += b"\x02" + struct.pack("<Q", len(cover)) + cover
    else:
        out += b"\x00"
    if audio:
        out += b"\x01" + struct.pack("<Q", len(audio)) + audio
    else:
        out += b"\x00"
    for ms, x, y, quantum in notes:
        out += struct.pack("<IB", ms, 1 if quantum else 0)
        out += struct.pack("<ff", x, y) if quantum else bytes([int(x), int(y)])
    return bytes(out)


def build_sspm_v2(map_id="v2-id", name="V2 Song", song="V2 Song Full", mappers=("m1", "m2"),
                  difficulty=4, rating=12, difficulty_name="Custom!",
                  notes=((250, 1, 1, False), (750, 2.5, -0.5, True), (900, 0, 2, False)),
                  audio=b"\xff\xfbAU", cover=b"\x89PNGxx") -> bytes:
    def s16(s):
        d = s.encode()
        return struct.pack("<H", len(d)) + d

    strings = s16(map_id) + s16(name) + s16(song) + struct.pack("<H", len(mappers))
    for m in mappers:
        strings += s16(m)

    custom = struct.pack("<H", 1) + s16("difficulty_name") + b"\x09" + s16(difficulty_name)

    defs = b"\x01" + s16("ssp_note") + b"\x01\x07\x00"

    markers = bytearray()
    for ms, x, y, quantum in notes:
        markers += struct.pack("<IB", ms, 0)
        if quantum:
            markers += b"\x01" + struct.pack("<ff", x, y)
        else:
            markers += b"\x00" + bytes([int(x), int(y)])

    header_size = 0x30 + 80  # fixed header + 5 offset/length pairs
    strings_off = header_size
    custom_off = strings_off + len(strings)
    audio_off = custom_off + len(custom)
    cover_off = audio_off + len(audio)
    defs_off = cover_off + len(cover)
    markers_off = defs_off + len(defs)

    out = bytearray(b"SS+m" + struct.pack("<H", 2) + b"\x00" * 4)
    out += b"\x00" * 20  # sha1 (unused)
    out += struct.pack("<III", max(n[0] for n in notes), len(notes), len(notes))
    out += struct.pack("<BH", difficulty, rating)
    out += struct.pack("<BBB", 1 if audio else 0, 1 if cover else 0, 0)
    for off, length in ((custom_off, len(custom)), (audio_off, len(audio)),
                        (cover_off, len(cover)), (defs_off, len(defs)),
                        (markers_off, len(markers))):
        out += struct.pack("<QQ", off, length)
    assert len(out) == header_size
    out += strings + custom + audio + cover + defs + markers
    return bytes(out)


class TempFileMixin:
    def write_temp(self, data: bytes, suffix: str) -> str:
        f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        f.write(data)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name


class TestRhr(TempFileMixin, unittest.TestCase):
    def test_current_version(self):
        path = self.write_temp(build_rhr(), ".rhr")
        r = rhr.load(path)
        self.assertEqual(r.username, "tester")
        self.assertEqual(r.map_legacy_id, "map-id")
        self.assertEqual(r.map_online_id, 42)
        self.assertEqual(r.beatmap_hash, "ab" * 32)
        self.assertEqual(r.hits, 39)
        self.assertEqual(r.misses, 1)
        self.assertFalse(r.failed)
        self.assertEqual(len(r.frames), 2)
        self.assertEqual(r.frames[1].ms, 100)
        self.assertAlmostEqual(r.frames[1].y, -0.5, places=5)
        self.assertTrue(r.frames[1].important)

    def test_old_version_negated_y_float_time(self):
        path = self.write_temp(build_rhr(version=20260101), ".rhr")
        r = rhr.load(path)
        # Pre-V_NEGATE_Y files store y negated; the parser un-negates.
        self.assertAlmostEqual(r.frames[1].y, -0.5, places=5)
        self.assertEqual(r.frames[1].ms, 100)  # float32 time truncated
        self.assertEqual(r.beatmap_hash, "")  # header field didn't exist yet
        # V_EXTENDED fields didn't exist either; speed defaults to 1.0.
        self.assertEqual(r.speed, 1.0)

    def test_failed_replay(self):
        path = self.write_temp(build_rhr(fail_time=12345), ".rhr")
        r = rhr.load(path)
        self.assertTrue(r.failed)
        self.assertEqual(r.fail_time, 12345)

    def test_interpolation(self):
        path = self.write_temp(build_rhr(), ".rhr")
        r = rhr.load(path)
        x, y = r.cursor_position_at(50)
        self.assertAlmostEqual(x, 0.5, places=5)
        self.assertAlmostEqual(y, -0.25, places=5)
        self.assertAlmostEqual(r.health_at(50), 0.95, places=5)


class TestRhmRoundtrip(TempFileMixin, unittest.TestCase):
    def test_save_load(self):
        m = Map(
            metadata=MapMetadata(online_id=7, online_status="RANKED", legacy_id="lid",
                                 song_name="Song", mappers=["mp"], title="Title",
                                 duration_ms=9000, difficulty=2,
                                 custom_difficulty_name="Weird", star_rating=3.5),
            notes=[Note(100, 0, 2), Note(200, 1.5, -0.5)],
            audio_bytes=b"\xff\xfbxx", cover_bytes=b"\x89PNG",
        )
        path = self.write_temp(b"", ".rhm")
        rhm.save(m, path)
        back = rhm.load(path)
        self.assertEqual(back.metadata.legacy_id, "lid")
        self.assertEqual(back.metadata.custom_difficulty_name, "Weird")
        self.assertEqual([(n.time_ms, n.x, n.y) for n in back.notes],
                         [(100, 0.0, 2.0), (200, 1.5, -0.5)])
        self.assertEqual(back.audio_bytes, m.audio_bytes)
        self.assertEqual(back.cover_bytes, m.cover_bytes)


class TestSspm(TempFileMixin, unittest.TestCase):
    def test_v1(self):
        path = self.write_temp(build_sspm_v1(), ".sspm")
        m = sspm.load(path)
        self.assertEqual(m.metadata.legacy_id, "v1-id")
        self.assertEqual(m.metadata.mappers, ["a", "b"])
        self.assertEqual(m.metadata.difficulty, 3)
        self.assertEqual([(n.time_ms, n.x, n.y) for n in m.notes],
                         [(500, 0.0, 2.0), (1000, 1.5, -0.25)])
        self.assertEqual(m.audio_bytes, b"OggSxx")

    def test_v2(self):
        path = self.write_temp(build_sspm_v2(), ".sspm")
        m = sspm.load(path)
        self.assertEqual(m.metadata.legacy_id, "v2-id")
        self.assertEqual(m.metadata.song_name, "V2 Song Full")
        self.assertEqual(m.metadata.mappers, ["m1", "m2"])
        self.assertEqual(m.metadata.custom_difficulty_name, "Custom!")
        self.assertEqual(m.metadata.star_rating, 12.0)
        self.assertEqual([(n.time_ms, n.x, n.y) for n in m.notes],
                         [(250, 1.0, 1.0), (750, 2.5, -0.5), (900, 0.0, 2.0)])
        self.assertEqual(m.audio_bytes, b"\xff\xfbAU")
        self.assertEqual(m.cover_bytes, b"\x89PNGxx")

    def test_v2_read_meta_skips_media(self):
        path = self.write_temp(build_sspm_v2(), ".sspm")
        m = sspm.read_meta(path)
        self.assertEqual(len(m.notes), 3)
        self.assertEqual(m.audio_bytes, b"")
        self.assertEqual(m.cover_bytes, b"")

    def test_bad_signature(self):
        path = self.write_temp(b"NOPE" + b"\x00" * 16, ".sspm")
        with self.assertRaises(ValueError):
            sspm.load(path)

    def test_maps_dispatcher(self):
        v2 = self.write_temp(build_sspm_v2(), ".sspm")
        self.assertEqual(maps.load(v2).metadata.legacy_id, "v2-id")


class TestLocate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = self.dir.name
        with open(os.path.join(self.folder, "a.sspm"), "wb") as f:
            f.write(build_sspm_v2(map_id="the-legacy-id", song="Great Tune",
                                  notes=tuple((100 * i, 0, 0, False) for i in range(1, 6))))

    def _replay(self, legacy_id="", online_id=0, hits=0, misses=0):
        data = build_rhr(legacy_id=legacy_id, online_id=online_id, hits=hits, misses=misses)
        f = tempfile.NamedTemporaryFile(suffix=".rhr", delete=False)
        f.write(data)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return rhr.load(f.name)

    def test_match_by_legacy_id(self):
        r = self._replay(legacy_id="the-legacy-id")
        self.assertTrue(locate.find_map_for_replay(r, [self.folder]).endswith("a.sspm"))

    def test_match_by_song_name(self):
        r = self._replay(legacy_id="xx - Great Tune - yy")
        self.assertTrue(locate.find_map_for_replay(r, [self.folder]).endswith("a.sspm"))

    def test_match_by_note_count(self):
        r = self._replay(legacy_id="nothing-in-common", hits=4, misses=1)
        self.assertTrue(locate.find_map_for_replay(r, [self.folder]).endswith("a.sspm"))

    def test_no_match(self):
        r = self._replay(legacy_id="nothing-in-common", hits=90, misses=9)
        self.assertIsNone(locate.find_map_for_replay(r, [self.folder]))


if __name__ == "__main__":
    unittest.main()
