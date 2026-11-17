"""
ULTRON // UNIFIED WINDOW COORDINATOR (MIC BUTTON & DOC AGENT)
=============================================================
Provides cross-process synchronization and hotkey coordination for:
- "ULTRON Core" (Floating Mic Button)
- "ULTRON // ALWAYS-ON-TOP DOC AGENT" (Document & Code Agent)

Features:
1. Unified Toggle (F6):
   - If either window is visible -> minimizes / hides BOTH into stealth mode.
   - If both windows are hidden -> restores / maximizes BOTH on top.
2. Cross-process debounce & state synchronization via atomic JSON state.
3. Fallback launch if one component is not yet running when maximizing.
"""

import os
import sys
import time
import json
import tempfile
import subprocess
import ctypes
from ctypes import wintypes

MIC_TITLE = "ULTRON Core"
DOC_TITLE = "ULTRON // ALWAYS-ON-TOP DOC AGENT"

SYNC_FILE = os.path.join(tempfile.gettempdir(), "ultron_sync_state.json")
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.platform == "win32":
    user32 = ctypes.windll.user32

    # Attach to interactive desktop winsta0\default
    try:
        _hwinsta = user32.OpenWindowStationW("winsta0", False, 0x10000000)
        if _hwinsta:
            user32.SetProcessWindowStation(_hwinsta)
        _hdesk = user32.OpenDesktopW("default", 0, False, 0x10000000)
        if _hdesk:
            user32.SetThreadDesktop(_hdesk)
    except Exception:
        pass

    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    HWND_TOPMOST = wintypes.HWND(-1)
    TOPMOST_FLAGS = 0x0001 | 0x0002 | 0x0010 | 0x0040  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW


def get_mic_hwnd():
    if sys.platform != "win32":
        return None
    try:
        return user32.FindWindowW(None, MIC_TITLE)
    except Exception:
        return None


def get_doc_hwnd():
    if sys.platform != "win32":
        return None
    try:
        return user32.FindWindowW(None, DOC_TITLE)
    except Exception:
        return None


def is_mic_visible():
    hwnd = get_mic_hwnd()
    if not hwnd:
        return False
    try:
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def is_doc_visible():
    hwnd = get_doc_hwnd()
    if not hwnd:
        return False
    try:
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def are_any_visible():
    return is_mic_visible() or is_doc_visible()


def read_sync_state():
    try:
        if os.path.exists(SYNC_FILE):
            with open(SYNC_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"visible": True, "timestamp": 0.0, "seq": 0}


def write_sync_state(visible: bool):
    try:
        curr = read_sync_state()
        new_data = {
            "visible": visible,
            "timestamp": time.time(),
            "seq": curr.get("seq", 0) + 1
        }
        with open(SYNC_FILE, "w", encoding="utf-8") as f:
            json.dump(new_data, f)
        return new_data
    except Exception:
        return {"visible": visible, "timestamp": time.time(), "seq": 1}


def launch_mic():
    """Starts Ultron Mic process if not running."""
    try:
        venv_pyw = os.path.join(_BASE_DIR, "venv", "Scripts", "pythonw.exe")
        venv_py = os.path.join(_BASE_DIR, "venv", "Scripts", "python.exe")
        py_exec = venv_pyw if os.path.isfile(venv_pyw) else (venv_py if os.path.isfile(venv_py) else sys.executable)
        mic_script = os.path.join(_BASE_DIR, "mic_button.py")
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([py_exec, mic_script], cwd=_BASE_DIR, creationflags=flags, close_fds=True)
    except Exception as e:
        print(f"[ultron_sync] error launching mic: {e}")


def launch_doc():
    """Starts Ultron Doc Agent process if not running."""
    try:
        venv_pyw = os.path.join(_BASE_DIR, "venv", "Scripts", "pythonw.exe")
        venv_py = os.path.join(_BASE_DIR, "venv", "Scripts", "python.exe")
        py_exec = venv_pyw if os.path.isfile(venv_pyw) else (venv_py if os.path.isfile(venv_py) else sys.executable)
        doc_script = os.path.join(_BASE_DIR, "doc_agent.py")
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([py_exec, doc_script], cwd=_BASE_DIR, creationflags=flags, close_fds=True)
    except Exception as e:
        print(f"[ultron_sync] error launching doc: {e}")


def hide_all(calling_app=None):
    """Minimizes / hides both Ultron Mic and Doc Agent."""
    write_sync_state(visible=False)

    # 1. Hide calling app first via native Tkinter
    if calling_app is not None and hasattr(calling_app, "hide"):
        try:
            calling_app.hide()
        except Exception:
            pass

    # 2. Hide other windows via Win32 SW_HIDE
    if sys.platform == "win32":
        h_mic = get_mic_hwnd()
        if h_mic:
            try:
                user32.ShowWindow(h_mic, 0)  # SW_HIDE
            except Exception:
                pass
        h_doc = get_doc_hwnd()
        if h_doc:
            try:
                user32.ShowWindow(h_doc, 0)  # SW_HIDE
            except Exception:
                pass


def show_all(calling_app=None):
    """Restores / maximizes both Ultron Mic and Doc Agent on top."""
    write_sync_state(visible=True)

    # 1. Show calling app first via native Tkinter
    if calling_app is not None and hasattr(calling_app, "show"):
        try:
            calling_app.show()
        except Exception:
            pass

    # 2. Show other windows via Win32 SW_RESTORE + HWND_TOPMOST
    if sys.platform == "win32":
        h_mic = get_mic_hwnd()
        if h_mic:
            try:
                user32.ShowWindow(h_mic, 9)  # SW_RESTORE
                user32.SetWindowPos(h_mic, HWND_TOPMOST, 0, 0, 0, 0, TOPMOST_FLAGS)
            except Exception:
                pass
        else:
            launch_mic()

        h_doc = get_doc_hwnd()
        if h_doc:
            try:
                user32.ShowWindow(h_doc, 9)  # SW_RESTORE
                user32.SetWindowPos(h_doc, HWND_TOPMOST, 0, 0, 0, 0, TOPMOST_FLAGS)
            except Exception:
                pass
        else:
            launch_doc()


def toggle_all(calling_app=None):
    """
    Unified toggle:
    - If either window is visible -> hide both.
    - If both windows are hidden -> show both.
    Includes 350ms debounce so duplicate listeners don't double-toggle.
    """
    state = read_sync_state()
    now = time.time()
    if (now - state.get("timestamp", 0.0)) < 0.35:
        # Debounce: already toggled recently, just ensure calling app matches current state
        if calling_app:
            if not state.get("visible", True) and hasattr(calling_app, "hide"):
                calling_app.hide()
            elif state.get("visible", True) and hasattr(calling_app, "show"):
                calling_app.show()
        return

    if are_any_visible():
        hide_all(calling_app)
    else:
        show_all(calling_app)
