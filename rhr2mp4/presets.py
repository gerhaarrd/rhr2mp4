"""Shareable preset bundles (.rhrp): every render setting -- plus the skin
and colorset files themselves, when selected -- packed into a single zip so
one person's exact look can be sent to someone else and imported in one
click (GUI) or used directly from the CLI (--preset).

Bundle layout:
    settings.json   required; {"format": 1, "settings": {...GUI dict...},
                    "resolved": {width/height/fps/quality/codec/hw/...}}
    skin.rhs        optional; the .rhs the settings were built with
    colorset.txt    optional; the .txt colorset
"""

from __future__ import annotations

import json
import os
import zipfile

BUNDLE_EXT = ".rhrp"
BUNDLE_FORMAT = 1


def _import_dir() -> str:
    """Where imported bundle assets (skin/colorset) are unpacked, so they
    survive after the .rhrp file itself is deleted."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "rhr2mp4", "imported_presets")


def export_bundle(path: str, settings: dict, resolved: dict | None = None,
                  skin_path: str | None = None, colorset_path: str | None = None) -> None:
    """Writes a .rhrp bundle. `settings` is the app's own settings dict (the
    GUI preset shape); `resolved` carries CLI-friendly concrete values
    (width, height, fps, quality, codec, ...)."""
    doc = {"format": BUNDLE_FORMAT, "settings": settings, "resolved": resolved or {}}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("settings.json", json.dumps(doc, indent=2))
        if skin_path and os.path.isfile(skin_path):
            zf.write(skin_path, "skin.rhs")
        if colorset_path and os.path.isfile(colorset_path):
            zf.write(colorset_path, "colorset.txt")


def load_bundle(path: str) -> tuple[dict, dict, str | None, str | None]:
    """Reads a .rhrp bundle. Returns (settings, resolved, skin_path,
    colorset_path); the skin/colorset are extracted to a persistent app
    folder named after the bundle, or None when the bundle ships none."""
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "settings.json" not in names:
            raise ValueError(f"{path}: not a preset bundle (missing settings.json)")
        doc = json.loads(zf.read("settings.json"))
        if not isinstance(doc, dict) or "settings" not in doc:
            raise ValueError(f"{path}: malformed settings.json")

        skin_path = colorset_path = None
        stem = os.path.splitext(os.path.basename(path))[0] or "preset"
        target = os.path.join(_import_dir(), stem)
        for entry, out_name in (("skin.rhs", "skin.rhs"), ("colorset.txt", "colorset.txt")):
            if entry in names:
                os.makedirs(target, exist_ok=True)
                out = os.path.join(target, out_name)
                with open(out, "wb") as f:
                    f.write(zf.read(entry))
                if entry == "skin.rhs":
                    skin_path = out
                else:
                    colorset_path = out

    return (dict(doc.get("settings") or {}), dict(doc.get("resolved") or {}),
            skin_path, colorset_path)
