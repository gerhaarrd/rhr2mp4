# PyInstaller spec shared by the Linux (AppImage) and Windows builds.
# Build from anywhere:  pyinstaller packaging/rhr2mp4.spec
import os
import sys

# SPECPATH is provided by PyInstaller: the directory containing this file.
PROJECT_DIR = os.path.dirname(SPECPATH)  # noqa: F821
ASSETS = os.path.join(PROJECT_DIR, "rhr2mp4", "assets")

a = Analysis(
    [os.path.join(PROJECT_DIR, "main.py")],
    pathex=[PROJECT_DIR],
    datas=[
        (ASSETS, os.path.join("rhr2mp4", "assets")),
    ],
    hiddenimports=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="rhr2mp4",
    debug=False,
    strip=False,
    upx=False,
    # windowed app; the CLI still works (Qt is only imported without args)
    console=False,
    icon=os.path.join(ASSETS, "rhr2mp4.ico") if sys.platform == "win32" else None,
)

targets = [exe]

if sys.platform == "win32":
    # Windowed exes can't print to the console, so ship a second entry
    # point for CLI use (rhr2mp4-cli.exe replay.rhr -o out.mp4 ...).
    targets.append(EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name="rhr2mp4-cli",
        debug=False,
        strip=False,
        upx=False,
        console=True,
        icon=os.path.join(ASSETS, "rhr2mp4.ico"),
    ))

coll = COLLECT(
    *targets,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="rhr2mp4",
)
