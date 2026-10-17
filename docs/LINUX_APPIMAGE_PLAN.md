# Linux GUI and AppImage

## Architecture

- GUI: Python Tkinter, kept separate from extraction implementation.
- Backend: [`ssut/payload-dumper-go`](https://github.com/ssut/payload-dumper-go), Apache-2.0, invoked through its documented CLI. The adapter uses `-l -m` for partition listing and `-o` / `-p` for extraction.
- Packaging: PyInstaller freezes the GUI's Python/Tk runtime. AppDir includes the Go backend, the `xz` executable and the backend license. `appimagetool` produces the distributable artifact.
- Initial target: x86_64 Linux. ARM64 is an optional explicit build architecture, not yet a tested release target.

## Screens

1. Choose source `.bin`/`.zip` and output directory.
2. Inspect partition metadata and select one or more partitions.
3. Extract selected partitions or extract all when none are selected.
4. View backend log output and generated SHA-256 fingerprints. The fingerprint toggle does not claim a hash verification; the backend performs its own source/output checks.

## Build requirements

Install Python 3, Tkinter, PyInstaller, `xz`, `wget`, `tar`, `file`, and `appimagetool` on the build machine, then run `VERSION=x.y.z bash scripts/build-linux.sh`. CI also runs compile checks and unit tests.

## Limitations / release gates

- The backend currently does not support `PUFFDIFF`, `ZUCCHINI`, and `LZ4DIFF_*` operations used by some delta OTA partitions.
- Some incremental OTA packages require extracted base images. The GUI does not yet expose a base-image directory, so such cases are not a supported GUI workflow yet.
- The backend uses `xz`; the packager includes the executable, but native shared-library dependencies and Tcl/Tk packaging must still be verified with `ldd` and clean-container launch tests before calling the AppImage fully portable.
- Cancellation currently terminates the backend process. Partial output files from cancellation/errors may need manual cleanup; they must not be flashed.
- Test real payload `.bin`, OTA ZIP, partition selection, hash checks, cancellation, missing FUSE fallback, and AppImage launch on clean Ubuntu, Fedora, and Arch environments before a production release.
