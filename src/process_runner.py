"""Shared bounded subprocess streaming and termination primitives."""
from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections.abc import Callable, Sequence


class ProcessCancelledError(RuntimeError):
    """Raised when a running child process is cancelled by its caller."""


def terminate_process(proc: subprocess.Popen, grace_seconds: float = 1.0) -> None:
    """Terminate a child with bounded waits; never wait indefinitely."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=max(0.0, grace_seconds))
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=max(0.0, grace_seconds))
    except subprocess.TimeoutExpired:
        pass


def run_streaming(
    command: Sequence[str],
    *,
    timeout: float | None = None,
    cancel_event=None,
    on_line: Callable[[str], None] | None = None,
    max_output_chars: int = 2 * 1024 * 1024,
    max_queue_lines: int = 1000,
    popen_kwargs: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drain child output without unbounded queues or retained diagnostics."""
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError("Process cancelled before launch.")
    proc = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **(popen_kwargs or {}),
    )
    assert proc.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue(maxsize=max(1, max_queue_lines))
    drops = {"count": 0}
    drops_lock = threading.Lock()

    def reader() -> None:
        try:
            for line in proc.stdout:
                try:
                    lines.put_nowait(line)
                except queue.Full:
                    with drops_lock:
                        drops["count"] += 1
        finally:
            while True:
                try:
                    lines.put_nowait(None)
                    return
                except queue.Full:
                    try:
                        lines.get_nowait()
                        with drops_lock:
                            drops["count"] += 1
                    except queue.Empty:
                        continue

    thread = threading.Thread(target=reader, name="DroidLab-ProcessReader", daemon=True)
    thread.start()
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    retained: list[str] = []
    retained_chars = 0
    omitted = 0
    stream_closed = False
    try:
        while not stream_closed:
            if cancel_event is not None and cancel_event.is_set():
                terminate_process(proc)
                raise ProcessCancelledError("Process cancelled by user.")
            if deadline is not None and time.monotonic() >= deadline and proc.poll() is None:
                terminate_process(proc)
                raise subprocess.TimeoutExpired(list(command), timeout)
            wait = 0.1
            if deadline is not None:
                wait = min(wait, max(0.01, deadline - time.monotonic()))
            try:
                line = lines.get(timeout=wait)
            except queue.Empty:
                continue
            if line is None:
                stream_closed = True
                continue
            if on_line is not None:
                on_line(line)
            if retained_chars + len(line) <= max_output_chars:
                retained.append(line)
                retained_chars += len(line)
            else:
                omitted += 1
        code = proc.wait(timeout=2)
        with drops_lock:
            omitted += drops["count"]
        output = "".join(retained)
        if omitted:
            output += "\n[DroidLab: %s additional output line(s) omitted]\n" % omitted
        return subprocess.CompletedProcess(list(command), code, output, "")
    except BaseException:
        terminate_process(proc)
        raise
    finally:
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass
