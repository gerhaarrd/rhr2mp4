# rhr2mp4

Converts Rhythia replays (`.rhr`) into `.mp4` videos by reconstructing the run
from the replay data and its matching map (`.rhm`).

The project includes:

- a PyQt5 GUI with preview, render queue, and persistent settings
- a headless CLI renderer
- automatic `.rhm` detection from the replay
- video rendering with audio, optional intro, partial clips, motion blur, and
  support for H.264, HEVC, and AV1
- visual overrides for HUD, skin, and colorset

## Download

Prebuilt packages are on the
[releases page](https://github.com/gerhaarrd/rhr2mp4/releases):

- **Windows** — `rhr2mp4-windows-x86_64.zip`: unzip and run `rhr2mp4.exe`
  (GUI) or `rhr2mp4-cli.exe` (command line). Fully self-contained, ffmpeg
  included.
- **Linux** — `rhr2mp4-x86_64.AppImage`: `chmod +x` and run. Requires
  `ffmpeg` installed on the system.

To run from source instead, see below.

## Requirements

- Python 3.10+
- `ffmpeg` installed and available on `PATH`

Current Python dependencies:

- `PyQt5`
- `Pillow`
- `numpy`

## Installation

Run these commands from this directory (`rhr2mp4/`):

```bash
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Prebuilt Packages

Standalone builds live in `packaging/` (no Python needed to run them):

- **Linux AppImage** — `./packaging/build_appimage.sh` produces
  `packaging/dist/rhr2mp4-x86_64.AppImage` (PyInstaller + appimagetool).
  Requires `ffmpeg` installed on the system at runtime.
- **Windows zip** — `./packaging/build_windows_wine.sh` cross-builds from
  Linux using Wine (installs Windows Python into a cached prefix) and
  produces `packaging/dist-win/rhr2mp4-windows-x86_64.zip`, which is fully
  self-contained: `rhr2mp4.exe` (GUI), `rhr2mp4-cli.exe` (command line) and
  a bundled `ffmpeg.exe`.
- **CI** — `.github/workflows/build.yml` builds both artifacts on native
  runners (on `v*` tags or manual dispatch) if the repo is pushed to GitHub.

Both builds share `packaging/rhr2mp4.spec`. A bundled `ffmpeg`/`ffmpeg.exe`
placed next to the executable is picked up automatically (see
`rhr2mp4/paths.py`); otherwise the one on `PATH` is used.

## GUI Usage

```bash
./.venv/bin/python main.py
```

The app opens a large preview on the left and controls on the right. You can:

- drop or select a `.rhr` replay
- let the app auto-detect the matching `.rhm`
- load an optional `.rhs` skin, `.txt` colorset, and game directory
- scrub through the replay with the slider
- mark clip start and end points
- render a short preview around the current position
- queue multiple `.rhr` files and render them sequentially

When rendering finishes, the app offers actions to play the video and open the
output folder, and it will try to show a system notification.

## GUI Options

The options dialog (`Ctrl+O`) contains the main settings:

- **Output**: final `.mp4` path
- **Render settings**: resolution, FPS, quality, codec, hardware acceleration,
  audio bitrate, spawn distance, approach rate, trail size, motion blur,
  parallax, background dots, hit effects, and intro
- **HUD elements**: show or hide title, progress, combo, grade, accuracy,
  score, points, misses, notes, health, speed, and other HUD items
- **Optional resources**: `.rhs` skin, `.txt` colorset, game directory
- **Colors**: built-in presets, user-saved presets, and the colorsets
  auto-discovered from the Rhythia install (shown as "Rhythia: …" — a few,
  like the game's default, are bundled with the app so no install is needed)

Settings persist across sessions.

## CLI Usage

When `main.py` receives arguments, it runs without opening the GUI:

```bash
./.venv/bin/python main.py replay.rhr [map.rhm] -o out.mp4
```

If the map is omitted, the program looks for it:

- in the replay's directory
- in `exports/` inside `--game-dir`, when provided

More complete example:

```bash
./.venv/bin/python main.py replay.rhr \
  --game-dir /path/to/Rhythia \
  --skin skin.rhs \
  --colorset colors.txt \
  --resolution 1920x1080 \
  --fps 60 \
  --codec h264 \
  --hw auto \
  --clip 0:10-0:35 \
  --intro \
  --motion-blur filter \
  -o output.mp4
```

Useful CLI options:

- `--resolution WxH`
- `--vertical`
- `--fps 30|60`
- `--quality fast|balanced|quality`
- `--codec h264|hevc|av1`
- `--hw auto|nvenc|vaapi|qsv|none`
- `--audio-bitrate 128k|192k|256k|320k`
- `--music-volume PCT` (default 100; 0 mutes the track)
- `--hitsound-volume PCT` (default 100; 0 disables hit sounds; skins without
  their own hit sound use the app's bundled default)
- `--skin file.rhs`
- `--colorset file.txt` (or a discovered colorset name; see `--list-colorsets`)
- `--game-dir /path/to/game`
- `--spawn-distance N`
- `--approach-rate N`
- `--no-trail`
- `--no-parallax`
- `--no-dots`
- `--no-hit-effects`
- `--trail-length PCT`
- `--motion-blur off|filter|subframe`
- `--blur-intensity PCT`
- `--clip START-END`
- `--intro`
- `--hide title,progress,combo,...|all`
- `--workers N`

Use `--help` for the full argument list.

## Parser Validation

```bash
./.venv/bin/python scripts/validate.py replay.rhr map.rhm
```

This script checks whether the parsers read both files consistently, including
map IDs, note counts, accuracy, and duration.

## Project Structure

- `main.py`: entry point; launches the GUI without arguments and the CLI with
  arguments
- `rhr2mp4/cli.py`: headless command-line interface
- `rhr2mp4/gui/`: PyQt5 application and stylesheet
- `rhr2mp4/formats/`: parsers for `.rhr`, `.rhm`, `.rhs`, plus map lookup
- `rhr2mp4/sim/`: hit registration, timeline generation, and mod handling
- `rhr2mp4/render/`: frame composition, intro rendering, audio, and video
  pipeline
- `scripts/validate.py`: quick parser sanity checks

## Rendering Pipeline

The replay is parsed, converted into a visual timeline, and rendered in
parallel segments. Each worker draws frames and encodes its own segment with
`ffmpeg`; at the end, the segments are concatenated and the `.rhm` audio is
muxed into the final output.

When `--hw auto` is enabled, the project probes available encoders and tries
NVENC, VAAPI, or QSV before falling back to CPU encoding.
