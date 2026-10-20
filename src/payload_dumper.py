"""Adapter for the documented payload-dumper-go command-line backend."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
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
    try:
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            if progress:
                progress(line)
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
    out.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in out.rglob("*") if path.is_file()}
    args = ["-o", str(out)]
    if selected:
        args.extend(["-p", ",".join(selected)])
    args.append(str(src))
    if progress:
        progress("Starting payload-dumper-go; backend hash checks are reported in the log.")
    result = _run_process(cli, args, progress, cancel_event, "Extraction")
    if result.returncode:
        raise BackendError(
            f"payload-dumper-go exited with status {result.returncode}. "
            "Check the log; some delta OTA operation types are unsupported."
        )
    outputs = sorted(path for path in out.rglob("*") if path.is_file() and path.resolve() not in before)
    if not outputs:
        outputs = sorted(path for path in out.rglob("*.img") if path.is_file())
    return outputs


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute a SHA-256 fingerprint; this alone is not OTA integrity verification."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
