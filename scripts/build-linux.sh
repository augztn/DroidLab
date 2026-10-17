#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
APP_NAME="Payload-Dumper"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-x86_64}"
BUILD_DIR="${ROOT}/build"
APPDIR="${ROOT}/AppDir"
DIST_DIR="${ROOT}/dist"

command -v "$PYTHON" >/dev/null || { echo "python3 is required" >&2; exit 1; }
"$PYTHON" -c 'import tkinter' || {
  echo "Python Tkinter is missing. Install python3-tk (Ubuntu/Debian), python3-tkinter (Fedora), or tk (Arch)." >&2
  exit 1
}

rm -rf "$APPDIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"

install -m 0644 src/app.py "$APPDIR/usr/bin/app.py"
install -m 0644 src/payload_dumper.py "$APPDIR/usr/bin/payload_dumper.py"
cat > "$APPDIR/usr/bin/payload-dumper" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/app.py" "$@"
LAUNCH
chmod 0755 "$APPDIR/usr/bin/payload-dumper"

cat > "$APPDIR/usr/share/applications/payload-dumper.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Payload Dumper
Comment=Extract Android OTA payload partitions
Exec=payload-dumper
Icon=payload-dumper
Categories=Utility;System;
Terminal=false
DESKTOP

cat > "$APPDIR/usr/share/icons/hicolor/scalable/apps/payload-dumper.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect x="8" y="8" width="112" height="112" rx="24" fill="#172554"/>
  <path d="M32 34h64v12H32zm0 24h64v12H32zm0 24h42v12H32z" fill="#93c5fd"/>
  <path d="M82 78l18 10-18 10z" fill="#34d399"/>
</svg>
SVG

cat > "$APPDIR/AppRun" <<'RUN'
#!/usr/bin/env bash
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/payload-dumper" "$@"
RUN
chmod 0755 "$APPDIR/AppRun"
cp "$APPDIR/usr/share/applications/payload-dumper.desktop" "$APPDIR/payload-dumper.desktop"
cp "$APPDIR/usr/share/icons/hicolor/scalable/apps/payload-dumper.svg" "$APPDIR/payload-dumper.svg"
printf '0.1.0\n' > "$APPDIR/VERSION"
printf 'x86_64\n' > "$APPDIR/ARCH"

if command -v appimagetool >/dev/null; then
  ARCH="$ARCH" appimagetool "$APPDIR" "$DIST_DIR/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
  echo "Created $DIST_DIR/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
else
  echo "AppDir created at $APPDIR."
  echo "Install appimagetool to create an AppImage, or run scripts/run-portable.sh for local development."
  exit 2
fi
