"""Locating bundled resources (assets, ffmpeg) in a way that works both
from source and inside frozen builds (PyInstaller onedir/onefile, where
data files live under sys._MEIPASS and a bundled ffmpeg sits next to the
executable)."""

from __future__ import annotations

import os
import shutil
import sys


def _bundle_dirs() -> list[str]:
    dirs = []
    if getattr(sys, "frozen", False):
        # onefile extracts to _MEIPASS; onedir keeps files next to the exe.
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            dirs.append(meipass)
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    return dirs


def asset_path(name: str) -> str:
    """Absolute path of a file in rhr2mp4/assets (bundled or from source)."""
    for base in _bundle_dirs():
        candidate = os.path.join(base, "rhr2mp4", "assets", name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)


def ffmpeg_exe() -> str | None:
    """The ffmpeg binary to use: one shipped alongside a frozen build wins
    (so the Windows zip is self-contained), otherwise whatever is on PATH."""
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for base in _bundle_dirs():
        for candidate in (os.path.join(base, name), os.path.join(base, "ffmpeg", name)):
            if os.path.isfile(candidate):
                return candidate
    return shutil.which(name)
