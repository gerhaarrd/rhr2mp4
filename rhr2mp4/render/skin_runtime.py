"""Resolves a formats.rhs.Skin (or None) into ready-to-draw assets: PIL images
already decoded, the note mesh already rasterized to a mask, and every field
frame.py needs with a sensible default -- so frame.py never has to special-
case "no skin" vs "skin missing some assets".
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image

from ..formats.rhs import Skin
from .notemesh import extract_accent_color, rasterize_note_mask

DEFAULT_NOTE_MASK_SIZE = 256


@dataclass
class RuntimeBackgroundLayer:
    image: Image.Image
    center_x: float
    center_y: float
    scale_x: float
    scale_y: float
    rotation: float
    flip_horizontal: bool
    tint_rgb: tuple[int, int, int]
    tint_opacity: float


@dataclass
class RuntimeSkin:
    note_mask: Image.Image
    # The official visualizer draws plain white notes when no colorset is
    # active.
    note_color: tuple[int, int, int] = (255, 255, 255)
    # Colorset: when non-empty, notes cycle through these per note index
    # (the game's behavior); note_color is the single-color fallback.
    note_colors: list[tuple[int, int, int]] = field(default_factory=list)
    note_scale: float = 1.0
    note_opacity: float = 1.0

    cursor_mask: Image.Image | None = None
    # Custom cursor / cursor-trail textures from the skin's cursorSkin/ and
    # cursorTrailSkin/ zip entries; when None the plain dot is used.
    cursor_image: Image.Image | None = None
    cursor_trail_image: Image.Image | None = None

    border_image: Image.Image | None = None
    # Pixel bbox (left, top, right, bottom) of the region *enclosed* by the
    # border's frame stroke inside border_image (the stroke's inner edge) --
    # skin images carry transparent margins and decorations around the
    # stroke, so the image edges don't tell you where the frame is, and the
    # playfield must sit inside the stroke, not straddle it. None when
    # there's no border image.
    border_stroke_bbox: tuple[int, int, int, int] | None = None
    border_color: tuple[int, int, int] = (255, 255, 255)
    border_opacity: float = 0.3  # official visualizer: rgba(255,255,255,0.3)

    background_layers: list[RuntimeBackgroundLayer] = field(default_factory=list)
    background_rgb: tuple[int, int, int] | None = None  # None = keep the built-in gradient
    background_accent_rgb: tuple[int, int, int] = (0, 0, 0)
    background_accent_from_hit_note: bool = False

    background_tunnel_enabled: bool = False
    background_tunnel_opacity: float = 0.15

    background_chevron_enabled: bool = False
    background_chevron_opacity: float = 0.3
    background_chevron_width: float = 10.0
    background_chevron_gap: float = 20.0
    background_chevron_speed_multiplier: float = 1.0
    background_chevron_small_size: float = 0.5
    background_chevron_large_size: float = 1.0

    background_rays_enabled: bool = False
    background_rays_intensity: float = 1.0
    background_rays_opacity: float = 0.3
    background_rays_width: float = 10.0

    background_grid_enabled: bool = False
    background_grid_opacity: float = 0.2
    background_grid_line_width: float = 1.0
    background_grid_cell_size: float = 50.0
    background_grid_center_gap: float = 0.0
    background_grid_speed_multiplier: float = 1.0
    background_grid_fade_falloff: float = 1.0

    playfield_grid_enabled: bool = False
    playfield_grid_color: tuple[int, int, int] = (255, 255, 255)
    playfield_grid_opacity: float = 0.1
    playfield_grid_thickness: float = 1.0

    camera_fov: float = 90.0
    parallax: float = 0.15

    cursor_color: tuple[int, int, int] = (255, 255, 255)
    cursor_scale: float = 1.0
    cursor_opacity: float = 1.0

    cursor_trail_enabled: bool = True  # the game default shows the trail
    cursor_trail_color: tuple[int, int, int] = (255, 255, 255)
    cursor_trail_opacity: float = 0.3
    cursor_trail_fade_time_s: float = 0.15
    # GUI-only length multiplier on the trail (1.0 = normal); scales the
    # fade window, so 0.5 draws a trail half as long.
    cursor_trail_scale: float = 1.0

    # GUI-only toggle for the visualizer's ambient drifting dots behind the
    # playfield (see frame._draw_background_effects).
    ambient_dots_enabled: bool = True

    # GUI-only toggle for the hit burst effect (expanding outline +
    # fragments when a note is hit; see frame._draw_hit_effects).
    hit_effects_enabled: bool = True

    combo_text_enabled: bool = True  # the big faint combo in the playfield center
    combo_text_color: tuple[int, int, int] = (255, 255, 255)
    combo_text_opacity: float = 0.35  # the game draws the combo faintly
    combo_text_scale: float = 1.0
    combo_text_vertical_position_percent: float = 20.0

    combo_ring_color: tuple[int, int, int] = (60, 230, 230)
    combo_ring_opacity: float = 0.9

    panel_color: tuple[int, int, int] = (45, 28, 62)
    panel_opacity: float = 1.0
    panel_background_opacity: float = 0.0  # game panels are bare text
    panel_gap: float = 10.0
    panel_angle: float = 0.0

    interface_text_color: tuple[int, int, int] = (255, 255, 255)
    interface_values_font_size: float = 1.0

    song_info_enabled: bool = True
    progress_bar_enabled: bool = True
    progress_bar_color: tuple[int, int, int] = (0, 0, 0)  # game default: dark fill on light track
    progress_bar_alpha: float = 0.86

    health_bar_enabled: bool = True
    # The speed/mods label under the health bar ("S++++  HR GH").
    speed_text_enabled: bool = True
    # Hit-error (timing) bar under the playfield: ticks for recent hits by
    # how early/late they were. An app render option, not a game HUD element,
    # so it defaults off.
    hit_error_bar_enabled: bool = False
    health_bar_color: tuple[int, int, int] = (211, 255, 151)  # game default light green
    health_bar_alpha: float = 1.0
    fail_vignette_opacity: float = 0.0
    miss_effect_opacity: float = 0.0

    left_panel_accuracy_enabled: bool = True
    left_panel_combo_ring_enabled: bool = True
    left_panel_pauses_enabled: bool = True
    right_panel_notes_enabled: bool = True
    right_panel_misses_enabled: bool = True
    right_panel_score_enabled: bool = True
    right_panel_points_enabled: bool = True

    hit_sound_bytes: bytes | None = None
    hit_sound_volume: float = 100.0
    miss_sound_bytes: bytes | None = None
    miss_sound_combo_threshold: int = 0


def _solid_circle_mask(size: int = DEFAULT_NOTE_MASK_SIZE) -> Image.Image:
    """Used for the cursor (always a plain dot, skin or not)."""
    from PIL import ImageDraw

    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    pad = size * 0.06
    d.ellipse([pad, pad, size - pad, size - pad], fill=255)
    return mask


def _default_note_ring_mask(size: int = DEFAULT_NOTE_MASK_SIZE) -> Image.Image:
    """Default note shape when no skin (or a skin with no note .obj) is
    active: a plain square outline, matching the real game's own default
    (unskinned) note look rather than an arbitrary filled circle."""
    from PIL import ImageDraw

    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    # Official visualizer note: an 80-unit rounded square with corner radius
    # 25 and stroke weight 12, rounded joins -- i.e. radius 25/80 and stroke
    # 12/80 of the note size. The outline's outer edge sits on the mask
    # boundary (inset only by the 1px the rasterizer needs) so a sprite
    # sized to 0.8 of a cell is exactly the note the game draws.
    pad = 1
    width = max(2, round(size * 12 / 80))
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size * 25 / 80, outline=255, width=width)
    return mask


def _band_inner_edge(line, outer: int, step: int, threshold: int, max_gap: int, limit: int) -> int:
    """Given the outermost opaque pixel of the frame stroke on a scan line,
    walks inward across the stroke band (tolerating small transparent gaps,
    e.g. between a double stroke) and returns the last opaque pixel of the
    band -- the stroke's inner edge."""
    x = outer
    last_opaque = outer
    while 0 <= x < len(line) and abs(x - outer) < limit:
        if line[x] >= threshold:
            last_opaque = x
        elif abs(x - last_opaque) > max_gap:
            break
        x += step
    return last_opaque


def _border_stroke_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Finds the pixel bbox of the region enclosed by the frame stroke of a
    border-skin image (i.e. the stroke's inner edges).

    Border images carry transparent margins and decorative art (mascots,
    stickers) around and *on* the frame stroke, so neither the image edges
    nor the alpha bounding box locate the frame itself. Instead, scan a few
    rows/columns around the image's middle: on each line the outermost
    opaque pixel starts the stroke band, then walk inward across it to its
    inner edge, and take the median per edge so the handful of lines that
    cross a decoration (e.g. a mascot sitting on the top edge) get outvoted.
    The inner edge (rather than the outer one) is what the playfield must
    align to: notes fill the region *inside* the frame, flush against the
    stroke, not straddling it.
    """
    import statistics

    import numpy as np

    alpha = np.asarray(img.getchannel("A"), dtype=np.uint8)
    h, w = alpha.shape
    threshold = 40
    fractions = (0.35, 0.42, 0.5, 0.58, 0.65)
    max_gap = max(2, w // 100)
    limit = int(w * 0.15)

    lefts, rights, tops, bottoms = [], [], [], []
    for f in fractions:
        row = alpha[int(h * f)]
        xs = np.nonzero(row >= threshold)[0]
        if len(xs):
            lefts.append(_band_inner_edge(row, int(xs[0]), 1, threshold, max_gap, limit) + 1)
            rights.append(_band_inner_edge(row, int(xs[-1]), -1, threshold, max_gap, limit))
        col = alpha[:, int(w * f)]
        ys = np.nonzero(col >= threshold)[0]
        if len(ys):
            tops.append(_band_inner_edge(col, int(ys[0]), 1, threshold, max_gap, limit) + 1)
            bottoms.append(_band_inner_edge(col, int(ys[-1]), -1, threshold, max_gap, limit))

    if not lefts or not tops:
        return (0, 0, w, h)
    return (
        int(statistics.median(lefts)),
        int(statistics.median(tops)),
        int(statistics.median(rights)),
        int(statistics.median(bottoms)),
    )


def resolve(skin: Skin | None) -> RuntimeSkin:
    cursor_mask = _solid_circle_mask(size=128)

    if skin is None:
        # Bare RuntimeSkin defaults already mirror the official visualizer
        # (white notes, thin 30% border, no grid lines / tunnel effects).
        return RuntimeSkin(
            note_mask=_default_note_ring_mask(),
            cursor_mask=cursor_mask,
        )

    note_mask = (
        rasterize_note_mask(skin.note_obj_bytes)
        if skin.note_obj_bytes
        else _default_note_ring_mask()
    )

    border_image = None
    border_stroke_bbox = None
    note_color = (255, 255, 255)
    if skin.border_image_bytes:
        border_image = Image.open(io.BytesIO(skin.border_image_bytes)).convert("RGBA")
        border_stroke_bbox = _border_stroke_bbox(border_image)
        note_color = extract_accent_color(border_image, default=note_color)

    cursor_image = None
    if skin.cursor_image_bytes:
        cursor_image = Image.open(io.BytesIO(skin.cursor_image_bytes)).convert("RGBA")
    cursor_trail_image = None
    if skin.cursor_trail_image_bytes:
        cursor_trail_image = Image.open(io.BytesIO(skin.cursor_trail_image_bytes)).convert("RGBA")

    background_layers = [
        RuntimeBackgroundLayer(
            image=Image.open(io.BytesIO(bl.image_bytes)).convert("RGBA"),
            center_x=bl.center_x,
            center_y=bl.center_y,
            scale_x=bl.scale_x,
            scale_y=bl.scale_y,
            rotation=bl.rotation,
            flip_horizontal=bl.flip_horizontal,
            tint_rgb=bl.tint_rgb,
            tint_opacity=bl.tint_opacity,
        )
        for bl in skin.background_layers
    ]

    return RuntimeSkin(
        note_mask=note_mask,
        note_color=note_color,
        note_colors=list(skin.note_colors),
        cursor_mask=cursor_mask,
        cursor_image=cursor_image,
        cursor_trail_image=cursor_trail_image,
        note_scale=skin.note_scale,
        note_opacity=skin.note_opacity,
        border_image=border_image,
        border_stroke_bbox=border_stroke_bbox,
        border_color=skin.border_color,
        border_opacity=skin.border_opacity,
        background_layers=background_layers,
        background_rgb=skin.background_rgb,
        background_accent_rgb=skin.background_accent_rgb,
        background_accent_from_hit_note=skin.background_accent_from_hit_note,
        background_tunnel_enabled=skin.background_tunnel_enabled,
        background_tunnel_opacity=skin.background_tunnel_opacity,
        background_chevron_enabled=skin.background_chevron_enabled,
        background_chevron_opacity=skin.background_chevron_opacity,
        background_chevron_width=skin.background_chevron_width,
        background_chevron_gap=skin.background_chevron_gap,
        background_chevron_speed_multiplier=skin.background_chevron_speed_multiplier,
        background_chevron_small_size=skin.background_chevron_small_size,
        background_chevron_large_size=skin.background_chevron_large_size,
        background_rays_enabled=skin.background_rays_enabled,
        background_rays_intensity=skin.background_rays_intensity,
        background_rays_opacity=skin.background_rays_opacity,
        background_rays_width=skin.background_rays_width,
        background_grid_enabled=skin.background_grid_enabled,
        background_grid_opacity=skin.background_grid_opacity,
        background_grid_line_width=skin.background_grid_line_width,
        background_grid_cell_size=skin.background_grid_cell_size,
        background_grid_center_gap=skin.background_grid_center_gap,
        background_grid_speed_multiplier=skin.background_grid_speed_multiplier,
        background_grid_fade_falloff=skin.background_grid_fade_falloff,
        playfield_grid_enabled=skin.playfield_grid_enabled,
        playfield_grid_color=skin.playfield_grid_color,
        playfield_grid_opacity=skin.playfield_grid_opacity,
        playfield_grid_thickness=skin.playfield_grid_thickness,
        camera_fov=skin.camera_fov,
        # The skin's Parallax is on a different scale than our camera's sway
        # factor (this game's camera-rig units aren't recoverable exactly --
        # see formats/rhs.py docstring); divide down empirically so it reads
        # as a subtle sway rather than an exaggerated one.
        parallax=skin.parallax / 15.0,
        cursor_color=skin.cursor_color,
        cursor_scale=skin.cursor_scale,
        cursor_opacity=skin.cursor_opacity,
        cursor_trail_enabled=skin.cursor_trail_enabled,
        cursor_trail_color=(
            skin.cursor_color if skin.cursor_trail_inherit_from_cursor else skin.cursor_trail_color
        ),
        cursor_trail_opacity=skin.cursor_trail_opacity,
        cursor_trail_fade_time_s=skin.cursor_trail_fade_time_s,
        combo_text_color=skin.combo_text_color,
        combo_text_opacity=skin.combo_text_opacity,
        combo_text_scale=skin.combo_text_font_size / 100.0,
        combo_text_vertical_position_percent=skin.combo_text_vertical_position_percent,
        combo_ring_color=skin.combo_ring_color,
        combo_ring_opacity=skin.combo_ring_opacity,
        panel_color=skin.panel_color,
        panel_opacity=skin.panel_opacity,
        panel_background_opacity=skin.panel_background_opacity,
        panel_gap=skin.panel_gap,
        panel_angle=skin.panel_angle,
        interface_text_color=skin.interface_text_color,
        interface_values_font_size=skin.interface_values_font_size,
        song_info_enabled=skin.song_info_enabled,
        progress_bar_enabled=skin.progress_bar_enabled,
        progress_bar_color=skin.progress_bar_color,
        progress_bar_alpha=skin.progress_bar_alpha,
        health_bar_enabled=skin.health_bar_enabled,
        health_bar_color=skin.health_bar_color,
        health_bar_alpha=skin.health_bar_alpha,
        fail_vignette_opacity=skin.fail_vignette_opacity,
        miss_effect_opacity=skin.miss_effect_opacity,
        left_panel_accuracy_enabled=skin.left_panel_accuracy_enabled,
        left_panel_combo_ring_enabled=skin.left_panel_combo_ring_enabled,
        left_panel_pauses_enabled=skin.left_panel_pauses_enabled,
        right_panel_notes_enabled=skin.right_panel_notes_enabled,
        right_panel_misses_enabled=skin.right_panel_misses_enabled,
        right_panel_score_enabled=skin.right_panel_score_enabled,
        right_panel_points_enabled=skin.right_panel_points_enabled,
        hit_sound_bytes=skin.hit_sound_bytes,
        hit_sound_volume=skin.hit_sound_volume,
        miss_sound_bytes=skin.miss_sound_bytes,
        miss_sound_combo_threshold=skin.miss_sound_combo_threshold,
    )
