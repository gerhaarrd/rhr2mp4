#!/usr/bin/env bash
# Builds the Windows package (rhr2mp4-windows-x86_64.zip) from Linux using
# Wine: installs Windows Python + deps into a dedicated prefix (once),
# runs the Windows PyInstaller over the shared spec and bundles ffmpeg.exe
# so the zip is fully self-contained.
#
# Requires: wine (>= 8), curl. Artifacts land in packaging/dist-win/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(dirname "$HERE")"
CACHE="${RHR2MP4_WINEBUILD:-$HOME/.cache/rhr2mp4-winebuild}"
export WINEPREFIX="$CACHE/prefix" WINEDEBUG=-all
PYTHON_VERSION=3.12.10
WINPY='C:\Python312\python.exe'

mkdir -p "$CACHE"

# 1. Windows Python inside the wine prefix (installed once)
if ! wine "$WINPY" --version >/dev/null 2>&1; then
    if [ ! -f "$CACHE/python-installer.exe" ]; then
        curl -fsSL -o "$CACHE/python-installer.exe" \
            "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe"
    fi
    wine "$CACHE/python-installer.exe" /quiet InstallAllUsers=0 \
        TargetDir='C:\Python312' Include_launcher=0 Include_test=0 \
        AssociateFiles=0 Shortcuts=0
fi

# 2. Windows wheels
wine "$WINPY" -m pip install --quiet --no-warn-script-location \
    pyinstaller PyQt5 Pillow numpy

# 3. PyInstaller (Windows) over the shared spec
cd "$PROJECT"
wine "$WINPY" -m PyInstaller --noconfirm \
    --distpath packaging/dist-win --workpath packaging/build-win \
    packaging/rhr2mp4.spec

# 4. Bundle ffmpeg.exe (gyan.dev release essentials) next to the exe
if [ ! -f "$CACHE/ffmpeg-win.zip" ]; then
    curl -fsSL -o "$CACHE/ffmpeg-win.zip" \
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
fi
python3 - "$CACHE/ffmpeg-win.zip" "$PROJECT/packaging/dist-win/rhr2mp4/ffmpeg.exe" <<'EOF'
import shutil, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    name = next(n for n in z.namelist() if n.endswith("/bin/ffmpeg.exe"))
    with z.open(name) as src, open(sys.argv[2], "wb") as dst:
        shutil.copyfileobj(src, dst)
EOF

# 5. Zip it up
cd "$PROJECT/packaging/dist-win"
rm -f rhr2mp4-windows-x86_64.zip
python3 -c "import shutil; shutil.make_archive('rhr2mp4-windows-x86_64', 'zip', '.', 'rhr2mp4')"
echo "==> $PROJECT/packaging/dist-win/rhr2mp4-windows-x86_64.zip"
