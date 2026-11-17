"""
Quick test: types into whatever window has focus after 5 seconds.
Run this, then click into Notepad (or CodeTantra) within 5 seconds.
"""
import time
import ctypes
import ctypes.wintypes

# ── Win32 SendInput structures ──────────────────────────────
KEYEVENTF_UNICODE  = 0x0004
KEYEVENTF_KEYUP    = 0x0002
INPUT_KEYBOARD     = 1

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.wintypes.WORD),
        ("wScan",       ctypes.wintypes.WORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", INPUT_UNION),
    ]

SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype  = ctypes.c_uint

def send_unicode_char(char):
    """Send a single unicode character via SendInput UNICODE scan code."""
    code = ord(char)
    # Key down
    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.union.ki.wVk = 0
    inp_down.union.ki.wScan = code
    inp_down.union.ki.dwFlags = KEYEVENTF_UNICODE
    inp_down.union.ki.time = 0
    inp_down.union.ki.dwExtraInfo = None
    # Key up
    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.union.ki.wVk = 0
    inp_up.union.ki.wScan = code
    inp_up.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    inp_up.union.ki.time = 0
    inp_up.union.ki.dwExtraInfo = None

    arr = (INPUT * 2)(inp_down, inp_up)
    result = SendInput(2, arr, ctypes.sizeof(INPUT))
    return result

def send_vk_key(vk, scan=0):
    """Send a virtual key press (e.g. Enter = 0x0D)."""
    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.union.ki.wVk = vk
    inp_down.union.ki.wScan = scan
    inp_down.union.ki.dwFlags = 0
    inp_down.union.ki.time = 0
    inp_down.union.ki.dwExtraInfo = None

    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.union.ki.wVk = vk
    inp_up.union.ki.wScan = scan
    inp_up.union.ki.dwFlags = KEYEVENTF_KEYUP
    inp_up.union.ki.time = 0
    inp_up.union.ki.dwExtraInfo = None

    arr = (INPUT * 2)(inp_down, inp_up)
    return SendInput(2, arr, ctypes.sizeof(INPUT))

def type_string(text, char_delay=0.01):
    """Type a full string using raw Win32 SendInput UNICODE."""
    for char in text:
        if char == '\n':
            send_vk_key(0x0D)  # VK_RETURN
        elif char == '\t':
            # Type 4 spaces instead of tab
            for _ in range(4):
                send_unicode_char(' ')
                time.sleep(char_delay)
            continue
        else:
            send_unicode_char(char)
        time.sleep(char_delay)


if __name__ == "__main__":
    test_code = '#include <stdio.h>\n#include <stdlib.h>\n\nint main() {\n    printf("Hello World");\n    return 0;\n}'

    print("=" * 50)
    print("ULTRON TYPING TEST")
    print("=" * 50)
    print(f"Will type {len(test_code)} chars into the focused window.")
    print("Click into Notepad or CodeTantra NOW!")
    print()

    for i in range(5, 0, -1):
        print(f"  Starting in {i}...")
        time.sleep(1)

    print("  TYPING NOW!")
    type_string(test_code, char_delay=0.012)
    print("  DONE!")

