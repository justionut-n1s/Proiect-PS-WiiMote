#!/usr/bin/env python3
# =========================================================
# WiiMote Virtual Controller Server (ViGEm Bridge) + Modern UI
# =========================================================

from __future__ import annotations
from PIL import Image, ImageTk
import atexit
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "server_config.json")
LOCK_FILE   = os.path.join(BASE_DIR, "server.lock")
LOG_FILE    = os.path.join(BASE_DIR, "server.log")
DISCOVERY_PORT = 5006
PAIR_PORT = 5007
DEFAULT_DATA_PORT = 5005

BRIDGE_ADDR = ("127.0.0.1", 9876)
BRIDGE_PATH = os.path.join(BASE_DIR, "bridge", "WiimoteBridge.exe")

MAX_CONTROLLERS = 4
TIMEOUT_SEC = 2.0

# Wii Sports profile (proven)
DT_MIN = 1 / 500
DT_MAX = 1 / 40

CF_ALPHA = 0.985
ACC_G_TOL = 1.2
DRIFT_ERR_THRESH = 0.03
DRIFT_STEP = (1 - CF_ALPHA)

ANGLE_DEADZONE = 0.008
PITCH_TO_STICK = 0.30
YAW_RATE_TO_STICK = 1.9

INVERT_RX = True
INVERT_RY = True

RECENTER_HOLD_SEC = 0.60

# Pointer ranges (radians) for gyro→pointer mapping
YAW_RANGE_RAD = math.radians(22.0)     # left/right span
PITCH_RANGE_RAD = math.radians(15.0)   # up/down span

SWING_THRESHOLD = 2.3
SWING_COOLDOWN = 0.25

WII_TO_BRIDGE = {
    "A": "A",
    "B": "B",
    "1": "X",
    "2": "Y",
    "+": "START",
    "-": "BACK",
    "HOME": "GUIDE",
    "UP": "UP",
    "DOWN": "DOWN",
    "LEFT": "LEFT",
    "RIGHT": "RIGHT",
}

# Theme
COL_BG = "#121417"
COL_CARD = "#1B1F26"
COL_CARD2 = "#151A20"
COL_BORDER = "#2A313B"
COL_TEXT = "#E8EEF7"
COL_MUTED = "#A7B0C0"
COL_BLUE = "#4C8DFF"
COL_GREEN = "#33D17A"
COL_RED = "#FF4D4D"

LOG_LOCK = threading.Lock()

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with LOG_LOCK:
        print(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except:
            pass

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"data_port": DEFAULT_DATA_PORT, "active_slot": 1}

def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except:
        pass

CONFIG = load_config()
DATA_PORT = int(CONFIG.get("data_port", DEFAULT_DATA_PORT))
ACTIVE_SLOT_FOR_BRIDGE = int(CONFIG.get("active_slot", 1))
ACTIVE_SLOT_FOR_BRIDGE = max(1, min(MAX_CONTROLLERS, ACTIVE_SLOT_FOR_BRIDGE))

def _windows_pid_exists(pid: int) -> bool:
    try:
        out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"], text=True, encoding="utf-8", errors="ignore")
        return str(pid) in out
    except:
        return True

def acquire_lock() -> None:
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                pid = int((f.read() or "0").strip() or "0")
        except:
            pid = 0

        stale = False
        if pid > 0 and os.name == "nt":
            stale = not _windows_pid_exists(pid)
        elif pid > 0:
            try:
                os.kill(pid, 0)
                stale = False
            except:
                stale = True

        if stale:
            try:
                os.remove(LOCK_FILE)
                log(f"[LOCK] Removed stale lock from PID {pid}.")
            except:
                pass
        else:
            log("[ERROR] Server already running.")
            sys.exit(1)

    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    log(f"[LOCK] Acquired (PID {os.getpid()}).")

def release_lock() -> None:
    try:
        os.remove(LOCK_FILE)
        log("[LOCK] Released.")
    except:
        pass

def get_ipv4() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return None

@dataclass
class MotionState:
    last_ts: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    pitch0: float = 0.0
    yaw0: float = 0.0
    home_hold: Optional[float] = None
    last_swing: float = 0.0

@dataclass
class ControllerState:
    device_id: str
    username: str
    addr: Tuple[str, int]
    token: str
    buttons: Dict[str, int] = field(default_factory=dict)
    gyro: Dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    accel: Dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    motion: MotionState = field(default_factory=MotionState)

controllers: Dict[int, Optional[ControllerState]] = {i: None for i in range(MAX_CONTROLLERS)}
last_seen: Dict[int, float] = {i: 0.0 for i in range(MAX_CONTROLLERS)}
pkt_count: Dict[int, int] = {i: 0 for i in range(MAX_CONTROLLERS)}
hz_est: Dict[int, float] = {i: 0.0 for i in range(MAX_CONTROLLERS)}
_last_hz_t: float = time.time()

ACTIVE_LOCK = threading.Lock()

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def maybe_deg_to_rad(v: float) -> float:
    return v * math.pi / 180.0 if abs(v) > 20 else v

def accel_to_pitch(ax: float, ay: float, az: float) -> float:
    denom = math.sqrt(ay * ay + az * az)
    if denom < 1e-6:
        denom = 1e-6
    return math.atan2(-ax, denom)

def apply_deadzone(x: float, dz: float) -> float:
    return 0.0 if abs(x) < dz else x

def start_bridge() -> None:
    log(f"[DEBUG] BASE_DIR = {BASE_DIR}")
    log(f"[DEBUG] BRIDGE_PATH = {BRIDGE_PATH}")

    if not os.path.exists(BRIDGE_PATH):
        log(f"[ERROR] WiimoteBridge.exe NOT FOUND at: {BRIDGE_PATH}")
        return

    try:
        subprocess.Popen([BRIDGE_PATH], creationflags=subprocess.CREATE_NEW_CONSOLE)
        log("[BRIDGE] Started.")
    except Exception as e:
        log(f"[BRIDGE] Failed: {e!r}")
        
    if not os.path.exists(BRIDGE_PATH):
        log(f"[WARN] WiimoteBridge.exe not found: {BRIDGE_PATH}")
        return
    try:
        subprocess.Popen([BRIDGE_PATH], creationflags=subprocess.CREATE_NEW_CONSOLE)
        log("[BRIDGE] Started.")
    except Exception as e:
        log(f"[BRIDGE] Failed: {e!r}")

def send_to_bridge(msg: str) -> None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(msg.encode("utf-8"), BRIDGE_ADDR)
        s.close()
    except:
        pass

def discovery_loop(stop_evt: threading.Event) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while not stop_evt.is_set():
        msg = json.dumps({"type": "DISCOVERY", "pair_port": PAIR_PORT, "data_port": int(DATA_PORT)}).encode("utf-8")
        try:
            s.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
        except:
            pass
        stop_evt.wait(1.0)

def pair_server(stop_evt: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", PAIR_PORT))
    log(f"[PAIR] Listening on :{PAIR_PORT}")

    while not stop_evt.is_set():
        try:
            sock.settimeout(0.25)
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except:
            continue

        try:
            j = json.loads(data.decode("utf-8", errors="ignore"))
        except:
            continue

        if j.get("type") != "PAIR_REQUEST":
            continue

        device_id = str(j.get("device_id", "")).strip()
        username = str(j.get("username", "Phone")).strip()[:64]
        if not device_id:
            continue

        # refresh existing
        for slot, c in controllers.items():
            if c and c.device_id == device_id:
                c.username = username
                c.addr = addr
                c.token = c.token or secrets.token_hex(16)
                last_seen[slot] = time.time()
                resp = {"type": "PAIR_ACCEPT", "slot": slot, "token": c.token, "data_port": int(DATA_PORT)}
                try:
                    sock.sendto(json.dumps(resp).encode("utf-8"), addr)
                except:
                    pass
                log(f"[PAIR] Refresh slot={slot+1} user={username}")
                break
        else:
            free = next((i for i in range(MAX_CONTROLLERS) if controllers[i] is None), None)
            if free is None:
                try:
                    sock.sendto(json.dumps({"type": "PAIR_DENY", "reason": "NO_FREE_SLOTS"}).encode("utf-8"), addr)
                except:
                    pass
                log("[PAIR] Deny: no free slots")
                continue

            controllers[free] = ControllerState(device_id=device_id, username=username, addr=addr, token=secrets.token_hex(16))
            last_seen[free] = time.time()
            resp = {"type": "PAIR_ACCEPT", "slot": free, "token": controllers[free].token, "data_port": int(DATA_PORT)}
            try:
                sock.sendto(json.dumps(resp).encode("utf-8"), addr)
            except:
                pass
            log(f"[PAIR] Accepted slot={free+1} user={username}")

def phone_listener(stop_evt: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("", int(DATA_PORT)))
    except Exception as e:
        log(f"[PHONE] Bind failed on {DATA_PORT}: {e!r}")
        return
    log(f"[PHONE] Listening on :{DATA_PORT}")

    while not stop_evt.is_set():
        try:
            sock.settimeout(0.25)
            data, addr = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except:
            continue

        try:
            j = json.loads(data.decode("utf-8", errors="ignore"))
        except:
            continue

        device_id = str(j.get("device_id", "")).strip()
        token = str(j.get("token", "")).strip()
        if not device_id or not token:
            continue

        slot = None
        for s, c in controllers.items():
            if c and c.device_id == device_id:
                slot = s
                break
        if slot is None:
            continue

        c = controllers.get(slot)
        if not c or c.token != token:
            continue

        last_seen[slot] = time.time()
        c.addr = addr
        c.buttons = j.get("buttons", {}) or {}
        c.gyro = j.get("gyro", {}) or {}
        c.accel = j.get("accel", {}) or {}

        pkt_count[slot] += 1

def cleanup_loop(stop_evt: threading.Event) -> None:
    while not stop_evt.is_set():
        now = time.time()
        for s in range(MAX_CONTROLLERS):
            if controllers[s] is not None and (now - last_seen[s] > TIMEOUT_SEC):
                log(f"[CTRL] Timeout slot={s+1}")
                controllers[s] = None
                last_seen[s] = 0.0
                hz_est[s] = 0.0
                pkt_count[s] = 0
        stop_evt.wait(0.5)

def stats_loop(stop_evt: threading.Event) -> None:
    global _last_hz_t
    while not stop_evt.is_set():
        stop_evt.wait(1.0)
        now = time.time()
        dt = now - _last_hz_t
        _last_hz_t = now
        if dt <= 0:
            continue
        for s in range(MAX_CONTROLLERS):
            hz_est[s] = pkt_count[s] / dt
            pkt_count[s] = 0

def update_motion(c: ControllerState) -> None:
    m = c.motion
    gyro = c.gyro or {}
    accel = c.accel or {}

    now = time.time()
    if m.last_ts == 0.0:
        m.last_ts = now
        return

    dt = clamp(now - m.last_ts, DT_MIN, DT_MAX)
    m.last_ts = now

    gx = maybe_deg_to_rad(float(gyro.get("x", 0.0) or 0.0))
    gz = maybe_deg_to_rad(float(gyro.get("z", 0.0) or 0.0))
    m.pitch += gx * dt
    m.yaw += gz * dt

    ax = float(accel.get("x", 0.0) or 0.0)
    ay = float(accel.get("y", 0.0) or 0.0)
    az = float(accel.get("z", 0.0) or 0.0)
    acc_mag = math.sqrt(ax*ax + ay*ay + az*az)

    if abs(acc_mag - 9.81) < ACC_G_TOL:
        apitch = accel_to_pitch(ax, ay, az)
        err = apitch - m.pitch
        if abs(err) > DRIFT_ERR_THRESH:
            m.pitch += DRIFT_STEP * err

def compute_axes(c: ControllerState) -> Tuple[int, int]:
    gyro = c.gyro or {}
    m = c.motion

    now = time.time()
    if m.last_ts == 0.0:
        m.last_ts = now
        return 0, 0

    dt = clamp(now - m.last_ts, 0.001, 0.05)
    m.last_ts = now

    # gyro brut (rad/s)
    gx = maybe_deg_to_rad(float(gyro.get("x", 0.0) or 0.0))
    gy = maybe_deg_to_rad(float(gyro.get("y", 0.0) or 0.0))

    # deadzone
    DZ = 0.015
    if abs(gx) < DZ:
        gx = 0.0
    if abs(gy) < DZ:
        gy = 0.0

    # integrare POINTER (NU motion global)
    m.yaw += gx * dt
    m.pitch += gy * dt

    # clamp soft (previne runaway)
    m.yaw = clamp(m.yaw, -0.6, 0.6)
    m.pitch = clamp(m.pitch, -0.45, 0.45)

    # mapare → stick
    rx = m.yaw / 0.6
    ry = -m.pitch / 0.45

    if INVERT_RX:
        rx = -rx
    if INVERT_RY:
        ry = -ry

    return int(rx * 32767), int(ry * 32767)


def bridge_loop(stop_evt: threading.Event) -> None:
    while not stop_evt.is_set():
        with ACTIVE_LOCK:
            active_slot_1based = ACTIVE_SLOT_FOR_BRIDGE
        slot = active_slot_1based - 1

        c = controllers.get(slot)
        if not c:
            stop_evt.wait(0.01)
            continue

        update_motion(c)

        home_now = int((c.buttons or {}).get("HOME", 0))
        if home_now == 1:
            if c.motion.home_hold is None:
                c.motion.home_hold = time.time()
            else:
                if (time.time() - c.motion.home_hold) >= RECENTER_HOLD_SEC:
                    c.motion.pitch0 = c.motion.pitch
                    c.motion.yaw0 = c.motion.yaw
                    c.motion.home_hold = time.time() + 9999
        else:
            c.motion.home_hold = None

        RX, RY = compute_axes(c)

        ax = float((c.accel or {}).get("x", 0.0) or 0.0)
        now = time.time()
        swing = False
        if abs(ax) > SWING_THRESHOLD and (now - c.motion.last_swing) > SWING_COOLDOWN:
            swing = True
            c.motion.last_swing = now

        parts = []
        btns = c.buttons or {}
        for wii, bridge_key in WII_TO_BRIDGE.items():
            parts.append(f"{bridge_key}={int(bool(btns.get(wii, 0)))}")
        if swing:
            parts.append("A=1")
        parts.append(f"RX={RX}")
        parts.append(f"RY={RY}")

        send_to_bridge(",".join(parts))
        stop_evt.wait(1/120)

class ModernUI(tk.Tk):
    def __init__(self, stop_evt: threading.Event):
        super().__init__()
        self.stop_evt = stop_evt

        self.title("WiiMote Server (ViGEm) – Modern UI")
        self.geometry("980x620")
        self.configure(bg=COL_BG)
        self.resizable(False, False)

        self._init_style()

        header = tk.Frame(self, bg=COL_BG)
        header.pack(fill="x", padx=18, pady=(16, 10))

        # icon
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        ICON_PATH = os.path.join(BASE_DIR, "controller.png")

        icon_img = Image.open(ICON_PATH).resize((36, 36), Image.LANCZOS)

        self.icon_tk = ImageTk.PhotoImage(icon_img)

        tk.Label(
            header,
            image=self.icon_tk,
            bg=COL_BG
        ).pack(side="left", padx=(0, 10))

        # title
        tk.Label(
            header,
            text="WiiMote Server",
            bg=COL_BG,
            fg=COL_TEXT,
            font=("Segoe UI", 20, "bold")
        ).pack(side="left")

        tk.Label(
            header,
            text="• ViGEm bridge mode •",
            bg=COL_BG,
            fg=COL_MUTED,
            font=("Segoe UI", 10)
        ).pack(side="left", padx=14)


        body = tk.Frame(self, bg=COL_BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        left = tk.Frame(body, bg=COL_BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=COL_BG)
        right.pack(side="right", fill="y", padx=(16, 0))

        self.ctrl_card = self._card(left, "Controllers")
        self.ctrl_card.pack(fill="x")
        self.ctrl_rows = []
        for i in range(MAX_CONTROLLERS):
            row = self._controller_row(self.ctrl_card.content, i)
            row.pack(fill="x", padx=14, pady=6)
            self.ctrl_rows.append(row)

        sel = tk.Frame(self.ctrl_card.content, bg=COL_CARD)
        sel.pack(fill="x", padx=14, pady=(10, 14))
        tk.Label(sel, text="Active controller → ViGEm bridge:", bg=COL_CARD, fg=COL_TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        self.active_combo = ttk.Combobox(sel, values=[1,2,3,4], width=4, state="readonly")
        self.active_combo.set(str(ACTIVE_SLOT_FOR_BRIDGE))
        self.active_combo.pack(side="left", padx=10)
        self.active_combo.bind("<<ComboboxSelected>>", self.on_change_active)

        tk.Label(sel, text="(One virtual pad for now)", bg=COL_CARD, fg=COL_MUTED, font=("Segoe UI", 9)).pack(side="left", padx=10)

        self.logs = self._card(left, "Logs")
        self.logs.pack(fill="both", expand=True, pady=(14, 0))

        self.log_text = tk.Text(self.logs.content, height=12, bg=COL_CARD2, fg=COL_TEXT,
                                insertbackground=COL_TEXT, relief="flat", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=14, pady=(10, 10))
        self.log_text.config(state="disabled")

        self.net_card = self._card(right, "Network")
        self.net_card.pack(fill="x")

        self.ip_value = tk.StringVar(value=get_ipv4() or "-")
        self.port_value = tk.StringVar(value=str(DATA_PORT))

        row1 = tk.Frame(self.net_card.content, bg=COL_CARD)
        row1.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(row1, text="IPv4:", bg=COL_CARD, fg=COL_MUTED, width=8, anchor="w").pack(side="left")
        ip_lbl = tk.Label(row1, textvariable=self.ip_value, bg=COL_CARD, fg=COL_BLUE,
                          font=("Segoe UI", 10, "underline"), cursor="hand2")
        ip_lbl.pack(side="left")
        ip_lbl.bind("<Button-1>", lambda _e: self.copy_ip())

        tk.Button(row1, text="Copy", bg=COL_CARD2, fg=COL_TEXT, relief="flat",
                  activebackground=COL_BORDER, activeforeground=COL_TEXT,
                  command=self.copy_ip).pack(side="right")

        row2 = tk.Frame(self.net_card.content, bg=COL_CARD)
        row2.pack(fill="x", padx=14, pady=(4, 6))
        tk.Label(row2, text="Data port:", bg=COL_CARD, fg=COL_MUTED, width=8, anchor="w").pack(side="left")
        tk.Entry(row2, textvariable=self.port_value, width=8, bg=COL_CARD2, fg=COL_TEXT,
                 insertbackground=COL_TEXT, relief="flat").pack(side="left")
        tk.Button(row2, text="Save", bg=COL_BLUE, fg="white", relief="flat",
                  command=self.save_port).pack(side="right")

        row3 = tk.Frame(self.net_card.content, bg=COL_CARD)
        row3.pack(fill="x", padx=14, pady=(4, 12))
        self.net_status = tk.Label(row3, text="Status: checking…", bg=COL_CARD, fg=COL_MUTED)
        self.net_status.pack(side="left")

      
    

        footer = tk.Frame(self, bg=COL_BG)
        footer.pack(fill="x", padx=18, pady=(0, 12))
        self.footer_lbl = tk.Label(footer, text="Ready.", bg=COL_BG, fg=COL_MUTED)
        self.footer_lbl.pack(side="left")
        tk.Button(footer, text="Quit", bg=COL_RED, fg="white", relief="flat", command=self.on_quit).pack(side="right")

        self.after(200, self.refresh_ui)
        self.after(1000, self.refresh_network)
        self.after(400, self.refresh_logs)

    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except:
            pass
        style.configure("TCombobox", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", COL_CARD2)])

    class _Card(tk.Frame):
        def __init__(self, parent, title: str):
            super().__init__(parent, bg=COL_CARD, highlightbackground=COL_BORDER, highlightthickness=1)
            top = tk.Frame(self, bg=COL_CARD)
            top.pack(fill="x", padx=14, pady=(10, 0))
            tk.Label(top, text=title, bg=COL_CARD, fg=COL_TEXT, font=("Segoe UI", 12, "bold")).pack(side="left")
            self.content = tk.Frame(self, bg=COL_CARD)
            self.content.pack(fill="both", expand=True)

    def _card(self, parent, title: str):
        return self._Card(parent, title)

    def _controller_row(self, parent, slot: int) -> tk.Frame:
        row = tk.Frame(parent, bg=COL_CARD2, highlightbackground=COL_BORDER, highlightthickness=1)
        row.grid_columnconfigure(1, weight=1)

        left = tk.Frame(row, bg=COL_CARD2)
        left.grid(row=0, column=0, sticky="w", padx=10, pady=8)

        tk.Label(left, text=f"Wii Remote {slot+1}", bg=COL_CARD2, fg=COL_TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        status = tk.Label(left, text="Free", bg=COL_CARD2, fg=COL_RED, font=("Segoe UI", 9))
        status.pack(anchor="w")

        mid = tk.Frame(row, bg=COL_CARD2)
        mid.grid(row=0, column=1, sticky="ew", padx=10, pady=8)
        name = tk.Label(mid, text="Waiting for device…", bg=COL_CARD2, fg=COL_MUTED)
        name.pack(anchor="w")
        stats = tk.Label(mid, text="—", bg=COL_CARD2, fg=COL_MUTED)
        stats.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(row, bg=COL_CARD2)
        right.grid(row=0, column=2, sticky="e", padx=10, pady=8)
        btn = tk.Button(right, text="Disconnect", bg=COL_RED, fg="white", relief="flat",
                        command=lambda s=slot: self.disconnect_slot(s))
        btn.pack()
        btn.pack_forget()

        row._ui = {"status": status, "name": name, "stats": stats, "btn": btn}
        return row

    def disconnect_slot(self, slot: int):
        controllers[slot] = None
        last_seen[slot] = 0.0
        hz_est[slot] = 0.0
        log(f"[UI] Disconnected slot {slot+1}")

    def on_change_active(self, _evt=None):
        global ACTIVE_SLOT_FOR_BRIDGE
        try:
            val = int(self.active_combo.get())
        except:
            return
        val = max(1, min(MAX_CONTROLLERS, val))
        with ACTIVE_LOCK:
            ACTIVE_SLOT_FOR_BRIDGE = val
        CONFIG["active_slot"] = val
        save_config(CONFIG)
        log(f"[UI] Active slot set to {val}")

    def copy_ip(self):
        ip = get_ipv4()
        if not ip:
            messagebox.showerror("Copy IP", "No IPv4 detected. Connect to Wi‑Fi / LAN.")
            return
        self.clipboard_clear()
        self.clipboard_append(ip)
        self.footer_lbl.config(text=f"Copied {ip}.")
        log(f"[UI] Copied IPv4 {ip}")

    def save_port(self):
        try:
            p = int(self.port_value.get().strip())
            if not (1024 <= p <= 65535):
                raise ValueError
        except:
            messagebox.showerror("Invalid port", "Port must be integer 1024..65535.")
            self.port_value.set(str(DATA_PORT))
            return

        CONFIG["data_port"] = p
        save_config(CONFIG)
        messagebox.showinfo("Saved", f"Saved data port = {p}.\nRestart server to apply.")
        log(f"[UI] Saved data port {p} (restart required)")

    def open_joycpl(self):
        if os.name != "nt":
            messagebox.showinfo("joy.cpl", "Windows only.")
            return
        try:
            subprocess.Popen(["control", "joy.cpl"])
        except Exception as e:
            messagebox.showerror("joy.cpl", f"Failed: {e!r}")

    def firewall_tip(self):
        messagebox.showinfo(
            "Firewall tip",
            "If phone can't connect:\n\n"
            "1) Allow Python through Windows Firewall (Private network).\n"
            "2) Ensure UDP data port is open.\n"
            f"   Current data port: {DATA_PORT}\n"
            "3) Phone and PC must be on the same Wi‑Fi.\n"
        )

    def on_quit(self):
        self.stop_evt.set()
        self.destroy()

    def refresh_ui(self):
        now = time.time()
        with ACTIVE_LOCK:
            active = ACTIVE_SLOT_FOR_BRIDGE

        for i, row in enumerate(self.ctrl_rows):
            ui = row._ui
            c = controllers.get(i)
            if c:
                ui["status"].config(text="Connected", fg=COL_GREEN)
                ui["name"].config(text=f"{c.username}  •  id {c.device_id[:8]}…", fg=COL_TEXT)

                age = now - last_seen[i] if last_seen[i] else 0.0
                hz = hz_est.get(i, 0.0)
                ui["stats"].config(text=f"Age: {age:0.1f}s  •  {hz:0.0f} Hz", fg=COL_MUTED)

                if not ui["btn"].winfo_ismapped():
                    ui["btn"].pack()
                row.config(highlightbackground=COL_GREEN)
            else:
                ui["status"].config(text="Free", fg=COL_RED)
                ui["name"].config(text="Waiting for device…", fg=COL_MUTED)
                ui["stats"].config(text="—", fg=COL_MUTED)
                if ui["btn"].winfo_ismapped():
                    ui["btn"].pack_forget()
                row.config(highlightbackground=COL_BORDER)

        self.footer_lbl.config(text=f"Active slot: {active} → bridge  |  Data port: {DATA_PORT}")
        self.after(200, self.refresh_ui)

    def refresh_network(self):
        ip = get_ipv4()
        self.ip_value.set(ip or "-")
        self.net_status.config(text=("Status: online" if ip else "Status: no IPv4"),
                               fg=(COL_GREEN if ip else COL_RED))
        self.after(1000, self.refresh_network)

    def refresh_logs(self):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-200:]
            text = "".join(lines)
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", text)
            self.log_text.config(state="disabled")
            self.log_text.see("end")
        except:
            pass
        self.after(400, self.refresh_logs)

def start_threads(stop_evt: threading.Event):
    start_bridge()
    threading.Thread(target=discovery_loop, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=pair_server, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=phone_listener, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=cleanup_loop, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=stats_loop, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=bridge_loop, args=(stop_evt,), daemon=True).start()
    log("[SERVER] Threads started.")

def main():
    acquire_lock()
    atexit.register(release_lock)

    stop_evt = threading.Event()
    start_threads(stop_evt)

    app = ModernUI(stop_evt)
    app.mainloop()

    stop_evt.set()
    time.sleep(0.2)

if __name__ == "__main__":
    main()
