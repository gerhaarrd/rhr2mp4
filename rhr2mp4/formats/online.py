"""Fetching maps from rhythia.com when no local copy is found.

The website's own SPA talks to a public JSON API (reverse-engineered from
the rhythia.com bundle; the endpoints accept an empty session string for
read-only queries):

    POST https://development.rhythia.com/api/getBeatmapPage
         {"session": "", "id": <online id>, "limit": 1}
    ->   {"beatmap": {"title", "beatmapFile" (.sspm URL), "mapHash", ...}}

`mapHash` is the same sha256 hex string replays store as `beatmap_hash`
(format version >= 20260517), so a downloaded map can be verified to be
exactly the one the replay was played on -- something local matching can
only approximate.

Downloads are cached in a per-user directory so each map is fetched once.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

API_URL = "https://development.rhythia.com/api/getBeatmapPage"
USER_AGENT = "rhr2mp4 (+https://github.com/gerhaarrd/rhr2mp4)"
TIMEOUT_S = 20


def cache_dir() -> str:
    """Per-user download cache (~/.cache/rhr2mp4/maps or the OS equivalent)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "rhr2mp4", "maps")


def fetch_map_info(online_id: int) -> dict | None:
    """The API's beatmap record for a map id, or None if unknown/unreachable."""
    if not online_id or online_id <= 0:
        return None
    body = json.dumps({"session": "", "id": int(online_id), "limit": 1}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            doc = json.loads(resp.read())
    except Exception:
        return None
    beatmap = doc.get("beatmap")
    return beatmap if isinstance(beatmap, dict) and beatmap.get("beatmapFile") else None


def download_map(online_id: int, dest_dir: str | None = None,
                 expected_hash: str = "") -> str | None:
    """Downloads the map for an online id into `dest_dir` (default: the
    user cache) and returns its path, or None if it can't be fetched.

    When the replay's `beatmap_hash` is passed as `expected_hash`, the map
    is only accepted if the API reports the same hash -- i.e. the online map
    is still byte-identical to the one the replay was recorded on."""
    info = fetch_map_info(online_id)
    if info is None:
        return None
    if expected_hash and info.get("mapHash") and info["mapHash"].lower() != expected_hash.lower():
        return None

    url = info["beatmapFile"]
    ext = ".sspm" if url.lower().endswith(".sspm") else ".rhm"
    title = re.sub(r'[<>:"/\\|?*]+', "_", str(info.get("title") or f"map-{online_id}")).strip()
    dest_dir = dest_dir or cache_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{online_id} - {title}{ext}")
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(256 * 1024):
                f.write(chunk)
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return dest


def download_map_for_replay(replay, dest_dir: str | None = None) -> str | None:
    """Fetches the exact map a replay was played on, verified against the
    replay's beatmap hash when it has one."""
    return download_map(replay.map_online_id, dest_dir, expected_hash=replay.beatmap_hash)
