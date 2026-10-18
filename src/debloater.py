"""Conservative Android package-management helpers for DroidLab.

Community risk profiles supply context only; local core-package protections remain
authoritative, and unknown packages are never assumed safe.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
import threading


CORE_EXACT = {
    "android",
    "com.android.systemui",
    "com.android.settings",
    "com.android.phone",
    "com.android.server.telecom",
    "com.android.shell",
    "com.android.permissioncontroller",
    "com.android.providers.settings",
    "com.android.providers.telephony",
    "com.android.providers.media",
    "com.google.android.gms",
    "com.google.android.gsf",
}
CORE_PREFIXES = (
    "com.android.providers.",
    "com.android.systemui.",
    "com.android.permissioncontroller.",
)
RISKY_SYSTEM_EXACT = {
    "com.android.vending",
    "com.google.android.googlequicksearchbox",
    "com.google.android.packageinstaller",
    "com.android.packageinstaller",
}


PROFILE_FILES = ("android.json", "samsung.json", "realme.json", "xiaomi.json")
_PROFILE_RISK_WEIGHT = {"low": 1, "review": 2, "high": 3}


def _profile_directory(profile_dir=None) -> Path:
    if profile_dir is not None:
        return Path(profile_dir)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "safety-db"
    return Path(__file__).resolve().parent.parent / "resources" / "safety-db"


def load_package_profiles(profile_dir=None) -> dict[str, dict]:
    """Load attributed offline community profiles, choosing the stricter duplicate risk."""
    root = _profile_directory(profile_dir)
    profiles: dict[str, dict] = {}
    for filename in PROFILE_FILES:
        path = root / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        entries = payload.get("packages", {}) if isinstance(payload, dict) else {}
        if not isinstance(entries, dict):
            continue
        vendor = str(meta.get("vendor", filename.removesuffix(".json")))
        for package, record in entries.items():
            if not isinstance(package, str) or not isinstance(record, dict):
                continue
            package = package.strip().lower()
            is_exact = bool(re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+|android", package))
            is_prefix_pattern = package.endswith(".*") and bool(
                re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package[:-2])
            )
            if not (is_exact or is_prefix_pattern):
                continue
            risk = str(record.get("risk", "")).strip().lower()
            if risk not in _PROFILE_RISK_WEIGHT:
                continue
            item = {
                "risk": risk,
                "category": str(record.get("category", "Unknown")),
                "reason": str(record.get("reason", "Community profile contains no explanation.")),
                "recommended": bool(record.get("recommended", False)),
                "source": vendor,
                "source_file": filename,
            }
            previous = profiles.get(package)
            if previous:
                sources = list(previous.get("sources", [previous.get("source", "")]))
                if vendor not in sources:
                    sources.append(vendor)
                item["sources"] = sources
                if _PROFILE_RISK_WEIGHT[previous["risk"]] > _PROFILE_RISK_WEIGHT[risk]:
                    previous["sources"] = sources
                    continue
            else:
                item["sources"] = [vendor]
            profiles[package] = item
    return profiles


PACKAGE_PROFILES = load_package_profiles()


def get_package_profile(package: str) -> dict:
    """Return the strictest exact/prefix guidance without inferring a safety guarantee."""
    name = package.strip().lower()
    exact = PACKAGE_PROFILES.get(name)
    if exact:
        return dict(exact)
    matches = [
        (pattern, record) for pattern, record in PACKAGE_PROFILES.items()
        if pattern.endswith(".*") and name.startswith(pattern[:-1])
    ]
    if not matches:
        return {}
    matches.sort(key=lambda item: _PROFILE_RISK_WEIGHT.get(item[1].get("risk", ""), 0), reverse=True)
    result = dict(matches[0][1])
    sources = sorted({source for _, record in matches for source in record.get("sources", [])})
    result["sources"] = sources
    result["matched_pattern"] = matches[0][0]
    return result


class DebloaterError(RuntimeError):
    """Raised when a device or package-management operation fails."""


def _hidden_windows_options():
    """Avoid a console window flash when invoking command-line tools on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def run_adb(binary: str, args: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **_hidden_windows_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise DebloaterError(f"ADB command timed out after {timeout}s: {args!r}") from exc
    except OSError as exc:
        raise DebloaterError(f"Unable to start ADB: {exc}") from exc
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise DebloaterError(output or f"ADB exited with status {result.returncode}: {args!r}")
    return result.stdout or ""


def parse_devices(output: str) -> list[dict[str, str]]:
    devices = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] in {
            "device", "unauthorized", "offline", "recovery", "sideload"
        }:
            props = {}
            for item in fields[2:]:
                if ":" in item:
                    key, value = item.split(":", 1)
                    props[key] = value
            devices.append({
                "serial": fields[0],
                "state": fields[1],
                "model": props.get("model", "").replace("_", " "),
                "product": props.get("product", ""),
                "transport_id": props.get("transport_id", ""),
            })
    return devices


def parse_package_list(output: str) -> dict[str, str]:
    """Return package name -> APK path, accepting common pm output variants."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        entry = line[len("package:"):]
        apk_path = ""
        if "=" in entry:
            apk_path, entry = entry.rsplit("=", 1)
        package = entry.strip()
        if package and re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+|android", package):
            result[package] = apk_path.strip()
    return result


def is_core_protected(package: str) -> bool:
    name = package.strip().lower()
    return name in CORE_EXACT or any(
        name == prefix.rstrip(".") or name.startswith(prefix)
        for prefix in CORE_PREFIXES
    )


def classify_package(package: str, package_type: str) -> tuple[str, str]:
    name = package.strip().lower()
    if is_core_protected(name):
        return "PROTECTED", "Core Android component; package actions are blocked."
    if name in RISKY_SYSTEM_EXACT:
        profile = get_package_profile(name)
        return "HIGH RISK", profile.get("reason", "Important store/installer/search component; review dependencies carefully.")
    profile = get_package_profile(name)
    if profile:
        label = {"high": "HIGH RISK", "review": "REVIEW", "low": "LOW RISK"}[profile["risk"]]
        return label, profile["reason"]
    if package_type == "User":
        return "USER APP", "Installed for a user; verify app data and dependencies before removal."
    return "UNKNOWN", "No bundled profile matched this package. Unknown does not mean safe."


def scan_device(binary: str, serial: str) -> dict:
    base = ["-s", serial, "shell"]
    props_output = run_adb(binary, [*base, "getprop"], timeout=30)
    properties = {}
    for line in props_output.splitlines():
        match = re.match(r"^\[([^\]]+)\]: \[(.*)\]$", line.strip())
        if match:
            properties[match.group(1)] = match.group(2)

    package_paths = parse_package_list(
        run_adb(binary, [*base, "pm", "list", "packages", "-f"], timeout=90)
    )
    try:
        system_names = set(parse_package_list(
            run_adb(binary, [*base, "pm", "list", "packages", "-s"], timeout=60)
        ))
    except DebloaterError:
        system_names = set()
    try:
        user_names = set(parse_package_list(
            run_adb(binary, [*base, "pm", "list", "packages", "-3"], timeout=60)
        ))
    except DebloaterError:
        user_names = set()

    # Prefer package-manager flags; paths are only a fallback on older Android builds.
    packages = []
    for name, apk_path in package_paths.items():
        if name in system_names:
            package_type = "System"
        elif name in user_names:
            package_type = "User"
        elif apk_path.startswith(("/system/", "/product/", "/vendor/", "/system_ext/", "/odm/", "/apex/")):
            package_type = "System"
        else:
            package_type = "Unknown"
        risk, reason = classify_package(name, package_type)
        profile = get_package_profile(name)
        packages.append({
            "name": name, "path": apk_path, "type": package_type,
            "risk": risk, "reason": reason,
            "category": profile.get("category", "Unknown"),
            "profile_source": profile.get("source", ""),
            "profile_sources": profile.get("sources", []),
            "profile_recommended": profile.get("recommended", False),
        })
    packages.sort(key=lambda item: (item["type"], item["name"].casefold()))
    brand = properties.get("ro.product.brand", "Unknown")
    model = properties.get("ro.product.model", "Unknown")
    android_version = properties.get("ro.build.version.release", "Unknown")
    sdk = properties.get("ro.build.version.sdk", "Unknown")
    battery = ""
    try:
        output = run_adb(binary, [*base, "dumpsys", "battery"], timeout=20)
        level = re.search(r"(?m)^\s*level:\s*(\d+)", output)
        temp = re.search(r"(?m)^\s*temperature:\s*(\d+)", output)
        status = re.search(r"(?m)^\s*status:\s*(\d+)", output)
        status_label = {"2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
        if level:
            battery = f" · Battery {level.group(1)}%"
            if temp:
                battery += f" · {int(temp.group(1)) / 10:.1f}°C"
            if status:
                battery += f" · {status_label.get(status.group(1), 'Status unknown')}"
    except DebloaterError:
        pass
    summary = f"{brand} {model} · Android {android_version} (SDK {sdk}) · {serial}{battery}"
    return {"packages": packages, "summary": summary, "properties": properties}


def _rescue_dir() -> Path:
    return Path.home() / "DroidLab" / "rescue"


def write_rescue_point(serial: str, action: str, packages: list[str], device_summary: str = "") -> Path:
    folder = _rescue_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    safe_serial = re.sub(r"[^A-Za-z0-9_.-]+", "_", serial)
    path = folder / f"rescue-{safe_serial}-{stamp}.json"
    payload = {
        "format": "droidlab-rescue-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "serial": serial,
        "device": device_summary,
        "action": action,
        "packages": sorted(set(packages)),
        "note": "This file saves package names, not APK files or user data. install-existing only restores packages still available on the device.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_package_selection(path: str) -> list[str]:
    raw = Path(path).read_text(encoding="utf-8-sig")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if isinstance(parsed, dict):
        parsed = parsed.get("packages", [])
    if not isinstance(parsed, list):
        raise DebloaterError("Selection/rescue file must contain a package list.")
    names = sorted({
        str(item).strip()
        for item in parsed
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+|android", item.strip())
    })
    if not names:
        raise DebloaterError("No valid package names were found in this file.")
    return names


def perform_package_action(binary: str, serial: str, action: str, packages: list[str],
                           device_summary: str = "", progress=None,
                           allow_advanced: bool = False) -> dict:
    if action not in {"uninstall", "disable", "enable", "restore"}:
        raise DebloaterError(f"Unsupported package action: {action}")
    unique = list(dict.fromkeys(packages))
    blocked = [p for p in unique if is_core_protected(p)]
    blocked_advanced = [
        p for p in unique
        if p not in blocked and action in {"uninstall", "disable"}
        and get_package_profile(p).get("risk") == "high" and not allow_advanced
    ]
    targets = [p for p in unique if p not in blocked and p not in blocked_advanced]
    if not targets:
        return {"action": action, "succeeded": [], "failed": [],
                "blocked": blocked, "blocked_advanced": blocked_advanced,
                "rescue_path": ""}
    rescue_path = None
    if action in {"uninstall", "disable"}:
        rescue_path = write_rescue_point(serial, action, targets, device_summary)
        if progress:
            progress(f"Rescue point saved before changes: {rescue_path}")

    succeeded, failed = [], []
    for package in targets:
        if action == "uninstall":
            args = ["-s", serial, "shell", "pm", "uninstall", "--user", "0", package]
        elif action == "disable":
            args = ["-s", serial, "shell", "pm", "disable-user", "--user", "0", package]
        elif action == "enable":
            args = ["-s", serial, "shell", "pm", "enable", "--user", "0", package]
        else:
            args = ["-s", serial, "shell", "cmd", "package", "install-existing", "--user", "0", package]
        try:
            output = run_adb(binary, args, timeout=60).strip()
            if action == "restore" and ("Unknown command" in output or "not found" in output.lower()):
                output = run_adb(binary, ["-s", serial, "shell", "pm", "install-existing", "--user", "0", package], timeout=60).strip()
            if any(word in output.lower() for word in ("failure", "unknown package", "not installed")):
                raise DebloaterError(output)
            succeeded.append(package)
            if progress:
                progress(f"{action.upper()} OK · {package}" + (f" · {output}" if output else ""))
        except DebloaterError as exc:
            failed.append((package, str(exc)))
            if progress:
                progress(f"{action.upper()} FAILED · {package} · {exc}")
    return {"action": action, "succeeded": succeeded, "failed": failed,
            "blocked": blocked, "blocked_advanced": blocked_advanced,
            "rescue_path": str(rescue_path) if rescue_path else ""}


class DebloaterTab:
    """Tk UI for offline-first package listing and guarded user-0 actions."""

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.packages = []
        self.summary = ""
        self.devices = {}
        self.busy = False
        self.filter_var = tk.StringVar()
        self.risk_filter_var = tk.StringVar(value="All risks")
        self.device_var = tk.StringVar()
        self.advanced_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Refresh devices, select an authorized device, then scan packages.")
        self._build()

    def _build(self):
        p = self.parent
        p.columnconfigure(0, weight=1)
        p.rowconfigure(3, weight=1)
        ttk.Label(p, text="Android App Manager", font=("Sans", 21, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(p, text="Inspect installed packages, review conservative risk labels, and manage apps for Android user 0.",
                  style="Muted.TLabel", wraplength=1100).grid(row=1, column=0, sticky="w", pady=(4, 10))
        top = ttk.Frame(p, style="Panel.TFrame", padding=10)
        top.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(0, weight=1)
        row = ttk.Frame(top, style="Panel.TFrame")
        row.grid(row=0, column=0, sticky="ew")
        self.device_combo = ttk.Combobox(row, textvariable=self.device_var, state="readonly")
        self.device_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Refresh devices", command=self.refresh_devices).pack(side="left", padx=(7, 0))
        self.scan_button = ttk.Button(row, text="Scan packages", command=self.scan)
        self.scan_button.pack(side="left", padx=(7, 0))
        self.summary_label = ttk.Label(top, textvariable=self.status_var, background="#1f2937",
                                       foreground="#cbd5e1", wraplength=1100, justify="left")
        self.summary_label.grid(row=1, column=0, sticky="w", pady=(7, 0))

        main = ttk.Frame(p, style="Panel.TFrame", padding=10)
        main.grid(row=3, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        filter_row = ttk.Frame(main, style="Panel.TFrame")
        filter_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(filter_row, text="Search packages", background="#1f2937", foreground="#93c5fd").pack(side="left", padx=(0, 8))
        search = ttk.Entry(filter_row, textvariable=self.filter_var)
        search.pack(side="left", fill="x", expand=True)
        self.count_label = ttk.Label(filter_row, text="0 packages", background="#1f2937", foreground="#cbd5e1")
        self.count_label.pack(side="right", padx=(10, 0))
        self.risk_combo = ttk.Combobox(filter_row, textvariable=self.risk_filter_var, state="readonly", width=14,
            values=("All risks", "PROTECTED", "HIGH RISK", "REVIEW", "LOW RISK", "USER APP", "UNKNOWN"))
        self.risk_combo.pack(side="right", padx=(8, 0))
        self.filter_var.trace_add("write", lambda *_: self.render_packages())
        self.risk_filter_var.trace_add("write", lambda *_: self.render_packages())

        columns = ("package", "type", "risk", "path")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("package", text="Package name")
        self.tree.heading("type", text="Type")
        self.tree.heading("risk", text="Risk")
        self.tree.heading("path", text="APK path")
        self.tree.column("package", width=280, minwidth=200, stretch=True)
        self.tree.column("type", width=90, anchor="center", stretch=False)
        self.tree.column("risk", width=115, anchor="center", stretch=False)
        self.tree.column("path", width=420, minwidth=180, stretch=True)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.show_package_details)
        actions = ttk.Frame(main, style="Panel.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for text, cmd in (
            ("Select visible", self.select_visible),
            ("Clear selection", self.clear_selection),
            ("Package details…", self.show_package_details),
            ("Uninstall for user 0…", lambda: self.act("uninstall")),
            ("Disable…", lambda: self.act("disable")),
            ("Enable", lambda: self.act("enable")),
            ("Export selection…", self.export_selection),
            ("Import selection…", self.import_selection),
            ("Restore rescue file…", self.restore_rescue),
        ):
            ttk.Button(actions, text=text, command=cmd).pack(side="left", padx=(0, 5))
        ttk.Button(actions, text="Select low-risk candidates", command=self.select_low_risk_candidates).pack(side="left", padx=(0, 5))
        ttk.Checkbutton(actions, text="Advanced Mode — allow system/high-risk changes",
                        variable=self.advanced_var, style="TCheckbutton").pack(side="right")
        warning = ttk.Label(p, text=f"Community risk guidance: {len(PACKAGE_PROFILES)} package profiles loaded. Ratings may be outdated or inaccurate; LOW RISK is not a guarantee. Core components are blocked. System or high-risk changes require Advanced Mode. Rescue files store names, not APKs or user data.",
                            foreground="#fecaca", background="#2a1d27", wraplength=1100, justify="left", padding=9)
        warning.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(p, textvariable=self.status_var, style="Muted.TLabel").grid(row=5, column=0, sticky="w", pady=(6, 0))

    def _binary(self):
        binary = self.app._tool_path("adb.exe" if os.name == "nt" else "adb") or self.app._tool_path("adb")
        if not binary:
            messagebox.showerror("ADB not found", "DroidLab could not find adb.exe/adb. Use the bundled platform-tools or install Android SDK Platform-Tools.")
            return None
        return binary

    def _post(self, kind, payload):
        self.app.events.put(("debloat_" + kind, payload))

    def refresh_devices(self):
        binary = self._binary()
        if not binary or self.busy:
            return
        self.busy = True
        self.status_var.set("Detecting ADB devices…")
        threading.Thread(target=self._refresh_worker, args=(binary,), daemon=True).start()

    def _refresh_worker(self, binary):
        try:
            output = run_adb(binary, ["devices", "-l"], timeout=15)
            self._post("devices", parse_devices(output))
        except DebloaterError as exc:
            self._post("error", str(exc))

    def scan(self):
        if self.busy:
            return
        selected = self.devices.get(self.device_var.get())
        if not selected:
            messagebox.showwarning("Select device", "Refresh devices and select a connected Android device first.")
            return
        if selected["state"] != "device":
            messagebox.showwarning("Device not ready", f"Device state is {selected['state']}. Unlock the phone and authorize USB debugging.")
            return
        binary = self._binary()
        if not binary:
            return
        self.busy = True
        self.status_var.set("Reading device details and installed packages…")
        threading.Thread(target=self._scan_worker, args=(binary, selected["serial"]), daemon=True).start()

    def _scan_worker(self, binary, serial):
        try:
            result = scan_device(binary, serial)
            result["serial"] = serial
            self._post("scan", result)
        except DebloaterError as exc:
            self._post("error", str(exc))

    def render_packages(self):
        query = self.filter_var.get().strip().casefold()
        risk_filter = self.risk_filter_var.get()
        for item in self.tree.get_children():
            self.tree.delete(item)
        visible = [
            p for p in self.packages
            if (not query or any(query in str(p.get(key, "")).casefold()
                                 for key in ("name", "type", "risk", "path", "category", "reason", "profile_source")))
            and (risk_filter == "All risks" or p.get("risk") == risk_filter)
        ]
        for package in visible:
            self.tree.insert("", "end", iid=package["name"],
                             values=(package["name"], package["type"], package["risk"], package["path"]))
        self.count_label.configure(text=f"{len(visible)} shown / {len(self.packages)} total")

    def show_package_details(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            if _event is not None:
                name = self.tree.identify_row(_event.y)
                if name:
                    selected = (name,)
        if not selected:
            return
        name = selected[0]
        package = next((item for item in self.packages if item["name"] == name), {"name": name})
        sources = package.get("profile_sources") or ([package.get("profile_source")] if package.get("profile_source") else [])
        source_text = ", ".join(sources) if sources else "No community profile matched"
        recommended = "Yes — still review device-specific dependencies" if package.get("profile_recommended") else "No"
        detail = (
            f"Package: {package.get('name', name)}\n"
            f"Type: {package.get('type', 'Unknown')}\n"
            f"Risk guidance: {package.get('risk', 'UNKNOWN')}\n"
            f"Category: {package.get('category', 'Unknown')}\n\n"
            f"Guidance: {package.get('reason', 'No package guidance available.')}\n\n"
            f"Profile source: {source_text}\n"
            f"Community profile marks as a removal candidate: {recommended}\n\n"
            f"APK path: {package.get('path', 'Unknown')}\n\n"
            "Community ratings are informational, may not match your firmware version, and never guarantee safe removal."
        )
        messagebox.showinfo("Package details", detail, parent=self.parent)

    def select_visible(self):
        self.tree.selection_set(self.tree.get_children())

    def clear_selection(self):
        self.tree.selection_remove(self.tree.selection())

    def select_low_risk_candidates(self):
        visible_names = self.tree.get_children()
        candidates = {
            package["name"] for package in self.packages
            if package["name"] in visible_names
            and package.get("risk") == "LOW RISK"
            and package.get("profile_recommended")
        }
        if not candidates:
            messagebox.showinfo("No profile candidates",
                "No visible packages are marked as low-risk candidates by the bundled community profiles. Clear filters or review package details.",
                parent=self.parent)
            return
        self.tree.selection_set(sorted(candidates))
        self.status_var.set(f"Selected {len(candidates)} low-risk profile candidate(s). Review each package before applying an action.")

    def _selected_packages(self):
        return list(self.tree.selection())

    def act(self, action):
        packages = self._selected_packages()
        if not packages:
            messagebox.showwarning("No packages selected", "Select one or more packages first.")
            return
        if action in {"uninstall", "disable"}:
            package_by_name = {item["name"]: item for item in self.packages}
            system_selected = [p for p in packages if package_by_name.get(p, {}).get("type") == "System"]
            high_risk_selected = [p for p in packages if package_by_name.get(p, {}).get("risk") == "HIGH RISK"]
            if (system_selected or high_risk_selected) and not self.advanced_var.get():
                reasons = []
                if system_selected:
                    reasons.append(f"{len(system_selected)} system package(s)")
                if high_risk_selected:
                    reasons.append(f"{len(high_risk_selected)} high-risk package(s)")
                messagebox.showwarning("Advanced Mode required",
                    "Your selection includes " + " and ".join(reasons) + ". Review package details and enable Advanced Mode only if you accept the device-specific risk.")
                return
            title = "Confirm package removal" if action == "uninstall" else "Confirm package disable"
            verb = "uninstall for Android user 0" if action == "uninstall" else "disable for Android user 0"
            sample = "\n".join(packages[:18]) + ("\n…" if len(packages) > 18 else "")
            if not messagebox.askyesno(title,
                f"This will {verb} for {len(packages)} selected package(s) on {self.device_var.get()}.\n\n"
                f"Packages:\n{sample}\n\nDroidLab will save a package-name rescue point first. This does not back up APK files or user data. Continue?",
                icon="warning"):
                return
        elif action in {"enable", "restore"}:
            if not messagebox.askyesno("Confirm package restore", f"Attempt to enable/restore {len(packages)} package(s) on {self.device_var.get()}?"):
                return
        selected = self.devices.get(self.device_var.get())
        if not selected or selected["state"] != "device":
            messagebox.showwarning("Device not ready", "Refresh devices and select an authorized device.")
            return
        binary = self._binary()
        if not binary:
            return
        self.busy = True
        threading.Thread(target=self._action_worker,
            args=(binary, selected["serial"], action, packages, self.advanced_var.get()), daemon=True).start()

    def _action_worker(self, binary, serial, action, packages, allow_advanced=False):
        try:
            result = perform_package_action(binary, serial, action, packages, self.summary,
                progress=lambda line: self._post("progress", line), allow_advanced=allow_advanced)
            self._post("action", result)
        except DebloaterError as exc:
            self._post("error", str(exc))

    def export_selection(self):
        packages = self._selected_packages()
        if not packages:
            messagebox.showwarning("No selection", "Select packages to export first.")
            return
        path = filedialog.asksaveasfilename(title="Export package selection", defaultextension=".json",
            initialfile="droidlab-package-selection.json", filetypes=[("JSON selection", "*.json"), ("Text files", "*.txt")])
        if not path:
            return
        try:
            Path(path).write_text(json.dumps({"format": "droidlab-selection-v1", "packages": packages}, indent=2), encoding="utf-8")
            self.app._append(f"Exported {len(packages)} selected package names to {path}")
            self.status_var.set(f"Exported {len(packages)} package names.")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))

    def import_selection(self):
        path = filedialog.askopenfilename(title="Import package selection", filetypes=[("Selection / rescue files", "*.json *.txt"), ("All files", "*")])
        if not path:
            return
        try:
            names = read_package_selection(path)
        except (OSError, UnicodeError, DebloaterError) as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        available = {p["name"] for p in self.packages}
        matched = [name for name in names if name in available]
        for item in self.tree.selection():
            self.tree.selection_remove(item)
        query = self.filter_var.get()
        self.filter_var.set("")
        self.tree.selection_set([name for name in matched if self.tree.exists(name)])
        self.status_var.set(f"Imported {len(matched)} of {len(names)} package names that are currently installed.")
        if len(matched) < len(names):
            self.app._append(f"Selection import: {len(names)-len(matched)} package(s) are not in the current scan.")

    def restore_rescue(self):
        path = filedialog.askopenfilename(title="Choose DroidLab rescue point", filetypes=[("Rescue/selection JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            packages = read_package_selection(path)
        except (OSError, UnicodeError, DebloaterError) as exc:
            messagebox.showerror("Restore failed", str(exc))
            return
        selected = self.devices.get(self.device_var.get())
        if not selected or selected["state"] != "device":
            messagebox.showwarning("Device not ready", "Refresh devices and select an authorized device.")
            return
        if not messagebox.askyesno("Restore rescue point",
            f"Attempt to restore {len(packages)} package(s) on {selected['serial']} with install-existing --user 0?\n\n"
            "This works only when the package is still present on the device. It does not restore deleted APK files or user data.", icon="warning"):
            return
        binary = self._binary()
        if not binary:
            return
        self.busy = True
        threading.Thread(target=self._action_worker, args=(binary, selected["serial"], "restore", packages), daemon=True).start()

    def handle_event(self, kind, payload):
        if kind == "debloat_devices":
            self.busy = False
            self.devices = {f"{d['serial']} [{d['state']}]": d for d in payload}
            labels = list(self.devices)
            self.device_combo.configure(values=labels)
            current = self.device_var.get()
            if current not in self.devices:
                ready = next((label for label, d in self.devices.items() if d["state"] == "device"), "")
                self.device_var.set(ready or (labels[0] if len(labels) == 1 else ""))
            self.status_var.set("No ADB devices found." if not labels else f"{len(labels)} device(s) detected. Select an authorized device and scan.")
            self.app._append("Debloater device scan: " + (", ".join(labels) if labels else "no devices"))
        elif kind == "debloat_scan":
            self.busy = False
            self.packages = payload["packages"]
            self.summary = payload["summary"]
            self.status_var.set(self.summary)
            self.render_packages()
            self.app._append(f"Debloater scan: found {len(self.packages)} packages for {payload['serial']}.")
        elif kind == "debloat_progress":
            self.app._append("Debloater: " + str(payload))
            self.status_var.set(str(payload))
        elif kind == "debloat_action":
            self.busy = False
            action = payload["action"]
            succeeded = payload["succeeded"]
            failed = payload["failed"]
            blocked = payload.get("blocked", [])
            blocked_advanced = payload.get("blocked_advanced", [])
            detail = (f"{action.title()} complete: {len(succeeded)} succeeded, {len(failed)} failed, "
                      f"{len(blocked)} core-protected, {len(blocked_advanced)} high-risk blocked by safety mode.")
            if payload.get("rescue_path"):
                detail += f"\nRescue point: {payload['rescue_path']}"
            self.status_var.set(detail)
            self.app._append("Debloater: " + detail)
            if failed:
                self.app._append("Failed packages: " + "; ".join(f"{p}: {err}" for p, err in failed))
            messagebox.showinfo("Package operation finished", detail)
            # Refresh list after changes, preserving the selected target.
            self.scan()
        elif kind == "debloat_error":
            self.busy = False
            self.status_var.set("Debloater operation failed.")
            self.app._append("Debloater error: " + str(payload))
            messagebox.showerror("Debloater operation failed", str(payload))
