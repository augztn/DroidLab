"""Adapter for payload-dumper backend.

Uses the maintained Python payload-dumper package when installed. Kept separate
from the GUI so extraction can be tested and replaced independently.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable


Progress = Callable[[str], None]


class BackendError(RuntimeError):
    pass


def _find_cli() -> list[str] | None:
    executable = shutil.which("payload-dumper")
    if executable:
        return [executable]
    return None


def inspect_source(source: str, output_dir: str) -> list[dict]:
    """Return partition metadata if the installed CLI supports JSON listing."""
    cli = _find_cli()
    if cli:
        for args in (["--list", "--json", source], ["--input", source, "--list-json"]):
            proc = subprocess.run(cli + args, text=True, capture_output=True, check=False)
            if proc.returncode == 0:
                import json
                try:
                    data = json.loads(proc.stdout)
                    items = data.get("partitions", data if isinstance(data, list) else [])
                    return [
                        {"name": str(item.get("name", item)), "size": item.get("size_bytes"),
                         "hash": item.get("hash", "")}
                        for item in items
                    ]
                except (ValueError, AttributeError, TypeError):
                    pass
    # Inspection is delegated to the official backend. Don't guess partition
    # names from the ZIP central directory or claim the payload was parsed.
    return []


def extract(source: str, output_dir: str, selected: list[str] | None = None,
            progress: Progress | None = None) -> list[Path]:
    """Run payload-dumper CLI and return newly produced image paths."""
    cli = _find_cli()
    if not cli:
        raise BackendError(
            "The payload-dumper backend is not installed. Build/package the backend "
            "with the app; see docs/LINUX_APPIMAGE_PLAN.md."
        )
    source_path = Path(source).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    if not source_path.is_file():
        raise BackendError(f"Input file does not exist: {source_path}")
    if source_path.suffix.lower() not in {".bin", ".zip"}:
        raise BackendError("Choose a payload .bin file or OTA .zip file.")
    out.mkdir(parents=True, exist_ok=True)

    before = {p.resolve() for p in out.rglob("*") if p.is_file()}
    args = cli + ["-o", str(out), str(source_path)]
    if selected:
        args.extend(["--partitions", ",".join(selected)])
    if progress:
        progress("Running payload-dumper backend…")
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            if progress:
                progress(line.rstrip())
        code = proc.wait()
    except OSError as exc:
        raise BackendError(f"Could not start backend: {exc}") from exc
    if code != 0:
        raise BackendError(f"payload-dumper exited with status {code}. Check the log for details.")
    outputs = sorted(p for p in out.rglob("*") if p.is_file() and p.resolve() not in before)
    if not outputs:
        # Some CLIs overwrite files or report an alternate output folder.
        outputs = sorted(p for p in out.rglob("*.img") if p.is_file())
    return outputs


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
