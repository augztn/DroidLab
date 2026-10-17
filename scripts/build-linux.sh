#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PYTHON="${PYTHON:-python3}"; APP_NAME="FlashLab"; VERSION="${VERSION:-0.1.0}"; ARCH="${ARCH:-x86_64}"; BACKEND_VERSION="${BACKEND_VERSION:-2.1.0}"
APPDIR="$ROOT/AppDir"; DIST_DIR="$ROOT/dist"; WORK="$ROOT/build/appimage-work"
command -v "$PYTHON" >/dev/null || { echo "python3 is required" >&2; exit 1; }
"$PYTHON" -c "import tkinter" || { echo "Tkinter is required in the build environment" >&2; exit 1; }
"$PYTHON" -m PyInstaller --version >/dev/null 2>&1 || { echo "Install PyInstaller: python3 -m pip install pyinstaller" >&2; exit 1; }
command -v wget >/dev/null || { echo "wget is required" >&2; exit 1; }; command -v tar >/dev/null || { echo "tar is required" >&2; exit 1; }
rm -rf "$APPDIR" "$WORK"; mkdir -p "$WORK" "$DIST_DIR" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps" "$APPDIR/opt/payload-dumper/src" "$APPDIR/opt/payload-dumper/bin" "$APPDIR/usr/share/licenses/payload-dumper-go"
# Freeze the GUI and adapter as one Python/Tk application bundle.
# Bundle GIO introspection modules when PyGObject is available on Linux hosts.
PYI_GIO_ARGS=()
if "$PYTHON" -c "import gi; gi.require_version('Gio', '2.0'); from gi.repository import Gio" >/dev/null 2>&1; then
  PYI_GIO_ARGS+=(--hidden-import=gi --hidden-import=gi.repository.Gio)
fi
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --windowed --name flashlab-gui --distpath "$WORK/pyinstaller-dist" --workpath "$WORK/pyinstaller-build" --specpath "$WORK" --paths src --hidden-import=payload_dumper --hidden-import=tkinter.ttk "${PYI_GIO_ARGS[@]}" --collect-all tkinter --collect-all tkinterdnd2 src/app.py
install -m 0755 "$WORK/pyinstaller-dist/payload-dumper-gui" "$APPDIR/usr/bin/payload-dumper-gui-bin"
BACKEND_ARCH="linux_amd64"; if [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then BACKEND_ARCH="linux_arm64"; fi
BACKEND_ARCHIVE="payload-dumper-go_${BACKEND_VERSION}_${BACKEND_ARCH}.tar.gz"
wget -q --show-progress "https://github.com/ssut/payload-dumper-go/releases/download/${BACKEND_VERSION}/${BACKEND_ARCHIVE}" -O "$WORK/$BACKEND_ARCHIVE"
mkdir -p "$WORK/backend"; tar -xzf "$WORK/$BACKEND_ARCHIVE" -C "$WORK/backend"
BACKEND_PATH="$(find "$WORK/backend" -type f -name payload-dumper-go -print -quit)"
[[ -n "$BACKEND_PATH" ]] || { echo "payload-dumper-go not found in release archive" >&2; exit 1; }
install -m 0755 "$BACKEND_PATH" "$APPDIR/opt/payload-dumper/bin/payload-dumper-go"
XZ_PATH="$(command -v xz || true)"; [[ -n "$XZ_PATH" ]] || { echo "xz is required" >&2; exit 1; }
install -m 0755 "$XZ_PATH" "$APPDIR/opt/payload-dumper/bin/xz"
# Do not bundle glibc or the dynamic loader: doing so can override the host
# system C library and break /usr/bin/env, bash, and other system tools.
# AppImage runs on the host's glibc; only bundle xz itself.
mkdir -p "$APPDIR/opt/payload-dumper/lib"
if command -v ldd >/dev/null; then
  ldd "$XZ_PATH" | awk '/=> \/|^[[:space:]]*\// {for (i=1;i<=NF;i++) if ($i ~ /^\//) {print $i; break}}' | sort -u | while read -r dep; do
    [[ -f "$dep" ]] || continue
    case "$(basename "$dep")" in
      libc.so*|ld-linux*.so*|ld-*.so*) continue ;;
    esac
    cp -L "$dep" "$APPDIR/opt/payload-dumper/lib/$(basename "$dep")"
  done
fi
cat > "$APPDIR/usr/bin/payload-dumper-gui" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
export PATH="$HERE/opt/payload-dumper/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/opt/payload-dumper/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/usr/bin/payload-dumper-gui-bin" "$@"
LAUNCH
chmod 0755 "$APPDIR/usr/bin/payload-dumper-gui" "$APPDIR/usr/bin/payload-dumper-gui-bin"
cat > "$APPDIR/AppRun" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
export PATH="$HERE/opt/payload-dumper/bin:$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/opt/payload-dumper/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/usr/bin/payload-dumper-gui" "$@"
RUN
chmod 0755 "$APPDIR/AppRun"
cat > "$APPDIR/usr/share/applications/flashlab.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=FlashLab
Comment=Android firmware extraction and Fastboot flashing
Exec=payload-dumper-gui
Icon=payload-dumper
Categories=Utility;System;
Terminal=false
DESKTOP
cat > "$APPDIR/usr/share/icons/hicolor/scalable/apps/flashlab.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect x="8" y="8" width="112" height="112" rx="24" fill="#172554"/>
  <path d="M32 34h64v12H32zm0 24h64v12H32zm0 24h42v12H32z" fill="#93c5fd"/>
  <path d="M82 78l18 10-18 10z" fill="#34d399"/>
</svg>
SVG
cp "$APPDIR/usr/share/applications/payload-dumper.desktop" "$APPDIR/payload-dumper.desktop"
cp "$APPDIR/usr/share/icons/hicolor/scalable/apps/payload-dumper.svg" "$APPDIR/payload-dumper.svg"
printf "%s\n" "$VERSION" > "$APPDIR/VERSION"; printf "%s\n" "$ARCH" > "$APPDIR/ARCH"
wget -q "https://raw.githubusercontent.com/ssut/payload-dumper-go/main/LICENSE" -O "$APPDIR/usr/share/licenses/payload-dumper-go/LICENSE"
if ! command -v appimagetool >/dev/null; then echo "appimagetool not found; AppDir prepared at $APPDIR" >&2; exit 2; fi
ARCH="$ARCH" appimagetool "$APPDIR" "$DIST_DIR/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
echo "Created $DIST_DIR/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
