"""Sanity-checks the .rhm / .rhr parsers against real sample files.

Usage: python scripts/validate.py path/to/replay.rhr path/to/map.rhm
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rhr2mp4.formats import rhm, rhr


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <replay.rhr> <map.rhm>")
        sys.exit(1)

    replay_path, map_path = sys.argv[1], sys.argv[2]

    m = rhm.load(map_path)
    print("=== Map ===")
    print("title:", m.metadata.title)
    print("mappers:", m.metadata.mappers)
    print("online_id:", m.metadata.online_id)
    print("legacy_id:", m.metadata.legacy_id)
    print("duration_ms:", m.metadata.duration_ms)
    print("note count:", len(m.notes))
    print("audio bytes:", len(m.audio_bytes))
    print("cover bytes:", len(m.cover_bytes))

    r = rhr.load(replay_path)
    print()
    print("=== Replay ===")
    print("version:", r.version)
    print("username:", r.username)
    print("map_legacy_id:", r.map_legacy_id)
    print("map_online_id:", r.map_online_id)
    print("mode:", r.mode)
    print("passed:", r.passed, "spin:", r.spin, "fail_time:", r.fail_time)
    print("mods:", repr(r.mods))
    print("speed:", r.speed)
    print("start_from:", r.start_from)
    print("total_score:", r.total_score)
    print("accuracy_pct:", r.accuracy_pct)
    print("hits:", r.hits, "misses:", r.misses, "sum:", r.note_count_from_stats)
    print("points:", r.points)
    print("beatmap_hash:", r.beatmap_hash, "len:", len(r.beatmap_hash))
    print("frame count:", len(r.frames))
    print("first frame:", r.frames[0])
    print("last frame:", r.frames[-1])
    print("replay length_ms:", r.length_ms)

    print()
    print("=== Cross-checks ===")
    ok = True

    if r.map_online_id != m.metadata.online_id:
        print("FAIL: replay map_online_id != map online_id")
        ok = False
    else:
        print("OK: replay map_online_id == map online_id ({})".format(r.map_online_id))

    if r.map_legacy_id != m.metadata.legacy_id:
        print("FAIL: replay map_legacy_id != map legacy_id")
        ok = False
    else:
        print("OK: replay map_legacy_id == map legacy_id")

    if r.note_count_from_stats != len(m.notes):
        print(f"FAIL: hits+misses ({r.note_count_from_stats}) != map note count ({len(m.notes)})")
        ok = False
    else:
        print(f"OK: hits+misses == map note count ({len(m.notes)})")

    expected_acc = 100.0 * r.hits / len(m.notes)
    if abs(expected_acc - r.accuracy_pct) > 0.01:
        print(f"FAIL: accuracy mismatch, expected {expected_acc}, got {r.accuracy_pct}")
        ok = False
    else:
        print(f"OK: accuracy matches hits/total ({r.accuracy_pct:.4f}%)")

    if abs(r.length_ms - m.duration_ms) > 10000:
        print(f"WARN: replay length ({r.length_ms}ms) far from map duration ({m.duration_ms}ms)")
    else:
        print(f"OK: replay length ({r.length_ms:.0f}ms) close to map duration ({m.duration_ms}ms)")

    # cursor path monotonic timestamps, no huge jumps
    prev = None
    max_gap = 0
    for fr in r.frames:
        if prev is not None:
            max_gap = max(max_gap, fr.ms - prev)
        prev = fr.ms
    print(f"max gap between consecutive frame timestamps: {max_gap}ms")

    print()
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
