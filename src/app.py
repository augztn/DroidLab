#!/usr/bin/env python3
"""Payload Dumper desktop UI (Tkinter)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from payload_dumper import BackendError, extract, sha256_file


class PayloadDumperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Payload Dumper")
        self.geometry("1040x720")
        self.minsize(780, 560)
        self.configure(bg="#111827")
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.source = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / "Payload-Dumper-Output"))
        self.verify = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Ready")
        self._build()
        self.after(100, self._poll_events)

    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#111827")
        style.configure("Panel.TFrame", background="#1f2937")
        style.configure("TLabel", background="#111827", foreground="#e5e7eb", font=("Sans", 10))
        style.configure("Title.TLabel", font=("Sans", 22, "bold"), foreground="#f9fafb")
        style.configure("Muted.TLabel", foreground="#9ca3af")
        style.configure("TButton", padding=(12, 7))
        style.configure("Treeview", background="#111827", fieldbackground="#111827",
                        foreground="#e5e7eb", rowheight=28)
        style.configure("Treeview.Heading", background="#374151", foreground="#f9fafb")

        root = ttk.Frame(self, padding=22)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 18))
        ttk.Label(header, text="Payload Dumper", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Android OTA partition extractor · Linux desktop",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 0))

        source_panel = ttk.Frame(root, style="Panel.TFrame", padding=14)
        source_panel.pack(fill="x", pady=(0, 12))
        ttk.Label(source_panel, text="SOURCE FILE", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 9, "bold")).pack(anchor="w")
        source_row = ttk.Frame(source_panel, style="Panel.TFrame")
        source_row.pack(fill="x", pady=(8, 0))
        ttk.Entry(source_row, textvariable=self.source).pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(source_row, text="Browse…", command=self._browse_source).pack(side="left", padx=(8, 0))

        output_panel = ttk.Frame(root, style="Panel.TFrame", padding=14)
        output_panel.pack(fill="x", pady=(0, 12))
        ttk.Label(output_panel, text="OUTPUT DIRECTORY", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 9, "bold")).pack(anchor="w")
        out_row = ttk.Frame(output_panel, style="Panel.TFrame")
        out_row.pack(fill="x", pady=(8, 0))
        ttk.Entry(out_row, textvariable=self.output).pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(out_row, text="Choose…", command=self._browse_output).pack(side="left", padx=(8, 0))

        options = ttk.Frame(root)
        options.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(options, text="Calculate SHA-256 fingerprints for extracted images",
                        variable=self.verify).pack(side="left")
        self.start_button = ttk.Button(options, text="Extract payload", command=self._start)
        self.start_button.pack(side="right")
        self.cancel_button = ttk.Button(options, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=(0, 8))

        result_panel = ttk.Frame(root, style="Panel.TFrame", padding=12)
        result_panel.pack(fill="both", expand=True)
        ttk.Label(result_panel, text="ACTIVITY LOG", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 9, "bold")).pack(anchor="w")
        self.log = tk.Text(result_panel, bg="#0b1220", fg="#d1d5db", insertbackground="white",
                           relief="flat", wrap="word", font=("Monospace", 10), height=12)
        self.log.pack(fill="both", expand=True, pady=(8, 8))
        self.log.configure(state="disabled")
        self.progress = ttk.Progressbar(result_panel, mode="indeterminate")
        self.progress.pack(fill="x")
        ttk.Label(root, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

        self._append("Select a payload.bin or OTA .zip file and an output directory.")
        self._append("The extraction backend must be bundled or installed for extraction to work.")

    def _browse_source(self) -> None:
        path = filedialog.askopenfilename(title="Choose Android OTA package",
                                          filetypes=[("Android OTA", "*.bin *.zip"),
                                                     ("All files", "*")])
        if path:
            self.source.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose output directory",
                                       initialdir=self.output.get() or str(Path.home()))
        if path:
            self.output.set(path)

    def _append(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        source = self.source.get().strip()
        output = self.output.get().strip()
        if not source:
            messagebox.showwarning("Input required", "Choose a payload .bin or OTA .zip file.")
            return
        if not output:
            messagebox.showwarning("Output required", "Choose an output directory.")
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.start(12)
        self.status.set("Extracting…")
        self._append(f"Input: {source}")
        self._append(f"Output: {output}")
        self.worker = threading.Thread(target=self._run_extract,
                                       args=(source, output, self.verify.get()), daemon=True)
        self.worker.start()

    def _run_extract(self, source: str, output: str, verify: bool) -> None:
        try:
            outputs = extract(source, output, progress=lambda line: self.events.put(("log", line)))
            if not outputs:
                self.events.put(("log", "Backend finished but no new output files were detected."))
            for path in outputs:
                self.events.put(("log", f"Created: {path}"))
                if verify:
                    digest = sha256_file(str(path))
                    self.events.put(("log", f"SHA-256 (fingerprint): {digest}  {path.name}"))
            self.events.put(("done", f"Finished. Output files detected: {len(outputs)}"))
        except BackendError as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:
            self.events.put(("error", f"Unexpected error: {exc}"))

    def _cancel(self) -> None:
        # The current CLI adapter cannot guarantee safe mid-operation cancellation.
        # Do not kill arbitrary child processes; report limitation clearly.
        self._append("Cancellation requested. Current backend adapter does not yet support safe process cancellation.")
        self.status.set("Cancellation is not available for the current backend.")
        self.cancel_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append(str(payload))
                elif kind == "done":
                    self._finish(str(payload))
                    messagebox.showinfo("Extraction finished", str(payload))
                elif kind == "error":
                    self._finish("Extraction failed")
                    messagebox.showerror("Extraction failed", str(payload))
                    self._append("ERROR: " + str(payload))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _finish(self, status: str) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status.set(status)


if __name__ == "__main__":
    PayloadDumperApp().mainloop()
