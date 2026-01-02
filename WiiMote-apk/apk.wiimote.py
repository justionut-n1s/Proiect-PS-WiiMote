# apk.wiimote.py
from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.properties import StringProperty, NumericProperty, BooleanProperty

from plyer import accelerometer, gyroscope

import socket
import json
import os
import threading
import time
import secrets
import traceback

SETTINGS_FILE = "settings.json"

DISCOVERY_PORT = 5006
PAIR_PORT_FALLBACK = 5007
DATA_PORT_FALLBACK = 5005


class SettingsLayout(BoxLayout):
    username = StringProperty("MyPhone")
    manual_ip = StringProperty("")
    manual_port = NumericProperty(5005)
    use_manual = BooleanProperty(False)

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.username = data.get("username", self.username)
                self.manual_ip = data.get("manual_ip", self.manual_ip)
                self.manual_port = int(data.get("manual_port", self.manual_port))
                self.use_manual = bool(data.get("use_manual", self.use_manual))
            except Exception:
                pass

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "username": self.username,
                    "manual_ip": self.manual_ip,
                    "manual_port": int(self.manual_port),
                    "use_manual": bool(self.use_manual),
                }, f, indent=2)
        except Exception:
            pass

        App.get_running_app().apply_settings(
            self.username,
            self.manual_ip,
            int(self.manual_port),
            bool(self.use_manual)
        )


class WiiControllerApp(App):

    def build(self):
        # defaults
        self.username = "MyPhone"
        self.manual_ip = ""
        self.manual_port = 5005
        self.use_manual = False

        self.device_id = self._load_or_create_device_id()

        self.server_ip = None
        self.data_port = None
        self.pair_port = None

        self.token = None
        self.paired = False
        self.pairing_state = "NOT_PAIRED"

        self._load_settings()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.buttons_state = {
            "UP": 0, "DOWN": 0, "LEFT": 0, "RIGHT": 0,
            "A": 0, "B": 0, "1": 0, "2": 0, "-": 0, "+": 0, "HOME": 0
        }

        try:
            root = Builder.load_file("ui.kv")
        except Exception:
            # dacă moare aici, motivul e 100% KV/paths/assets
            traceback.print_exc()
            # fallback minimal ca să NU moară fără mesaj
            return Label(text="KV load failed. Check logcat.")

        self._map_buttons(root)

        # IMPORTANT: nu pornim senzorii în build() (pe unele device-uri crapă devreme)
        # îi pornim în on_start()

        threading.Thread(target=self._discovery_listener, daemon=True).start()
        Clock.schedule_interval(self._send_data, 1 / 60)

        return root

    def on_start(self):
        # Safe enable sensors
        try:
            accelerometer.enable()
        except Exception:
            pass
        try:
            gyroscope.enable()
        except Exception:
            pass

    def on_stop(self):
        try:
            accelerometer.disable()
        except Exception:
            pass
        try:
            gyroscope.disable()
        except Exception:
            pass

    # -------------------------
    # Settings
    # -------------------------
    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.username = data.get("username", self.username)
                self.manual_ip = data.get("manual_ip", self.manual_ip)
                self.manual_port = int(data.get("manual_port", self.manual_port))
                self.use_manual = bool(data.get("use_manual", self.use_manual))
            except Exception:
                pass

    def apply_settings(self, username, manual_ip, manual_port, use_manual):
        self.username = (username or "MyPhone")[:64]
        self.manual_ip = (manual_ip or "").strip()
        self.manual_port = int(manual_port)
        self.use_manual = bool(use_manual)

        if self.use_manual and self.manual_ip:
            self.server_ip = self.manual_ip
            self.data_port = self.manual_port
            self.pair_port = PAIR_PORT_FALLBACK
            self._request_pair()

    def open_settings(self):
        layout = SettingsLayout()
        layout.load_settings()
        popup = Popup(title="SETTINGS", content=layout, size_hint=(0.85, 0.6), auto_dismiss=True)
        popup.open()

    # -------------------------
    # Device ID
    # -------------------------
    def _load_or_create_device_id(self):
        path = "device_id.txt"
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                    if v:
                        return v
            except Exception:
                pass
        v = secrets.token_hex(8)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(v)
        except Exception:
            pass
        return v

    # -------------------------
    # Button mapping (nu schimbăm nimic)
    # -------------------------
    def _map_buttons(self, root):
        for w in root.walk():
            if hasattr(w, "text"):
                key = (w.text or "").strip()
                mapping = {"↑": "UP", "↓": "DOWN", "←": "LEFT", "→": "RIGHT", "H": "HOME"}
                if key in mapping:
                    key = mapping[key]
                if key in self.buttons_state:
                    w.bind(on_press=lambda ww, k=key: self._press(k),
                           on_release=lambda ww, k=key: self._release(k))

    def _press(self, k): self.buttons_state[k] = 1
    def _release(self, k): self.buttons_state[k] = 0

    # -------------------------
    # Discovery
    # -------------------------
    def _discovery_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", DISCOVERY_PORT))
        except Exception:
            return

        while True:
            try:
                data, addr = s.recvfrom(4096)
            except Exception:
                continue

            try:
                j = json.loads(data.decode("utf-8", errors="ignore"))
            except Exception:
                continue

            if j.get("type") != "DISCOVERY":
                continue

            if self.use_manual:
                continue

            ip = addr[0]
            self.server_ip = ip
            self.pair_port = int(j.get("pair_port", PAIR_PORT_FALLBACK))
            self.data_port = int(j.get("data_port", DATA_PORT_FALLBACK))

            if not self.paired and self.pairing_state != "PENDING":
                self._request_pair()

    # -------------------------
    # Pairing
    # -------------------------
    def _request_pair(self):
        if not self.server_ip:
            return
        self.pairing_state = "PENDING"

        req = {
            "type": "PAIR_REQUEST",
            "username": self.username,
            "device_id": self.device_id,
            "ts": int(time.time())
        }

        try:
            self.sock.sendto(json.dumps(req).encode("utf-8"),
                             (self.server_ip, int(self.pair_port or PAIR_PORT_FALLBACK)))
        except Exception:
            return

        threading.Thread(target=self._pair_wait_response, daemon=True).start()

    def _pair_wait_response(self):
        deadline = time.time() + 5.0
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.bind(("", 0))
        except Exception:
            return

        try:
            req = {
                "type": "PAIR_REQUEST",
                "username": self.username,
                "device_id": self.device_id,
                "ts": int(time.time())
            }
            s.sendto(json.dumps(req).encode("utf-8"),
                     (self.server_ip, int(self.pair_port or PAIR_PORT_FALLBACK)))
        except Exception:
            try:
                s.close()
            except Exception:
                pass
            return

        while time.time() < deadline:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break

            try:
                j = json.loads(data.decode("utf-8", errors="ignore"))
            except Exception:
                continue

            t = j.get("type")
            if t == "PAIR_PENDING":
                self.pairing_state = "PENDING"
                continue

            if t == "PAIR_DENY":
                self.pairing_state = "DENIED"
                self.paired = False
                self.token = None
                break

            if t == "PAIR_ACCEPT":
                self.token = str(j.get("token", "")).strip()
                try:
                    self.data_port = int(j.get("data_port", self.data_port or DATA_PORT_FALLBACK))
                except Exception:
                    pass
                self.paired = bool(self.token)
                self.pairing_state = "PAIRED" if self.paired else "NOT_PAIRED"
                break

        try:
            s.close()
        except Exception:
            pass

    # -------------------------
    # Send loop
    # -------------------------
    def _send_data(self, _dt):
        if not self.paired or not self.server_ip or not self.data_port or not self.token:
            return

        accel = {"x": 0.0, "y": 0.0, "z": 0.0}
        gyro_ = {"x": 0.0, "y": 0.0, "z": 0.0}

        try:
            a = accelerometer.acceleration
            if a:
                accel = {"x": float(a[0] or 0.0), "y": float(a[1] or 0.0), "z": float(a[2] or 0.0)}
        except Exception:
            pass

        try:
            g = gyroscope.rotation
            if g:
                gyro_ = {"x": float(g[0] or 0.0), "y": float(g[1] or 0.0), "z": float(g[2] or 0.0)}
        except Exception:
            pass

        payload = {
            "device_id": self.device_id,
            "username": self.username,
            "token": self.token,
            "accel": accel,
            "gyro": gyro_,
            "buttons": dict(self.buttons_state),
            "ts": time.time()
        }

        try:
            self.sock.sendto(json.dumps(payload).encode("utf-8"),
                             (self.server_ip, int(self.data_port)))
        except Exception:
            pass


if __name__ == "__main__":
    WiiControllerApp().run()
