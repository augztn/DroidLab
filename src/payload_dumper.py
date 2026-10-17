"""Adapter for the documented payload-dumper-go command-line backend."""
from __future__ import annotations
import hashlib, os, shutil, subprocess, sys
from pathlib import Path
from typing import Callable
Progress = Callable[[str], None]
class BackendError(RuntimeError):
    """Raised when the extraction backend cannot complete an operation."""
def _bundle_root() -> Path:
    """Resolve the bundle root for development, PyInstaller, and AppImage runs."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent
def _find_cli() -> list[str] | None:
    root = _bundle_root()
    candidates = [root / "opt" / "payload-dumper" / "bin" / "payload-dumper-go",
                  root / "bin" / "payload-dumper-go"]
    for item in candidates:
        if item.is_file() and os.access(item, os.X_OK): return [str(item)]
    found = shutil.which("payload-dumper-go")
    return [found] if found else None
def _run(args: list[str], progress: Progress | None = None) -> subprocess.CompletedProcess[str]:
    cli=_find_cli()
    if not cli: raise BackendError("payload-dumper-go was not found. Install it or use the bundled AppImage.")
    try:
        proc=subprocess.Popen(cli+args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",bufsize=1)
        assert proc.stdout is not None; lines=[]
        for line in proc.stdout:
            line=line.rstrip(); lines.append(line)
            if progress: progress(line)
        code=proc.wait()
    except OSError as exc: raise BackendError(f"Could not start payload-dumper-go: {exc}") from exc
    return subprocess.CompletedProcess(cli+args,code,"\n".join(lines),"")
def inspect_source(source: str, output_dir: str = "", progress: Progress | None = None) -> list[dict]:
    path=Path(source).expanduser().resolve()
    if not path.is_file(): raise BackendError(f"Input file does not exist: {path}")
    result=_run(["-l","-m",str(path)],progress=progress)
    if result.returncode: raise BackendError(result.stdout.strip() or "Could not inspect the OTA payload.")
    parts=[]
    for line in result.stdout.splitlines():
        name,sep,size=line.partition(":")
        if not sep or not name.strip(): continue
        try: size_bytes=int(size.strip())*1024
        except ValueError: continue
        parts.append({"name":name.strip(),"size_bytes":size_bytes,"hash":""})
    return parts
def extract(source: str, output_dir: str, selected: list[str] | None = None,
            progress: Progress | None = None, cancel_event=None) -> list[Path]:
    src=Path(source).expanduser().resolve(); out=Path(output_dir).expanduser().resolve()
    if not src.is_file(): raise BackendError(f"Input file does not exist: {src}")
    if src.suffix.lower() not in {".bin",".zip"}: raise BackendError("Choose a payload .bin file or OTA .zip file.")
    cli=_find_cli()
    if not cli: raise BackendError("payload-dumper-go was not found. Install it or use the bundled AppImage.")
    out.mkdir(parents=True,exist_ok=True)
    before={p.resolve() for p in out.rglob("*") if p.is_file()}
    args=["-o",str(out)]
    if selected: args.extend(["-p",",".join(selected)])
    args.append(str(src))
    if progress: progress("Starting payload-dumper-go; backend hash checks are reported in the log.")
    try:
        proc=subprocess.Popen(cli+args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",bufsize=1)
    except OSError as exc: raise BackendError(f"Could not start payload-dumper-go: {exc}") from exc
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
                raise BackendError("Extraction cancelled by user.")
            if progress: progress(line.rstrip())
        code=proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        raise
    if code: raise BackendError(f"payload-dumper-go exited with status {code}. Check the log; some delta OTA operation types are unsupported.")
    outputs=sorted(p for p in out.rglob("*") if p.is_file() and p.resolve() not in before)
    if not outputs: outputs=sorted(p for p in out.rglob("*.img") if p.is_file())
    return outputs
def sha256_file(path: str, chunk_size: int = 1024*1024) -> str:
    """Compute a SHA-256 fingerprint; this alone is not OTA integrity verification."""
    digest=hashlib.sha256()
    with open(path,"rb") as stream:
        while chunk:=stream.read(chunk_size): digest.update(chunk)
    return digest.hexdigest()
