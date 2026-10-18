#!/usr/bin/env python3
"""DroidLab Linux desktop UI."""
from __future__ import annotations
import os, queue, threading, tkinter as tk, shutil, re, shlex, sys
import subprocess, webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD
from pathlib import Path
from payload_dumper import BackendError, extract, inspect_source, sha256_file
from debloater import DebloaterTab

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
class PayloadDumperApp(TkinterDnD.Tk):
    def __init__(self):
        super().__init__(); self.title(APP_NAME); self.geometry("1440x900"); self.minsize(1024,700); self.configure(bg="#111827")
        self.events=queue.Queue(); self.worker=None; self.cancel_event=threading.Event()
        menu=tk.Menu(self)
        help_menu=tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="About DroidLab", command=self._show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)
        self.source=tk.StringVar(); self.output=tk.StringVar(value=str(Path.home()/APP_NAME)); self.fingerprint=tk.BooleanVar(value=True); self.status=tk.StringVar(value="Ready")
        self.parts={}; self.part_vars={}; self._build(); self.after(100,self._poll_events)
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
        s.configure("TFrame",background="#111827"); s.configure("Panel.TFrame",background="#1f2937"); s.configure("TLabel",background="#111827",foreground="#e5e7eb",font=("Sans",13)); s.configure("Title.TLabel",font=("Sans",23,"bold"),foreground="#f9fafb"); s.configure("Muted.TLabel",foreground="#cbd5e1"); s.configure("TButton",padding=(14,10),font=("Sans",12)); s.configure("TCheckbutton",background="#1f2937",foreground="#f9fafb",font=("Sans",13)); s.configure("Large.TCheckbutton",background="#1f2937",foreground="#f9fafb",font=("Sans",15),padding=(2,5)); s.configure("Large.TButton",padding=(16,13),font=("Sans",14,"bold")); s.configure("TEntry",padding=7,font=("Sans",12)); s.configure("TCombobox",padding=7,font=("Sans",12)); s.configure("Treeview",background="#0b1220",fieldbackground="#0b1220",foreground="#f1f5f9",rowheight=38,font=("Sans",12)); s.configure("Treeview.Heading",background="#374151",foreground="#ffffff",font=("Sans",12,"bold"),padding=(9,9)); s.map("Treeview",background=[("selected","#1d4ed8")],foreground=[("selected","#ffffff")])
        notebook=ttk.Notebook(self); notebook.pack(fill="both",expand=True,padx=8,pady=8); root=ttk.Frame(notebook,padding=16); notebook.add(root,text="Payload Extractor"); flash_tab=ttk.Frame(notebook,padding=16); notebook.add(flash_tab,text="Fastboot & Flashing"); self._build_flash_tab(flash_tab); adb_tab=ttk.Frame(notebook,padding=16); notebook.add(adb_tab,text="ADB & Sideload"); self._build_adb_tab(adb_tab); debloat_tab=ttk.Frame(notebook,padding=16); notebook.add(debloat_tab,text="App Manager"); self.debloater=DebloaterTab(debloat_tab,self); h=ttk.Frame(root); h.pack(fill="x",pady=(0,10))
        ttk.Label(h,text=APP_NAME,style="Title.TLabel").pack(anchor="w"); ttk.Label(h,text="Android firmware toolkit · " + ("Windows desktop" if os.name == "nt" else "Linux desktop"),style="Muted.TLabel").pack(anchor="w",pady=(3,0))
        src=ttk.Frame(root,style="Panel.TFrame",padding=10); src.pack(fill="x",pady=(0,7)); ttk.Label(src,text="INPUT FILE PATH — TYPE OR PASTE THE FULL PATH",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); row=ttk.Frame(src,style="Panel.TFrame"); row.pack(fill="x",pady=(6,0)); self.source_entry=ttk.Entry(row,textvariable=self.source); self.source_entry.pack(side="left",fill="x",expand=True,ipady=4); self.source_entry.bind("<Return>",lambda _event:self._apply_manual_source_path()); ttk.Button(row,text="Browse…",command=self._browse_source).pack(side="left",padx=(7,0)); actions=ttk.Frame(src,style="Panel.TFrame"); actions.pack(fill="x",pady=(7,0)); ttk.Button(actions,text="Use typed path",command=self._apply_manual_source_path).pack(side="left"); ttk.Button(actions,text="Inspect partitions",command=self._inspect).pack(side="left",padx=(7,0))
        out=ttk.Frame(root,style="Panel.TFrame",padding=10); out.pack(fill="x",pady=(0,7)); ttk.Label(out,text="OUTPUT DIRECTORY",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); row=ttk.Frame(out,style="Panel.TFrame"); row.pack(fill="x",pady=(5,0)); ttk.Entry(row,textvariable=self.output).pack(side="left",fill="x",expand=True,ipady=4); ttk.Button(row,text="Choose…",command=self._browse_output).pack(side="left",padx=(7,0))
        # Compact mounted-drive controls share the output panel to save vertical space for partitions.
        drive_row=ttk.Frame(out,style="Panel.TFrame"); drive_row.pack(fill="x",pady=(5,0)); ttk.Label(drive_row,text="Mounted drive",background="#1f2937",foreground="#cbd5e1").pack(side="left",padx=(0,8)); self.storage_var=tk.StringVar(); self.storage_combo=ttk.Combobox(drive_row,textvariable=self.storage_var,state="readonly"); self.storage_combo.pack(side="left",fill="x",expand=True); ttk.Button(drive_row,text="Refresh",command=self._refresh_storage_locations).pack(side="left",padx=(7,0)); ttk.Button(drive_row,text="Browse drive",command=self._browse_selected_storage).pack(side="left",padx=(7,0)); ttk.Button(drive_row,text="Use for output",command=self._use_selected_storage).pack(side="left",padx=(7,0));
        middle=ttk.Frame(root); middle.pack(fill="both",expand=True,pady=(0,10)); panel=ttk.Frame(middle,style="Panel.TFrame",padding=10); panel.pack(side="left",fill="both",expand=True,padx=(0,10)); ttk.Label(panel,text="PARTITIONS (no selection = all)",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); actions=ttk.Frame(panel,style="Panel.TFrame"); actions.pack(fill="x",pady=5); ttk.Button(actions,text="Select all",command=lambda:self._select_all(True)).pack(side="left"); ttk.Button(actions,text="Select none",command=lambda:self._select_all(False)).pack(side="left",padx=5)
        self.tree=ttk.Treeview(panel,columns=("size",),show="tree headings",selectmode="none"); self.tree.heading("#0",text="Partition"); self.tree.heading("size",text="Size"); self.tree.column("#0",width=420,minwidth=260,stretch=True); self.tree.column("size",width=150,minwidth=130,anchor="e",stretch=False); self.tree.pack(fill="both",expand=True); self.tree.bind("<Button-1>",self._toggle)
        side=ttk.Frame(middle,style="Panel.TFrame",padding=22,width=420); side.pack(side="right",fill="y"); side.pack_propagate(False); ttk.Label(side,text="OPTIONS",background="#1f2937",foreground="#93c5fd",font=("Sans",16,"bold")).pack(anchor="w",pady=(0,8)); ttk.Separator(side).pack(fill="x",pady=(0,10)); ttk.Checkbutton(side,text="Generate SHA-256 fingerprints",variable=self.fingerprint,style="Large.TCheckbutton").pack(anchor="w",pady=(10,8)); ttk.Label(side,text="Creates a SHA-256 checksum for each extracted partition. This is separate from the backend’s OTA verification.",background="#1f2937",foreground="#cbd5e1",justify="left",wraplength=360,font=("Sans",12)).pack(anchor="w",pady=(4,16)); self.start_button=ttk.Button(side,text="Extract payload",command=self._start,style="Large.TButton"); self.start_button.pack(fill="x",pady=(8,8),ipady=5); self.cancel_button=ttk.Button(side,text="Cancel",command=self._cancel,state="disabled",style="Large.TButton"); self.cancel_button.pack(fill="x",ipady=5)
        logbox=ttk.Frame(root,style="Panel.TFrame",padding=10); logbox.pack(fill="x",expand=False); ttk.Label(logbox,text="ACTIVITY LOG",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); self.log=tk.Text(logbox,bg="#0b1220",fg="#d1d5db",insertbackground="white",relief="flat",wrap="word",font=("Monospace",11),height=4); self.log.pack(fill="x",expand=False,pady=6); self.log.configure(state="disabled"); self.progress=ttk.Progressbar(logbox,mode="indeterminate"); self.progress.pack(fill="x",pady=(2,0)); ttk.Label(root,textvariable=self.status,style="Muted.TLabel").pack(anchor="w",pady=(7,0))
        self._refresh_storage_locations(); self._enable_drop(self); self._enable_drop(src); self._enable_drop(self.source_entry); self._enable_drop(root); self._append("Drag payload.bin or an OTA .zip onto the window, or type/paste its full path and choose Use typed path. Inspect to select individual partitions."); self._append("Backend: payload-dumper-go (Apache-2.0). Some delta OTA operations are unsupported.")
    def _build_flash_tab(self, tab):
        tab.columnconfigure(0, weight=7, uniform="flash_columns")
        tab.columnconfigure(1, weight=4, uniform="flash_columns")
        tab.rowconfigure(2, weight=1)

        ttk.Label(tab, text="Fastboot & Flashing", font=("Sans", 22, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="Drop an image to detect its likely partition. Review all details before flashing.", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Keep flashing controls in their own column so the command reference never overlaps them.
        controls = ttk.Frame(tab)
        controls.grid(row=2, column=0, sticky="nsew", padx=(0, 12))
        controls.columnconfigure(0, weight=1)

        box = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(box, text="IMAGE FILE", background="#1f2937", foreground="#93c5fd", font=("Sans", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        self.flash_image = tk.StringVar()
        self.flash_entry = ttk.Entry(row, textvariable=self.flash_image)
        self.flash_entry.pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(row, text="Browse…", command=self._browse_flash_image).pack(side="left", padx=(8, 0))
        self._enable_drop(box)
        self._enable_drop(self.flash_entry)
        self._enable_drop(tab)

        device = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        device.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(device, text="FASTBOOT DEVICE", background="#1f2937", foreground="#93c5fd", font=("Sans", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(device, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        self.fastboot_device = tk.StringVar()
        self.device_combo = ttk.Combobox(row, textvariable=self.fastboot_device, state="readonly")
        self.device_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Refresh devices", command=self._refresh_fastboot_devices).pack(side="left", padx=(8, 0))
        ttk.Label(device, text="Target partition", background="#1f2937", foreground="#93c5fd").pack(anchor="w", pady=(10, 0))
        self.flash_partition = tk.StringVar()
        self.partition_combo = ttk.Combobox(
            device, textvariable=self.flash_partition,
            values=("boot", "vendor", "vendor_boot", "init_boot", "dtbo", "vbmeta", "recovery", "system", "product", "odm", "system_ext", "vbmeta_system", "vbmeta_vendor"),
            state="normal",
        )
        self.partition_combo.pack(fill="x", pady=(5, 0))

        preview = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        preview.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(preview, text="COMMAND PREVIEW", background="#1f2937", foreground="#93c5fd", font=("Sans", 10, "bold")).pack(anchor="w")
        self.flash_command = tk.StringVar(value="Select an image and Fastboot device to preview the command.")
        ttk.Label(preview, textvariable=self.flash_command, background="#0b1220", foreground="#f1f5f9", padding=10, wraplength=650, justify="left").pack(fill="x", pady=(6, 0))

        ttk.Label(
            controls,
            text="FLASHING RISK  ·  An incompatible image or incorrect partition can stop Android from booting. Confirm the image matches your exact device and verify the target partition before flashing.",
            foreground="#fecaca", background="#2a1d27", wraplength=900, justify="left",
            font=("Sans", 10), padding=12,
        ).grid(row=3, column=0, sticky="ew", pady=(2, 12))
        row = ttk.Frame(controls)
        row.grid(row=4, column=0, sticky="w")
        ttk.Button(row, text="Preview command", command=self._update_flash_preview).pack(side="left")
        ttk.Button(row, text="Flash image…", command=self._flash_image).pack(side="left", padx=(8, 0))

        # Executable destructive maintenance actions; each action requires explicit confirmation.
        reference = ttk.Frame(tab, style="Panel.TFrame", padding=14)
        reference.grid(row=2, column=1, sticky="nsew")
        ttk.Label(reference, text="ERASING & FORMATTING", background="#1f2937", foreground="#fb923c", font=("Sans", 13, "bold")).pack(anchor="w")
        ttk.Label(reference, text="Destructive actions permanently remove data. Confirm the selected device before continuing.", background="#1f2937", foreground="#fecaca", justify="left", wraplength=440, font=("Sans", 11)).pack(anchor="w", pady=(8, 12))
        ttk.Button(reference, text="Erase cache", command=lambda: self._run_fastboot_maintenance("erase", "cache")).pack(fill="x", pady=3)
        ttk.Button(reference, text="Erase userdata", command=lambda: self._run_fastboot_maintenance("erase", "userdata")).pack(fill="x", pady=3)
        ttk.Button(reference, text="Wipe data (fastboot -w)", command=lambda: self._run_fastboot_maintenance("-w")).pack(fill="x", pady=3)
        self.maintenance_command = tk.StringVar(value="Select an operation to preview its command.")
        ttk.Label(reference, text="COMMAND", background="#1f2937", foreground="#93c5fd").pack(anchor="w", pady=(14, 5))
        ttk.Label(reference, textvariable=self.maintenance_command, background="#0b1220", foreground="#f1f5f9", padding=9, justify="left", wraplength=520, font=("Monospace", 10)).pack(fill="x")
        self.maintenance_status = tk.StringVar(value="No maintenance command executed.")
        ttk.Label(reference, textvariable=self.maintenance_status, background="#1f2937", foreground="#cbd5e1", wraplength=520, justify="left", font=("Sans", 10)).pack(anchor="w", pady=(10, 0))

        self.flash_image.trace_add("write", lambda *_: self._on_flash_image_changed())
        self.flash_partition.trace_add("write", lambda *_: self._update_flash_preview())
        self.fastboot_device.trace_add("write", lambda *_: self._update_flash_preview())

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
        ttk.Label(device,textvariable=self.adb_device_status,background="#1f2937",foreground="#cbd5e1",wraplength=1000).pack(anchor="w",pady=(5,0))
        lower=ttk.Frame(tab); lower.grid(row=3,column=0,sticky="nsew"); lower.columnconfigure(0,weight=1); lower.rowconfigure(2,weight=1)
        actions=ttk.Frame(lower); actions.grid(row=0,column=0,sticky="ew",pady=(0,10))
        power=ttk.Frame(actions,style="Panel.TFrame",padding=10); power.pack(side="left",fill="both",expand=True,padx=(0,8))
        ttk.Label(power,text="POWER & REBOOTING",background="#1f2937",foreground="#93c5fd").pack(anchor="w",pady=(0,6))
        row=ttk.Frame(power,style="Panel.TFrame"); row.pack(fill="x")
        ttk.Button(row,text="Reboot",command=lambda:self._adb_reboot("")).pack(side="left",padx=(0,4))
        ttk.Button(row,text="Bootloader",command=lambda:self._adb_reboot("bootloader")).pack(side="left",padx=4)
        ttk.Button(row,text="Recovery",command=lambda:self._adb_reboot("recovery")).pack(side="left",padx=4)
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
        self.adb_shell_command=tk.StringVar(value="dumpsys battery"); ttk.Entry(row,textvariable=self.adb_shell_command).pack(side="left",fill="x",expand=True,ipady=4)
        ttk.Button(row,text="Run shell command",command=self._run_adb_shell).pack(side="left",padx=(7,0))
        self.adb_output=tk.Text(shell,height=7,bg="#0b1220",fg="#d1d5db",insertbackground="white",relief="flat",wrap="word",font=("Monospace",10)); self.adb_output.grid(row=2,column=0,sticky="nsew")
        self.adb_output.insert("end","Select a device, then run dumpsys battery or another trusted shell command.\n"); self.adb_output.configure(state="disabled")
        self.adb_log_process = None
        self.adb_log_window = None
        self.adb_log_text = None
        self.adb_log_filter = tk.StringVar()
        self.adb_log_paused = False
        self.adb_log_lines = []

    def _refresh_adb_devices(self):
        binary=self._tool_path("adb.exe" if os.name == "nt" else "adb") or self._tool_path("adb")
        if not binary: messagebox.showerror("ADB not found","Install Android SDK Platform-Tools and ensure adb is on PATH."); return
        try: result=subprocess.run([binary,"devices","-l"],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=10,check=False,**_windows_process_options())
        except (OSError,subprocess.TimeoutExpired) as exc: messagebox.showerror("ADB scan failed",str(exc)); return
        targets={}
        for line in result.stdout.splitlines()[1:]:
            fields=line.split()
            if len(fields)>=2 and fields[1] in ("device","sideload","recovery","unauthorized","offline"):
                label=fields[0]+" ["+fields[1]+"]"; targets[label]=(fields[0],fields[1])
        self._adb_targets=targets; labels=list(targets); self.adb_device_combo["values"]=labels
        self.adb_device.set(labels[0] if len(labels)==1 else (self.adb_device.get() if self.adb_device.get() in targets else ""))
        self.adb_device_status.set(result.stdout.strip() or "No devices reported by adb.")
        self._append("ADB scan: "+(", ".join(labels) if labels else "no devices found"))

    def _adb_require(self, states=("device",)):
        binary = self._tool_path("adb.exe" if os.name == "nt" else "adb") or self._tool_path("adb")
        if not binary: messagebox.showerror("ADB not found","Install Android SDK Platform-Tools and ensure adb is on PATH."); return None
        target=getattr(self,"_adb_targets",{}).get(self.adb_device.get())
        if not target: messagebox.showwarning("Select device","Refresh devices and select a device."); return None
        serial,state=target
        if state not in states: messagebox.showwarning("Device not ready","Device state is "+state+". Expected: "+", ".join(states)+"."); return None
        return binary,serial

    def _adb_reboot(self, mode):
        selected=self._adb_require(("device","recovery"))
        if not selected:return
        binary,serial=selected; label=mode or "normal"; command=[binary,"-s",serial,"reboot"]+([mode] if mode else [])
        if not messagebox.askyesno("Confirm reboot","Reboot "+serial+" into "+label+" mode?"):return
        try:
            result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=20,check=False,**_windows_process_options()); output=(result.stdout+"\n"+result.stderr).strip()
            self._append("ADB reboot "+label+": "+(output or "exit status "+str(result.returncode)))
            if result.returncode:messagebox.showerror("ADB reboot failed",output or "adb exit status "+str(result.returncode))
            else:self.adb_device_status.set("Reboot command sent. Refresh after the device changes mode.")
        except (OSError,subprocess.TimeoutExpired) as exc:messagebox.showerror("ADB reboot failed",str(exc))

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
        """Open a media-aware ZIP picker rooted at the user's home, not /."""
        dialog = tk.Toplevel(self)
        dialog.title("Choose OTA ZIP to sideload")
        dialog.geometry("900x620")
        dialog.minsize(700, 460)
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(3, weight=1)

        ttk.Label(dialog, text="Choose an OTA ZIP", font=("Sans", 16, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        shortcuts = ttk.Frame(dialog)
        shortcuts.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        ttk.Label(shortcuts, text="Media / mount drive:").pack(side="left", padx=(0, 6))
        drive_var = tk.StringVar()
        drive_combo = ttk.Combobox(shortcuts, textvariable=drive_var, state="readonly", width=42)
        drive_combo.pack(side="left", fill="x", expand=True)

        path_row = ttk.Frame(dialog)
        path_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        path_var = tk.StringVar(value=str(Path.home()))
        path_entry = ttk.Entry(path_row, textvariable=path_var)
        path_entry.pack(side="left", fill="x", expand=True, ipady=4)
        file_list = tk.Listbox(dialog, bg="#0b1220", fg="#e5e7eb", selectbackground="#2563eb", font=("Sans", 12), activestyle="dotbox")
        file_list.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=file_list.yview)
        scrollbar.grid(row=3, column=1, sticky="ns", pady=(0, 8))
        file_list.configure(yscrollcommand=scrollbar.set)
        footer = ttk.Frame(dialog)
        footer.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        selected_var = tk.StringVar()
        ttk.Label(footer, textvariable=selected_var).pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Go to folder", command=lambda: populate(path_var.get())).pack(side="left", padx=4)
        ttk.Button(footer, text="Up", command=lambda: populate(str(Path(path_var.get()).expanduser().parent))).pack(side="left", padx=4)
        ttk.Button(footer, text="Refresh mounts", command=lambda: load_drives()).pack(side="left", padx=4)
        ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right", padx=(4, 0))
        ttk.Button(footer, text="Select ZIP", command=lambda: accept_selected()).pack(side="right", padx=4)

        entries = []
        def load_drives():
            choices = [("Home folder", str(Path.home()))]
            user = os.environ.get("USER", Path.home().name)
            roots = [Path("/run/media") / user, Path("/media") / user, Path("/mnt"), Path("/media"), Path("/run/media")]
            for root in roots:
                try:
                    if root.is_dir():
                        if root in (Path("/media"), Path("/run/media")):
                            for child in root.iterdir():
                                if child.is_dir():
                                    choices.append((f"Mounted: {child.name}", str(child)))
                        else:
                            for child in root.iterdir():
                                if child.is_dir():
                                    choices.append((f"Mounted: {child.name}", str(child)))
                except OSError:
                    pass
            for label, path in getattr(self, "_storage_locations", []):
                choices.append((label, path))
            unique = []
            seen = set()
            for label, path in choices:
                try:
                    p = str(Path(path).expanduser().resolve())
                except OSError:
                    continue
                if p not in seen and Path(p).is_dir() and os.access(p, os.R_OK | os.X_OK):
                    seen.add(p)
                    unique.append((label, p))
            drive_choices[:] = unique
            drive_combo["values"] = [f"{label} — {path}" for label, path in unique]
            if unique and not drive_var.get():
                drive_var.set(drive_combo["values"][0])

        def on_drive_selected(_event=None):
            idx = drive_combo.current()
            if 0 <= idx < len(drive_choices):
                populate(drive_choices[idx][1])

        def populate(directory):
            nonlocal entries
            try:
                folder = Path(directory).expanduser().resolve()
                if not folder.is_dir():
                    raise NotADirectoryError(str(folder))
                items = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
                entries = [p for p in items if (p.is_dir() or (p.is_file() and p.suffix.lower() == ".zip"))]
            except (OSError, RuntimeError) as exc:
                messagebox.showerror("Cannot open folder", str(exc), parent=dialog)
                return
            path_var.set(str(folder))
            file_list.delete(0, "end")
            for p in entries:
                file_list.insert("end", ("📁 " if p.is_dir() else "📦 ") + p.name)
            selected_var.set(f"{len(entries)} folders / ZIP files")
            try:
                for idx, (_label, drive_path) in enumerate(drive_choices):
                    if Path(drive_path).resolve() == folder:
                        drive_combo.current(idx)
                        break
            except (OSError, RuntimeError):
                pass

        def accept_selected():
            selection = file_list.curselection()
            if not selection:
                messagebox.showwarning("Select ZIP", "Select an OTA .zip file from the list.", parent=dialog)
                return
            chosen = entries[selection[0]]
            if chosen.is_dir():
                populate(chosen)
                return
            if chosen.suffix.lower() != ".zip":
                messagebox.showwarning("OTA ZIP required", "Choose a .zip update package.", parent=dialog)
                return
            self.sideload_file.set(str(chosen.resolve()))
            self.adb_device_status.set("OTA ZIP selected: " + chosen.name)
            self._append("OTA ZIP selected for sideload: " + str(chosen.resolve()))
            dialog.destroy()

        drive_choices = []
        drive_combo.bind("<<ComboboxSelected>>", on_drive_selected)
        file_list.bind("<Double-Button-1>", lambda _event: accept_selected())
        path_entry.bind("<Return>", lambda _event: populate(path_var.get()))
        load_drives()
        populate(str(Path.home()))

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

    def _adb_task(self, command, timeout, kind):
        try:
            result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout,check=False,**_windows_process_options())
            output=(result.stdout+"\n"+result.stderr).strip() or "Exit status: "+str(result.returncode)
            self.events.put(("adb_result",(kind,result.returncode,output)))
        except (OSError,subprocess.TimeoutExpired) as exc:self.events.put(("adb_result",(kind,-1,str(exc))))

    def _run_adb_shell(self):
        selected=self._adb_require(("device",))
        if not selected:return
        text=self.adb_shell_command.get().strip()
        if not text:messagebox.showwarning("Shell command required","Enter a command such as dumpsys battery.");return
        risky=re.compile(r"(^|[;&|]\s*)(rm\s+-rf|reboot|poweroff|shutdown|mkfs\b|dd\b|format\b|wipe\b|setprop\b|pm\s+(uninstall|disable-user)|svc\s+.*disable)",re.I)
        if risky.search(text) and not messagebox.askyesno("Confirm potentially destructive shell command","This command may change device state or delete data:\n\n"+text+"\n\nRun it on "+self.adb_device.get()+"?",icon="warning"):
            self._append("ADB shell command cancelled by user.");return
        binary,serial=selected; self._append("Running adb shell command on "+serial+": "+text)
        threading.Thread(target=self._adb_task,args=([binary,"-s",serial,"shell",text],90,"shell"),daemon=True).start()

    def _append_adb_output(self, text):
        self.adb_output.configure(state="normal"); self.adb_output.insert("end",text+"\n"); self.adb_output.see("end"); self.adb_output.configure(state="disabled")

    def _start_adb_logcat(self):
        selected=self._adb_require(("device",))
        if not selected:return
        if self.adb_log_process and self.adb_log_process.poll() is None:messagebox.showinfo("Logcat running","Stop the current logcat window first.");return
        binary,serial=selected
        window=tk.Toplevel(self); window.title("DroidLab Live Logcat - "+serial); window.geometry("1000x650"); window.configure(bg="#111827")
        toolbar=ttk.Frame(window); toolbar.pack(fill="x",padx=8,pady=(8,0))
        ttk.Label(toolbar,text="Filter").pack(side="left",padx=(0,6))
        self.adb_log_filter=tk.StringVar()
        ttk.Entry(toolbar,textvariable=self.adb_log_filter).pack(side="left",fill="x",expand=True)
        text=tk.Text(window,bg="#0b1220",fg="#d1d5db",wrap="none",font=("Monospace",10),state="disabled")
        text.pack(fill="both",expand=True,padx=8,pady=8)
        self.adb_log_window,self.adb_log_text=window,text
        self.adb_log_lines=[]; self.adb_log_paused=False
        self.adb_log_filter.trace_add("write",lambda *_:self._render_adb_logcat())
        try:self.adb_log_process=subprocess.Popen([binary,"-s",serial,"logcat"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",bufsize=1,**_windows_process_options())
        except OSError as exc:window.destroy(); self.adb_log_window=self.adb_log_text=None; messagebox.showerror("Logcat failed",str(exc)); return
        buttons=ttk.Frame(window); buttons.pack(fill="x",padx=8,pady=(0,8))
        self.adb_log_pause_button=ttk.Button(buttons,text="Pause display",command=self._toggle_adb_logcat_pause); self.adb_log_pause_button.pack(side="left")
        ttk.Button(buttons,text="Save visible log…",command=self._save_visible_adb_logcat).pack(side="left",padx=6)
        ttk.Button(buttons,text="Stop logcat",command=self._stop_adb_logcat).pack(side="right")
        process=self.adb_log_process
        def reader():
            try:
                if process.stdout:
                    for line in process.stdout:self.events.put(("adb_log_line",line.rstrip("\n")))
            finally:self.events.put(("adb_log_end",None))
        threading.Thread(target=reader,daemon=True).start(); window.protocol("WM_DELETE_WINDOW",self._stop_adb_logcat)

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
        path=filedialog.asksaveasfilename(title="Save filtered logcat",defaultextension=".txt",initialfile="logcat-filtered.txt",filetypes=[("Text files","*.txt"),("All files","*")])
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
        path=filedialog.asksaveasfilename(title="Save Android logcat",defaultextension=".txt",initialfile="logs.txt",filetypes=[("Text files","*.txt"),("All files","*")])
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
        path=filedialog.asksaveasfilename(title="Save Android bugreport",defaultextension=".zip",initialfile="bugreport.zip",filetypes=[("ZIP archive","*.zip"),("All files","*")])
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
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False, **_windows_process_options())
            output = (result.stdout + "\n" + result.stderr).strip()
            self._append(output or ("Fastboot exit status: " + str(result.returncode)))
            if result.returncode == 0:
                self.maintenance_status.set("Completed: " + label)
                messagebox.showinfo("Fastboot operation complete", label + " completed successfully.\n\n" + (output or "fastboot exit status 0"))
            else:
                self.maintenance_status.set("Failed: " + label)
                messagebox.showerror("Fastboot operation failed", output or "fastboot exit status " + str(result.returncode))
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.maintenance_status.set("Failed: " + label)
            messagebox.showerror("Fastboot operation failed", str(exc))

    def _on_flash_image_changed(self):
        path = Path(self.flash_image.get().strip().strip(chr(34)).strip(chr(39)))
        mapping = {"boot.img":"boot", "vendor.img":"vendor", "vendor_boot.img":"vendor_boot", "init_boot.img":"init_boot", "dtbo.img":"dtbo", "vbmeta.img":"vbmeta", "recovery.img":"recovery", "system.img":"system", "product.img":"product", "odm.img":"odm", "system_ext.img":"system_ext", "vbmeta_system.img":"vbmeta_system", "vbmeta_vendor.img":"vbmeta_vendor"}
        if path.name.lower() in mapping: self.flash_partition.set(mapping[path.name.lower()]); self._append("Filename suggests partition " + mapping[path.name.lower()] + "; verify compatibility before flashing.")
        self._update_flash_preview()

    def _browse_flash_image(self):
        path = filedialog.askopenfilename(title="Choose image to flash", filetypes=[("Android image", "*.img"), ("All files", "*")])
        if path: self.flash_image.set(path)

    def _refresh_fastboot_devices(self):
        binary = self._tool_path("fastboot.exe" if os.name == "nt" else "fastboot") or self._tool_path("fastboot")
        if not binary: messagebox.showerror("Fastboot not found", "Install Android platform-tools and ensure fastboot is on PATH."); return
        try: result = subprocess.run([binary, "devices", "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, check=False, **_windows_process_options())
        except (OSError, subprocess.TimeoutExpired) as exc: messagebox.showerror("Device detection failed", str(exc)); return
        devices = [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "fastboot"]
        self.device_combo["values"] = devices; self.fastboot_device.set(devices[0] if len(devices) == 1 else "")
        self._append("Fastboot scan: " + (", ".join(devices) if devices else "no devices found"))

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
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False, **_windows_process_options()); output = (result.stdout + "\n" + result.stderr).strip()
            for line in output.splitlines(): self._append(line)
            if result.returncode == 0: self.status.set("Fastboot flash completed"); messagebox.showinfo("Flash complete", "Fastboot reported success for " + part + ". Verify the device before rebooting.")
            else: self.status.set("Fastboot failed"); messagebox.showerror("Flash failed", output or "fastboot exit status " + str(result.returncode))
        except (OSError, subprocess.TimeoutExpired) as exc: self.status.set("Fastboot operation failed"); messagebox.showerror("Fastboot error", str(exc))
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
    def _append(self,msg):self.log.configure(state="normal"); self.log.insert("end",str(msg)+"\n"); self.log.see("end"); self.log.configure(state="disabled")
    def _busy(self,value):self.start_button.configure(state="disabled" if value else "normal"); self.cancel_button.configure(state="normal" if value else "disabled"); self.progress.start(12) if value else self.progress.stop()
    def _browse_source(self):
        p=filedialog.askopenfilename(title="Choose Android OTA package",filetypes=[("Android OTA","*.bin *.zip"),("All files","*")])
        if p:
            self.source.set(p); self.parts.clear(); self.part_vars.clear()
            for i in self.tree.get_children():self.tree.delete(i)
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
        p=filedialog.askdirectory(title="Choose output directory",initialdir=self.output.get() or str(Path.home()))
        if p:self.output.set(p)
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
        try:
            while True:
                kind,payload=self.events.get_nowait()
                if kind.startswith("debloat_"):
                    self.debloater.handle_event(kind,payload)
                elif kind=="log":self._append(payload)
                elif kind=="adb_log_line":
                    if self.adb_log_text and self.adb_log_window and self.adb_log_window.winfo_exists():
                        self.adb_log_lines.append(payload)
                        if len(self.adb_log_lines)>10000: del self.adb_log_lines[:len(self.adb_log_lines)-10000]
                        if not self.adb_log_paused: self._render_adb_logcat()
                elif kind=="adb_result":
                    kind_name,code,output=payload
                    self._append("ADB "+kind_name+" result:\n"+output)
                    if kind_name=="shell":self._append_adb_output(output)
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
        self.after(100,self._poll_events)
if __name__=="__main__":PayloadDumperApp().mainloop()
