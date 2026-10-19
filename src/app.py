#!/usr/bin/env python3
"""Cross-platform DroidLab desktop UI for Android firmware workflows."""
from __future__ import annotations
import os, queue, threading, tkinter as tk, shutil, re, shlex, sys
import subprocess, webbrowser, logging, platform
from logging.handlers import RotatingFileHandler
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD
from pathlib import Path
from payload_dumper import BackendError, extract, inspect_source, sha256_file

def _windows_process_options():
    """Keep ADB/Fastboot subprocesses from flashing console windows on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


APP_NAME = "DroidLab"  # Canonical product name.

def _log_directory():
    """Keep logs in a clearly visible DroidLab folder inside the user's home directory."""
    return Path.home() / "DroidLab" / "logs"

def _configure_logging():
    directory = _log_directory()
    log_file = directory / "droidlab.log"
    logger = logging.getLogger()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Keep exactly one file handler for this path, even if an earlier
        # startup path or packaged runtime installed duplicate handlers.
        matching = [
            handler for handler in logger.handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")).resolve() == log_file.resolve()
        ]
        marked = [handler for handler in matching if getattr(handler, "_droidlab_handler", False)]
        keep = marked[0] if marked else (matching[0] if matching else None)
        for handler in matching:
            if handler is not keep:
                logger.removeHandler(handler)
                try:
                    handler.close()
                except OSError:
                    pass
        if keep is None:
            keep = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")
            logger.addHandler(keep)
        keep._droidlab_handler = True
        keep.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"))
        logger.setLevel(logging.INFO)
        if not getattr(logger, "_droidlab_started", False):
            logger._droidlab_started = True
            logging.info("==== %s starting ====", APP_NAME)
            logging.info("Platform: %s | Python: %s | frozen: %s", platform.platform(), sys.version.replace("\n", " "), bool(getattr(sys, "frozen", False)))
        return log_file
    except OSError:
        logging.basicConfig(level=logging.INFO)
        logging.exception("Could not initialize file logging at %s", directory)
        return None

def _log_uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("Uncaught application exception", exc_info=(exc_type, exc_value, exc_traceback))

class PayloadDumperApp(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.log_path = _configure_logging()
        sys.excepthook = _log_uncaught_exception
        self.title(APP_NAME)
        # Use a practical Windows-first size; ttk layouts and system fonts scale
        # more predictably than the oversized Linux-oriented defaults.
        self.geometry("1240x820")
        self.minsize(900, 640)
        self.configure(bg="#111827")
        self._ui_font = "Segoe UI" if os.name == "nt" else "Sans"
        self._mono_font = "Consolas" if os.name == "nt" else "Monospace"
        self.events=queue.Queue(); self.worker=None; self.cancel_event=threading.Event()
        menu=tk.Menu(self)
        help_menu=tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Debug Tools / Open Logs", command=self._open_log_folder)
        help_menu.add_command(label="Save Diagnostic Report…", command=self._save_diagnostic_report)
        help_menu.add_separator()
        help_menu.add_command(label="About DroidLab", command=self._show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)
        self.source=tk.StringVar(); self.output=tk.StringVar(value=str(Path.home()/APP_NAME)); self.fingerprint=tk.BooleanVar(value=True); self.status=tk.StringVar(value="Ready")
        self.parts={}; self.part_vars={}; self._build()
        self.report_callback_exception = self._report_callback_exception
        logging.info("UI initialized")
        self.after(100,self._poll_events)
    def _report_callback_exception(self, exc_type, exc_value, exc_traceback):
        logging.error("Unhandled Tkinter callback exception", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            messagebox.showerror("DroidLab error", "An unexpected error occurred. Details were saved to the application log.\n\n" + str(self.log_path or _log_directory()), parent=self)
        except tk.TclError:
            pass

    def _open_log_folder(self):
        directory = _log_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(directory))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(directory)])
            else:
                subprocess.Popen(["xdg-open", str(directory)])
            logging.info("Opened log directory")
        except (OSError, tk.TclError) as exc:
            logging.exception("Could not open log directory")
            messagebox.showerror("Cannot open logs", "Log folder: " + str(directory) + "\n\n" + str(exc), parent=self)

    def _save_diagnostic_report(self):
        path = filedialog.asksaveasfilename(parent=self, title="Save DroidLab diagnostic report", defaultextension=".txt", initialfile="droidlab-diagnostics.txt", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            log_path = _log_directory() / "droidlab.log"
            with open(path, "w", encoding="utf-8") as report:
                report.write("DroidLab diagnostic report\n")
                report.write("Platform: " + platform.platform() + "\nPython: " + sys.version + "\n")
                report.write("Executable: " + sys.executable + "\nFrozen build: " + str(bool(getattr(sys, "frozen", False))) + "\n")
                report.write("Log directory: " + str(_log_directory()) + "\n\n--- Application log (latest file) ---\n")
                report.write(log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else "No log file found.\n")
            logging.info("Diagnostic report exported to %s", path)
            messagebox.showinfo("Diagnostics saved", "Report saved to:\n" + path, parent=self)
        except OSError:
            logging.exception("Could not export diagnostic report")
            messagebox.showerror("Export failed", "Could not write the diagnostic report. Check the destination and permissions.", parent=self)

    def _tool_path(self, name):
        """Prefer bundled platform-tools, then fall back to the user's PATH."""
        candidates = []
        if getattr(sys, "frozen", False):
            root = Path(sys.executable).resolve().parent
            candidates.extend([root / "bin" / name, root / "platform-tools" / name])
        else:
            root = Path(__file__).resolve().parent.parent
            candidates.extend([root / "bin" / name, root / "platform-tools" / name])
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which(name)

    def _show_about(self):
        """Show a responsive, scrollable About dialog."""
        dialog = tk.Toplevel(self)
        dialog.title("About DroidLab")
        dialog.configure(bg="#111827")
        dialog.transient(self)
        dialog.resizable(True, True)

        self.update_idletasks()
        width = min(620, max(440, self.winfo_screenwidth() - 80))
        height = min(680, max(420, self.winfo_screenheight() - 100))
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.minsize(min(420, width), min(360, height))

        # Keep the title and close action fixed; scroll the complete content area.
        outer = ttk.Frame(dialog, padding=(14, 12))
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="About DroidLab", font=("Sans", 14, "bold")).pack(side="left")
        ttk.Button(header, text="Close", command=dialog.destroy).pack(side="right")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg="#111827", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content_frame = ttk.Frame(canvas, padding=(2, 2, 10, 14))
        content_window = canvas.create_window((0, 0), window=content_frame, anchor="nw")

        def update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_content_width(event):
            canvas.itemconfigure(content_window, width=event.width)

        content_frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_content_width)

        hero = tk.Frame(content_frame, bg="#1d4ed8", padx=18, pady=22)
        hero.pack(fill="x")
        tk.Label(hero, text="DL", bg="#1d4ed8", fg="#ffffff",
                 font=("Sans", 18, "bold")).pack(pady=(0, 6))
        tk.Label(hero, text="DroidLab", bg="#1d4ed8", fg="#ffffff",
                 font=("Sans", 25, "bold")).pack()
        tk.Label(hero, text="Android firmware toolkit", bg="#1d4ed8", fg="#dbeafe",
                 font=("Sans", 12)).pack(pady=(5, 0))

        intro = ttk.Frame(content_frame, style="Panel.TFrame", padding=16)
        intro.pack(fill="x", pady=(12, 0))
        ttk.Label(intro, text="A toolkit for Android firmware workflows",
                  background="#1f2937", foreground="#f9fafb",
                  font=("Sans", 13, "bold"), wraplength=500).pack(anchor="w")
        ttk.Label(intro,
                  text="Inspect and extract OTA payloads, review partition images, manage connected devices with ADB, and preview guarded Fastboot operations.",
                  background="#1f2937", foreground="#cbd5e1",
                  font=("Sans", 11), wraplength=500, justify="left").pack(
                      anchor="w", fill="x", pady=(7, 0))

        features = ttk.Frame(content_frame, style="Panel.TFrame", padding=16)
        features.pack(fill="x", pady=(10, 0))
        ttk.Label(features, text="WHAT YOU CAN DO", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 10, "bold")).pack(anchor="w")
        for item in (
            "Extract all or selected partitions from OTA payloads",
            "Generate optional SHA-256 fingerprints",
            "Inspect images and preview Fastboot commands",
            "Reboot, collect logs, run diagnostics, and sideload OTA ZIPs",
        ):
            ttk.Label(features, text="•  " + item, background="#1f2937",
                      foreground="#e5e7eb", font=("Sans", 11),
                      wraplength=500, justify="left").pack(anchor="w", pady=(7, 0))

        tech = ttk.Frame(content_frame, style="Panel.TFrame", padding=16)
        tech.pack(fill="x", pady=(10, 0))
        ttk.Label(tech, text="TOOLS & TECHNOLOGY", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 10, "bold")).pack(anchor="w")
        for label, value in (
            ("Desktop UI", "Python · Tkinter"),
            ("Payload backend", "payload-dumper-go"),
            ("Device tools", "Android SDK Platform-Tools · ADB & Fastboot"),
        ):
            line = ttk.Frame(tech, style="Panel.TFrame")
            line.pack(fill="x", pady=(7, 0))
            ttk.Label(line, text=label, background="#1f2937",
                      foreground="#9ca3af", font=("Sans", 10)).pack(anchor="w")
            ttk.Label(line, text=value, background="#1f2937",
                      foreground="#e5e7eb", font=("Sans", 11),
                      wraplength=500, justify="left").pack(anchor="w", pady=(2, 0))

        details = ttk.Frame(content_frame, style="Panel.TFrame", padding=16)
        details.pack(fill="x", pady=(10, 0))
        ttk.Label(details, text="CREATED BY", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 9, "bold")).pack(anchor="w")
        ttk.Label(details, text="nightx24 (augztn)", background="#1f2937",
                  foreground="#f9fafb", font=("Sans", 15, "bold")).pack(
                      anchor="w", pady=(4, 10))
        links = ttk.Frame(details, style="Panel.TFrame")
        links.pack(fill="x")
        instagram = ttk.Label(links, text="Instagram  ↗", background="#1f2937",
                              foreground="#60a5fa", cursor="hand2",
                              font=("Sans", 11, "underline"))
        instagram.pack(side="left", padx=(0, 18))
        instagram.bind("<Button-1>", lambda _event: webbrowser.open("https://www.instagram.com/augztn/"))
        github = ttk.Label(links, text="GitHub  ↗", background="#1f2937",
                           foreground="#60a5fa", cursor="hand2",
                           font=("Sans", 11, "underline"))
        github.pack(side="left")
        github.bind("<Button-1>", lambda _event: webbrowser.open("https://github.com/nightx24"))

        ttk.Label(content_frame, text="DroidLab · Community firmware utility",
                  style="Muted.TLabel", font=("Sans", 10)).pack(
                      anchor="center", pady=(14, 4))

        # Trackpad and mouse-wheel scrolling: support Linux/X11 wheel events,
        # Windows/macOS delta events, and smooth touchpad deltas.
        def on_mousewheel(event):
            delta = getattr(event, "delta", 0) or 0
            if delta:
                if abs(delta) >= 120:
                    units = int(-delta / 120)
                else:
                    # Linux touchpads often send small deltas instead of 120-step wheel events.
                    units = -1 if delta > 0 else 1
                canvas.yview_scroll(units, "units")
            elif getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            return "break"

        # Bind wheel events to the canvas and every child in the scrollable area,
        # since Tk widgets under the pointer otherwise consume the event.
        def bind_scroll_events(widget):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>",
                             "<Shift-MouseWheel>"):
                widget.bind(sequence, on_mousewheel, add="+")
            for child in widget.winfo_children():
                bind_scroll_events(child)

        bind_scroll_events(content_frame)
        bind_scroll_events(hero)
        canvas.bind("<MouseWheel>", on_mousewheel, add="+")
        canvas.bind("<Button-4>", on_mousewheel, add="+")
        canvas.bind("<Button-5>", on_mousewheel, add="+")
        # Some desktop environments report touchpad gestures as XInput/Touchpad
        # events; keeping focus on the scrollable surface helps keyboard navigation.
        canvas.configure(takefocus=True)
        canvas.bind("<Up>", lambda _e: canvas.yview_scroll(-1, "units"))
        canvas.bind("<Down>", lambda _e: canvas.yview_scroll(1, "units"))
        canvas.bind("<Prior>", lambda _e: canvas.yview_scroll(-1, "pages"))
        canvas.bind("<Next>", lambda _e: canvas.yview_scroll(1, "pages"))
        dialog.grab_set()

    def _build(self):
        s=ttk.Style(self)
        try:s.theme_use("clam")
        except tk.TclError:pass
        font = self._ui_font
        s.configure("TFrame", background="#111827")
        s.configure("Panel.TFrame", background="#1f2937")
        s.configure("TLabel", background="#111827", foreground="#e5e7eb", font=(font, 10 if os.name == "nt" else 13))
        s.configure("Title.TLabel", font=(font, 20, "bold"), foreground="#f9fafb")
        s.configure("Muted.TLabel", foreground="#cbd5e1")
        s.configure("TButton", padding=(9, 6), font=(font, 10 if os.name == "nt" else 12))
        s.configure("TCheckbutton", background="#1f2937", foreground="#f9fafb", font=(font, 11 if os.name == "nt" else 13))
        s.configure("Large.TCheckbutton", background="#1f2937", foreground="#f9fafb", font=(font, 12 if os.name == "nt" else 15), padding=(2, 4))
        s.configure("Large.TButton", padding=(10, 9), font=(font, 11 if os.name == "nt" else 14, "bold"))
        s.configure("TEntry", padding=5, font=(font, 10 if os.name == "nt" else 12))
        s.configure("TCombobox", padding=5, font=(font, 10 if os.name == "nt" else 12))
        s.configure("Treeview", background="#0b1220", fieldbackground="#0b1220", foreground="#f1f5f9", rowheight=30 if os.name == "nt" else 38, font=(font, 10 if os.name == "nt" else 12))
        s.configure("Treeview.Heading", background="#374151", foreground="#ffffff", font=(font, 10 if os.name == "nt" else 12, "bold"), padding=(7, 6))
        s.map("Treeview", background=[("selected", "#1d4ed8")], foreground=[("selected", "#ffffff")])
        notebook=ttk.Notebook(self); notebook.pack(fill="both",expand=True,padx=8,pady=8)
        root=ttk.Frame(notebook,padding=16); notebook.add(root,text="Payload Extractor")
        device_tab=ttk.Frame(notebook,padding=0); notebook.add(device_tab,text="Device Control & Flashing")
        self.device_mode=tk.StringVar(value="adb")
        self.device_workspace=ttk.Frame(device_tab)
        self.device_workspace.pack(fill="both",expand=True)
        self.flash_workspace=ttk.Frame(self.device_workspace,padding=16)
        self.adb_workspace=ttk.Frame(self.device_workspace,padding=16)
        self.flash_workspace.grid(row=0,column=0,sticky="nsew")
        self.adb_workspace.grid(row=0,column=0,sticky="nsew")
        self.device_workspace.columnconfigure(0,weight=1); self.device_workspace.rowconfigure(0,weight=1)
        self._build_flash_tab(self.flash_workspace); self._build_adb_tab(self.adb_workspace)
        self._show_device_workspace("adb")
        h=ttk.Frame(root); h.pack(fill="x",pady=(0,10))
        ttk.Label(h,text=APP_NAME,style="Title.TLabel").pack(anchor="w"); ttk.Label(h,text="Android firmware toolkit · " + ("Windows desktop" if os.name == "nt" else "Linux desktop"),style="Muted.TLabel").pack(anchor="w",pady=(3,0))
        src=ttk.Frame(root,style="Panel.TFrame",padding=10); src.pack(fill="x",pady=(0,7)); ttk.Label(src,text="INPUT FILE PATH — TYPE OR PASTE THE FULL PATH",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); row=ttk.Frame(src,style="Panel.TFrame"); row.pack(fill="x",pady=(6,0)); self.source_entry=ttk.Entry(row,textvariable=self.source); self.source_entry.pack(side="left",fill="x",expand=True,ipady=4); self.source_entry.bind("<Return>",lambda _event:self._apply_manual_source_path()); ttk.Button(row,text="Browse…",command=self._browse_source).pack(side="left",padx=(7,0)); actions=ttk.Frame(src,style="Panel.TFrame"); actions.pack(fill="x",pady=(7,0)); ttk.Button(actions,text="Use typed path",command=self._apply_manual_source_path).pack(side="left"); ttk.Button(actions,text="Inspect partitions",command=self._inspect).pack(side="left",padx=(7,0))
        out=ttk.Frame(root,style="Panel.TFrame",padding=10); out.pack(fill="x",pady=(0,7)); ttk.Label(out,text="OUTPUT DIRECTORY",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); row=ttk.Frame(out,style="Panel.TFrame"); row.pack(fill="x",pady=(5,0)); ttk.Entry(row,textvariable=self.output).pack(side="left",fill="x",expand=True,ipady=4); ttk.Button(row,text="Choose…",command=self._browse_output).pack(side="left",padx=(7,0))
        # Compact mounted-drive controls share the output panel to save vertical space for partitions.
        drive_row=ttk.Frame(out,style="Panel.TFrame"); drive_row.pack(fill="x",pady=(5,0)); ttk.Label(drive_row,text="Mounted drive",background="#1f2937",foreground="#cbd5e1").pack(side="left",padx=(0,8)); self.storage_var=tk.StringVar(); self.storage_combo=ttk.Combobox(drive_row,textvariable=self.storage_var,state="readonly"); self.storage_combo.pack(side="left",fill="x",expand=True); ttk.Button(drive_row,text="Refresh",command=self._refresh_storage_locations).pack(side="left",padx=(7,0)); ttk.Button(drive_row,text="Browse drive",command=self._browse_selected_storage).pack(side="left",padx=(7,0)); ttk.Button(drive_row,text="Use for output",command=self._use_selected_storage).pack(side="left",padx=(7,0));
        middle=ttk.Frame(root); middle.pack(fill="both",expand=True,pady=(0,10)); panel=ttk.Frame(middle,style="Panel.TFrame",padding=10); panel.pack(side="left",fill="both",expand=True,padx=(0,10)); ttk.Label(panel,text="PARTITIONS (no selection = all)",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); actions=ttk.Frame(panel,style="Panel.TFrame"); actions.pack(fill="x",pady=5); ttk.Button(actions,text="Select all",command=lambda:self._select_all(True)).pack(side="left"); ttk.Button(actions,text="Select none",command=lambda:self._select_all(False)).pack(side="left",padx=5)
        self.tree=ttk.Treeview(panel,columns=("size",),show="tree headings",selectmode="none"); self.tree.heading("#0",text="Partition"); self.tree.heading("size",text="Size"); self.tree.column("#0",width=420,minwidth=260,stretch=True); self.tree.column("size",width=150,minwidth=130,anchor="e",stretch=False); self.tree.pack(fill="both",expand=True); self.tree.bind("<Button-1>",self._toggle)
        side=ttk.Frame(middle,style="Panel.TFrame",padding=14,width=300); side.pack(side="right",fill="y"); side.pack_propagate(False); ttk.Label(side,text="OPTIONS",background="#1f2937",foreground="#93c5fd",font=("Sans",16,"bold")).pack(anchor="w",pady=(0,8)); ttk.Separator(side).pack(fill="x",pady=(0,10)); ttk.Checkbutton(side,text="Generate SHA-256 fingerprints",variable=self.fingerprint,style="Large.TCheckbutton").pack(anchor="w",pady=(10,8)); ttk.Label(side,text="Creates a SHA-256 checksum for each extracted partition. This is separate from the backend’s OTA verification.",background="#1f2937",foreground="#cbd5e1",justify="left",wraplength=260,font=(self._ui_font,10 if os.name == "nt" else 12)).pack(anchor="w",pady=(4,16)); self.start_button=ttk.Button(side,text="Extract payload",command=self._start,style="Large.TButton"); self.start_button.pack(fill="x",pady=(8,8),ipady=5); self.cancel_button=ttk.Button(side,text="Cancel",command=self._cancel,state="disabled",style="Large.TButton"); self.cancel_button.pack(fill="x",ipady=5)
        logbox=ttk.Frame(root,style="Panel.TFrame",padding=10); logbox.pack(fill="x",expand=False); ttk.Label(logbox,text="ACTIVITY LOG",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); self.log=tk.Text(logbox,bg="#0b1220",fg="#d1d5db",insertbackground="white",relief="flat",wrap="word",font=(self._mono_font,10),height=4); self.log.pack(fill="x",expand=False,pady=6); self.log.configure(state="disabled"); self.progress=ttk.Progressbar(logbox,mode="indeterminate"); self.progress.pack(fill="x",pady=(2,0)); ttk.Label(root,textvariable=self.status,style="Muted.TLabel").pack(anchor="w",pady=(7,0))
        self._refresh_storage_locations(); self._enable_drop(self); self._enable_drop(src); self._enable_drop(self.source_entry); self._enable_drop(root); self._append("Drag payload.bin or an OTA .zip onto the window, or type/paste its full path and choose Use typed path. Inspect to select individual partitions."); self._append("Backend: payload-dumper-go (Apache-2.0). Some delta OTA operations are unsupported.")
    def _show_device_workspace(self, mode):
        """Show only the controls appropriate for the selected device mode."""
        mode = "fastboot" if mode == "fastboot" else "adb"
        self.device_mode.set(mode)
        if mode == "fastboot":
            self.flash_workspace.tkraise()
        else:
            self.adb_workspace.tkraise()

    def _on_shared_device_selected(self):
        target = getattr(self, "_adb_targets", {}).get(self.adb_device.get())
        if not target:
            self._update_reboot_actions()
            if hasattr(self, "device_info_vars"):
                self._set_device_info({"Brand / Manufacturer": "—", "Device name / Model": "—",
                    "Model code": "—", "Codename": "—", "Active slot": "—",
                    "Bootloader": "—", "Battery": "—"}, "NO DEVICE", "#4b5563")
            return
        serial, state = target
        if state == "fastboot":
            self.fastboot_device.set(serial)
            self._show_device_workspace("fastboot")
        else:
            self._show_device_workspace("adb")
            if hasattr(self, "device_mode_status"):
                adb_state = state.split(":", 1)[1] if state.startswith("adb:") else state
                mode = {"device": "ADB / Android", "recovery": "ADB Recovery", "sideload": "ADB Sideload", "unauthorized": "ADB Unauthorized", "offline": "ADB Offline"}.get(adb_state, "ADB (" + adb_state + ")")
                self.device_mode_status.set("MODE DETECTOR: " + mode + " · Serial: " + serial)
        self._update_reboot_actions()
        if hasattr(self, "device_info_vars"):
            self._refresh_device_info()

    def _build_flash_tab(self, tab):
        """Build the Fastboot workspace with device-first selection and a live console."""
        tab.columnconfigure(0, weight=3, uniform="fastboot_workspace")
        tab.columnconfigure(1, weight=2, uniform="fastboot_workspace")
        tab.rowconfigure(3, weight=1)

        ttk.Label(tab, text="Fastboot & Flashing", font=("Sans", 22, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="Select the connected device first, then choose an image or maintenance action.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # Device selector comes first so every later operation has a clear target.
        device = ttk.Frame(tab, style="Panel.TFrame", padding=10)
        device.grid(row=2, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        device.columnconfigure(0, weight=1)
        info = ttk.Frame(tab, style="Panel.TFrame", padding=10)
        info.grid(row=2, column=1, sticky="nsew", pady=(0, 10))
        info.columnconfigure(0, weight=1)
        ttk.Label(info, text="DEVICE INFO", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.device_info_mode = tk.StringVar(value="NO DEVICE")
        self.device_info_mode_label = tk.Label(info, textvariable=self.device_info_mode,
            bg="#4b5563", fg="#fef3c7", padx=8, pady=3, font=("Sans", 9, "bold"))
        self.device_info_mode_label.grid(row=0, column=1, sticky="e", padx=(4, 0))
        self.device_info_vars = {}
        for row, (key, initial) in enumerate((("Brand / Manufacturer", "—"), ("Device name / Model", "—"),
                ("Model code", "—"), ("Codename", "—"), ("Active slot", "—"),
                ("Bootloader", "—"), ("Battery", "—")), start=1):
            ttk.Label(info, text=key + ":", background="#1f2937", foreground="#cbd5e1").grid(row=row, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=initial)
            self.device_info_vars[key] = var
            ttk.Label(info, textvariable=var, background="#1f2937", foreground="#f8fafc",
                      wraplength=230).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=1)
        ttk.Label(device, text="FASTBOOT DEVICE", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.fastboot_device = tk.StringVar()
        self.device_combo = ttk.Combobox(device, textvariable=self.fastboot_device, state="readonly")
        self.device_combo.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(device, text="Refresh devices", command=self._refresh_fastboot_devices).grid(
            row=1, column=1, padx=(8, 0), pady=(5, 0))

        # Scrollable left workspace keeps every control reachable on short displays.
        left_scroll = ttk.Frame(tab)
        left_scroll.grid(row=3, column=0, sticky="nsew", padx=(0, 10))
        left_scroll.columnconfigure(0, weight=1)
        left_scroll.rowconfigure(0, weight=1)
        controls_canvas = tk.Canvas(left_scroll, bg="#111827", highlightthickness=0, borderwidth=0)
        controls_scrollbar = ttk.Scrollbar(left_scroll, orient="vertical", command=controls_canvas.yview)
        controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        controls_scrollbar.grid(row=0, column=1, sticky="ns")
        controls_canvas.grid(row=0, column=0, sticky="nsew")
        controls = ttk.Frame(controls_canvas)
        controls_window = controls_canvas.create_window((0, 0), window=controls, anchor="nw")
        controls.columnconfigure(0, weight=1)
        def _sync_controls_scrollregion(_event=None):
            controls_canvas.configure(scrollregion=controls_canvas.bbox("all"))
        def _fit_controls_width(event):
            controls_canvas.itemconfigure(controls_window, width=event.width)
        controls.bind("<Configure>", _sync_controls_scrollregion)
        controls_canvas.bind("<Configure>", _fit_controls_width)
        def _controls_mousewheel(event):
            if getattr(event, "delta", 0):
                controls_canvas.yview_scroll(int(-event.delta / 120), "units")
            elif getattr(event, "num", None) == 4:
                controls_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                controls_canvas.yview_scroll(1, "units")
        controls_canvas.bind_all("<MouseWheel>", _controls_mousewheel)
        controls_canvas.bind_all("<Button-4>", _controls_mousewheel)
        controls_canvas.bind_all("<Button-5>", _controls_mousewheel)

        action_row = ttk.Frame(controls)
        action_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        action_row.columnconfigure(0, weight=1, uniform="fastboot_actions")
        action_row.columnconfigure(1, weight=1, uniform="fastboot_actions")
        reboot = ttk.Frame(action_row, style="Panel.TFrame", padding=10)
        reboot.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ttk.Label(reboot, text="REBOOT CONTROLS", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 11, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Button(reboot, text="Reboot system", command=lambda: self._fastboot_quick_action(["reboot"])).pack(fill="x", pady=2)
        ttk.Button(reboot, text="Reboot recovery", command=lambda: self._fastboot_quick_action(["reboot", "recovery"])).pack(fill="x", pady=2)
        ttk.Button(reboot, text="Reboot bootloader", command=lambda: self._fastboot_quick_action(["reboot-bootloader"])).pack(fill="x", pady=2)
        reference = ttk.Frame(action_row, style="Panel.TFrame", padding=10)
        reference.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ttk.Label(reference, text="ERASE & FORMAT", background="#1f2937", foreground="#fb923c",
                  font=("Sans", 11, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Button(reference, text="Erase cache", command=lambda: self._run_fastboot_maintenance("erase", "cache")).pack(fill="x", pady=2)
        ttk.Button(reference, text="Erase userdata", command=lambda: self._run_fastboot_maintenance("erase", "userdata")).pack(fill="x", pady=2)
        ttk.Button(reference, text="Wipe data (fastboot -w)", command=lambda: self._run_fastboot_maintenance("-w")).pack(fill="x", pady=2)
        self.maintenance_command = tk.StringVar(value="Select an operation to preview its command.")
        ttk.Label(reference, textvariable=self.maintenance_command, background="#0b1220", foreground="#f1f5f9",
                  padding=7, justify="left", wraplength=320, font=("Monospace", 9)).pack(fill="x", pady=(5, 0))
        self.maintenance_status = tk.StringVar(value="No maintenance command executed.")
        ttk.Label(reference, textvariable=self.maintenance_status, background="#1f2937", foreground="#cbd5e1",
                  wraplength=320, justify="left", font=("Sans", 9)).pack(anchor="w", pady=(5, 0))

        box = ttk.Frame(controls, style="Panel.TFrame", padding=12)
        box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(box, text="IMAGE FILE", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 10, "bold")).pack(anchor="w")
        image_row = ttk.Frame(box, style="Panel.TFrame")
        image_row.pack(fill="x", pady=(5, 0))
        self.flash_image = tk.StringVar()
        self.flash_entry = ttk.Entry(image_row, textvariable=self.flash_image)
        self.flash_entry.pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(image_row, text="Browse…", command=self._browse_flash_image).pack(side="left", padx=(7, 0))
        self._enable_drop(box)
        self._enable_drop(self.flash_entry)
        self._enable_drop(tab)

        partition = ttk.Frame(controls, style="Panel.TFrame", padding=12)
        partition.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(partition, text="TARGET PARTITION", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 10, "bold")).pack(anchor="w")
        self.flash_partition = tk.StringVar()
        self.partition_combo = ttk.Combobox(
            partition, textvariable=self.flash_partition,
            values=("boot", "vendor", "vendor_boot", "init_boot", "dtbo", "vbmeta", "recovery",
                    "system", "product", "odm", "system_ext", "vbmeta_system", "vbmeta_vendor"),
            state="normal")
        self.partition_combo.pack(fill="x", pady=(5, 0))

        preview = ttk.Frame(controls, style="Panel.TFrame", padding=12)
        preview.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(preview, text="COMMAND PREVIEW", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 10, "bold")).pack(anchor="w")
        self.flash_command = tk.StringVar(value="Select an image, partition, and Fastboot device.")
        ttk.Label(preview, textvariable=self.flash_command, background="#0b1220", foreground="#f1f5f9",
                  padding=9, wraplength=700, justify="left").pack(fill="x", pady=(5, 0))

        ttk.Label(controls,
                  text="FLASHING RISK · An incompatible image or incorrect partition can stop Android from booting. Verify compatibility before flashing.",
                  foreground="#fecaca", background="#2a1d27", wraplength=850, justify="left",
                  font=("Sans", 10), padding=10).grid(row=4, column=0, sticky="ew", pady=(0, 8))
        flash_buttons = ttk.Frame(controls)
        flash_buttons.grid(row=5, column=0, sticky="nw")
        ttk.Button(flash_buttons, text="Preview command", command=self._update_flash_preview).pack(side="left")
        ttk.Button(flash_buttons, text="Flash image…", command=self._flash_image).pack(side="left", padx=(7, 0))

        # Advanced commands live below the flashing controls, not under the console.
        manual = ttk.Frame(controls, style="Panel.TFrame", padding=12)
        manual.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(manual, text="ADVANCED FASTBOOT COMMAND", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 11, "bold")).pack(anchor="w")
        ttk.Label(manual, text="Run host-side commands such as fastboot devices, fastboot getvar all, or fastboot reboot. Paths with spaces are supported.",
                  background="#1f2937", foreground="#cbd5e1", wraplength=800, justify="left").pack(
                      anchor="w", pady=(4, 6))
        self.fastboot_manual_command = tk.StringVar(value="fastboot devices")
        self.fastboot_manual_entry = ttk.Entry(manual, textvariable=self.fastboot_manual_command)
        self.fastboot_manual_entry.pack(fill="x", pady=(0, 6), ipady=4)
        manual_buttons = ttk.Frame(manual, style="Panel.TFrame")
        manual_buttons.pack(fill="x")
        ttk.Button(manual_buttons, text="Run Fastboot command", command=self._run_fastboot_manual).pack(side="left")
        ttk.Button(manual_buttons, text="Clear", command=lambda: self.fastboot_manual_command.set("")).pack(
            side="left", padx=(7, 0))
        self._enable_command_drop(self.fastboot_manual_entry, self.fastboot_manual_command, "fastboot")

        # The console owns the full right workspace height for continuous output visibility.
        right = ttk.Frame(tab, style="Panel.TFrame", padding=10)
        right.grid(row=3, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        console_panel = ttk.Frame(right, style="Panel.TFrame", padding=2)
        console_panel.grid(row=0, column=0, sticky="nsew")
        ttk.Label(console_panel, text="REAL-TIME CONSOLE / ACTIVITY", background="#1f2937",
                  foreground="#93c5fd", font=("Sans", 11, "bold")).pack(anchor="w", pady=(0, 5))
        fast_console_frame = ttk.Frame(console_panel, style="Panel.TFrame")
        fast_console_frame.pack(fill="both", expand=True)
        self.fastboot_console = tk.Text(fast_console_frame, height=12, bg="#0b1220", fg="#d1d5db",
            insertbackground="white", relief="flat", wrap="word", font=(self._mono_font, 9), state="disabled")
        fast_console_scroll = ttk.Scrollbar(fast_console_frame, orient="vertical", command=self.fastboot_console.yview)
        self.fastboot_console.configure(yscrollcommand=fast_console_scroll.set)
        fast_console_scroll.pack(side="right", fill="y")
        self.fastboot_console.pack(side="left", fill="both", expand=True)
        self._console_write(self.fastboot_console, "[DroidLab] Fastboot console ready. Commands and activity will appear here.\n")

        self.flash_image.trace_add("write", lambda *_: self._on_flash_image_changed())
        self.flash_partition.trace_add("write", lambda *_: self._update_flash_preview())
        self.fastboot_device.trace_add("write", lambda *_: self._update_flash_preview())

    def _fastboot_quick_action(self, args):
        """Run a confirmed Fastboot reboot action through the live streaming runner."""
        serial = self.fastboot_device.get().strip()
        binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        if not binary:
            messagebox.showerror("Fastboot not found", "Install Android Platform-Tools or add fastboot to PATH.")
            return
        if not serial or serial not in self.device_combo["values"]:
            messagebox.showwarning("Select Fastboot device", "Refresh devices and select a connected Fastboot device first.")
            return
        command = [binary, "-s", serial] + list(args)
        rendered = " ".join(shlex.quote(part) for part in command)
        if not messagebox.askyesno("Confirm reboot", "Execute this command on " + serial + "?\n\n" + rendered):
            self._console_write(self.fastboot_console, "[DroidLab] Cancelled: " + rendered + "\n")
            return
        self._append("Executing Fastboot action: " + rendered)
        self._console_write(self.fastboot_console, "[DroidLab] $ " + rendered + "\n")
        self.status.set("Running Fastboot command…")
        threading.Thread(target=self._adb_task, args=(command, 90, "fastboot_manual"), daemon=True).start()

    def _build_adb_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)
        ttk.Label(tab, text="ADB & Sideload", font=("Sans", 20, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(tab, text="Reboot, collect logs and diagnostics, run device shell commands, or sideload an OTA ZIP from recovery.", style="Muted.TLabel", wraplength=1100).grid(row=1, column=0, sticky="w", pady=(0, 12))
        device=ttk.Frame(tab,style="Panel.TFrame",padding=10); device.grid(row=2,column=0,sticky="ew",pady=(0,10))
        ttk.Label(device,text="ADB DEVICE",background="#1f2937",foreground="#93c5fd").pack(anchor="w")
        row=ttk.Frame(device,style="Panel.TFrame"); row.pack(fill="x",pady=(5,0))
        self.adb_device=tk.StringVar(); self.adb_device_combo=ttk.Combobox(row,textvariable=self.adb_device,state="readonly"); self.adb_device_combo.pack(side="left",fill="x",expand=True)
        ttk.Button(row,text="Refresh devices",command=self._refresh_adb_devices).pack(side="left",padx=(8,0))
        self.adb_device_status=tk.StringVar(value="Connect device or enter recovery sideload mode, then refresh.")
        self.device_mode_status = tk.StringVar(value="MODE DETECTOR: Waiting for device scan")
        ttk.Label(device,textvariable=self.device_mode_status,background="#1f2937",foreground="#86efac",
                  wraplength=1000,font=("Sans",10,"bold")).pack(anchor="w",pady=(6,0))
        ttk.Label(device,textvariable=self.adb_device_status,background="#1f2937",foreground="#cbd5e1",wraplength=1000).pack(anchor="w",pady=(3,0))
        adb_info = ttk.Frame(tab, style="Panel.TFrame", padding=10)
        adb_info.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        adb_info.columnconfigure(1, weight=1)
        ttk.Label(adb_info, text="DEVICE INFO", background="#1f2937", foreground="#93c5fd",
                  font=("Sans", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.adb_device_info_mode = tk.StringVar(value="NO DEVICE")
        self.adb_device_info_mode_label = tk.Label(adb_info, textvariable=self.adb_device_info_mode,
            bg="#4b5563", fg="#fef3c7", padx=8, pady=3, font=("Sans", 9, "bold"))
        self.adb_device_info_mode_label.grid(row=0, column=2, sticky="e")
        self.adb_device_info_vars = {}
        for row_idx, (key, initial) in enumerate((("Brand / Manufacturer", "—"), ("Device name / Model", "—"),
                ("Model code", "—"), ("Codename", "—"), ("Active slot", "—"),
                ("Bootloader", "—"), ("Battery", "—")), start=1):
            ttk.Label(adb_info, text=key + ":", background="#1f2937", foreground="#cbd5e1").grid(
                row=row_idx, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=initial)
            self.adb_device_info_vars[key] = var
            ttk.Label(adb_info, textvariable=var, background="#1f2937", foreground="#f8fafc",
                      wraplength=300).grid(row=row_idx, column=1, sticky="w", padx=(8, 0), pady=1)
        # Two-column workspace keeps controls visible beside the live console.
        workspace=ttk.Frame(tab); workspace.grid(row=4,column=0,sticky="nsew"); workspace.columnconfigure(0,weight=3,uniform="adb_workspace"); workspace.columnconfigure(1,weight=2,uniform="adb_workspace"); workspace.rowconfigure(0,weight=1)
        lower=ttk.Frame(workspace); lower.grid(row=0,column=0,sticky="nsew",padx=(0,10)); lower.columnconfigure(0,weight=1); lower.rowconfigure(2,weight=1)
        adb_console_panel=ttk.Frame(workspace,style="Panel.TFrame",padding=10); adb_console_panel.grid(row=0,column=1,sticky="nsew"); adb_console_panel.rowconfigure(1,weight=1); adb_console_panel.columnconfigure(0,weight=1)
        ttk.Label(adb_console_panel,text="REAL-TIME CONSOLE / ACTIVITY",background="#1f2937",foreground="#93c5fd",font=("Sans",12,"bold")).grid(row=0,column=0,sticky="w",pady=(0,6))
        self.adb_console=tk.Text(adb_console_panel,bg="#0b1220",fg="#d1d5db",insertbackground="white",relief="flat",wrap="word",font=(self._mono_font,9),state="disabled")
        adb_console_scroll=ttk.Scrollbar(adb_console_panel,orient="vertical",command=self.adb_console.yview); adb_console_scroll.grid(row=1,column=1,sticky="ns"); self.adb_console.configure(yscrollcommand=adb_console_scroll.set); self.adb_console.grid(row=1,column=0,sticky="nsew")
        self._console_write(self.adb_console,"[DroidLab] ADB command output will appear here in real time.\n")
        actions=ttk.Frame(lower); actions.grid(row=0,column=0,sticky="ew",pady=(0,10))
        power=ttk.Frame(actions,style="Panel.TFrame",padding=10); power.pack(side="left",fill="both",expand=True,padx=(0,8))
        ttk.Label(power,text="POWER & REBOOTING",background="#1f2937",foreground="#93c5fd").pack(anchor="w",pady=(0,6))
        row=ttk.Frame(power,style="Panel.TFrame"); row.pack(fill="x")
        self.reboot_button=ttk.Button(row,text="Reboot",command=lambda:self._adb_reboot(""))
        self.reboot_button.pack(side="left",padx=(0,4))
        self.bootloader_button=ttk.Button(row,text="Bootloader",command=lambda:self._adb_reboot("bootloader"))
        self.bootloader_button.pack(side="left",padx=4)
        self.recovery_button=ttk.Button(row,text="Recovery",command=lambda:self._adb_reboot("recovery"))
        self.recovery_button.pack(side="left",padx=4)
        diag=ttk.Frame(actions,style="Panel.TFrame",padding=10); diag.pack(side="left",fill="both",expand=True)
        ttk.Label(diag,text="LOGS & DIAGNOSTICS",background="#1f2937",foreground="#93c5fd").pack(anchor="w",pady=(0,6))
        row=ttk.Frame(diag,style="Panel.TFrame"); row.pack(fill="x")
        ttk.Button(row,text="Live logcat",command=self._start_adb_logcat).pack(side="left",padx=(0,4))
        ttk.Button(row,text="Save logcat…",command=self._save_adb_logcat).pack(side="left",padx=4)
        ttk.Button(row,text="Bugreport ZIP…",command=self._create_adb_bugreport).pack(side="left",padx=4)
        side=ttk.Frame(lower,style="Panel.TFrame",padding=10); side.grid(row=1,column=0,sticky="ew",pady=(0,10))
        ttk.Label(side,text="ADB SIDELOAD — INSTALL ZIP PACKAGE",background="#1f2937",foreground="#93c5fd",font=("Sans",12,"bold")).pack(anchor="w")
        row=ttk.Frame(side,style="Panel.TFrame"); row.pack(fill="x",pady=(5,0))
        self.sideload_file=tk.StringVar(); self.sideload_entry=ttk.Entry(row,textvariable=self.sideload_file); self.sideload_entry.pack(side="left",fill="x",expand=True,ipady=4)
        ttk.Button(row,text="Browse ZIP…",command=self._browse_sideload_zip).pack(side="left",padx=(7,0))
        ttk.Button(row,text="Sideload ZIP…",command=self._run_adb_sideload).pack(side="left",padx=(7,0))
        drop_hint=ttk.Label(side,text="Drop a compatible flashable ZIP here, or use Browse ZIP…",background="#111827",foreground="#93c5fd",padding=8,wraplength=1100); drop_hint.pack(fill="x",pady=(7,0))
        for drop_widget in (side, self.sideload_entry, drop_hint): self._enable_sideload_drop(drop_widget)
        ttk.Label(side,text="Boot into a compatible recovery and choose Apply update from ADB (or its equivalent). Select the device marked [sideload]. Compatible packages may include OTA updates, custom ROMs, Magisk, kernels, or mods. Verify the package and recovery instructions for your exact device; not every ZIP supports sideload.",background="#1f2937",foreground="#cbd5e1",wraplength=1100).pack(anchor="w",pady=(5,0))
        shell=ttk.Frame(lower,style="Panel.TFrame",padding=10); shell.grid(row=2,column=0,sticky="nsew"); shell.columnconfigure(0,weight=1); shell.rowconfigure(2,weight=1)
        ttk.Label(shell,text="ADVANCED DEVICE SHELL",background="#1f2937",foreground="#93c5fd",font=("Sans",12,"bold")).grid(row=0,column=0,sticky="w")
        row=ttk.Frame(shell,style="Panel.TFrame"); row.grid(row=1,column=0,sticky="ew",pady=(5,7))
        self.adb_shell_command=tk.StringVar(value="dumpsys battery")
        self.adb_shell_entry=ttk.Entry(row,textvariable=self.adb_shell_command)
        self.adb_shell_entry.pack(side="left",fill="x",expand=True,ipady=4)
        self.adb_shell_entry.bind("<Return>",lambda _event:self._run_adb_shell())
        ttk.Button(row,text="Run command",command=self._run_adb_shell).pack(side="left",padx=(7,0))
        self._enable_command_drop(self.adb_shell_entry,self.adb_shell_command,"adb")
        ttk.Label(shell,text="Tip: Enter device-shell commands (dumpsys battery) OR host commands (adb devices, adb reboot recovery). Do not type adb commands into the Android shell itself.",
                  background="#1f2937",foreground="#cbd5e1",wraplength=1100,justify="left").grid(row=3,column=0,sticky="w",pady=(5,0))
        self.adb_output=tk.Text(shell,height=7,bg="#0b1220",fg="#d1d5db",insertbackground="white",relief="flat",wrap="word",font=("Monospace",10)); self.adb_output.grid(row=2,column=0,sticky="nsew")
        self.adb_output.insert("end","Select a device, then run dumpsys battery or another trusted shell command.\n"); self.adb_output.configure(state="disabled")
        self.adb_log_process = None
        self.adb_log_window = None
        self.adb_log_text = None
        self.adb_log_filter = tk.StringVar()
        self.adb_log_paused = False
        self.adb_log_lines = []

    def _refresh_adb_devices(self):
        """Refresh one selector with devices visible to either ADB or Fastboot."""
        adb_binary = self._tool_path("adb.exe" if os.name == "nt" else "adb") or self._tool_path("adb")
        fastboot_binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        targets = {}
        status_lines = []
        if adb_binary:
            try:
                result = subprocess.run([adb_binary, "devices", "-l"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=10, check=False, **_windows_process_options())
                for line in result.stdout.splitlines()[1:]:
                    fields = line.split()
                    if len(fields) >= 2 and fields[1] in ("device", "sideload", "recovery", "unauthorized", "offline"):
                        targets[fields[0] + " [ADB: " + fields[1] + "]"] = (fields[0], "adb:" + fields[1])
                status_lines.append("ADB: " + (", ".join(k for k,v in targets.items() if v[1].startswith("adb:")) or "no devices"))
            except (OSError, subprocess.TimeoutExpired) as exc:
                status_lines.append("ADB scan failed: " + str(exc))
        else:
            status_lines.append("ADB tools not found")
        if fastboot_binary:
            try:
                result = subprocess.run([fastboot_binary, "devices", "-l"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=8, check=False, **_windows_process_options())
                found = []
                for line in result.stdout.splitlines():
                    fields = line.split()
                    if len(fields) >= 2 and fields[1].lower() == "fastboot":
                        label = fields[0] + " [Fastboot]"
                        targets[label] = (fields[0], "fastboot")
                        found.append(label)
                status_lines.append("Fastboot: " + (", ".join(found) or "no devices"))
            except (OSError, subprocess.TimeoutExpired) as exc:
                status_lines.append("Fastboot scan failed: " + str(exc))
        else:
            status_lines.append("Fastboot tools not found")
        previous = self.adb_device.get()
        # Classify the selected device before enabling mode-specific actions.
        self._adb_targets = targets
        labels = list(targets)
        self.adb_device_combo["values"] = labels
        self.adb_device.set(previous if previous in targets else (labels[0] if len(labels) == 1 else ""))
        self._on_shared_device_selected()
        self.adb_device_status.set(" | ".join(status_lines))
        selected = targets.get(self.adb_device.get())
        if selected:
            serial, state = selected
            if state == "fastboot":
                mode_detail = self._detect_fastboot_submode(fastboot_binary, serial)
                self.device_mode_status.set("MODE DETECTOR: " + mode_detail + " · Serial: " + serial)
            elif state.startswith("adb:"):
                adb_state = state.split(":", 1)[1]
                mode = {"device": "ADB / Android", "recovery": "ADB Recovery", "sideload": "ADB Sideload", "unauthorized": "ADB Unauthorized", "offline": "ADB Offline"}.get(adb_state, "ADB (" + adb_state + ")")
                self.device_mode_status.set("MODE DETECTOR: " + mode + " · Serial: " + serial)
        else:
            self.device_mode_status.set("MODE DETECTOR: No device detected · Check USB, drivers/udev rules, and Platform-Tools")
        self._refresh_device_info()
        self._append("Combined device scan: " + (", ".join(labels) if labels else "no ADB or Fastboot devices found"))

    def _set_device_info(self, values, mode, color):
        for vars_name, mode_name, label_name in (
            ("device_info_vars", "device_info_mode", "device_info_mode_label"),
            ("adb_device_info_vars", "adb_device_info_mode", "adb_device_info_mode_label")):
            vars_map = getattr(self, vars_name, None)
            if not vars_map:
                continue
            mode_var = getattr(self, mode_name, None)
            mode_label = getattr(self, label_name, None)
            if mode_var:
                mode_var.set(mode)
            if mode_label:
                mode_label.configure(bg=color)
            for key, value in values.items():
                if key in vars_map:
                    vars_map[key].set(value or "—")

    def _refresh_device_info(self):
        """Read best-effort device identity and safety metadata for the selected target."""
        target = getattr(self, "_adb_targets", {}).get(self.adb_device.get())
        empty = {"Brand / Manufacturer": "—", "Device name / Model": "—", "Model code": "—",
                 "Codename": "—", "Active slot": "—", "Bootloader": "—", "Battery": "—"}
        if not target:
            self._set_device_info(empty, "NO DEVICE", "#4b5563")
            return
        serial, state = target
        values = dict(empty)
        if state == "fastboot":
            binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
            mode = self._detect_fastboot_submode(binary, serial) if binary else "Fastboot"
            values["Device name / Model"] = serial
            values["Model code"] = self._fastboot_getvar(binary, serial, "product")
            values["Codename"] = values["Model code"]
            values["Active slot"] = self._fastboot_getvar(binary, serial, "current-slot")
            unlocked = self._fastboot_getvar(binary, serial, "unlocked").lower()
            if unlocked == "yes": values["Bootloader"] = "Unlocked"
            elif unlocked == "no": values["Bootloader"] = "Locked"
            values["Brand / Manufacturer"] = self._fastboot_getvar(binary, serial, "manufacturer")
            self._set_device_info(values, "FASTBOOTD" if "Fastbootd" in mode else "FASTBOOT",
                                  "#2563eb" if "Fastbootd" in mode else "#ea580c")
            return
        adb = self._tool_path("adb.exe" if os.name == "nt" else "adb") or self._tool_path("adb")
        adb_state = state.split(":", 1)[1] if state.startswith("adb:") else state
        mode, color = {"device": ("ADB", "#16a34a"), "recovery": ("RECOVERY", "#ca8a04"),
                       "sideload": ("SIDELOAD", "#ca8a04"), "unauthorized": ("ADB UNAUTHORIZED", "#ca8a04"),
                       "offline": ("ADB OFFLINE", "#ca8a04")}.get(adb_state, ("ADB", "#16a34a"))
        if adb:
            def prop(name):
                return self._adb_read_value(adb, serial, ["shell", "getprop", name])
            values["Brand / Manufacturer"] = prop("ro.product.manufacturer") or prop("ro.product.brand")
            values["Device name / Model"] = prop("ro.product.marketname") or prop("ro.product.model")
            values["Model code"] = prop("ro.product.name") or prop("ro.product.model")
            values["Codename"] = prop("ro.product.device") or prop("ro.build.product")
            values["Active slot"] = prop("ro.boot.slot_suffix") or prop("ro.boot.slot")
            locked = (prop("ro.boot.flash.locked") or prop("ro.boot.vbmeta.device_state")).lower()
            values["Bootloader"] = {"0": "Unlocked", "1": "Locked", "unlocked": "Unlocked",
                                    "locked": "Locked"}.get(locked, "Unknown")
            battery = self._adb_read_value(adb, serial, ["shell", "dumpsys", "battery"])
            match = re.search(r"(?m)^\s*level:\s*(\d+)", battery)
            if match:
                values["Battery"] = match.group(1) + "%"
        self._set_device_info(values, mode, color)

    def _adb_read_value(self, binary, serial, args):
        try:
            result = subprocess.run([binary, "-s", serial] + args, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3, check=False, **_windows_process_options())
            return (result.stdout or "").strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _fastboot_getvar(self, binary, serial, name):
        if not binary:
            return ""
        try:
            result = subprocess.run([binary, "-s", serial, "getvar", name], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=3, check=False, **_windows_process_options())
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            match = re.search(r"(?im)^\s*" + re.escape(name) + r"\s*:\s*(.+?)\s*$", output)
            return match.group(1).strip() if match else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _detect_fastboot_submode(self, binary, serial):
        """Distinguish bootloader Fastboot from userspace Fastbootd when supported."""
        if not binary:
            return "Fastboot"
        try:
            result = subprocess.run([binary, "-s", serial, "getvar", "is-userspace"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=4, check=False, **_windows_process_options())
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            normalized = output.lower()
            if re.search(r"is-userspace\s*:\s*yes|is-userspace\s*=\s*yes", normalized):
                return "Fastbootd (userspace Fastboot)"
            if re.search(r"is-userspace\s*:\s*no|is-userspace\s*=\s*no", normalized):
                return "Bootloader Fastboot"
        except (OSError, subprocess.TimeoutExpired):
            pass
        return "Fastboot (submode unknown)"

    def _update_reboot_actions(self):
        target = getattr(self, "_adb_targets", {}).get(self.adb_device.get())
        is_fastboot = bool(target and target[1] == "fastboot")
        if hasattr(self, "bootloader_button"):
            self.bootloader_button.configure(state="disabled" if is_fastboot else "normal")
        if hasattr(self, "reboot_button"):
            self.reboot_button.configure(text="Fastboot reboot" if is_fastboot else "Reboot")
        if hasattr(self, "recovery_button"):
            self.recovery_button.configure(text="Reboot recovery" if is_fastboot else "Recovery")
        if is_fastboot:
            self.adb_device_status.set("Fastboot/bootloader mode detected. Bootloader action is disabled; reboot actions use Fastboot.")

    def _adb_require(self, states=("device",)):
        binary = self._tool_path("adb.exe" if os.name == "nt" else "adb") or self._tool_path("adb")
        if not binary: messagebox.showerror("ADB not found","Install Android SDK Platform-Tools and ensure adb is on PATH."); return None
        target=getattr(self,"_adb_targets",{}).get(self.adb_device.get())
        if not target: messagebox.showwarning("Select device","Refresh devices and select a device."); return None
        serial,state=target
        if state.startswith("adb:"):
            state = state.split(":", 1)[1]
        if state not in states: messagebox.showwarning("Device not ready","Device state is "+state+". Expected: "+", ".join(states)+"."); return None
        return binary,serial

    def _adb_reboot(self, mode):
        target = getattr(self, "_adb_targets", {}).get(self.adb_device.get())
        if not target:
            messagebox.showwarning("Select device", "Refresh devices and select an ADB or Fastboot device.")
            return
        serial, state = target
        is_fastboot = state == "fastboot"
        if is_fastboot:
            binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
            if not binary:
                messagebox.showerror("Fastboot not found", "Install Android platform-tools and ensure fastboot is on PATH.")
                return
            if mode == "bootloader":
                messagebox.showinfo("Already in Fastboot", "The device is already in bootloader/Fastboot mode.")
                return
            command = [binary, "-s", serial, "reboot"] + (["recovery"] if mode == "recovery" else [])
            label = "recovery" if mode == "recovery" else "normal"
            kind = "fastboot_reboot"
            title = "Fastboot reboot"
        else:
            selected = self._adb_require(("device", "recovery"))
            if not selected:
                return
            binary, serial = selected
            command = [binary, "-s", serial, "reboot"] + ([mode] if mode else [])
            label = mode or "normal"
            kind = "adb_reboot"
            title = "ADB reboot"
        if not messagebox.askyesno("Confirm reboot", "Reboot " + serial + " into " + label + " mode?"):
            self._console_write(self.fastboot_console if is_fastboot else self.adb_console,
                                "[DroidLab] Reboot cancelled by user.\n")
            return
        # Use the same asynchronous streaming runner as manual commands so the
        # command, stdout/stderr, errors, and exit status appear in the live console.
        self.adb_device_status.set(title + " in progress…")
        threading.Thread(target=self._adb_task, args=(command, 35, kind), daemon=True).start()

    def _enable_sideload_drop(self, widget):
        """Register a drop target that routes OTA ZIPs to the sideload field."""
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._drop_sideload)
        except Exception as exc:
            self._append("ADB sideload drag-and-drop unavailable on this widget: " + str(exc))

    def _drop_sideload(self, event):
        try:
            paths = [Path(p).expanduser() for p in self.tk.splitlist(event.data)]
        except (tk.TclError, TypeError):
            self.adb_device_status.set("Could not read dropped path; use Browse ZIP.")
            return "break"
        packages = [p for p in paths if p.is_file() and p.suffix.lower() == ".zip"]
        if not packages:
            messagebox.showwarning("OTA ZIP required", "Drop an existing .zip update package onto the ADB Sideload area.")
            return "break"
        package = packages[0].resolve()
        self.sideload_file.set(str(package))
        self.adb_device_status.set("OTA ZIP selected: " + package.name + ". Choose Sideload ZIP when the device is in recovery sideload mode.")
        self._append("OTA ZIP dropped for ADB sideload: " + str(package))
        return "break"

    def _browse_sideload_zip(self):
        """Use the operating system's native ZIP picker on Windows and Linux."""
        current = self.sideload_file.get().strip().strip(chr(34)).strip(chr(39))
        try:
            initial_dir = str(Path(current).expanduser().resolve().parent) if current and Path(current).expanduser().exists() else str(Path.home())
        except (OSError, RuntimeError):
            initial_dir = str(Path.home())
        try:
            selected = filedialog.askopenfilename(
                parent=self,
                title="Choose OTA ZIP package for ADB sideload",
                initialdir=initial_dir,
                filetypes=[
                    ("Android update ZIP packages", "*.zip"),
                    ("All files", "*.*"),
                ],
            )
        except (tk.TclError, OSError) as exc:
            logging.exception("Native ADB sideload ZIP picker failed")
            messagebox.showerror(
                "Browse ZIP failed",
                "Windows/Linux could not open the file picker. You can paste the full ZIP path into the package field.\n\n" + str(exc),
                parent=self,
            )
            return
        if not selected:
            return
        package = Path(selected).expanduser()
        if not package.is_file() or package.suffix.lower() != ".zip":
            messagebox.showerror("Invalid update ZIP", "Choose an existing .zip update package.", parent=self)
            return
        resolved = str(package.resolve())
        self.sideload_file.set(resolved)
        self.adb_device_status.set("OTA ZIP selected: " + package.name)
        self._append("OTA ZIP selected for sideload: " + resolved)

    def _run_adb_sideload(self):
        selected=self._adb_require(("sideload",))
        if not selected:return
        binary,serial=selected; package=Path(self.sideload_file.get().strip().strip(chr(34)).strip(chr(39))).expanduser()
        if not package.is_file() or package.suffix.lower()!=".zip":messagebox.showerror("Invalid update ZIP","Choose an existing .zip update package.");return
        command=[binary,"-s",serial,"sideload",str(package.resolve())]
        detail="Device: "+serial+"\nPackage: "+str(package.resolve())+"\nCommand: "+" ".join(shlex.quote(x) for x in command)+"\n\nOnly install a package intended for this exact device. Continue?"
        if not messagebox.askyesno("Confirm ADB sideload",detail,icon="warning"):return
        self._append("Starting ADB sideload: "+" ".join(shlex.quote(x) for x in command)); self.adb_device_status.set("Sideload in progress…")
        threading.Thread(target=self._adb_task,args=(command,1800,"sideload"),daemon=True).start()

    def _console_write(self, widget, text):
        """Append text to a console widget from the Tk/UI thread only."""
        if not widget or not widget.winfo_exists():
            return
        widget.configure(state="normal")
        widget.insert("end", str(text))
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > 5000:
            widget.delete("1.0", f"{line_count - 5000}.0")
        widget.see("end")
        widget.configure(state="disabled")

    def _stream_process(self, command, timeout, kind):
        """Run a host command and stream output safely on Windows and POSIX."""
        import time
        from queue import Queue, Empty

        rendered = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        self.events.put(("console_line", (kind, "[DroidLab] $ " + rendered + "\n")))
        process = None
        output_lines = []
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                       errors="replace", bufsize=1, **_windows_process_options())
            # A dedicated reader avoids blocking the timeout loop on readline() when
            # a tool stays silent. Queue and thread usage is portable across Windows/Linux.
            lines = Queue()

            def read_output():
                try:
                    for line in process.stdout:
                        lines.put(line)
                finally:
                    lines.put(None)

            reader = threading.Thread(target=read_output, name="DroidLab-CommandOutput", daemon=True)
            reader.start()
            deadline = time.monotonic() + timeout
            stream_closed = False
            while not stream_closed:
                if time.monotonic() >= deadline and process.poll() is None:
                    process.kill()
                    process.wait()
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    line = lines.get(timeout=min(0.15, max(0.01, deadline - time.monotonic())))
                except Empty:
                    continue
                if line is None:
                    stream_closed = True
                else:
                    output_lines.append(line)
                    self.events.put(("console_line", (kind, line)))
            code = process.wait()
            output = "".join(output_lines).strip() or "Exit status: " + str(code)
            self.events.put(("console_line", (kind, "[DroidLab] Process exited with code " + str(code) + "\n")))
            self.events.put(("adb_result", (kind, code, output)))
        except (OSError, subprocess.TimeoutExpired) as exc:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            message = str(exc)
            self.events.put(("console_line", (kind, "[DroidLab] ERROR: " + message + "\n")))
            self.events.put(("adb_result", (kind, -1, message)))

    def _adb_task(self, command, timeout, kind):
        self._stream_process(command, timeout, kind)

    def _run_adb_shell(self):
        text = self.adb_shell_command.get().strip()
        if not text:
            messagebox.showwarning("Command required", "Enter a device-shell command (dumpsys battery) or host ADB command (adb devices).")
            return
        # ADB commands run on the computer. Plain commands run on the selected
        # Android device. This avoids sending "adb devices" into Android's shell.
        try:
            tokens = shlex.split(text, posix=(os.name != "nt"))
        except ValueError as exc:
            messagebox.showerror("Invalid command", "Check the command quoting:\n" + str(exc))
            return
        if not tokens:
            return
        first = Path(tokens[0]).name.lower()
        # Fastboot is a host-side executable, never an Android device-shell
        # command. Route it to its own runner and console even when entered in
        # the advanced command field.
        if first in ("fastboot", "fastboot.exe"):
            selected_target = getattr(self, "_adb_targets", {}).get(self.adb_device.get())
            if selected_target and selected_target[1] == "fastboot":
                self.fastboot_device.set(selected_target[0])
            self.fastboot_manual_command.set(text)
            self._run_fastboot_manual()
            return
        if first in ("adb", "adb.exe"):
            binary = self._tool_path("adb.exe" if os.name == "nt" else "adb") or self._tool_path("adb")
            if not binary:
                messagebox.showerror("ADB not found", "Bundle platform-tools or install ADB and add it to PATH.")
                return
            args = tokens[1:]
            if not args:
                args = ["devices", "-l"]
            # If a serial is selected and the command doesn't already select a device,
            # target it automatically for commands other than device discovery.
            discovery = args[0] in ("devices", "version", "help")
            selected = self._adb_require(("device",)) if not discovery else None
            if selected and not any(args[i] == "-s" for i in range(min(len(args), 3))):
                args = ["-s", selected[1]] + args
            command = [binary] + args
            risky = re.compile(r"(^|[;&|]\s*)(reboot|sideload|root|remount|disable-verity)\b|\\b(erase|format|flash|wipe|uninstall)\b", re.I)
            if risky.search(text) and not messagebox.askyesno("Confirm ADB command", "This command can change device state:\n\n" + text + "\n\nContinue?", icon="warning"):
                self._append("ADB command cancelled by user.")
                return
            self._append("Running host ADB command: " + " ".join(shlex.quote(x) for x in command))
            threading.Thread(target=self._adb_task, args=(command, 180, "shell"), daemon=True).start()
            return

        selected = self._adb_require(("device",))
        if not selected:
            return
        binary, serial = selected
        risky = re.compile(r"(^|[;&|]\s*)(rm\s+-rf|reboot|poweroff|shutdown|mkfs\b|dd\b|format\b|wipe\b|setprop\b|pm\s+(uninstall|disable-user)|svc\s+.*disable)", re.I)
        if risky.search(text) and not messagebox.askyesno("Confirm potentially destructive shell command", "This command may change device state or delete data:\n\n" + text + "\n\nRun it on " + serial + "?", icon="warning"):
            self._append("Device-shell command cancelled by user.")
            return
        self._append("Running device shell command on " + serial + ": " + text)
        threading.Thread(target=self._adb_task, args=([binary, "-s", serial, "shell"] + tokens, 90, "shell"), daemon=True).start()

    def _run_fastboot_manual(self):
        text = self.fastboot_manual_command.get().strip()
        if not text:
            messagebox.showwarning("Command required", "Enter a Fastboot command.")
            return
        try:
            tokens = shlex.split(text, posix=(os.name != "nt"))
        except ValueError as exc:
            messagebox.showerror("Invalid command", "Check the command quoting:\n" + str(exc))
            return
        if not tokens:
            return
        first = Path(tokens[0]).name.lower()
        if first in ("fastboot", "fastboot.exe"):
            tokens = tokens[1:]
        binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        if not binary:
            messagebox.showerror("Fastboot not found", "Bundle platform-tools or install Fastboot and add it to PATH.")
            return
        if not tokens:
            tokens = ["devices"]
        serial = self.fastboot_device.get().strip()
        # If the dedicated Fastboot selector is empty, reuse the shared ADB &
        # Sideload selector only when it currently identifies a Fastboot device.
        if not serial:
            shared = getattr(self, "_adb_targets", {}).get(self.adb_device.get())
            if shared and shared[1] == "fastboot":
                serial = shared[0]
        discovery = tokens[0] in ("devices", "help", "--help", "--version", "version")
        if serial and not discovery and not any(tokens[i] == "-s" for i in range(min(len(tokens), 3))):
            tokens = ["-s", serial] + tokens
        command = [binary] + tokens
        rendered = " ".join(shlex.quote(x) for x in command)
        destructive = re.compile(r"\b(flash|erase|format|delete-logical-partition|wipe|set_active|flashing\s+(unlock|lock)|-w)\b", re.I)
        if destructive.search(" ".join(tokens)):
            if not messagebox.askyesno("Confirm potentially destructive Fastboot command", "Verify the connected device, image, and partition before continuing.\n\n" + rendered + "\n\nThis operation may erase data or make the device unbootable. Continue?", icon="warning"):
                self._append("Manual Fastboot command cancelled: " + rendered)
                return
        self._append("Executing manual Fastboot command: " + rendered)
        self.status.set("Running Fastboot command…")
        threading.Thread(target=self._adb_task, args=(command, 900, "fastboot_manual"), daemon=True).start()

    def _enable_command_drop(self, widget, variable, tool):
        """Allow a file path to be dropped into a manual command field."""
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", lambda event: self._drop_command_path(event, variable, tool))
        except Exception as exc:
            logging.info("Command-field drag-and-drop unavailable (%s): %s", tool, exc)

    def _drop_command_path(self, event, variable, tool):
        try:
            paths = [Path(path) for path in self.tk.splitlist(event.data)]
        except (tk.TclError, TypeError):
            self._append("Could not read dropped path; paste it into the command field instead.")
            return "break"
        if not paths:
            return "break"
        path = paths[0].expanduser()
        # Quote paths containing spaces so they remain one argument on both platforms.
        quoted = subprocess.list2cmdline([str(path)]) if os.name == "nt" else shlex.quote(str(path))
        current = variable.get().strip()
        if current in ("adb", "fastboot", "adb.exe", "fastboot.exe"):
            variable.set(current + " " + quoted)
        elif current:
            variable.set(current + " " + quoted)
        else:
            variable.set(quoted)
        self._append(f"Inserted dropped file path into {tool} command: {path}")
        return "break"

    def _append_adb_output(self, text):
        self.adb_output.configure(state="normal"); self.adb_output.insert("end",text+"\n"); self.adb_output.see("end"); self.adb_output.configure(state="disabled")

    def _start_adb_logcat(self):
        selected = self._adb_require(("device",))
        if not selected:
            return
        if self.adb_log_process and self.adb_log_process.poll() is None:
            messagebox.showinfo("Logcat running", "Stop the current logcat window first.", parent=self)
            return
        binary, serial = selected
        logging.info("Starting live logcat for device %s using %s", serial, binary)
        window = tk.Toplevel(self)
        window.title("DroidLab Live Logcat - " + serial)
        window.geometry("1000x650")
        window.configure(bg="#111827")
        toolbar = ttk.Frame(window)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(toolbar, text="Filter").pack(side="left", padx=(0, 6))
        self.adb_log_filter = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.adb_log_filter).pack(side="left", fill="x", expand=True)
        text = tk.Text(window, bg="#0b1220", fg="#d1d5db", wrap="none", font=("Monospace", 10), state="disabled")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        self.adb_log_window, self.adb_log_text = window, text
        self.adb_log_lines = []
        self.adb_log_paused = False
        self.adb_log_pause_button = None
        self.adb_log_filter.trace_add("write", lambda *_: self._render_adb_logcat())
        try:
            self.adb_log_process = subprocess.Popen(
                [binary, "-s", serial, "logcat", "-v", "threadtime"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1, **_windows_process_options())
        except OSError as exc:
            logging.exception("Could not start live logcat for %s", serial)
            window.destroy()
            self.adb_log_window = self.adb_log_text = None
            messagebox.showerror("Logcat failed", str(exc), parent=self)
            return
        buttons = ttk.Frame(window)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        self.adb_log_pause_button = ttk.Button(buttons, text="Pause display", command=self._toggle_adb_logcat_pause)
        self.adb_log_pause_button.pack(side="left")
        ttk.Button(buttons, text="Save visible log…", command=self._save_visible_adb_logcat).pack(side="left", padx=6)
        ttk.Button(buttons, text="Stop logcat", command=self._stop_adb_logcat).pack(side="right")
        process = self.adb_log_process
        def reader():
            try:
                if process.stdout:
                    for line in process.stdout:
                        self.events.put(("adb_log_line", line.rstrip("\r\n")))
            except (OSError, ValueError):
                logging.exception("Error reading live logcat output")
            finally:
                self.events.put(("adb_log_end", process.poll()))
        threading.Thread(target=reader, name="DroidLab-LogcatReader", daemon=True).start()
        window.protocol("WM_DELETE_WINDOW", self._stop_adb_logcat)

    def _render_adb_logcat(self):
        if not self.adb_log_text or not self.adb_log_window or not self.adb_log_window.winfo_exists(): return
        needle=self.adb_log_filter.get().casefold().strip()
        visible=[line for line in self.adb_log_lines if not needle or needle in line.casefold()]
        self.adb_log_text.configure(state="normal"); self.adb_log_text.delete("1.0","end")
        if visible: self.adb_log_text.insert("end","\n".join(visible[-5000:])+"\n"); self.adb_log_text.see("end")
        self.adb_log_text.configure(state="disabled")

    def _toggle_adb_logcat_pause(self):
        self.adb_log_paused=not self.adb_log_paused
        self.adb_log_pause_button.configure(text="Resume display" if self.adb_log_paused else "Pause display")

    def _save_visible_adb_logcat(self):
        needle=self.adb_log_filter.get().casefold().strip()
        visible=[line for line in self.adb_log_lines if not needle or needle in line.casefold()]
        path=filedialog.asksaveasfilename(parent=self, title="Save filtered logcat",defaultextension=".txt",initialfile="logcat-filtered.txt",filetypes=[("Text files","*.txt"),("All files","*.*")])
        if not path:return
        try:
            with open(path,"w",encoding="utf-8") as output: output.write("\n".join(visible)+("\n" if visible else ""))
            self._append("Saved "+str(len(visible))+" filtered logcat lines to "+path)
            messagebox.showinfo("Logcat saved","Saved "+str(len(visible))+" lines to:\n"+path)
        except OSError as exc: messagebox.showerror("Logcat save failed",str(exc))

    def _stop_adb_logcat(self):
        if self.adb_log_process and self.adb_log_process.poll() is None:self.adb_log_process.terminate()
        self.adb_log_process=None
        if self.adb_log_window and self.adb_log_window.winfo_exists():self.adb_log_window.destroy()
        self.adb_log_window=self.adb_log_text=None

    def _save_adb_logcat(self):
        selected=self._adb_require(("device",))
        if not selected:return
        path=filedialog.asksaveasfilename(parent=self, title="Save Android logcat",defaultextension=".txt",initialfile="logs.txt",filetypes=[("Text files","*.txt"),("All files","*.*")])
        if not path:return
        binary,serial=selected
        self._append("Starting logcat capture to " + path)
        threading.Thread(target=self._capture_logcat_file,args=(binary,serial,path),daemon=True).start()

    def _capture_logcat_file(self,binary,serial,path):
        process=None
        try:
            with open(path,"w",encoding="utf-8") as output:
                process=subprocess.Popen([binary,"-s",serial,"logcat"],stdout=output,stderr=subprocess.PIPE,text=True,encoding="utf-8",errors="replace",**_windows_process_options())
                try:
                    _stdout,stderr=process.communicate(timeout=3600)
                    self.events.put(("adb_result",("logcat_file",process.returncode,"Saved logcat to "+path+(("\n"+stderr.strip()) if stderr and stderr.strip() else ""))))
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
                    self.events.put(("adb_result",("logcat_file",0,"One-hour capture limit reached. Partial logcat saved to "+path)))
        except OSError as exc:
            if process and process.poll() is None: process.terminate()
            self.events.put(("adb_result",("logcat_file",-1,str(exc))))

    def _create_adb_bugreport(self):
        selected=self._adb_require(("device",))
        if not selected:return
        path=filedialog.asksaveasfilename(parent=self, title="Save Android bugreport",defaultextension=".zip",initialfile="bugreport.zip",filetypes=[("ZIP archive","*.zip"),("All files","*.*")])
        if not path:return
        binary,serial=selected
        if not messagebox.askyesno("Create bugreport","The report may include device identifiers, logs, and app activity.\nSave diagnostics from "+serial+" to:\n"+path+"?",icon="warning"):return
        self._bugreport_target_path=path
        threading.Thread(target=self._adb_task,args=([binary,"-s",serial,"bugreport",path],600,"bugreport"),daemon=True).start()

    def _run_fastboot_maintenance(self, operation, partition=None):
        binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        if not binary:
            messagebox.showerror("Fastboot not found", "Install Android SDK Platform-Tools and ensure fastboot is on PATH.")
            return
        serial = self.fastboot_device.get().strip()
        if not serial or serial not in self.device_combo["values"]:
            messagebox.showwarning("Select Fastboot device", "Click Refresh devices and select a connected Fastboot device first.")
            return
        if operation == "-w":
            command = [binary, "-s", serial, "-w"]
            label = "Wipe user data and cache (fastboot -w)"
            warning = "This will wipe user data and cache on device " + serial + ". This is destructive and may remove your apps, accounts, photos, and files. Continue only if you have a backup and intend to factory-reset this device."
        else:
            command = [binary, "-s", serial, operation, partition]
            label = "Erase " + partition
            if partition == "cache":
                warning = "This will erase the cache partition on " + serial + ", if this device has one. Some devices do not have a separate cache partition. Continue?"
            else:
                warning = "This will erase the userdata partition on " + serial + " and permanently delete user data. This can make data unrecoverable. Ensure you have a backup. Continue?"
        rendered = " ".join(shlex.quote(part) for part in command)
        self.maintenance_command.set(rendered)
        if not messagebox.askyesno("Confirm destructive Fastboot operation", warning + "\n\nCommand to execute:\n" + rendered, icon="warning"):
            self.maintenance_status.set("Cancelled: " + label)
            self._append("Fastboot maintenance cancelled: " + rendered)
            return
        self.maintenance_status.set("Running: " + label + "…")
        self._append("Executing Fastboot maintenance: " + rendered)
        threading.Thread(target=self._adb_task, args=(command, 900, "fastboot_maintenance"), daemon=True).start()

    def _on_flash_image_changed(self):
        path = Path(self.flash_image.get().strip().strip(chr(34)).strip(chr(39)))
        mapping = {"boot.img":"boot", "vendor.img":"vendor", "vendor_boot.img":"vendor_boot", "init_boot.img":"init_boot", "dtbo.img":"dtbo", "vbmeta.img":"vbmeta", "recovery.img":"recovery", "system.img":"system", "product.img":"product", "odm.img":"odm", "system_ext.img":"system_ext", "vbmeta_system.img":"vbmeta_system", "vbmeta_vendor.img":"vbmeta_vendor"}
        if path.name.lower() in mapping: self.flash_partition.set(mapping[path.name.lower()]); self._append("Filename suggests partition " + mapping[path.name.lower()] + "; verify compatibility before flashing.")
        self._update_flash_preview()

    def _browse_flash_image(self):
        path = filedialog.askopenfilename(parent=self, title="Choose image to flash", initialdir=str(Path.home()), filetypes=[("Android image", "*.img"), ("All files", "*.*")])
        if path: self.flash_image.set(path)

    def _refresh_fastboot_devices(self):
        binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        if not binary: messagebox.showerror("Fastboot not found", "Install Android platform-tools and ensure fastboot is on PATH."); return
        try: result = subprocess.run([binary, "devices", "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, check=False, **_windows_process_options())
        except (OSError, subprocess.TimeoutExpired) as exc: messagebox.showerror("Device detection failed", str(exc)); return
        devices = [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "fastboot"]
        self.device_combo["values"] = devices
        shared = getattr(self, "_adb_targets", {})
        shared_fastboot = next((label for label, target in shared.items() if target[1] == "fastboot" and target[0] in devices), "")
        chosen = shared_fastboot or (devices[0] if len(devices) == 1 else "")
        self.fastboot_device.set(chosen.split(" [Fastboot]")[0] if chosen.endswith(" [Fastboot]") else chosen)
        self._append("Fastboot scan: " + (", ".join(devices) if devices else "no devices found"))
        if devices:
            self._show_device_workspace("fastboot")

    def _update_flash_preview(self):
        image = self.flash_image.get().strip().strip(chr(34)).strip(chr(39)); part = self.flash_partition.get().strip(); serial = self.fastboot_device.get().strip()
        self.flash_command.set("Select an image, partition, and one Fastboot device." if not (image and part and serial) else "fastboot -s " + serial + " flash " + part + " " + shlex.quote(image))

    def _flash_image(self):
        image = Path(self.flash_image.get().strip().strip(chr(34)).strip(chr(39))).expanduser(); part = self.flash_partition.get().strip(); serial = self.fastboot_device.get().strip(); binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        if not binary: messagebox.showerror("Fastboot not found", "Install Android platform-tools and ensure fastboot is on PATH."); return
        if not image.is_file() or image.suffix.lower() != ".img": messagebox.showerror("Invalid image", "Choose an existing .img file."); return
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", part): messagebox.showerror("Invalid partition", "Enter a valid partition name."); return
        if not serial: messagebox.showwarning("Select device", "Refresh devices and select exactly one Fastboot device."); return
        command = [binary, "-s", serial, "flash", part, str(image.resolve())]
        detail = "Device: " + serial + "\nImage: " + str(image.resolve()) + "\nPartition: " + part + "\nCommand: " + " ".join(shlex.quote(x) for x in command) + "\n\nWrong or incompatible images can make the device unbootable. Verify this image is for this exact device and partition. Continue?"
        if not messagebox.askyesno("Confirm flashing", detail, icon="warning"): self._append("Flash cancelled before execution."); return
        self._append("Executing: " + " ".join(shlex.quote(x) for x in command)); self.status.set("Flashing image…"); self.update_idletasks()
        threading.Thread(target=self._adb_task, args=(command, 900, "fastboot_flash"), daemon=True).start()
    def _enable_drop(self, widget):
        """Allow files to be dropped onto the main window and source controls."""
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._drop_source)
        except Exception as exc:
            # Manual path entry and file browsing remain available if DnD fails.
            self._append(f"Drag-and-drop unavailable on this system: {exc}")
    def _drop_source(self, event):
        try:
            paths = [Path(p) for p in self.tk.splitlist(event.data)]
        except (tk.TclError, TypeError):
            self._append("Could not read the dropped file path. Use Browse to select it.")
            return "break"
        images = [p for p in paths if p.is_file() and p.suffix.lower() == ".img"]
        if images:
            self.flash_image.set(str(images[0].resolve())); self.status.set("Flash image selected: " + images[0].name); self._append("Flash image dropped: " + str(images[0])); return "break"
        candidates = [p for p in paths if p.is_file() and p.suffix.lower() in (".bin", ".zip")]
        if not candidates:
            self.status.set("Unsupported dropped file")
            self._append("Drop a valid payload .bin or OTA .zip file.")
            messagebox.showwarning("Unsupported file", "Drop a payload .bin or Android OTA .zip file.")
            return "break"
        # Prefer payload.bin when several files are dropped together.
        chosen = next((p for p in candidates if p.name.lower() == "payload.bin"), candidates[0])
        self.source.set(str(chosen))
        self.parts.clear()
        self.part_vars.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.status.set(f"Selected: {chosen.name}")
        self._append(f"Selected by drag and drop: {chosen}")
        return "break"
    def _append(self, msg):
        message = str(msg)
        logging.log(logging.ERROR if message.startswith("ERROR:") else logging.INFO, "%s", message)
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
    def _busy(self,value):self.start_button.configure(state="disabled" if value else "normal"); self.cancel_button.configure(state="normal" if value else "disabled"); self.progress.start(12) if value else self.progress.stop()
    def _browse_source(self):
        """Open the native file picker with a valid starting folder and owner."""
        current = Path(self.source.get().strip().strip('"').strip("'")).expanduser() if self.source.get().strip() else None
        initialdir = current.parent if current and current.is_file() else Path.home()
        if not initialdir.is_dir():
            initialdir = Path.home()
        try:
            path = filedialog.askopenfilename(
                parent=self,
                title="Choose Android OTA package",
                initialdir=str(initialdir),
                filetypes=[("Android OTA packages", "*.bin *.zip"), ("All files", "*.*")],
            )
        except tk.TclError as exc:
            self._append(f"File picker failed: {exc}")
            messagebox.showerror("File picker error", f"Windows could not open the file picker. You can paste the full path into the input field instead.\n\n{exc}", parent=self)
            return
        if path:
            self.source.set(str(Path(path).resolve()))
            self.parts.clear()
            self.part_vars.clear()
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.status.set(f"Selected: {Path(path).name}")
            self._append(f"Selected input file: {path}")
    def _apply_manual_source_path(self):
        """Validate a path typed or pasted into the input field."""
        raw = self.source.get().strip().strip('"').strip("'")
        if not raw:
            messagebox.showwarning("Input path required", "Type or paste the full path to payload.bin or an Android OTA .zip file.")
            self.source_entry.focus_set()
            return
        path = Path(raw).expanduser()
        if not path.is_file():
            messagebox.showerror("File not found", "The input path is not an existing file:\n" + str(path))
            self.source_entry.focus_set()
            return
        if path.suffix.lower() not in {".bin", ".zip"}:
            messagebox.showerror("Unsupported file", "Choose a payload .bin file or Android OTA .zip file.")
            return
        resolved = str(path.resolve())
        self.source.set(resolved)
        self.parts.clear()
        self.part_vars.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.status.set("Input path ready: " + path.name)
        self._append("Input path set manually: " + resolved)

    def _refresh_storage_locations(self):
        """List user-mounted removable drives, not system partitions or virtual filesystems."""
        locations = []
        seen_paths = set()
        user = os.environ.get("USER", "")
        # These are the common desktop mount roots for removable media.
        allowed_roots = [Path("/run/media") / user, Path("/media") / user]
        allowed_roots = [p.resolve() for p in allowed_roots if user]

        def add(path, label):
            try:
                resolved = Path(path).expanduser().resolve()
                # Keep the selector focused on user-mounted removable drives.
                # Do not show /, /home, /boot, /run/user/*/gvfs, or other system mounts.
                if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                    return
                if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.X_OK):
                    return
                key = str(resolved)
                if key not in seen_paths:
                    seen_paths.add(key)
                    locations.append((f"{label} — {key}", key))
            except (OSError, RuntimeError):
                pass

        # GIO provides desktop-managed mount names; only keep paths under the
        # user's conventional removable-media mount roots.
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
            monitor = Gio.VolumeMonitor.get()
            for mount in monitor.get_mounts():
                try:
                    path = mount.get_root().get_path()
                    if path:
                        add(path, mount.get_name() or Path(path).name or "Mounted drive")
                except Exception:
                    continue
        except (ImportError, ValueError, AttributeError) as exc:
            self._append(f"GIO volume discovery unavailable; scanning mounted media folders instead: {exc}")

        # Filesystem fallback for desktops where GIO bindings are unavailable.
        for root in allowed_roots:
            if not root.is_dir():
                continue
            try:
                for candidate in root.iterdir():
                    if candidate.is_dir():
                        add(candidate, "Mounted removable drive")
            except OSError:
                continue

        locations.sort(key=lambda item: item[1].casefold())
        self._storage_locations = locations
        labels = [label for label, _path in locations]
        self.storage_combo["values"] = labels
        current = self.storage_var.get()
        self.storage_var.set(current if current in labels else (labels[0] if labels else ""))
        if locations:
            self.status.set(f"Found {len(locations)} mounted removable drive(s)")
            self._append(f"Drive list refreshed: {len(locations)} mounted removable drive(s)")
        else:
            self.status.set("No mounted removable drives found. Use Browse or Choose output.")
            self._append("Drive list refreshed: no user-mounted removable drives found.")

    def _selected_storage_path(self):
        selected = self.storage_var.get()
        return next((path for label, path in getattr(self, "_storage_locations", []) if label == selected), "")

    def _browse_selected_storage(self):
        directory = self._selected_storage_path()
        if not directory:
            messagebox.showwarning("Storage location", "Select an accessible storage location first.")
            return
        path = filedialog.askopenfilename(
            title=f"Choose payload from {directory}",
            initialdir=directory,
            filetypes=[("Android OTA", "*.bin *.zip"), ("All files", "*")],
        )
        if path:
            self.source.set(path)
            self.parts.clear()
            self.part_vars.clear()
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.status.set(f"Selected: {Path(path).name}")
            self._append(f"Selected input from storage location: {path}")

    def _use_selected_storage(self):
        directory = self._selected_storage_path()
        if not directory:
            messagebox.showwarning("Storage location", "Select an accessible storage location first.")
            return
        self.output.set(directory)
        self.status.set(f"Output directory: {directory}")
        self._append(f"Output directory set to: {directory}")

    def _browse_output(self):
        """Choose an output directory without passing an invalid Windows path."""
        raw = self.output.get().strip().strip('"').strip("'")
        initial = Path(raw).expanduser() if raw else Path.home()
        if not initial.is_dir():
            initial = initial.parent if initial.parent.is_dir() else Path.home()
        try:
            path = filedialog.askdirectory(
                parent=self, title="Choose output directory",
                initialdir=str(initial), mustexist=True,
            )
        except tk.TclError as exc:
            self._append(f"Output folder picker failed: {exc}")
            messagebox.showerror("Folder picker error", f"Could not open the folder picker. Enter the output path manually instead.\n\n{exc}", parent=self)
            return
        if path:
            self.output.set(str(Path(path).resolve()))
            self.status.set(f"Output directory: {path}")
            self._append(f"Output directory selected: {path}")
    def _inspect(self):
        if self.worker and self.worker.is_alive():return
        src=self.source.get().strip()
        if not src:messagebox.showwarning("Input required","Choose an OTA .bin or .zip file first.");return
        self._busy(True);self.status.set("Inspecting payload…");self.worker=threading.Thread(target=self._inspect_worker,args=(src,),daemon=True);self.worker.start()
    def _inspect_worker(self,src):
        try:self.events.put(("parts",inspect_source(src,progress=lambda x:self.events.put(("log",x)))));self.events.put(("info_done","Inspection complete."))
        except BackendError as e:self.events.put(("error",str(e)))
    def _load_parts(self,parts):
        for i in self.tree.get_children():self.tree.delete(i)
        self.parts.clear();self.part_vars.clear()
        for part in parts:
            n=str(part["name"]);self.parts[n]=part;self.part_vars[n]=tk.BooleanVar(value=False);self.tree.insert("","end",iid=n,text="☐  "+n,values=(self._size(int(part.get("size_bytes") or 0)),))
        self._append(f"Found {len(parts)} partitions.")
    @staticmethod
    def _size(n):
        v=float(n)
        for unit in ("B","KB","MB","GB","TB"):
            if v<1024 or unit=="TB":return f"{v:.1f} {unit}"
            v/=1024
    def _toggle(self,event):
        n=self.tree.identify_row(event.y)
        if n not in self.part_vars:return
        v=not self.part_vars[n].get();self.part_vars[n].set(v);self.tree.item(n,text=("☑  " if v else "☐  ")+n);return "break"
    def _select_all(self,yes):
        for n,var in self.part_vars.items():var.set(yes);self.tree.item(n,text=("☑  " if yes else "☐  ")+n)
    def _start(self):
        if self.worker and self.worker.is_alive():return
        src=self.source.get().strip();out=self.output.get().strip()
        if not src:messagebox.showwarning("Input required","Choose a payload .bin or OTA .zip file.");return
        if not out:messagebox.showwarning("Output required","Choose an output directory.");return
        selected=[n for n,v in self.part_vars.items() if v.get()];self.cancel_event.clear();self._busy(True);self.status.set("Extracting…");self._append("Input: "+src);self._append("Output: "+out)
        self.worker=threading.Thread(target=self._extract_worker,args=(src,out,self.fingerprint.get(),selected),daemon=True);self.worker.start()
    def _extract_worker(self,src,out,fingerprint,selected):
        try:
            outputs=extract(src,out,selected or None,progress=lambda x:self.events.put(("log",x)),cancel_event=self.cancel_event)
            for p in outputs:
                self.events.put(("log",f"Created: {p}"))
                if fingerprint:self.events.put(("log",f"SHA-256 fingerprint: {sha256_file(str(p))}  {p.name}"))
            if not outputs:self.events.put(("log","No new files were detected in the output directory."))
            self.events.put(("done",f"Finished. Output files detected: {len(outputs)}"))
        except BackendError as e:self.events.put(("cancelled" if self.cancel_event.is_set() else "error",str(e)))
        except Exception as e:self.events.put(("error",f"Unexpected error: {e}"))
    def _cancel(self):self.cancel_event.set();self.status.set("Cancelling…");self._append("Cancellation requested.")
    def _poll_events(self):
        logcat_changed = False
        try:
            while True:
                kind,payload=self.events.get_nowait()
                if kind=="log":self._append(payload)
                elif kind=="adb_log_line":
                    if self.adb_log_text and self.adb_log_window and self.adb_log_window.winfo_exists():
                        self.adb_log_lines.append(payload)
                        if len(self.adb_log_lines)>10000: del self.adb_log_lines[:len(self.adb_log_lines)-10000]
                        logcat_changed = True
                elif kind=="console_line":
                    kind_name, line = payload
                    target = self.fastboot_console if kind_name.startswith("fastboot") else self.adb_console
                    self._console_write(target, line)
                elif kind=="adb_log_end":
                    logging.info("Live logcat reader ended; process return code: %s", payload)
                    if self.adb_log_window and self.adb_log_window.winfo_exists():
                        self.adb_log_lines.append("[DroidLab] Logcat stream ended (return code: %s)" % payload)
                        logcat_changed = True
                elif kind=="adb_result":
                    kind_name,code,output=payload
                    self._append(("Fastboot " if kind_name=="fastboot_manual" else "ADB ")+kind_name+" result:\n"+output)
                    if kind_name in ("shell","fastboot_manual"):
                        self._append_adb_output(output)
                    if kind_name.startswith("fastboot"):
                        self.status.set("Fastboot operation finished (exit code %s)" % code)
                        if kind_name == "fastboot_maintenance":
                            self.maintenance_status.set("Completed" if code == 0 else "Failed")
                        if kind_name == "fastboot_flash":
                            self.status.set("Fastboot flash completed" if code == 0 else "Fastboot flash failed")
                    elif kind_name=="bugreport":
                        report_path=getattr(self,"_bugreport_target_path","")
                        if code==0 and report_path and Path(report_path).is_file():
                            messagebox.showinfo("Bugreport complete","Saved archive ("+str(Path(report_path).stat().st_size)+" bytes):\n"+report_path+"\n\nBugreports may contain sensitive device data.")
                        elif code==0: messagebox.showwarning("Bugreport status","ADB returned success, but the expected archive was not found. Check output:\n"+output)
                        else: messagebox.showerror("Bugreport failed",output)
                    elif kind_name=="sideload":
                        if code==0:messagebox.showinfo("Sideload complete","ADB reported that the sideload command completed.")
                        else:messagebox.showerror("Sideload failed",output)
                    elif kind_name=="logcat_file":
                        if code==0:messagebox.showinfo("Logcat saved",output)
                        else:messagebox.showerror("Save logcat failed",output)
                elif kind=="parts":self._load_parts(payload)
                elif kind=="info_done":self._busy(False);self.status.set(payload)
                elif kind=="done":self._busy(False);self.status.set(payload);messagebox.showinfo("Extraction finished",payload)
                elif kind in ("error","cancelled"):
                    self._busy(False);self.status.set("Extraction failed" if kind=="error" else "Cancelled");self._append(("ERROR: " if kind=="error" else "CANCELLED: ")+payload)
                    if kind=="error":messagebox.showerror("Operation failed",payload)
        except queue.Empty:pass
        if logcat_changed and not self.adb_log_paused:
            self._render_adb_logcat()
        self.after(100,self._poll_events)
if __name__ == "__main__":
    _configure_logging()
    sys.excepthook = _log_uncaught_exception
    try:
        PayloadDumperApp().mainloop()
    except Exception:
        logging.exception("Fatal exception in application main loop")
        raise
    finally:
        logging.info("==== %s stopped ====", APP_NAME)
        logging.shutdown()
