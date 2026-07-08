from __future__ import annotations

import os
import tempfile
import time
import traceback

from PyQt5.QtCore import (
    QSettings, Qt, QThread, QTimer, QUrl, pyqtSignal
)

try:
    # WAV looping for the animated preview. QtMultimedia needs a working
    # audio backend at runtime; when it's missing the preview stays silent.
    from PyQt5.QtMultimedia import QSoundEffect
except ImportError:  # pragma: no cover
    QSoundEffect = None
from PyQt5.QtGui import (
    QColor, QDesktopServices, QImage, QPainter, QPainterPath, QPixmap
)
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..formats import colorsets, locate, rhm, rhr, rhs
from ..render.video import render_video
from ..sim.timeline import DEFAULT_APPROACH_RATE, DEFAULT_SPAWN_DISTANCE
from .styles import build_stylesheet

RESOLUTIONS = {
    "1920×1080 (60 fps)": (1920, 1080, 60),
    "1280×720  (60 fps)": (1280, 720, 60),
    "1920×1080 (30 fps)": (1920, 1080, 30),
    "1280×720  (30 fps)": (1280, 720, 30),
    "1080×1920 (60 fps) — vertical": (1080, 1920, 60),
    "1080×1920 (30 fps) — vertical": (1080, 1920, 30),
    "720×1280  (60 fps) — vertical": (720, 1280, 60),
}

QUALITIES = {
    "Fast (recommended)": "fast",
    "Balanced": "balanced",
    "High quality (slower)": "quality",
}

CODECS = {
    "H.264 (max compatibility)": "h264",
    "H.265 / HEVC (smaller files)": "hevc",
    "AV1 (best compression)": "av1",
}

HW_ACCELS = {
    "Auto (detect GPU)": "auto",
    "NVIDIA (NVENC)": "nvenc",
    "VAAPI (AMD/Intel)": "vaapi",
    "Intel (QSV)": "qsv",
    "CPU only": "none",
}

AUDIO_BITRATES = {
    "128 kbps": "128k",
    "192 kbps": "192k",
    "256 kbps": "256k",
    "320 kbps": "320k",
}

DIFFICULTY_NAMES = {0: "N/A", 1: "Easy", 2: "Medium", 3: "Hard", 4: "Logic", 5: "Tasukete"}

MOTION_BLUR_MODES = {
    "Off": "off",
    "Fast (ffmpeg filter)": "filter",
    "High quality (≈4× slower)": "subframe",
}

# HUD elements the user can show/hide, mapped to RuntimeSkin flags.
# Checked = always drawn (even if the skin hides it — e.g. POINTS),
# unchecked = hidden.
HUD_ELEMENTS = [
    ("Song title & time", "song_info_enabled"),
    ("Progress bar", "progress_bar_enabled"),
    ("Center combo", "combo_text_enabled"),
    ("Combo ring", "left_panel_combo_ring_enabled"),
    ("Pauses", "left_panel_pauses_enabled"),
    ("Accuracy", "left_panel_accuracy_enabled"),
    ("Score", "right_panel_score_enabled"),
    ("Points", "right_panel_points_enabled"),
    ("Misses", "right_panel_misses_enabled"),
    ("Notes", "right_panel_notes_enabled"),
    ("Health bar", "health_bar_enabled"),
    ("Speed label (S++++)", "speed_text_enabled"),
]

# Color presets: note colors cycle per note (like the game's colorsets);
# cursor/trail/border are single colors. Users can save their own, and the
# colorsets found in the Rhythia install (plus the ones bundled with the
# app) appear automatically with this prefix.
GAME_PRESET_PREFIX = "Rhythia: "
FROM_SKIN_PRESET = "From skin / colorset"
BUILTIN_COLOR_PRESETS = {
    "Classic White": {"notes": ["#ffffff"], "cursor": "#ffffff", "trail": "#ffffff", "border": "#ffffff"},
    "Rhythia Pink": {"notes": ["#ff0059", "#ffd8e6"], "cursor": "#ffffff", "trail": "#ff0059", "border": "#ffffff"},
    "Sakura": {"notes": ["#ffb7c5", "#ff69b4", "#ffffff"], "cursor": "#ffd8e6", "trail": "#ffb7c5", "border": "#ffc8f0"},
    "Teto": {"notes": ["#b21f3c", "#f4f4f5"], "cursor": "#ffffff", "trail": "#b21f3c", "border": "#f4f4f5"},
    "Neon": {"notes": ["#00ffea", "#ff00e1"], "cursor": "#ffffff", "trail": "#00ffea", "border": "#00ffea"},
    "RGB": {"notes": ["#ff5555", "#55ff55", "#5555ff"], "cursor": "#ffffff", "trail": "#ffffff", "border": "#ffffff"},
    "Grayscale Fade": {"notes": ["#ffffff", "#dbdbdb", "#c4c4c4", "#adadad", "#969696", "#808080", "#696969"],
                        "cursor": "#ffffff", "trail": "#ffffff", "border": "#ffffff"},
}


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rounded_pixmap(pixmap: QPixmap, size: int, radius: int) -> QPixmap:
    """Center-crop a pixmap to a square and clip it to rounded corners."""
    pm = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    pm = pm.copy((pm.width() - size) // 2, (pm.height() - size) // 2, size, size)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pm)
    painter.end()
    return out


class ColorSwatch(QPushButton):
    """A clickable color square that opens a color picker."""

    colorChanged = pyqtSignal()

    def __init__(self, hex_color: str = "#ffffff"):
        super().__init__()
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self._hex = hex_color
        self._apply_style()
        self.clicked.connect(self._pick)

    def hex(self) -> str:
        return self._hex

    def set_hex(self, hex_color: str):
        self._hex = hex_color
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._hex}; border: 2px solid rgba(255,255,255,0.25);"
            f" border-radius: 8px; }}"
            f"QPushButton:hover {{ border: 2px solid rgba(255,255,255,0.6); }}"
        )
        self.setToolTip(self._hex)

    def _pick(self):
        color = QColorDialog.getColor(QColor(self._hex), self, "Pick a color")
        if color.isValid():
            self.set_hex(color.name())
            self.colorChanged.emit()


def _map_matches_replay(replay, game_map) -> bool:
    if replay.map_legacy_id and game_map.metadata.legacy_id and replay.map_legacy_id == game_map.metadata.legacy_id:
        return True
    if replay.map_online_id and game_map.metadata.online_id and replay.map_online_id == game_map.metadata.online_id:
        return True
    # Exported .rhm files carry per-export ids that rarely equal the ones in
    # the replay even for the same map; matching note counts is a strong
    # signal it's the right one, so don't nag the user in that case.
    return len(game_map.notes) == replay.hits + replay.misses


def _format_duration(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Worker thread for rendering
# ---------------------------------------------------------------------------

class RenderWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, replay, game_map, skin, output_path: str, width: int, height: int, fps: int,
                 quality: str, spawn_distance: float, approach_rate: float, parallax_enabled: bool,
                 trail_enabled: bool, note_colors: list[tuple[int, int, int]] | None,
                 video_codec: str, hw_accel: str, audio_bitrate: str,
                 color_overrides: dict | None, background_dots_enabled: bool,
                 trail_scale: float, motion_blur: float, motion_blur_mode: str,
                 clip_start_ms: float | None = None, clip_end_ms: float | None = None,
                 intro_enabled: bool = False, hit_effects_enabled: bool = True,
                 hud_overrides: dict | None = None,
                 music_volume: float = 100.0, hit_sound_volume: float = 100.0):
        super().__init__()
        self.replay = replay
        self.game_map = game_map
        self.skin = skin
        self.output_path = output_path
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality
        self.spawn_distance = spawn_distance
        self.approach_rate = approach_rate
        self.parallax_enabled = parallax_enabled
        self.trail_enabled = trail_enabled
        self.note_colors = note_colors
        self.video_codec = video_codec
        self.hw_accel = hw_accel
        self.audio_bitrate = audio_bitrate
        self.color_overrides = color_overrides
        self.background_dots_enabled = background_dots_enabled
        self.trail_scale = trail_scale
        self.motion_blur = motion_blur
        self.motion_blur_mode = motion_blur_mode
        self.clip_start_ms = clip_start_ms
        self.clip_end_ms = clip_end_ms
        self.intro_enabled = intro_enabled
        self.hit_effects_enabled = hit_effects_enabled
        self.hud_overrides = hud_overrides
        self.music_volume = music_volume
        self.hit_sound_volume = hit_sound_volume
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            render_video(
                self.game_map,
                self.replay,
                self.output_path,
                width=self.width,
                height=self.height,
                fps=self.fps,
                quality=self.quality,
                spawn_distance=self.spawn_distance,
                approach_rate=self.approach_rate,
                skin=self.skin,
                parallax_enabled=self.parallax_enabled,
                trail_enabled=self.trail_enabled,
                note_colors=self.note_colors,
                video_codec=self.video_codec,
                hw_accel=self.hw_accel,
                audio_bitrate=self.audio_bitrate,
                color_overrides=self.color_overrides,
                background_dots_enabled=self.background_dots_enabled,
                trail_scale=self.trail_scale,
                motion_blur=self.motion_blur,
                motion_blur_mode=self.motion_blur_mode,
                clip_start_ms=self.clip_start_ms,
                clip_end_ms=self.clip_end_ms,
                intro_enabled=self.intro_enabled,
                hit_effects_enabled=self.hit_effects_enabled,
                music_volume=self.music_volume,
                hit_sound_volume=self.hit_sound_volume,
                hud_overrides=self.hud_overrides,
                progress_cb=lambda done, total: self.progress.emit(done, total),
                cancel_cb=lambda: self._cancelled,
            )

            if self._cancelled:
                self.failed.emit("Rendering canceled.")
            else:
                self.finished_ok.emit(self.output_path)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Animated preview worker
# ---------------------------------------------------------------------------

ANIM_FPS = 24
ANIM_DURATION_S = 2.0


class PreviewAnimWorker(QThread):
    """Renders a short looping preview (~2s at 24fps, small resolution) in
    the background so the static preview stays instant. Also mixes the
    matching 2s of audio (music + hit sounds, at the configured volumes)
    so the loop plays with sound."""

    done = pyqtSignal(list, object)  # list[QImage], WAV bytes | None

    def __init__(self, replay, game_map, skin_obj, note_colors, opts: dict):
        super().__init__()
        self.replay = replay
        self.game_map = game_map
        self.skin_obj = skin_obj
        self.note_colors = note_colors
        self.opts = opts
        self._cancelled = False
        self._results = None

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            frames = self._render()
        except Exception:
            frames = []
        audio = None
        if frames and not self._cancelled and not self.opts.get("muted", False):
            try:
                audio = self._render_audio()
            except Exception:
                audio = None  # preview stays silent rather than failing
        if not self._cancelled:
            self.done.emit(frames, audio)

    def _render_audio(self) -> bytes | None:
        from ..render.audio import default_hit_sound, extract_music_snippet, mix_audio
        from ..render.video import _detect_audio_ext

        o = self.opts
        speed = self.replay.speed if self.replay.speed and self.replay.speed > 0 else 1.0
        window_ms = ANIM_DURATION_S * 1000.0
        t0 = o["t0"]

        snippet = extract_music_snippet(
            self.game_map.audio_bytes, _detect_audio_ext(self.game_map.audio_bytes),
            t0, window_ms, tempo=speed,
        )

        skin = self.skin_obj
        hit_bytes = skin.hit_sound_bytes if (skin is not None and skin.hit_sound_bytes) else default_hit_sound()
        skin_hit_volume = skin.hit_sound_volume if skin is not None else 100.0
        effective_hit_volume = skin_hit_volume * o["hit_vol"] / 100.0

        overlays = []
        if hit_bytes and effective_hit_volume > 0 and self._results is not None:
            end = t0 + window_ms * speed  # window in song time
            times = [
                (r.hit_ms - t0) / speed  # wall-time offset inside the snippet
                for r in self._results
                if r.hit and r.hit_ms is not None and t0 <= r.hit_ms < end
            ]
            if times:
                overlays.append((hit_bytes, times, effective_hit_volume))

        if not overlays and o["music_vol"] == 100.0:
            return snippet
        return mix_audio(snippet, "wav", o["music_vol"], overlays)

    def _render(self) -> list[QImage]:
        from ..render.frame import build_context, draw_frame_blurred, draw_frame_tmix
        from ..render.video import _resolve_runtime
        from ..sim.hitreg import match_hits
        from ..sim.mods import resolve_mods
        from ..sim.timeline import Timeline

        o = self.opts
        runtime = _resolve_runtime(self.skin_obj, o["parallax"], o["trail"], o["dots"],
                                   o["trail_scale"], o["hit_fx"], self.note_colors,
                                   o["color_overrides"], o["hud"])
        notes, ghost, chaos, extent, mods_label = resolve_mods(self.game_map.notes, self.replay)
        results = match_hits(notes, self.replay.frames)
        self._results = results  # reused by _render_audio for hit timestamps
        timeline = Timeline(notes, results, self.replay,
                            spawn_distance=o["spawn"], approach_rate=o["approach"],
                            ghost=ghost, chaos=chaos)
        ctx = build_context(o["w"], o["h"], self.game_map.cover_bytes, runtime,
                            playfield_extent=extent)
        ctx.mods_label = mods_label

        speed = self.replay.speed if self.replay.speed and self.replay.speed > 0 else 1.0
        anim_dt = 1000.0 / ANIM_FPS * speed
        n = int(ANIM_DURATION_S * ANIM_FPS)
        frames: list[QImage] = []
        for i in range(n):
            if self._cancelled:
                return []
            t = min(self.replay.length_ms, o["t0"] + i * anim_dt)
            if o["blur_mode"] == "subframe":
                img = draw_frame_blurred(ctx, timeline, self.game_map.metadata, self.replay,
                                         t, o["frame_dt"], o["blur"])
            else:
                img = draw_frame_tmix(ctx, timeline, self.game_map.metadata, self.replay,
                                      t, o["frame_dt"], o["blur"])
            frames.append(QImage(img.tobytes(), img.width, img.height,
                                 img.width * 3, QImage.Format_RGB888).copy())
        return frames


# ---------------------------------------------------------------------------
# Drag-and-drop file button
# ---------------------------------------------------------------------------

class DropField(QPushButton):
    fileDropped = pyqtSignal(str)

    def __init__(self, label: str, extension: str):
        super().__init__(label)
        self.extension = extension
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(48)
        self.path = ""
        self._empty_label = label
        self._set_state("empty")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_state("drag")

    def dragLeaveEvent(self, event):
        self._set_state("has_file" if self.path else "empty")
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self.extension):
                self.set_path(path)
                break
        else:
            self._set_state("has_file" if self.path else "empty")

    def set_path(self, path: str):
        self.path = path
        self.setText(os.path.basename(path))
        self.setToolTip(path)
        self._set_state("has_file")
        self.fileDropped.emit(path)

    def clear_path(self):
        self.path = ""
        self.setText(self._empty_label)
        self.setToolTip("")
        self._set_state("empty")

    def _set_state(self, state: str):
        self.setProperty("dropState", state)
        self.style().unpolish(self)
        self.style().polish(self)


# ---------------------------------------------------------------------------
# Collapsible card
# ---------------------------------------------------------------------------

class CollapsibleCard(QFrame):
    """A card with a clickable header that toggles the body visibility."""

    def __init__(self, title: str, expanded: bool = True, extra_header_widget: QWidget | None = None):
        super().__init__()
        self.setObjectName("cardPanel")
        self._expanded = expanded

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(22, 14, 22, 0)
        root_layout.setSpacing(0)

        # ── Header row ──
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._arrow = QPushButton("▾" if expanded else "▸")
        self._arrow.setObjectName("collapseToggle")
        self._arrow.setFixedSize(22, 22)
        self._arrow.setCursor(Qt.PointingHandCursor)
        self._arrow.clicked.connect(self.toggle)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("sectionTitle")
        self._title_label.setCursor(Qt.PointingHandCursor)
        self._title_label.mousePressEvent = lambda _: self.toggle()

        header.addWidget(self._arrow)
        header.addWidget(self._title_label)
        header.addStretch(1)

        if extra_header_widget is not None:
            header.addWidget(extra_header_widget)

        root_layout.addLayout(header)

        # ── Body container ──
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 10, 0, 18)
        self._body_layout.setSpacing(0)
        self._body.setVisible(expanded)

        root_layout.addWidget(self._body)

        # Adjust bottom margin when collapsed
        if not expanded:
            root_layout.setContentsMargins(22, 14, 22, 14)

    def set_body_widget(self, widget: QWidget):
        """Set the collapsible content widget."""
        self._body_layout.addWidget(widget)

    def toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")
        # Adjust bottom padding
        m = self.layout().contentsMargins()
        self.layout().setContentsMargins(m.left(), m.top(), m.right(), 0 if self._expanded else 14)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker: RenderWorker | None = None
        self._settings = QSettings("rhr2mp4", "rhr2mp4")
        self._colorset_note = ""
        self.rhr_path = ""
        self.rhm_path = ""
        self._rhm_auto = False  # current map came from auto-detection
        self._output_auto = False
        self._render_start_time: float | None = None
        self._last_done = 0
        self._last_total = 0
        self._eta_timer = QTimer(self)
        self._eta_timer.setInterval(1000)
        self._eta_timer.timeout.connect(self._update_eta_display)
        # Animated preview playback
        self._anim_worker: PreviewAnimWorker | None = None
        self._preview_sound = None  # QSoundEffect | None
        self._preview_audio_path = ""
        self._anim_frames: list[QImage] = []
        self._anim_index = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(1000 // ANIM_FPS)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._tray = None
        self._last_output_path = ""
        # Auto-load the preview shortly after both files are in (debounced:
        # loading a replay often sets the map right after via auto-detect).
        self._auto_preview_timer = QTimer(self)
        self._auto_preview_timer.setSingleShot(True)
        self._auto_preview_timer.setInterval(150)
        self._auto_preview_timer.timeout.connect(self._auto_preview)
        # Batch queue: replays waiting to render after the current one.
        self.render_queue: list[dict] = []
        self._batch_done = 0
        self.init_ui()

    # ── UI construction ──────────────────────────────────────────────

    def init_ui(self):
        self.setWindowTitle("rhr2mp4 — Replay Converter")
        self.setMinimumSize(1040, 640)
        self.setObjectName("root")
        self.setStyleSheet(build_stylesheet())
        # Files dropped anywhere on the window are routed by extension, so
        # the user doesn't have to aim at the individual fields.
        self.setAcceptDrops(True)

        # Outer layout with scroll support
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # ── Header ──
        header_card = self._make_header()

        # ── Files card ──
        files_card = self._make_files_card()

        # ── Batch queue card (hidden until something is queued) ──
        queue_card = self._make_queue_card()

        # ── Preview panel ──
        preview_panel = self._make_preview_card()

        # ── Options dialog (render settings, output, extras, colors) ──
        self._options_dialog = self._make_options_dialog()

        # ── Assemble: big auto-loading preview on the left, file
        #    selection and queue on the right ──
        layout.addWidget(header_card)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(preview_panel, 3)

        right_col = QVBoxLayout()
        right_col.setSpacing(16)
        right_col.addWidget(files_card)
        right_col.addWidget(queue_card)
        right_col.addStretch(1)
        columns.addLayout(right_col, 2)

        layout.addLayout(columns, 1)

        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        # ── Sticky footer: render button + progress, always visible ──
        outer.addWidget(self._make_footer())

        self._restore_settings()

    # ── Settings persistence ───────────────────────────────────────────

    def _restore_settings(self):
        s = self._settings
        # Resolution combo
        res = s.value("resolution", "", type=str)
        if res and self.resolution_combo.findText(res) >= 0:
            self.resolution_combo.setCurrentText(res)
        # Quality combo
        qual = s.value("quality", "", type=str)
        if qual and self.quality_combo.findText(qual) >= 0:
            self.quality_combo.setCurrentText(qual)
        # Spawn distance
        sd = s.value("spawn_distance", None)
        if sd is not None:
            self.spawn_distance_spin.setValue(float(sd))
        # Approach rate
        ar = s.value("approach_rate", None)
        if ar is not None:
            self.approach_rate_spin.setValue(float(ar))
        # Parallax
        par = s.value("parallax", None)
        if par is not None:
            self.parallax_check.setChecked(str(par).lower() == "true")
        # Cursor trail
        trail = s.value("cursor_trail", None)
        if trail is not None:
            self.trail_check.setChecked(str(trail).lower() == "true")
        # Background dots
        dots = s.value("background_dots", None)
        if dots is not None:
            self.bg_dots_check.setChecked(str(dots).lower() == "true")
        # Hit effects
        fx = s.value("hit_effects", None)
        if fx is not None:
            self.hit_fx_check.setChecked(str(fx).lower() == "true")
        # Intro screen
        intro = s.value("intro_enabled", None)
        if intro is not None:
            self.intro_check.setChecked(str(intro).lower() == "true")
        # HUD element visibility
        import json
        try:
            hud = json.loads(s.value("hud_flags", "{}", type=str))
        except Exception:
            hud = {}
        for flag, cb in self.hud_checks.items():
            if flag in hud:
                cb.setChecked(bool(hud[flag]))
        # Trail length
        tl = s.value("trail_length", None)
        if tl is not None:
            self.trail_length_spin.setValue(float(tl))
        # Motion blur (migrate from the old checkbox-era boolean key)
        mode = s.value("motion_blur_mode", "", type=str)
        if not mode and str(s.value("motion_blur_enabled", "")).lower() == "true":
            mode = "Fast (ffmpeg filter)"
        if mode and self.motion_blur_combo.findText(mode) >= 0:
            self.motion_blur_combo.setCurrentText(mode)
        self.motion_blur_spin.setEnabled(
            MOTION_BLUR_MODES.get(self.motion_blur_combo.currentText()) != "off"
        )
        mbi = s.value("motion_blur_intensity", None)
        if mbi is not None:
            self.motion_blur_spin.setValue(float(mbi))
        # Audio volumes
        mv = s.value("music_volume", None)
        if mv is not None:
            self.music_volume_spin.setValue(float(mv))
        hv = s.value("hitsound_volume", None)
        if hv is not None:
            self.hit_volume_spin.setValue(float(hv))
        # Preview mute (defaults to muted, set at widget creation)
        pm = s.value("preview_muted", None)
        if pm is not None:
            self.preview_mute_btn.setChecked(str(pm).lower() == "true")
        # Encoding options
        for key, combo in (("video_codec", self.codec_combo), ("hw_accel", self.hw_combo), ("audio_bitrate", self.audio_combo)):
            val = s.value(key, "", type=str)
            if val and combo.findText(val) >= 0:
                combo.setCurrentText(val)

    def _save_settings(self):
        s = self._settings
        s.setValue("resolution", self.resolution_combo.currentText())
        s.setValue("quality", self.quality_combo.currentText())
        s.setValue("spawn_distance", self.spawn_distance_spin.value())
        s.setValue("approach_rate", self.approach_rate_spin.value())
        s.setValue("parallax", self.parallax_check.isChecked())
        s.setValue("cursor_trail", self.trail_check.isChecked())
        s.setValue("background_dots", self.bg_dots_check.isChecked())
        s.setValue("hit_effects", self.hit_fx_check.isChecked())
        s.setValue("intro_enabled", self.intro_check.isChecked())
        import json
        s.setValue("hud_flags", json.dumps(self._hud_overrides()))
        s.setValue("trail_length", self.trail_length_spin.value())
        s.setValue("motion_blur_mode", self.motion_blur_combo.currentText())
        s.setValue("motion_blur_intensity", self.motion_blur_spin.value())
        s.setValue("video_codec", self.codec_combo.currentText())
        s.setValue("hw_accel", self.hw_combo.currentText())
        s.setValue("audio_bitrate", self.audio_combo.currentText())
        s.setValue("music_volume", self.music_volume_spin.value())
        s.setValue("hitsound_volume", self.hit_volume_spin.value())
        s.setValue("preview_muted", self.preview_mute_btn.isChecked())
        self._save_color_settings()

    def closeEvent(self, event):
        self._stop_preview_animation()
        self._save_settings()
        super().closeEvent(event)

    # ── Header ────────────────────────────────────────────────────────

    def _make_header(self) -> QFrame:
        card = QFrame()
        card.setObjectName("headerCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        # Logo (small)
        logo_label = QLabel()
        logo_label.setFixedSize(42, 42)
        logo_path = self._asset_path("rhythialogo.png")
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            logo_label.setPixmap(
                pixmap.scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

        # Title block
        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(2)
        title = QLabel("rhr2mp4")
        title.setObjectName("headerTitle")
        subtitle = QLabel("Rhythia Replay → MP4 Converter")
        subtitle.setObjectName("headerSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        layout.addWidget(logo_label)
        layout.addLayout(title_block)
        layout.addStretch(1)

        options_btn = QPushButton("⚙  Options")
        options_btn.setObjectName("secondaryButton")
        options_btn.setCursor(Qt.PointingHandCursor)
        options_btn.setToolTip("Render settings, output path, skin, colors… (Ctrl+O)")
        options_btn.setShortcut("Ctrl+O")
        options_btn.clicked.connect(self._open_options)
        layout.addWidget(options_btn)

        return card

    # ── Files card ────────────────────────────────────────────────────

    def _make_files_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("cardPanel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        section = QLabel("FILES")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)

        # Replay
        self.rhr_field = DropField("Drop or browse replay (.rhr)", ".rhr")
        self.rhr_field.clicked.connect(self.pick_rhr)
        self.rhr_field.fileDropped.connect(lambda path: self._set_rhr(path))
        self.rhr_clear = QPushButton("Clear")
        self.rhr_clear.clicked.connect(self.clear_rhr)
        layout.addWidget(self._file_row("Replay file", self.rhr_field, self.rhr_clear))

        # Quick facts about the loaded replay (player, duration, speed, date)
        self.rhr_info = QLabel("")
        self.rhr_info.setObjectName("metaLabel")
        self.rhr_info.setVisible(False)
        layout.addWidget(self.rhr_info)

        # Map
        self.rhm_field = DropField("Drop or browse map (.rhm)", ".rhm")
        self.rhm_field.clicked.connect(self.pick_rhm)
        self.rhm_field.fileDropped.connect(lambda path: self._set_rhm(path))
        self.rhm_clear = QPushButton("Clear")
        self.rhm_clear.clicked.connect(self.clear_rhm)
        layout.addWidget(self._file_row("Map file", self.rhm_field, self.rhm_clear))

        # Quick facts about the loaded map (cover, song, mappers, difficulty)
        self.rhm_info_box = QWidget()
        info_row = QHBoxLayout(self.rhm_info_box)
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(12)
        self.rhm_cover = QLabel()
        self.rhm_cover.setFixedSize(48, 48)
        self.rhm_cover.setScaledContents(False)
        info_text = QVBoxLayout()
        info_text.setContentsMargins(0, 0, 0, 0)
        info_text.setSpacing(2)
        self.rhm_title = QLabel("")
        self.rhm_title.setObjectName("mapInfoTitle")
        self.rhm_meta = QLabel("")
        self.rhm_meta.setObjectName("metaLabel")
        info_text.addWidget(self.rhm_title)
        info_text.addWidget(self.rhm_meta)
        info_row.addWidget(self.rhm_cover)
        info_row.addLayout(info_text, 1)
        self.rhm_info_box.setVisible(False)
        layout.addWidget(self.rhm_info_box)

        return card

    # ── Batch queue ───────────────────────────────────────────────────

    def _make_queue_card(self) -> QFrame:
        self._queue_card = QFrame()
        self._queue_card.setObjectName("cardPanel")
        layout = QVBoxLayout(self._queue_card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        section = QLabel("QUEUE")
        section.setObjectName("sectionTitle")
        header.addWidget(section)
        header.addStretch(1)

        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("secondaryButton")
        remove_btn.clicked.connect(self._remove_queue_item)
        header.addWidget(remove_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("secondaryButton")
        clear_btn.clicked.connect(self._clear_queue)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(150)
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.queue_list.setTextElideMode(Qt.ElideMiddle)
        self.queue_list.setStyleSheet(
            "QListWidget { background: rgba(10, 17, 40, 0.9); color: #cfdaff;"
            " border: 1px solid rgba(100, 120, 255, 0.14); border-radius: 10px;"
            " padding: 4px; }"
            "QListWidget::item { padding: 5px 8px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #253570; color: #ffffff; }"
        )
        layout.addWidget(self.queue_list)

        self._queue_card.setVisible(False)
        return self._queue_card

    def _enqueue_replay(self, path: str):
        """Adds a replay to the batch queue, auto-detecting its map and
        output name (like the single-file flow does)."""
        try:
            replay = rhr.load(path)
        except Exception as e:
            self.status_label.setText(f"⚠ Could not queue {os.path.basename(path)}: {e}")
            return
        rhm_path = self._autofind_map(replay, rhr_path=path)
        map_name = None
        if rhm_path:
            try:
                map_name = rhm.load(rhm_path).metadata.song_name or None
            except Exception:
                rhm_path = None
        output = os.path.join(
            self._preferred_output_dir(path),
            locate.default_output_name(map_name, replay.username, path),
        )
        self.render_queue.append({"rhr": path, "rhm": rhm_path, "output": output})
        self._refresh_queue_ui()

    def _refresh_queue_ui(self):
        self.queue_list.clear()
        for item in self.render_queue:
            rhm_name = os.path.basename(item["rhm"]) if item["rhm"] else "⚠ map not found (will be skipped)"
            self.queue_list.addItem(f"{os.path.basename(item['rhr'])}   →   {rhm_name}")
            entry = self.queue_list.item(self.queue_list.count() - 1)
            entry.setToolTip(f"{item['rhr']}\n→ {item['rhm'] or 'no map'}\n→ {item['output']}")
        self._queue_card.setVisible(bool(self.render_queue))

    def _remove_queue_item(self):
        row = self.queue_list.currentRow()
        if 0 <= row < len(self.render_queue):
            self.render_queue.pop(row)
            self._refresh_queue_ui()

    def _clear_queue(self):
        self.render_queue.clear()
        self._refresh_queue_ui()

    def _load_next_queue_item(self) -> bool:
        """Moves the next renderable queue item into the file fields.
        Returns False when the queue ran out (mapless items are skipped)."""
        while self.render_queue:
            item = self.render_queue.pop(0)
            self._refresh_queue_ui()
            if not item["rhm"]:
                self.status_label.setText(
                    f"Skipped {os.path.basename(item['rhr'])} — no matching map found."
                )
                continue
            self.rhr_field.set_path(item["rhr"])
            self.rhm_field.set_path(item["rhm"])
            self._rhm_auto = True  # queue maps come from auto-detection
            self.output_edit.setText(item["output"])
            self._output_auto = True
            return True
        return False

    # ── Options dialog ────────────────────────────────────────────────

    def _make_options_dialog(self) -> QDialog:
        dlg = QDialog(self)
        dlg.setWindowTitle("Options — rhr2mp4")
        dlg.setObjectName("root")
        dlg.setStyleSheet(build_stylesheet())
        dlg.setMinimumSize(760, 620)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(self._make_output_card())
        layout.addWidget(self._make_settings_card())
        layout.addWidget(self._make_hud_card())
        layout.addWidget(self._make_extras_card())
        layout.addWidget(self._make_colors_card())
        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Footer with a close button
        footer = QFrame()
        footer.setObjectName("footerBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 12)
        footer_layout.addStretch(1)
        close_btn = QPushButton("Done")
        close_btn.setObjectName("secondaryButton")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setMinimumWidth(120)
        close_btn.clicked.connect(dlg.accept)
        footer_layout.addWidget(close_btn)
        outer.addWidget(footer)

        return dlg

    def _open_options(self):
        self._options_dialog.exec_()
        self._save_settings()

    def _make_output_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("cardPanel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        section = QLabel("OUTPUT")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output video path (.mp4) — auto-filled from the replay")
        self.output_edit.setReadOnly(False)
        output_browse = QPushButton("Browse")
        output_browse.clicked.connect(self.pick_output)
        layout.addWidget(self._line_row("Save video as", self.output_edit, output_browse))

        return card

    # ── Settings card (collapsible) ─────────────────────────────────

    def _make_settings_card(self) -> CollapsibleCard:
        self.parallax_check = QCheckBox("Camera parallax")
        self.parallax_check.setChecked(True)

        self.trail_check = QCheckBox("Cursor trail")
        self.trail_check.setChecked(True)
        self.trail_check.setToolTip("Uncheck to render the cursor without the comet trail behind it.")

        self.bg_dots_check = QCheckBox("Background dots")
        self.bg_dots_check.setChecked(True)
        self.bg_dots_check.setToolTip("The white dots drifting behind the playfield. Uncheck for a clean background.")

        self.hit_fx_check = QCheckBox("Hit effects")
        self.hit_fx_check.setChecked(True)
        self.hit_fx_check.setToolTip("Particle burst when a note is hit (expanding outline + flying fragments).")

        toggles = QWidget()
        toggles_layout = QHBoxLayout(toggles)
        toggles_layout.setContentsMargins(0, 0, 0, 0)
        toggles_layout.setSpacing(14)
        toggles_layout.addWidget(self.trail_check)
        toggles_layout.addWidget(self.parallax_check)
        toggles_layout.addWidget(self.bg_dots_check)
        toggles_layout.addWidget(self.hit_fx_check)

        card = CollapsibleCard("RENDER SETTINGS", expanded=True,
                               extra_header_widget=toggles)

        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)

        # Row 0
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTIONS.keys())
        self._style_combobox(self.resolution_combo)
        grid.addWidget(self._wrap_labeled("Resolution & FPS", self.resolution_combo), 0, 0)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITIES.keys())
        self._style_combobox(self.quality_combo)
        grid.addWidget(self._wrap_labeled("Render quality", self.quality_combo), 0, 1)

        # Row 1
        self.spawn_distance_spin = QDoubleSpinBox()
        self.spawn_distance_spin.setRange(0.1, 100.0)
        self.spawn_distance_spin.setSingleStep(0.1)
        self.spawn_distance_spin.setValue(DEFAULT_SPAWN_DISTANCE)
        grid.addWidget(self._wrap_labeled("Spawn distance", self.spawn_distance_spin), 1, 0)

        self.approach_rate_spin = QDoubleSpinBox()
        self.approach_rate_spin.setRange(0.1, 100.0)
        self.approach_rate_spin.setSingleStep(0.1)
        self.approach_rate_spin.setValue(DEFAULT_APPROACH_RATE)
        grid.addWidget(self._wrap_labeled("Approach rate", self.approach_rate_spin), 1, 1)

        # Row 2: export/encoding options
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(CODECS.keys())
        self._style_combobox(self.codec_combo)
        grid.addWidget(self._wrap_labeled("Video codec", self.codec_combo), 2, 0)

        self.hw_combo = QComboBox()
        self.hw_combo.addItems(HW_ACCELS.keys())
        self.hw_combo.setToolTip(
            "Hardware encoder offloads video compression to the GPU, freeing\n"
            "CPU cores for frame drawing. Auto probes NVENC, VAAPI and QSV\n"
            "and silently falls back to CPU when unavailable."
        )
        self._style_combobox(self.hw_combo)
        grid.addWidget(self._wrap_labeled("Hardware acceleration", self.hw_combo), 2, 1)

        self.audio_combo = QComboBox()
        self.audio_combo.addItems(AUDIO_BITRATES.keys())
        self.audio_combo.setCurrentText("192 kbps")
        self._style_combobox(self.audio_combo)
        grid.addWidget(self._wrap_labeled("Audio bitrate", self.audio_combo), 3, 0)

        # Row 3-4: visual tweaks
        self.trail_length_spin = QDoubleSpinBox()
        self.trail_length_spin.setRange(10.0, 300.0)
        self.trail_length_spin.setSingleStep(10.0)
        self.trail_length_spin.setDecimals(0)
        self.trail_length_spin.setSuffix(" %")
        self.trail_length_spin.setValue(100.0)
        self.trail_length_spin.setToolTip("Length of the cursor trail (100% = default; 50% = half as long).")
        grid.addWidget(self._wrap_labeled("Trail length", self.trail_length_spin), 3, 1)

        self.motion_blur_combo = QComboBox()
        self.motion_blur_combo.addItems(MOTION_BLUR_MODES.keys())
        self.motion_blur_combo.setToolTip(
            "Fast: blur applied by ffmpeg while encoding (tmix filter) — no impact\n"
            "on render speed; blends whole previous frames (persistence look).\n"
            "High quality: physically samples 4 sub-frames inside each frame\n"
            "interval — smoother blur, but ≈4× slower to render."
        )
        self._style_combobox(self.motion_blur_combo)
        self.motion_blur_spin = QDoubleSpinBox()
        self.motion_blur_spin.setRange(10.0, 100.0)
        self.motion_blur_spin.setSingleStep(10.0)
        self.motion_blur_spin.setDecimals(0)
        self.motion_blur_spin.setSuffix(" %")
        self.motion_blur_spin.setValue(50.0)
        self.motion_blur_spin.setEnabled(False)
        self.motion_blur_spin.setToolTip("Blur intensity (100% = max smear; 50% ≈ cinematic).")
        self.motion_blur_combo.currentTextChanged.connect(
            lambda text: self.motion_blur_spin.setEnabled(MOTION_BLUR_MODES.get(text) != "off")
        )
        blur_row = QHBoxLayout()
        blur_row.setSpacing(10)
        blur_row.addWidget(self.motion_blur_combo, 1)
        blur_row.addWidget(self.motion_blur_spin)
        grid.addWidget(self._wrap_labeled("Motion blur", self._hbox_widget(blur_row)), 4, 0)

        self.intro_check = QCheckBox("Intro screen (2.5s cover + stats before gameplay)")
        grid.addWidget(self._wrap_labeled("Intro", self.intro_check), 4, 1)

        # Row 5: audio volumes (applied to the rendered video and the preview)
        self.music_volume_spin = QDoubleSpinBox()
        self.music_volume_spin.setRange(0.0, 200.0)
        self.music_volume_spin.setSingleStep(10.0)
        self.music_volume_spin.setDecimals(0)
        self.music_volume_spin.setSuffix(" %")
        self.music_volume_spin.setValue(100.0)
        self.music_volume_spin.setToolTip("Music volume in the video and preview (0% mutes the track).")
        grid.addWidget(self._wrap_labeled("Music volume", self.music_volume_spin), 5, 0)

        self.hit_volume_spin = QDoubleSpinBox()
        self.hit_volume_spin.setRange(0.0, 200.0)
        self.hit_volume_spin.setSingleStep(10.0)
        self.hit_volume_spin.setDecimals(0)
        self.hit_volume_spin.setSuffix(" %")
        self.hit_volume_spin.setValue(100.0)
        self.hit_volume_spin.setToolTip(
            "Hit sound volume in the video and preview (0% disables it).\n"
            "Skins without their own hit sound use the app's default one."
        )
        grid.addWidget(self._wrap_labeled("Hitsound volume", self.hit_volume_spin), 5, 1)

        # Re-run the animated preview so volume changes are heard right away.
        self.music_volume_spin.valueChanged.connect(self._schedule_auto_preview)
        self.hit_volume_spin.valueChanged.connect(self._schedule_auto_preview)

        card.set_body_widget(body)
        return card

    # ── HUD visibility card (collapsible, starts collapsed) ───────────

    def _make_hud_card(self) -> CollapsibleCard:
        card = CollapsibleCard("HUD ELEMENTS", expanded=False)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        hint = QLabel("Checked elements are always drawn (even if the skin hides them); "
                      "uncheck to hide them from the video.")
        hint.setObjectName("fieldLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.hud_checks: dict[str, QCheckBox] = {}
        for i, (label, flag) in enumerate(HUD_ELEMENTS):
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.hud_checks[flag] = cb
            grid.addWidget(cb, i // 3, i % 3)
        v.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(8)
        all_btn = QPushButton("Show all")
        all_btn.setObjectName("secondaryButton")
        all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in self.hud_checks.values()])
        none_btn = QPushButton("Hide all")
        none_btn.setObjectName("secondaryButton")
        none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in self.hud_checks.values()])
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch(1)
        v.addLayout(row)

        card.set_body_widget(body)
        return card

    def _hud_overrides(self) -> dict:
        return {flag: cb.isChecked() for flag, cb in self.hud_checks.items()}

    # ── Optional resources card (collapsible, starts collapsed) ───────

    def _make_extras_card(self) -> CollapsibleCard:
        card = CollapsibleCard("OPTIONAL RESOURCES", expanded=False)

        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)

        # Skin
        self.skin_field = DropField("Optional .rhs skin", ".rhs")
        self.skin_field.clicked.connect(self.pick_skin)
        self.skin_field.fileDropped.connect(self._apply_skin_gameplay_settings)
        skin_clear = QPushButton("Clear")
        skin_clear.clicked.connect(self.clear_skin)
        grid.addWidget(self._file_row("Skin file", self.skin_field, skin_clear), 0, 0)

        # Colorset
        self.colorset_field = DropField("Optional .txt colorset", ".txt")
        self.colorset_field.clicked.connect(self.pick_colorset)
        colorset_clear = QPushButton("Clear")
        colorset_clear.clicked.connect(self.clear_colorset)
        grid.addWidget(self._file_row("Colorset", self.colorset_field, colorset_clear), 0, 1)

        # Game folder
        self.gamedir_edit = QLineEdit()
        self.gamedir_edit.setPlaceholderText("Game install folder")
        self.gamedir_edit.setText(self._settings.value("game_dir", "", type=str))
        self.gamedir_edit.setCursorPosition(0)
        gamedir_browse = QPushButton("Browse")
        gamedir_browse.clicked.connect(self.pick_gamedir)
        grid.addWidget(self._line_row("Rhythia folder", self.gamedir_edit, gamedir_browse), 0, 2)

        card.set_body_widget(body)
        return card

    # ── Colors / skin presets card ────────────────────────────────────

    def _make_colors_card(self):
        card = CollapsibleCard("COLORS", expanded=False)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # Preset row
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self.color_preset_combo = QComboBox()
        self._reload_preset_combo()
        self._style_combobox(self.color_preset_combo)
        self.color_preset_combo.currentTextChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self.color_preset_combo, 1)

        save_btn = QPushButton("Save preset…")
        save_btn.setObjectName("secondaryButton")
        save_btn.clicked.connect(self._save_color_preset)
        preset_row.addWidget(save_btn)

        self.delete_preset_btn = QPushButton("Delete")
        self.delete_preset_btn.setObjectName("secondaryButton")
        self.delete_preset_btn.clicked.connect(self._delete_color_preset)
        preset_row.addWidget(self.delete_preset_btn)
        v.addWidget(self._wrap_labeled("Preset", self._hbox_widget(preset_row)))

        # Note colors (cycled per note, like the game's colorsets)
        self._editors_box = QWidget()
        editors = QVBoxLayout(self._editors_box)
        editors.setContentsMargins(0, 0, 0, 0)
        editors.setSpacing(12)

        notes_row = QHBoxLayout()
        notes_row.setSpacing(6)
        self.note_swatch_row = QHBoxLayout()
        self.note_swatch_row.setSpacing(6)
        self.note_swatches: list[ColorSwatch] = []
        notes_row.addLayout(self.note_swatch_row)
        add_btn = QPushButton("+")
        add_btn.setObjectName("secondaryButton")
        add_btn.setFixedSize(30, 30)
        add_btn.setToolTip("Add another note color (notes cycle through them in order)")
        add_btn.clicked.connect(lambda: self._add_note_swatch())
        remove_btn = QPushButton("−")
        remove_btn.setObjectName("secondaryButton")
        remove_btn.setFixedSize(30, 30)
        remove_btn.clicked.connect(self._remove_note_swatch)
        notes_row.addWidget(add_btn)
        notes_row.addWidget(remove_btn)
        notes_row.addStretch(1)
        editors.addWidget(self._wrap_labeled("Note colors (cycle)", self._hbox_widget(notes_row)))

        # Cursor / trail / border single colors
        singles_row = QHBoxLayout()
        singles_row.setSpacing(18)
        self.cursor_swatch = ColorSwatch("#ffffff")
        self.trail_swatch = ColorSwatch("#ffffff")
        self.border_swatch = ColorSwatch("#ffffff")
        for label, swatch in (("Cursor", self.cursor_swatch), ("Trail", self.trail_swatch), ("Border", self.border_swatch)):
            singles_row.addWidget(self._wrap_labeled(label, swatch))
        singles_row.addStretch(1)
        editors.addLayout(singles_row)

        v.addWidget(self._editors_box)
        card.set_body_widget(body)

        self._load_color_settings()
        return card

    @staticmethod
    def _hbox_widget(layout: QHBoxLayout) -> QWidget:
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _user_color_presets(self) -> dict:
        import json
        try:
            return json.loads(self._settings.value("user_color_presets", "{}", type=str))
        except Exception:
            return {}

    def _reload_preset_combo(self):
        current = self.color_preset_combo.currentText() if self.color_preset_combo.count() else ""
        # Colorsets from the Rhythia install (and the ones bundled with the
        # app) show up automatically — no game-folder setup needed.
        game_dir = self.gamedir_edit.text().strip() if hasattr(self, "gamedir_edit") else ""
        try:
            self._game_colorsets = colorsets.discover_game_colorsets(game_dir)
        except Exception:
            self._game_colorsets = {}
        self.color_preset_combo.blockSignals(True)
        self.color_preset_combo.clear()
        self.color_preset_combo.addItem(FROM_SKIN_PRESET)
        self.color_preset_combo.addItems(BUILTIN_COLOR_PRESETS.keys())
        for name in sorted(self._game_colorsets):
            self.color_preset_combo.addItem(GAME_PRESET_PREFIX + name)
        user = self._user_color_presets()
        if user:
            self.color_preset_combo.addItems(sorted(user.keys()))
        if current and self.color_preset_combo.findText(current) >= 0:
            self.color_preset_combo.setCurrentText(current)
        self.color_preset_combo.blockSignals(False)

    def _preset_colors(self, name: str) -> dict | None:
        """The colors dict for a combo entry: builtin, discovered Rhythia
        colorset, or user preset."""
        preset = BUILTIN_COLOR_PRESETS.get(name)
        if preset is not None:
            return preset
        if name.startswith(GAME_PRESET_PREFIX):
            colors = getattr(self, "_game_colorsets", {}).get(name[len(GAME_PRESET_PREFIX):])
            if colors:
                return {
                    "notes": ["#{:02x}{:02x}{:02x}".format(*rgb) for rgb in colors],
                    "cursor": "#ffffff", "trail": "#ffffff", "border": "#ffffff",
                }
            return None
        return self._user_color_presets().get(name)

    def _add_note_swatch(self, hex_color: str = "#ffffff"):
        if len(self.note_swatches) >= 12:
            return
        sw = ColorSwatch(hex_color)
        self.note_swatches.append(sw)
        self.note_swatch_row.addWidget(sw)

    def _remove_note_swatch(self):
        if len(self.note_swatches) <= 1:
            return
        sw = self.note_swatches.pop()
        self.note_swatch_row.removeWidget(sw)
        sw.deleteLater()

    def _set_note_swatches(self, hex_list: list[str]):
        while self.note_swatches:
            self._remove_note_swatch() if len(self.note_swatches) > 1 else self.note_swatches.pop().deleteLater()
        self.note_swatches = []
        for h in (hex_list or ["#ffffff"])[:12]:
            self._add_note_swatch(h)

    def _on_preset_selected(self, name: str):
        preset = self._preset_colors(name)
        self._editors_box.setEnabled(name != FROM_SKIN_PRESET)
        self.delete_preset_btn.setEnabled(name in self._user_color_presets())
        if preset:
            self._set_note_swatches(preset.get("notes", ["#ffffff"]))
            self.cursor_swatch.set_hex(preset.get("cursor", "#ffffff"))
            self.trail_swatch.set_hex(preset.get("trail", "#ffffff"))
            self.border_swatch.set_hex(preset.get("border", "#ffffff"))
        self._save_color_settings()

    def _collect_colors(self) -> dict:
        return {
            "notes": [sw.hex() for sw in self.note_swatches],
            "cursor": self.cursor_swatch.hex(),
            "trail": self.trail_swatch.hex(),
            "border": self.border_swatch.hex(),
        }

    def _color_overrides(self) -> dict | None:
        """The overrides dict for the renderer, or None when colors come
        from the skin/colorset."""
        if self.color_preset_combo.currentText() == FROM_SKIN_PRESET:
            return None
        c = self._collect_colors()
        return {
            "note_colors": [_hex_to_rgb(h) for h in c["notes"]],
            "cursor": _hex_to_rgb(c["cursor"]),
            "trail": _hex_to_rgb(c["trail"]),
            "border": _hex_to_rgb(c["border"]),
        }

    def _save_color_preset(self):
        import json
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name == FROM_SKIN_PRESET or name in BUILTIN_COLOR_PRESETS or name.startswith(GAME_PRESET_PREFIX):
            QMessageBox.warning(self, "Reserved name", "That name is reserved; pick another one.")
            return
        user = self._user_color_presets()
        user[name] = self._collect_colors()
        self._settings.setValue("user_color_presets", json.dumps(user))
        self._reload_preset_combo()
        self.color_preset_combo.setCurrentText(name)

    def _delete_color_preset(self):
        import json
        name = self.color_preset_combo.currentText()
        user = self._user_color_presets()
        if name not in user:
            return
        del user[name]
        self._settings.setValue("user_color_presets", json.dumps(user))
        self._reload_preset_combo()
        self.color_preset_combo.setCurrentText(FROM_SKIN_PRESET)

    def _save_color_settings(self):
        import json
        self._settings.setValue("color_preset_selected", self.color_preset_combo.currentText())
        self._settings.setValue("color_custom", json.dumps(self._collect_colors()))

    def _load_color_settings(self):
        import json
        selected = self._settings.value("color_preset_selected", FROM_SKIN_PRESET, type=str)
        if self.color_preset_combo.findText(selected) < 0:
            selected = FROM_SKIN_PRESET
        self.color_preset_combo.setCurrentText(selected)
        try:
            custom = json.loads(self._settings.value("color_custom", "", type=str) or "{}")
        except Exception:
            custom = {}
        preset = self._preset_colors(selected) or custom
        if preset:
            self._set_note_swatches(preset.get("notes", ["#ffffff"]))
            self.cursor_swatch.set_hex(preset.get("cursor", "#ffffff"))
            self.trail_swatch.set_hex(preset.get("trail", "#ffffff"))
            self.border_swatch.set_hex(preset.get("border", "#ffffff"))
        else:
            self._set_note_swatches(["#ffffff"])
        self._editors_box.setEnabled(selected != FROM_SKIN_PRESET)
        self.delete_preset_btn.setEnabled(selected in self._user_color_presets())

    # ── Preview panel (left column, always visible) ───────────────────

    def _make_preview_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("cardPanel")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(12)

        section = QLabel("PREVIEW")
        section.setObjectName("sectionTitle")
        outer.addWidget(section)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self.preview_label = QLabel("The preview loads here once a replay and map are selected.")
        self.preview_label.setObjectName("previewImage")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(420, 320)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v.addWidget(self.preview_label, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        # Debounced live scrub: while dragging, re-render shortly after the
        # slider settles (cheap once the preview context is cached).
        self._preview_scrub_timer = QTimer(self)
        self._preview_scrub_timer.setSingleShot(True)
        self._preview_scrub_timer.setInterval(150)
        self._preview_scrub_timer.timeout.connect(self._render_preview)

        self.preview_slider = QSlider(Qt.Horizontal)
        self.preview_slider.setRange(0, 100)
        self.preview_slider.setValue(40)
        self.preview_slider.valueChanged.connect(self._on_preview_scrub)
        self.preview_slider.sliderReleased.connect(self._render_preview)

        self.preview_time_label = QLabel("40%")
        self.preview_time_label.setObjectName("fieldLabel")
        self.preview_time_label.setFixedWidth(72)
        self.preview_time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        preview_btn = QPushButton("Render preview")
        preview_btn.setObjectName("secondaryButton")
        preview_btn.setCursor(Qt.PointingHandCursor)
        preview_btn.setToolTip("Shows the frame instantly, then plays a looping 2s animated preview.")
        preview_btn.clicked.connect(self._render_preview_animated)

        # Preview audio is muted by default (looping short clips getting
        # loud unexpectedly is annoying); this toggles it independently of
        # the render's music/hitsound volumes.
        self.preview_mute_btn = QPushButton("Muted")
        self.preview_mute_btn.setObjectName("secondaryButton")
        self.preview_mute_btn.setCursor(Qt.PointingHandCursor)
        self.preview_mute_btn.setCheckable(True)
        self.preview_mute_btn.setChecked(True)
        self.preview_mute_btn.setMinimumWidth(88)
        self.preview_mute_btn.setToolTip("Preview sound is muted — click to unmute.")
        self.preview_mute_btn.toggled.connect(self._on_preview_mute_toggled)

        row.addWidget(self.preview_slider, 1)
        row.addWidget(self.preview_time_label)
        row.addWidget(self.preview_mute_btn)
        row.addWidget(preview_btn)
        v.addLayout(row)

        # Clip row: render only a section of the replay. Empty fields = full
        # video; "Mark" buttons grab the current slider position.
        clip_row = QHBoxLayout()
        clip_row.setContentsMargins(0, 0, 0, 0)
        clip_row.setSpacing(8)

        clip_label = QLabel("Clip")
        clip_label.setObjectName("fieldLabel")
        clip_row.addWidget(clip_label)

        self.clip_start_edit = QLineEdit()
        self.clip_start_edit.setPlaceholderText("start")
        self.clip_start_edit.setFixedWidth(70)
        self.clip_start_edit.setToolTip("Clip start (mm:ss or seconds). Empty = beginning.")
        mark_start_btn = QPushButton("Mark start")
        mark_start_btn.setObjectName("secondaryButton")
        mark_start_btn.setToolTip("Set clip start to the preview slider position")
        mark_start_btn.clicked.connect(lambda: self._mark_clip(self.clip_start_edit))

        self.clip_end_edit = QLineEdit()
        self.clip_end_edit.setPlaceholderText("end")
        self.clip_end_edit.setFixedWidth(70)
        self.clip_end_edit.setToolTip("Clip end (mm:ss or seconds). Empty = end of replay.")
        mark_end_btn = QPushButton("Mark end")
        mark_end_btn.setObjectName("secondaryButton")
        mark_end_btn.setToolTip("Set clip end to the preview slider position")
        mark_end_btn.clicked.connect(lambda: self._mark_clip(self.clip_end_edit))

        clip_clear_btn = QPushButton("×")
        clip_clear_btn.setObjectName("secondaryButton")
        clip_clear_btn.setFixedSize(30, 30)
        clip_clear_btn.setToolTip("Clear the clip range (render the full replay)")
        clip_clear_btn.clicked.connect(lambda: (self.clip_start_edit.clear(), self.clip_end_edit.clear()))

        clip_row.addWidget(self.clip_start_edit)
        clip_row.addWidget(mark_start_btn)
        clip_row.addWidget(self.clip_end_edit)
        clip_row.addWidget(mark_end_btn)
        clip_row.addWidget(clip_clear_btn)
        clip_row.addStretch(1)
        v.addLayout(clip_row)

        outer.addWidget(body, 1)
        return card

    def _mark_clip(self, edit: QLineEdit):
        length_ms = getattr(self, "_preview_length_ms", 0)
        if not length_ms and self.rhr_path:
            try:
                length_ms = rhr.load(self.rhr_path).length_ms
            except Exception:
                length_ms = 0
        if not length_ms:
            self.status_label.setText("Load a replay first to mark clip points.")
            return
        edit.setText(_format_duration(length_ms * self.preview_slider.value() / 100 / 1000))

    @staticmethod
    def _parse_clip_time(text: str) -> float | None:
        """Parses 'mm:ss', 'h:mm:ss' or plain seconds into ms; None if empty."""
        text = text.strip()
        if not text:
            return None
        parts = text.split(":")
        try:
            seconds = 0.0
            for p in parts:
                seconds = seconds * 60 + float(p)
        except ValueError:
            raise ValueError(f"invalid time {text!r} (use mm:ss or seconds)")
        return seconds * 1000.0

    def _clip_range(self) -> tuple[float | None, float | None]:
        """Reads the clip fields; raises ValueError on malformed input."""
        start = self._parse_clip_time(self.clip_start_edit.text())
        end = self._parse_clip_time(self.clip_end_edit.text())
        if start is not None and end is not None and end <= start:
            raise ValueError("clip end must be after clip start")
        return start, end

    def _on_preview_scrub(self, value: int):
        self._stop_preview_animation()
        self._update_preview_time_label(value)
        # Only live-render when a preview was already built once.
        if getattr(self, "_preview_cache", None) is not None:
            self._preview_scrub_timer.start()

    def _update_preview_time_label(self, value: int):
        # Show the song timestamp when a replay is loaded; else a bare %.
        length_ms = getattr(self, "_preview_length_ms", 0)
        if length_ms:
            self.preview_time_label.setText(_format_duration(length_ms * value / 100 / 1000))
        else:
            self.preview_time_label.setText(f"{value}%")

    # ── Animated preview ──────────────────────────────────────────────

    def _schedule_auto_preview(self):
        if self.rhr_path and self.rhm_path:
            self._auto_preview_timer.start()

    def _auto_preview(self):
        # Don't compete with an actual render for CPU.
        if self.worker is not None and self.worker.isRunning():
            return
        if self.rhr_path and self.rhm_path:
            self._render_preview_animated()

    def _stop_preview_animation(self):
        self._anim_timer.stop()
        self._anim_frames = []
        if self._anim_worker is not None and self._anim_worker.isRunning():
            self._anim_worker.cancel()
        self._stop_preview_audio()

    def _stop_preview_audio(self):
        if self._preview_sound is not None:
            self._preview_sound.stop()
            self._preview_sound.deleteLater()
            self._preview_sound = None
        if self._preview_audio_path:
            try:
                os.remove(self._preview_audio_path)
            except OSError:
                pass  # Windows may keep it open briefly; temp dir cleanup gets it
            self._preview_audio_path = ""

    def _on_preview_mute_toggled(self, muted: bool):
        self.preview_mute_btn.setText("Muted" if muted else "Sound")
        self.preview_mute_btn.setToolTip(
            "Preview sound is muted — click to unmute." if muted
            else "Preview sound is on — click to mute."
        )
        if self._preview_sound is not None:
            self._preview_sound.setVolume(0.0 if muted else 1.0)
        elif not muted:
            # Unmuting when no sound is loaded yet (muted preview skips the
            # audio mix entirely — see PreviewAnimWorker.run) needs a fresh
            # render to actually produce something to play.
            self._schedule_auto_preview()

    def _play_preview_audio(self, wav_bytes):
        self._stop_preview_audio()
        if not wav_bytes or QSoundEffect is None:
            return
        try:
            # A fresh file every time: QSoundEffect caches samples per URL,
            # so rewriting the same path would replay the stale snippet.
            f = tempfile.NamedTemporaryFile(prefix="rhr2mp4_preview_", suffix=".wav", delete=False)
            f.write(wav_bytes)
            f.close()
        except OSError:
            return
        self._preview_audio_path = f.name
        sound = QSoundEffect(self)
        sound.setSource(QUrl.fromLocalFile(f.name))
        sound.setLoopCount(QSoundEffect.Infinite)
        sound.setVolume(0.0 if self.preview_mute_btn.isChecked() else 1.0)
        sound.play()
        self._preview_sound = sound

    def _anim_tick(self):
        if not self._anim_frames:
            return
        self._anim_index = (self._anim_index + 1) % len(self._anim_frames)
        pix = QPixmap.fromImage(self._anim_frames[self._anim_index]).scaled(
            max(320, self.preview_label.width()), max(240, self.preview_label.height()),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(pix)

    def _render_preview_animated(self):
        self._render_preview()
        inputs = self._load_render_inputs(interactive=False)
        if inputs is None:
            return
        replay, game_map, skin_obj, note_colors, _note = inputs

        self._stop_preview_animation()
        rw, rh, fps = RESOLUTIONS[self.resolution_combo.currentText()]
        scale = 640.0 / max(rw, rh)
        speed = replay.speed if replay.speed and replay.speed > 0 else 1.0
        mode = MOTION_BLUR_MODES[self.motion_blur_combo.currentText()]
        opts = {
            "w": max(2, int(rw * scale)), "h": max(2, int(rh * scale)),
            "t0": replay.length_ms * self.preview_slider.value() / 100.0,
            "spawn": self.spawn_distance_spin.value(),
            "approach": self.approach_rate_spin.value(),
            "parallax": self.parallax_check.isChecked(),
            "trail": self.trail_check.isChecked(),
            "dots": self.bg_dots_check.isChecked(),
            "trail_scale": self.trail_length_spin.value() / 100.0,
            "hit_fx": self.hit_fx_check.isChecked(),
            "hud": self._hud_overrides(),
            "color_overrides": self._color_overrides(),
            "blur": self.motion_blur_spin.value() / 100.0 if mode != "off" else 0.0,
            "blur_mode": mode,
            "frame_dt": 1000.0 / fps * speed,
            "music_vol": self.music_volume_spin.value(),
            "hit_vol": self.hit_volume_spin.value(),
            "muted": self.preview_mute_btn.isChecked(),
        }
        self._anim_worker = PreviewAnimWorker(replay, game_map, skin_obj, note_colors, opts)
        self._anim_worker.done.connect(self._on_anim_ready)
        self._anim_worker.start()
        self.preview_time_label.setText("animating…")

    def _on_anim_ready(self, frames: list, audio):
        if not frames:
            self._update_preview_time_label(self.preview_slider.value())
            return
        self._anim_frames = frames
        self._anim_index = 0
        self._anim_timer.start()
        self._play_preview_audio(audio)
        self._update_preview_time_label(self.preview_slider.value())

    def _render_preview(self):
        self._preview_scrub_timer.stop()
        self._stop_preview_animation()
        inputs = self._load_render_inputs(interactive=False)
        if inputs is None:
            self.status_label.setText("Preview needs the .rhr and .rhm selected.")
            return
        replay, game_map, skin_obj, note_colors, _note = inputs

        from ..render.frame import build_context, draw_frame_blurred, draw_frame_tmix
        from ..render.skin_runtime import resolve as resolve_skin
        from ..sim.hitreg import match_hits
        from ..sim.mods import resolve_mods
        from ..sim.timeline import Timeline

        key = (
            self.rhr_path, self.rhm_path, self.skin_field.path, self.colorset_field.path,
            self.gamedir_edit.text().strip(), self.spawn_distance_spin.value(),
            self.approach_rate_spin.value(), self.parallax_check.isChecked(),
            self.trail_check.isChecked(), repr(self._color_overrides()),
            self.bg_dots_check.isChecked(), self.trail_length_spin.value(),
            self.hit_fx_check.isChecked(), repr(sorted(self._hud_overrides().items())),
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cache = getattr(self, "_preview_cache", None)
            if cache is not None and cache[0] == key:
                _, ctx, timeline = cache
            else:
                runtime = resolve_skin(skin_obj)
                if not self.parallax_check.isChecked():
                    runtime.parallax = 0.0
                if not self.trail_check.isChecked():
                    runtime.cursor_trail_enabled = False
                runtime.ambient_dots_enabled = self.bg_dots_check.isChecked()
                runtime.cursor_trail_scale = self.trail_length_spin.value() / 100.0
                runtime.hit_effects_enabled = self.hit_fx_check.isChecked()
                for flag, shown in self._hud_overrides().items():
                    setattr(runtime, flag, shown)
                overrides = self._color_overrides()
                if overrides:
                    runtime.note_colors = list(overrides["note_colors"])
                    runtime.cursor_color = overrides["cursor"]
                    runtime.cursor_trail_color = overrides["trail"]
                    runtime.border_color = overrides["border"]
                if note_colors:
                    runtime.note_colors = list(note_colors)
                notes, ghost, chaos, extent, mods_label = resolve_mods(game_map.notes, replay)
                results = match_hits(notes, replay.frames)
                timeline = Timeline(
                    notes, results, replay,
                    spawn_distance=self.spawn_distance_spin.value(),
                    approach_rate=self.approach_rate_spin.value(),
                    ghost=ghost, chaos=chaos,
                )
                ctx = build_context(1280, 720, game_map.cover_bytes, runtime, playfield_extent=extent)
                ctx.mods_label = mods_label
                self._preview_cache = (key, ctx, timeline)

            self._preview_length_ms = replay.length_ms
            t = replay.length_ms * self.preview_slider.value() / 100.0
            # Match the render: apply motion blur to the preview frame too,
            # using whichever implementation the selected mode will use.
            fps = RESOLUTIONS[self.resolution_combo.currentText()][2]
            speed = replay.speed if replay.speed and replay.speed > 0 else 1.0
            frame_dt_ms = 1000.0 / fps * speed
            mode = MOTION_BLUR_MODES[self.motion_blur_combo.currentText()]
            motion_blur = self.motion_blur_spin.value() / 100.0 if mode != "off" else 0.0
            if mode == "subframe":
                img = draw_frame_blurred(ctx, timeline, game_map.metadata, replay,
                                         t, frame_dt_ms, motion_blur)
            else:
                img = draw_frame_tmix(ctx, timeline, game_map.metadata, replay,
                                      t, frame_dt_ms, motion_blur)
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg).scaled(
                max(320, self.preview_label.width()), max(240, self.preview_label.height()),
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self.preview_label.setPixmap(pix)
            self._update_preview_time_label(self.preview_slider.value())
        except Exception as e:
            QMessageBox.critical(self, "Preview error", f"Could not render the preview:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    # ── Window-wide drag & drop routing ───────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        routes = {
            ".rhm": lambda p: self.rhm_field.set_path(p),
            ".rhs": lambda p: self.skin_field.set_path(p),
            ".txt": lambda p: self.colorset_field.set_path(p),
        }
        rhr_paths: list[str] = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            if ext == ".rhr":
                rhr_paths.append(path)
            else:
                handler = routes.get(ext)
                if handler:
                    handler(path)

        if not rhr_paths:
            return
        rendering = self.worker is not None and self.worker.isRunning()
        if rendering:
            # Mid-render, everything dropped joins the queue.
            for p in rhr_paths:
                self._enqueue_replay(p)
        else:
            self.rhr_field.set_path(rhr_paths[0])
            for p in rhr_paths[1:]:
                self._enqueue_replay(p)
        if len(rhr_paths) > 1 or rendering:
            self.status_label.setText(
                f"{len(self.render_queue)} replay(s) queued — they'll render one after another."
            )

    # ── Footer: render button + progress (always visible) ─────────────

    def _make_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footerBar")
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(28, 14, 28, 14)
        layout.setSpacing(10)

        self.render_button = QPushButton("▶  Start Render")
        self.render_button.setObjectName("renderButton")
        self.render_button.setCursor(Qt.PointingHandCursor)
        self.render_button.setShortcut("Ctrl+R")
        self.render_button.setToolTip("Start rendering (Ctrl+R)")
        self.render_button.clicked.connect(self.start_render)
        layout.addWidget(self.render_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(10)

        self.status_label = QLabel("Ready — drop your files and hit render.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.status_label, 1)

        self.eta_label = QLabel("")
        self.eta_label.setObjectName("etaLabel")
        self.eta_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status_row.addWidget(self.eta_label)

        self.play_button = QPushButton("▶ Play")
        self.play_button.setObjectName("secondaryButton")
        self.play_button.clicked.connect(self.play_output)
        self.play_button.setEnabled(False)
        self.play_button.setCursor(Qt.PointingHandCursor)
        self.play_button.setToolTip("Open the rendered video in your player")
        status_row.addWidget(self.play_button)

        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setObjectName("secondaryButton")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.setCursor(Qt.PointingHandCursor)
        status_row.addWidget(self.open_folder_button)

        layout.addLayout(status_row)

        return footer

    def play_output(self):
        path = self._last_output_path or self.output_edit.text().strip()
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _notify(self, title: str, message: str):
        """Fire a system notification; falls back to notify-send on Linux
        when no system tray is available."""
        try:
            from PyQt5.QtWidgets import QSystemTrayIcon

            if QSystemTrayIcon.isSystemTrayAvailable():
                if self._tray is None:
                    icon = QPixmap(self._asset_path("rhythialogo.png"))
                    from PyQt5.QtGui import QIcon

                    self._tray = QSystemTrayIcon(QIcon(icon), self)
                    self._tray.show()
                self._tray.showMessage(title, message, QSystemTrayIcon.Information, 6000)
                return
        except Exception:
            pass
        try:
            import subprocess

            subprocess.Popen(["notify-send", title, message])
        except Exception:
            pass

    # ── Widget helpers ────────────────────────────────────────────────

    @staticmethod
    def _style_combobox(combo: QComboBox):
        """Force the popup view to use dark colors (Linux Qt5 workaround)."""
        view = combo.view()
        view.setStyleSheet(
            "QAbstractItemView {"
            "  background-color: #0e1634;"
            "  color: #dce4ff;"
            "  border: 1px solid #1e2d5a;"
            "  outline: none;"
            "  selection-background-color: #253570;"
            "  selection-color: #ffffff;"
            "}"
        )
        # Ensure popup window has no translucency artifacts
        combo.view().window().setWindowFlags(
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        )
        combo.view().window().setAttribute(Qt.WA_TranslucentBackground, False)

    def _wrap_labeled(self, label: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return container

    def _file_row(self, label: str, field: QWidget, clear_button: QPushButton) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel(label)
        title.setObjectName("fieldLabel")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        clear_button.setFixedSize(74, 34)
        clear_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(field, 1)
        row.addWidget(clear_button, 0)
        layout.addWidget(title)
        layout.addLayout(row)
        return container

    def _line_row(self, label: str, line_edit: QLineEdit, action_button: QPushButton) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel(label)
        title.setObjectName("fieldLabel")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        action_button.setFixedSize(86, 34)
        row.addWidget(line_edit, 1)
        row.addWidget(action_button, 0)
        layout.addWidget(title)
        layout.addLayout(row)
        return container

    def _asset_path(self, filename: str) -> str:
        from ..paths import asset_path

        return asset_path(filename)

    # ── File selection slots ──────────────────────────────────────────

    def _set_rhr(self, path: str):
        # Swapping to a different replay invalidates a manually chosen
        # output name (it referred to the previous replay).
        if self.rhr_path and path != self.rhr_path:
            self._output_auto = True
        self.rhr_path = path
        try:
            replay = rhr.load(path)
        except Exception as e:
            self.rhr_info.setText(f"⚠ Could not read replay: {e}")
            self.rhr_info.setVisible(True)
            return

        parts = [replay.username, _format_duration(replay.length_ms / 1000)]
        if abs(replay.speed - 1.0) > 1e-3:
            parts.append(f"{replay.speed:.2f}x")
        parts.append(f"{replay.accuracy_pct:.1f}%")
        from ..sim.mods import parse_mods
        mods = parse_mods(replay.mods)
        if mods:
            nice = {"mod_hardrock": "HR", "mod_mirror": "MR", "mod_ghost": "GH", "mod_chaos": "CH"}
            parts.append("+".join(nice.get(m, m) for m in sorted(mods)))
        played = replay.date_played
        if played is not None:
            parts.append(played.astimezone().strftime("%d/%m/%Y %H:%M"))
        self.rhr_info.setText("  ·  ".join(str(p) for p in parts))
        self.rhr_info.setVisible(True)

        # (Re-)pair the map the replay was played on. A manually chosen map
        # is kept as long as it still matches the new replay; an
        # auto-detected (or mismatched) one is re-detected, so swapping the
        # replay swaps the map and the output name along with it.
        need_map = not self.rhm_path or self._rhm_auto
        if not need_map:
            try:
                need_map = not _map_matches_replay(replay, rhm.load(self.rhm_path))
            except Exception:
                need_map = True
        if need_map:
            found = self._autofind_map(replay)
            if found:
                if found != self.rhm_path:
                    self.rhm_field.set_path(found)
                    self.status_label.setText(f"Map auto-detected: {os.path.basename(found)}")
                self._rhm_auto = True
            elif self.rhm_path and self._rhm_auto:
                # Stale auto-detected map from the previous replay.
                self.clear_rhm()

        self._suggest_output()
        self._schedule_auto_preview()

    def _set_rhm(self, path: str):
        self.rhm_path = path
        # Any direct set counts as a manual choice; the auto-detect callers
        # in _set_rhr / _load_next_queue_item flip this back right after.
        self._rhm_auto = False
        try:
            game_map = rhm.load(path)
        except Exception as e:
            self.rhm_cover.clear()
            self.rhm_title.setText("⚠ Could not read map")
            self.rhm_meta.setText(str(e))
            self.rhm_info_box.setVisible(True)
        else:
            meta = game_map.metadata
            title = meta.song_name or meta.title or os.path.basename(path)
            self.rhm_title.setText(title)

            parts = []
            if meta.mappers:
                parts.append("mapped by " + ", ".join(meta.mappers))
            diff = meta.custom_difficulty_name or DIFFICULTY_NAMES.get(meta.difficulty, "")
            if diff:
                parts.append(diff)
            if meta.star_rating:
                parts.append(f"★ {meta.star_rating:.1f}")
            if game_map.duration_ms:
                parts.append(_format_duration(game_map.duration_ms / 1000))
            parts.append(f"{len(game_map.notes):,} notes")
            self.rhm_meta.setText("  ·  ".join(parts))

            cover = QPixmap()
            if game_map.cover_bytes and cover.loadFromData(game_map.cover_bytes):
                self.rhm_cover.setPixmap(_rounded_pixmap(cover, 48, 10))
                self.rhm_cover.setVisible(True)
            else:
                self.rhm_cover.clear()
                self.rhm_cover.setVisible(False)
            self.rhm_info_box.setVisible(True)
        self._suggest_output()
        self._schedule_auto_preview()

    def clear_rhr(self):
        self.rhr_path = ""
        self.rhr_field.clear_path()
        self.rhr_info.setVisible(False)

    def clear_rhm(self):
        self.rhm_path = ""
        self._rhm_auto = False
        self.rhm_field.clear_path()
        self.rhm_info_box.setVisible(False)

    def open_output_folder(self):
        output_path = self.output_edit.text().strip()
        if not output_path:
            return
        folder = os.path.dirname(output_path)
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def pick_rhr(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose replay", "", "Rhythia Replay (*.rhr)")
        if path:
            self.rhr_field.set_path(path)

    def pick_rhm(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose map", "", "Rhythia Map (*.rhm)")
        if path:
            self.rhm_field.set_path(path)

    def pick_skin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose skin", "", "Rhythia Skin (*.rhs)")
        if path:
            self.skin_field.set_path(path)

    def clear_skin(self):
        self.skin_field.clear_path()

    def _apply_skin_gameplay_settings(self, path: str):
        try:
            skin = rhs.load(path)
        except Exception:
            return
        if skin.approach_rate > 0:
            self.approach_rate_spin.setValue(skin.approach_rate)
        if skin.spawn_distance > 0:
            self.spawn_distance_spin.setValue(skin.spawn_distance)

    def pick_colorset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose colorset", "", "Colorset (*.txt)")
        if path:
            self.colorset_field.set_path(path)

    def clear_colorset(self):
        self.colorset_field.clear_path()

    def pick_gamedir(self):
        path = QFileDialog.getExistingDirectory(self, "Choose Rhythia folder")
        if path:
            self.gamedir_edit.setText(path)

    def pick_output(self):
        start = self.output_edit.text().strip() or self._settings.value("output_dir", "", type=str)
        path, _ = QFileDialog.getSaveFileName(self, "Save video as", start, "Video (*.mp4)")
        if path:
            self.output_edit.setText(path)
            self._output_auto = False
            self._settings.setValue("output_dir", os.path.dirname(path))

    def _preferred_output_dir(self, fallback_path: str) -> str:
        """The last folder a video was actually saved to, if it still
        exists; otherwise the folder the replay itself lives in (the
        original default, for first-time use)."""
        saved = self._settings.value("output_dir", "", type=str)
        if saved and os.path.isdir(saved):
            return saved
        return os.path.dirname(fallback_path)

    def _suggest_output(self):
        # Skip if user manually typed/browsed a path
        if self.output_edit.text() and not self._output_auto:
            return
        if not self.rhr_path:
            return
        out_dir = self._preferred_output_dir(self.rhr_path)
        # Try to build "{map name} played by {username}.mp4"
        try:
            replay = rhr.load(self.rhr_path)
            username = replay.username or "unknown"
        except Exception:
            username = "unknown"
        if self.rhm_path:
            try:
                game_map = rhm.load(self.rhm_path)
                map_name = game_map.metadata.song_name or game_map.metadata.title or "unknown"
            except Exception:
                map_name = "unknown"
        else:
            map_name = None
        self.output_edit.setText(
            os.path.join(out_dir, locate.default_output_name(map_name, username, self.rhr_path))
        )
        self._output_auto = True
        # The output field lives in the options dialog now, so surface the
        # destination on the main screen.
        self.status_label.setText(
            f"Ready — will save to {os.path.basename(self.output_edit.text())} (change in ⚙ Options)"
        )

    # ── Input loading (shared by render and preview) ──────────────────

    def _load_render_inputs(self, interactive: bool):
        """Loads and validates every selected file. Returns
        (replay, game_map, skin, note_colors, colorset_note) or None.
        When `interactive` is False, missing required files return None
        silently instead of popping dialogs (used by the preview)."""
        if not self.rhr_path or not self.rhm_path:
            if interactive:
                QMessageBox.warning(self, "Missing files", "Select both the .rhr and .rhm before rendering.")
            return None
        try:
            replay = rhr.load(self.rhr_path)
            game_map = rhm.load(self.rhm_path)
            skin = rhs.load(self.skin_field.path) if self.skin_field.path else None
            note_colors = None
            if self.colorset_field.path:
                with open(self.colorset_field.path, encoding="utf-8", errors="replace") as f:
                    note_colors = rhs.parse_colorset(f.read())
                if not note_colors:
                    if interactive:
                        QMessageBox.warning(
                            self, "Empty colorset",
                            "No valid colors were found in the selected colorset "
                            "(expected comma-separated hex colors, e.g. ff0059,ffd8e6).")
                        return None
                    note_colors = None

            colorset_note = ""
            game_dir = self.gamedir_edit.text().strip()
            self._settings.setValue("game_dir", game_dir)
            if note_colors is None and skin is not None and not skin.note_colors:
                colorset_ref = str(skin.raw.get("ColorSet") or "")
                if colorset_ref:
                    found = rhs.resolve_colorset_path(game_dir, colorset_ref)
                    if found:
                        with open(found, encoding="utf-8", errors="replace") as f:
                            note_colors = rhs.parse_colorset(f.read()) or None
                        if note_colors:
                            colorset_note = f" (colorset: {os.path.basename(found)})"
                    if note_colors is None:
                        # No game folder configured (or no hit there): try
                        # the known Rhythia locations + bundled colorsets.
                        note_colors = colorsets.find_colorset_by_name(colorset_ref, game_dir)
                        if note_colors:
                            colorset_note = f" (colorset: {os.path.basename(colorset_ref)})"
                        else:
                            colorset_note = f" (colorset \"{os.path.basename(colorset_ref)}\" not found)"
        except Exception as e:
            QMessageBox.critical(self, "File read error", f"Could not read the selected files:\n{e}")
            return None
        return replay, game_map, skin, note_colors, colorset_note

    # ── Map auto-detection ────────────────────────────────────────────

    def _autofind_map(self, replay, rhr_path: str | None = None) -> str | None:
        """Looks for the .rhm this replay was played on: same folder as the
        replay, then the game folder's exports/ (shared logic in
        formats/locate.py)."""
        rhr_path = rhr_path or self.rhr_path
        candidates = [os.path.dirname(rhr_path)]
        game_dir = self.gamedir_edit.text().strip()
        if game_dir:
            candidates.append(os.path.join(game_dir, "exports"))
        return locate.find_map_for_replay(replay, candidates, replay_filename=rhr_path)

    # ── Render button state styling ──────────────────────────────────

    def _set_render_button_state(self, state: str):
        """Set visual state: 'idle' or 'cancel'."""
        self.render_button.setProperty("renderState", state)
        self.render_button.style().unpolish(self.render_button)
        self.render_button.style().polish(self.render_button)

    def _set_progress_active(self, active: bool):
        self.progress_bar.setProperty("renderActive", "true" if active else "false")
        self.progress_bar.style().unpolish(self.progress_bar)
        self.progress_bar.style().polish(self.progress_bar)

    # ── Rendering ─────────────────────────────────────────────────────

    def start_render(self, from_queue: bool = False):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if self.render_queue:
                self.render_queue.clear()
                self._refresh_queue_ui()
                self.status_label.setText("Canceling… (queue cleared)")
            else:
                self.status_label.setText("Canceling…")
            self.render_button.setText("▶  Start Render")
            self._set_render_button_state("idle")
            self._eta_timer.stop()
            self.eta_label.setText("")
            return

        if not from_queue:
            self._batch_done = 0

        if not self.rhr_path or not self.rhm_path:
            QMessageBox.warning(self, "Missing files", "Select both the .rhr and .rhm before rendering.")
            return

        output_path = self.output_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, "Missing output", "Choose where to save the .mp4.")
            return

        inputs = self._load_render_inputs(interactive=True)
        if inputs is None:
            return
        replay, game_map, skin, note_colors, colorset_note = inputs

        if not _map_matches_replay(replay, game_map):
            proceed = QMessageBox.question(
                self,
                "Map may not match replay",
                "The selected .rhm does not appear to be the map used by this replay "
                f"(replay points to \"{replay.map_legacy_id}\" (id {replay.map_online_id}), "
                f"selected file is \"{game_map.metadata.legacy_id}\" (id {game_map.metadata.online_id})).\n\n"
                "Render anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                return

        width, height, fps = RESOLUTIONS[self.resolution_combo.currentText()]
        quality = QUALITIES[self.quality_combo.currentText()]
        spawn_distance = self.spawn_distance_spin.value()
        approach_rate = self.approach_rate_spin.value()
        parallax_enabled = self.parallax_check.isChecked()
        trail_enabled = self.trail_check.isChecked()
        video_codec = CODECS[self.codec_combo.currentText()]
        hw_accel = HW_ACCELS[self.hw_combo.currentText()]
        audio_bitrate = AUDIO_BITRATES[self.audio_combo.currentText()]
        color_overrides = self._color_overrides()
        background_dots_enabled = self.bg_dots_check.isChecked()
        trail_scale = self.trail_length_spin.value() / 100.0
        motion_blur_mode = MOTION_BLUR_MODES[self.motion_blur_combo.currentText()]
        motion_blur = self.motion_blur_spin.value() / 100.0 if motion_blur_mode != "off" else 0.0
        intro_enabled = self.intro_check.isChecked()
        hit_effects_enabled = self.hit_fx_check.isChecked()
        try:
            clip_start_ms, clip_end_ms = self._clip_range()
        except ValueError as e:
            QMessageBox.warning(self, "Invalid clip range", str(e))
            return

        # Resolve now (with probing) so the status line names the encoder
        # actually used, including auto/fallback decisions.
        from ..render.video import resolve_encoder
        _hw, encoder_name = resolve_encoder(video_codec, hw_accel)
        colorset_note += f" · {encoder_name}"
        if self.render_queue or self._batch_done:
            colorset_note += f" · video {self._batch_done + 1}/{self._batch_done + 1 + len(self.render_queue)}"

        self.progress_bar.setValue(0)
        self.open_folder_button.setEnabled(False)
        self._colorset_note = colorset_note
        self._render_start_time = time.monotonic()
        self._last_done = 0
        self._last_total = 0
        self.status_label.setText(f"Rendering…{colorset_note}")
        self.eta_label.setText("00:00 elapsed")
        self.render_button.setText("■  Cancel")
        self._set_render_button_state("cancel")
        self._set_progress_active(True)
        self._eta_timer.start()

        self.worker = RenderWorker(replay, game_map, skin, output_path, width, height, fps,
                                    quality, spawn_distance, approach_rate, parallax_enabled,
                                    trail_enabled, note_colors, video_codec, hw_accel, audio_bitrate,
                                    color_overrides, background_dots_enabled, trail_scale,
                                    motion_blur, motion_blur_mode,
                                    clip_start_ms, clip_end_ms, intro_enabled, hit_effects_enabled,
                                    self._hud_overrides(),
                                    self.music_volume_spin.value(), self.hit_volume_spin.value())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished_ok)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_progress(self, done: int, total: int):
        pct = int(100 * done / total) if total else 0
        self.progress_bar.setValue(pct)
        self._last_done = done
        self._last_total = total
        self.status_label.setText(f"Frame {done:,}/{total:,}{self._colorset_note}")
        self._update_eta_display()

    def _update_eta_display(self):
        if self._render_start_time is None:
            return
        elapsed = time.monotonic() - self._render_start_time
        elapsed_str = _format_duration(elapsed)
        if self._last_done > 0 and self._last_total > 0:
            remaining_frames = self._last_total - self._last_done
            rate = self._last_done / elapsed if elapsed > 0 else 0
            eta = remaining_frames / rate if rate > 0 else 0
            eta_str = _format_duration(eta)
            self.eta_label.setText(f"{elapsed_str} elapsed  ·  ~{eta_str} remaining")
        else:
            self.eta_label.setText(f"{elapsed_str} elapsed")

    def on_finished_ok(self, output_path: str):
        self._eta_timer.stop()
        elapsed = time.monotonic() - self._render_start_time if self._render_start_time else 0
        self._render_start_time = None
        self.render_button.setText("▶  Start Render")
        self._set_render_button_state("idle")
        self._set_progress_active(False)
        self.open_folder_button.setEnabled(True)
        self.play_button.setEnabled(True)
        self._last_output_path = output_path
        self._settings.setValue("output_dir", os.path.dirname(output_path))
        self.progress_bar.setValue(100)
        self.eta_label.setText("")
        self._batch_done += 1

        # Batch: chain straight into the next queued replay.
        if self.render_queue and self._load_next_queue_item():
            QTimer.singleShot(100, lambda: self.start_render(from_queue=True))
            return

        elapsed_str = _format_duration(elapsed)
        if self._batch_done > 1:
            self.status_label.setText(f"✓ Queue done — {self._batch_done} videos rendered")
            self._notify("rhr2mp4", f"Queue finished: {self._batch_done} videos rendered.")
        else:
            self.status_label.setText(f"✓ Done in {elapsed_str} — {os.path.basename(output_path)}")
            self._notify("rhr2mp4 — render complete", os.path.basename(output_path))

    def on_failed(self, message: str):
        self._eta_timer.stop()
        self._render_start_time = None
        self.render_button.setText("▶  Start Render")
        self._set_render_button_state("idle")
        self._set_progress_active(False)
        self.open_folder_button.setEnabled(False)
        self.play_button.setEnabled(False)
        # A failure stops the chain; queued items stay listed for a retry.
        if self.render_queue:
            self.status_label.setText("✗ Failed — queue paused (hit Start Render to resume)")
        else:
            self.status_label.setText("✗ Failed")
        self.eta_label.setText("")
        QMessageBox.critical(self, "Render error", message)


def main():
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec_()
