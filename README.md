# rhr2mp4

Converts Rhythia replays (`.rhr`) into `.mp4` videos by reconstructing the run
from the replay data and its matching map (`.rhm`, or a Sound Space Plus `.sspm`).

The project includes:

- a PyQt5 GUI with preview, render queue, and persistent settings
- a headless CLI renderer
- automatic map detection (`.rhm` or `.sspm`) from the replay
- automatic map download from rhythia.com when no local copy is found
  (verified against the replay's beatmap hash)
- video rendering with audio, optional intro, partial clips, motion blur, and
  support for H.264, HEVC, and AV1 — plus `.webm` and animated `.gif` output
- custom background image, looping video, or animated GIF/WebP (files that
  ffmpeg can't decode, like animated WebP saved as .gif, are transcoded
  automatically via Pillow), with a brightness control (darken or brighten)
- auto-highlight: picks the best clip window (densest section, near-deaths,
  the fail moment) automatically
- an optional osu!-style hit-error (timing) bar and a live stats card
  (rolling UR, mean offset, timing histogram)
- movable HUD: drag any element (title, panels, combo, health bar, timing
  bar, stats, versus panel) on the GUI preview to reposition it, or use
  `--move` on the CLI; right-click an element to remove it from the video
  (`--hide` on the CLI), including the playfield border (`--no-border`)
- removable skin images: right-click a skin's decorative image (mascots,
  side art) on the preview to take it out of the video (`--hide-assets`
  on the CLI) while keeping the rest of the skin
- render effects: dynamic camera (combo zoom + miss shake), beat pulse
  (background reacts to the music), miss particles, note spawn pop-in,
  cinematic edge blur (depth of field), and reverse playback
- ghost race: overlay a second replay of the same map (its cursor in a
  distinct color plus a side-by-side live stats panel)
- picture-in-picture overlay for a webcam recording
- montage: auto-picks the best moment of several replays and joins them
  into one highlight reel with styled transitions (GUI queue button or
  `--montage` on the CLI)
- shareable preset bundles (`.rhrp`): all render settings plus the skin and
  colorset files in one file (GUI Export/Import, CLI `--preset`)
- post-render webhook (Discord-compatible; attaches the video when small
  enough)
- pre-render time estimate in the GUI, and live elapsed/remaining during
  the render
- visual overrides for HUD, skin, and colorset
- an `.sspm` → `.rhm` converter (`--convert`)

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
- let the app auto-detect the matching map (`.rhm` or `.sspm`)
- load an optional `.rhs` skin, `.txt` colorset, and game directory
- scrub through the replay with the slider
- mark clip start and end points (or hit **✨ Auto** to auto-pick a highlight)
- drag HUD elements around on the preview to customize the layout
  (double-click one to reset it; **↺ Layout** resets everything)
- right-click a HUD element on the preview to hide it (re-check it in
  Options → HUD elements to bring it back), or right-click one of the
  skin's decorative images (mascots, side art) to remove it from the
  video — right-click again anywhere to show hidden images
- render a short preview around the current position
- queue multiple `.rhr` files and render them sequentially
- turn the queue into a highlight reel with the **🎬 Montage** button (the
  best ~15s of each replay, joined with the chosen transition)
- watch a folder so new replays are queued automatically (Options → Optional
  resources)

When rendering finishes, the app offers actions to play the video and open the
output folder, and it will try to show a system notification.

## GUI Options

The options dialog (`Ctrl+O`) contains the main settings:

- **Output**: final path — the extension picks the container (`.mp4`, `.webm`
  or `.gif`)
- **Render settings**: named presets (plus Export/Import of shareable
  `.rhrp` bundles), resolution, FPS, quality, codec, hardware acceleration,
  audio bitrate, spawn distance, approach rate, trail size, motion blur,
  parallax, background dots, hit effects, intro, a custom background
  image/video/gif with a brightness control, render effects (dynamic
  camera, beat pulse, miss particles, note spawn pop, reverse), edge blur
  (DoF), and a picture-in-picture overlay video
- **HUD elements**: show or hide title, progress, combo, grade, accuracy,
  score, points, misses, notes, health, speed, timing bar, live stats, the
  playfield border, and other HUD items
- **Optional resources**: `.rhs` skin, `.txt` colorset, game directory,
  watch folder, ghost race replay, and a post-render webhook URL
- **Colors**: built-in presets, user-saved presets, and the colorsets
  auto-discovered from the Rhythia install (shown as "Rhythia: …" — a few,
  like the game's default, are bundled with the app so no install is needed)

Settings persist across sessions.

## CLI Usage

When `main.py` receives arguments, it runs without opening the GUI:

```bash
./.venv/bin/python main.py replay.rhr [map.rhm|map.sspm] -o out.mp4
```

If the map is omitted, the program looks for it:

- in the replay's directory
- in `exports/` inside `--game-dir`, when provided
- failing that, it downloads the map from rhythia.com by the replay's online
  id (cached in `~/.cache/rhr2mp4/maps`; disable with `--no-download`)

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
- `--clip START-END` or `--clip auto[:SECONDS]` (auto-picked highlight)
- `--intro`
- `--hide title,progress,combo,border,...|all`
- `--no-border` (borderless playfield, same as `--hide border`)
- `--hide-assets 1,2|all` (remove the skin's decorative background images;
  `--list-assets` shows each one's number and position)
- `--timing-bar` (osu!-style hit-error bar)
- `--stats-overlay` (live UR/mean/timing-histogram card)
- `--reverse` (plays the replay and audio backwards)
- `--dynamic-camera` / `--beat-pulse [PCT]` / `--miss-particles` /
  `--note-anim` (render effects)
- `--ghost-replay other.rhr` (ghost race overlay; `--no-ghost-panel` hides
  the versus stats)
- `--edge-blur PCT` (cinematic depth-of-field edge blur; mp4/webm)
- `--pip webcam.mp4` / `--pip-corner` / `--pip-scale PCT` (picture-in-picture)
- `--montage a.rhr b.rhr c.rhr --transition wipeleft` (highlight reel; the
  best moment of each replay, auto-detected maps, joined with transitions;
  `--clip auto:SECONDS` sets each moment's length)
- `--preset my-look.rhrp` (apply a preset bundle exported from the GUI;
  explicit flags still win)
- `--webhook URL` (POST the finished video to a Discord-style webhook)
- `--move ELEM=DX,DY` — move a HUD element by percent of the canvas
  (repeatable; elements: title, combo, left_panel, right_panel, health,
  timing; e.g. `--move health=0,5 --move title=0,80`)
- `--bg-image PATH` / `--bg-video PATH` (videos and gifs loop) /
  `--bg-brightness PCT` (100 = untouched, lower darkens, up to 200 brightens)
- `--no-download`
- `--convert OUT.rhm` (convert an `.sspm` map to `.rhm` and exit)
- `--workers N`

Use `--help` for the full argument list.

## Tests

```bash
./.venv/bin/python -m unittest discover tests
```

The suite covers the `.rhr`/`.rhm`/`.sspm` parsers (including old format
versions), map lookup, hit matching, and the highlight finder, all on
synthetic files.

## Parser Validation

```bash
./.venv/bin/python scripts/validate.py replay.rhr map.rhm  # or map.sspm
```

This script checks whether the parsers read both files consistently, including
map IDs, note counts, accuracy, and duration.

## Project Structure

- `main.py`: entry point; launches the GUI without arguments and the CLI with
  arguments
- `rhr2mp4/cli.py`: headless command-line interface
- `rhr2mp4/gui/`: PyQt5 application and stylesheet
- `rhr2mp4/formats/`: parsers for `.rhr`, `.rhm`, `.sspm`, `.rhs`, map lookup,
  and the rhythia.com map downloader
- `rhr2mp4/sim/`: hit registration, timeline generation, and mod handling
- `rhr2mp4/render/`: frame composition, intro rendering, audio, and video
  pipeline
- `scripts/validate.py`: quick parser sanity checks
- `tests/`: unit tests (`python -m unittest discover tests`)

## Rendering Pipeline

The replay is parsed, converted into a visual timeline, and rendered in
parallel segments. Each worker draws frames and encodes its own segment with
`ffmpeg`; at the end, the segments are concatenated and the map's audio is
muxed into the final output.

When `--hw auto` is enabled, the project probes available encoders and tries
NVENC, VAAPI, or QSV before falling back to CPU encoding.

## License

MIT — see [LICENSE](LICENSE).
