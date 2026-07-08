"""Draws a single video frame from a TimelineState.

Layout (matches the reference CapoRhythia screenshot, used with or without a
loaded .rhs skin): a square playfield centered on the canvas, with an
accuracy panel to its left, a notes panel to its right, a combo readout
centered near the top of the square, and a progress bar just below it. A
title block (cover thumbnail, title/mapper, elapsed/total time) sits above
the square and the username sits in the top-right corner.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from ..formats.rhm import MapMetadata
from ..formats.rhr import Replay
from ..sim.timeline import TimelineState
from .camera import Camera
from .skin_runtime import RuntimeSkin
from .skin_runtime import resolve as resolve_skin

# Bundled with the package (rhr2mp4/assets) so frozen Windows/Linux builds
# and dev checkouts all render with the same face.
from ..paths import asset_path

FONT_BOLD = asset_path("DejaVuSans-Bold.ttf")
FONT_REGULAR = asset_path("DejaVuSans.ttf")

# Fraction of its grid cell a note fills at the hit moment. 1.0 = a note
# spans its whole cell, so neighboring notes' edges touch each other (and
# the outer lanes touch the cell boundary), matching the real game -- the
# web visualizer's smaller 80/100 note reads as gaps that the game doesn't
# have.
NOTE_CELL_FILL = 1.0


def note_to_scene(x: float, y: float) -> tuple[float, float]:
    """Map a note's native grid coords (0..2, +y up-ish) into the centered
    scene space the replay's cursor coordinates were found to already use."""
    return (x - 1.0, 1.0 - y)


def _format_time(ms: float) -> str:
    total_s = max(0, int(ms / 1000))
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


# The game's speed-modifier notation (shown as a suffix after the grade,
# e.g. "S--" for an S rank at 0.8x).
_SPEED_SUFFIXES = [
    (0.75, "---"),
    (0.8, "--"),
    (0.85, "-"),
    (1.0, ""),
    (1.15, "+"),
    (1.25, "++"),
    (1.35, "+++"),
    (1.45, "++++"),
]


def _speed_suffix(speed: float) -> str:
    return min(_SPEED_SUFFIXES, key=lambda kv: abs(kv[0] - speed))[1]


@dataclass
class RenderContext:
    width: int
    height: int
    skin: RuntimeSkin
    camera: Camera
    playfield_origin: tuple[int, int]
    playfield_size: int
    font_title: ImageFont.FreeTypeFont
    font_small: ImageFont.FreeTypeFont
    font_combo: ImageFont.FreeTypeFont
    font_panel_label: ImageFont.FreeTypeFont
    font_panel_value: ImageFont.FreeTypeFont
    base_background: Image.Image
    # Portrait (9:16) canvases move the stat panels below the playfield
    # instead of beside it (they don't fit next to a near-full-width square).
    portrait: bool = False
    # Short mod codes ("HR MR GH CH", from sim.mods.resolve_mods) appended
    # to the grade line under the playfield.
    mods_label: str = ""
    cover_thumb: Image.Image | None = None
    border_resized: Image.Image | None = None
    # Where to paste border_resized: its frame stroke is aligned to the
    # playfield square, so the image itself (margins, mascots) extends past
    # the square and this origin is up/left of playfield_origin.
    border_origin: tuple[int, int] = (0, 0)
    # Skin cursor/trail textures, already tinted with the skin's colors and
    # downscaled to a cheap-to-resize working size; None = plain dot.
    cursor_sprite: Image.Image | None = None
    cursor_trail_sprite: Image.Image | None = None


def _apply_tint(img: Image.Image, tint_rgb: tuple[int, int, int], tint_opacity: float) -> Image.Image:
    if tint_rgb == (255, 255, 255) and tint_opacity >= 1.0:
        return img
    arr = np.array(img).astype(np.float32)
    arr[..., 0] *= tint_rgb[0] / 255.0
    arr[..., 1] *= tint_rgb[1] / 255.0
    arr[..., 2] *= tint_rgb[2] / 255.0
    arr[..., 3] *= tint_opacity
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGBA")


def _make_background(width: int, height: int, cover_bytes: bytes | None, skin: RuntimeSkin) -> tuple[Image.Image, Image.Image | None]:
    if skin.background_rgb is not None:
        top = bottom = np.array(skin.background_rgb, dtype=np.float32)
    else:
        # The official visualizer clears to background(10) -- near-black
        # gray -- regardless of the map's cover art.
        top = bottom = np.array([10, 10, 10], dtype=np.float32)

    t = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    gradient = (top[None, :] + (bottom - top)[None, :] * t).astype(np.uint8)
    gradient = np.repeat(gradient[:, None, :], width, axis=1)
    bg = Image.fromarray(gradient, mode="RGB").convert("RGBA")

    if skin.background_layers:
        for layer in skin.background_layers:
            img = layer.image
            if layer.flip_horizontal:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            # scale_x/scale_y define a bounding box, not literal target
            # dimensions -- resizing to both independently stretched every
            # character image (their aspect ratio never exactly matches
            # scale_x*width : scale_y*height). Fit the image inside the box
            # instead, preserving its own aspect ratio (matches the "Fit"
            # field in the config, whose exact enum values we don't have
            # documented -- see formats/rhs.py -- but this is what the
            # reference recording's proportions look like).
            box_w = max(1, layer.scale_x * width)
            box_h = max(1, layer.scale_y * height)
            fit_scale = min(box_w / img.width, box_h / img.height)
            target_w = max(1, int(img.width * fit_scale))
            target_h = max(1, int(img.height * fit_scale))
            img = img.resize((target_w, target_h), Image.LANCZOS)
            if layer.rotation:
                img = img.rotate(-layer.rotation, expand=True, resample=Image.BICUBIC)
            img = _apply_tint(img, layer.tint_rgb, layer.tint_opacity)
            px = int(layer.center_x * width - img.width / 2)
            py = int(layer.center_y * height - img.height / 2)
            bg.alpha_composite(img, (px, py))

    cover_thumb = None
    if cover_bytes:
        cover = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
        thumb_size = int(min(width, height) * 0.1)
        cover_thumb = cover.resize((thumb_size, thumb_size))

    return bg.convert("RGB"), cover_thumb


def build_context(width: int, height: int, cover_bytes: bytes | None, skin: RuntimeSkin | None = None,
                  playfield_extent: float = 1.5) -> RenderContext:
    if skin is None:
        skin = resolve_skin(None)

    # Proportions measured from a real gameplay recording at 1080p: the
    # playfield border spans ~620px (57.5% of height) with its top at ~23%,
    # leaving room above for title/time/progress bar (and skin mascots that
    # hang outside the border). Portrait (9:16) canvases instead size the
    # playfield off the width and move the stat panels below it (see
    # _draw_side_panels).
    portrait = width < height
    if portrait:
        playfield_size = int(width * 0.82)
        playfield_top = int(height * 0.16)
    else:
        playfield_size = int(height * 0.575)
        playfield_top = int(height * 0.23)
    playfield_left = (width - playfield_size) // 2

    camera = Camera(width=playfield_size, height=playfield_size, fov_deg=skin.camera_fov,
                    parallax=skin.parallax, half_extent=playfield_extent)

    # Font sizes eyeballed against a real gameplay recording at 1080p:
    # title ~30px bold, time ~26px, panel labels ~26px gray / values ~30px
    # white, combo ~10% of the playfield (PlayfieldComboTextFontSize=100).
    # Scaled by the smaller canvas dimension so portrait text doesn't blow
    # up with the 1920px height (identical to height in landscape).
    ref = min(width, height)
    font_title = ImageFont.truetype(FONT_BOLD, size=int(ref * 0.028))
    font_small = ImageFont.truetype(FONT_BOLD, size=int(ref * 0.023))
    font_combo = ImageFont.truetype(FONT_BOLD, size=max(8, int(playfield_size * 0.10 * skin.combo_text_scale)))
    font_panel_label = ImageFont.truetype(FONT_BOLD, size=max(6, int(ref * 0.024 * skin.interface_values_font_size)))
    font_panel_value = ImageFont.truetype(FONT_BOLD, size=max(8, int(ref * 0.028 * skin.interface_values_font_size)))

    bg, cover_thumb = _make_background(width, height, cover_bytes, skin)

    border_resized = None
    border_origin = (0, 0)
    if skin.border_image is not None:
        # Scale the image so its frame *stroke* (not the image edges -- skin
        # images carry transparent margins and mascot art around the stroke,
        # see skin_runtime._border_stroke_bbox) spans exactly the playfield
        # square, then position it so stroke and square coincide. Mascots and
        # margins deliberately hang outside the square, like in the game.
        img = skin.border_image
        bl, bt, br, bb = skin.border_stroke_bbox or (0, 0, img.width, img.height)
        sx = playfield_size / max(1, br - bl)
        sy = playfield_size / max(1, bb - bt)
        border_resized = img.resize(
            (max(1, round(img.width * sx)), max(1, round(img.height * sy))), Image.LANCZOS
        )
        border_origin = (round(playfield_left - bl * sx), round(playfield_top - bt * sy))

    def _prep_texture(img: Image.Image | None, tint: tuple[int, int, int]) -> Image.Image | None:
        if img is None:
            return None
        # Textures ship at wildly different sizes (25px..2000px); downscale
        # once here so the per-frame fit-resizes stay cheap, and bake the
        # skin's tint color in (the game multiplies the texture by
        # CursorColor / the trail color).
        scale = 256 / max(img.width, img.height)
        if scale < 1:
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
        return _apply_tint(img, tint, 1.0)

    cursor_sprite = _prep_texture(skin.cursor_image, skin.cursor_color)
    cursor_trail_sprite = _prep_texture(skin.cursor_trail_image, skin.cursor_trail_color)

    return RenderContext(
        width=width,
        height=height,
        skin=skin,
        camera=camera,
        playfield_origin=(playfield_left, playfield_top),
        playfield_size=playfield_size,
        font_title=font_title,
        font_small=font_small,
        font_combo=font_combo,
        font_panel_label=font_panel_label,
        font_panel_value=font_panel_value,
        base_background=bg,
        portrait=portrait,
        cover_thumb=cover_thumb,
        border_resized=border_resized,
        border_origin=border_origin,
        cursor_sprite=cursor_sprite,
        cursor_trail_sprite=cursor_trail_sprite,
    )


def _fit_sprite(img: Image.Image, size: float, opacity: float) -> Image.Image | None:
    """Resizes a pre-tinted RGBA sprite to fit within `size` px (preserving
    aspect) and scales its alpha by `opacity`."""
    size = int(size)
    if size < 2 or opacity <= 0:
        return None
    scale = size / max(img.width, img.height)
    out = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.BILINEAR)
    if opacity < 1.0:
        out.putalpha(out.getchannel("A").point(lambda a: int(a * opacity)))
    return out


def _tinted_sprite(mask: Image.Image, size: int, color: tuple[int, int, int], opacity: float) -> Image.Image | None:
    size = int(size)
    if size < 2 or opacity <= 0:
        return None
    resized_mask = mask.resize((size, size), Image.BILINEAR)
    arr = np.empty((size, size, 4), dtype=np.uint8)
    arr[..., 0] = color[0]
    arr[..., 1] = color[1]
    arr[..., 2] = color[2]
    alpha = np.asarray(resized_mask, dtype=np.float32) * opacity
    arr[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGBA")


def _draw_default_border(draw, ox, oy, size, color, opacity):
    """Default playfield frame when no border-skin image is loaded: the
    official visualizer's border -- a thin rounded rectangle spanning the
    whole playfield (360x360 world units, stroke weight 2, corner radius 5,
    white at 30% opacity)."""
    alpha = int(255 * opacity)
    if alpha <= 0:
        return
    width = max(1, round(size * 2 / 360))
    radius = size * 5 / 360
    draw.rounded_rectangle(
        [ox, oy, ox + size - 1, oy + size - 1],
        radius=radius,
        outline=(*color, alpha),
        width=width,
    )


def draw_frame(ctx: RenderContext, state: TimelineState, map_meta: MapMetadata, replay: Replay) -> Image.Image:
    img = ctx.base_background.copy().convert("RGBA")
    overlay = Image.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    ox, oy = ctx.playfield_origin
    size = ctx.playfield_size
    skin = ctx.skin
    cursor_scene = state.cursor_xy

    # Notes/cursor are drawn on their own layer and clipped to the playfield
    # square before compositing: a note or cursor sitting exactly on a
    # corner or edge (grid lanes 0/2 sit flush on the playfield's own
    # boundary, not inset from it -- see camera.py) always has half its own
    # size extending past that boundary by construction, which without
    # clipping bled into the title block / side panels above and beside the
    # square instead of being cut off cleanly at the frame like the corner
    # brackets are.
    playfield_layer = Image.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 0))
    playfield_draw = ImageDraw.Draw(playfield_layer, "RGBA")

    _draw_background_effects(ctx, playfield_layer, playfield_draw, state, ox, oy, size)
    _draw_playfield_grid(ctx, playfield_layer, playfield_draw, cursor_scene, ox, oy)
    _draw_notes(ctx, playfield_layer, state, cursor_scene, ox, oy)
    if skin.hit_effects_enabled:
        _draw_hit_effects(ctx, playfield_draw, state, cursor_scene, ox, oy)
    _draw_cursor_trail(ctx, playfield_layer, playfield_draw, replay, state.time_ms, cursor_scene, ox, oy)
    _draw_cursor(ctx, playfield_layer, cursor_scene, ox, oy)
    _draw_miss_effect(ctx, playfield_layer, state, ox, oy, size)

    clip_mask = Image.new("L", (ctx.width, ctx.height), 0)
    ImageDraw.Draw(clip_mask).rectangle([ox, oy, ox + size, oy + size], fill=255)
    playfield_layer.putalpha(ImageChops.multiply(playfield_layer.getchannel("A"), clip_mask))
    overlay.alpha_composite(playfield_layer)

    if ctx.border_resized is not None:
        # border_origin may be negative (the image's margins/mascots hang
        # outside the playfield square, possibly past the canvas edge) and
        # alpha_composite rejects negative dest coords, so shift the
        # overflow into the `source` crop instead.
        bx, by = ctx.border_origin
        overlay.alpha_composite(ctx.border_resized, (max(0, bx), max(0, by)), (max(0, -bx), max(0, -by)))
    else:
        _draw_default_border(draw, ox, oy, size, skin.border_color, skin.border_opacity)

    _draw_combo(ctx, overlay, draw, state, ox, oy, size)
    _draw_side_panels(ctx, overlay, draw, state, replay, ox, oy, size)
    _draw_health_bar(ctx, draw, state, replay, ox, oy, size)

    if skin.song_info_enabled:
        _draw_title_block(ctx, draw, map_meta, replay, state, ox, oy, size)

    _draw_fail_vignette(ctx, overlay, state)

    return Image.alpha_composite(img, overlay).convert("RGB")


def draw_frame_blurred(ctx: RenderContext, timeline, map_meta: MapMetadata, replay: Replay,
                       t_ms: float, frame_dt_ms: float, shutter: float,
                       samples: int = 4) -> Image.Image:
    """High-quality motion blur via sub-frame accumulation: renders `samples`
    frames spread over the trailing `shutter` fraction of the frame interval
    and averages them (shutter 0.5 = a cinematic 180° shutter, 1.0 = max
    blur). Costs `samples`× the drawing time; stateless across frames, so it
    stays safe under the segmented multi-process pipeline in video.py."""
    if shutter <= 0 or samples <= 1:
        return draw_frame(ctx, timeline.state_at(t_ms), map_meta, replay)

    acc: np.ndarray | None = None
    for j in range(samples):
        tj = max(0.0, t_ms - frame_dt_ms * shutter * (j / samples))
        img = draw_frame(ctx, timeline.state_at(tj), map_meta, replay)
        arr = np.asarray(img, dtype=np.uint16)
        acc = arr if acc is None else acc + arr
    return Image.fromarray((acc // samples).astype(np.uint8), mode="RGB")


def draw_frame_tmix(ctx: RenderContext, timeline, map_meta: MapMetadata, replay: Replay,
                    t_ms: float, frame_dt_ms: float, intensity: float,
                    frames: int = 4) -> Image.Image:
    """Preview-only emulation of the fast motion-blur mode. The video itself
    gets blurred by ffmpeg's tmix filter (see video.py), which blends each
    frame with the previous `frames - 1` output frames using weights
    1, d, d², d³ (newest first, d = intensity, normalized). This draws those
    same past frames -- one full frame interval apart -- and blends them with
    the same weights, so the preview matches what the encoder produces."""
    if intensity <= 0 or frames <= 1:
        return draw_frame(ctx, timeline.state_at(t_ms), map_meta, replay)

    d = max(0.05, min(1.0, intensity))
    acc: np.ndarray | None = None
    total_weight = 0.0
    for j in range(frames):  # j frames back in time, weight d^j
        tj = max(0.0, t_ms - frame_dt_ms * j)
        img = draw_frame(ctx, timeline.state_at(tj), map_meta, replay)
        w = d ** j
        arr = np.asarray(img, dtype=np.float32) * w
        acc = arr if acc is None else acc + arr
        total_weight += w
    return Image.fromarray(np.clip(acc / total_weight, 0, 255).astype(np.uint8), mode="RGB")


def draw_intro_card(ctx: RenderContext, map_meta: MapMetadata, replay: Replay,
                    cover_bytes: bytes | None) -> Image.Image:
    """The static intro screen (see video.py's intro segment): big rounded
    cover art, title, player line and a stats row on the map background.
    Drawn once; the per-frame fade is applied by the caller."""
    img = ctx.base_background.copy().convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = ctx.width, ctx.height
    ref = min(w, h)

    font_big = ImageFont.truetype(FONT_BOLD, size=int(ref * 0.045))
    font_sub = ImageFont.truetype(FONT_REGULAR, size=int(ref * 0.028))
    font_stats = ImageFont.truetype(FONT_BOLD, size=int(ref * 0.026))

    # Cover art, center-cropped square with rounded corners.
    cover_size = int(ref * 0.34)
    cover_y = int(h * 0.24)
    if cover_bytes:
        cover = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
        side = min(cover.size)
        cover = cover.crop((
            (cover.width - side) // 2, (cover.height - side) // 2,
            (cover.width + side) // 2, (cover.height + side) // 2,
        )).resize((cover_size, cover_size), Image.LANCZOS)
        mask = Image.new("L", (cover_size, cover_size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, cover_size - 1, cover_size - 1], radius=int(cover_size * 0.08), fill=255
        )
        img.paste(cover, ((w - cover_size) // 2, cover_y), mask)

    y = cover_y + cover_size + int(ref * 0.05)

    title = map_meta.title or map_meta.song_name
    tw = draw.textlength(title, font=font_big)
    draw.text((w / 2 - tw / 2, y), title, font=font_big, fill=(255, 255, 255, 255))
    y += font_big.size + int(ref * 0.02)

    player = f"played by {replay.username}"
    played = replay.date_played
    if played is not None:
        player += played.astimezone().strftime(" · %d/%m/%Y")
    pw = draw.textlength(player, font=font_sub)
    draw.text((w / 2 - pw / 2, y), player, font=font_sub, fill=(190, 200, 230, 255))
    y += font_sub.size + int(ref * 0.045)

    acc = f"{replay.accuracy_pct:.1f}".rstrip("0").rstrip(".")
    stats = f"{acc}%   ·   {replay.total_score:,}"
    if abs(replay.speed - 1.0) > 1e-3:
        stats += f"   ·   S{_speed_suffix(replay.speed)} ({replay.speed:.2f}x)"
    sw = draw.textlength(stats, font=font_stats)
    draw.text((w / 2 - sw / 2, y), stats, font=font_stats, fill=(235, 238, 250, 255))

    return img.convert("RGB")


def intro_frame_brightness(progress: float) -> float:
    """Fade envelope for the intro: quick fade-in, fade-out to black over
    the last stretch before gameplay starts."""
    fade_in = min(1.0, progress / 0.12) if progress < 0.12 else 1.0
    fade_out = min(1.0, (1.0 - progress) / 0.15)
    return max(0.0, fade_in * fade_out)


def _draw_background_effects(ctx, overlay, draw, state, ox, oy, size):
    """Procedural background effects (tunnel/rays/chevron/grid) drawn behind
    the notes, inside the playfield square.

    These parameters (cell size, gap, speed multiplier, ray width...) are in
    the original game's own unrecoverable units (same caveat as approach_rate
    /spawn_distance -- see formats/rhr.py), so they're scaled here against
    the playfield size rather than reproduced exactly; treat as a stylistic
    approximation, not a pixel-accurate match.
    """
    skin = ctx.skin
    cx, cy = ox + size / 2.0, oy + size / 2.0
    t_s = state.time_ms / 1000.0

    # The official visualizer's ambient effect: two mirrored columns of
    # three dots (a "<" and a ">" of points at x = +/-1..2 cells) drifting
    # from far behind the hit plane towards/past the camera, three phases
    # offset by 1500ms, each cycling every 4500ms from z=-3000 to z=+3000
    # world units (-30..+30 cells), drawn in gray (205).
    for phase_ms in (0.0, 1500.0, 3000.0) if skin.ambient_dots_enabled else ():
        h = ((state.time_ms - phase_ms) % 4500.0) / 4500.0
        depth = 30.0 - h * 60.0  # +30 (far) -> -30 (past the camera)
        if depth <= -6.0:
            continue  # behind/too close to the camera; the game clips these
        for sign in (1.0, -1.0):
            for dot_x, dot_y in ((1.0, 1.0), (2.0, 0.0), (1.0, -1.0)):
                px, py, dot_scale = ctx.camera.project(sign * dot_x, dot_y, depth, state.cursor_xy)
                r = max(0.5, size * (3.0 / 360.0) * dot_scale / 2.0)
                if -r < px < size + r and -r < py < size + r:
                    draw.ellipse(
                        [ox + px - r, oy + py - r, ox + px + r, oy + py + r],
                        fill=(205, 205, 205, 235),
                    )

    if skin.background_tunnel_enabled and skin.background_tunnel_opacity > 0:
        alpha = int(255 * skin.background_tunnel_opacity)
        color = (255, 255, 255, alpha)
        width = max(1, int(size * 0.003))
        for corner in ((ox, oy), (ox + size, oy), (ox, oy + size), (ox + size, oy + size)):
            draw.line([corner, (cx, cy)], fill=color, width=width)

    if skin.background_rays_enabled and skin.background_rays_opacity > 0:
        alpha = int(255 * max(0.0, min(1.0, skin.background_rays_opacity * skin.background_rays_intensity)))
        n_rays = 12
        length = size * 0.75
        width = max(1, int(size * 0.004 * max(0.2, skin.background_rays_width / 10.0)))
        rotation = t_s * 8.0
        for i in range(n_rays):
            angle = math.radians(rotation + i * 360.0 / n_rays)
            x2 = cx + length * math.cos(angle)
            y2 = cy + length * math.sin(angle)
            draw.line([(cx, cy), (x2, y2)], fill=(255, 255, 255, alpha), width=width)

    if skin.background_chevron_enabled and skin.background_chevron_opacity > 0:
        alpha = int(255 * skin.background_chevron_opacity)
        gap = max(4.0, size * 0.05 * max(0.2, skin.background_chevron_gap / 20.0))
        speed = skin.background_chevron_speed_multiplier
        offset = (t_s * speed * gap * 0.6) % gap
        width = max(1, int(size * 0.003 * max(0.2, skin.background_chevron_width / 10.0)))
        max_r = size * 0.75
        i = 0
        r = offset
        while r < max_r:
            bbox = [cx - r, cy - r, cx + r, cy + r]
            draw.arc(bbox, start=205, end=335, fill=(255, 255, 255, alpha), width=width)
            i += 1
            r = offset + i * gap

    if skin.background_grid_enabled and skin.background_grid_opacity > 0:
        cell = max(6.0, size * 0.08 * max(0.2, skin.background_grid_cell_size / 50.0))
        offset = (t_s * skin.background_grid_speed_multiplier * cell * 0.5) % cell
        falloff = max(0.05, skin.background_grid_fade_falloff)
        gap_frac = max(0.0, min(0.9, skin.background_grid_center_gap / 100.0))
        width = max(1, int(skin.background_grid_line_width))
        color = (255, 255, 255)

        x = ox - cell + offset
        while x <= ox + size:
            dist = abs((x - cx) / (size / 2.0))
            a = skin.background_grid_opacity * max(0.0, 1.0 - dist * falloff)
            if dist > gap_frac and a > 0.01:
                draw.line([(x, oy), (x, oy + size)], fill=(*color, int(255 * a)), width=width)
            x += cell

        y = oy - cell + offset
        while y <= oy + size:
            dist = abs((y - cy) / (size / 2.0))
            a = skin.background_grid_opacity * max(0.0, 1.0 - dist * falloff)
            if dist > gap_frac and a > 0.01:
                draw.line([(ox, y), (ox + size, y)], fill=(*color, int(255 * a)), width=width)
            y += cell


def _draw_playfield_grid(ctx, overlay, draw, cursor_scene, ox, oy):
    skin = ctx.skin

    if skin.playfield_grid_enabled and skin.playfield_grid_opacity > 0:
        # A static reference grid on the playfield itself (distinct from the
        # animated BackgroundGrid effect): the note grid is 3x3 (x, y each
        # 0, 1, or 2), with lane centers 0 and 2 sitting exactly on the
        # playfield's own edge (verified against real replay cursor data --
        # cursor position at the moment of a hit lines up with
        # note_to_scene(note.x, note.y), not some inset variant). So the two
        # internal cell-boundary lines per axis sit at the midpoints between
        # lanes (0.5 and 1.5); the outer cell edges are just the playfield's
        # own border, already drawn separately.
        alpha = int(255 * skin.playfield_grid_opacity)
        color = (*skin.playfield_grid_color, alpha)
        width = max(1, int(skin.playfield_grid_thickness))
        for g in (0.5, 1.5):
            sx, sy = note_to_scene(g, 1.0)
            px, _, _ = ctx.camera.project(sx, sy, 0.0, cursor_scene)
            draw.line([(ox + px, oy), (ox + px, oy + ctx.playfield_size)], fill=color, width=width)
            _, py, _ = ctx.camera.project(sy, sx, 0.0, cursor_scene)
            draw.line([(ox, oy + py), (ox + ctx.playfield_size, oy + py)], fill=color, width=width)


def _draw_notes(ctx, overlay, state, cursor_scene, ox, oy):
    skin = ctx.skin
    notes_sorted = sorted(state.notes, key=lambda ns: -ns.depth)
    for ns in notes_sorted:
        sx, sy = note_to_scene(ns.note.x, ns.note.y)
        sx += ns.offset_x  # CHAOS wobble (zero without the mod)
        sy += ns.offset_y
        px, py, scale = ctx.camera.project(sx, sy, ns.depth, cursor_scene)
        # The playfield spans half_extent*2 cells (3.0 normally -- see
        # camera.py) and a note fills its whole 1-cell lane at the hit
        # plane, so its corners touch the border/neighbors. Misses fly past
        # the plane (scale > 1) and keep growing on the way out, exactly
        # like the game -- no clamping.
        max_r = ctx.playfield_size * (NOTE_CELL_FILL / (ctx.camera.half_extent * 2.0)) / 2.0
        r = max_r * scale * skin.note_scale
        cx, cy = ox + px, oy + py

        if skin.note_colors:
            color = skin.note_colors[ns.index % len(skin.note_colors)]
        else:
            color = skin.note_color

        opacity = max(0.0, min(1.0, ns.opacity * skin.note_opacity))
        if opacity <= 0 or r <= 0:
            continue

        sprite = _tinted_sprite(skin.note_mask, r * 2.0, color, opacity)
        if sprite is not None:
            overlay.alpha_composite(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2)))


def _draw_hit_effects(ctx, draw, state, cursor_scene, ox, oy):
    """Burst when a note is hit: its outline expands and fades at the hit
    plane while four small fragments fly out of the corners. Purely
    stylistic -- the real game despawns hit notes with no effect -- so it's
    optional (RuntimeSkin.hit_effects_enabled)."""
    skin = ctx.skin
    for hs in state.recent_hits:
        sx, sy = note_to_scene(hs.note.x, hs.note.y)
        px, py, scale = ctx.camera.project(sx, sy, 0.0, cursor_scene)
        max_r = ctx.playfield_size * (NOTE_CELL_FILL / (ctx.camera.half_extent * 2.0)) / 2.0
        r = max_r * scale * skin.note_scale
        if r <= 1:
            continue
        cx, cy = ox + px, oy + py

        if skin.note_colors:
            color = skin.note_colors[hs.index % len(skin.note_colors)]
        else:
            color = skin.note_color

        fade = (1.0 - hs.burst) ** 1.5
        alpha = int(255 * 0.85 * fade)
        if alpha <= 2:
            continue

        # Expanding rounded outline (the note's silhouette growing out).
        er = r * (1.0 + 0.55 * hs.burst)
        width = max(1, round(er * 0.10 * (1.0 - hs.burst * 0.5)))
        draw.rounded_rectangle(
            [cx - er, cy - er, cx + er, cy + er],
            radius=er * 0.28,
            outline=(*color, alpha),
            width=width,
        )

        # Corner fragments flying outward and shrinking.
        frag = r * 0.16 * (1.0 - hs.burst)
        if frag >= 1:
            dist = r * (0.9 + 1.1 * hs.burst)
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                fx, fy = cx + dx * dist * 0.7071, cy + dy * dist * 0.7071
                draw.rounded_rectangle(
                    [fx - frag, fy - frag, fx + frag, fy + frag],
                    radius=frag * 0.4,
                    fill=(*color, alpha),
                )


def _draw_cursor_trail(ctx, overlay, draw, replay, t_ms, cursor_scene, ox, oy):
    """The game's trail is a single smooth "snake": a continuous ribbon
    along the recent cursor path, cursor-wide at the head and tapering to a
    point at the tail, fading out along the way (see reference gameplay
    footage). Skins with a cursorTrailSkin/ texture instead stamp that
    texture along the path.

    The ribbon is drawn as one quad per path segment plus a rounding dot at
    each joint, all onto a dedicated layer that is alpha-composited once --
    plain ImageDraw fills *replace* rather than blend where they overlap, so
    same-color overlaps at the joints are harmless there but would darken or
    punch holes if drawn straight onto the shared overlay.

    The skin's CursorTrailFadeTimeSeconds is often very short (e.g. 0.05s,
    ~3 frames at 60fps -- barely visible), which doesn't match how
    pronounced the trail looks in actual gameplay recordings; a floor keeps
    it visually present when the trail is enabled at all.
    """
    skin = ctx.skin
    if not skin.cursor_trail_enabled or skin.cursor_trail_opacity <= 0 or skin.cursor_trail_scale <= 0:
        return
    # cursor_trail_scale is the GUI's trail-length control: it scales the
    # fade window after the floor, so 0.5 really is half the default length.
    fade_ms = max(250.0, skin.cursor_trail_fade_time_s * 1000.0) * skin.cursor_trail_scale
    # 0.0278 * playfield == the old 0.016 * height in landscape (playfield =
    # 0.575 * height there); playfield-relative so portrait stays consistent.
    base_r = ctx.playfield_size * 0.0278 * skin.cursor_scale

    if ctx.cursor_trail_sprite is not None:
        steps = 28
        for i in range(1, steps + 1):
            frac = i / steps
            past_t = t_ms - fade_ms * frac
            px, py, _ = ctx.camera.project(*replay.cursor_position_at(past_t), 0.0, cursor_scene)
            alpha = skin.cursor_trail_opacity * (1.0 - frac) ** 1.5
            r = base_r * (1.0 - frac * 0.85)
            sprite = _fit_sprite(ctx.cursor_trail_sprite, r * 2.0, alpha)
            if sprite is None:
                continue
            cx, cy = ox + px, oy + py
            overlay.alpha_composite(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2)))
        return

    steps = 24
    pts = []
    for i in range(steps + 1):
        frac = i / steps  # 0 = head (now) .. 1 = tail
        past_t = t_ms - fade_ms * frac
        px, py, _ = ctx.camera.project(*replay.cursor_position_at(past_t), 0.0, cursor_scene)
        pts.append((ox + px, oy + py))

    layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer, "RGBA")
    color = skin.cursor_trail_color
    head_w = base_r * 0.9

    for i in range(steps):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        seg_dx, seg_dy = x1 - x0, y1 - y0
        seg_len = math.hypot(seg_dx, seg_dy)
        w0 = head_w * (1.0 - i / steps)
        w1 = head_w * (1.0 - (i + 1) / steps)
        alpha = int(255 * skin.cursor_trail_opacity * (1.0 - i / steps) ** 1.2)
        if alpha <= 0:
            continue
        fill = (*color, alpha)
        if seg_len > 0.5:
            nx, ny = -seg_dy / seg_len, seg_dx / seg_len
            ldraw.polygon(
                [
                    (x0 + nx * w0, y0 + ny * w0),
                    (x1 + nx * w1, y1 + ny * w1),
                    (x1 - nx * w1, y1 - ny * w1),
                    (x0 - nx * w0, y0 - ny * w0),
                ],
                fill=fill,
            )
        # Round the joint so curves stay smooth and slow cursors still show
        # a stub instead of nothing.
        if w0 > 0.5:
            ldraw.ellipse([x0 - w0, y0 - w0, x0 + w0, y0 + w0], fill=fill)

    overlay.alpha_composite(layer)


def _draw_cursor(ctx, overlay, cursor_scene, ox, oy):
    skin = ctx.skin
    cx, cy, _ = ctx.camera.project(cursor_scene[0], cursor_scene[1], 0.0, cursor_scene)
    cx, cy = ox + cx, oy + cy
    cr = ctx.playfield_size * 0.0278 * skin.cursor_scale
    opacity = skin.cursor_opacity

    if ctx.cursor_sprite is not None:
        # Skin cursor texture (cursorSkin/), pre-tinted with CursorColor.
        core = _fit_sprite(ctx.cursor_sprite, cr * 2.0, opacity)
        if core is not None:
            overlay.alpha_composite(core, (int(cx - core.width / 2), int(cy - core.height / 2)))
        return

    if skin.cursor_mask is not None:
        core = _tinted_sprite(skin.cursor_mask, cr * 2.0, skin.cursor_color, opacity)
        if core is not None:
            overlay.alpha_composite(core, (int(cx - core.width / 2), int(cy - core.height / 2)))


def _draw_combo(ctx, overlay, draw, state, ox, oy, size):
    skin = ctx.skin
    if not skin.combo_text_enabled:
        return
    combo_text = f"{state.combo}"
    tw = draw.textlength(combo_text, font=ctx.font_combo)
    tx = ox + size / 2 - tw / 2
    ty = oy + size * (skin.combo_text_vertical_position_percent / 100.0) - ctx.font_combo.size / 2
    alpha = int(255 * skin.combo_text_opacity)
    draw.text((tx, ty), combo_text, font=ctx.font_combo, fill=(*skin.combo_text_color, alpha))


def _draw_hex_combo_entry(ctx, draw, x_center, y_center, combo_value):
    """The left panel's combo readout gets its own hexagonal ring (the real
    game's "ComboRing" styling applies here, not around the big center
    combo -- see reference recording)."""
    skin = ctx.skin
    text = f"{combo_value}x"
    r = ctx.font_panel_value.size * 1.3

    if skin.combo_ring_opacity > 0:
        color = (*skin.combo_ring_color, int(255 * skin.combo_ring_opacity))
        draw.regular_polygon((x_center, y_center, r), n_sides=6, rotation=0, outline=color, width=max(2, int(r * 0.08)))

    tw = draw.textlength(text, font=ctx.font_panel_value)
    draw.text((x_center - tw / 2, y_center - ctx.font_panel_value.size / 2), text, font=ctx.font_panel_value, fill=(*skin.interface_text_color, 255))


def _draw_panel_card(ctx, overlay, draw, x_center, top_y, rows):
    """One continuous rounded-rect card per side holding every enabled stat
    row, rather than a separate box per row (matches the reference: a single
    card, not a stack of pill-shaped boxes)."""
    skin = ctx.skin
    if not rows:
        return

    row_gap = ctx.font_panel_value.size * 2.2
    pad = max(6.0, skin.panel_gap)

    max_w = 0.0
    for label, value, is_combo in rows:
        max_w = max(max_w, draw.textlength(label, font=ctx.font_panel_label))
        if not is_combo:
            max_w = max(max_w, draw.textlength(value, font=ctx.font_panel_value))
    card_w = max_w + pad * 4
    card_h = row_gap * len(rows) + pad * 2

    if skin.panel_background_opacity > 0:
        card = Image.new("RGBA", (max(1, int(card_w)), max(1, int(card_h))), (0, 0, 0, 0))
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, card.size[0] - 1, card.size[1] - 1], radius=card_h * 0.06, fill=255
        )
        fill = Image.new("RGBA", card.size, (*skin.panel_color, int(255 * skin.panel_background_opacity)))
        card.paste(fill, (0, 0), mask)
        if skin.panel_angle:
            card = card.rotate(skin.panel_angle, expand=True, resample=Image.BICUBIC)
        overlay.alpha_composite(card, (int(x_center - card.width / 2), int(top_y - pad)))

    y = top_y + row_gap / 2
    for label, value, is_combo in rows:
        if is_combo:
            _draw_hex_combo_entry(ctx, draw, x_center, y, value)
        else:
            lw = draw.textlength(label, font=ctx.font_panel_label)
            vw = draw.textlength(value, font=ctx.font_panel_value)
            draw.text((x_center - lw / 2, y - ctx.font_panel_value.size), label, font=ctx.font_panel_label, fill=(138, 138, 146, 255))
            draw.text((x_center - vw / 2, y), value, font=ctx.font_panel_value, fill=(*skin.interface_text_color, 255))
        y += row_gap


def _draw_side_panels(ctx, overlay, draw, state, replay, ox, oy, size):
    skin = ctx.skin
    # Measured from the reference recording: panel text centers sit about
    # 0.24 playfield-widths outside the border, vertically ~47% down it.
    # Portrait canvases have no room beside the near-full-width playfield,
    # so the two columns sit below it instead (under the health bar).
    left_x = ox - size * 0.24
    right_x = ox + size + size * 0.24
    if ctx.portrait:
        left_x = ox + size * 0.25
        right_x = ox + size * 0.75
    row_gap = ctx.font_panel_value.size * 2.2

    left_rows = []
    if skin.left_panel_combo_ring_enabled:
        left_rows.append(("COMBO", state.combo, True))
    if skin.left_panel_pauses_enabled:
        # Pause events aren't recorded in the .rhr format (confirmed against
        # the official parser -- see formats/rhr.py) -- always shown as 0.
        left_rows.append(("PAUSES", "0", False))
    if skin.left_panel_accuracy_enabled:
        # The game shows one decimal, dropping it when exactly whole
        # ("99.3%", "100%").
        acc = f"{state.accuracy_pct:.1f}".rstrip("0").rstrip(".")
        left_rows.append(("ACCURACY", f"{acc}%", False))

    if ctx.portrait:
        panels_top = oy + size + ctx.height * 0.075
        left_top = right_top_portrait = panels_top
    else:
        left_top = oy + size * 0.47 - (len(left_rows) - 1) * row_gap / 2
    _draw_panel_card(ctx, overlay, draw, left_x, left_top, left_rows)

    # The replay stores only the *final* score/points totals, not their
    # accrual over time (and in-game accrual isn't linear -- combo
    # multipliers ramp), so mid-play values are interpolated in proportion
    # to hits so far; they land exactly on the real totals by the end.
    hit_frac = state.hits_so_far / replay.hits if replay.hits else 0.0

    right_rows = []
    if skin.right_panel_score_enabled:
        right_rows.append(("SCORE", f"{int(replay.total_score * hit_frac):,}", False))
    if skin.right_panel_points_enabled:
        right_rows.append(("POINTS", f"{replay.points * hit_frac:.0f}", False))
    if skin.right_panel_misses_enabled:
        right_rows.append(("MISSES", f"{state.misses_so_far}", False))
    if skin.right_panel_notes_enabled:
        # The game counts hits over notes *passed so far*, not the map total
        # ("856/869" mid-song).
        right_rows.append(("NOTES", f"{state.hits_so_far}/{state.resolved_count}", False))

    if ctx.portrait:
        right_top = right_top_portrait
    else:
        right_top = oy + size * 0.47 - (len(right_rows) - 1) * row_gap / 2
    _draw_panel_card(ctx, overlay, draw, right_x, right_top, right_rows)


def _draw_title_block(ctx, draw, map_meta, replay, state, ox, oy, size):
    """The game's top HUD, as in its replay viewer: a bold centered
    "Watching <player> play <title>" line, the elapsed/total time under it,
    and a thin progress bar (playfield-wide) right below -- above the
    playfield, not under it."""
    w = ctx.width
    skin = ctx.skin

    title = f"Watching {replay.username} play {map_meta.title}"
    tw = draw.textlength(title, font=ctx.font_title)
    draw.text((w / 2 - tw / 2, ctx.height * 0.022), title, font=ctx.font_title, fill=(255, 255, 255, 255))

    time_str = f"{_format_time(state.time_ms)} / {_format_time(replay.length_ms)}"
    tsw = draw.textlength(time_str, font=ctx.font_small)
    draw.text((w / 2 - tsw / 2, ctx.height * 0.058), time_str, font=ctx.font_small, fill=(235, 235, 240, 255))

    if skin.progress_bar_enabled:
        bar_h = max(3, round(ctx.height * 0.0065))
        bar_y = round(ctx.height * 0.092)
        length = replay.length_ms or 1.0
        prog = min(1.0, max(0.0, state.time_ms / length))
        alpha = int(255 * skin.progress_bar_alpha)
        draw.rounded_rectangle([ox, bar_y, ox + size, bar_y + bar_h], radius=bar_h / 2, fill=(255, 255, 255, 60))
        if prog > 0:
            draw.rounded_rectangle(
                [ox, bar_y, ox + max(bar_h, size * prog), bar_y + bar_h],
                radius=bar_h / 2,
                fill=(*skin.progress_bar_color, alpha),
            )


def _draw_miss_effect(ctx, overlay, state, ox, oy, size):
    """A brief red vignette pulse around the playfield edges on miss -- not a
    flat tint (MissEffectOpacity is often 1.0, which as a flat fill over the
    whole square hid the gameplay entirely; a vignette keeps the center
    clear regardless of how strong the configured opacity is)."""
    skin = ctx.skin
    if skin.miss_effect_opacity <= 0 or state.last_miss_ms is None:
        return
    duration = 300.0
    dt = state.time_ms - state.last_miss_ms
    if dt < 0 or dt > duration:
        return
    peak = skin.miss_effect_opacity * (1.0 - dt / duration)
    if peak <= 0.01:
        return

    yy, xx = np.mgrid[0:size, 0:size]
    dist = np.sqrt(((xx - size / 2.0) / (size / 2.0)) ** 2 + ((yy - size / 2.0) / (size / 2.0)) ** 2)
    vign_alpha = (np.clip(dist, 0.0, 1.0) ** 2 * peak * 200).astype(np.uint8)

    tint = Image.new("RGBA", (size, size), (255, 40, 40, 0))
    tint.putalpha(Image.fromarray(vign_alpha, mode="L"))
    overlay.alpha_composite(tint, (ox, oy))


def _draw_health_bar(ctx, draw, state, replay, ox, oy, size):
    """Below the playfield, like the game: a rounded playfield-wide health
    bar, then the current grade (with the speed-modifier suffix, e.g.
    "S--" at 0.8x) centered under it."""
    skin = ctx.skin
    grade_y = oy + size + ctx.height * 0.028

    if skin.health_bar_enabled:
        bar_h = max(3, round(ctx.height * 0.011))
        bar_y = oy + size + round(ctx.height * 0.028)
        frac = max(0.0, min(1.0, state.health_pct / 100.0))
        alpha = int(255 * skin.health_bar_alpha)
        if frac > 0:
            draw.rounded_rectangle(
                [ox, bar_y, ox + max(bar_h, size * frac), bar_y + bar_h],
                radius=bar_h / 2,
                fill=(*skin.health_bar_color, alpha),
            )
        grade_y = bar_y + bar_h + ctx.height * 0.012

    if not skin.speed_text_enabled:
        return
    # The bottom label is the game's *speed* notation ("S" = speed, always a
    # single S: S--- .. S++++), not the accuracy grade, plus the mod codes.
    label = "S" + _speed_suffix(replay.speed)
    if ctx.mods_label:
        label += "  " + ctx.mods_label
    gw = draw.textlength(label, font=ctx.font_small)
    draw.text((ox + size / 2 - gw / 2, grade_y), label, font=ctx.font_small, fill=(255, 255, 255, 255))


def _draw_fail_vignette(ctx, overlay, state):
    skin = ctx.skin
    if skin.fail_vignette_opacity <= 0:
        return
    danger = max(0.0, 1.0 - state.health_pct / 100.0)
    if danger <= 0:
        return
    alpha_peak = skin.fail_vignette_opacity * danger
    if alpha_peak <= 0.002:
        return

    w, h = ctx.width, ctx.height
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt(((xx - w / 2.0) / (w / 2.0)) ** 2 + ((yy - h / 2.0) / (h / 2.0)) ** 2)
    vign_alpha = (np.clip(dist, 0.0, 1.0) ** 2 * alpha_peak * 255).astype(np.uint8)

    tint = Image.new("RGBA", (w, h), (200, 20, 20, 0))
    tint.putalpha(Image.fromarray(vign_alpha, mode="L"))
    overlay.alpha_composite(tint)
