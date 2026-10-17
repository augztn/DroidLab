"""Adapter for the documented payload-dumper-go command-line backend."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

Progress = Callable[[str], None]


class BackendError(RuntimeError):
    """Raised when the extraction backend cannot complete an operation."""


def _find_cli() -> list[str] | None:
    """Prefer a bundled backend, then a user-installed payload-dumper-go."""
    roots = [Path(__file__).resolve().parent.parent / "bin" / "payload-dumper-go",
             Path(__file__).resolve().parent.parent / "opt" / "payload-dumper" / "bin" / "payload-dumper-go"]
    for bundled in roots:
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return [str(bundled)]
    executable = shutil.which("payload-dumper-go")
    return [executable] if executable else None


def _run(args: list[str], progress: Progress | None = None) -> subprocess.CompletedProcess[str]:
    cli = _find_cli()
    if not cli:
        raise BackendError("payload-dumper-go was not found. Install it or use an AppImage build that bundles it.")
    try:
        proc = subprocess.Popen(cli + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        assert proc.stdout is not None
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            if progress:
                progress(line)
        code = proc.wait()
    except OSError as exc:
        raise BackendError(f"Could not start payload-dumper-go: {exc}") from exc
    return subprocess.CompletedProcess(cli + args, code, "\n".join(lines), "")


def inspect_source(source: str, output_dir: str = "", progress: Progress | None = None) -> list[dict]:
    """Use documented -l -m output (partition name and size in KiB)."""
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise BackendError(f"Input file does not exist: {path}")
    result = _run(["-l", "-m", str(path)], progress=progress)
    if result.returncode:
        raise BackendError(result.stdout.strip() or "Could not inspect the OTA payload.")
    parts: list[dict] = []
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


def extract(source: str, output_dir: str, selected: list[str] | None = None,
            progress: Progress | None = None, cancel_event=None) -> list[Path]:
    """Extract all or selected partitions and return output image paths."""
    source_path = Path(source).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    if not source_path.is_file():
        raise BackendError(f"Input file does not exist: {source_path}")
    if source_path.suffix.lower() not in {".bin", ".zip"}:
        raise BackendError("Choose a payload .bin file or OTA .zip file.")
    cli = _find_cli()
    if not cli:
        raise BackendError("payload-dumper-go was not found. Install it or use an AppImage build that bundles it.")
    out.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in out.rglob("*") if p.is_file()}
    args = ["-o", str(out)]
    if selected:
        args.extend(["-p", ",".join(selected)])
    args.append(str(source_path))
    if progress:
        progress("Starting payload-dumper-go. The backend verifies OTA hashes when available.")
    try:
        proc = subprocess.Popen(cli + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as exc:
        raise BackendError(f"Could not start payload-dumper-go: {exc}") from exc
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise BackendError("Extraction cancelled by user.")
            if progress:
                progress(line.rstrip())
        code = proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        raise
    if code:
        raise BackendError(f"payload-dumper-go exited with status {code}. Check the activity log. Some delta OTA operation types are unsupported.")
    outputs = sorted(p for p in out.rglob("*") if p.is_file() and p.resolve() not in before)
    if not outputs:
        outputs = sorted(p for p in out.rglob("*.img") if p.is_file())
    return outputs


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute a SHA-256 fingerprint, which is not itself source verification."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
