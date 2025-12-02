from kivy.app import App
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from plyer import accelerometer, gyroscope
import os, socket, json

# --- Fix icons & path on Android ---
if hasattr(os, 'environ') and "ANDROID_ARGUMENT" in os.environ:
    android_path = "/data/user/0/org.example.wii_controller/files/app"
    os.environ["KIVY_HOME"] = android_path

# --- UDP setup ---
UDP_IP = "192.168.68.115"
UDP_PORT = 5005
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

try: accelerometer.enable()
except: accelerometer = None

try: gyroscope.enable()
except: gyroscope = None


class WiiControllerApp(App):
    def build(self):
        self.layout = FloatLayout()

        # Background
        with self.layout.canvas.before:
            Color(0.97, 0.97, 0.97, 1)
            self.bg = RoundedRectangle(pos=self.layout.pos, size=Window.size)
        self.layout.bind(size=self.update_bg)

        # Button states
        self.buttons_state = {
            "UP":0, "DOWN":0, "LEFT":0, "RIGHT":0,
            "A":0, "B":0, "1":0, "2":0,
            "-":0, "+":0, "HOME":0
        }

        # ---- D-PAD ----
        cx = 0.5
        cy = 0.70
        s = 0.12

        self.add_btn("↑", cx - s/2, cy + s, s, s, "UP")
        self.add_btn("↓", cx - s/2, cy - s, s, s, "DOWN")
        self.add_btn("←", cx - s*1.5, cy, s, s, "LEFT")
        self.add_btn("→", cx + s*0.5, cy, s, s, "RIGHT")

        # ---- A & B ----
        self.add_btn("A", 0.5 - 0.12/2, 0.52, 0.12, 0.12, "A", radius=80)
        self.add_btn("B", 0.5 - 0.12/2, 0.41, 0.12, 0.12, "B", radius=10)

        # ---- -, HOME, + ----
        small = 0.09
        self.add_btn("-",  0.5 - small*1.2, 0.30, small, small, "-",   radius=20)
        self.add_btn("H",  0.5 - small/2,   0.30, small, small, "HOME", radius=20)
        self.add_btn("+",  0.5 + small*0.2, 0.30, small, small, "+",   radius=20)

        # ---- 1 & 2 ----
        self.add_btn("1", 0.5 - 0.11, 0.18, 0.10, 0.10, "1", radius=40)
        self.add_btn("2", 0.5 + 0.01, 0.18, 0.10, 0.10, "2", radius=40)

        # Start sending data
        Clock.schedule_interval(self.send_data, 1/30)

        return self.layout

    def add_btn(self, text, x, y, w, h, key, radius=40):
        btn = Button(
            text=text,
            size_hint=(w, h),
            pos_hint={"x":x, "y":y},
            background_normal="",
            background_color=(0,0,0,0),
            color=(0,0,0,1),
            font_size="22sp"
        )

        with btn.canvas.before:
            Color(0.75, 0.75, 0.75, 1)
            btn.shape = RoundedRectangle(size=btn.size, pos=btn.pos, radius=[radius])

        btn.bind(pos=lambda inst, val: setattr(inst.shape, "pos", val))
        btn.bind(size=lambda inst, val: setattr(inst.shape, "size", val))

        btn.bind(on_press=lambda *_: self._press(key))
        btn.bind(on_release=lambda *_: self._release(key))

        self.layout.add_widget(btn)

    def _press(self, key):
        self.buttons_state[key] = 1

    def _release(self, key):
        self.buttons_state[key] = 0

    def update_bg(self, *args):
        self.bg.size = self.layout.size

    def send_data(self, dt):
        try: accel = accelerometer.acceleration or (0,0,0)
        except: accel = (0,0,0)

        try: gyro = gyroscope.rotation or (0,0,0)
        except: gyro = (0,0,0)

        data = {
            "accel": {"x": accel[0], "y": accel[1], "z": accel[2]},
            "gyro":  {"x": gyro[0],  "y": gyro[1],  "z": gyro[2]},
            "buttons": self.buttons_state
        }

        try:
            sock.sendto(json.dumps(data).encode(), (UDP_IP, UDP_PORT))
        except:
            pass


if __name__ == "__main__":
    WiiControllerApp().run()
