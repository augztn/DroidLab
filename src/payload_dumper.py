"""Adapter for the documented payload-dumper-go command-line backend."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

Progress = Callable[[str], None]


class BackendError(RuntimeError):
    """Raised when the extraction backend cannot complete an operation."""


def _bundle_root() -> Path:
    """Resolve the bundle root for development, PyInstaller, and AppImage runs."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        # AppImage: AppDir/usr/bin/droidlab-gui-bin; backend is under AppDir/opt.
        if executable.parent.name == "bin" and executable.parent.parent.name == "usr":
            return executable.parent.parent.parent
        return executable.parent
    return Path(__file__).resolve().parent.parent


def _find_cli() -> list[str] | None:
    root = _bundle_root()
    candidates = [
        root / "opt" / "payload-dumper" / "bin" / "payload-dumper-go",
        root / "opt" / "payload-dumper" / "bin" / "payload-dumper-go.exe",
        root / "bin" / "payload-dumper-go",
        root / "bin" / "payload-dumper-go.exe",
        root / "payload-dumper-go",
        root / "payload-dumper-go.exe",
    ]
    for item in candidates:
        if item.is_file() and (os.name == "nt" or os.access(item, os.X_OK)):
            return [str(item)]
    for name in ("payload-dumper-go", "payload-dumper-go.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def _run_process(
    cli: list[str],
    args: list[str],
    progress: Progress | None = None,
    cancel_event=None,
    operation: str = "Payload operation",
) -> subprocess.CompletedProcess[str]:
    """Stream backend output and share one cancellation path for inspect/extract."""
    command = cli + args
    if cancel_event is not None and cancel_event.is_set():
        raise BackendError(f"{operation} cancelled by user.")
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise BackendError(f"Could not start payload-dumper-go: {exc}") from exc

    assert proc.stdout is not None
    finished = threading.Event()
    cancellation_requested = threading.Event()

    def watch_cancel():
        """Terminate a quiet backend even while the reader is waiting for output."""
        while not finished.wait(0.1):
            if cancel_event is None or not cancel_event.is_set():
                continue
            cancellation_requested.set()
            if proc.poll() is not None:
                return
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except OSError:
                # It may have exited between poll() and terminate().
                pass
            return

    watcher = None
    if cancel_event is not None:
        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()

    lines = []
    retained_chars = 0
    max_retained_chars = 2 * 1024 * 1024
    dropped_lines = 0
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if progress:
                progress(line)
            # Continue draining every line, but retain only bounded diagnostics.
            if retained_chars + len(line) + 1 <= max_retained_chars:
                lines.append(line)
                retained_chars += len(line) + 1
            else:
                dropped_lines += 1
        code = proc.wait()
    except BaseException:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except OSError:
                pass
        raise
    finally:
        finished.set()
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except OSError:
                pass
        if watcher is not None:
            watcher.join(timeout=0.2)

    if cancellation_requested.is_set() or (cancel_event is not None and cancel_event.is_set()):
        raise BackendError(f"{operation} cancelled by user.")
    if dropped_lines:
        lines.append(f"[DroidLab: {dropped_lines} additional output lines omitted from retained diagnostics]")
    return subprocess.CompletedProcess(command, code, "\n".join(lines), "")


def _run(
    args: list[str],
    progress: Progress | None = None,
    cancel_event=None,
) -> subprocess.CompletedProcess[str]:
    cli = _find_cli()
    if not cli:
        raise BackendError("payload-dumper-go was not found. Install it or use the bundled AppImage.")
    return _run_process(cli, args, progress, cancel_event, "Inspection")


def inspect_source(
    source: str,
    output_dir: str = "",
    progress: Progress | None = None,
    cancel_event=None,
) -> list[dict]:
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise BackendError(f"Input file does not exist: {path}")
    result = _run(["-l", "-m", str(path)], progress=progress, cancel_event=cancel_event)
    if result.returncode:
        raise BackendError(result.stdout.strip() or "Could not inspect the OTA payload.")
    parts = []
    for line in result.stdout.splitlines():
        name, sep, size = line.partition(":")
        if not sep or not name.strip():
            continue
        try:
            size_bytes = int(size.strip()) * 1024
        except ValueError:
            continue
        parts.append({"name": name.strip(), "size_bytes": size_bytes, "hash": ""})
    return parts


def extract(
    source: str,
    output_dir: str,
    selected: list[str] | None = None,
    progress: Progress | None = None,
    cancel_event=None,
) -> list[Path]:
    src = Path(source).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    if not src.is_file():
        raise BackendError(f"Input file does not exist: {src}")
    if src.suffix.lower() not in {".bin", ".zip"}:
        raise BackendError("Choose a payload .bin file or OTA .zip file.")
    cli = _find_cli()
    if not cli:
        raise BackendError("payload-dumper-go was not found. Install it or use the bundled AppImage.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    # A unique sibling staging directory keeps backend output isolated while
    # remaining on the same filesystem as the destination for atomic linking.
    stage = Path(tempfile.mkdtemp(prefix=".droidlab-extract-", dir=str(out.parent)))
    published: list[Path] = []
    try:
        args = ["-o", str(stage)]
        if selected:
            args.extend(["-p", ",".join(selected)])
        args.append(str(src))
        if progress:
            progress("Starting payload-dumper-go in an isolated staging directory.")
        result = _run_process(cli, args, progress, cancel_event, "Extraction")
        if result.returncode:
            raise BackendError(
                f"payload-dumper-go exited with status {result.returncode}. "
                "Check the log; some delta OTA operation types are unsupported."
            )
        if cancel_event is not None and cancel_event.is_set():
            raise BackendError("Extraction cancelled by user.")

        artifacts = sorted(
            path for path in stage.rglob("*.img")
            if path.is_file() and not path.is_symlink() and path.stat().st_size > 0
        )
        if not artifacts:
            raise BackendError("Extraction completed without producing any non-empty .img artifacts.")

        if selected:
            actual = {path.stem.casefold() for path in artifacts}
            missing = sorted({name.casefold() for name in selected} - actual)
            if missing:
                raise BackendError(
                    "Extraction did not produce the requested partition image(s): "
                    + ", ".join(missing)
                )

        output_root = out.resolve()
        for artifact in artifacts:
            relative = artifact.relative_to(stage)
            destination = out / relative
            # Do not allow a backend-created symlink or path resolution to publish
            # outside the chosen output root.
            destination_parent = destination.parent.resolve()
            try:
                destination_parent.relative_to(output_root)
            except ValueError as exc:
                raise BackendError("An extracted artifact resolves outside the output directory.") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination_parent = destination.parent.resolve()
            try:
                destination_parent.relative_to(output_root)
            except ValueError as exc:
                raise BackendError("An output path resolves outside the output directory.") from exc
            if destination.exists() or destination.is_symlink():
                raise BackendError(
                    f"Output already exists and will not be overwritten: {destination}"
                )
            try:
                # Hard-link publication is atomic and fails if a destination
                # appeared concurrently; stage is created on the same filesystem.
                os.link(artifact, destination)
            except FileExistsError as exc:
                raise BackendError(
                    f"Output already exists and will not be overwritten: {destination}"
                ) from exc
            except OSError as exc:
                raise BackendError(f"Could not publish extracted artifact {destination}: {exc}") from exc
            published.append(destination)
            artifact.unlink()

        if cancel_event is not None and cancel_event.is_set():
            raise BackendError("Extraction cancelled before publication completed.")
        if progress:
            progress(f"Published {len(published)} verified non-empty image artifact(s).")
        return published
    except Exception:
        # Publication is a multi-file operation, not a filesystem-wide atomic
        # transaction. Roll back files created by this operation on failure.
        for destination in reversed(published):
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        try:
            shutil.rmtree(stage)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise BackendError(f"Extraction staging cleanup failed for {stage}: {exc}") from exc


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute a SHA-256 fingerprint; this alone is not OTA integrity verification."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
