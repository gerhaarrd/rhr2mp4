"""Discovery of Rhythia colorsets without any configuration.

Both clients keep the user's colorsets in well-known per-user folders:

- Rhythia (current client, Godot `user://`): `~/.local/share/Rhythia/colorsets`
  on Linux, `%APPDATA%/Rhythia/colorsets` on Windows;
- CapoRhythia: `~/.config/CapoRhythia/skins/colorsets` on Linux,
  `%APPDATA%/CapoRhythia/skins/colorsets` on Windows.

The app also ships a few colorsets of its own in `rhr2mp4/assets/colorsets`
(at least the game's stock default), so rendering never depends on a game
install being present.
"""

from __future__ import annotations

import os
import re

from ..paths import asset_path
from .rhs import parse_colorset


def game_colorset_dirs(game_dir: str = "") -> list[str]:
    home = os.path.expanduser("~")
    dirs = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            dirs += [
                os.path.join(appdata, "Rhythia", "colorsets"),
                os.path.join(appdata, "CapoRhythia", "skins", "colorsets"),
            ]
    else:
        dirs += [
            os.path.join(home, ".local", "share", "Rhythia", "colorsets"),
            os.path.join(home, ".config", "CapoRhythia", "skins", "colorsets"),
        ]
    if game_dir:
        dirs += [
            os.path.join(game_dir, "colorsets"),
            os.path.join(game_dir, "skins", "colorsets"),
            os.path.join(game_dir, "user", "colorsets"),
        ]
    return dirs


def _display_name(filename: str) -> str:
    """Filename -> display name: extension and the game's re-import
    timestamp suffixes stripped ("Teto-20260324-222051-20260614-202904.txt"
    -> "Teto")."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    return re.sub(r"(-\d{8}-\d{6})+$", "", stem) or stem


def discover_game_colorsets(game_dir: str = "") -> dict[str, list[tuple[int, int, int]]]:
    """Colorsets found in the known Rhythia folders (plus the ones bundled
    with the app), as {display name: [rgb, ...]}. Game folders win over the
    bundled copies of the same name so user edits show through."""
    # Keyed case-insensitively so the game's live copy of a bundled
    # colorset ("default.txt" vs bundled "Default.txt") replaces it
    # instead of duplicating the entry.
    found: dict[str, tuple[str, list[tuple[int, int, int]]]] = {}
    bundled = os.path.join(os.path.dirname(asset_path("colorsets")), "colorsets")
    for folder in [bundled] + game_colorset_dirs(game_dir):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".txt"):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                    colors = parse_colorset(f.read())
            except OSError:
                continue
            if colors:
                display = _display_name(name)
                key = display.lower()
                display = found.get(key, (display,))[0]  # keep first-seen casing
                found[key] = (display, colors)
    return {display: colors for display, colors in found.values()}


def find_colorset_by_name(ref: str, game_dir: str = "") -> list[tuple[int, int, int]] | None:
    """Resolves a skin's ColorSet reference (or a bare name) against the
    discovered colorsets, matching on the timestamp-stripped stem — used as
    a fallback when the configured game folder doesn't yield a hit."""
    want = _display_name(ref).lower()
    for name, colors in discover_game_colorsets(game_dir).items():
        if name.lower() == want:
            return colors
    return None
