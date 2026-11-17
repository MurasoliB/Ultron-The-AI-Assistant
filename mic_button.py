import sys
import os

# Auto-relaunch in venv if started with system Python
if __name__ == "__main__" and sys.prefix == sys.base_prefix:
    _here = os.path.dirname(os.path.abspath(__file__))
    _venv_pyw = os.path.join(_here, "venv", "Scripts", "pythonw.exe")
    _venv_py = os.path.join(_here, "venv", "Scripts", "python.exe")
    _target_exe = _venv_pyw if os.path.exists(_venv_pyw) else (_venv_py if os.path.exists(_venv_py) else None)
    if _target_exe and os.path.abspath(sys.executable).lower() != os.path.abspath(_target_exe).lower():
        os.execv(_target_exe, [_target_exe] + sys.argv)


import tkinter as tk
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import ctypes

# Attach process and thread to interactive desktop winsta0\default
if sys.platform == "win32":
    try:
        _u32 = ctypes.windll.user32
        _hwinsta = _u32.OpenWindowStationW("winsta0", False, 0x10000000)
        if _hwinsta:
            _u32.SetProcessWindowStation(_hwinsta)
        _hdesk = _u32.OpenDesktopW("default", 0, False, 0x10000000)
        if _hdesk:
            _u32.SetThreadDesktop(_hdesk)
    except Exception:
        pass
import threading
import json
import os
import math
import random
import time
from tkinter import messagebox

from pynput import mouse
from pynput.keyboard import Key, Listener as KeyboardListener
import ultron_sync

from doc_agent import open_doc_agent, get_doc_agent_instance
from voice import listen, speak, stop_speaking, stop_listening
import voice as voice_module
from assistant import process_message


# ---------------------------------------------------------------
# STATE FILE
# ---------------------------------------------------------------
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mic_button_pos.json")
DEFAULT_POS = {"x": 40, "y": 200}


def _load_pos():
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return int(data.get("x", DEFAULT_POS["x"])), int(data.get("y", DEFAULT_POS["y"]))
    except Exception:
        return DEFAULT_POS["x"], DEFAULT_POS["y"]


def _save_pos(x, y):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"x": int(x), "y": int(y)}, f)
    except Exception as e:
        print(f"[mic_button] couldn't save position: {e}")


# ---------------------------------------------------------------
# COLOR HELPERS
# ---------------------------------------------------------------
def _hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb):
    r, g, b = rgb
    return "#{:02x}{:02x}{:02x}".format(max(0, min(255, int(r))),
                                        max(0, min(255, int(g))),
                                        max(0, min(255, int(b))))


def _lerp(c1, c2, t):
    """Blend two hex colors. t in [0..1]."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex((r1 + (r2 - r1) * t,
                        g1 + (g2 - g1) * t,
                        b1 + (b2 - b1) * t))


def _blend_with_bg(color, alpha, bg="#050608"):
    """Fake alpha blending against dark background."""
    return _lerp(bg, color, alpha)


# ---------------------------------------------------------------
# FULL SCREEN TRANSPARENT LASER OVERLAY
# ---------------------------------------------------------------
class LaserOverlayWindow:
    """Invisible transparent fullscreen overlay that passes through clicks or displays lasers."""
    TRANS_KEY = "#000001"

    def __init__(self, parent_root):
        self.top = tk.Toplevel(parent_root)
        self.top.title("Ultron Overlay")
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)

        # Transparent background setup
        self.top.configure(bg=self.TRANS_KEY)
        try:
            self.top.wm_attributes("-transparentcolor", self.TRANS_KEY)
        except Exception:
            pass

        ws = self.top.winfo_screenwidth()
        hs = self.top.winfo_screenheight()
        self.top.geometry(f"{ws}x{hs}+0+0")

        self.canvas = tk.Canvas(
            self.top, width=ws, height=hs,
            bg=self.TRANS_KEY, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        if os.name == "nt":
            try:
                self.top.update_idletasks()
                user32 = ctypes.windll.user32
                get_window_long = user32.GetWindowLongW
                get_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int)
                get_window_long.restype = ctypes.c_long
                set_window_long = user32.SetWindowLongW
                set_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
                set_window_long.restype = ctypes.c_long

                hwnd = self.top.winfo_id()
                ex_style = get_window_long(hwnd, -20)
                set_window_long(hwnd, -20, ex_style | 0x00000020)
            except Exception as error:
                print(f"[mic_button] overlay click-through setup failed: {error}")

        self.hide()

    def show(self):
        self.top.deiconify()
        self.top.lift()
        self.top.attributes("-topmost", True)

    def hide(self):
        self.canvas.delete("all")
        self.top.withdraw()


# ---------------------------------------------------------------
# ULTRON ADVANCED FLOATING MIC BUTTON
# ---------------------------------------------------------------
class MicButton:
    SIZE = 140
    RING_PAD = 20
    DRAG_THRESHOLD = 4   # px of movement before a press counts as a drag, not a click

    COLOR_MAP = {
        "IDLE": "#ff0033",          # Threat Crimson
        "CURIOUS": "#ff9900",       # Inquisitive Amber
        "ALERT": "#ff0011",         # Threat Target Red
        "LISTENING": "#ff0225",     # Intercept Crimson
        "PROCESSING": "#00f0ff",    # Laser Core Cyan
        "SPEAKING": "#ff7a1a",      # Vocal Resonance Amber-Orange
        "SUCCESS": "#00ff66",       # Execution Complete Green
        "ERROR": "#ff0055",         # Instability Glitch Magenta/Red
        "SLEEP": "#3a4454",         # Dormant Slate Blue
        "RAGE": "#ff2200",          # Overload Plasma Orange/Red
        "ANALYTICAL": "#ffcc00",    # Core Scan Gold
        "STEALTH": "#00ffaa",       # Stealth Phantom Mint
    }
    BG = "#050608"
    FRAME_MS = 30

    def __init__(self, root):
        self.root = root
        self.root.title("ULTRON Core")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-transparentcolor", self.BG)
        except Exception:
            pass

        x, y = _load_pos()
        self.root.geometry(f"{self.SIZE}x{self.SIZE}+{x}+{y}")
        self.root.configure(bg=self.BG)

        # Full Screen Overlay Initialization
        self.overlay = LaserOverlayWindow(self.root)

        # State Flags
        self.is_recording = False
        self.is_transcribing = False
        self.is_speaking = False
        self.is_typing = False
        self.is_dragging = False
        self._moved = False
        self._hover = False
        self._is_hidden = False

        # Document & Code Agent Integration
        self.doc_agent = None

        # Drag-to-move state (local widget drag, independent of the global laser hook)
        self._press_pos_root = None
        self._press_win_pos = None
        self._drag_moved = False

        # Persistent Emotion System
        self.emotion = "IDLE"
        self._base_loop_emotion = "IDLE"

        # Global Laser System State
        self.laser_enabled = False  # Disabled by default to prevent stealing focus from CodeTantra
        self._laser_active = False
        self._laser_type = "RED_PLASMA"  # "RED_PLASMA", "CYAN_CYBER"
        self._laser_target_root = (0, 0)
        self._laser_impact_particles = []
        self._last_click_time = 0

        # Drivers
        self._t = 0.0
        self._anim_job = None
        self._glitch_timer = 0
        self._glitch_offset = (0, 0)

        # Eye Geometry
        self._blink_progress = 1.0
        self._blink_direction = -1
        self._is_blinking = False
        self._next_blink_time = random.randint(80, 140)

        self._eye_look_x = 0.0
        self._eye_look_y = 0.0
        self._target_look_x = 0.0
        self._target_look_y = 0.0
        self._next_look_time = random.randint(20, 60)

        self._eye_height = 1.0
        self._brow_slant = 0.0
        self._pupil_scale = 1.0
        self._iris_rotation = 0.0
        self._scan_line_y = -20.0
        self._curr_color = self.COLOR_MAP["IDLE"]
        self._glint_angle = random.uniform(0, 360)

        # Visuals
        self._particles = [self._spawn_particle() for _ in range(16)]
        self._tendrils = []
        self._tendril_timer = 0

        # Listening: reactive bars + outward "hearing" ripples
        self._bar_phases = [random.random() * math.tau for _ in range(7)]
        self._bar_levels = [0.2 for _ in range(7)]
        self._listen_ripples = []
        self._listen_ripple_timer = 0

        # Speaking: synthetic voice-amplitude generator (no real mic feedback from TTS,
        # so this blends a couple of sine waves with drifting phase for an organic feel)
        self._speak_phase = random.uniform(0, math.tau)
        self._speak_amp_disp = 0.0

        self._gear_angle1 = 0.0
        self._gear_angle2 = 180.0
        self._hud_angle = 45.0

        # Widget Canvas
        self.canvas = tk.Canvas(
            self.root, width=self.SIZE, height=self.SIZE,
            bg=self.BG, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        # Internal Binds
        self.canvas.bind("<Button-1>", self._on_local_press)
        self.canvas.bind("<B1-Motion>", self._on_local_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_local_release)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.canvas.bind("<Enter>", lambda e: self._on_hover(True))
        self.canvas.bind("<Leave>", lambda e: self._on_hover(False))

        # Context Menu
        self.menu = tk.Menu(
            self.root, tearoff=0, bg="#0d0e12", fg="#ff0033",
            activebackground="#ff0033", activeforeground="#ffffff",
            font=("Consolas", 9, "bold")
        )
        self.menu.add_command(
            label="👁️ STEALTH HIDE / RESTORE ALL (F6)",
            command=self.toggle_all
        )
        self.menu.add_command(
            label="📑 DOC AGENT (CODETANTRA & LAB EXPERIMENTS)",
            command=self.open_doc_agent
        )
        self.menu.add_command(
            label="📧 SWITCH EMAIL ACCOUNT",
            command=self._switch_email_account
        )
        self.menu.add_command(
            label="🛑 TOGGLE LASERS (OFF/ON)",
            command=self.toggle_lasers
        )
        self.menu.add_separator()
        self.menu.add_command(label="TERMINATE RECORDING", command=self._stop_recording)
        self.menu.add_command(label="MUTE VOICE", command=self._stop_speaking)
        self.menu.add_separator()
        self.menu.add_command(
            label="TEST LOCAL NEURAL VOICE",
            command=self._test_local_neural_voice,
        )
        self.menu.add_separator()
        self.menu.add_command(label="LOOP: RAGE", command=lambda: self.set_loop_emotion("RAGE"))
        self.menu.add_command(label="LOOP: ANALYTICAL", command=lambda: self.set_loop_emotion("ANALYTICAL"))
        self.menu.add_command(label="LOOP: STEALTH", command=lambda: self.set_loop_emotion("STEALTH"))
        self.menu.add_command(label="LOOP: CURIOUS", command=lambda: self.set_loop_emotion("CURIOUS"))
        self.menu.add_command(label="LOOP: ALERT", command=lambda: self.set_loop_emotion("ALERT"))
        self.menu.add_command(label="LOOP: SLEEP", command=lambda: self.set_loop_emotion("SLEEP"))
        self.menu.add_command(label="LOOP: IDLE", command=lambda: self.set_loop_emotion("IDLE"))
        self.menu.add_separator()
        self.menu.add_command(label="RE-CALIBRATE POSITION", command=self._reset_position)
        self.menu.add_command(label="PURGE CORE (EXIT)", command=self._exit)

        # Start Global Desktop Mouse Hook Thread
        self._mouse_listener = mouse.Listener(
            on_click=self._on_global_click,
            on_move=self._on_global_move
        )
        self._mouse_listener.start()

        # Start Global Keyboard Listener for Unified F6 Toggle (Minimizes / Restores Both)
        self._init_global_hotkeys()

        self._animate()
        self.root.after(1000, self._keep_on_top)

    def _keep_on_top(self):
        try:
            if self.root.winfo_exists() and not getattr(self, "_is_hidden", False):
                self.root.attributes("-topmost", True)
                self.root.after(2000, self._keep_on_top)
            else:
                self.root.after(1000, self._keep_on_top)
        except Exception:
            pass

    def hide(self):
        """Minimizes / hides Ultron mic into stealth mode."""
        self._is_hidden = True
        self.root.withdraw()
        if hasattr(self, "overlay") and self.overlay:
            self.overlay.hide()

    def show(self):
        """Restores / maximizes Ultron mic on top."""
        self._is_hidden = False
        self.root.deiconify()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.lift()

    def toggle_all(self):
        """Unified toggle: minimizes or restores both Mic and Doc Agent together."""
        ultron_sync.toggle_all(self)

    def _init_global_hotkeys(self):
        """Listens globally for F6 to toggle both Ultron Mic and Doc Agent."""
        def _on_press(key):
            try:
                if key == Key.f6:
                    self.root.after(0, self.toggle_all)
            except Exception:
                pass

        try:
            self._key_listener = KeyboardListener(on_press=_on_press)
            self._key_listener.daemon = True
            self._key_listener.start()
        except Exception as e:
            print(f"[mic_button] Hotkey listener warning: {e}")

    def set_loop_emotion(self, emotion_name):
        if emotion_name in self.COLOR_MAP:
            self._base_loop_emotion = emotion_name
            self.emotion = emotion_name
            if emotion_name in ("ERROR", "RAGE"):
                self._trigger_glitch(duration_frames=12)

    def _get_language_ring_color(self, emotion_color):
        return emotion_color

    def _spawn_particle(self):
        cx = cy = self.SIZE / 2
        angle = random.uniform(0, math.tau)
        dist = random.uniform(15, self.SIZE / 2 - 2)
        return {
            "x": cx + dist * math.cos(angle),
            "y": cy + dist * math.sin(angle),
            "speed": random.uniform(0.3, 1.6),
            "angle": angle,
            "size": random.randint(1, 3),
            "alpha": random.uniform(0.2, 0.85)
        }

    def _trigger_glitch(self, duration_frames=6):
        self._glitch_timer = duration_frames
        self._is_blinking = True
        self._blink_direction = -1

    # --- GLOBAL MOUSE HOOK HANDLERS (screen-wide lasers only) ---
    def _on_global_click(self, x, y, button, pressed):
        if not self.laser_enabled:
            return
        """Fires lasers anywhere on screen. Clicks that land on the eye itself are
        left alone here - they're handled locally (drag-to-move / toggle-record)
        so the eye never fires a laser at itself while you pick it up to move it."""
        now = time.time()

        try:
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
        except Exception:
            wx, wy = -9999, -9999
        inside_widget = wx <= x <= wx + self.SIZE and wy <= y <= wy + self.SIZE

        if pressed:
            is_double_click = (now - self._last_click_time < 0.28)
            self._last_click_time = now

            if is_double_click:
                self._laser_active = False
                self.root.after(0, self.overlay.hide)
                return

            if inside_widget:
                # Reserved for the local drag / click handlers below.
                return

            if button == mouse.Button.left:
                self._laser_type = "RED_PLASMA"
            elif button == mouse.Button.middle:
                self._laser_type = "CYAN_CYBER"
            else:
                return

            self._laser_active = True
            self.root.after(0, self.overlay.show)
            self._update_laser_target(x, y)
        else:
            self._laser_active = False
            self.root.after(0, self.overlay.hide)

    def _on_global_move(self, x, y):
        if not self.laser_enabled:
            return
        if self._laser_active:
            self._update_laser_target(x, y)

    def _update_laser_target(self, root_x, root_y):
        self._laser_target_root = (root_x, root_y)

        win_x = self.root.winfo_x()
        win_y = self.root.winfo_y()

        local_tx = root_x - win_x
        local_ty = root_y - win_y

        dx = local_tx - (self.SIZE / 2)
        dy = local_ty - (self.SIZE / 2)
        dist = math.hypot(dx, dy) or 1.0
        self._target_look_x = (dx / dist) * 10.0
        self._target_look_y = (dy / dist) * 6.0

    # --- LOCAL DRAG / CLICK HANDLERS (the eye itself) ---
    def _on_local_press(self, event):
        self._press_pos_root = (event.x_root, event.y_root)
        self._press_win_pos = (self.root.winfo_x(), self.root.winfo_y())
        self._drag_moved = False
        self.is_dragging = True

    def _on_local_drag(self, event):
        if self._press_pos_root is None:
            return
        dx = event.x_root - self._press_pos_root[0]
        dy = event.y_root - self._press_pos_root[1]

        if not self._drag_moved and (abs(dx) > self.DRAG_THRESHOLD or abs(dy) > self.DRAG_THRESHOLD):
            self._drag_moved = True
            self._moved = True
            # Picking the eye up to move it is not "firing a laser at yourself" -
            # cancel whatever the global hook may have started on press.
            self._laser_active = False
            self.root.after(0, self.overlay.hide)

        if self._drag_moved:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            nx = self._press_win_pos[0] + dx
            ny = self._press_win_pos[1] + dy
            nx = max(0, min(nx, max(0, sw - self.SIZE)))
            ny = max(0, min(ny, max(0, sh - self.SIZE)))
            self.root.geometry(f"+{nx}+{ny}")

    def _on_local_release(self, event):
        self.is_dragging = False
        moved = self._drag_moved
        self._press_pos_root = None
        self._drag_moved = False

        if moved:
            _save_pos(self.root.winfo_x(), self.root.winfo_y())
            return

        # A clean click on the eye (no drag) toggles recording, same as before.
        self._on_click()

    def _animate(self):
        try:
            self._t += 1

            if self.is_typing:
                self.emotion = "PROCESSING"
            elif self.is_recording:
                self.emotion = "LISTENING"
            elif self.is_transcribing:
                self.emotion = "PROCESSING"
            elif self.is_speaking:
                self.emotion = "SPEAKING"
            else:
                self.emotion = self._base_loop_emotion

            if self.emotion == "IDLE":
                t_height, t_slant, t_pupil = 1.0, 2.0, 1.0
                blink_interval = 110
            elif self.emotion == "CURIOUS":
                t_height, t_slant, t_pupil = 1.4, -6.0, 1.35
                blink_interval = 70
            elif self.emotion == "ALERT":
                t_height, t_slant, t_pupil = 0.6, 12.0, 0.7
                blink_interval = 40
            elif self.emotion == "LISTENING":
                t_height, t_slant, t_pupil = 0.7, 8.0, 1.25 + random.uniform(-0.15, 0.15)
                blink_interval = 120
            elif self.emotion == "PROCESSING":
                t_height, t_slant, t_pupil = 0.35, 0.0, 0.6
                blink_interval = 200
            elif self.emotion == "SPEAKING":
                pulse = self._speak_amp_disp
                t_height = 0.55 + 0.45 * pulse
                t_slant = 3.0 * math.sin(self._t * 0.2)
                t_pupil = 0.9 + 0.3 * pulse
                blink_interval = 95
            elif self.emotion == "SUCCESS":
                t_height, t_slant, t_pupil = 1.25, -4.0, 1.15
                blink_interval = 150
            elif self.emotion == "ERROR":
                t_height, t_slant, t_pupil = 0.5 + random.uniform(-0.2, 0.2), 10.0, 1.4
                blink_interval = 15
            elif self.emotion == "SLEEP":
                t_height, t_slant, t_pupil = 0.05, 0.0, 0.2
                blink_interval = 500
            elif self.emotion == "RAGE":
                t_height, t_slant, t_pupil = 0.8, 16.0, 1.5 + random.uniform(-0.2, 0.2)
                blink_interval = 25
            elif self.emotion == "ANALYTICAL":
                t_height, t_slant, t_pupil = 0.9, 0.0, 0.85
                blink_interval = 130
            elif self.emotion == "STEALTH":
                t_height, t_slant, t_pupil = 0.45, 4.0, 0.5
                blink_interval = 180

            self._eye_height += (t_height - self._eye_height) * 0.2
            self._brow_slant += (t_slant - self._brow_slant) * 0.2
            self._pupil_scale += (t_pupil - self._pupil_scale) * 0.2

            target_color = self.COLOR_MAP.get(self.emotion, self.COLOR_MAP["IDLE"])
            self._curr_color = _lerp(self._curr_color, target_color, 0.2)

            iris_speed = 8.0 if self.emotion == "RAGE" else (4.0 if self.emotion == "PROCESSING" else 1.0)
            self._iris_rotation = (self._iris_rotation + iris_speed) % 360
            self._glint_angle = (self._glint_angle + 0.6) % 360

            if self.emotion == "ANALYTICAL":
                self._scan_line_y += 1.5
                if self._scan_line_y > 25:
                    self._scan_line_y = -25

            # --- Listening: reactive bars + outward "hearing" ripples ---
            if self.emotion == "LISTENING":
                self._listen_ripple_timer += 1
                if self._listen_ripple_timer >= 10:
                    self._listen_ripple_timer = 0
                    self._listen_ripples.append({"r": 0.0, "alpha": 1.0})
                for rip in self._listen_ripples:
                    rip["r"] += 2.0
                    rip["alpha"] -= 0.025
                self._listen_ripples = [r for r in self._listen_ripples if r["alpha"] > 0.02]

                avg_level = sum(self._bar_levels) / len(self._bar_levels)
                for i in range(len(self._bar_levels)):
                    self._bar_phases[i] += 0.4 + i * 0.05
                    target = 0.25 + 0.75 * abs(math.sin(self._bar_phases[i]))
                    target *= 0.75 + 0.35 * math.sin(self._t * 0.15 + i)
                    self._bar_levels[i] += (target - self._bar_levels[i]) * 0.4
                self._listen_avg_level = avg_level
            else:
                for i in range(len(self._bar_levels)):
                    self._bar_levels[i] += (0.15 - self._bar_levels[i]) * 0.1
                self._listen_ripples = []
                self._listen_avg_level = 0.0

            # --- Speaking: synthetic voice-amplitude pulse ---
            if self.is_speaking:
                self._speak_phase += 0.3 + 0.12 * math.sin(self._t * 0.05)
                amp = (((math.sin(self._speak_phase) + 1) / 2) * 0.65
                       + ((math.sin(self._speak_phase * 2.4 + 1.3) + 1) / 2) * 0.35)
                self._speak_amp_disp += (amp - self._speak_amp_disp) * 0.35
            else:
                self._speak_amp_disp *= 0.85

            if not self._is_blinking and self._t >= self._next_blink_time and self.emotion != "SLEEP":
                self._is_blinking = True
                self._blink_direction = -1

            if self._is_blinking:
                speed = 0.4 if self.emotion in ("ALERT", "ERROR", "RAGE") else 0.2
                if self._blink_direction == -1:
                    self._blink_progress -= speed
                    if self._blink_progress <= 0.05:
                        self._blink_progress = 0.05
                        self._blink_direction = 1
                else:
                    self._blink_progress += speed
                    if self._blink_progress >= 1.0:
                        self._blink_progress = 1.0
                        self._is_blinking = False
                        self._next_blink_time = self._t + blink_interval

            if self._t >= self._next_look_time and not self._laser_active:
                if self.emotion in ("CURIOUS", "ANALYTICAL"):
                    self._target_look_x = random.choice([-8.0, -4.0, 4.0, 8.0])
                    self._target_look_y = random.choice([-4.0, 0.0, 4.0])
                    self._next_look_time = self._t + random.randint(15, 35)
                elif self.emotion in ("ALERT", "RAGE"):
                    self._target_look_x = random.choice([-9.0, 0.0, 9.0])
                    self._target_look_y = random.choice([-3.0, 3.0])
                    self._next_look_time = self._t + random.randint(10, 20)
                elif self.emotion in ("PROCESSING", "SLEEP", "STEALTH", "SPEAKING"):
                    self._target_look_x = 0.0
                    self._target_look_y = 0.0
                    self._next_look_time = self._t + 60
                else:
                    self._target_look_x = random.choice([-5.0, 0.0, 0.0, 5.0])
                    self._target_look_y = random.choice([-2.0, 0.0, 2.0])
                    self._next_look_time = self._t + random.randint(40, 90)

            self._eye_look_x += (self._target_look_x - self._eye_look_x) * 0.25
            self._eye_look_y += (self._target_look_y - self._eye_look_y) * 0.25

            if self._glitch_timer > 0:
                self._glitch_timer -= 1
                self._glitch_offset = (random.randint(-5, 5), random.randint(-5, 5))
            elif self.emotion in ("ERROR", "RAGE") or random.random() < 0.02:
                self._glitch_offset = (random.randint(-2, 2), random.randint(-2, 2))
            else:
                self._glitch_offset = (0, 0)

            p_speed_mult = 3.5 if self.emotion == "RAGE" else (2.0 if self.emotion in ("PROCESSING", "ALERT") else (0.3 if self.emotion == "SLEEP" else 1.0))
            for p in self._particles:
                p["angle"] += 0.02 * p["speed"] * p_speed_mult
                p["y"] -= 0.4 * p["speed"] * p_speed_mult
                if p["y"] < 0 or p["y"] > self.SIZE:
                    p["y"] = self.SIZE - 5
                    p["x"] = self.SIZE / 2 + random.randint(-35, 35)

            gear_speed = 15 if self.emotion == "RAGE" else (10 if self.emotion == "PROCESSING" else 1.5)
            self._gear_angle1 = (self._gear_angle1 + gear_speed) % 360
            self._gear_angle2 = (self._gear_angle2 - (gear_speed * 1.3)) % 360
            self._hud_angle = (self._hud_angle + 0.8) % 360

            if self._laser_active:
                tx, ty = self._laser_target_root
                for _ in range(4):
                    ang = random.uniform(0, math.tau)
                    spd = random.uniform(3, 8)
                    self._laser_impact_particles.append({
                        "x": tx, "y": ty,
                        "vx": math.cos(ang) * spd,
                        "vy": math.sin(ang) * spd,
                        "life": 1.0
                    })

            for lp in self._laser_impact_particles:
                lp["x"] += lp["vx"]
                lp["y"] += lp["vy"]
                lp["life"] -= 0.12
            self._laser_impact_particles = [lp for lp in self._laser_impact_particles if lp["life"] > 0]

            self._render()
        except tk.TclError:
            self._anim_job = None
            return
        except Exception as e:
            print(f"[mic_button] render error: {e}")

        try:
            self._anim_job = self.root.after(self.FRAME_MS, self._animate)
        except tk.TclError:
            self._anim_job = None

    def _render(self):
        c = self.canvas
        c.delete("all")

        s = self.SIZE
        gx, gy = self._glitch_offset
        cx = s / 2 + gx
        cy = s / 2 + gy
        base_r = s / 2 - self.RING_PAD
        primary = self._curr_color
        ring_color = self._get_language_ring_color(primary)

        if self._glitch_timer > 0 and random.random() < 0.4:
            primary = "#ffffff" if random.random() < 0.5 else "#ff0055"

        breath_freq = 0.2 if self.emotion in ("ALERT", "RAGE") else (0.03 if self.emotion == "SLEEP" else 0.07)
        breath = (math.sin(self._t * breath_freq) + 1) / 2

        for p in self._particles:
            col = _blend_with_bg(primary, p["alpha"] * (0.2 if self.emotion == "SLEEP" else 1.0), self.BG)
            c.create_rectangle(p["x"] - p["size"] + gx, p["y"] - p["size"] + gy,
                               p["x"] + p["size"] + gx, p["y"] + p["size"] + gy,
                               fill=col, outline="")

        halo_r = base_r + (10 * breath if self.emotion in ("ALERT", "RAGE") else 4 * breath)
        halo_color = _blend_with_bg(ring_color, 0.9, self.BG)

        for width, offset in ((8, 0), (4, 2), (1, 3)):
            rr = halo_r - offset
            c.create_oval(
                cx - rr, cy - rr, cx + rr, cy + rr,
                outline=halo_color, width=width,
            )

        sp_r = base_r + 4
        for deg in (0, 120, 240):
            c.create_arc(cx - sp_r, cy - sp_r, cx + sp_r, cy + sp_r,
                         start=(self._gear_angle1 + deg) % 360, extent=70, style="arc", outline=ring_color, width=2)

        reticle_r = base_r + 8
        for deg in range(0, 360, 45):
            rad = math.radians(deg + self._hud_angle)
            rx1 = cx + reticle_r * math.cos(rad)
            ry1 = cy + reticle_r * math.sin(rad)
            rx2 = cx + (reticle_r + 4) * math.cos(rad)
            ry2 = cy + (reticle_r + 4) * math.sin(rad)
            c.create_line(rx1, ry1, rx2, ry2, fill=_blend_with_bg(ring_color, 0.75, self.BG), width=1.5)

        c.create_oval(
            cx - base_r, cy - base_r, cx + base_r, cy + base_r,
            outline=_blend_with_bg(ring_color, 0.75, self.BG), width=3,
        )
        inner_r = base_r - 5
        c.create_oval(cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r, fill="#08090d", outline="")

        if self.emotion == "LISTENING":
            self._draw_listen_ripples(cx, cy, primary)
            self._draw_waveform_bars(cx, cy, primary)
            self._draw_expressive_eye(cx, cy, primary)
        elif self.emotion == "PROCESSING":
            self._draw_processing_core(cx, cy, primary)
        elif self.emotion == "SPEAKING":
            self._draw_expressive_eye(cx, cy, primary)
            self._draw_speaking_pulse(cx, cy, primary)
        else:
            self._draw_expressive_eye(cx, cy, primary)

        if self._laser_active:
            self._render_fullscreen_laser()

    def _draw_expressive_eye(self, cx, cy, color):
        c = self.canvas

        eye_cx = cx + self._eye_look_x
        eye_cy = cy + self._eye_look_y

        base_w = 32
        base_h = 15

        eye_w = base_w
        eye_h = max(1, base_h * self._eye_height * self._blink_progress)

        c.create_oval(eye_cx - eye_w / 2, eye_cy - eye_h / 2,
                      eye_cx + eye_w / 2, eye_cy + eye_h / 2,
                      outline=color, width=2)

        if abs(self._brow_slant) > 0.5 and eye_h > 3:
            brow_y = eye_cy - (eye_h / 2) - 3
            slant = self._brow_slant
            c.create_line(eye_cx - 15, brow_y - slant * 0.5,
                          eye_cx - 2, brow_y + slant * 0.5,
                          fill=color, width=2, capstyle="round")
            c.create_line(eye_cx + 2, brow_y + slant * 0.5,
                          eye_cx + 15, brow_y - slant * 0.5,
                          fill=color, width=2, capstyle="round")

        pupil_cx = eye_cx + (self._eye_look_x * 0.2)
        pupil_cy = eye_cy + (self._eye_look_y * 0.2)
        # Subtle idle "breathing" on top of the emotion-driven pupil scale,
        # so the eye never looks perfectly frozen even when nothing else changes.
        iris_r = 9 * self._pupil_scale * (1 + 0.04 * math.sin(self._t * 0.12))

        if eye_h > 3 and self.emotion != "SLEEP":
            for i in range(6):
                blade_angle = math.radians(self._iris_rotation + (i * 60))
                bx = pupil_cx + iris_r * math.cos(blade_angle)
                by = pupil_cy + (iris_r * self._eye_height * self._blink_progress) * math.sin(blade_angle)
                bx2 = pupil_cx + (iris_r * 0.4) * math.cos(blade_angle + 0.8)
                by2 = pupil_cy + (iris_r * 0.4 * self._eye_height * self._blink_progress) * math.sin(blade_angle + 0.8)
                c.create_line(bx, by, bx2, by2, fill=_blend_with_bg(color, 0.7, self.BG), width=1.5)

        pupil_w = 7 * self._pupil_scale
        pupil_h = max(1, (9 * self._pupil_scale * self._eye_height) * self._blink_progress)

        if pupil_h > 1.5 and self.emotion != "SLEEP":
            glow_pupil = _lerp(color, "#ffffff", 0.75)
            c.create_oval(pupil_cx - pupil_w / 2, pupil_cy - pupil_h / 2,
                          pupil_cx + pupil_w / 2, pupil_cy + pupil_h / 2,
                          fill=glow_pupil, outline="")

            # Small rotating glint - a glossy highlight so the eye reads as
            # a living lens rather than a flat colored shape.
            glint_rad = math.radians(self._glint_angle)
            glint_x = pupil_cx + iris_r * 0.45 * math.cos(glint_rad)
            glint_y = pupil_cy + iris_r * 0.3 * math.sin(glint_rad) - pupil_h * 0.15
            c.create_oval(glint_x - 1.6, glint_y - 1.2, glint_x + 1.6, glint_y + 1.2,
                          fill="#ffffff", outline="")

        if self.emotion == "ANALYTICAL" and eye_h > 3:
            scan_y = eye_cy + self._scan_line_y
            if eye_cy - eye_h / 2 <= scan_y <= eye_cy + eye_h / 2:
                c.create_line(eye_cx - eye_w / 2 + 2, scan_y, eye_cx + eye_w / 2 - 2, scan_y, fill="#ffffff", width=2)

        c.create_line(eye_cx - eye_w / 2 - 2, eye_cy, eye_cx - eye_w / 2 - 7, eye_cy, fill=color, width=2)
        c.create_line(eye_cx + eye_w / 2 + 2, eye_cy, eye_cx + eye_w / 2 + 7, eye_cy, fill=color, width=2)

    def _draw_waveform_bars(self, cx, cy, color):
        c = self.canvas
        bars = self._bar_levels
        n = len(bars)
        bar_w = 4
        gap = 3
        total_w = n * bar_w + (n - 1) * gap
        x0 = cx - total_w / 2
        max_h = 22
        for i, level in enumerate(bars):
            h = max(3, level * max_h)
            x1 = x0 + i * (bar_w + gap)
            y1 = cy - h / 2 + 18
            y2 = cy + h / 2 + 18
            bar_color = _blend_with_bg(color, 0.4 + 0.6 * level, self.BG)
            c.create_line(x1 + bar_w / 2, y1, x1 + bar_w / 2, y2, fill=bar_color, width=bar_w, capstyle="round")

    def _draw_listen_ripples(self, cx, cy, color):
        """Rings expanding outward while recording, like sound being drawn in."""
        c = self.canvas
        base_r = self.SIZE / 2 - self.RING_PAD
        for rip in self._listen_ripples:
            rr = base_r + rip["r"]
            if rr <= base_r + 26:
                col = _blend_with_bg(color, rip["alpha"] * 0.85, self.BG)
                c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=col, width=2)

    def _draw_speaking_pulse(self, cx, cy, color):
        """A small voice-amplitude meter + pulse ring beneath the eye while Ultron talks."""
        c = self.canvas
        amp = self._speak_amp_disp

        bars = 5
        bar_w = 4
        gap = 3
        total_w = bars * bar_w + (bars - 1) * gap
        x0 = cx - total_w / 2
        base_y = cy + 22
        for i in range(bars):
            local_amp = amp * (0.5 + 0.5 * math.sin(self._t * 0.3 + i * 1.1))
            h = 3 + local_amp * 16
            x1 = x0 + i * (bar_w + gap)
            col = _blend_with_bg(color, 0.4 + 0.6 * local_amp, self.BG)
            c.create_line(x1 + bar_w / 2, base_y - h / 2, x1 + bar_w / 2, base_y + h / 2,
                         fill=col, width=bar_w, capstyle="round")

        ring_r = 14 + amp * 10
        ring_col = _blend_with_bg(color, 0.3 + 0.5 * amp, self.BG)
        c.create_oval(cx - ring_r, base_y - ring_r * 0.4, cx + ring_r, base_y + ring_r * 0.4,
                     outline=ring_col, width=2)

    def _draw_processing_core(self, cx, cy, color):
        c = self.canvas
        r = 16

        # A faint closed-eye slit beneath the core - Ultron is still "there", just focused.
        c.create_line(cx - 14, cy + 24, cx + 14, cy + 24,
                      fill=_blend_with_bg(color, 0.35, self.BG), width=2)

        # Slow outer ring for depth
        outer_r = r + 14
        c.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                      outline=_blend_with_bg(color, 0.25, self.BG), width=1)

        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=self._gear_angle1 * 3, extent=120,
                     style="arc", outline=color, width=3)
        c.create_arc(cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4, start=self._gear_angle2 * 2, extent=140,
                     style="arc", outline=_lerp(color, "#ffffff", 0.6), width=2)

        # Orbiting "data-bit" dots - makes the thinking state feel busier/alive.
        for i, phase in enumerate((0, 180)):
            ang = math.radians(self._hud_angle * (2 if i == 0 else -2.4) + phase)
            ox = cx + outer_r * math.cos(ang)
            oy = cy + outer_r * math.sin(ang)
            c.create_rectangle(ox - 2, oy - 2, ox + 2, oy + 2,
                               fill=_lerp(color, "#ffffff", 0.3), outline="")

        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#ffffff", outline="")

    def _render_fullscreen_laser(self):
        ov = self.overlay.canvas
        ov.delete("all")

        win_x = self.root.winfo_x()
        win_y = self.root.winfo_y()

        eye_center_x = win_x + (self.SIZE / 2) + self._eye_look_x
        eye_center_y = win_y + (self.SIZE / 2) + self._eye_look_y

        orig_l = (eye_center_x - 8, eye_center_y)
        orig_r = (eye_center_x + 8, eye_center_y)

        tx, ty = self._laser_target_root

        if self._laser_type == "RED_PLASMA":
            for ox, oy in (orig_l, orig_r):
                jx = random.uniform(-2, 2)
                jy = random.uniform(-2, 2)
                ov.create_line(ox, oy, tx + jx, ty + jy, fill="#ff0033", width=8, capstyle="round")
                ov.create_line(ox, oy, tx + jx, ty + jy, fill="#ff7700", width=4, capstyle="round")
                ov.create_line(ox, oy, tx, ty, fill="#ffffff", width=2, capstyle="round")

            for lp in self._laser_impact_particles:
                ov.create_line(lp["x"], lp["y"], lp["x"] + lp["vx"], lp["y"] + lp["vy"], fill="#ffcc00", width=2)
            ov.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill="#ffffff", outline="#ff0033", width=3)

        elif self._laser_type == "CYAN_CYBER":
            for ox, oy in (orig_l, orig_r):
                ov.create_line(ox, oy, tx, ty, fill="#00f0ff", width=5, capstyle="round")
                ov.create_line(ox, oy, tx, ty, fill="#00ffaa", width=2, capstyle="round")

            r_pulse = 10 + 4 * math.sin(self._t * 0.5)
            ov.create_oval(tx - r_pulse, ty - r_pulse, tx + r_pulse, ty + r_pulse, outline="#00f0ff", width=2)
            ov.create_line(tx - 15, ty, tx + 15, ty, fill="#00f0ff", width=1.5)
            ov.create_line(tx, ty - 15, tx, ty + 15, fill="#00f0ff", width=1.5)

    def _on_hover(self, entering):
        self._hover = entering

    def _show_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _reset_position(self):
        self.root.geometry(f"+{DEFAULT_POS['x']}+{DEFAULT_POS['y']}")
        _save_pos(DEFAULT_POS["x"], DEFAULT_POS["y"])

    def _stop_speaking(self):
        try:
            stop_speaking()
        except Exception:
            pass

    def _test_local_neural_voice(self):
        if self.is_speaking:
            messagebox.showinfo(
                "Ultron is speaking",
                "Wait for the current speech to finish, then try the voice test again.",
                parent=self.root,
            )
            return

        self.is_speaking = True

        def test_voice():
            try:
                succeeded = voice_module.speak(
                    "Sir, this is Ultron. Your local neural voice is online."
                ) in ("local-piper", "edge-ultron", "rvc-ultron")
                self.root.after(
                    0,
                    self._finish_created_voice_test,
                    succeeded,
                )
            except Exception as error:
                voice_module._log_voice_event(
                    f"Floating mic local neural voice test failed "
                    f"({type(error).__name__})."
                )
                try:
                    self.root.after(
                        0,
                        lambda error=error: messagebox.showerror(
                            "Local neural voice test",
                            f"Voice test failed: {error}\n\n"
                            "Run setup_local_tts.bat, then see ultron_voice.log.",
                            parent=self.root,
                        ),
                    )
                except tk.TclError:
                    pass
            finally:
                self.is_speaking = False

        threading.Thread(target=test_voice, daemon=True).start()

    def _finish_created_voice_test(self, succeeded):
        self.is_speaking = False
        if succeeded:
            messagebox.showinfo(
                "Local neural voice test",
                "Ultron's local Piper neural voice played successfully.",
                parent=self.root,
            )
        else:
            messagebox.showerror(
                "Local neural voice test",
                "Ultron could not play the local neural voice. "
                "Run setup_local_tts.bat and check ultron_voice.log.",
                parent=self.root,
            )

    def _stop_recording(self):
        if self.is_recording:
            try:
                stop_listening()
            except Exception:
                pass

    def _switch_email_account(self):
        try:
            from inbox import switch_active_account
            new_email = switch_active_account()
            print(f"[Ultron] Switched active email to: {new_email}")
            self.show_transcription_pill(f"Email: {new_email}")
        except Exception as e:
            print(f"[mic_button] error switching email: {e}")

    def open_doc_agent(self):
        try:
            import subprocess
            base_dir = os.path.dirname(os.path.abspath(__file__))
            doc_script = os.path.join(base_dir, "doc_agent.py")
            venv_pythonw = os.path.join(base_dir, "venv", "Scripts", "pythonw.exe")
            py_exec = venv_pythonw if os.path.isfile(venv_pythonw) else sys.executable
            subprocess.Popen([py_exec, doc_script])
        except Exception as e:
            print(f"[mic_button] error opening doc agent via subprocess: {e}")
            try:
                self.doc_agent = open_doc_agent(parent_root=self.root)
            except Exception as e2:
                print(f"[mic_button] fallback open_doc_agent failed: {e2}")

    def open_code_typer(self):
        """Legacy alias redirecting to Ultron Doc & Code Agent."""
        self.open_doc_agent()

    def toggle_lasers(self):
        self.laser_enabled = not self.laser_enabled
        if not self.laser_enabled:
            self._laser_active = False
            try:
                self.overlay.hide()
            except Exception:
                pass
            print("[mic_button] Desktop lasers DISABLED.")
            try:
                messagebox.showinfo("Laser System", "Desktop Lasers are now DISABLED.\nClicks will not trigger laser overlays.", parent=self.root)
            except Exception:
                pass
        else:
            print("[mic_button] Desktop lasers ENABLED.")
            try:
                messagebox.showinfo("Laser System", "Desktop Lasers are now ENABLED.", parent=self.root)
            except Exception:
                pass

    def _on_code_agent_state(self, is_typing):
        self.is_typing = is_typing
        if not is_typing:
            self.emotion = "SUCCESS"
            try:
                self.root.after(2000, lambda: setattr(self, "emotion", self._base_loop_emotion))
            except Exception:
                pass

    def _exit(self):
        try:
            _save_pos(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass
        if self._anim_job is not None:
            try:
                self.root.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

        if hasattr(self, "_key_listener") and self._key_listener:
            try:
                self._key_listener.stop()
            except Exception:
                pass
        self._mouse_listener.stop()
        self.overlay.top.destroy()
        self.root.destroy()

    # Workflow Actions
    def _on_click(self):
        if self.is_transcribing:
            return
        if not self.is_recording:
            self._start_recording()
        else:
            self._request_stop()

    def _start_recording(self):
        print("[mic_button] >>> Ultron core activated (listening)")
        self.is_recording = True
        self._tendrils = []
        self._tendril_timer = 0
        threading.Thread(target=self._record_worker, daemon=True).start()

    def _request_stop(self):
        print("[mic_button] >>> Ultron processing data")
        self.is_recording = False
        self.is_transcribing = True
        try:
            stop_listening()
        except Exception:
            pass

    def _record_worker(self):
        try:
            heard = listen(phrase_time_limit=60)
        except Exception as e:
            print(f"[mic_button] listen error: {type(e).__name__}: {e}")
            heard = ""

        self.is_recording = False
        self.is_transcribing = False

        if not heard:
            print("[mic_button] core idle: no audio input")
            self._trigger_glitch(duration_frames=30)
            return

        print(f"[mic_button] heard: {heard!r}")

        reply = process_message(heard, provider="auto")

        print(f"[mic_button] Ultron reply: {reply!r}")
        self._speak(reply)

    def _speak(self, text):
        self.is_speaking = True
        try:
            speak(text)
        except Exception as e:
            print(f"[mic_button] audio output error: {e}")
        finally:
            self.is_speaking = False


# ---------------------------------------------------------------
def _bring_existing_to_front():
    if "--force" in sys.argv:
        return False
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "ULTRON Core")
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetWindowPos(
                    hwnd, ctypes.wintypes.HWND(-1), 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0010 | 0x0040
                )
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                try:
                    ultron_sync.show_all()
                except Exception:
                    pass
                return True
        except Exception:
            pass
    return False


if __name__ == "__main__":
    if _bring_existing_to_front():
        sys.exit(0)
    root = tk.Tk()
    app = MicButton(root)
    root.mainloop()