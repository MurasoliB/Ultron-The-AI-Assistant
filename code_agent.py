"""
ULTRON // CODE AGENT (GHOST TYPER FOR CODETANTRA & WEB IDES)
============================================================
Autonomous typing agent designed to bypass tab-switching and copy-paste
restrictions on platforms like CodeTantra, HackerRank, and online lab exams.

Features:
- Keystroke-by-keystroke OS-level input emulation (SendInput/pynput).
  To the browser, it appears 100% identical to physical human typing.
- Guaranteed focus lock: the CodeTantra / browser window is located (ranked by
  cursor position, foreground state and title), activated with the Alt-tick
  foreground-lock bypass and verified before a single character is sent.
- Automatic mouse assist: finds the CodeTantra window, moves the mouse smoothly
  inside it and clicks the code editor itself (UI Automation first, geometric
  fallback) so the caret is placed and typing starts with zero manual work.
- Elevation (UIPI) detection: warns when CodeTantra runs as Administrator and
  every keystroke would be silently dropped by Windows.
- Honest reporting: counts delivered keystrokes and reports failure instead of
  fake success when Windows refuses the input.
- Smart Auto-Indent Compensation: Cleans auto-inserted whitespace back to
  column 0 on newlines so code retains exact indentation with zero drift.
- Tab-to-Spaces expansion to prevent accidental browser focus escape.
- Global Hotkeys:
    [F8]     : Start typing code (zero focus-loss / no tab switch required)
    [F9]/ESC : Instant Emergency Abort
- Countdown timer with optional audio cues (beeps & TTS).
- Dark Ultron-themed GUI interface with customizable speeds.
"""

import os
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import time
import json
import ctypes
from ctypes import wintypes
import random
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pyautogui
pyautogui.FAILSAFE = False
import pyperclip
from pynput.keyboard import Controller, Key, KeyCode, Listener
from pynput import mouse

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False


# -------------------------------------------------------------
# SPEED CONFIGURATIONS (seconds per character & per line)
# -------------------------------------------------------------
SPEED_PRESETS = {
    "TURBO": {
        "char_delay": 0.015,
        "line_delay": 0.03,
        "jitter": 0.001,
        "label": "Turbo (~60 chars/sec)"
    },
    "FAST": {
        "char_delay": 0.030,
        "line_delay": 0.06,
        "jitter": 0.002,
        "label": "Smooth & In-Order (Recommended)"
    },
    "STEALTH": {
        "char_delay": 0.065,
        "line_delay": 0.15,
        "jitter": 0.015,
        "label": "Human Stealth (Natural Cadence)"
    }
}


# -------------------------------------------------------------
# ELEVATION & WINDOW TARGETING HELPERS
# -------------------------------------------------------------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
VK_SHIFT = 0x10


def is_admin():
    """Checks whether the current Python process has Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def restart_as_admin():
    """Restarts Ultron as Administrator by prompting the standard Windows UAC dialog."""
    try:
        script = os.path.abspath(sys.argv[0]) if sys.argv else os.path.abspath(__file__)
        args = f'"{script}"'
        if len(sys.argv) > 1:
            args += " " + " ".join(f'"{a}"' for a in sys.argv[1:])
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            args,
            os.path.dirname(script),
            1
        )
        if ret > 32:
            os._exit(0)
            return True
    except Exception as e:
        print(f"[Admin] Elevation error: {e}")
    return False


# Default window-title keywords used to locate the CodeTantra / exam / browser window.
DEFAULT_TARGET_KEYWORDS = (
    "codetantra", "tantra", "sea", "exam", "viva", "assess", "practice",
    "code", "test", "lab", "browser",
    "chrome", "chromium", "edge", "firefox", "mozilla", "brave", "opera",
)
_EXCLUDED_TITLE_KEYWORDS = ("ultron", "code agent", "visual studio", "jdk", "maven", "gradle")

# Private Win32 handles with strict typing so 64-bit HWNDs are never truncated.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv32 = ctypes.WinDLL("advapi32", use_last_error=True)

# Pin DPI awareness ONCE at startup: uiautomation flips this process to DPI-aware
# when first imported, which would otherwise make window rects (measured before)
# and pyautogui mouse moves (made after) use DIFFERENT coordinate scales ->
# the mouse would land in the wrong place. One awareness for the whole process
# keeps Win32 rects, UIA rectangles and cursor moves consistent (physical pixels).
try:
    _u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _u32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    _u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        _u32.SetProcessDPIAware()
    except Exception:
        pass

_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_u32.GetAncestor.restype = wintypes.HWND
_u32.WindowFromPoint.argtypes = [wintypes.POINT]
_u32.WindowFromPoint.restype = wintypes.HWND
_u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindowVisible.restype = wintypes.BOOL
_u32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
_u32.IsChild.restype = wintypes.BOOL
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextLengthW.restype = ctypes.c_int
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextW.restype = ctypes.c_int
_u32.SetForegroundWindow.argtypes = [wintypes.HWND]
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.BringWindowToTop.argtypes = [wintypes.HWND]
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.GetWindowRect.restype = wintypes.BOOL
_u32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT
]
_u32.SetWindowPos.restype = wintypes.BOOL
_k32.GetCurrentProcessId.restype = wintypes.DWORD
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_adv32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv32.OpenProcessToken.restype = wintypes.BOOL
_adv32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
]
_adv32.GetTokenInformation.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_u32.EnumWindows.restype = wintypes.BOOL

GA_ROOT = 2
SW_RESTORE = 9
SW_MINIMIZE = 6
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


def _window_text(hwnd):
    """WindowText of a top-level handle ('' when unavailable)."""
    try:
        length = _u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buff = ctypes.create_unicode_buffer(length + 1)
        _u32.GetWindowTextW(hwnd, buff, length + 1)
        return buff.value
    except Exception:
        return ""


def _window_pid(hwnd):
    try:
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _window_root(hwnd):
    """Top-level owner of a (possibly child) window handle."""
    try:
        return _u32.GetAncestor(hwnd, GA_ROOT) or hwnd
    except Exception:
        return hwnd


def _window_under_cursor():
    """Top-level window currently under the mouse cursor (the app the user is looking at)."""
    try:
        pt = wintypes.POINT()
        if not _u32.GetCursorPos(ctypes.byref(pt)):
            return None
        hwnd = _u32.WindowFromPoint(pt)
        return _window_root(hwnd) if hwnd else None
    except Exception:
        return None


def process_is_elevated(pid):
    """True/False when determinable, otherwise None (process could not be opened)."""
    try:
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            token = wintypes.HANDLE()
            if not _adv32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
                return None
            try:
                elevated = wintypes.DWORD()
                size = wintypes.DWORD()
                if not _adv32.GetTokenInformation(
                    token, TOKEN_ELEVATION, ctypes.byref(elevated),
                    ctypes.sizeof(elevated), ctypes.byref(size)
                ):
                    return None
                return bool(elevated.value)
            finally:
                _k32.CloseHandle(token)
        finally:
            _k32.CloseHandle(handle)
    except Exception:
        return None


def window_is_elevated(hwnd):
    """True/False when determinable, otherwise None."""
    if not hwnd:
        return None
    pid = _window_pid(hwnd)
    return process_is_elevated(pid) if pid else None


def _window_rect(hwnd):
    """(left, top, right, bottom) in screen coordinates, or None."""
    try:
        rect = wintypes.RECT()
        if not _u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def _foreground_root():
    """Top-level window that currently owns the keyboard focus (None when unknown)."""
    try:
        fg = _u32.GetForegroundWindow()
        return _window_root(fg) if fg else None
    except Exception:
        return None


def _window_at_point(x, y):
    """Top-level window that would actually receive a click at (x, y) — i.e. whatever
    is topmost at that spot (this catches Ultron's own overlay intercepting the click)."""
    try:
        pt = wintypes.POINT(int(x), int(y))
        hwnd = _u32.WindowFromPoint(pt)
        return _window_root(hwnd) if hwnd else None
    except Exception:
        return None


HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def _raise_above(hwnd):
    """Temporarily floats the target above every obstacle (Ultron's own topmost
    windows included) so the mouse click is guaranteed to land on it."""
    try:
        return bool(_u32.SetWindowPos(
            hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        ))
    except Exception:
        return False
    """Never elevate target to HWND_TOPMOST over Ultron's floating doc agent / mic button."""
    return True


def _lower_back(hwnd):
    """Undoes _raise_above (only call this when WE changed the topmost state)."""
    try:
        return bool(_u32.SetWindowPos(
            hwnd, wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        ))
    except Exception:
        return False
    """No-op companion to _raise_above."""
    return True


_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetWindowLongW.restype = ctypes.c_long
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008


def _is_topmost(hwnd):
    """True when the window already had the topmost (kiosk) style - we must never
    strip that away, it belongs to the exam app itself."""
    try:
        return bool(_u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)
    except Exception:
        return False


def _uia_module():
    """Returns the uiautomation module, or None when unavailable."""
    try:
        import uiautomation as auto
        return auto
    except Exception:
        return None


def _uia_editor_controls(hwnd, timeout=2.0):
    """Ordered editor-candidate CONTROLS inside the CodeTantra window: real
    Edit/Document boxes (largest first), panes named like an editor, and Ace's
    tiny hidden <textarea>. Used both for UIA SetFocus and for deriving click points."""
    auto = _uia_module()
    if auto is None:
        return []
    try:
        try:
            auto.SetGlobalSearchTimeout(0.3)
        except Exception:
            pass
        root = auto.ControlFromHandle(int(hwnd))
        if root is None:
            return []
        deadline = time.time() + timeout
        stack = [(root, 0)]
        seen = 0
        real_edits, tiny_edits, named_panes = [], [], []
        name_hints = ("editor", "ace", "code", "program", "write", "language", "solution")

        while stack and seen < 250 and time.time() < deadline:
            ctrl, depth = stack.pop()
            seen += 1
            try:
                ct = ctrl.ControlTypeName
                rect = ctrl.BoundingRectangle
                width = height = 0
                if rect:
                    try:
                        width, height = rect.right - rect.left, rect.bottom - rect.top
                    except Exception:
                        rect = None
                if rect and width > 0 and height > 0:
                    if ct in ("EditControl", "DocumentControl"):
                        if width >= 40 and height >= 20:
                            real_edits.append((width * height, ctrl))
                        elif width <= 8 and height <= 8:
                            tiny_edits.append((0, ctrl))  # Ace's 1px hidden textarea
                    elif width >= 80 and height >= 60 and ct in (
                        "PaneControl", "GroupControl", "CustomControl", "TextControl"
                    ):
                        name = (ctrl.Name or "").lower()
                        if any(hint in name for hint in name_hints):
                            named_panes.append((width * height, ctrl))
                if depth < 5:
                    child = ctrl.GetFirstChildControl()
                    while child is not None and seen < 250 and time.time() < deadline:
                        stack.append((child, depth + 1))
                        seen += 1
                        child = child.GetNextSiblingControl()
            except Exception:
                continue

        real_edits.sort(key=lambda item: item[0], reverse=True)
        named_panes.sort(key=lambda item: item[0], reverse=True)
        return (
            [c for _, c in real_edits[:2]]
            + [c for _, c in named_panes[:1]]
            + [c for _, c in tiny_edits[:1]]
        )
    except Exception:
        return []


def _uia_control_point(ctrl):
    """Click point for an editor control: centre of real boxes, or just right of
    Ace's ~1px hidden textarea (which sits on the caret line inside the editor)."""
    try:
        rect = ctrl.BoundingRectangle
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 8 and height <= 8:
            return (int(rect.left + 24), int(rect.top + 10))
        return (int(rect.left + width * 0.40), int(rect.top + height * 0.50))
    except Exception:
        return None


def _uia_editor_candidates(hwnd, timeout=2.0):
    """Click points derived from _uia_editor_controls()."""
    points = []
    for ctrl in _uia_editor_controls(hwnd, timeout):
        point = _uia_control_point(ctrl)
        if point:
            points.append(point)
    return points


def _pick_target_by_process():
    """Legacy fallback: locate the CodeTantra desktop client via its running process
    (matches process name *and* executable path, e.g. ...\\CodeTantra\\SeaClient.exe)."""
    found = []
    try:
        import psutil
        name_hits = ("codetantra", "tantra", "sea", "elder", "exam", "assess")
        path_hits = ("codetantra", "tantra")
        target_pids = set()
        for p in psutil.process_iter(['pid', 'name', 'exe']):
            pname = (p.info['name'] or '').lower()
            ppath = (p.info.get('exe') or '').lower()
            if any(kw in pname for kw in name_hits) or any(kw in ppath for kw in path_hits):
                target_pids.add(p.info['pid'])
        if target_pids:
            def _proc_enum_cb(hwnd, _):
                if _u32.IsWindowVisible(hwnd) and _window_pid(hwnd) in target_pids:
                    found.append(hwnd)
                return True
            _u32.EnumWindows(WNDENUMPROC(_proc_enum_cb), 0)
    except Exception:
        pass
    return found[0] if found else None


def pick_target_window(title_keywords=None):
    """Ranks every matching window and returns the one the user actually means.

    Scoring prefers the window under the mouse cursor (+100), then the current
    foreground window (+60), then anything whose title screams CodeTantra/exam (+30).
    """
    keywords = tuple(
        k.lower() for k in (title_keywords if title_keywords is not None else DEFAULT_TARGET_KEYWORDS)
    )
    own_pid = _k32.GetCurrentProcessId()
    cursor_root = _window_under_cursor()
    fg = _u32.GetForegroundWindow()
    fg_root = _window_root(fg) if fg else None
    matches = []

    def _enum_cb(hwnd, _):
        if not _u32.IsWindowVisible(hwnd):
            return True
        title = _window_text(hwnd)
        if not title:
            return True
        lowered = title.lower()
        if any(kw in lowered for kw in _EXCLUDED_TITLE_KEYWORDS):
            return True
        pid = _window_pid(hwnd)
        if not pid or pid == own_pid:
            return True
        if not any(kw in lowered for kw in keywords):
            return True
        score = 0
        root = _window_root(hwnd)
        if cursor_root and root == cursor_root:
            score += 100
        if fg_root and root == fg_root:
            score += 60
        if any(kw in lowered for kw in ("codetantra", "exam", "viva", "sea")):
            score += 30
        matches.append((score, hwnd))
        return True

    try:
        _u32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception as e:
        print(f"[Focus] Window enumeration error: {e}")

    if matches:
        matches.sort(key=lambda item: item[0], reverse=True)
        return matches[0][1]
    if title_keywords is None:
        return _pick_target_by_process()
    return None


def _activate_window(target):
    """Brings `target` to the foreground, defeating the Windows foreground-lock."""
    try:
        fg = _u32.GetForegroundWindow()
        if fg and (fg == target or _u32.IsChild(target, fg)):
            return True  # already focused: never disturb the caret

        curr_thread = _k32.GetCurrentThreadId()
        fore_thread = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fore_thread and fore_thread != curr_thread:
            try:
                attached = bool(user32.AttachThreadInput(curr_thread, fore_thread, True))
            except Exception:
                attached = False
        try:
            _u32.ShowWindow(target, SW_RESTORE)
            _u32.BringWindowToTop(target)
            # Alt-tick: Windows only honours SetForegroundWindow after a real key event
            user32.keybd_event(0x12, 0x38, 0, 0)  # Alt down
            time.sleep(0.02)
            user32.keybd_event(0x12, 0x38, KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
            _u32.SetForegroundWindow(target)
            # Re-pin Document Agent window above target so it is never covered or minimized
            try:
                doc_hwnd = _u32.FindWindowW(None, "ULTRON // ALWAYS-ON-TOP DOC AGENT")
                if doc_hwnd:
                    _u32.SetWindowPos(doc_hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            except Exception:
                pass
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(curr_thread, fore_thread, False)
                except Exception:
                    pass
        time.sleep(0.05)
        fg_after = _u32.GetForegroundWindow()
        return bool(fg_after) and (fg_after == target or _u32.IsChild(target, fg_after))
    except Exception as e:
        print(f"[Focus] Activation error: {e}")
        return False


def focus_target_window(title_keywords=None):
    """Finds and activates the CodeTantra / exam / browser window. True when verified."""
    try:
        target = pick_target_window(title_keywords)
        if not target:
            return False
        if _activate_window(target):
            return True
        # One retry after re-picking (the visible window may have changed)
        target = pick_target_window(title_keywords)
        return bool(target) and _activate_window(target)
    except Exception as e:
        print(f"[Focus] Window focus error: {e}")
    return False


# -------------------------------------------------------------
# HARDWARE & DIRECT UNICODE INPUT EMULATION
# -------------------------------------------------------------
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_BACK = 0x08
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

SHIFT_SCAN = 0x2A
ENTER_SCAN = 0x1C
TAB_SCAN = 0x0F
BACK_SCAN = 0x0E

_fallback_keyboard = Controller()

# --- 64-bit SendInput Structures (Direct OS Unicode Injection) ---
INPUT_KEYBOARD = 1

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD)
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t)
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ('mi', MOUSEINPUT),
        ('ki', KEYBDINPUT),
        ('hi', HARDWAREINPUT)
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ('type', wintypes.DWORD),
        ('u', _INPUT_UNION)
    ]

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def safe_vk_key_scan(c):
    """Safely calls VkKeyScanW handling both wchar and integer calling conventions."""
    try:
        return user32.VkKeyScanW(c)
    except Exception:
        try:
            return user32.VkKeyScanW(ord(c))
        except Exception:
            return -1


def send_unicode_char(c):
    """Sends a character directly via 64-bit SendInput UNICODE to bypass keyboard layout,
    Caps Lock, Shift states, and anti-cheat filters (native Chromium/Monaco DOM input)."""
    if c == '\n':
        inp_down = INPUT()
        inp_down.type = INPUT_KEYBOARD
        inp_down.u.ki.wVk = VK_RETURN
        inp_down.u.ki.wScan = ENTER_SCAN
        inp_down.u.ki.dwFlags = 0
        inp_down.u.ki.time = 0
        inp_down.u.ki.dwExtraInfo = 0

        inp_up = INPUT()
        inp_up.type = INPUT_KEYBOARD
        inp_up.u.ki.wVk = VK_RETURN
        inp_up.u.ki.wScan = ENTER_SCAN
        inp_up.u.ki.dwFlags = KEYEVENTF_KEYUP
        inp_up.u.ki.time = 0
        inp_up.u.ki.dwExtraInfo = 0

        user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
        time.sleep(0.015)
        return user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT)) == 1

    if c == '\t':
        inp_down = INPUT()
        inp_down.type = INPUT_KEYBOARD
        inp_down.u.ki.wVk = VK_TAB
        inp_down.u.ki.wScan = TAB_SCAN
        inp_down.u.ki.dwFlags = 0
        inp_down.u.ki.time = 0
        inp_down.u.ki.dwExtraInfo = 0

        inp_up = INPUT()
        inp_up.type = INPUT_KEYBOARD
        inp_up.u.ki.wVk = VK_TAB
        inp_up.u.ki.wScan = TAB_SCAN
        inp_up.u.ki.dwFlags = KEYEVENTF_KEYUP
        inp_up.u.ki.time = 0
        inp_up.u.ki.dwExtraInfo = 0

        user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
        time.sleep(0.015)
        return user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT)) == 1

    code = ord(c)
    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.u.ki.wVk = 0
    inp_down.u.ki.wScan = code
    inp_down.u.ki.dwFlags = KEYEVENTF_UNICODE
    inp_down.u.ki.time = 0
    inp_down.u.ki.dwExtraInfo = 0

    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.u.ki.wVk = 0
    inp_up.u.ki.wScan = code
    inp_up.u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    inp_up.u.ki.time = 0
    inp_up.u.ki.dwExtraInfo = 0

    arr = (INPUT * 2)(inp_down, inp_up)
    return user32.SendInput(2, arr, ctypes.sizeof(INPUT)) == 2


def hardware_press_char(c):
    """Sends authentic physical keystrokes (Virtual Key + Hardware Scan Code) via Win32 keybd_event.
    Holds keys for 20ms so Chromium's V8 event loop registers keydown, keypress, and DOM input."""
    if c == '\n':
        user32.keybd_event(VK_RETURN, ENTER_SCAN, 0, 0)
        time.sleep(0.020)
        user32.keybd_event(VK_RETURN, ENTER_SCAN, KEYEVENTF_KEYUP, 0)
        return True

    if c == '\t':
        user32.keybd_event(VK_TAB, TAB_SCAN, 0, 0)
        time.sleep(0.020)
        user32.keybd_event(VK_TAB, TAB_SCAN, KEYEVENTF_KEYUP, 0)
        return True

    if c == '\b':
        user32.keybd_event(VK_BACK, BACK_SCAN, 0, 0)
        time.sleep(0.020)
        user32.keybd_event(VK_BACK, BACK_SCAN, KEYEVENTF_KEYUP, 0)
        return True

    res = safe_vk_key_scan(c)
    if res == -1 or res == 0xFFFF:
        return False

    vk = res & 0xFF
    shift = ((res >> 8) & 0x01) != 0
    ctrl = ((res >> 8) & 0x02) != 0
    alt = ((res >> 8) & 0x04) != 0
    scan = user32.MapVirtualKeyW(vk, 0)

    # Press modifiers
    if ctrl:
        user32.keybd_event(0x11, 0x1D, 0, 0)
        time.sleep(0.005)
    if alt:
        user32.keybd_event(0x12, 0x38, 0, 0)
        time.sleep(0.005)
    if shift:
        user32.keybd_event(VK_SHIFT, SHIFT_SCAN, 0, 0)
        time.sleep(0.005)

    # Press & release key with 20ms hold time
    user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.020)
    user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, 0)

    # Release modifiers
    if shift:
        time.sleep(0.005)
        user32.keybd_event(VK_SHIFT, SHIFT_SCAN, KEYEVENTF_KEYUP, 0)
    if alt:
        time.sleep(0.005)
        user32.keybd_event(0x12, 0x38, KEYEVENTF_KEYUP, 0)
    if ctrl:
        time.sleep(0.005)
        user32.keybd_event(0x11, 0x1D, KEYEVENTF_KEYUP, 0)
    return True


def send_paste_injection(code_text):
    """Instant clipboard injection via hardware Ctrl+V and Shift+Insert bypass."""
    try:
        pyperclip.copy(code_text)
        time.sleep(0.05)
        # 1. Hardware Ctrl+V
        user32.keybd_event(0x11, 0x1D, 0, 0)
        user32.keybd_event(0x56, 0x2F, 0, 0)
        time.sleep(0.025)
        user32.keybd_event(0x56, 0x2F, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(0x11, 0x1D, KEYEVENTF_KEYUP, 0)

        # 2. Hardware Shift+Insert (Universal terminal/IDE paste)
        time.sleep(0.05)
        user32.keybd_event(0x10, 0x2A, 0, 0)
        user32.keybd_event(0x2D, 0x52, 0, 0)
        time.sleep(0.025)
        user32.keybd_event(0x2D, 0x52, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(0x10, 0x2A, KEYEVENTF_KEYUP, 0)
        return True
    except Exception as e:
        print(f"[Paste] Error: {e}")
        return False


# -------------------------------------------------------------
# CORE CODE TYPER ENGINE
# -------------------------------------------------------------
class CodeTyper:
    """Manages keystroke simulation, hotkeys, and safe abort handling."""

    def __init__(self):
        self.code_text = ""
        self.is_typing = False
        self._armed = False
        self.injection_mode = "UNICODE"  # UNICODE (Default/Smooth), HYBRID, SCANCODE, PASTE
        self._stop_event = threading.Event()
        self._thread = None
        self._keyboard = Controller()
        self._listener = None
        self._mouse_listener = None
        self.speed_mode = "FAST"
        self.auto_indent_fix = False  # Disabled by default so newlines are never deleted
        self.expand_tabs = True
        self.tab_spaces = 4
        self.audio_cues = True
        self.countdown_seconds = 3
        # One safety click at the mouse position (only when it is over the CodeTantra window)
        # so the text caret is placed inside the editor before typing starts.
        self.auto_click_editor = True
        self._chars_sent = 0
        self._chars_failed = 0
        self._minimized_self = []
        self._last_user_click_ts = 0.0
        self._last_user_click_pos = None

        # Callbacks for GUI or mic button state
        self.on_progress = None       # func(line_idx, total_lines, pct, status_str)
        self.on_status_change = None  # func(status_str)
        self.on_state_change = None   # func(is_typing)

        self._start_mouse_listener()

    def _start_mouse_listener(self):
        """Monitors for user mouse clicks on screen to trigger instant Click-to-Inject."""
        if self._mouse_listener is not None:
            return

        def _on_click(x, y, button, pressed):
            try:
                if button == mouse.Button.left:
                    self._last_user_click_ts = time.time()
                    self._last_user_click_pos = (x, y)
                # When user releases left-click while ARMED
                if not pressed and button == mouse.Button.left and self._armed:
                    self._armed = False
                    self._notify_status("🎯 Click detected in CodeTantra! Typing begins in 250ms...")
                    self._beep(1400, 150)
                    time.sleep(0.25)
                    self.start_typing(countdown=0)
            except Exception as e:
                print(f"[MouseHook] Error: {e}")

        try:
            self._mouse_listener = mouse.Listener(on_click=_on_click)
            self._mouse_listener.daemon = True
            self._mouse_listener.start()
        except Exception as e:
            print(f"[CodeTyper] Mouse hook error: {e}")

    def arm_click_to_inject(self, code=None):
        """Arms Ultron to wait for the user to left-click inside CodeTantra, then types immediately."""
        if self.is_typing:
            return False

        if code is not None:
            self.code_text = code

        if not self.code_text.strip():
            self._notify_status("Error: No code provided to type.")
            return False

        self._armed = True
        self._beep(1300, 120)
        self._notify_status("🎯 ARMED! Move mouse and CLICK inside CodeTantra code box...")
        return True

    def disarm(self):
        if self._armed:
            self._armed = False
            self._notify_status("Disarmed.")

    def _get_code_or_clip(self):
        code = self.code_text.strip()
        if not code:
            try:
                clip = pyperclip.paste().strip()
                if clip and len(clip) >= 3:
                    code = clip
            except Exception:
                pass
        return code

    def start_hotkeys(self):
        """Starts global keyboard listener for F8 (start), F7 (armed click), and F9/Esc (abort)."""
        if self._listener is not None:
            return

        def on_press(key):
            try:
                # F8: Trigger typing with countdown
                if key == Key.f8:
                    if not self.is_typing:
                        code_to_type = self._get_code_or_clip()
                        if code_to_type:
                            self.start_typing(code=code_to_type, countdown=self.countdown_seconds)
                        else:
                            self._notify_status("F8 pressed, but no code loaded or in clipboard!")

                # F7: ARM Click-to-Inject (instant typing on next click inside CodeTantra)
                elif key == Key.f7:
                    if not self.is_typing:
                        code_to_type = self._get_code_or_clip()
                        if code_to_type:
                            self.arm_click_to_inject(code=code_to_type)
                        else:
                            self._notify_status("F7 pressed, but no code loaded or in clipboard!")

                # F9 or ESC: Emergency abort / disarm
                elif key in (Key.f9, Key.esc):
                    if self._armed:
                        self.disarm()
                    if self.is_typing:
                        self.abort()

            except Exception as e:
                print(f"[CodeTyper] Hotkey handler error: {e}")

        try:
            self._listener = Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:
            print(f"[CodeTyper] Could not start hotkey listener: {e}")

    def stop_hotkeys(self):
        """Stops global keyboard listener."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _beep(self, freq, duration):
        """Play a non-blocking system beep if audio cues enabled."""
        if not self.audio_cues or not WINSOUND_AVAILABLE:
            return
        def _worker():
            try:
                winsound.Beep(int(freq), int(duration))
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def _notify_status(self, msg):
        if self.on_status_change:
            try:
                self.on_status_change(msg)
            except Exception:
                pass
        print(f"[CodeTyper] {msg}")

    def _notify_state(self, is_typing):
        self.is_typing = is_typing
        if self.on_state_change:
            try:
                self.on_state_change(is_typing)
            except Exception:
                pass

    def start_typing(self, code=None, countdown=None):
        """Begins typing workflow in a background thread."""
        if self.is_typing:
            return False

        if code is not None:
            self.code_text = code

        if not self.code_text.strip():
            self._notify_status("Error: No code provided to type.")
            return False

        if countdown is not None:
            self.countdown_seconds = countdown

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_typing_process, daemon=True)
        self._thread.start()
        return True

    def abort(self):
        """Immediately signals the typing thread to terminate."""
        if self.is_typing:
            self._stop_event.set()
            self._beep(450, 300)
            self._notify_status("TYPING ABORTED BY USER (F9/ESC).")

    def _prepare_code(self, raw_code: str):
        """Normalizes newlines, expands tabs to spaces if enabled."""
        if not raw_code or not raw_code.strip():
            return []
        code = raw_code.replace("\r\n", "\n").replace("\r", "\n")
        if self.expand_tabs:
            code = code.replace("\t", " " * self.tab_spaces)
        lines = code.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    # ---------------------------------------------------------
    # FOCUS GUARANTEE HELPERS (keystrokes must land in CodeTantra)
    # ---------------------------------------------------------
    def _focus_is_ours(self):
        """True when Ultron's own window currently owns the keyboard focus."""
        try:
            hwnd = _u32.GetForegroundWindow()
            if not hwnd:
                return True
            return _window_pid(hwnd) == _k32.GetCurrentProcessId()
        except Exception:
            return True

    def _target_requires_admin(self):
        """True when the CodeTantra window is elevated while Ultron is not (UIPI would eat our keys)."""
        try:
            target = pick_target_window()
            if not target:
                return False
            return bool(window_is_elevated(target))
        except Exception:
            return False

    def _minimize_own_windows(self):
        """Never minimize Ultron windows: the user needs them visible to see code."""
        self._minimized_self = []

    def _restore_own_windows(self):
        self._minimized_self = []

    def _secure_focus_for_typing(self, timeout=5.0):
        """Guarantees keystrokes land in CodeTantra instead of Ultron's own (locked) window.

        1. If another app already owns focus, assume the user clicked the editor: proceed.
        2. Otherwise actively activate the CodeTantra window for up to `timeout` seconds.
        3. Last resort: minimize Ultron itself so Windows must hand focus away.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._focus_is_ours():
                return True
            focus_target_window()
            if not self._focus_is_ours():
                return True
            time.sleep(0.25)

        self._minimize_own_windows()
        focus_target_window()
        time.sleep(0.2)
        if not self._focus_is_ours():
            return True
        self._restore_own_windows()
        return False

    def _user_recently_clicked_in(self, target):
        """True when the user's own click (last 2 s) already landed inside `target`,
        meaning they placed the caret themselves and we must not click again."""
        if time.time() - self._last_user_click_ts >= 2.0:
            return False
        pos = getattr(self, "_last_user_click_pos", None)
        if not pos:
            return True  # conservative: assume the recent click was the editor click
        try:
            pt = wintypes.POINT(int(pos[0]), int(pos[1]))
            return _window_root(_u32.WindowFromPoint(pt)) == target
        except Exception:
            return True

    def _editor_click_candidates(self, target):
        """Ordered click points believed to land inside CodeTantra's Ace code editor.

        1. UI Automation discoveries (real inputs, Ace's hidden textarea, named panes).
        2. CodeTantra modal geometry: question panel occupies the top/left, the Ace
           editor sits centre/lower-right, and the Run/Submit bar hugs the very bottom
           (candidates are clamped upward to stay clear of it).
        """
        points, seen = [], set()

        def add(x, y):
            rect_now = _window_rect(target)
            if rect_now:
                left, top, right, bottom = rect_now
                if right - left < 80 or bottom - top < 80:
                    return
                x = min(max(int(x), left + 30), right - 30)
                y = min(max(int(y), top + 50), bottom - 40)
            else:
                x, y = int(x), int(y)
            key = (x // 25, y // 25)
            if key in seen:
                return
            seen.add(key)
            points.append((x, y))

        for cand in _uia_editor_candidates(target):
            add(*cand)

        rect = _window_rect(target)
        if rect:
            left, top, right, bottom = rect
            width, height = right - left, bottom - top
            if width >= 160 and height >= 160:
                add(left + width * 0.50, top + height * 0.62)  # main editor area
                add(left + width * 0.72, top + height * 0.45)  # right-side editor pane
                add(left + width * 0.50, top + height * 0.72)  # editor above the button bar
        return points

    def _editor_focused(self, near=None, tolerance=200, must_window=None):
        """Did the code editor take focus after our click?

        True  -> a text editor owns the focus, it sits near the click point, AND
                 (when given) the foreground window is the CodeTantra window itself
        False -> a button/other widget, a different text box, or the wrong window
                 received the click (try the next point)
        None  -> cannot be determined (UIA unavailable or ambiguous pane focus)
        """
        auto = _uia_module()
        if auto is None:
            return None
        try:
            # The focused element must live in the TARGET window: Ultron's own GUI
            # text box is an EditControl too and must never count as success.
            if must_window is not None and _foreground_root() != must_window:
                return False
            try:
                ctrl = auto.GetFocusedControl()
            except Exception:
                return None
            if ctrl is None:
                return None
            ct = ctrl.ControlTypeName
            if ct in ("EditControl", "DocumentControl"):
                if near:
                    try:
                        r = ctrl.BoundingRectangle
                        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                        if abs(cx - near[0]) > tolerance or abs(cy - near[1]) > tolerance:
                            return False  # some *other* text box got the click
                    except Exception:
                        pass
                return True
            if ct in (
                "ButtonControl", "HyperlinkControl", "TabItemControl", "MenuItemControl",
                "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "ListItemControl",
                "TreeItemControl", "SliderControl"
            ):
                return False
            return None  # Pane/Text/Group/Window: ambiguous
        except Exception:
            return None

    def _verify_text_visible(self, lines):
        """Reads the CodeTantra window's UIA tree AFTER typing and looks for the code
        we just sent. True -> text is really on screen; False -> keys were delivered
        but never rendered (wrong element had the caret); None -> cannot read it back."""
        target = pick_target_window()
        if not target:
            return None
        auto = _uia_module()
        if auto is None:
            return None
        probes = []
        for line in lines:
            norm = "".join(line.split()).lower()
            if len(norm) >= 8:
                probes.append(norm[:25])
            if len(probes) >= 3:
                break
        if not probes:
            return None
        try:
            try:
                auto.SetGlobalSearchTimeout(0.25)
            except Exception:
                pass
            root = auto.ControlFromHandle(int(target))
            if root is None:
                return None
            deadline = time.time() + 2.5
            stack = [(root, 0)]
            seen = 0
            while stack and seen < 400 and time.time() < deadline:
                ctrl, depth = stack.pop()
                seen += 1
                try:
                    name = ctrl.Name or ""
                    if name:
                        norm = "".join(name.split()).lower()
                        if any(p in norm for p in probes):
                            return True
                    if ctrl.ControlTypeName == "EditControl":
                        try:
                            value = ctrl.GetValue() or ""
                            norm = "".join(value.split()).lower()
                            if any(p in norm for p in probes):
                                return True
                        except Exception:
                            pass
                    if depth < 6:
                        child = ctrl.GetFirstChildControl()
                        while child is not None and seen < 400 and time.time() < deadline:
                            stack.append((child, depth + 1))
                            seen += 1
                            child = child.GetNextSiblingControl()
                except Exception:
                    continue
            return False  # full tree scanned, our code is nowhere in it
        except Exception:
            return None

    def _focus_editor_via_uia(self, target):
        """Stage 1 caret placement: ask the editor control itself to take focus via
        the UIA FocusPattern - no mouse movement, immune to topmost/z-order games."""
        auto = _uia_module()
        if auto is None:
            return False
        for ctrl in _uia_editor_controls(target):
            try:
                ctrl.SetFocus()
                time.sleep(0.12)
            except Exception:
                continue
            if self._editor_focused(must_window=target) is True:
                return True
        return False

    def _reassert_editor_focus(self, target):
        """Caret replant used when a kiosk app steals focus mid-typing:
        UIA focus first, then at most two owned-pixel mouse clicks."""
        try:
            if self._focus_editor_via_uia(target):
                return True
            for x, y in self._editor_click_candidates(target)[:2]:
                if _window_at_point(x, y) != target:
                    continue
                pyautogui.click(x, y)
                time.sleep(0.12)
                if self._editor_focused(near=(x, y), must_window=target) is True:
                    return True
            return False
        except Exception:
            return False

    def _click_editor_into_place(self):
        """AUTOMATIC MOUSE ASSIST (Ace-aware): finds the CodeTantra window, moves the
        mouse smoothly inside, clicks a candidate spot of the Ace code editor and
        VERIFIES the text editor actually took focus before typing is allowed to start.
        When the click missed (question panel, button, wrong widget) it tries the next
        candidate instead of typing into the void."""
        try:
            target = pick_target_window()
            if not target:
                self._notify_status("Could not find the CodeTantra window to click into.")
                return False
            title = _window_text(target) or "CodeTantra"
            if self._user_recently_clicked_in(target):
                return False  # caret already placed by the user's own click
            # Ultron's own always-on-top GUI/overlay windows can intercept the click
            # (and they are text boxes too -> would swallow every keystroke).
            # Get them out of the way; they are restored when typing finishes.
            self._minimize_own_windows()
            if not _activate_window(target):
                self._notify_status(f"Could not bring CodeTantra ('{title}') to the front.")
                return False

            # Stage 1 (preferred, z-order independent): ask the editor control itself
            # to take focus via UI Automation - no mouse movement involved at all.
            if self._focus_editor_via_uia(target):
                self._notify_status(
                    f"Caret placed inside '{title}' via accessibility focus (no mouse needed)."
                )
                return True

            # Stage 2: mouse click candidates (each pixel ownership-checked)
            points = self._editor_click_candidates(target)
            if not points:
                self._notify_status(
                    f"CodeTantra window '{title}' found, but no clickable editor area could be located."
                )
                return False

            self._notify_status(f"Moving mouse into '{title}' to click the code editor...")
            last_state = None
            unknown_streak = 0
            raised = False
            try:
                for x, y in points[:4]:
                    pyautogui.moveTo(x, y, duration=0.30)
                    time.sleep(0.08)
                    # Which window would REALLY get this click? (something may float
                    # above CodeTantra at this exact pixel — check, raise, re-check)
                    owner = _window_at_point(x, y)
                    if owner != target:
                        was_topmost = _is_topmost(target)
                        _raise_above(target)
                        if not was_topmost:
                            raised = True  # only WE may undo this later
                        time.sleep(0.15)
                        owner = _window_at_point(x, y)
                        if owner != target:
                            continue  # point still owned by someone else: next candidate
                    pyautogui.click(x, y)
                    time.sleep(0.20)
                    state = self._editor_focused(near=(x, y), must_window=target)
                    if state is None and _uia_module() is not None:
                        time.sleep(0.25)  # give the UIA tree a beat to settle
                        state = self._editor_focused(near=(x, y), must_window=target)
                    last_state = state
                    if state is True:
                        self._notify_status(
                            f"Mouse clicked the Ace code editor inside '{title}' - caret ready!"
                        )
                        return True
                    if state is None:
                        unknown_streak += 1
                        if unknown_streak >= 2:
                            break  # cannot verify twice: stop clicking around the app
            finally:
                if raised:
                    _lower_back(target)

            if last_state is False:
                self._notify_status(
                    f"Clicked '{title}' but the code editor did not take focus. "
                    "Click the code box once manually, then press F8."
                )
            else:
                self._notify_status(
                    f"Clicked '{title}' (editor focus could not be verified) - typing anyway."
                )
            return True  # window is focused; typing proceeds as best effort
        except Exception as e:
            print(f"[CodeTyper] Auto mouse click error: {e}")
            return False

    def _press_char(self, char):
        """Sends keystroke through multiple redundant injection methods.

        Returns True only when a keystroke was actually handed to Windows,
        so the caller can detect blocked input instead of reporting fake success.
        """
        mode = getattr(self, "injection_mode", "HYBRID")

        if mode == "UNICODE":
            try:
                if send_unicode_char(char):
                    return True
            except Exception:
                pass

        if mode in ("SCANCODE", "HYBRID"):
            try:
                if hardware_press_char(char):
                    return True
            except Exception:
                pass

        # Redundant fallback 1: SendInput UNICODE
        try:
            if send_unicode_char(char):
                return True
        except Exception:
            pass

        # Redundant fallback 2: pynput controller
        try:
            self._keyboard.type(char)
            return True
        except Exception:
            pass

        # Redundant fallback 3: pyautogui
        try:
            pyautogui.write(char)
            return True
        except Exception:
            pass

        return False

    def _run_typing_process(self):
        """Core execution thread: countdown -> caret focus -> injection -> honest report."""
        self._notify_state(True)
        self._chars_sent = 0
        self._chars_failed = 0
        self._minimized_self = []
        lines = self._prepare_code(self.code_text)
        if not lines:
            self._notify_state(False)
            self._notify_status("Error: Code buffer is empty. Paste your code into Ultron first.")
            return

        total_lines = len(lines)
        paste_mode = getattr(self, "injection_mode", "HYBRID") == "PASTE"

        # ---------------- Countdown Phase ----------------
        cd = max(0, int(self.countdown_seconds))
        # Initial target window focus once at start if countdown > 0
        if cd > 0:
            focus_target_window()

        for remaining in range(cd, 0, -1):
            if self._stop_event.is_set():
                self._notify_state(False)
                return
            self._beep(1000, 100)
            if getattr(self, "auto_click_editor", False):
                msg = f"Starting in {remaining}s... I will move the mouse into CodeTantra and click the editor myself!"
            else:
                msg = f"Starting in {remaining}s... CLICK inside CodeTantra code editor now!"
            self._notify_status(msg)
            if self.on_progress:
                try:
                    self.on_progress(0, total_lines, 0, msg)
                except Exception:
                    pass
            time.sleep(1.0)

        if self._stop_event.is_set():
            self._notify_state(False)
            return

        # ---------------- Windows may block us completely (UIPI / elevation) ----------------
        if not is_admin() and self._target_requires_admin():
            self._notify_state(False)
            self._beep(400, 400)
            self._notify_status(
                "BLOCKED: CodeTantra runs as Administrator. Press the Admin button, restart Ultron and retry."
            )
            return

        # ---------------- Make sure the keystrokes have somewhere to land ----------------
        if getattr(self, "auto_click_editor", False):
            self._click_editor_into_place()

        if not self._secure_focus_for_typing():
            self._notify_state(False)
            self._beep(400, 400)
            self._notify_status(
                "FAILED: could not focus CodeTantra. Click inside its code box, then press F8 / INJECT."
            )
            return

        # Starting sound cue
        self._beep(1500, 150)

        # ---------------- Instant Paste Mode ----------------
        if paste_mode:
            self._notify_status(f"Pasting {total_lines} lines into CodeTantra...")
            pasted = send_paste_injection(self.code_text)
            self._restore_own_windows()
            self._notify_state(False)
            if pasted:
                self._beep(1800, 250)
                self._notify_status(f"Success: {total_lines} lines pasted into CodeTantra!")
            else:
                self._beep(400, 400)
                self._notify_status("Paste failed. Press the Admin button, restart Ultron and retry.")
            if self.on_progress:
                try:
                    self.on_progress(total_lines, total_lines, 100, f"Pasted: All {total_lines} lines injected!")
                except Exception:
                    pass
            return

        self._notify_status(f"Typing initiated ({total_lines} lines). Press F9 or ESC to stop.")
        try:
            self._typing_target = pick_target_window()
        except Exception:
            self._typing_target = None
        focus_kicks = 0

        # Speed settings
        speed = SPEED_PRESETS.get(self.speed_mode, SPEED_PRESETS["FAST"])
        base_char_delay = speed["char_delay"]
        base_line_delay = speed["line_delay"]
        jitter_range = speed["jitter"]
        idx = -1
        pct = 0

        # ---------------- Typing Phase ----------------
        for idx, line in enumerate(lines):
            if self._stop_event.is_set() or (self._chars_sent == 0 and self._chars_failed >= 5):
                break

            # Kiosk watchdog: exam apps (like CodeTantra-SEA) re-grab focus/topmost
            # state between lines. If the caret was stolen from the editor, replant
            # it instantly (UIA focus first) so the next line lands in Ace again.
            if (
                idx > 0
                and focus_kicks < 8
                and getattr(self, "_typing_target", None)
                and _uia_module() is not None
            ):
                try:
                    if self._editor_focused(must_window=self._typing_target) is not True:
                        focus_kicks += 1
                        if self._reassert_editor_focus(self._typing_target):
                            self._notify_status(
                                "Caret was stolen by the exam app - replanted inside the editor."
                            )
                except Exception:
                    pass

            # Handle newline if beyond line 0
            if idx > 0:
                if self._press_char('\n'):
                    self._chars_sent += 1
                else:
                    self._chars_failed += 1
                time.sleep(0.02)

                # Compensation for Web IDE Auto-Indentation (CodeTantra / Monaco / Ace)
                # Only apply if user explicitly enabled it AND the line being typed starts with indentation or closing brace
                if self.auto_indent_fix and (line.startswith(" ") or line.startswith("\t") or line.strip().startswith("}")):
                    time.sleep(0.015)
                    try:
                        self._keyboard.press(Key.shift)
                        self._keyboard.press(Key.home)
                        self._keyboard.release(Key.home)
                        self._keyboard.release(Key.shift)
                        time.sleep(0.005)
                        self._keyboard.press(Key.backspace)
                        self._keyboard.release(Key.backspace)
                    except Exception:
                        pass
                    time.sleep(0.01)

            # Type the characters of the current line
            for char in line:
                if self._stop_event.is_set():
                    break

                if self._press_char(char):
                    self._chars_sent += 1
                else:
                    self._chars_failed += 1
                    if self._chars_failed == 1:
                        self._beep(300, 400)
                        self._notify_status(
                            "WARNING: Windows is rejecting keystrokes. Press the Admin button and retry."
                        )
                    # Every keystroke refused = focus or integrity problem: stop pretending to type
                    if self._chars_sent == 0 and self._chars_failed >= 5:
                        break

                # Small delay between characters for reliability
                if base_char_delay > 0:
                    delay = base_char_delay
                    if jitter_range > 0:
                        delay += random.uniform(-jitter_range, jitter_range)
                    if delay > 0:
                        time.sleep(delay)

            # Progress update
            pct = int(((idx + 1) / total_lines) * 100)
            status_msg = f"Typing line {idx + 1}/{total_lines} ({pct}%)... Press F9 to stop"
            if self.on_progress:
                try:
                    self.on_progress(idx + 1, total_lines, pct, status_msg)
                except Exception:
                    pass

            # Pause between lines
            if base_line_delay > 0:
                time.sleep(base_line_delay)

        # ---------------- Finish Phase ----------------
        was_aborted = self._stop_event.is_set()
        blocked = (self._chars_sent == 0 and self._chars_failed > 0)
        self._restore_own_windows()
        self._notify_state(False)

        if was_aborted:
            self._notify_status("Typing aborted by user.")
            if self.on_progress:
                try:
                    self.on_progress(idx + 1, total_lines, pct, "Aborted.")
                except Exception:
                    pass
        elif blocked:
            self._beep(400, 400)
            self._notify_status(
                "FAILED: Windows blocked every keystroke. Press the Admin button, restart Ultron, "
                "click inside the CodeTantra code box and press F8."
            )
        else:
            # Read the CodeTantra window back to prove (or disprove) the text is on screen
            verdict = None
            try:
                verdict = self._verify_text_visible(lines)
            except Exception:
                verdict = None
            if verdict is True:
                self._beep(1800, 250)
                msg = (
                    f"VERIFIED: {self._chars_sent} characters are visible inside the "
                    f"CodeTantra editor ({total_lines} lines)!"
                )
            elif verdict is False:
                self._beep(400, 400)
                self._beep(400, 400)
                msg = (
                    "NOT TYPED: keys were delivered but your code never appeared in the "
                    "editor - the caret was in the wrong box. Keep AutoMouse ON and make "
                    "sure the question's code editor is visible, then press F8 again "
                    "(or click the code box once manually first)."
                )
            else:
                self._beep(1800, 250)
                msg = (
                    f"Done: {self._chars_sent} characters / {total_lines} lines sent "
                    "(the editor text could not be read back to verify)."
                )
            if self._chars_failed:
                msg += f" ({self._chars_failed} characters could not be delivered)"
            self._notify_status(msg)
            if self.on_progress:
                try:
                    self.on_progress(total_lines, total_lines, 100, f"Completed: All {total_lines} lines typed!")
                except Exception:
                    pass


# Global singleton instance
code_agent_typer = CodeTyper()
code_agent_typer.start_hotkeys()


# -------------------------------------------------------------
# ULTRON CODE AGENT GUI WINDOW
# -------------------------------------------------------------
# -------------------------------------------------------------
# ULTRON CODE AGENT GUI WINDOW (COMPACT & ALWAYS-ON-TOP)
# -------------------------------------------------------------
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "code_agent_pos.json")


def _load_agent_pos():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return int(data.get("x", 60)), int(data.get("y", 220))
    except Exception:
        pass
    return 60, 220


def _save_agent_pos(x, y):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"x": int(x), "y": int(y)}, f)
    except Exception:
        pass


class CodeAgentWindow:
    """Compact, Always-On-Top floating UI for CodeTantra with Mini HUD mode."""

    BG_DARK = "#0a0b10"
    BG_CARD = "#12141c"
    BG_TEXT = "#161824"
    FG_TEXT = "#f0f2f5"
    ACCENT_RED = "#ff0033"
    ACCENT_CYAN = "#00f0ff"
    ACCENT_GREEN = "#00ff66"
    MUTED_TEXT = "#8c94a6"

    DEFAULT_W = 350
    DEFAULT_H = 270
    MINI_H = 80

    def __init__(self, parent_root=None, typer=None):
        self.typer = typer or code_agent_typer
        self.parent = parent_root
        self.is_mini = False
        self.saved_w = self.DEFAULT_W
        self.saved_h = self.DEFAULT_H
        self._topmost_job = None

        if parent_root:
            self.window = tk.Toplevel(parent_root)
        else:
            self.window = tk.Tk()

        self.window.title("⚡ CODE AGENT")
        x, y = _load_agent_pos()
        self.window.geometry(f"{self.DEFAULT_W}x{self.DEFAULT_H}+{x}+{y}")
        self.window.minsize(260, 160)
        self.window.configure(bg=self.BG_DARK)

        # Always-On-Top enforcement (matches Ultron Mic Button)
        self.window.attributes("-topmost", True)
        self._keep_on_top()

        self._build_ui()
        self._bind_events()

        # Pre-load code if available from clipboard or default C sample
        sample_c = (
            "#include <stdio.h>\n"
            "#include <stdlib.h>\n\n"
            "int main() {\n"
            "    printf(\"Hello CodeTantra\\n\");\n"
            "    return 0;\n"
            "}\n"
        )
        try:
            clip = pyperclip.paste().strip()
            if clip and len(clip) >= 10 and not clip.startswith("Trajectory ID") and not clip.startswith("Error:"):
                self.text_editor.insert("1.0", clip)
            else:
                self.text_editor.insert("1.0", sample_c)
        except Exception:
            self.text_editor.insert("1.0", sample_c)

        # Connect callbacks
        self.typer.on_progress = self._on_typer_progress
        self.typer.on_status_change = self._on_typer_status
        self.typer.on_state_change = self._on_typer_state

        self._update_stats()

    def _keep_on_top(self):
        """Periodically reaffirms topmost status so it never hides behind CodeTantra."""
        try:
            if self.window.winfo_exists():
                # Pause periodic topmost resets while typing so focus stays strictly in CodeTantra
                if not self.typer.is_typing:
                    self.window.attributes("-topmost", True)
                self._topmost_job = self.window.after(2000, self._keep_on_top)
        except Exception:
            pass

    def _build_ui(self):
        # 1. Header Toolbar (Always visible in both normal and mini mode)
        self.header = tk.Frame(self.window, bg=self.BG_CARD, padx=6, pady=4)
        self.header.pack(fill="x", side="top")

        self.title_lbl = tk.Label(
            self.header,
            text="⚡ ULTRON",
            font=("Consolas", 10, "bold"),
            fg=self.ACCENT_RED,
            bg=self.BG_CARD
        )
        self.title_lbl.pack(side="left", padx=(0, 4))

        # Admin privilege indicator / 1-click elevation button
        self.is_elevated = is_admin()
        if not self.is_elevated:
            self.btn_admin = tk.Button(
                self.header, text="🛡️ Admin", font=("Segoe UI", 8, "bold"),
                bg="#e60039", fg="#ffffff", activebackground="#ff2255",
                activeforeground="#ffffff", bd=0, padx=5, pady=2, cursor="hand2",
                command=restart_as_admin
            )
            self.btn_admin.pack(side="left", padx=2)
        else:
            self.admin_lbl = tk.Label(
                self.header, text="🛡️ Admin",
                font=("Consolas", 8, "bold"), fg=self.ACCENT_GREEN, bg=self.BG_CARD
            )
            self.admin_lbl.pack(side="left", padx=2)

        self.btn_paste = tk.Button(
            self.header, text="📋 Paste", font=("Segoe UI", 8, "bold"),
            bg="#1e2230", fg=self.ACCENT_CYAN, activebackground=self.ACCENT_CYAN,
            activeforeground="#000", bd=0, padx=6, pady=2, cursor="hand2",
            command=self._paste_clipboard
        )
        self.btn_paste.pack(side="left", padx=2)

        self.btn_load = tk.Button(
            self.header, text="📁", font=("Segoe UI", 8),
            bg="#1e2230", fg="#fff", activebackground="#fff",
            activeforeground="#000", bd=0, padx=5, pady=2, cursor="hand2",
            command=self._load_file
        )
        self.btn_load.pack(side="left", padx=1)

        self.btn_clear = tk.Button(
            self.header, text="🗑", font=("Segoe UI", 8),
            bg="#1e2230", fg="#ff4d4d", activebackground="#ff4d4d",
            activeforeground="#000", bd=0, padx=5, pady=2, cursor="hand2",
            command=self._clear_text
        )
        self.btn_clear.pack(side="left", padx=1)

        # Mini Mode / Compact Toggle Button
        self.btn_toggle_mini = tk.Button(
            self.header, text="🗕 Mini", font=("Segoe UI", 8, "bold"),
            bg="#2a2d3d", fg="#ffcc00", activebackground="#ffcc00",
            activeforeground="#000", bd=0, padx=6, pady=2, cursor="hand2",
            command=self._toggle_mini_mode
        )
        self.btn_toggle_mini.pack(side="right", padx=(2, 0))

        self.stats_lbl = tk.Label(
            self.header, text="0L",
            font=("Consolas", 8), fg=self.MUTED_TEXT, bg=self.BG_CARD
        )
        self.stats_lbl.pack(side="right", padx=2)

        # 2. Code Text Area Frame (Collapsible in Mini Mode)
        self.editor_frame = tk.Frame(self.window, bg=self.BG_DARK)
        self.editor_frame.pack(fill="both", expand=True, padx=4, pady=2)

        scrollbar = tk.Scrollbar(self.editor_frame)
        scrollbar.pack(side="right", fill="y")

        self.text_editor = tk.Text(
            self.editor_frame,
            wrap="none",
            font=("Consolas", 9),
            bg=self.BG_TEXT,
            fg=self.FG_TEXT,
            insertbackground=self.ACCENT_CYAN,
            selectbackground=self.ACCENT_RED,
            selectforeground="#ffffff",
            bd=1,
            relief="solid",
            yscrollcommand=scrollbar.set,
            undo=True,
            height=6
        )
        self.text_editor.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text_editor.yview)

        # 3. Compact Options Bar (Collapsible in Mini Mode)
        self.opts_frame = tk.Frame(self.window, bg=self.BG_CARD, padx=6, pady=3)
        self.opts_frame.pack(fill="x", padx=4, pady=2)

        tk.Label(self.opts_frame, text="Mode:", font=("Segoe UI", 8, "bold"), fg=self.ACCENT_CYAN, bg=self.BG_CARD).pack(side="left", padx=(0, 2))
        self.mode_var = tk.StringVar(value=self.typer.injection_mode)
        mode_menu = ttk.Combobox(
            self.opts_frame, textvariable=self.mode_var,
            values=["UNICODE", "HYBRID", "SCANCODE", "PASTE"], width=8, font=("Segoe UI", 8), state="readonly"
        )
        mode_menu.pack(side="left", padx=(0, 6))
        mode_menu.bind("<<ComboboxSelected>>", lambda e: setattr(self.typer, "injection_mode", self.mode_var.get()))

        tk.Label(self.opts_frame, text="Speed:", font=("Segoe UI", 8, "bold"), fg="#fff", bg=self.BG_CARD).pack(side="left", padx=(0, 2))
        self.speed_var = tk.StringVar(value=self.typer.speed_mode)
        speed_menu = ttk.Combobox(
            self.opts_frame, textvariable=self.speed_var,
            values=["FAST", "TURBO", "STEALTH"], width=6, font=("Segoe UI", 8), state="readonly"
        )
        speed_menu.pack(side="left", padx=(0, 6))
        speed_menu.bind("<<ComboboxSelected>>", lambda e: setattr(self.typer, "speed_mode", self.speed_var.get()))

        self.indent_var = tk.BooleanVar(value=self.typer.auto_indent_fix)
        chk_indent = tk.Checkbutton(
            self.opts_frame, text="IndentFix",
            variable=self.indent_var, font=("Segoe UI", 8),
            fg="#fff", bg=self.BG_CARD, selectcolor=self.BG_DARK,
            activebackground=self.BG_CARD, activeforeground="#fff",
            command=lambda: setattr(self.typer, "auto_indent_fix", self.indent_var.get())
        )
        chk_indent.pack(side="left", padx=(0, 4))

        # Automatic mouse assist: moves the mouse into the CodeTantra window and clicks
        # the code editor itself so the caret is placed before typing starts
        self.click_var = tk.BooleanVar(value=self.typer.auto_click_editor)
        chk_click = tk.Checkbutton(
            self.opts_frame, text="AutoMouse",
            variable=self.click_var, font=("Segoe UI", 8),
            fg="#fff", bg=self.BG_CARD, selectcolor=self.BG_DARK,
            activebackground=self.BG_CARD, activeforeground="#fff",
            command=lambda: setattr(self.typer, "auto_click_editor", self.click_var.get())
        )
        chk_click.pack(side="left", padx=(0, 4))

        tk.Label(self.opts_frame, text="Delay:", font=("Segoe UI", 8), fg="#fff", bg=self.BG_CARD).pack(side="left", padx=(0, 1))
        self.cd_var = tk.IntVar(value=self.typer.countdown_seconds)
        cd_spin = tk.Spinbox(self.opts_frame, from_=0, to=15, textvariable=self.cd_var, width=2, font=("Segoe UI", 8))
        cd_spin.pack(side="left")

        # 4. Primary Action Row (Always visible in both modes)
        self.action_frame = tk.Frame(self.window, bg=self.BG_DARK, padx=4, pady=2)
        self.action_frame.pack(fill="x", side="bottom")

        # CLICK-TO-INJECT (NO TIMER PANIC - JUST CLICK CODETANTRA!)
        self.btn_arm = tk.Button(
            self.action_frame,
            text="🎯 CLICK-TO-TYPE (F7)",
            font=("Segoe UI", 8, "bold"),
            bg="#0077b6",
            fg="#ffffff",
            activebackground="#0096c7",
            activeforeground="#ffffff",
            bd=0,
            padx=6,
            pady=4,
            cursor="hand2",
            command=self._arm_clicked
        )
        self.btn_arm.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.btn_start = tk.Button(
            self.action_frame,
            text="🚀 INJECT (F8)",
            font=("Segoe UI", 8, "bold"),
            bg=self.ACCENT_RED,
            fg="#ffffff",
            activebackground="#ff3355",
            activeforeground="#ffffff",
            bd=0,
            padx=6,
            pady=4,
            cursor="hand2",
            command=self._start_typing_clicked
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.btn_stop = tk.Button(
            self.action_frame,
            text="🛑 F9",
            font=("Segoe UI", 8, "bold"),
            bg="#2a2d3d",
            fg="#ff4d4d",
            activebackground="#ff4d4d",
            activeforeground="#ffffff",
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._stop_clicked
        )
        self.btn_stop.pack(side="right")

        # 5. Compact Progress & Status Footer (Always visible)
        self.footer = tk.Frame(self.window, bg=self.BG_CARD, padx=6, pady=3)
        self.footer.pack(fill="x", side="bottom", padx=4, pady=(0, 2))

        self.progress_bar = ttk.Progressbar(self.footer, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 2))

        self.status_lbl = tk.Label(
            self.footer,
            text="Ready • In CodeTantra press [F8] to type",
            font=("Segoe UI", 8),
            fg=self.ACCENT_CYAN,
            bg=self.BG_CARD
        )
        self.status_lbl.pack(anchor="w")

    def _toggle_mini_mode(self):
        """Toggles between expanded code editor view and ultra-compact floating mini bar."""
        self.is_mini = not self.is_mini

        if self.is_mini:
            # Save current expanded dimensions
            self.saved_w = max(self.DEFAULT_W, self.window.winfo_width())
            self.saved_h = max(self.DEFAULT_H, self.window.winfo_height())
            
            # Hide editor and options frames
            self.editor_frame.pack_forget()
            self.opts_frame.pack_forget()
            
            # Shrink window to compact bar
            self.window.minsize(260, 75)
            self.window.geometry(f"{self.saved_w}x{self.MINI_H}")
            self.btn_toggle_mini.config(text="🗖 Expand", fg=self.ACCENT_CYAN)
        else:
            # Restore expanded layout
            self.window.minsize(260, 160)
            self.window.geometry(f"{self.saved_w}x{self.saved_h}")
            
            # Re-pack in proper order
            self.editor_frame.pack(fill="both", expand=True, padx=4, pady=2, before=self.footer)
            self.opts_frame.pack(fill="x", padx=4, pady=2, before=self.footer)
            self.btn_toggle_mini.config(text="🗕 Mini", fg="#ffcc00")

        self.window.attributes("-topmost", True)
        if not self.is_mini:
            self.window.lift()

    def _bind_events(self):
        self.text_editor.bind("<<Modified>>", self._on_text_modified)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Configure>", lambda e: self._on_window_configure(e))

    def _on_window_configure(self, event):
        # Save position when moved
        if event.widget == self.window and not self.is_mini:
            _save_agent_pos(self.window.winfo_x(), self.window.winfo_y())

    def _on_text_modified(self, event=None):
        if self.text_editor.edit_modified():
            self._update_stats()
            self.text_editor.edit_modified(False)

    def _update_stats(self):
        content = self.text_editor.get("1.0", "end-1c")
        self.typer.code_text = content
        lines = len(content.splitlines()) if content else 0
        chars = len(content)
        self.stats_lbl.config(text=f"{lines}L|{chars}C")

    def _paste_clipboard(self):
        try:
            content = pyperclip.paste()
            if content:
                self.text_editor.delete("1.0", "end")
                self.text_editor.insert("1.0", content)
                self._update_stats()
                self._on_typer_status("Code loaded from clipboard.")
            else:
                self._on_typer_status("Clipboard is empty.")
        except Exception as e:
            self._on_typer_status(f"Paste error: {e}")

    def _load_file(self):
        try:
            path = filedialog.askopenfilename(
                parent=self.window,
                title="Select Code File",
                filetypes=[
                    ("All Source Code", "*.py;*.c;*.cpp;*.java;*.js;*.html;*.css;*.txt"),
                    ("Python Files", "*.py"),
                    ("C/C++ Files", "*.c;*.cpp;*.h"),
                    ("Java Files", "*.java"),
                    ("All Files", "*.*")
                ]
            )
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self.text_editor.delete("1.0", "end")
                self.text_editor.insert("1.0", content)
                self._update_stats()
                self._on_typer_status(f"Loaded {os.path.basename(path)}")
        except Exception as e:
            self._on_typer_status(f"File error: {e}")

    def _clear_text(self):
        self.text_editor.delete("1.0", "end")
        self._update_stats()
        self._on_typer_status("Cleared.")

    def _start_typing_clicked(self):
        content = self.text_editor.get("1.0", "end-1c").strip()
        if not content or len(content) < 3:
            # Check clipboard as fallback
            try:
                clip = pyperclip.paste().strip()
                if clip and len(clip) >= 3:
                    content = clip
                    self.text_editor.delete("1.0", "end")
                    self.text_editor.insert("1.0", clip)
                    self._update_stats()
            except Exception:
                pass

        if not content or len(content) < 3:
            self._on_typer_status("Error: No code to type. Please paste code first.")
            try:
                messagebox.showwarning(
                    "No Code to Inject",
                    "The code editor is empty!\n\n"
                    "Please paste (Ctrl+V) or load your code file before clicking INJECT.",
                    parent=self.window
                )
            except Exception:
                pass
            return

        cd = self.cd_var.get()
        self.typer.countdown_seconds = cd
        self.typer.speed_mode = self.speed_var.get()
        self.typer.auto_indent_fix = self.indent_var.get()
        self.typer.injection_mode = self.mode_var.get()

        # Check if the CodeTantra window is elevated: Windows (UIPI) silently drops
        # every simulated keystroke unless Ultron runs as Administrator too.
        if not is_admin():
            target_elevated = window_is_elevated(pick_target_window())
            if target_elevated is None:
                try:
                    import psutil
                    ct_procs = [
                        p.info['name'] for p in psutil.process_iter(['name'])
                        if any(k in (p.info['name'] or '').lower() for k in ("codetantra", "sea", "exam"))
                    ]
                    target_elevated = bool(ct_procs)
                except Exception:
                    target_elevated = False
            if target_elevated:
                ans = messagebox.askyesno(
                    "Administrator Permission Required",
                    "CodeTantra is running with elevated Administrator privileges.\n\n"
                    "Windows blocks simulated typing unless Ultron also runs as Administrator.\n\n"
                    "Restart Ultron with Administrator rights now?",
                    parent=self.window
                )
                if ans:
                    restart_as_admin()
                    return

        # 1. Lock and disable the text editor so it is physically impossible to type inside Ultron
        self.text_editor.config(state="disabled")

        # 2. Collapse into ultra-compact floating Mini HUD bar so user sees countdown & progress
        if not self.is_mini:
            self._toggle_mini_mode()

        # 3. Automatically find and focus CodeTantra window if running
        target_found = focus_target_window()

        started = self.typer.start_typing(code=content, countdown=cd)
        if started:
            hint = "CodeTantra focused!" if target_found else "CLICK CodeTantra editor now!"
            self._on_typer_status(f"Starting in {cd}s... {hint}")

    def _arm_clicked(self):
        content = self.text_editor.get("1.0", "end-1c").strip()
        if not content or len(content) < 3:
            try:
                clip = pyperclip.paste().strip()
                if clip and len(clip) >= 3:
                    content = clip
                    self.text_editor.delete("1.0", "end")
                    self.text_editor.insert("1.0", clip)
                    self._update_stats()
            except Exception:
                pass

        if not content or len(content) < 3:
            self._on_typer_status("Error: No code to type. Please paste code first.")
            return

        self.typer.speed_mode = self.speed_var.get()
        self.typer.auto_indent_fix = self.indent_var.get()
        self.typer.injection_mode = self.mode_var.get()

        # 1. Lock and disable editor
        self.text_editor.config(state="disabled")

        # 2. Collapse to mini mode
        if not self.is_mini:
            self._toggle_mini_mode()

        self.btn_arm.config(text="🎯 ARMED...", bg="#ffaa00", fg="#000")
        self.typer.arm_click_to_inject(code=content)

    def _stop_clicked(self):
        if getattr(self.typer, "_armed", False):
            self.typer.disarm()
            if hasattr(self, 'btn_arm'):
                self.btn_arm.config(text="🎯 CLICK-TO-TYPE (F7)", bg="#0077b6", fg="#ffffff")
        if self.typer.is_typing:
            self.typer.abort()

    def _on_typer_progress(self, line_idx, total_lines, pct, status_str):
        def _gui_update():
            try:
                if self.window.winfo_exists():
                    self.progress_bar["value"] = pct
                    self.status_lbl.config(text=status_str)
            except Exception:
                pass
        self.window.after(0, _gui_update)

    def _on_typer_status(self, msg):
        def _gui_update():
            try:
                if self.window.winfo_exists():
                    self.status_lbl.config(text=msg)
                    if not self.typer._armed and hasattr(self, 'btn_arm') and not self.typer.is_typing:
                        self.btn_arm.config(text="🎯 CLICK-TO-TYPE (F7)", bg="#0077b6", fg="#ffffff")
            except Exception:
                pass
        self.window.after(0, _gui_update)

    def _on_typer_state(self, is_typing):
        def _gui_update():
            try:
                if self.window.winfo_exists():
                    if is_typing:
                        self.text_editor.config(state="disabled")
                        self.btn_start.config(state="disabled", bg="#555555")
                        if hasattr(self, 'btn_arm'):
                            self.btn_arm.config(state="disabled", bg="#555555")
                        self.btn_stop.config(state="normal", bg="#ff2244")
                    else:
                        self.text_editor.config(state="normal")
                        self.btn_start.config(state="normal", bg=self.ACCENT_RED)
                        if hasattr(self, 'btn_arm'):
                            self.btn_arm.config(state="normal", bg="#0077b6", text="🎯 CLICK-TO-TYPE (F7)", fg="#ffffff")
                        self.btn_stop.config(state="normal", bg="#2a2d3d")
                        try:
                            self.window.attributes("-topmost", True)
                        except Exception:
                            pass
            except Exception:
                pass
        self.window.after(0, _gui_update)

    def _on_close(self):
        _save_agent_pos(self.window.winfo_x(), self.window.winfo_y())
        self.window.withdraw()

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.text_editor.focus_set()


# Module-level opener helper
_agent_window_instance = None

def open_code_agent(parent_root=None):
    """Opens or restores the singleton Code Agent window."""
    global _agent_window_instance
    if _agent_window_instance is None or not _agent_window_instance.window.winfo_exists():
        _agent_window_instance = CodeAgentWindow(parent_root=parent_root, typer=code_agent_typer)
    _agent_window_instance.show()
    return _agent_window_instance


if __name__ == "__main__":
    app = open_code_agent()
    app.window.mainloop()

