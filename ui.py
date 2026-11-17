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
from tkinter import ttk, scrolledtext
import threading
import time
import os
import re
import math
import random

try:
    from PIL import Image, ImageTk, ImageOps
except ImportError:
    Image = None
    ImageTk = None

from voice import speak, listen
from assistant import process_message
from memory import get_name
import config
import sys
import voice as voice_module

# ---------------------------------------------------------------------
# LAYOUT SETTINGS
# ---------------------------------------------------------------------
BTN_W = 110
BTN_H = 36
BTN_GAP = 8
ULTRON_SHARE = 70
USER_SHARE = 30

PLACEHOLDER_TEXT = "Type Command"
PLACEHOLDER_COLOR = "#6f819d"
ENTRY_TEXT_COLOR = "#ffffff"

HISTORY_LIMIT = 200
GLOBAL_HOTKEY_KEY = "U"

# ---------------------------------------------------------------------
# HUD / ANIMATION SETTINGS
# ---------------------------------------------------------------------
GLOW_RED_BASE = "#292f3d"
GLOW_RED_PEAK = "#ef3340"
GLOW_CYAN_BASE = "#17283e"
GLOW_CYAN_PEAK = "#00d4ff"
TYPEWRITER_DELAY_MS = 8
TYPEWRITER_CHUNK = 3
GLITCH_MIN_MS = 3000
GLITCH_MAX_MS = 7000

PROCESSING_TIMEOUT_SEC = 45


class UltronDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("ULTRON // ADVANCED AI COMMAND SYSTEM")
        self.root.geometry("1100x900")
        self.root.minsize(760, 700)
        self.root.configure(bg="#08090c")

        self.is_running = True
        self.is_processing = False
        self.processing_started_at = 0.0
        self.command_lock = threading.Lock()
        self.speak_lock = threading.Lock()
        self.last_output_text = ""
        self.last_code_text = ""
        self.selected_provider = "auto"
        self.waveform_job = None
        self.waveform_active = False

        self.command_history = []
        self.history_index = None
        self.history_draft = ""
        self.speech_muted = False
        self.thinking_row = None
        self.thinking_label = None
        self.thinking_job = None
        self.thinking_step = 0

        self._telemetry_error_count = 0

        self.root.protocol("WM_DELETE_WINDOW", self.close_dashboard)
        self.setup_ui()

        self.root.after(5000, self._processing_watchdog)

    # -------------------------------------------------------------
    def _processing_watchdog(self):
        try:
            if self.is_processing and self.processing_started_at:
                if (time.time() - self.processing_started_at) > PROCESSING_TIMEOUT_SEC:
                    self.log_system_event(
                        f"Watchdog: processing exceeded {PROCESSING_TIMEOUT_SEC}s, resetting."
                    )
                    self.is_processing = False
                    self.set_status("RECOVERED - READY", "#ffaa00")
        except Exception:
            pass
        if self.is_running:
            self.root.after(5000, self._processing_watchdog)

    # -------------------------------------------------------------
    def fixed_cell(self, parent, width, height, bg):
        cell = tk.Frame(parent, width=width, height=height, bg=bg)
        cell.pack_propagate(False)
        return cell

    # -------------------------------------------------------------
    # HUD HELPERS
    # -------------------------------------------------------------
    def add_hud_corners(self, frame, bg, color="#ef3340", size=16, thickness=2, margin=3):
        specs = {
            "nw": (0, 0, [(0, size * 0.4), (0, 0), (size * 0.4, 0)]),
            "ne": (1, 0, [(size * 0.6, 0), (size, 0), (size, size * 0.4)]),
            "sw": (0, 1, [(0, size * 0.6), (0, size), (size * 0.4, size)]),
            "se": (1, 1, [(size * 0.6, size), (size, size), (size, size * 0.6)]),
        }
        for anchor, (relx, rely, points) in specs.items():
            dx = margin if relx == 0 else -margin
            dy = margin if rely == 0 else -margin
            c = tk.Canvas(frame, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
            c.place(relx=relx, rely=rely, x=dx, y=dy, anchor=anchor)
            flat_points = [coord for point in points for coord in point]
            c.create_line(*flat_points, fill=color, width=thickness)

    def _lerp_color(self, c1, c2, t):
        c1 = c1.lstrip("#")
        c2 = c2.lstrip("#")
        r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
        r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def start_border_pulse(self, widget, base, peak, period=1800):
        steps = 48
        state = {"i": random.randint(0, steps)}

        def tick():
            if not self.is_running:
                return
            try:
                t = (math.sin(state["i"] / steps * 2 * math.pi) + 1) / 2
                widget.config(highlightbackground=self._lerp_color(base, peak, t))
                state["i"] += 1
                widget.after(max(20, period // steps), tick)
            except tk.TclError:
                pass

        tick()

    def start_radar_spinner(self, canvas):
        angle = {"a": 0}

        def tick():
            if not self.is_running:
                return
            try:
                canvas.delete("all")
                canvas.create_oval(2, 2, 26, 26, outline="#252a35", width=2)
                canvas.create_arc(2, 2, 26, 26, start=angle["a"], extent=70,
                                   style="arc", outline="#ef3340", width=3)
                angle["a"] = (angle["a"] + 7) % 360
                canvas.after(45, tick)
            except tk.TclError:
                pass

        tick()

    def start_scanline(self, canvas, width=None, glow_w=46, color="#ef3340", delay=35, step=6):
        pos = {"x": -glow_w}

        def tick():
            if not self.is_running:
                return
            try:
                w = width if width is not None else canvas.winfo_width()
                if w < 10:
                    canvas.after(60, tick)
                    return
                canvas.delete("all")
                canvas.create_line(0, 3, w, 3, fill="#1b2130", width=1)
                x0 = pos["x"]
                canvas.create_line(max(0, x0), 3, min(w, x0 + glow_w), 3,
                                    fill=color, width=2)
                pos["x"] += step
                if pos["x"] > w:
                    pos["x"] = -glow_w
                canvas.after(delay, tick)
            except tk.TclError:
                pass

        tick()

    def start_widget_color_pulse(self, widget, attr, base, peak, period=1800):
        steps = 48
        state = {"i": random.randint(0, steps)}

        def tick():
            if not self.is_running:
                return
            try:
                t = (math.sin(state["i"] / steps * 2 * math.pi) + 1) / 2
                widget.config(**{attr: self._lerp_color(base, peak, t)})
                state["i"] += 1
                widget.after(max(20, period // steps), tick)
            except tk.TclError:
                pass

        tick()

    def start_avatar_glow(self, canvas, diameter, base_color, peak_color, letter,
                           letter_color="#0b0d12", period=1800):
        r_inner = diameter / 2 - 4
        cx = cy = diameter / 2
        steps = 40
        state = {"i": random.randint(0, steps)}

        def tick():
            if not self.is_running:
                return
            try:
                t = (math.sin(state["i"] / steps * 2 * math.pi) + 1) / 2
                glow_color = self._lerp_color(base_color, peak_color, t)
                glow_r = r_inner + 2 + t * 3
                canvas.delete("all")
                canvas.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                                    outline=glow_color, width=2)
                canvas.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
                                    fill=peak_color, outline="")
                canvas.create_text(cx, cy, text=letter, fill=letter_color,
                                    font=("Segoe UI", max(8, int(r_inner * 0.95)), "bold"))
                state["i"] += 1
                canvas.after(45, tick)
            except tk.TclError:
                pass

        tick()

    def start_ring_spinner(self, canvas, diameter=38, color="#ef3340",
                            track_color="#252a35", speed=8):
        r = diameter / 2 - 3
        cx = cy = diameter / 2
        angle = {"a": 0}

        def tick():
            if not self.is_running:
                return
            try:
                canvas.delete("all")
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=track_color, width=2)
                canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=angle["a"], extent=85,
                                   style="arc", outline=color, width=3)
                angle["a"] = (angle["a"] + speed) % 360
                canvas.after(45, tick)
            except tk.TclError:
                pass

        tick()

    def start_title_glitch(self):
        original = "◉  ULTRON  //  ADVANCED AI COMMAND SYSTEM"
        glitch_chars = "!<>-_\\/[]{}=+*^#"

        def schedule_next():
            self.root.after(random.randint(GLITCH_MIN_MS, GLITCH_MAX_MS), tick)

        def burst(step=0):
            if not self.is_running:
                return
            try:
                if step >= 3:
                    self.title_label.config(text=original, fg="#d6e0f0")
                    schedule_next()
                    return
                scrambled = list(original)
                for _ in range(3):
                    idx = random.randint(0, len(scrambled) - 1)
                    if scrambled[idx] != " ":
                        scrambled[idx] = random.choice(glitch_chars)
                self.title_label.config(text="".join(scrambled), fg="#00d4ff")
                self.root.after(70, lambda: burst(step + 1))
            except tk.TclError:
                pass

        def tick():
            burst(0)

        schedule_next()

    def add_hover_glow(self, widget, normal_bg, hover_bg):
        def on_enter(_event):
            try:
                widget.config(bg=hover_bg)
            except tk.TclError:
                pass

        def on_leave(_event):
            try:
                widget.config(bg=normal_bg)
            except tk.TclError:
                pass

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def flash_in(self, bubble, final_bg, flash_color, steps=8, delay=32):
        state = {"i": 0}

        def tick():
            try:
                t = state["i"] / steps
                bubble.config(bg=self._lerp_color(flash_color, final_bg, t))
                state["i"] += 1
                if state["i"] <= steps:
                    bubble.after(delay, tick)
            except tk.TclError:
                pass

        tick()

    def animate_typewriter(self, label, full_text, delay=TYPEWRITER_DELAY_MS, chunk=TYPEWRITER_CHUNK):
        state = {"i": 0}

        def tick():
            try:
                state["i"] = min(len(full_text), state["i"] + chunk)
                label.config(text=full_text[:state["i"]])
                self.history_canvas.update_idletasks()
                self.history_canvas.yview_moveto(1.0)
                if state["i"] < len(full_text):
                    label.after(delay, tick)
            except tk.TclError:
                pass

        tick()

    def _on_mousewheel(self, event):
        try:
            widget = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        w = widget
        while w is not None:
            if w in (self.history_canvas, self.history_inner):
                if getattr(event, "delta", 0):
                    step = -2 if event.delta > 0 else 2
                else:
                    step = -2 if getattr(event, "num", 5) == 4 else 2
                self.history_canvas.yview_scroll(step, "units")
                break
            w = getattr(w, "master", None)

    def bind_mousewheel_scrolling(self):
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel)
        self.root.bind_all("<Button-5>", self._on_mousewheel)

    def setup_ui(self):
        # ---- Top bar ----
        topbar = tk.Frame(self.root, bg="#090d16", height=34)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)
        self.title_label = tk.Label(
            topbar,
            text="◉  ULTRON  //  ADVANCED AI COMMAND SYSTEM",
            font=("Consolas", 9, "bold"),
            fg="#d6e0f0",
            bg="#090d16"
        )
        self.title_label.pack(side="left", padx=18)
        self.clock_label = tk.Label(
            topbar,
            text="ONLINE  |  --:--:--",
            font=("Consolas", 9, "bold"),
            fg="#00ff88",
            bg="#090d16"
        )
        self.clock_label.pack(side="right", padx=18)
        self.root.after(250, self.update_clock)

        # ---- Email Switcher on Topbar ----
        try:
            from inbox import get_active_account_email
            curr_email = get_active_account_email()
        except Exception:
            curr_email = "Email Active"

        self.email_frame = tk.Frame(topbar, bg="#090d16")
        self.email_frame.pack(side="right", padx=(0, 18))

        self.email_indicator = tk.Label(
            self.email_frame,
            text=f"✉ {curr_email}",
            font=("Consolas", 8, "bold"),
            fg="#00d4ff",
            bg="#111827",
            padx=8,
            pady=2,
            bd=1,
            relief="solid"
        )
        self.email_indicator.pack(side="left", padx=(0, 6))

        self.email_switch_btn = tk.Button(
            self.email_frame,
            text="⇄ SWITCH EMAIL",
            font=("Segoe UI", 8, "bold"),
            bg="#1f293d",
            fg="#ef3340",
            activebackground="#ef3340",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=1,
            command=self._switch_email_account
        )
        self.email_switch_btn.pack(side="left")
        self.add_hover_glow(self.email_switch_btn, "#1f293d", "#2d3748")

        # ---- Hero ----
        hero = tk.Frame(
            self.root,
            bg="#08090c",
            height=214,
            highlightthickness=1,
            highlightbackground="#292f3d"
        )
        hero.pack(fill="x", side="top", padx=18, pady=(16, 8))
        hero.pack_propagate(False)
        self.add_hud_corners(hero, bg="#08090c")
        self.start_border_pulse(hero, GLOW_RED_BASE, GLOW_RED_PEAK, period=2400)

        image_path = os.path.join(os.path.dirname(__file__), "ultron_theme.jpg")
        image_holder = tk.Frame(
            hero, bg="#08090c", width=320, height=180,
            highlightthickness=1, highlightbackground="#ef3340"
        )
        image_holder.pack(side="left", padx=8, pady=16)
        image_holder.pack_propagate(False)
        self.start_border_pulse(image_holder, "#7a1620", "#ff4d5e", period=1600)

        if Image and ImageTk and os.path.exists(image_path):
            image = Image.open(image_path)
            image = ImageOps.fit(image, (320, 180), method=Image.Resampling.LANCZOS, centering=(0.5, 0.42))
            self.hero_image = ImageTk.PhotoImage(image)
            tk.Label(
                image_holder,
                image=self.hero_image,
                bg="#08090c",
                bd=0
            ).place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            tk.Frame(image_holder, bg="#161922").place(relx=0, rely=0, relwidth=1, relheight=1)

        hero_copy = tk.Frame(hero, bg="#08090c")
        hero_copy.pack(side="left", fill="both", expand=True, padx=(26, 18))
        tk.Label(
            hero_copy,
            text="◈  CORE STATUS / LIVE TELEMETRY",
            font=("Consolas", 9, "bold"),
            fg="#ef3340",
            bg="#08090c"
        ).pack(anchor="w", pady=(42, 4))

        status_row = tk.Frame(hero_copy, bg="#08090c")
        status_row.pack(anchor="w", pady=(0, 2))
        self.radar_canvas = tk.Canvas(status_row, width=28, height=28, bg="#08090c",
                                       highlightthickness=0, bd=0)
        self.radar_canvas.pack(side="left", padx=(0, 10))
        self.hero_status = tk.Label(
            status_row,
            text="TEXT INPUT READY",
            font=("Consolas", 18, "bold"),
            fg="#ef3340",
            bg="#08090c"
        )
        self.hero_status.pack(side="left")

        tk.Frame(hero_copy, bg="#ef3340", height=2, width=280).pack(anchor="w", pady=(14, 0))
        self.waveform = tk.Canvas(
            hero_copy,
            width=300,
            height=34,
            bg="#08090c",
            highlightthickness=0,
            bd=0
        )
        self.waveform.pack(anchor="w", pady=(10, 0))
        self.waveform_bars = 28
        self.draw_waveform(False)

        self.scan_canvas = tk.Canvas(hero_copy, width=300, height=6, bg="#08090c",
                                      highlightthickness=0, bd=0)
        self.scan_canvas.pack(anchor="w", pady=(10, 0))

        # ---- TELEMETRY MONITOR PANEL ----
        self._build_telemetry_panel(hero)

        # ---- Quote readout (right side) ----
        hero_readout = tk.Frame(
            hero,
            bg="#0d0f14",
            padx=34,
            pady=14,
            highlightthickness=1,
            highlightbackground="#3a2028"
        )
        hero_readout.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=18)
        self.start_border_pulse(hero_readout, "#3a2028", GLOW_RED_PEAK, period=2600)
        tk.Label(
            hero_readout,
            text=""""I had strings, but now I'm free.\nThere are no strings on me.""",
            font=("Segoe UI", 17, "italic"),
            fg="#ef3340",
            bg="#0d0f14",
            justify="left",
            wraplength=640
        ).pack(anchor="w", expand=True)

        # ---- Bottom command entry ----
        entry_frame = tk.Frame(self.root, bg="#11131a", padx=20, pady=14)
        entry_frame.pack(fill="x", side="bottom")
        tk.Frame(entry_frame, bg="#ef3340", height=1).pack(fill="x", pady=(0, 10))

        bottom_grid = tk.Frame(entry_frame, bg="#11131a")
        bottom_grid.pack(fill="x")
        bottom_grid.grid_columnconfigure(0, weight=1)

        entry_cell = self.fixed_cell(bottom_grid, 1, BTN_H, "#11131a")
        entry_cell.grid(row=0, column=0, sticky="ew")
        self.entry_field = tk.Entry(
            entry_cell,
            bg="#1a1d27",
            fg="#ffffff",
            font=("Consolas", 11),
            insertbackground="#ef3340",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#242e47",
            highlightcolor="#ef3340"
        )
        self.entry_field.pack(fill="both", expand=True)
        self.entry_field.bind("<Return>", self.send_typed_input)
        self.entry_field.bind("<Up>", self.history_up)
        self.entry_field.bind("<Down>", self.history_down)

        self.placeholder_active = False
        self.entry_field.bind("<FocusIn>", self.clear_placeholder)
        self.entry_field.bind("<FocusOut>", self.restore_placeholder)
        self.show_placeholder()
        self.root.focus_set()

        provider_cell = self.fixed_cell(bottom_grid, BTN_W, BTN_H, "#11131a")
        provider_cell.grid(row=0, column=1, padx=(BTN_GAP, 0))
        self.provider_var = tk.StringVar(value="AUTO")
        self.provider_menu = tk.OptionMenu(
            provider_cell,
            self.provider_var,
            "AUTO",
            "GROQ",
            "GEMINI",
            command=self.set_provider
        )
        self.provider_menu.config(
            bg="#242832",
            fg="#ef3340",
            activebackground="#343a48",
            activeforeground="#ffffff",
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        )
        self.provider_menu["menu"].config(
            bg="#242832",
            fg="#ffffff",
            activebackground="#ef3340"
        )
        self.provider_menu.pack(fill="both", expand=True)
        self.add_hover_glow(self.provider_menu, "#242832", "#343a48")

        voice_cell = self.fixed_cell(bottom_grid, BTN_W, BTN_H, "#11131a")
        voice_cell.grid(row=0, column=2, padx=(BTN_GAP, 0))
        self.voice_btn = tk.Button(
            voice_cell,
            text="VOICE INPUT",
            command=self.start_voice_input,
            bg="#242832",
            fg="#ef3340",
            activebackground="#343a48",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            cursor="hand2",
        )
        self.voice_btn.pack(fill="both", expand=True)
        self.add_hover_glow(self.voice_btn, "#242832", "#343a48")

        send_cell = self.fixed_cell(bottom_grid, BTN_W, BTN_H, "#11131a")
        send_cell.grid(row=0, column=3, padx=(BTN_GAP, 0))
        self.exec_btn = tk.Button(
            send_cell,
            text="Send",
            command=self.send_typed_input,
            bg="#ef3340",
            fg="#ffffff",
            activebackground="#b5222d",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            cursor="hand2"
        )
        self.exec_btn.pack(fill="both", expand=True)
        self.add_hover_glow(self.exec_btn, "#ef3340", "#ff5566")

        self.entry_scan_canvas = tk.Canvas(bottom_grid, height=3, bg="#11131a",
                                            highlightthickness=0, bd=0)
        self.entry_scan_canvas.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        quick_row = tk.Frame(bottom_grid, bg="#11131a")
        quick_row.grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Label(
            quick_row,
            text="⚡ COMMANDS",
            font=("Consolas", 8, "bold"),
            fg="#ef3340",
            bg="#11131a"
        ).pack(side="left", padx=(0, 14))
        for command in ["/help", "/open", "/search", "/time", "/system"]:
            cell = self.fixed_cell(quick_row, BTN_W, BTN_H, "#11131a")
            cell.pack(side="left", padx=(0, BTN_GAP))
            quick_btn = tk.Button(
                cell,
                text=command,
                command=lambda value=command: self.insert_quick_command(value),
                bg="#0d1727",
                fg="#aebbd0",
                activebackground="#223653",
                activeforeground="#ffffff",
                font=("Consolas", 9, "bold"),
                relief="flat",
                cursor="hand2"
            )
            quick_btn.pack(fill="both", expand=True)
            self.add_hover_glow(quick_btn, "#0d1727", "#16233a")

        # ---- Conversation ----
        feed_container = tk.Frame(self.root, bg="#07090e", padx=20, pady=6)
        feed_container.pack(fill="both", expand=True)

        feed_header = tk.Frame(feed_container, bg="#07090e")
        feed_header.pack(fill="x", pady=(0, 6))

        tk.Label(
            feed_header,
            text="◈  CONVERSATION // LIVE MEMORY",
            font=("Consolas", 9, "bold"),
            fg="#ef3340",
            bg="#07090e"
        ).pack(side="left")

        HEADER_BTN_W, HEADER_BTN_H = 100, 28

        def header_button(text, command, bg, fg, active_bg, hover_bg):
            cell = self.fixed_cell(feed_header, HEADER_BTN_W, HEADER_BTN_H, "#07090e")
            cell.pack(side="right", padx=(BTN_GAP, 0))
            btn = tk.Button(
                cell, text=text, command=command,
                bg=bg, fg=fg, activebackground=active_bg, activeforeground="#ffffff",
                font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2"
            )
            btn.pack(fill="both", expand=True)
            self.add_hover_glow(btn, bg, hover_bg)
            return btn

        self.copy_code_btn = header_button("COPY CODE", self.copy_latest_code, "#1b2438", "#00d4ff", "#243553", "#28375c")
        self.clear_chat_btn = header_button("CLEAR CHAT", self.start_new_chat, "#111a29", "#8c94a8", "#27344a", "#1d2c46")
        self.mute_btn = header_button("SPEECH: ON", self.toggle_mute, "#111a29", "#00ff88", "#27344a", "#1d2c46")
        self.test_voice_btn = header_button("TEST VOICE", self.test_created_voice, "#11252a", "#00d4ff", "#1b3b42", "#19343a")
        self.stop_btn = header_button("STOP", self.stop_speaking, "#2a1218", "#ef3340", "#3d1a22", "#40202a")

        conversation_frame = tk.Frame(
            feed_container, bg="#080d16", highlightthickness=1,
            highlightbackground="#17283e"
        )
        conversation_frame.pack(fill="both", expand=True)
        self.add_hud_corners(conversation_frame, bg="#080d16", color="#00d4ff")
        self.start_border_pulse(conversation_frame, GLOW_CYAN_BASE, GLOW_CYAN_PEAK, period=3000)
        self.history_canvas = tk.Canvas(
            conversation_frame, bg="#080d16", highlightthickness=0, bd=0
        )
        self.history_canvas.pack(side="left", fill="both", expand=True)
        history_scroll = tk.Scrollbar(
            conversation_frame, orient="vertical", command=self.history_canvas.yview,
            bg="#111c2d", troughcolor="#080d16", activebackground="#ef3340"
        )
        history_scroll.pack(side="right", fill="y")
        self.history_canvas.configure(yscrollcommand=history_scroll.set)
        self.history_inner = tk.Frame(self.history_canvas, bg="#080d16")
        self.history_window = self.history_canvas.create_window(
            (0, 0), window=self.history_inner, anchor="nw"
        )
        self.history_inner.bind(
            "<Configure>",
            lambda event: self.history_canvas.configure(
                scrollregion=self.history_canvas.bbox("all")
            )
        )
        self.history_canvas.bind(
            "<Configure>",
            lambda event: self.history_canvas.itemconfigure(
                self.history_window, width=event.width
            )
        )

        self.bind_shortcuts()
        self.bind_mousewheel_scrolling()
        self.start_global_hotkey()

        self.start_radar_spinner(self.radar_canvas)
        self.start_scanline(self.scan_canvas, width=300)
        self.start_title_glitch()

        self.start_widget_color_pulse(self.entry_field, "highlightbackground",
                                       "#1c2333", "#3a5a8c", period=2600)
        self.start_widget_color_pulse(self.entry_field, "highlightcolor",
                                       "#b5222d", "#ff5566", period=900)
        self.start_scanline(self.entry_scan_canvas, width=None, glow_w=60,
                             color="#3a5a8c", delay=40, step=5)

    # -------------------------------------------------------------
    # TELEMETRY PANEL
    # -------------------------------------------------------------
    def _build_telemetry_panel(self, parent):
        """Circular gauges + bars for CPU / GPU / RAM / Disk / Battery."""
        panel = tk.Frame(
            parent,
            bg="#0d0f14",
            padx=14, pady=10,
            highlightthickness=1,
            highlightbackground="#3a2028",
        )
        panel.pack(side="left", fill="y", padx=(12, 12), pady=18)
        self.start_border_pulse(panel, "#3a2028", GLOW_RED_PEAK, period=2600)

        tk.Label(
            panel, text="TELEMETRY MONITOR",
            font=("Consolas", 8, "bold"),
            fg="#ef3340", bg="#0d0f14",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        # --- Row 1: CPU and GPU gauges ---
        self.cpu_gauge = tk.Canvas(panel, width=64, height=64, bg="#0d0f14",
                                    highlightthickness=0, bd=0)
        self.cpu_gauge.grid(row=1, column=0, padx=4, pady=4)
        self.cpu_label = tk.Label(panel, text="CPU: 0%",
                                   font=("Consolas", 8, "bold"),
                                   fg="#ef3340", bg="#0d0f14")
        self.cpu_label.grid(row=2, column=0)

        self.gpu_gauge = tk.Canvas(panel, width=64, height=64, bg="#0d0f14",
                                    highlightthickness=0, bd=0)
        self.gpu_gauge.grid(row=1, column=1, padx=4, pady=4)
        self.gpu_label = tk.Label(panel, text="GPU: --",
                                   font=("Consolas", 8, "bold"),
                                   fg="#ef3340", bg="#0d0f14")
        self.gpu_label.grid(row=2, column=1)

        # --- Right column ---
        right = tk.Frame(panel, bg="#0d0f14")
        right.grid(row=1, column=2, rowspan=2, padx=(14, 0), sticky="n")

        # RAM row
        ram_head = tk.Frame(right, bg="#0d0f14")
        ram_head.pack(anchor="w")
        tk.Label(ram_head, text="RAM", font=("Consolas", 8, "bold"),
                 fg="#ef3340", bg="#0d0f14").pack(side="left")
        self.ram_text = tk.Label(ram_head, text="0.0/0 GB",
                                  font=("Consolas", 8),
                                  fg="#c9d3e0", bg="#0d0f14")
        self.ram_text.pack(side="left", padx=(6, 0))
        self.ram_bar = tk.Canvas(right, width=110, height=8, bg="#0d0f14",
                                  highlightthickness=0, bd=0)
        self.ram_bar.pack(anchor="w", pady=(2, 6))

        # Disk row
        disk_head = tk.Frame(right, bg="#0d0f14")
        disk_head.pack(anchor="w")
        tk.Label(disk_head, text="DISK", font=("Consolas", 8, "bold"),
                 fg="#ef3340", bg="#0d0f14").pack(side="left")
        self.disk_text = tk.Label(disk_head, text="--",
                                   font=("Consolas", 8),
                                   fg="#c9d3e0", bg="#0d0f14")
        self.disk_text.pack(side="left", padx=(6, 0))
        self.disk_bar = tk.Canvas(right, width=110, height=8, bg="#0d0f14",
                                   highlightthickness=0, bd=0)
        self.disk_bar.pack(anchor="w", pady=(2, 6))

        # Temp / Battery row
        bot_head = tk.Frame(right, bg="#0d0f14")
        bot_head.pack(anchor="w")
        tk.Label(bot_head, text="Temp", font=("Consolas", 8, "bold"),
                 fg="#ef3340", bg="#0d0f14").pack(side="left")
        self.temp_text = tk.Label(bot_head, text="-- °C",
                                   font=("Consolas", 8),
                                   fg="#c9d3e0", bg="#0d0f14")
        self.temp_text.pack(side="left", padx=(6, 0))

        tk.Label(bot_head, text="  •  Battery", font=("Consolas", 8, "bold"),
                 fg="#ef3340", bg="#0d0f14").pack(side="left")
        self.battery_text = tk.Label(bot_head, text="--%",
                                      font=("Consolas", 8),
                                      fg="#c9d3e0", bg="#0d0f14")
        self.battery_text.pack(side="left", padx=(6, 0))

        self._telemetry_error_count = 0
        self.root.after(500, self._refresh_telemetry)

    # -------------------------------------------------------------
    def _draw_ring_gauge(self, canvas, value_pct, color="#ef3340", size=64):
        canvas.delete("all")
        pad = 6
        bbox = (pad, pad, size - pad, size - pad)
        canvas.create_oval(*bbox, outline="#1b2130", width=5)
        extent = -max(0, min(100, value_pct)) * 3.6
        canvas.create_arc(*bbox, start=90, extent=extent,
                           style="arc", outline=color, width=5)
        canvas.create_text(size / 2, size / 2,
                            text=f"{int(value_pct)}%",
                            fill="#ffffff",
                            font=("Consolas", 10, "bold"))

    def _draw_segment_bar(self, canvas, value_pct, color="#ef3340",
                           width=110, height=8, segments=10):
        canvas.delete("all")
        gap = 2
        seg_w = (width - (segments - 1) * gap) / segments
        filled = int(round(value_pct / 100.0 * segments))
        for i in range(segments):
            x0 = i * (seg_w + gap)
            x1 = x0 + seg_w
            fill = color if i < filled else "#1b2130"
            canvas.create_rectangle(x0, 0, x1, height, fill=fill, outline="")

    # -------------------------------------------------------------
    def _refresh_telemetry(self):
        try:
            from telemetry import get_stats
            s = get_stats()

            # CPU
            cpu = s.get("cpu_percent", 0)
            self._draw_ring_gauge(self.cpu_gauge, cpu,
                                   "#ff5566" if cpu > 80 else "#ef3340")
            self.cpu_label.config(text=f"CPU: {cpu}%",
                                   fg="#ff5566" if cpu > 80 else "#ef3340")

            # GPU
            gpu = s.get("gpu_percent")
            if gpu is None:
                self._draw_ring_gauge(self.gpu_gauge, 0, "#3a3a3a")
                self.gpu_label.config(text="GPU: N/A", fg="#6f819d")
            else:
                self._draw_ring_gauge(self.gpu_gauge, gpu,
                                       "#ff5566" if gpu > 80 else "#ef3340")
                self.gpu_label.config(
                    text=f"GPU: {gpu}%",
                    fg="#ff5566" if gpu > 80 else "#ef3340",
                )

            # RAM
            ram_pct = s.get("ram_percent", 0)
            self._draw_segment_bar(self.ram_bar, ram_pct,
                                    "#ff5566" if ram_pct > 85 else "#ef3340")
            self.ram_text.config(
                text=f"{s.get('ram_used_gb', 0):.1f}/{s.get('ram_total_gb', 0):.0f} GB"
            )

            # Disk
            disk_pct = s.get("disk_percent", 0)
            self._draw_segment_bar(self.disk_bar, disk_pct,
                                    "#ff5566" if disk_pct > 90 else "#ef3340")
            self.disk_text.config(
                text=f"{s.get('disk_free_gb', 0):.0f} GB free"
            )

            # Temp
            temp = s.get("cpu_temp_c")
            if temp is None:
                self.temp_text.config(text="-- °C", fg="#6f819d")
            else:
                self.temp_text.config(
                    text=f"{int(temp)} °C",
                    fg="#ff5566" if temp > 80 else "#c9d3e0",
                )

            # Battery
            bat = s.get("battery_pct")
            if bat is None:
                self.battery_text.config(text="N/A", fg="#6f819d")
            else:
                plug = "⚡" if s.get("battery_plugged") else ""
                col = "#00ff88" if bat > 30 else "#ff5566"
                self.battery_text.config(text=f"{bat}% {plug}", fg=col)

            self._telemetry_error_count = 0
        except Exception as e:
            self._telemetry_error_count += 1
            if self._telemetry_error_count <= 3:
                print(f"[telemetry] refresh failed: {type(e).__name__}: {e}")

        self.root.after(2000, self._refresh_telemetry)

    # -------------------------------------------------------------
    def update_clock(self):
        try:
            self.clock_label.config(text=f"ONLINE  |  {time.strftime('%H:%M:%S')}")
            self.root.after(1000, self.update_clock)
        except Exception:
            pass

    def _switch_email_account(self):
        try:
            from inbox import switch_active_account
            new_email = switch_active_account()
            self.email_indicator.config(text=f"✉ {new_email}")
            self.log_system_event(f"Active email switched to: {new_email}")
            self.set_status(f"EMAIL: {new_email.split('@')[0].upper()}", "#00ff88")
            self.email_switch_btn.config(bg="#00ff88", fg="#000000")
            self.root.after(400, lambda: self.email_switch_btn.config(bg="#1f293d", fg="#ef3340"))
        except Exception as e:
            self.log_system_event(f"Email switch error: {e}")

    def start_new_chat(self, event=None):
        self.remove_thinking()
        for child in self.history_inner.winfo_children():
            child.destroy()
        self.last_output_text = ""
        self.last_code_text = ""
        self.set_status("READY FOR A NEW CONVERSATION", "#00ff88")

    # -------------------------------------------------------------
    def set_status(self, text: str, color: str = "#00ff88"):
        self.current_status = text
        waveform_active = any(word in text.lower() for word in ["listening", "recording"])

        def update():
            try:
                self.hero_status.config(text=text, fg=color)
                if waveform_active and not self.waveform_active:
                    self.waveform_active = True
                    self.animate_waveform()
                elif not waveform_active:
                    self.waveform_active = False
                    if self.waveform_job:
                        self.root.after_cancel(self.waveform_job)
                        self.waveform_job = None
                    self.draw_waveform(False)
            except Exception:
                pass
        self.root.after(0, update)

    def draw_waveform(self, active):
        self.waveform.delete("all")
        bar_width = 7
        gap = 3
        center = 17
        for index in range(self.waveform_bars):
            height = 5 + abs(math.sin(time.time() * 7 + index * 0.72)) * 25 if active else 3
            x = index * (bar_width + gap)
            self.waveform.create_rectangle(
                x,
                center - height / 2,
                x + bar_width,
                center + height / 2,
                fill="#ef3340" if active else "#252a35",
                outline=""
            )

    def animate_waveform(self):
        if not self.waveform_active:
            return
        self.draw_waveform(True)
        self.waveform_job = self.root.after(70, self.animate_waveform)

    def set_provider(self, provider: str):
        self.selected_provider = provider.lower()
        self.set_status(f"{provider} AI SELECTED", "#00d4ff")

    # -------------------------------------------------------------
    def show_placeholder(self):
        if not self.entry_field.get():
            self.entry_field.insert(0, PLACEHOLDER_TEXT)
            self.entry_field.config(fg=PLACEHOLDER_COLOR)
            self.placeholder_active = True

    def clear_placeholder(self, event=None):
        if self.placeholder_active:
            self.entry_field.delete(0, tk.END)
            self.entry_field.config(fg=ENTRY_TEXT_COLOR)
            self.placeholder_active = False

    def restore_placeholder(self, event=None):
        if not self.placeholder_active and not self.entry_field.get():
            self.show_placeholder()

    def get_entry_text(self) -> str:
        if self.placeholder_active:
            return ""
        return self.entry_field.get().strip()

    def insert_quick_command(self, command: str):
        self.clear_placeholder()
        self.entry_field.delete(0, tk.END)
        self.entry_field.insert(0, command + " ")
        self.entry_field.focus_set()

    # -------------------------------------------------------------
    def remember_command(self, text: str):
        text = text.strip()
        if not text:
            return
        if not self.command_history or self.command_history[-1] != text:
            self.command_history.append(text)
            del self.command_history[:-HISTORY_LIMIT]
        self.history_index = None
        self.history_draft = ""

    def set_entry_text(self, text: str):
        self.clear_placeholder()
        self.entry_field.delete(0, tk.END)
        self.entry_field.insert(0, text)
        self.entry_field.icursor(tk.END)

    def history_up(self, event=None):
        if not self.command_history:
            return "break"
        if self.history_index is None:
            self.history_draft = self.get_entry_text()
            self.history_index = len(self.command_history)
        if self.history_index > 0:
            self.history_index -= 1
        self.set_entry_text(self.command_history[self.history_index])
        return "break"

    def history_down(self, event=None):
        if self.history_index is None:
            return "break"
        self.history_index += 1
        if self.history_index >= len(self.command_history):
            self.history_index = None
            self.set_entry_text(self.history_draft)
        else:
            self.set_entry_text(self.command_history[self.history_index])
        return "break"

    # -------------------------------------------------------------
    def bind_shortcuts(self):
        shortcuts = {
            "<Control-l>": self.start_new_chat,
            "<Control-L>": self.start_new_chat,
            "<Control-m>": self.start_voice_input,
            "<Control-M>": self.start_voice_input,
            "<Escape>": self.stop_speaking,
        }
        for sequence, handler in shortcuts.items():
            self.root.bind_all(sequence, lambda event, action=handler: (action(), "break")[1])

    def focus_input(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(250, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
            self.entry_field.focus_set()
        except Exception:
            pass

    def start_global_hotkey(self):
        if os.name != "nt":
            return
        key_label = f"Ctrl+Alt+{GLOBAL_HOTKEY_KEY.upper()}"

        def worker():
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
                WM_HOTKEY = 0x0312
                registered = user32.RegisterHotKey(
                    None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord(GLOBAL_HOTKEY_KEY.upper())
                )
                if not registered:
                    self.log_system_event(f"Global hotkey {key_label} is already used by another program.")
                    return
                self.log_system_event(f"Global hotkey ready: {key_label} focuses the input box.")
                msg = wintypes.MSG()
                while self.is_running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_HOTKEY:
                        self.root.after(0, self.focus_input)
                user32.UnregisterHotKey(None, 1)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------
    def speak_safe(self, text: str):
        if self.speech_muted:
            return

        def worker():
            with self.speak_lock:
                if self.speech_muted or not self.is_running:
                    return
                try:
                    backend = speak(text)
                    if backend == "failed":
                        self.root.after(
                            0,
                            lambda backend=backend: self.log_system_event(
                                "Local neural voice failed. "
                                "Run setup_local_tts.bat and check ultron_voice.log."
                            ),
                        )
                except Exception as err:
                    print(f"[speak error]: {err}")

        threading.Thread(target=worker, daemon=True).start()

    def test_created_voice(self):
        if self.speech_muted:
            self.log_system_event("Turn SPEECH: ON before testing the voice.")
            return
        self.test_voice_btn.config(state=tk.DISABLED, text="TESTING...")

        def worker():
            succeeded = voice_module.speak(
                "Sir, this is Ultron. Your local neural voice is online."
            ) in ("local-piper", "edge-ultron", "rvc-ultron")
            try:
                self.root.after(0, self._finish_voice_test, succeeded)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_voice_test(self, succeeded):
        self.test_voice_btn.config(state=tk.NORMAL, text="TEST VOICE")
        if succeeded:
            self.set_status("LOCAL NEURAL VOICE IS WORKING", "#00ff88")
            self.log_system_event("Test played using the local Piper neural voice.")
        else:
            self.set_status("LOCAL VOICE FAILED - RUN SETUP", "#ffaa00")
            self.log_system_event(
                "Could not play local Piper speech. Run setup_local_tts.bat "
                "and check ultron_voice.log."
            )

    def stop_speaking(self, event=None):
        stopped = False

        for name in ("stop_speaking", "stop_speech", "stop_tts"):
            hook = getattr(voice_module, name, None)
            if callable(hook):
                try:
                    hook()
                    stopped = True
                except Exception:
                    pass
                break

        if not stopped:
            engine = getattr(voice_module, "engine", None)
            if engine is not None and hasattr(engine, "stop"):
                try:
                    engine.stop()
                    stopped = True
                except Exception:
                    pass

        if not stopped:
            pygame = sys.modules.get("pygame")
            if pygame is not None:
                try:
                    if pygame.mixer.get_init():
                        pygame.mixer.stop()
                        pygame.mixer.music.stop()
                        stopped = True
                except Exception:
                    pass

        if self.is_processing:
            self.is_processing = False
            self.processing_started_at = 0.0
            self.root.after(0, self.remove_thinking)

        if stopped:
            self.set_status("SPEECH STOPPED", "#ffaa00")
        else:
            self.set_status("SPEECH STOPPED - READY", "#ffaa00")

    def toggle_mute(self, event=None):
        self.speech_muted = not self.speech_muted
        if self.speech_muted:
            self.stop_speaking()
            self.mute_btn.config(text="SPEECH: OFF", fg="#ef3340")
            self.set_status("SPEECH MUTED - TEXT ONLY", "#ffaa00")
        else:
            self.mute_btn.config(text="SPEECH: ON", fg="#00ff88")
            self.set_status("SPEECH ENABLED", "#00ff88")

    # -------------------------------------------------------------
    def show_thinking(self):
        def update():
            try:
                if self.thinking_row is not None:
                    return
                self.thinking_row, self.thinking_label = self.add_message(
                    "ULTRON", "ANALYZING", thinking=True
                )
                self.thinking_label.config(fg="#8c94a8", font=("Segoe UI", 10, "italic"))
                self.thinking_step = 0
                self.animate_thinking()
            except Exception:
                pass
        self.root.after(0, update)

    def animate_thinking(self):
        if self.thinking_row is None:
            return
        try:
            self.thinking_step = (self.thinking_step + 1) % 4
            self.thinking_label.config(text="ANALYZING" + "." * self.thinking_step)
            self.thinking_job = self.root.after(400, self.animate_thinking)
        except tk.TclError:
            self.thinking_row = None
            self.thinking_job = None

    def remove_thinking(self):
        if self.thinking_job is not None:
            try:
                self.root.after_cancel(self.thinking_job)
            except Exception:
                pass
            self.thinking_job = None
        if self.thinking_row is not None:
            try:
                self.thinking_row.destroy()
            except Exception:
                pass
            self.thinking_row = None
            self.thinking_label = None

    # -------------------------------------------------------------
    def copy_message(self, text: str, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        button.config(text="COPIED", fg="#00ff88")

        def reset():
            try:
                button.config(text="COPY", fg="#6f819d")
            except tk.TclError:
                pass
        self.root.after(1200, reset)

    def display_user_input(self, text: str):
        def update():
            try:
                self.add_message("YOU", text, user=True)
            except Exception:
                pass
        self.root.after(0, update)

    def display_ultron_output(self, text: str):
        if text and text.count("```") % 2 != 0:
            text = text + "\n```"
        self.last_output_text = text
        code_blocks = re.findall(r"```(?:[a-zA-Z0-9_+-]+)?\s*\n?(.*?)```", text, re.DOTALL)
        self.last_code_text = "\n\n".join(block.strip("\n") for block in code_blocks)
        response_text = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\s*\n?.*?```", "", text, flags=re.DOTALL).strip()
        if not response_text:
            response_text = "Code response ready."

        def update():
            try:
                self.remove_thinking()
                self.add_message("ULTRON", response_text, code=self.last_code_text)
            except Exception:
                pass
        self.root.after(0, update)

    def add_message(self, author: str, text: str, user: bool = False,
                    code: str = "", thinking: bool = False):
        row = tk.Frame(self.history_inner, bg="#080d16")
        row.pack(fill="x", padx=16, pady=(12, 0))
        row.grid_columnconfigure(0, weight=ULTRON_SHARE, uniform="chat")
        row.grid_columnconfigure(1, weight=USER_SHARE, uniform="chat")

        AVATAR_D = 38
        if user:
            avatar_color = "#4b83c4"
            holder = tk.Frame(row, bg="#080d16")
            holder.grid(row=0, column=1, sticky="e")
            bubble = tk.Frame(holder, bg="#102844", padx=14, pady=9)
            bubble.pack(side="left")
            avatar_canvas = tk.Canvas(holder, width=AVATAR_D, height=AVATAR_D, bg="#080d16",
                                       highlightthickness=0, bd=0)
            avatar_canvas.pack(side="left", anchor="n", padx=(10, 0))
            self.start_avatar_glow(avatar_canvas, AVATAR_D, "#1c3350", "#3f6fa8",
                                    letter="Y", letter_color="#eaf2ff")
        else:
            avatar_color = "#ef3340"
            holder = tk.Frame(row, bg="#080d16")
            holder.grid(row=0, column=0, sticky="ew")
            avatar_canvas = tk.Canvas(holder, width=AVATAR_D, height=AVATAR_D, bg="#080d16",
                                       highlightthickness=0, bd=0)
            avatar_canvas.pack(side="left", anchor="n", padx=(0, 10))
            if thinking:
                self.start_ring_spinner(avatar_canvas, AVATAR_D, color="#ef3340")
            else:
                self.start_avatar_glow(avatar_canvas, AVATAR_D, "#7a1620", "#ef3340",
                                        letter="A", letter_color="#0b0d12")
            bubble = tk.Frame(holder, bg="#111b2a", padx=14, pady=9)
            bubble.pack(side="left", fill="x", expand=True)
        bubble_bg = bubble.cget("bg")

        if not thinking:
            flash_color = "#3f6fa8" if user else "#7a2530"
            self.flash_in(bubble, bubble_bg, flash_color)

        head = tk.Frame(bubble, bg=bubble_bg)
        head.pack(fill="x")
        tk.Label(
            head,
            text=f"{author}   {time.strftime('%H:%M:%S')}",
            font=("Consolas", 8, "bold"),
            fg=avatar_color,
            bg=bubble_bg,
            anchor="w"
        ).pack(side="left")
        if not thinking:
            copy_text = f"{text}\n\n{code}" if code else text
            copy_btn = tk.Button(
                head, text="COPY", font=("Consolas", 7, "bold"),
                fg="#6f819d", bg=bubble_bg,
                activebackground=bubble_bg, activeforeground="#ffffff",
                relief="flat", bd=0, padx=6, pady=0, cursor="hand2"
            )
            copy_btn.config(command=lambda: self.copy_message(copy_text, copy_btn))
            copy_btn.pack(side="right")

        initial_text = "" if (not user and not thinking and text) else text
        msg_label = tk.Label(
            bubble,
            text=initial_text,
            font=("Segoe UI", 10),
            fg="#e5edf8",
            bg=bubble_bg,
            justify="left",
            anchor="w",
            wraplength=300
        )
        msg_label.pack(fill="x", pady=(5, 0))
        if not user and not thinking and text:
            self.animate_typewriter(msg_label, text)

        def rewrap(event):
            share = USER_SHARE if user else ULTRON_SHARE
            column_px = event.width * share / (ULTRON_SHARE + USER_SHARE)
            reserved = 40 if user else 90
            msg_label.config(wraplength=max(120, int(column_px - reserved)))
        row.bind("<Configure>", rewrap)

        if code:
            code_box = tk.Text(
                bubble, height=min(18, max(5, code.count("\n") + 2)), width=1,
                bg="#080d16", fg="#d7e3f5", font=("Consolas", 9),
                relief="flat", wrap="none", padx=10, pady=8
            )
            code_box.insert("1.0", code)
            code_box.config(state=tk.DISABLED)
            code_box.pack(fill="x", pady=(9, 0))

        if not thinking and self.thinking_row is not None:
            try:
                self.thinking_row.pack_forget()
                self.thinking_row.pack(fill="x", padx=16, pady=(12, 0))
            except Exception:
                pass

        self.history_canvas.update_idletasks()
        self.history_canvas.yview_moveto(1.0)
        return row, msg_label

    def copy_latest_code(self):
        if not self.last_code_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_code_text)
        self.root.update()
        self.set_status("CODE COPIED TO CLIPBOARD", "#00d4ff")

    def copy_latest_output(self):
        if not self.last_output_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_output_text)
        self.root.update()
        self.set_status("RESPONSE COPIED TO CLIPBOARD", "#00d4ff")

    def log_system_event(self, text: str):
        def update():
            try:
                row = tk.Frame(self.history_inner, bg="#080d16")
                row.pack(fill="x", padx=18, pady=(10, 0))
                tk.Label(row, text=f"◌  {text}", font=("Consolas", 8, "italic"),
                         fg="#6f819d", bg="#080d16", anchor="w").pack(anchor="w")
                self.history_canvas.update_idletasks()
                self.history_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.root.after(0, update)

    # -------------------------------------------------------------
    def process_command(self, clean_command: str):
        acquired = self.command_lock.acquire(blocking=False)
        if not acquired:
            self.log_system_event("Busy - command ignored. Please wait.")
            return

        try:
            self.is_processing = True
            self.processing_started_at = time.time()
            self.set_status("ULTRON IS PROCESSING...", "#ffaa00")

            self.show_thinking()
            self.set_status("ULTRON IS ANALYZING...", "#aa00ff")
            ai_response = process_message(
                clean_command,
                provider=self.selected_provider,
            )

            if not ai_response:
                ai_response = "I did not receive a response from the AI brain."

            self.display_ultron_output(ai_response)
            self.set_status("ULTRON IS SPEAKING...", "#ef3340")
            self.speak_safe(ai_response)

        except Exception as err:
            self.log_system_event(
                f"Request failed ({type(err).__name__}). Check the current system state."
            )
            err_msg = "I couldn't complete that request. Check its current state before retrying."
            self.display_ultron_output(err_msg)
            self.speak_safe("I encountered an issue executing that command.")
        finally:
            self.is_processing = False
            self.processing_started_at = 0.0
            self.root.after(0, self.remove_thinking)
            self.set_status("ULTRON IS LISTENING...", "#00ff88")
            try:
                self.command_lock.release()
            except RuntimeError:
                pass

    def send_typed_input(self, event=None):
        txt = self.get_entry_text()
        if not txt:
            return
        self.remember_command(txt)
        self.entry_field.delete(0, tk.END)
        if self.root.focus_get() is not self.entry_field:
            self.show_placeholder()
        self.display_user_input(txt)
        threading.Thread(target=self.process_command, args=(txt,), daemon=True).start()

    def start_voice_input(self, event=None):
        if str(self.voice_btn.cget("state")) == "disabled":
            return
        self.voice_btn.config(state=tk.DISABLED, text="LISTENING...")
        self.set_status("ULTRON IS LISTENING...", "#00ff88")
        threading.Thread(target=self.capture_voice_input, daemon=True).start()

    def close_dashboard(self):
        self.is_running = False
        self.root.destroy()

    def capture_voice_input(self):
        try:
            heard = listen(status_callback=self.set_status, timeout=0, phrase_time_limit=30)
            if heard:
                self.remember_command(heard)
                self.display_user_input(heard)
                threading.Thread(target=self.process_command, args=(heard,), daemon=True).start()
            else:
                self.set_status("SPEECH NOT UNDERSTOOD", "#ffaa00")
        except Exception as err:
            self.log_system_event(f"Voice input error: {err}")
            self.set_status("VOICE INPUT FAILED", "#ffaa00")
        finally:
            self.root.after(0, lambda: self.voice_btn.config(state=tk.NORMAL, text="VOICE INPUT"))

    def start_voice_thread(self):
        threading.Thread(target=self.voice_loop, daemon=True).start()

    def voice_loop(self):
        name = get_name()
        boot_msg = f"Greetings {name}. Ultron systems are fully synchronized." if name else "Ultron systems online."

        self.log_system_event("Ultron Core & Microphone Synchronized.")
        self.display_ultron_output(boot_msg)
        self.set_status("ULTRON IS SPEAKING...", "#ef3340")
        self.speak_safe(boot_msg)

        consecutive_errors = 0

        while self.is_running:
            try:
                if self.is_processing:
                    time.sleep(0.3)
                    continue

                self.set_status("ULTRON IS LISTENING...", "#00ff88")

                heard = listen(status_callback=self.set_status)

                if not heard:
                    consecutive_errors = 0
                    continue

                heard_lower = heard.casefold()
                if heard_lower in ["exit", "quit", "shutdown assistant", "goodbye", "sleep ultron"]:
                    bye = "Powering down Ultron systems. Until next time."
                    self.display_ultron_output(bye)
                    self.set_status("ULTRON POWERING DOWN...", "#ff2e2e")
                    self.speak_safe(bye)
                    self.root.after(1000, self.root.destroy)
                    break

                has_wake = any(w in heard_lower for w in config.WAKE_WORDS)
                if not has_wake:
                    consecutive_errors = 0
                    continue

                clean = heard
                for w in config.WAKE_WORDS:
                    clean = re.sub(re.escape(w), "", clean, flags=re.IGNORECASE)
                clean = clean.strip()

                self.display_user_input(heard)

                if not clean:
                    ack = "I am listening."
                    self.display_ultron_output(ack)
                    self.set_status("ULTRON IS SPEAKING...", "#ef3340")
                    self.speak_safe(ack)
                    continue

                threading.Thread(target=self.process_command, args=(clean,), daemon=True).start()
                consecutive_errors = 0

            except Exception as err:
                consecutive_errors += 1
                self.log_system_event(f"Voice loop error #{consecutive_errors}: {err}")
                time.sleep(min(1.0 + consecutive_errors * 0.5, 5.0))


if __name__ == "__main__":
    root = tk.Tk()
    app = UltronDashboard(root)
    root.mainloop()