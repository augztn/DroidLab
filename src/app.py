#!/usr/bin/env python3
"""Payload Dumper Linux desktop UI."""
from __future__ import annotations
import os, queue, threading, tkinter as tk
import subprocess
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD
from pathlib import Path
from payload_dumper import BackendError, extract, inspect_source, sha256_file

APP_NAME = "Payload Dumper"  # Change this single value when rebranding the app.
class PayloadDumperApp(TkinterDnD.Tk):
    def __init__(self):
        super().__init__(); self.title(APP_NAME); self.geometry("1440x900"); self.minsize(1024,700); self.configure(bg="#111827")
        self.events=queue.Queue(); self.worker=None; self.cancel_event=threading.Event()
        self.source=tk.StringVar(); self.output=tk.StringVar(value=str(Path.home()/APP_NAME)); self.fingerprint=tk.BooleanVar(value=True); self.status=tk.StringVar(value="Ready")
        self.parts={}; self.part_vars={}; self._build(); self.after(100,self._poll_events)
    def _build(self):
        s=ttk.Style(self)
        try:s.theme_use("clam")
        except tk.TclError:pass
        s.configure("TFrame",background="#111827"); s.configure("Panel.TFrame",background="#1f2937"); s.configure("TLabel",background="#111827",foreground="#e5e7eb",font=("Sans",12)); s.configure("Title.TLabel",font=("Sans",28,"bold"),foreground="#f9fafb"); s.configure("Muted.TLabel",foreground="#cbd5e1"); s.configure("TButton",padding=(13,9),font=("Sans",11)); s.configure("TCheckbutton",background="#1f2937",foreground="#f9fafb",font=("Sans",12)); s.configure("TEntry",padding=5,font=("Sans",11)); s.configure("TCombobox",padding=5,font=("Sans",10)); s.configure("Treeview",background="#0b1220",fieldbackground="#0b1220",foreground="#f1f5f9",rowheight=34,font=("Sans",11)); s.configure("Treeview.Heading",background="#374151",foreground="#ffffff",font=("Sans",10,"bold"),padding=(7,7)); s.map("Treeview",background=[("selected","#1d4ed8")],foreground=[("selected","#ffffff")])
        root=ttk.Frame(self,padding=16); root.pack(fill="both",expand=True); h=ttk.Frame(root); h.pack(fill="x",pady=(0,10))
        ttk.Label(h,text=APP_NAME,style="Title.TLabel").pack(anchor="w"); ttk.Label(h,text="Android OTA partition extractor · Linux desktop",style="Muted.TLabel").pack(anchor="w",pady=(3,0))
        src=ttk.Frame(root,style="Panel.TFrame",padding=10); src.pack(fill="x",pady=(0,7)); ttk.Label(src,text="INPUT FILE PATH — TYPE OR PASTE THE FULL PATH",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); row=ttk.Frame(src,style="Panel.TFrame"); row.pack(fill="x",pady=(6,0)); self.source_entry=ttk.Entry(row,textvariable=self.source); self.source_entry.pack(side="left",fill="x",expand=True,ipady=4); self.source_entry.bind("<Return>",lambda _event:self._apply_manual_source_path()); ttk.Button(row,text="Browse…",command=self._browse_source).pack(side="left",padx=(7,0)); actions=ttk.Frame(src,style="Panel.TFrame"); actions.pack(fill="x",pady=(7,0)); ttk.Button(actions,text="Use typed path",command=self._apply_manual_source_path).pack(side="left"); ttk.Button(actions,text="Inspect partitions",command=self._inspect).pack(side="left",padx=(7,0))
        out=ttk.Frame(root,style="Panel.TFrame",padding=10); out.pack(fill="x",pady=(0,7)); ttk.Label(out,text="OUTPUT DIRECTORY",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); row=ttk.Frame(out,style="Panel.TFrame"); row.pack(fill="x",pady=(6,0)); ttk.Entry(row,textvariable=self.output).pack(side="left",fill="x",expand=True,ipady=4); ttk.Button(row,text="Choose…",command=self._browse_output).pack(side="left",padx=(7,0))
        ttk.Label(out,text="MOUNTED REMOVABLE DRIVES",background="#1f2937",foreground="#93c5fd").pack(anchor="w",pady=(9,0)); drive_row=ttk.Frame(out,style="Panel.TFrame"); drive_row.pack(fill="x",pady=(5,0)); self.storage_var=tk.StringVar(); self.storage_combo=ttk.Combobox(drive_row,textvariable=self.storage_var,state="readonly"); self.storage_combo.pack(fill="x",expand=True); drive_actions=ttk.Frame(out,style="Panel.TFrame"); drive_actions.pack(fill="x",pady=(6,0)); ttk.Button(drive_actions,text="Refresh drives",command=self._refresh_storage_locations).pack(side="left"); ttk.Button(drive_actions,text="Browse selected drive",command=self._browse_selected_storage).pack(side="left",padx=(7,0)); ttk.Button(drive_actions,text="Use selected drive for output",command=self._use_selected_storage).pack(side="left",padx=(7,0));
        middle=ttk.Frame(root); middle.pack(fill="both",expand=True,pady=(0,10)); panel=ttk.Frame(middle,style="Panel.TFrame",padding=10); panel.pack(side="left",fill="both",expand=True,padx=(0,10)); ttk.Label(panel,text="PARTITIONS (no selection = all)",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); actions=ttk.Frame(panel,style="Panel.TFrame"); actions.pack(fill="x",pady=5); ttk.Button(actions,text="Select all",command=lambda:self._select_all(True)).pack(side="left"); ttk.Button(actions,text="Select none",command=lambda:self._select_all(False)).pack(side="left",padx=5)
        self.tree=ttk.Treeview(panel,columns=("size",),show="tree headings",selectmode="none"); self.tree.heading("#0",text="Partition"); self.tree.heading("size",text="Size"); self.tree.column("#0",width=260,minwidth=180,stretch=True); self.tree.column("size",width=120,minwidth=100,anchor="e",stretch=False); self.tree.pack(fill="both",expand=True); self.tree.bind("<Button-1>",self._toggle)
        side=ttk.Frame(middle,style="Panel.TFrame",padding=16,width=310); side.pack(side="right",fill="y"); side.pack_propagate(False); ttk.Label(side,text="OPTIONS",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); ttk.Checkbutton(side,text="Generate SHA-256\nfingerprints",variable=self.fingerprint).pack(anchor="w",pady=(12,4)); ttk.Label(side,text="Fingerprints are separate\nfrom the backend’s OTA\nverification.",background="#1f2937",foreground="#9ca3af",justify="left").pack(anchor="w",pady=8); self.start_button=ttk.Button(side,text="Extract payload",command=self._start); self.start_button.pack(fill="x",pady=(8,5)); self.cancel_button=ttk.Button(side,text="Cancel",command=self._cancel,state="disabled"); self.cancel_button.pack(fill="x")
        logbox=ttk.Frame(root,style="Panel.TFrame",padding=10); logbox.pack(fill="x",expand=False); ttk.Label(logbox,text="ACTIVITY LOG",background="#1f2937",foreground="#93c5fd").pack(anchor="w"); self.log=tk.Text(logbox,bg="#0b1220",fg="#d1d5db",insertbackground="white",relief="flat",wrap="word",font=("Monospace",10),height=4); self.log.pack(fill="x",expand=False,pady=6); self.log.configure(state="disabled"); self.progress=ttk.Progressbar(logbox,mode="indeterminate"); self.progress.pack(fill="x",pady=(2,0)); ttk.Label(root,textvariable=self.status,style="Muted.TLabel").pack(anchor="w",pady=(7,0))
        self._refresh_storage_locations(); self._enable_drop(self); self._enable_drop(src); self._enable_drop(self.source_entry); self._enable_drop(root); self._append("Drag payload.bin or an OTA .zip onto the window, or type/paste its full path and choose Use typed path. Inspect to select individual partitions."); self._append("Backend: payload-dumper-go (Apache-2.0). Some delta OTA operations are unsupported.")
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
                if kind=="log":self._append(payload)
                elif kind=="parts":self._load_parts(payload)
                elif kind=="info_done":self._busy(False);self.status.set(payload)
                elif kind=="done":self._busy(False);self.status.set(payload);messagebox.showinfo("Extraction finished",payload)
                elif kind in ("error","cancelled"):
                    self._busy(False);self.status.set("Extraction failed" if kind=="error" else "Cancelled");self._append(("ERROR: " if kind=="error" else "CANCELLED: ")+payload)
                    if kind=="error":messagebox.showerror("Operation failed",payload)
        except queue.Empty:pass
        self.after(100,self._poll_events)
if __name__=="__main__":PayloadDumperApp().mainloop()
