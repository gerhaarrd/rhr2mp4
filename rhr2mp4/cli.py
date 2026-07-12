"""Headless command-line interface: render a replay without opening the GUI.

    python main.py replay.rhr [mapa.rhm|mapa.sspm] -o out.mp4 [options]

The map can be omitted -- it's auto-detected from the replay's folder (and
the game folder's exports/, when --game-dir is given), exactly like the GUI
does (formats/locate.py).
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from .formats import colorsets, locate, maps, online, rhm, rhr, rhs
from .render.video import render_video
from .sim.highlight import find_highlight
from .sim.timeline import DEFAULT_APPROACH_RATE, DEFAULT_SPAWN_DISTANCE

# --hide names -> RuntimeSkin flags (same set the GUI's HUD card exposes).
HUD_HIDE_KEYS = {
    "title": "song_info_enabled",
    "progress": "progress_bar_enabled",
    "combo": "combo_text_enabled",
    "combo-ring": "left_panel_combo_ring_enabled",
    "pauses": "left_panel_pauses_enabled",
    "accuracy": "left_panel_accuracy_enabled",
    "score": "right_panel_score_enabled",
    "points": "right_panel_points_enabled",
    "misses": "right_panel_misses_enabled",
    "notes": "right_panel_notes_enabled",
    "health": "health_bar_enabled",
    "speed": "speed_text_enabled",
}


def _parse_time_ms(text: str) -> float:
    """'mm:ss', 'h:mm:ss' or plain seconds -> milliseconds."""
    seconds = 0.0
    for part in text.strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds * 1000.0


def _parse_clip(text: str):
    """'START-END' with either side optional ('0:10-0:35', '10-', '-35'), or
    'auto[:SECONDS]' to let the highlight finder pick the window."""
    lowered = text.strip().lower()
    if lowered.startswith("auto"):
        duration_s = 20.0
        if ":" in lowered:
            try:
                duration_s = float(lowered.split(":", 1)[1])
            except ValueError:
                raise argparse.ArgumentTypeError("auto clip must be auto or auto:SECONDS")
        if duration_s <= 0:
            raise argparse.ArgumentTypeError("auto clip duration must be > 0")
        return ("auto", duration_s * 1000.0)
    if "-" not in text:
        raise argparse.ArgumentTypeError("clip must be START-END (e.g. 0:10-0:35) or auto[:SECONDS]")
    start_s, end_s = text.split("-", 1)
    start = _parse_time_ms(start_s) if start_s.strip() else None
    end = _parse_time_ms(end_s) if end_s.strip() else None
    if start is not None and end is not None and end <= start:
        raise argparse.ArgumentTypeError("clip end must be after clip start")
    return start, end


def _parse_resolution(text: str) -> tuple[int, int]:
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("resolution must be WIDTHxHEIGHT (e.g. 1920x1080)")


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to legacy codepages (cp1252/cp850) that
    # can't encode "✓"/"·"; degrade gracefully instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="rhr2mp4",
        description="Render a Rhythia replay (.rhr) + map (.rhm or .sspm) to .mp4.",
    )
    parser.add_argument("replay", nargs="?", help="the .rhr replay file")
    parser.add_argument("map", nargs="?", help="the .rhm or .sspm map (omit to auto-detect)")
    parser.add_argument("-o", "--output", help="output path; the extension picks the container "
                                               "(.mp4, .webm or .gif; default: .mp4 alongside the replay)")
    parser.add_argument("--no-download", action="store_true",
                        help="don't fetch missing maps from rhythia.com")
    parser.add_argument("--convert", metavar="OUT.rhm",
                        help="convert the map to .rhm and exit (no render); "
                             "pass the map as the first positional argument")
    parser.add_argument("--resolution", type=_parse_resolution, default=(1920, 1080),
                        metavar="WxH", help="video resolution (default 1920x1080)")
    parser.add_argument("--vertical", action="store_true",
                        help="shortcut for 1080x1920 (9:16 portrait layout)")
    parser.add_argument("--fps", type=int, default=60, choices=(30, 60), help="frame rate")
    parser.add_argument("--quality", choices=("fast", "balanced", "quality"), default="fast")
    parser.add_argument("--codec", choices=("h264", "hevc", "av1"), default="h264")
    parser.add_argument("--hw", choices=("auto", "nvenc", "vaapi", "qsv", "none"), default="auto",
                        help="hardware encoder (default: auto-detect)")
    parser.add_argument("--audio-bitrate", default="192k")
    parser.add_argument("--music-volume", type=float, default=100, metavar="PCT",
                        help="music volume in %% (default 100; 0 mutes the track)")
    parser.add_argument("--hitsound-volume", type=float, default=100, metavar="PCT",
                        help="hit sound volume in %% (default 100; 0 disables hit sounds; "
                             "skins without a hit sound use the app's default one)")
    parser.add_argument("--skin", help="optional .rhs skin")
    parser.add_argument("--colorset", help="a .txt colorset file, or the name of one from the "
                                           "Rhythia install / bundled with the app (see --list-colorsets)")
    parser.add_argument("--list-colorsets", action="store_true",
                        help="list the colorsets auto-discovered from Rhythia and exit")
    parser.add_argument("--game-dir", default="", help="Rhythia install folder (map auto-detect, skin colorsets)")
    parser.add_argument("--spawn-distance", type=float, default=None)
    parser.add_argument("--approach-rate", type=float, default=None)
    parser.add_argument("--bg-image", metavar="PATH", help="custom background image")
    parser.add_argument("--bg-video", metavar="PATH",
                        help="custom background video or animated gif (loops; wins over --bg-image)")
    parser.add_argument("--bg-brightness", type=float, default=None, metavar="PCT",
                        help="custom background brightness (default 40; 100 = untouched, "
                             "lower darkens, up to 200 brightens)")
    parser.add_argument("--bg-dim", type=float, default=None, metavar="PCT",
                        help=argparse.SUPPRESS)  # deprecated: brightness = 100 - dim
    parser.add_argument("--timing-bar", action="store_true",
                        help="show the hit-error (timing) bar at the bottom of the playfield")
    parser.add_argument("--move", action="append", default=[], metavar="ELEM=DX,DY",
                        help="move a HUD element by DX,DY percent of the canvas "
                             "(repeatable; elements: title, combo, left_panel, "
                             "right_panel, health, timing; e.g. --move health=0,5)")
    parser.add_argument("--no-trail", action="store_true", help="hide the cursor trail")
    parser.add_argument("--no-parallax", action="store_true", help="disable camera parallax")
    parser.add_argument("--no-dots", action="store_true", help="hide the drifting background dots")
    parser.add_argument("--no-hit-effects", action="store_true", help="disable the hit burst effect")
    parser.add_argument("--trail-length", type=float, default=100, metavar="PCT",
                        help="cursor trail length in %% (default 100)")
    parser.add_argument("--motion-blur", choices=("off", "filter", "subframe"), default="off",
                        help="off | filter (ffmpeg, free) | subframe (physical, ~4x slower)")
    parser.add_argument("--blur-intensity", type=float, default=50, metavar="PCT",
                        help="motion blur intensity in %% (default 50)")
    parser.add_argument("--clip", type=_parse_clip, default=(None, None), metavar="START-END",
                        help="render only this section, e.g. 0:10-0:35 (either side optional), "
                             "or auto[:SECONDS] to auto-pick the best highlight window")
    parser.add_argument("--intro", action="store_true", help="prepend the 2.5s cover/stats intro card")
    parser.add_argument("--hide", default="", metavar="LIST",
                        help="comma-separated HUD elements to hide, or 'all': "
                             + ", ".join(HUD_HIDE_KEYS))
    parser.add_argument("--workers", type=int, default=None, help="render worker processes")
    args = parser.parse_args(argv)

    if args.list_colorsets:
        for name, colors in sorted(colorsets.discover_game_colorsets(args.game_dir).items()):
            print(f"{name}: " + ",".join("{:02x}{:02x}{:02x}".format(*c) for c in colors))
        return 0

    if args.convert:
        src = args.replay or args.map
        if not src:
            parser.error("--convert needs the map file as the first argument")
        try:
            game_map = maps.load(src)
        except Exception as e:
            parser.error(f"could not read map: {e}")
        rhm.save(game_map, args.convert)
        print(f"✓ {os.path.basename(src)} -> {args.convert} "
              f"({len(game_map.notes):,} notes)")
        return 0

    if not args.replay:
        parser.error("the replay file is required")

    try:
        replay = rhr.load(args.replay)
    except Exception as e:
        parser.error(f"could not read replay: {e}")

    map_path = args.map
    if not map_path:
        search = [os.path.dirname(os.path.abspath(args.replay))]
        if args.game_dir:
            search.append(os.path.join(args.game_dir, "exports"))
        map_path = locate.find_map_for_replay(replay, search, replay_filename=args.replay)
        if map_path:
            print(f"map auto-detected: {os.path.basename(map_path)}")
        elif not args.no_download:
            print("no local map found; fetching from rhythia.com...")
            map_path = online.download_map_for_replay(replay)
            if map_path:
                print(f"map downloaded: {os.path.basename(map_path)}")
        if not map_path:
            parser.error("no matching map (.rhm/.sspm) found next to the replay "
                         "and it couldn't be downloaded; pass the map explicitly")

    try:
        game_map = maps.load(map_path)
    except Exception as e:
        parser.error(f"could not read map: {e}")

    skin = None
    if args.skin:
        skin = rhs.load(args.skin)

    note_colors = None
    if args.colorset:
        if os.path.isfile(args.colorset):
            with open(args.colorset, encoding="utf-8", errors="replace") as f:
                note_colors = rhs.parse_colorset(f.read()) or None
        else:
            note_colors = colorsets.find_colorset_by_name(args.colorset, args.game_dir)
            if note_colors is None:
                parser.error(f"colorset {args.colorset!r} not found (see --list-colorsets)")
    if note_colors is None and skin is not None and not skin.note_colors:
        colorset_ref = str(skin.raw.get("ColorSet") or "")
        if colorset_ref:
            found = rhs.resolve_colorset_path(args.game_dir, colorset_ref)
            if found:
                with open(found, encoding="utf-8", errors="replace") as f:
                    note_colors = rhs.parse_colorset(f.read()) or None
            if note_colors is None:
                note_colors = colorsets.find_colorset_by_name(colorset_ref, args.game_dir)

    spawn_distance = args.spawn_distance
    approach_rate = args.approach_rate
    if spawn_distance is None:
        spawn_distance = skin.spawn_distance if skin is not None and skin.spawn_distance > 0 else DEFAULT_SPAWN_DISTANCE
    if approach_rate is None:
        approach_rate = skin.approach_rate if skin is not None and skin.approach_rate > 0 else DEFAULT_APPROACH_RATE

    width, height = (1080, 1920) if args.vertical else args.resolution

    output = args.output
    if not output:
        output = os.path.join(
            os.path.dirname(os.path.abspath(args.replay)),
            locate.default_output_name(game_map.metadata.song_name or game_map.metadata.title,
                                       replay.username, args.replay),
        )

    hud_overrides = None
    if args.hide.strip():
        names = [n.strip().lower() for n in args.hide.split(",") if n.strip()]
        if "all" in names:
            names = list(HUD_HIDE_KEYS)
        unknown = [n for n in names if n not in HUD_HIDE_KEYS]
        if unknown:
            parser.error(f"unknown --hide element(s): {', '.join(unknown)}")
        hud_overrides = {HUD_HIDE_KEYS[n]: False for n in names}
    if args.timing_bar:
        hud_overrides = dict(hud_overrides or {})
        hud_overrides["hit_error_bar_enabled"] = True

    from .render.frame import MOVABLE_ELEMENTS

    element_offsets: dict[str, tuple[float, float]] = {}
    for spec in args.move:
        try:
            name, deltas = spec.split("=", 1)
            dx_s, dy_s = deltas.split(",", 1)
            name = name.strip().lower()
            if name not in MOVABLE_ELEMENTS:
                raise ValueError
            element_offsets[name] = (float(dx_s) / 100.0, float(dy_s) / 100.0)
        except ValueError:
            parser.error(f"bad --move {spec!r}; expected ELEM=DX,DY with ELEM one of "
                         + ", ".join(MOVABLE_ELEMENTS))

    from .render.video import is_animated_image

    background_image = None
    background_video = args.bg_video
    if args.bg_image:
        if args.bg_image.lower().endswith(".gif") or is_animated_image(args.bg_image):
            # Animated gifs/webps go through the video path so they actually
            # play (and loop) instead of freezing on the first frame.
            background_video = background_video or args.bg_image
        else:
            try:
                with open(args.bg_image, "rb") as f:
                    background_image = f.read()
            except OSError as e:
                parser.error(f"could not read --bg-image: {e}")
    if background_video and not os.path.isfile(background_video):
        parser.error(f"--bg-video not found: {background_video}")

    if args.bg_brightness is not None:
        bg_brightness = args.bg_brightness / 100.0
    elif args.bg_dim is not None:  # deprecated spelling
        bg_brightness = (100.0 - args.bg_dim) / 100.0
    else:
        bg_brightness = 0.4
    bg_brightness = max(0.0, min(2.0, bg_brightness))

    if args.clip[0] == "auto":
        clip_start_ms, clip_end_ms = find_highlight(
            [n.time_ms for n in game_map.notes], replay, duration_ms=args.clip[1])
        print(f"highlight window: {clip_start_ms / 1000:.1f}s - {clip_end_ms / 1000:.1f}s")
    else:
        clip_start_ms, clip_end_ms = args.clip
    motion_blur = args.blur_intensity / 100.0 if args.motion_blur != "off" else 0.0
    motion_blur_mode = args.motion_blur if args.motion_blur != "off" else "filter"

    start_time = time.monotonic()

    def progress(done: int, total: int):
        elapsed = time.monotonic() - start_time
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        pct = 100 * done // total if total else 0
        sys.stdout.write(f"\r  {pct:3d}%  ({done:,}/{total:,} frames)  ETA {_format_eta(eta)} ")
        sys.stdout.flush()

    print(f"rendering {os.path.basename(args.replay)} -> {output}")
    print(f"  {width}x{height}@{args.fps}, {args.codec}/{args.hw}, quality={args.quality}")
    render_video(
        game_map, replay, output,
        width=width, height=height, fps=args.fps, quality=args.quality,
        workers=args.workers,
        spawn_distance=spawn_distance, approach_rate=approach_rate,
        skin=skin,
        parallax_enabled=not args.no_parallax,
        trail_enabled=not args.no_trail,
        note_colors=note_colors,
        video_codec=args.codec, hw_accel=args.hw, audio_bitrate=args.audio_bitrate,
        background_dots_enabled=not args.no_dots,
        trail_scale=args.trail_length / 100.0,
        motion_blur=motion_blur, motion_blur_mode=motion_blur_mode,
        clip_start_ms=clip_start_ms, clip_end_ms=clip_end_ms,
        intro_enabled=args.intro,
        hit_effects_enabled=not args.no_hit_effects,
        music_volume=args.music_volume,
        hit_sound_volume=args.hitsound_volume,
        hud_overrides=hud_overrides,
        background_image=background_image,
        background_video=background_video,
        background_brightness=bg_brightness,
        element_offsets=element_offsets or None,
        progress_cb=progress,
    )
    elapsed = time.monotonic() - start_time
    print(f"\n✓ done in {_format_eta(elapsed)} — {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
