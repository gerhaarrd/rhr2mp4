"""Camera model ported from Rhythia's own open-source map visualizer (the
p5.js sketch embedded by rhythia.com's Map preview).

The sketch works in world units where one grid cell = 100 units: lane
centers sit at -100/0/+100, a note spawns at z = -1500 (15 cells away) and
reaches z = 0 exactly at its timestamp, the playfield border spans +/-180
(1.8 cells), and p5's default WEBGL camera sits at eyeZ = 800 (8 cells) from
the hit plane. Everything here is that same model expressed in cell units
(1 unit = 1 lane spacing), with an optional parallax sway kept from the real
game (the visualizer itself has no cursor, so no sway).
"""

from __future__ import annotations

from dataclasses import dataclass

# p5's default WEBGL eye distance is 800 world units with cell = 100:
EYE_Z_CELLS = 8.0
# The playfield border frames the note grid exactly: lane centers at
# -1/0/+1 plus half a cell each side = +/-1.5 cells, so a corner note's
# outer corner lands on the border's corner (matches the user's real
# gameplay footage; the web visualizer's wider +/-1.8 border leaves a
# margin the game doesn't have).
HALF_EXTENT_CELLS = 1.5


@dataclass
class Camera:
    width: int
    height: int
    fov_deg: float = 0.0  # accepted for compatibility; the p5 model fixes the projection
    parallax: float = 0.15  # camera sway strength driven by cursor position
    # Scene half-width mapped to the playfield edge; 1.8 = standard border,
    # larger under the hardrock mod (bigger play area, same note grid).
    half_extent: float = HALF_EXTENT_CELLS

    def project(self, x: float, y: float, depth: float, cursor: tuple[float, float] = (0.0, 0.0)):
        """Project a scene-space point to playfield pixels.

        x, y: cell-unit scene coordinates (0,0 = center of the playfield,
            lane centers at -1/0/+1).
        depth: distance beyond the hit plane in cells (0 = on the hit plane,
            15 = just spawned; slightly negative = flying past the player,
            which is how misses leave the field).
        cursor: current cursor scene position, used for a subtle parallax
            camera sway. The sway fades out as depth -> 0 so a note lands
            exactly on its grid cell at the hit moment (the frame is drawn
            at a fixed screen position, unaffected by sway).

        Returns (screen_x, screen_y, scale) where scale is the relative size
        multiplier a note/cursor should be drawn at (1.0 at the hit plane).
        """
        sway = min(1.0, depth) if depth > 0 else 0.0
        vx = x + cursor[0] * self.parallax * sway
        vy = y + cursor[1] * self.parallax * sway

        # Perspective: a point `depth` cells behind the hit plane is
        # eye_z + depth from the camera; the hit plane itself is at eye_z.
        factor = EYE_Z_CELLS / max(0.5, EYE_Z_CELLS + depth)

        screen_x = (vx * factor / self.half_extent * 0.5 + 0.5) * self.width
        # Scene +y is up; screen +y is down.
        screen_y = (0.5 - vy * factor / self.half_extent * 0.5) * self.height
        return screen_x, screen_y, factor
