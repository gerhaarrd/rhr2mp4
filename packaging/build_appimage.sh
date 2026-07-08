#!/usr/bin/env bash
# Builds rhr2mp4-x86_64.AppImage: PyInstaller onedir -> AppDir -> appimagetool.
# Run from anywhere; artifacts land in packaging/dist/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(dirname "$HERE")"
DIST="$HERE/dist"
APPDIR="$DIST/AppDir"
PYTHON="${PYTHON:-$PROJECT/.venv/bin/python}"

# 1. PyInstaller onedir build (reuses the shared spec)
"$PYTHON" -m PyInstaller --noconfirm \
    --distpath "$DIST" --workpath "$HERE/build" "$HERE/rhr2mp4.spec"

# 2. Assemble the AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "$DIST/rhr2mp4" "$APPDIR/usr/lib/rhr2mp4"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/rhr2mp4/rhr2mp4" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/rhr2mp4.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=rhr2mp4
Comment=Rhythia Replay to MP4 converter
Exec=rhr2mp4
Icon=rhr2mp4
Categories=AudioVideo;Video;
Terminal=false
EOF

cp "$PROJECT/rhr2mp4/assets/rhythialogo.png" "$APPDIR/rhr2mp4.png"

# 3. appimagetool (downloaded once, cached in packaging/)
TOOL="$HERE/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi

# --appimage-extract-and-run keeps it working without FUSE (containers/CI)
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$DIST/rhr2mp4-x86_64.AppImage"

echo "==> $DIST/rhr2mp4-x86_64.AppImage"
