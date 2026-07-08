"""Parser for CapoRhythia .rhs skin files.

Like .rhm, a .rhs is a plain zip archive:
  - "config"            -> JSON, `{"SettingName": {"Value": ...}, ...}` (142
                            keys covering nearly every visual/audio/online
                            setting the game has -- see `raw` below)
  - "noteSkin/*.obj"     -> Wavefront mesh for the note shape (optional)
  - "borderSkin/*.png"   -> playfield border/frame texture (optional)
  - "hitSound/*.wav"     -> hit sound effect (optional)
  - "backgrounds/N/*.png" -> background character art, N matching the index
                             of that layer in the config's BackgroundImages
                             array (optional, any number of layers)

Several config fields (e.g. ColorSet, CursorSkin, CursorTrailSkin) reference
paths under the game's own install ("Textures/Game/...") rather than files
bundled in the zip -- those can't be resolved from the skin alone, so the
corresponding asset bytes are left as None and callers fall back to defaults.

Only a subset of the 142 config fields is surfaced as typed attributes here
(the ones that affect what gets drawn in a rendered video). Everything is
still available via `Skin.raw` for anyone who wants more.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field


def _rgb(raw: dict, prefix: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        return (
            int(raw[f"{prefix}Red"]),
            int(raw[f"{prefix}Green"]),
            int(raw[f"{prefix}Blue"]),
        )
    except KeyError:
        return default


@dataclass
class BackgroundLayer:
    image_bytes: bytes
    center_x: float
    center_y: float
    scale_x: float
    scale_y: float
    rotation: float
    flip_horizontal: bool
    tint_rgb: tuple[int, int, int]
    tint_opacity: float


@dataclass
class Skin:
    raw: dict

    note_obj_bytes: bytes | None
    border_image_bytes: bytes | None
    cursor_image_bytes: bytes | None
    cursor_trail_image_bytes: bytes | None
    hit_sound_bytes: bytes | None
    miss_sound_bytes: bytes | None
    background_layers: list[BackgroundLayer]

    note_scale: float
    note_opacity: float
    approach_rate: float
    spawn_distance: float
    camera_fov: float
    fade_length_s: float
    parallax: float
    grid_parallax: float

    cursor_color: tuple[int, int, int]
    cursor_scale: float
    cursor_opacity: float
    cursor_trail_enabled: bool
    cursor_trail_color: tuple[int, int, int]
    cursor_trail_inherit_from_cursor: bool
    cursor_trail_opacity: float
    cursor_trail_fade_time_s: float
    cursor_trail_spacing_multiplier: float
    cursor_trail_shrink_over_time: bool

    background_rgb: tuple[int, int, int]
    background_accent_rgb: tuple[int, int, int]
    background_accent_from_hit_note: bool

    background_tunnel_enabled: bool
    background_tunnel_opacity: float

    background_chevron_enabled: bool
    background_chevron_opacity: float
    background_chevron_width: float
    background_chevron_gap: float
    background_chevron_speed_multiplier: float
    background_chevron_small_size: float
    background_chevron_large_size: float

    background_rays_enabled: bool
    background_rays_intensity: float
    background_rays_opacity: float
    background_rays_width: float

    background_grid_enabled: bool
    background_grid_opacity: float
    background_grid_line_width: float
    background_grid_cell_size: float
    background_grid_center_gap: float
    background_grid_speed_multiplier: float
    background_grid_fade_falloff: float

    hit_sound_volume: float
    miss_sound_combo_threshold: int

    border_color: tuple[int, int, int]
    border_opacity: float

    playfield_grid_enabled: bool
    playfield_grid_color: tuple[int, int, int]
    playfield_grid_opacity: float
    playfield_grid_thickness: float

    combo_text_color: tuple[int, int, int]
    combo_text_opacity: float
    combo_text_font_size: float
    combo_text_vertical_position_percent: float

    combo_ring_color: tuple[int, int, int]
    combo_ring_opacity: float

    panel_color: tuple[int, int, int]
    panel_opacity: float
    panel_background_opacity: float
    panel_gap: float
    panel_angle: float

    interface_text_color: tuple[int, int, int]
    interface_values_font_size: float

    song_info_enabled: bool
    progress_bar_enabled: bool
    progress_bar_color: tuple[int, int, int]
    progress_bar_alpha: float

    health_bar_enabled: bool
    health_bar_color: tuple[int, int, int]
    health_bar_alpha: float
    fail_vignette_opacity: float
    miss_effect_opacity: float

    left_panel_accuracy_enabled: bool
    left_panel_combo_ring_enabled: bool
    left_panel_pauses_enabled: bool
    right_panel_notes_enabled: bool
    right_panel_misses_enabled: bool
    right_panel_score_enabled: bool
    right_panel_points_enabled: bool

    # Note colors cycled per note, from the skin's colorset (comma-separated
    # hex colors, matching the game client's `user/colorsets/*.txt` format).
    # The config's `ColorSet` usually references a file in the game's install
    # dir rather than the zip (see module docstring), so this is only
    # populated when the zip happens to bundle a colorset file; callers may
    # also fill it from a user-supplied colorset .txt via parse_colorset().
    note_colors: list[tuple[int, int, int]] = field(default_factory=list)


def parse_colorset(text: str) -> list[tuple[int, int, int]]:
    """Parses a Rhythia colorset: hex colors (rrggbb, optional # prefix or
    aa suffix) separated by commas and/or whitespace, cycled per note."""
    colors: list[tuple[int, int, int]] = []
    for token in text.replace(",", " ").split():
        token = token.lstrip("#").strip()
        if len(token) in (6, 8):
            try:
                colors.append((int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16)))
            except ValueError:
                continue
    return colors


def _colorset_stem(filename: str) -> str:
    """Normalized comparison key for a colorset filename: extension dropped,
    the game's re-import timestamp suffixes stripped (CapoRhythia renames
    imported files to e.g. "Teto-20260324-222051-20260614-202904.txt"),
    case folded."""
    import os
    import re

    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"(-\d{8}-\d{6})+$", "", stem)
    return stem.casefold().strip()


def resolve_colorset_path(install_dir: str, colorset_ref: str) -> str | None:
    """Locates the colorset file a skin config references.

    References come in two shapes (both observed in real CapoRhythia
    configs): an absolute path for user-imported colorsets (usable directly
    when it exists -- it won't when the skin came from another machine), and
    a path relative to the game's own data dir for stock ones (e.g.
    "Textures/Game/colorsets/Sakurai.txt"). Different clients also lay
    their folders out differently (Rhythia/Client uses "user/colorsets/"),
    so after trying the known layouts fall back to a bounded search of the
    whole tree, matching filenames with the game's re-import timestamp
    suffixes stripped so "Teto.txt" finds "Teto-20260324-222051.txt" and
    vice versa.
    """
    import os

    ref = colorset_ref.replace("\\", "/").strip()
    basename = os.path.basename(ref)
    if not basename:
        return None

    if os.path.isabs(colorset_ref) and os.path.isfile(colorset_ref):
        return colorset_ref

    if not install_dir:
        return None

    candidates = [
        os.path.join(install_dir, *ref.split("/")),
        os.path.join(install_dir, "user", "colorsets", basename),
        os.path.join(install_dir, "colorsets", basename),
        os.path.join(install_dir, "skins", "colorsets", basename),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    want = _colorset_stem(basename)
    visited_dirs = 0
    for root, _dirs, files in os.walk(install_dir):
        visited_dirs += 1
        if visited_dirs > 2000:
            break
        for f in files:
            if f == basename:
                return os.path.join(root, f)
            if f.lower().endswith(".txt") and _colorset_stem(f) == want:
                return os.path.join(root, f)
    return None


def _find_one(zf: zipfile.ZipFile, prefix: str) -> bytes | None:
    for name in zf.namelist():
        if name.startswith(prefix) and not name.endswith("/"):
            return zf.read(name)
    return None


def _load_background_layers(zf: zipfile.ZipFile, raw: dict) -> list[BackgroundLayer]:
    entries = raw.get("BackgroundImages", []) or []
    layers: list[BackgroundLayer] = []
    for i, entry in enumerate(entries):
        image_bytes = _find_one(zf, f"backgrounds/{i}/")
        if image_bytes is None:
            continue
        layers.append(
            BackgroundLayer(
                image_bytes=image_bytes,
                center_x=float(entry.get("CenterX", 0.5)),
                center_y=float(entry.get("CenterY", 0.5)),
                scale_x=float(entry.get("ScaleX", 0.3)),
                scale_y=float(entry.get("ScaleY", 0.3)),
                rotation=float(entry.get("Rotation", 0.0)),
                flip_horizontal=bool(entry.get("FlipHorizontal", False)),
                tint_rgb=(
                    int(entry.get("TintRed", 255)),
                    int(entry.get("TintGreen", 255)),
                    int(entry.get("TintBlue", 255)),
                ),
                tint_opacity=float(entry.get("TintOpacity", 1.0)),
            )
        )
    return layers


def load(path: str) -> Skin:
    with zipfile.ZipFile(path, "r") as zf:
        raw_data = zf.read("config")
        raw_wrapped = json.loads(raw_data)
        raw = {k: v.get("Value") for k, v in raw_wrapped.items()}

        note_obj_bytes = _find_one(zf, "noteSkin/")
        border_image_bytes = _find_one(zf, "borderSkin/")
        cursor_image_bytes = _find_one(zf, "cursorSkin/")
        cursor_trail_image_bytes = _find_one(zf, "cursorTrailSkin/")
        hit_sound_bytes = _find_one(zf, "hitSound/")
        miss_sound_bytes = _find_one(zf, "missSound/")
        background_layers = _load_background_layers(zf, raw)

        note_colors: list[tuple[int, int, int]] = []
        for name in zf.namelist():
            if "colorset" in name.lower() and not name.endswith("/"):
                note_colors = parse_colorset(zf.read(name).decode("utf-8", errors="replace"))
                break

    return Skin(
        raw=raw,
        note_obj_bytes=note_obj_bytes,
        border_image_bytes=border_image_bytes,
        cursor_image_bytes=cursor_image_bytes,
        cursor_trail_image_bytes=cursor_trail_image_bytes,
        hit_sound_bytes=hit_sound_bytes,
        miss_sound_bytes=miss_sound_bytes,
        background_layers=background_layers,
        note_colors=note_colors,
        note_scale=float(raw.get("NoteScale", 1.0)),
        note_opacity=float(raw.get("NoteOpacity", 1.0)),
        approach_rate=float(raw.get("ApproachRate", 29.0)),
        spawn_distance=float(raw.get("SpawnDistance", 12.0)),
        camera_fov=float(raw.get("CameraFov", 67.0)),
        fade_length_s=float(raw.get("FadeLength", 0.1)),
        parallax=float(raw.get("Parallax", 2.5)),
        grid_parallax=float(raw.get("GridParallax", 0.0)),
        cursor_color=_rgb(raw, "CursorColor", (255, 255, 255)),
        cursor_scale=float(raw.get("CursorScale", 1.0)),
        cursor_opacity=float(raw.get("CursorOpacity", 1.0)),
        cursor_trail_enabled=bool(raw.get("CursorTrailEnabled", True)),
        cursor_trail_color=_rgb(raw, "CursorTrailColor", (255, 255, 255)),
        cursor_trail_inherit_from_cursor=bool(raw.get("CursorTrailInheritFromCursor", True)),
        cursor_trail_opacity=float(raw.get("CursorTrailOpacity", 0.3)),
        cursor_trail_fade_time_s=float(raw.get("CursorTrailFadeTimeSeconds", 0.05)),
        cursor_trail_spacing_multiplier=float(raw.get("CursorTrailSpacingMultiplier", 0.05)),
        cursor_trail_shrink_over_time=bool(raw.get("CursorTrailShrinkOverTime", True)),
        background_rgb=_rgb(raw, "Background", (10, 14, 23)),
        background_accent_rgb=_rgb(raw, "BackgroundAccent", (0, 0, 0)),
        background_accent_from_hit_note=bool(raw.get("BackgroundAccentFromHitNote", False)),
        background_tunnel_enabled=bool(raw.get("BackgroundTunnelEnabled", False)),
        background_tunnel_opacity=float(raw.get("BackgroundTunnelOpacity", 0.3)),
        background_chevron_enabled=bool(raw.get("BackgroundChevronEnabled", False)),
        background_chevron_opacity=float(raw.get("BackgroundChevronOpacity", 0.3)),
        background_chevron_width=float(raw.get("BackgroundChevronWidth", 10.0)),
        background_chevron_gap=float(raw.get("BackgroundChevronGap", 20.0)),
        background_chevron_speed_multiplier=float(raw.get("BackgroundChevronSpeedMultiplier", 1.0)),
        background_chevron_small_size=float(raw.get("BackgroundChevronSmallSize", 0.5)),
        background_chevron_large_size=float(raw.get("BackgroundChevronLargeSize", 1.0)),
        background_rays_enabled=bool(raw.get("BackgroundRaysEnabled", False)),
        background_rays_intensity=float(raw.get("BackgroundRaysIntensity", 1.0)),
        background_rays_opacity=float(raw.get("BackgroundRaysOpacity", 0.3)),
        background_rays_width=float(raw.get("BackgroundRaysWidth", 10.0)),
        background_grid_enabled=bool(raw.get("BackgroundGridEnabled", False)),
        background_grid_opacity=float(raw.get("BackgroundGridOpacity", 0.2)),
        background_grid_line_width=float(raw.get("BackgroundGridLineWidth", 1.0)),
        background_grid_cell_size=float(raw.get("BackgroundGridCellSize", 50.0)),
        background_grid_center_gap=float(raw.get("BackgroundGridCenterGap", 0.0)),
        background_grid_speed_multiplier=float(raw.get("BackgroundGridSpeedMultiplier", 1.0)),
        background_grid_fade_falloff=float(raw.get("BackgroundGridFadeFalloff", 1.0)),
        hit_sound_volume=float(raw.get("HitSoundVolume", 100.0)),
        miss_sound_combo_threshold=int(raw.get("MissSoundComboThreshold", 0)),
        border_color=_rgb(raw, "BorderColor", (255, 255, 255)),
        border_opacity=float(raw.get("BorderOpacity", 1.0)),
        playfield_grid_enabled=bool(raw.get("PlayfieldGridEnabled", False)),
        playfield_grid_color=_rgb(raw, "PlayfieldGridColor", (255, 255, 255)),
        playfield_grid_opacity=float(raw.get("PlayfieldGridOpacity", 0.1)),
        playfield_grid_thickness=float(raw.get("PlayfieldGridThickness", 1.0)),
        combo_text_color=_rgb(raw, "PlayfieldComboTextColor", (255, 255, 255)),
        combo_text_opacity=float(raw.get("PlayfieldComboTextOpacity", 1.0)),
        combo_text_font_size=float(raw.get("PlayfieldComboTextFontSize", 100.0)),
        combo_text_vertical_position_percent=float(raw.get("PlayfieldComboTextVerticalPositionPercent", 20.0)),
        combo_ring_color=_rgb(raw, "ComboRingColor", (255, 255, 255)),
        combo_ring_opacity=float(raw.get("ComboRingOpacity", 0.0)),
        panel_color=_rgb(raw, "PanelColor", (255, 255, 255)),
        panel_opacity=float(raw.get("PanelOpacity", 1.0)),
        panel_background_opacity=float(raw.get("PanelBackgroundOpacity", 0.0)),
        panel_gap=float(raw.get("PanelGap", 0.0)),
        panel_angle=float(raw.get("PanelAngle", 0.0)),
        interface_text_color=_rgb(raw, "InterfaceTextColor", (255, 255, 255)),
        interface_values_font_size=float(raw.get("InterfaceValuesFontSize", 1.0)),
        song_info_enabled=bool(raw.get("SongInfoEnabled", True)),
        progress_bar_enabled=bool(raw.get("SongProgressBarEnabled", True)),
        progress_bar_color=_rgb(raw, "SongProgressBarColor", (255, 255, 255)),
        progress_bar_alpha=float(raw.get("SongProgressBarAlpha", 0.86)),
        health_bar_enabled=bool(raw.get("HealthBarEnabled", False)),
        health_bar_color=_rgb(raw, "HealthBarColor", (255, 255, 255)),
        health_bar_alpha=float(raw.get("HealthBarAlpha", 1.0)),
        fail_vignette_opacity=float(raw.get("FailVignetteOpacity", 0.0)),
        miss_effect_opacity=float(raw.get("MissEffectOpacity", 0.0)),
        left_panel_accuracy_enabled=bool(raw.get("LeftPanelAccuracyEnabled", True)),
        left_panel_combo_ring_enabled=bool(raw.get("LeftPanelComboRingEnabled", False)),
        left_panel_pauses_enabled=bool(raw.get("LeftPanelPausesEnabled", False)),
        right_panel_notes_enabled=bool(raw.get("RightPanelNotesEnabled", True)),
        right_panel_misses_enabled=bool(raw.get("RightPanelMissesEnabled", False)),
        right_panel_score_enabled=bool(raw.get("RightPanelScoreEnabled", False)),
        right_panel_points_enabled=bool(raw.get("RightPanelPointsEnabled", False)),
    )
