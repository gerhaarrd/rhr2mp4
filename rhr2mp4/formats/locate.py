"""Locating a replay's companion files and default output naming.

Shared by the GUI and the CLI so both auto-detect the .rhm a replay was
played on the same way.
"""

from __future__ import annotations

import json
import os
import re
import zipfile


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def find_map_for_replay(replay, search_dirs: list[str], replay_filename: str = "") -> str | None:
    """Looks for the .rhm this replay was played on in `search_dirs`.

    Exact LegacyId / OnlineId matches win (read straight from each
    candidate's zip, so it stays fast even in folders with many maps), but
    they're frequently impossible: game exports often carry a per-export
    hash LegacyId and no OnlineId, while the replay stores either a
    name-based id ("mapper - artist - song") or a *different* hash. So fall
    back, in order of confidence, to:
      1. the map's song name appearing in the replay's legacy id or in the
         replay's own filename (normalized to lowercase alphanumerics);
      2. the map's note count equaling the replay's hits+misses total."""
    name_candidate: str | None = None
    count_candidate: str | None = None
    replay_key = _norm(replay.map_legacy_id)
    filename_key = _norm(os.path.basename(replay_filename))
    note_count = replay.hits + replay.misses

    seen: set[str] = set()
    for folder in search_dirs:
        if not folder:
            continue
        folder = os.path.abspath(folder)
        if folder in seen or not os.path.isdir(folder):
            continue
        seen.add(folder)
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".rhm"):
                continue
            path = os.path.join(folder, name)
            try:
                with zipfile.ZipFile(path) as zf:
                    doc = json.loads(zf.read("map"))
            except Exception:
                continue
            if (replay.map_legacy_id and doc.get("LegacyId") == replay.map_legacy_id) or (
                replay.map_online_id and doc.get("OnlineId") == replay.map_online_id
            ):
                return path
            if name_candidate is None:
                song_key = _norm(doc.get("SongName") or doc.get("Title") or "")
                if song_key and (song_key in replay_key or (filename_key and song_key in filename_key)):
                    name_candidate = path
            if count_candidate is None and note_count > 0 and len(doc.get("Notes", [])) == note_count:
                count_candidate = path
    return name_candidate or count_candidate


def default_output_name(map_song_name: str | None, username: str | None, rhr_path: str) -> str:
    """The default .mp4 filename: "{song} played by {user}.mp4" when the map
    is known, else the replay's own basename."""
    if map_song_name:
        safe = f"{map_song_name} played by {username or 'unknown'}"
        safe = safe.replace("/", "_").replace("\\", "_").replace(":", "_")
        return safe + ".mp4"
    return os.path.splitext(os.path.basename(rhr_path))[0] + ".mp4"
