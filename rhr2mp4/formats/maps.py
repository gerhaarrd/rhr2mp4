"""Loading a map in any supported format, picked by file extension.

Both parsers return the same `Map` dataclass (formats/rhm.py), so callers
never need to know which format a path is -- they just call `maps.load`.
"""

from __future__ import annotations

import os

from . import rhm, sspm

# Every map format the app can read, in the order file pickers offer them.
MAP_EXTENSIONS = (".rhm", ".sspm")
MAP_FILE_FILTER = "Map (*.rhm *.sspm)"


def load(path: str) -> rhm.Map:
    if os.path.splitext(path)[1].lower() == ".sspm":
        return sspm.load(path)
    return rhm.load(path)
