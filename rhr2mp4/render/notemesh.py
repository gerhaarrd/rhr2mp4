"""Turns a Wavefront .obj note-skin mesh into a reusable 2D alpha mask.

Note-skin meshes shipped in .rhs skins are flat (or near-flat) shapes viewed
face-on -- e.g. a rounded square ring -- so there's no need for a real 3D
renderer: projecting vertices orthographically onto XY and filling each face
as a 2D polygon reconstructs the shape exactly (faces tile the mesh, so empty
areas like the hole in a ring stay empty). This is computed once per skin
(not per frame) and cached as a grayscale mask; frame.py tints/resizes/pastes
it per note.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
from PIL import Image, ImageDraw


def extract_accent_color(image: Image.Image, default: tuple[int, int, int] = (90, 200, 255)) -> tuple[int, int, int]:
    """Picks the most common vivid (saturated, non-black/white) color in an
    image -- used to tint notes from the border-skin texture's trim color,
    since the actual note color (`ColorSet` in the .rhs config) references a
    built-in game asset we don't have (see formats/rhs.py). Border textures
    are themed to match their note/UI color, so this reliably recovers
    something close to the real accent (validated against a reference
    recording: Marin's border's dominant color matched the real note color
    almost exactly).
    """
    arr = np.array(image.convert("RGBA"))
    opaque = arr[arr[..., 3] > 200][:, :3].astype(np.int32)
    if len(opaque) == 0:
        return default

    mx = opaque.max(axis=1)
    mn = opaque.min(axis=1)
    saturation = mx - mn
    vivid = opaque[(saturation > 60) & (mx > 80) & (mx < 250)]
    if len(vivid) == 0:
        return default

    # Bucket similar colors together so near-identical anti-aliased edge
    # pixels count as one color, then take the most common bucket.
    buckets = Counter(tuple(int(c) for c in (p // 16 * 16)) for p in vivid)
    return buckets.most_common(1)[0][0]


def _parse_obj(obj_text: str) -> tuple[list[tuple[float, float]], list[list[int]]]:
    vertices: list[tuple[float, float]] = []
    faces: list[list[int]] = []

    for line in obj_text.splitlines():
        line = line.strip()
        if line.startswith("v "):
            parts = line.split()
            vertices.append((float(parts[1]), float(parts[2])))
        elif line.startswith("f "):
            parts = line.split()[1:]
            idx = [int(p.split("/")[0]) - 1 for p in parts]  # obj indices are 1-based
            faces.append(idx)

    return vertices, faces


def rasterize_note_mask(obj_bytes: bytes, size: int = 256, padding: float = 0.04) -> Image.Image:
    vertices, faces = _parse_obj(obj_bytes.decode("utf-8", errors="replace"))

    if not vertices or not faces:
        # Fall back to a filled circle if the mesh failed to parse.
        mask = Image.new("L", (size, size), 0)
        d = ImageDraw.Draw(mask)
        pad = size * padding
        d.ellipse([pad, pad, size - pad, size - pad], fill=255)
        return mask

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    extent = max(max_x - min_x, max_y - min_y) or 1.0

    usable = size * (1.0 - 2 * padding)
    cx, cy = size / 2.0, size / 2.0

    def to_px(v: tuple[float, float]) -> tuple[float, float]:
        x, y = v
        nx = (x - (min_x + max_x) / 2.0) / extent
        ny = (y - (min_y + max_y) / 2.0) / extent
        # image Y grows downward, mesh Y grows upward
        return (cx + nx * usable, cy - ny * usable)

    points_px = [to_px(v) for v in vertices]

    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    for face in faces:
        poly = [points_px[i] for i in face]
        d.polygon(poly, fill=255)

    return mask
