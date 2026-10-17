# DroidLab for Linux

DroidLab is a Linux desktop Android firmware toolkit for inspecting and extracting OTA `payload.bin` files and OTA ZIP packages, with a guarded Fastboot image-flashing workflow. Targets x86_64 AppImage builds for Ubuntu, Fedora, and Arch Linux.

## Current implementation

- Tkinter desktop interface with source and output pickers.
- Inspect OTA metadata and show partition names/sizes with multi-select controls.
- Extract all partitions or selected partitions.
- Display backend output and provide cancellation by terminating the backend process.
- Optional SHA-256 fingerprints for output files. These fingerprints are not a substitute for the OTA backend's own integrity checks.
- Fastboot & Flashing tab: drag/drop `.img` files, filename-based partition suggestions, Fastboot device detection, command preview, and explicit confirmation before flashing\n- Flashing requires Android platform-tools (`fastboot`) installed on the host and available on `PATH`; it is not bundled.\n- AppImage build script packages the GUI plus the `payload-dumper-go` backend.

## Build locally

Build dependencies on Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-tk python3-pip xz-utils wget file
python3 -m pip install --user pyinstaller
```

Then install `appimagetool` and run:

```bash
VERSION=0.1.0 bash scripts/build-linux.sh
```

The build downloads the `payload-dumper-go` release binary and embeds its Apache-2.0 license notice in the AppDir. The AppImage build is currently x86_64 by default. The build script can create an AppDir if `appimagetool` is missing, but it will return a non-zero status rather than claim an AppImage was created.

For development, run `bash scripts/run-portable.sh`. The local development mode requires Python 3 and Tkinter plus `payload-dumper-go` on `PATH`.

## Backend

Backend project: [ssut/payload-dumper-go](https://github.com/ssut/payload-dumper-go), Apache License 2.0. It supports raw `payload.bin` and OTA ZIP inputs, listing partitions (`-l`), machine-readable listing (`-m`), selected partitions (`-p`), and output directories (`-o`).

Known limitations inherited from the backend include unsupported `PUFFDIFF`, `ZUCCHINI`, and `LZ4DIFF_*` delta operations in affected partitions. Incremental/delta packages may require base images, and `xz` must be available. Read the backend's documentation before relying on extracted images for device flashing.

## Status and validation

This is an active implementation, not yet a claimed production release. CI checks Python compilation and unit tests. A version-tag build is responsible for making the AppImage and attaching a checksum, but the produced artifact still needs smoke testing on clean Ubuntu, Fedora, and Arch machines and with real full/incremental OTA samples before being considered release-ready.

## Reference projects

- [rhythmcache/droidlab-gui](https://github.com/rhythmcache/droidlab-gui)
- [rajmani7584/Payload-Dumper-Android](https://github.com/rajmani7584/Payload-Dumper-Android)
