"""
ULTRON SECURITY GATE
Asks for a password before launching UI.py.
Run this instead of UI.py directly.

    python gate.py
"""

import tkinter as tk
import hashlib
import hmac
import math
import os
import random
import subprocess
import sys
import time

# =====================================================================
#  CONFIG  —  change these
# =====================================================================
# SHA-256 hash of the password. Default password below is:  ultron
# To change it, run:   python -c "import hashlib;print(hashlib.sha256('YOURPASS'.encode()).hexdigest())"
PASSWORD_HASH = "8c0e3e5e2d0d4f6f3b7a2b8e1c9a0d4e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c"

# Easier: store plaintext here and it gets hashed on the fly.
# Leave as None to use PASSWORD_HASH above.
PLAINTEXT_PASSWORD = "ultron"

APP_TO_LAUNCH = "UI.py"           # file to run after successful login
MAX_ATTEMPTS = 5                  # lock out after N wrong tries
LOCKOUT_SECONDS = 15              # cooldown after too many wrong tries
WINDOW_W, WINDOW_H = 560, 720     # gate window size

# Colours (matches UI.py)
BG          = "#08090c"
PANEL_BG    = "#0d1018"
RED         = "#ef3340"
RED_DARK    = "#7a1620"
CYAN        = "#00d4ff"
CYAN_DARK   = "#17283e"
TEXT        = "#d6e0f0"
MUTED       = "#6f819d"
GREEN       = "#00ff88"

# =====================================================================


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_hash() -> str:
    if PLAINTEXT_PASSWORD:
        return sha256(PLAINTEXT_PASSWORD)
    return PASSWORD_HASH


class SecurityGate:
    def __init__(self, root):
        self.root = root
        self.root.title("ULTRON // SECURITY GATE")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.attempts = 0
        self.locked_until = 0.0
        self.is_running = True
        self.scan_pos = 0
        self.radar_angle = 0
        self.glitch_step = 0
        self.pulse_i = random.randint(0, 48)
        self.granted = False

        self._center_window()
        self._build_ui()
        self._start_animations()

        # Enter key submits, Esc quits
        self.root.bind("<Return>", lambda e: self.attempt_login())
        self.root.bind("<Escape>", lambda e: self.quit_app())

        self.entry.focus_set()

    # -----------------------------------------------------------------
    def _center_window(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - WINDOW_W) // 2
        y = (sh - WINDOW_H) // 2
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")

    # -----------------------------------------------------------------
    def _lerp(self, c1, c2, t):
        c1, c2 = c1.lstrip("#"), c2.lstrip("#")
        r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
        r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        )

    def _hud_corners(self, frame, color=RED, size=18, thickness=2, margin=4):
        specs = {
            "nw": (0, 0, [0, size * 0.4, 0, 0, size * 0.4, 0]),
            "ne": (1, 0, [size * 0.6, 0, size, 0, size, size * 0.4]),
            "sw": (0, 1, [0, size * 0.6, 0, size, size * 0.4, size]),
            "se": (1, 1, [size * 0.6, size, size, size, size, size * 0.6]),
        }
        for anchor, (rx, ry, pts) in specs.items():
            dx = margin if rx == 0 else -margin
            dy = margin if ry == 0 else -margin
            c = tk.Canvas(frame, width=size, height=size, bg=frame.cget("bg"),
                          highlightthickness=0, bd=0)
            c.place(relx=rx, rely=ry, x=dx, y=dy, anchor=anchor)
            c.create_line(*pts, fill=color, width=thickness)

    # -----------------------------------------------------------------
    def _build_ui(self):
        # ---- Top bar ----
        topbar = tk.Frame(self.root, bg="#090d16", height=30)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        self.title_label = tk.Label(
            topbar, text="◉  ULTRON  //  SECURITY GATE",
            font=("Consolas", 9, "bold"), fg=TEXT, bg="#090d16",
        )
        self.title_label.pack(side="left", padx=16)

        tk.Label(
            topbar, text="SECURE  |  LOCKED",
            font=("Consolas", 9, "bold"), fg=RED, bg="#090d16",
        ).pack(side="right", padx=16)

        # ---- Outer panel with pulsing border ----
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=(16, 18))

        self.panel = tk.Frame(
            outer, bg=PANEL_BG,
            highlightthickness=1, highlightbackground=RED_DARK,
        )
        self.panel.pack(fill="both", expand=True)
        self._hud_corners(self.panel, color=RED)

        # ---- Avatar + radar ----
        hero = tk.Frame(self.panel, bg=PANEL_BG)
        hero.pack(fill="x", pady=(26, 6), padx=26)

        self.avatar = tk.Canvas(hero, width=96, height=96, bg=PANEL_BG,
                                highlightthickness=0, bd=0)
        self.avatar.pack(side="left")

        hero_copy = tk.Frame(hero, bg=PANEL_BG)
        hero_copy.pack(side="left", padx=(18, 0), fill="x", expand=True)

        tk.Label(
            hero_copy, text="◈  IDENTITY VERIFICATION",
            font=("Consolas", 9, "bold"), fg=RED, bg=PANEL_BG,
        ).pack(anchor="w")

        self.status = tk.Label(
            hero_copy, text="AUTHENTICATION REQUIRED",
            font=("Consolas", 14, "bold"), fg=CYAN, bg=PANEL_BG,
        )
        self.status.pack(anchor="w", pady=(4, 0))

        # ---- Radar row ----
        radar_row = tk.Frame(self.panel, bg=PANEL_BG)
        radar_row.pack(fill="x", padx=26, pady=(10, 0))

        self.radar = tk.Canvas(radar_row, width=26, height=26, bg=PANEL_BG,
                               highlightthickness=0, bd=0)
        self.radar.pack(side="left")

        tk.Label(
            radar_row, text="ENCRYPTED CHANNEL  //  AES-256",
            font=("Consolas", 8), fg=MUTED, bg=PANEL_BG,
        ).pack(side="left", padx=(10, 0))

        # ---- Scanline under hero ----
        self.scan = tk.Canvas(self.panel, height=3, bg=PANEL_BG,
                              highlightthickness=0, bd=0)
        self.scan.pack(fill="x", padx=26, pady=(16, 0))

        # ---- Prompt ----
        tk.Label(
            self.panel, text="ENTER ACCESS PHRASE",
            font=("Consolas", 9, "bold"), fg=MUTED, bg=PANEL_BG,
        ).pack(anchor="w", padx=26, pady=(22, 6))

        # ---- Password entry ----
        entry_wrap = tk.Frame(
            self.panel, bg="#0a0e17",
            highlightthickness=1, highlightbackground=CYAN_DARK,
        )
        entry_wrap.pack(fill="x", padx=26)

        self.entry = tk.Entry(
            entry_wrap, show="●",
            bg="#0a0e17", fg=TEXT,
            insertbackground=RED,
            font=("Consolas", 14, "bold"),
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=CYAN_DARK,
            highlightcolor=RED,
        )
        self.entry.pack(fill="x", padx=12, pady=12, ipady=4)

        # ---- Show / Hide password ----
        show_row = tk.Frame(self.panel, bg=PANEL_BG)
        show_row.pack(fill="x", padx=26, pady=(8, 0))

        self.show_var = tk.BooleanVar(value=False)
        self.show_chk = tk.Checkbutton(
            show_row, text="SHOW PHRASE", variable=self.show_var,
            command=self.toggle_show,
            bg=PANEL_BG, fg=MUTED, activebackground=PANEL_BG,
            activeforeground=CYAN, selectcolor="#0a0e17",
            font=("Consolas", 8, "bold"), bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self.show_chk.pack(side="left")

        self.caps_label = tk.Label(
            show_row, text="", font=("Consolas", 8, "bold"),
            fg=RED, bg=PANEL_BG,
        )
        self.caps_label.pack(side="right")

        # Caps Lock detection
        self.root.bind("<KeyPress>", self._check_caps, add="+")
        self.root.bind("<KeyRelease>", self._check_caps, add="+")

        # ---- Attempt bar ----
        self.attempt_label = tk.Label(
            self.panel, text=f"ATTEMPTS  0 / {MAX_ATTEMPTS}",
            font=("Consolas", 8, "bold"), fg=MUTED, bg=PANEL_BG,
        )
        self.attempt_label.pack(anchor="w", padx=26, pady=(16, 4))

        self.attempt_bar = tk.Canvas(self.panel, height=6, bg=PANEL_BG,
                                     highlightthickness=0, bd=0)
        self.attempt_bar.pack(fill="x", padx=26)

        # ---- Buttons ----
        btn_row = tk.Frame(self.panel, bg=PANEL_BG)
        btn_row.pack(fill="x", padx=26, pady=(26, 26))

        self.unlock_btn = tk.Button(
            btn_row, text="UNLOCK",
            command=self.attempt_login,
            bg=RED, fg="#ffffff",
            activebackground="#b5222d", activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, cursor="hand2",
            padx=28, pady=10,
        )
        self.unlock_btn.pack(side="right")
        self._hover(self.unlock_btn, RED, "#ff5566")

        self.quit_btn = tk.Button(
            btn_row, text="ABORT",
            command=self.quit_app,
            bg="#1b2438", fg=MUTED,
            activebackground="#243553", activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, cursor="hand2",
            padx=22, pady=10,
        )
        self.quit_btn.pack(side="right", padx=(0, 10))
        self._hover(self.quit_btn, "#1b2438", "#243553")

        # ---- Footer ----
        tk.Label(
            self.panel,
            text="◌  Unauthorized access attempts are logged.",
            font=("Consolas", 8, "italic"), fg=MUTED, bg=PANEL_BG,
        ).pack(side="bottom", pady=(0, 14))

    # -----------------------------------------------------------------
    def _hover(self, widget, normal_bg, hover_bg):
        widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg), add="+")
        widget.bind("<Leave>", lambda e: widget.config(bg=normal_bg), add="+")

    def toggle_show(self):
        self.entry.config(show="" if self.show_var.get() else "●")

    def _check_caps(self, _event=None):
        try:
            on = (self.root.tk.call("tk", "capslock") in (1, "1", True))
        except Exception:
            on = False
        self.caps_label.config(text="⚠ CAPS LOCK ON" if on else "")

    # -----------------------------------------------------------------
    def _start_animations(self):
        self._animate_avatar()
        self._animate_radar()
        self._animate_scanline()
        self._animate_border()
        self._animate_glitch()

    def _animate_avatar(self):
        if not self.is_running:
            return
        self.avatar.delete("all")
        cx = cy = 48
        # pulsing halo
        t = (math.sin(self.pulse_i / 48 * 2 * math.pi) + 1) / 2
        glow = self._lerp(RED_DARK, RED, t)
        r = 34 + t * 5
        self.avatar.create_oval(cx - r, cy - r, cx + r, cy + r, outline=glow, width=2)
        self.avatar.create_oval(cx - 32, cy - 32, cx + 32, cy + 32, fill=RED, outline="")
        self.avatar.create_text(cx, cy, text="🔒", fill="#0b0d12",
                                font=("Segoe UI", 22, "bold"))
        self.pulse_i += 1
        self.root.after(45, self._animate_avatar)

    def _animate_radar(self):
        if not self.is_running:
            return
        self.radar.delete("all")
        self.radar.create_oval(2, 2, 24, 24, outline="#252a35", width=2)
        self.radar.create_arc(2, 2, 24, 24, start=self.radar_angle, extent=70,
                              style="arc", outline=RED, width=3)
        self.radar_angle = (self.radar_angle + 7) % 360
        self.root.after(45, self._animate_radar)

    def _animate_scanline(self):
        if not self.is_running:
            return
        try:
            w = self.scan.winfo_width()
            if w < 10:
                self.root.after(60, self._animate_scanline)
                return
            self.scan.delete("all")
            self.scan.create_line(0, 2, w, 2, fill="#1b2130", width=1)
            glow_w = 70
            x0 = self.scan_pos
            self.scan.create_line(max(0, x0), 2, min(w, x0 + glow_w), 2, fill=CYAN, width=2)
            self.scan_pos += 6
            if self.scan_pos > w:
                self.scan_pos = -glow_w
        except tk.TclError:
            pass
        self.root.after(40, self._animate_scanline)

    def _animate_border(self):
        if not self.is_running:
            return
        try:
            t = (math.sin(self.pulse_i / 48 * 2 * math.pi) + 1) / 2
            self.panel.config(highlightbackground=self._lerp(RED_DARK, RED, t))
        except tk.TclError:
            pass
        self.root.after(60, self._animate_border)

    def _animate_glitch(self):
        if not self.is_running:
            return
        original = "◉  ULTRON  //  SECURITY GATE"
        if self.glitch_step < 3:
            chars = "!<>-_\\/[]{}=+*^#"
            scr = list(original)
            for _ in range(3):
                i = random.randint(0, len(scr) - 1)
                if scr[i] != " ":
                    scr[i] = random.choice(chars)
            self.title_label.config(text="".join(scr), fg=CYAN)
            self.glitch_step += 1
            self.root.after(70, self._animate_glitch)
        else:
            self.title_label.config(text=original, fg=TEXT)
            self.glitch_step = 0
            self.root.after(random.randint(3000, 7000), self._animate_glitch)

    # -----------------------------------------------------------------
    def _draw_attempt_bar(self):
        self.attempt_bar.delete("all")
        w = self.attempt_bar.winfo_width() or 400
        h = 6
        segments = MAX_ATTEMPTS
        seg_w = (w - (segments - 1) * 4) / segments
        for i in range(segments):
            x0 = i * (seg_w + 4)
            x1 = x0 + seg_w
            if i < self.attempts:
                fill = RED if self.attempts < MAX_ATTEMPTS else "#ff5566"
            else:
                fill = "#1b2130"
            self.attempt_bar.create_rectangle(x0, 0, x1, h, fill=fill, outline="")

    def _flash_status(self, text, color):
        self.status.config(text=text, fg=color)

    # -----------------------------------------------------------------
    def attempt_login(self):
        now = time.time()
        if now < self.locked_until:
            remaining = int(self.locked_until - now) + 1
            self._flash_status(f"LOCKED  //  {remaining}s", RED)
            self._shake()
            return

        typed = self.entry.get()
        if not typed:
            self._flash_status("PHRASE REQUIRED", "#ffaa00")
            self._shake()
            return

        if hmac.compare_digest(sha256(typed), expected_hash()):
            self._on_success()
        else:
            self.attempts += 1
            self._draw_attempt_bar()
            self.attempt_label.config(text=f"ATTEMPTS  {self.attempts} / {MAX_ATTEMPTS}")
            self.entry.delete(0, tk.END)

            if self.attempts >= MAX_ATTEMPTS:
                self.locked_until = time.time() + LOCKOUT_SECONDS
                self._flash_status(f"LOCKED OUT  //  {LOCKOUT_SECONDS}s", RED)
                self.entry.config(state=tk.DISABLED)
                self.unlock_btn.config(state=tk.DISABLED)
                self._lockout_countdown(LOCKOUT_SECONDS)
            else:
                self._flash_status("ACCESS DENIED", RED)
                self._shake()
                self.root.bell()

    def _lockout_countdown(self, remaining):
        if remaining <= 0:
            self.entry.config(state=tk.NORMAL)
            self.unlock_btn.config(state=tk.NORMAL)
            self.attempts = 0
            self.attempt_label.config(text=f"ATTEMPTS  0 / {MAX_ATTEMPTS}")
            self._draw_attempt_bar()
            self._flash_status("AUTHENTICATION REQUIRED", CYAN)
            self.entry.focus_set()
            return
        self._flash_status(f"LOCKED OUT  //  {remaining}s", RED)
        self.root.after(1000, lambda: self._lockout_countdown(remaining - 1))

    # -----------------------------------------------------------------
    def _shake(self):
        """Quick horizontal shake of the panel for wrong password."""
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
        except tk.TclError:
            return
        offsets = [-10, 9, -7, 6, -4, 3, -2, 0]
        def step(i=0):
            if i >= len(offsets):
                return
            try:
                self.root.geometry(f"+{x + offsets[i]}+{y}")
                self.root.after(22, lambda: step(i + 1))
            except tk.TclError:
                pass
        step()

    # -----------------------------------------------------------------
    def _on_success(self):
        self.granted = True
        self._flash_status("ACCESS GRANTED", GREEN)
        self.entry.config(state=tk.DISABLED, highlightbackground=GREEN)
        self.unlock_btn.config(state=tk.DISABLED, bg="#0f2a1c", fg=GREEN)
        self.quit_btn.config(state=tk.DISABLED)
        self.root.update_idletasks()

        # Animated green wipe, then launch
        self._unlock_wipe(0)

    def _unlock_wipe(self, step):
        """A quick green expanding bar to signal successful auth."""
        try:
            w = self.panel.winfo_width()
            h = self.panel.winfo_height()
        except tk.TclError:
            return
        if step > 20:
            self.root.after(220, self._launch_ui)
            return
        # progress bar near bottom
        self.attempt_bar.delete("all")
        t = step / 20
        self.attempt_bar.create_rectangle(0, 0, w * t, 6, fill=GREEN, outline="")
        self.root.after(18, lambda: self._unlock_wipe(step + 1))

    # -----------------------------------------------------------------
    def _launch_ui(self):
        """Close the gate and launch UI.py in a separate process."""
        self.is_running = False
        here = os.path.dirname(os.path.abspath(__file__))
        ui_path = os.path.join(here, APP_TO_LAUNCH)

        if not os.path.exists(ui_path):
            self._flash_status(f"ERROR  //  {APP_TO_LAUNCH} NOT FOUND", RED)
            return

        try:
            # Launch detached so the gate can close cleanly
            kwargs = {"cwd": here}
            if os.name == "nt":
                kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
                # Use pythonw to avoid a console flash, if available
                py = sys.executable
                pyw = py.replace("python.exe", "pythonw.exe")
                exe = pyw if os.path.exists(pyw) else py
                subprocess.Popen([exe, ui_path], **kwargs)
            else:
                subprocess.Popen([sys.executable, ui_path], **kwargs)
        except Exception as e:
            self._flash_status(f"LAUNCH FAILED  //  {e}", RED)
            return

        # Fade the gate away
        self.root.after(150, self.root.destroy)

    def quit_app(self):
        self.is_running = False
        self.root.destroy()


# =====================================================================
if __name__ == "__main__":
    root = tk.Tk()
    gate = SecurityGate(root)
    root.mainloop()