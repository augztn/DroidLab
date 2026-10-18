# DroidLab

DroidLab is a desktop Android firmware toolkit for inspecting and extracting OTA `payload.bin` files and OTA ZIP packages, guarded Fastboot flashing, ADB diagnostics/sideloading, and Android app management. Linux builds are distributed as AppImages; Windows builds are distributed as portable ZIP bundles.

## Current implementation

- Tkinter desktop interface with source and output pickers.
- Inspect OTA metadata and show partition names/sizes with multi-select controls.
- Extract all partitions or selected partitions.
- Display backend output and provide cancellation by terminating the backend process.
- Optional SHA-256 fingerprints for output files. These fingerprints are not a substitute for the OTA backend's own integrity checks.
- Fastboot & Flashing tab: drag/drop `.img` files, filename-based partition suggestions, Fastboot device detection, command preview, and explicit confirmation before flashing.
- ADB & Sideload tab: detect ADB device states; reboot normally, to bootloader, or recovery; view live `adb logcat`; save logcat to a text file; generate a bugreport ZIP; run trusted `adb shell` diagnostics such as `dumpsys battery`; and sideload OTA ZIPs while the device is in recovery's “Apply update from ADB” mode.
- ADB & Sideload tab: discover authorized and recovery-mode devices, reboot, view/filter/pause live `adb logcat`, save logs, create and verify bugreport ZIPs, run shell diagnostics, and sideload compatible ZIP packages.
- App Manager tab: scan installed packages, inspect community risk reasons, filter by risk, disable/uninstall for Android user 0, enable/restore, and import/export selections. Destructive actions require confirmation; rescue files store package names only, not APKs or user data.
- Windows bundles include Google's ADB/Fastboot when the platform-tools build is available. The app prefers the bundled tools and falls back to `PATH`; Windows subprocesses are launched without console-window flashes. Linux builds use installed ADB/Fastboot from `PATH`.
- Community safety profiles for Android/AOSP/Google, Samsung, Realme/Oppo, and Xiaomi are bundled offline. They are guidance, not guarantees, and unknown packages remain unknown. The data source and MIT license are listed in `resources/safety-db/NOTICE.md`.
- The app requires explicit confirmation before rebooting, flashing, sideloading, generating a bugreport, or changing packages. Bugreports can contain sensitive device data; removing packages can break device features.
- AppImage build script packages the GUI, the `payload-dumper-go` backend, and the offline package profiles.

## Windows build

Portable Windows `.exe` bundles are built by GitHub Actions for x64 (64-bit Intel/AMD) and ARM64. 32-bit Windows is currently unsupported because the upstream payload extraction backend does not provide a Windows 386 release and its dependencies do not compile for that target. Open the [Windows Actions](https://github.com/nightx24/DroidLab/actions/workflows/windows.yml) page, select a successful build, and download `droidlab-windows-x64` or `droidlab-windows-arm64`. Extract the ZIP and run `DroidLab.exe`.

Each bundle includes `DroidLab.exe`, the matching `payload-dumper-go.exe` backend, and Google's `adb.exe`/`fastboot.exe` when compatible platform-tools binaries are available for that build. DroidLab checks its bundled `bin` folder first, then falls back to tools on `PATH`. If a bundle does not contain ADB/Fastboot, install compatible Android SDK Platform-Tools and add them to `PATH`. ARM64 requires a native Windows ARM64 runner and a matching upstream backend asset; that build may fail if either dependency is unavailable. These builds still need testing on real Windows devices before being considered stable.

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
