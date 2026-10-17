# Linux GUI and AppImage Architecture

## Objective

Build a desktop app for Ubuntu, Fedora, and Arch Linux with an AppImage as the primary portable release. Initial supported architecture: x86_64. ARM64 can be added as a separate CI build after the primary artifact is validated.

## Suggested stack

- **GUI:** C++ with Dear ImGui and SDL2/OpenGL or GLFW/OpenGL. This fits the primary reference project's existing C++/ImGui direction and avoids switching to a web runtime.
- **Backend:** use the reference `payload-dumper-rust` library via its C API, or invoke a stable CLI backend from a worker process. Keep backend integration behind an adapter so the UI does not depend on implementation details.
- **Build:** CMake or Meson for C++; Cargo for the Rust backend; pinned dependency revisions for repeatable builds.
- **Portable packaging:** AppDir + linuxdeploy/appimagetool (or an equivalently maintained toolchain) in CI. Bundle the app and compatible non-system shared libraries. Avoid bundling the host's core graphics stack, glibc, and desktop display server.
- **Release automation:** GitHub Actions builds the x86_64 AppImage, runs smoke tests, records checksums, and attaches the output to a versioned GitHub Release.

## Main screens

1. **Source** — select a local `payload.bin` or OTA `.zip`, optionally enter a remote HTTP(S) URL, and validate the source.
2. **Partitions** — list partition name, size, operation count, and expected hash where available; support search, select all/none, and multi-select.
3. **Extraction** — choose output directory, concurrent job limit, and verification option; display per-partition progress and overall status; allow cancel.
4. **Logs** — show actionable errors and diagnostic logs without exposing sensitive local paths unnecessarily.

## Backend contract

Keep a small adapter interface with functions equivalent to:

- inspect source -> metadata plus a list of partitions
- extract selected partition(s) -> output files and progress callbacks
- cancel current job(s)
- verify output SHA-256 when a valid expected hash is available

Do not claim a checksum passed when the source does not provide a trusted expected digest. If the digest is absent, a generated SHA-256 can be reported as an output fingerprint, not an integrity verification.

Differential OTA extraction must be labeled experimental or unsupported until a source partition can be supplied and the backend explicitly confirms support.

## Linux portability rules

- Use UTF-8 paths and Linux-native file dialogs.
- Use XDG conventions for configuration, cache, and logs.
- Resolve the AppImage's own directory via `APPDIR` and do not assume the current working directory.
- Never extract payload contents as root.
- Handle interrupted extraction without leaving incomplete output files looking complete.
- Keep temporary downloads/caches outside the bundled AppImage, with a configurable location and adequate free-space checks.
- Validate URL scheme and surface HTTP Range support limitations for remote extraction.

## AppImage UX

The final AppImage should run as:

```bash
chmod +x Payload-Dumper-*.AppImage
./Payload-Dumper-*.AppImage
```

If FUSE is unavailable, document the supported extraction fallback (for example, `--appimage-extract`) rather than silently failing. Mention architecture and tested distributions in release notes. AppImage is portable packaging, not a guarantee that every kernel, graphics driver, or desktop environment is identical.

## Release gates

- Unit tests for backend adapter and source type detection.
- Test local payload.bin and OTA zip metadata.
- Test selected and multiple partition extraction.
- Test cancel behavior and partial-file cleanup.
- Test checksum success and mismatch handling.
- Smoke test app launch from a different working directory.
- Build and launch the AppImage on clean Ubuntu, Fedora, and Arch environments.
- Publish SHA-256 checksum and build/source provenance.
