# Payload Dumper for Linux

A portable Linux desktop application for inspecting and extracting partitions from Android OTA packages.

## Goals

- GUI for selecting local `payload.bin` or OTA `.zip` files.
- Browse partitions, choose one or more, and select an output directory.
- Show extraction progress, status, errors, and cancellation.
- Optional SHA-256 verification of extracted images.
- Support remote OTA URLs when the HTTP server supports byte-range requests.
- Ship as a portable AppImage for x86_64 Linux on Ubuntu, Fedora, and Arch Linux.

## Packaging target

The primary release artifact is an AppImage. It should bundle the GUI, payload extraction backend, and non-system runtime libraries. It should not require Python, Rust, or a compiler on the user's machine. The Linux host still needs a compatible FUSE/AppImage runtime; a documented extraction fallback will be provided for systems without FUSE.

## Reference projects

- [payload-dumper-gui](https://github.com/rhythmcache/payload-dumper-gui) — C++/Dear ImGui desktop implementation and Rust `payload_dumper` backend. Its existing desktop front end is Windows-specific, so the Linux UI requires a Linux platform layer and backend integration.
- [Payload-Dumper-Android](https://github.com/rajmani7584/Payload-Dumper-Android) — reference for progress, cancellation, ZIP handling, verification, diagnostics, and extraction workflow.

These projects are references, not bundled code. Any code copied or adapted must follow its source project's license and attribution terms.

## Planned layout

```
.
├── app/                  # Linux desktop UI and application state
├── backend/              # Payload extraction backend integration
├── packaging/
│   ├── AppDir/           # AppImage filesystem layout and desktop entry
│   └── appimage/
├── scripts/
│   ├── build-linux.sh
│   └── run-portable.sh
├── tests/
└── .github/workflows/    # CI and AppImage release builds
```

## Development status

The Linux GUI and AppImage pipeline are being established. A release should not be considered ready until local `.bin` and `.zip` extraction, progress/cancellation, SHA-256 verification, and AppImage launch tests pass on clean Ubuntu, Fedora, and Arch environments.
