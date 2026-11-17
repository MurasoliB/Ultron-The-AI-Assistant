import re
import subprocess
import webbrowser
import os
import ast
import ctypes
import operator
import time
import threading
import datetime
import pickle
import shutil
from urllib.parse import quote_plus
from pathlib import Path
import psutil
import pyautogui
import pyperclip
import pygetwindow
import win32con
import win32gui
from memory import remember_name, get_name
from intent_router import Intent, classify_intent, known_website_url

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


# -------------------------------------------------------------
# DEBUG FLAG
# -------------------------------------------------------------
_DEBUG_RESOLVER = os.getenv("ULTRON_DEBUG_RESOLVER", "0") == "1"


# -------------------------------------------------------------
# CAMERA STATE
# -------------------------------------------------------------
_camera_lock = threading.Lock()
_camera_recording = False
_camera_thread = None


# -------------------------------------------------------------
# POWER STATE
# -------------------------------------------------------------
_pending_power_action = None
_pending_power_expires = 0.0
_power_lock = threading.Lock()
POWER_CONFIRM_WINDOW_SEC = 20


# -------------------------------------------------------------
# FILE-TYPE OVERRIDES
# -------------------------------------------------------------
_EXTENSION_OVERRIDES = {
    ".pdf": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
}


# -------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------
def _norm_name(s: str) -> str:
    """Treat spaces, underscores, and hyphens as identical for matching."""
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r"[\s_\-]+", " ", s)
    return s.strip()


# -------------------------------------------------------------
# BROWSER RESOLUTION
# -------------------------------------------------------------
def _find_browser_exe(candidates):
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


_BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
]
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]
_FIREFOX_PATHS = [
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
]
_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_OPERA_PATHS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Opera\opera.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Opera GX\opera.exe"),
    r"C:\Program Files\Opera\opera.exe",
]

BRAVE_EXE = _find_browser_exe(_BRAVE_PATHS)
CHROME_EXE = _find_browser_exe(_CHROME_PATHS)
FIREFOX_EXE = _find_browser_exe(_FIREFOX_PATHS)
EDGE_EXE = _find_browser_exe(_EDGE_PATHS)
OPERA_EXE = _find_browser_exe(_OPERA_PATHS)

BROWSER_EXE_MAP = {
    "brave": BRAVE_EXE, "brave browser": BRAVE_EXE,
    "chrome": CHROME_EXE, "google chrome": CHROME_EXE,
    "firefox": FIREFOX_EXE, "mozilla firefox": FIREFOX_EXE,
    "edge": EDGE_EXE, "microsoft edge": EDGE_EXE,
    "opera": OPERA_EXE, "opera gx": OPERA_EXE,
}

_BROWSER_NAMES = r"(brave(?:\s+browser)?|google\s+chrome|chrome|mozilla\s+firefox|firefox|microsoft\s+edge|edge|opera(?:\s+gx)?)"
_BROWSER_SUFFIX_RE = re.compile(
    rf"\b(?:from|in|on|using|with)\s+{_BROWSER_NAMES}\b",
    re.IGNORECASE,
)


def _open_url_in_browser(url: str, browser_name: str = None) -> bool:
    if not browser_name:
        return bool(webbrowser.open(url))

    key = re.sub(r"\s+", " ", browser_name.lower().strip())
    exe = BROWSER_EXE_MAP.get(key)

    if exe and os.path.exists(exe):
        try:
            subprocess.Popen([exe, url], shell=False)
            return True
        except Exception as e:
            print(f"[Browser launch failed for {key}]: {e}")

    app_name = {
        "brave browser": "brave",
        "google chrome": "chrome",
        "mozilla firefox": "firefox",
        "microsoft edge": "edge",
    }.get(key, key)
    executable = _resolve_app_path(app_name)
    if executable and os.path.exists(executable):
        try:
            if executable.lower().endswith(".lnk"):
                os.startfile(executable, arguments=f'"{url}"')
            else:
                subprocess.Popen([executable, url], shell=False)
            return True
        except Exception as e:
            print(f"[Browser start failed for {key}]: {e}")

    return False


NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100"
}


def normalize_command(command: str) -> str:
    cmd = re.sub(r"[^a-zA-Z0-9_%+*/().?\- ]", " ", command.lower())
    cmd = re.sub(r"\s+", " ", cmd).strip()
    for filler in [
        r"^hey\s+ultron\s*[, ]*", r"^ultron\s*[, ]*", r"^hey\s+jarvis\s*[, ]*",
        r"\bplease\b", r"\bcould you\b", r"\bcan you\b", r"\bwould you\b",
        r"\bfor me\b", r"\bkindly\b"
    ]:
        cmd = re.sub(filler, " ", cmd)
    for word, number in sorted(NUMBER_WORDS.items(), key=lambda item: -len(item[0])):
        cmd = re.sub(rf"\b{word}\b", number, cmd)
    cmd = re.sub(r"(?<=\d)\s+by\s+(?=\d)", " / ", cmd)
    for source, replacement in {
        "what is": "what's", "turn the volume up": "volume up",
        "turn the volume down": "volume down", "turn brightness up": "increase brightness",
        "turn brightness down": "decrease brightness", "launch": "open",
        "start": "open", "close down": "close", "you tube": "youtube",
        "you deal": "youtube"
    }.items():
        cmd = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", replacement, cmd)
    cmd = re.sub(r"\bplus\b", "+", cmd)
    cmd = re.sub(r"\bminus\b", "-", cmd)
    cmd = re.sub(r"\btimes\b|\bmultiplied by\b", "*", cmd)
    cmd = re.sub(r"\bdivided by\b|\bover\b", "/", cmd)
    return re.sub(r"\s+", " ", cmd).strip(" ,.!?")


def calculate_expression(expression: str):
    operations = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod, ast.USub: operator.neg,
    }

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in operations:
            return operations[type(node.op)](evaluate(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            return operations[type(node.op)](evaluate(node.left), evaluate(node.right))
        raise ValueError("Only simple arithmetic is supported")

    return evaluate(ast.parse(expression, mode="eval"))


def get_current_brightness() -> int:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"],
            capture_output=True, text=True, check=False, shell=False,
        )
        if result.returncode == 0:
            value = result.stdout.strip()
            if value:
                try:
                    return max(0, min(100, int(value)))
                except ValueError:
                    pass
    except Exception:
        pass
    return 50


def set_brightness(level: int) -> bool:
    try:
        value = max(0, min(100, int(level)))
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {value})"],
            capture_output=True, text=True, check=False, shell=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def set_bluetooth_enabled(enabled: bool = True) -> bool:
    action = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-PnpDevice -Class Bluetooth | Where-Object FriendlyName -Match 'Adapter|Radio' | {action} -Confirm:$false"],
            capture_output=True, text=True, check=False, shell=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def set_hotspot_enabled(enabled: bool = True) -> bool:
    action = "start" if enabled else "stop"
    try:
        result = subprocess.run(
            ["netsh", "wlan", action, "hostednetwork"],
            capture_output=True, text=True, check=False, shell=False,
        )
        return result.returncode == 0
    except OSError:
        return False


# -------------------------------------------------------------
# UNIVERSAL APP RESOLVER
# -------------------------------------------------------------
_APP_CACHE = {}
_APP_CACHE_LOCK = threading.Lock()
_START_MENU_INDEX = None
_START_MENU_INDEX_LOCK = threading.Lock()


def _build_start_menu_index():
    global _START_MENU_INDEX
    if _START_MENU_INDEX is not None:
        return _START_MENU_INDEX

    with _START_MENU_INDEX_LOCK:
        if _START_MENU_INDEX is not None:
            return _START_MENU_INDEX

        index = {}
        roots = [
            os.path.join(os.environ.get("APPDATA", ""),
                         r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", ""),
                         r"Microsoft\Windows\Start Menu\Programs"),
        ]
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                for fn in filenames:
                    if fn.lower().endswith(".lnk"):
                        name = os.path.splitext(fn)[0].strip().lower()
                        full = os.path.join(dirpath, fn)
                        if name and name not in index:
                            index[name] = full
        _START_MENU_INDEX = index
        print(f"[AppResolver] indexed {len(index)} Start Menu shortcuts")
        return index


def _resolve_app_path(name: str):
    if not name:
        return None

    key = re.sub(r"\s+", " ", name.strip().lower())
    aliases = {
        "vs code": "visual studio code",
        "vscode": "visual studio code",
        "code": "visual studio code",
        "chrome": "google chrome",
        "edge": "microsoft edge",
        "office word": "word",
        "office excel": "excel",
        "ms word": "word",
        "ms excel": "excel",
        "explorer": "file explorer",
    }
    key = aliases.get(key, key)

    with _APP_CACHE_LOCK:
        if key in _APP_CACHE:
            return _APP_CACHE[key]

    result = None
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    known_paths = {
        "visual studio code": [
            os.path.join(local, r"Programs\Microsoft VS Code\Code.exe"),
            os.path.join(pf, r"Microsoft VS Code\Code.exe"),
            os.path.join(pf86, r"Microsoft VS Code\Code.exe"),
        ],
        "vlc": [
            os.path.join(pf, r"VideoLAN\VLC\vlc.exe"),
            os.path.join(pf86, r"VideoLAN\VLC\vlc.exe"),
        ],
        "obs studio": [
            os.path.join(pf, r"obs-studio\bin\64bit\obs64.exe"),
            os.path.join(pf86, r"obs-studio\bin\64bit\obs64.exe"),
        ],
        "blender": [
            os.path.join(pf, r"Blender Foundation\Blender 4.2\blender.exe"),
            os.path.join(pf, r"Blender Foundation\Blender 4.1\blender.exe"),
            os.path.join(pf, r"Blender Foundation\Blender 4.0\blender.exe"),
        ],
        "steam": [
            os.path.join(pf86, r"Steam\steam.exe"),
            os.path.join(pf, r"Steam\steam.exe"),
        ],
        "discord": [os.path.join(local, r"Discord\Update.exe")],
        "spotify": [
            os.path.join(local, r"Microsoft\WindowsApps\Spotify.exe"),
            os.path.join(local, r"Spotify\Spotify.exe"),
        ],
        "firefox": [
            os.path.join(pf, r"Mozilla Firefox\firefox.exe"),
            os.path.join(pf86, r"Mozilla Firefox\firefox.exe"),
        ],
        "google chrome": [
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
        ],
        "notepad++": [
            os.path.join(pf, r"Notepad++\notepad++.exe"),
            os.path.join(pf86, r"Notepad++\notepad++.exe"),
        ],
        "winrar": [
            os.path.join(pf, r"WinRAR\WinRAR.exe"),
            os.path.join(pf86, r"WinRAR\WinRAR.exe"),
        ],
        "7zip": [
            os.path.join(pf, r"7-Zip\7zFM.exe"),
            os.path.join(pf86, r"7-Zip\7zFM.exe"),
        ],
    }

    for candidate in known_paths.get(key, []):
        if candidate and os.path.exists(candidate):
            result = candidate
            break

    if not result:
        for known_key, paths in known_paths.items():
            if key in known_key or known_key in key:
                for candidate in paths:
                    if candidate and os.path.exists(candidate):
                        result = candidate
                        break
                if result:
                    break

    if not result:
        try:
            index = _build_start_menu_index()
            if key in index:
                result = index[key]
            else:
                matches = []
                for menu_name, menu_path in index.items():
                    words = re.findall(r"[a-z0-9]+", menu_name)
                    if key in menu_name or all(w in words for w in key.split()):
                        matches.append((len(menu_name), menu_name, menu_path))
                if matches:
                    matches.sort()
                    result = matches[0][2]
        except Exception as e:
            print(f"[AppResolver] start menu error: {e}")

    if not result:
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
                    with winreg.OpenKey(hive, reg_path) as root_key:
                        n = winreg.QueryInfoKey(root_key)[0]
                        for i in range(n):
                            sub = winreg.EnumKey(root_key, i)
                            sub_clean = sub.lower()
                            if sub_clean.endswith(".exe"):
                                sub_clean = sub_clean[:-4]
                            if key == sub_clean or key in sub_clean:
                                with winreg.OpenKey(root_key, sub) as app_key:
                                    try:
                                        path, _ = winreg.QueryValueEx(app_key, "")
                                        if path and os.path.exists(path):
                                            result = path
                                            break
                                    except FileNotFoundError:
                                        pass
                    if result:
                        break
                except FileNotFoundError:
                    continue
        except Exception as e:
            print(f"[AppResolver] registry error: {e}")

    if not result:
        try:
            import shutil as _sh
            for candidate in (key, key.replace(" ", "") + ".exe",
                              key + ".exe", key.replace(" ", "")):
                found = _sh.which(candidate)
                if found:
                    result = found
                    break
        except Exception as e:
            print(f"[AppResolver] where error: {e}")

    if not result:
        exe_name = key.replace(" ", "") + ".exe"
        search_roots = [pf, pf86, os.path.join(local, r"Programs")]
        for root in search_roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                for entry in os.listdir(root):
                    d1 = os.path.join(root, entry)
                    if not os.path.isdir(d1):
                        continue
                    candidate = os.path.join(d1, exe_name)
                    if os.path.exists(candidate):
                        result = candidate
                        break
                    try:
                        for sub in os.listdir(d1):
                            d2 = os.path.join(d1, sub)
                            if os.path.isdir(d2):
                                candidate = os.path.join(d2, exe_name)
                                if os.path.exists(candidate):
                                    result = candidate
                                    break
                    except Exception:
                        continue
                    if result:
                        break
            except Exception:
                continue
            if result:
                break

    with _APP_CACHE_LOCK:
        _APP_CACHE[key] = result
    if result:
        print(f"[AppResolver] '{name}' -> {result}")
    else:
        print(f"[AppResolver] '{name}' not found")
    return result


def _find_vscode_path():
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


# -------------------------------------------------------------
# FILE OPENER
# -------------------------------------------------------------
_EDITOR_PATHS = {
    "vs code": [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
    ],
    "notepad": ["notepad.exe"],
    "notepad++": [
        r"C:\Program Files\Notepad++\notepad++.exe",
        r"C:\Program Files (x86)\Notepad++\notepad++.exe",
    ],
    "sublime": [
        r"C:\Program Files\Sublime Text 3\sublime_text.exe",
        r"C:\Program Files\Sublime Text\sublime_text.exe",
    ],
    "wordpad": ["write.exe"],
}

_EDITOR_ALIASES = {
    "vscode": "vs code",
    "code": "vs code",
    "sublime text": "sublime",
    "notepad plus plus": "notepad++",
    "npp": "notepad++",
}

_COMMON_EXTENSIONS = [
    # Code
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs",
    ".rb", ".php", ".sh", ".bat", ".ps1", ".dart", ".swift",
    ".kt", ".kts", ".lua", ".pl", ".r", ".m", ".mm", ".scala",
    # Data & config
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".xml", ".sql", ".csv",
    # Text & docs
    ".txt", ".md", ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".pptx", ".ppt", ".rtf",
    # Media
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
    ".mp4", ".mkv", ".avi", ".mov", ".webm",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a",
    # Archives & misc
    ".zip", ".rar", ".7z", ".tar", ".gz", ".log", ".lnk", ".url",
]


def _search_roots():
    roots = []
    try:
        import config as _cfg
        roots.extend(getattr(_cfg, "FILE_SEARCH_ROOTS", None) or [])
    except Exception:
        pass
    roots.extend(_available_drive_roots())
    roots.extend((os.getcwd(), os.path.dirname(os.path.abspath(__file__)), os.path.expanduser("~")))
    return _dedupe_search_roots(roots)


def _available_drive_roots():
    if os.name != "nt":
        return []
    try:
        drive_mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        drive_mask = 0
    return [
        f"{chr(ord('A') + index)}:\\"
        for index in range(26)
        if drive_mask & (1 << index)
    ]


def _dedupe_search_roots(roots):
    normalized = []
    for root in roots:
        if not root:
            continue
        candidate = os.path.abspath(os.path.expanduser(os.fspath(root)))
        if os.path.isdir(candidate) and candidate not in normalized:
            normalized.append(candidate)

    result = []
    for candidate in normalized:
        def contains(parent, child):
            try:
                return os.path.commonpath((parent, child)) == parent
            except ValueError:
                return False

        if any(contains(existing, candidate) for existing in result):
            continue
        result = [
            existing for existing in result
            if not contains(candidate, existing)
        ]
        result.append(candidate)
    return result


def _is_reparse_directory(path):
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


# -------------------------------------------------------------
# FILE INDEX
# -------------------------------------------------------------
_FILE_INDEX_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".ultron_file_index_v2.pkl"
)
_FILE_INDEX = None
_FILE_INDEX_LOCK = threading.Lock()
_FILE_INDEX_MAX_AGE_SEC = 60 * 30
_INDEX_BACKGROUND_LOCK = threading.Lock()
_INDEX_BUILDING = set()


class IndexBuildingError(RuntimeError):
    """Raised when a computer-wide filesystem index is still being built."""


INDEX_BUILDING_MESSAGE = (
    "I'm indexing accessible drives for file and folder names and paths. "
    "The scan stays local and skips paths Windows won't allow me to read. "
    "Retry after the scan finishes, or give me the full path now."
)


def _load_index_cache(cache_path):
    """Return a saved index, marking whether it needs a background refresh."""
    if not os.path.exists(cache_path):
        return None, False
    try:
        age = time.time() - os.path.getmtime(cache_path)
        with open(cache_path, "rb") as f:
            index = pickle.load(f)
    except Exception as e:
        print(f"[Index] cache load failed ({type(e).__name__}); rebuilding.")
        return None, False
    return index, age >= _FILE_INDEX_MAX_AGE_SEC


def _start_index_build(kind):
    if kind in _INDEX_BUILDING:
        return
    _INDEX_BUILDING.add(kind)
    target = _build_file_index if kind == "file" else _build_folder_index
    threading.Thread(
        target=_run_index_build,
        args=(kind, target),
        name=f"Ultron-{kind}-index",
        daemon=True,
    ).start()


def _ensure_index_ready(kind):
    global _FILE_INDEX, _FOLDER_INDEX
    index = _FILE_INDEX if kind == "file" else _FOLDER_INDEX
    if index is not None:
        return True
    with _INDEX_BACKGROUND_LOCK:
        index = _FILE_INDEX if kind == "file" else _FOLDER_INDEX
        if index is not None:
            return True
        # A saved index answers the very first request immediately. An out of
        # date one is still far better than nothing, so it is used now while a
        # refresh runs in the background.
        cache = _FILE_INDEX_CACHE if kind == "file" else _FOLDER_INDEX_CACHE
        cached, stale = _load_index_cache(cache)
        if cached is not None:
            print(f"[{'File' if kind == 'file' else 'Folder'}Index] "
                  f"loaded {len(cached)} names from cache")
            if kind == "file":
                _FILE_INDEX = cached
            else:
                _FOLDER_INDEX = cached
            if stale:
                _start_index_build(kind)
            return True
        _start_index_build(kind)
    return False


def _run_index_build(kind, build_index):
    try:
        build_index()
    except Exception as error:
        print(f"[{kind.title()}Index] background scan failed: {type(error).__name__}: {error}")
    finally:
        with _INDEX_BACKGROUND_LOCK:
            _INDEX_BUILDING.discard(kind)


def ensure_file_index_ready():
    return _ensure_index_ready("file")


def ensure_folder_index_ready():
    return _ensure_index_ready("folder")


def _build_file_index(force: bool = False) -> dict:
    global _FILE_INDEX
    if _FILE_INDEX is not None and not force:
        return _FILE_INDEX

    with _FILE_INDEX_LOCK:
        if _FILE_INDEX is not None and not force:
            return _FILE_INDEX

        if not force and os.path.exists(_FILE_INDEX_CACHE):
            try:
                age = time.time() - os.path.getmtime(_FILE_INDEX_CACHE)
                if age < _FILE_INDEX_MAX_AGE_SEC:
                    with open(_FILE_INDEX_CACHE, "rb") as f:
                        _FILE_INDEX = pickle.load(f)
                    print(f"[FileIndex] loaded cache ({len(_FILE_INDEX)} names, "
                          f"{age/3600:.1f}h old)")
                    return _FILE_INDEX
            except Exception as e:
                print(f"[FileIndex] cache load failed: {e}")

        print("[FileIndex] building index (this may take a few seconds)...")
        t0 = time.time()
        index = {}
        count = 0
        access_errors = 0

        def record_error(error):
            nonlocal access_errors
            access_errors += 1

        for root in _search_roots():
            if not root or not os.path.isdir(root):
                continue
            print(f"[FileIndex] scanning {root}")
            for dirpath, dirnames, filenames in os.walk(
                root, topdown=True, onerror=record_error
            ):
                dirnames[:] = [
                    directory for directory in dirnames
                    if not _is_reparse_directory(os.path.join(dirpath, directory))
                ]
                for fn in filenames:
                    stem = os.path.splitext(fn)[0].strip().lower()
                    if not stem:
                        continue
                    full = os.path.join(dirpath, fn)
                    index.setdefault(stem, []).append(full)
                    count += 1
                    if count % 100000 == 0:
                        print(f"[FileIndex] scanned {count} files")

        _FILE_INDEX = index
        print(
            f"[FileIndex] indexed {count} files in {time.time()-t0:.1f}s; "
            f"{access_errors} inaccessible directories skipped"
        )

        try:
            with open(_FILE_INDEX_CACHE, "wb") as f:
                pickle.dump(index, f)
        except Exception as e:
            print(f"[FileIndex] cache save failed: {e}")

        return _FILE_INDEX


# -------------------------------------------------------------
# FOLDER INDEX  —  separate index that stores FOLDERS
# This is what makes "open X folder" work for any folder on the PC,
# no matter how deeply nested.
# -------------------------------------------------------------
_FOLDER_INDEX_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".ultron_folder_index_v2.pkl"
)
_FOLDER_INDEX = None
_FOLDER_INDEX_LOCK = threading.Lock()
_FOLDER_INDEX_MAX_AGE_SEC = 60 * 30


def _folder_index_roots():
    roots = []
    try:
        import config as _cfg
        extra = getattr(_cfg, "FOLDER_SEARCH_ROOTS", None)
        if extra:
            roots.extend(extra)
    except Exception:
        pass
    roots.extend(_search_roots())
    return _dedupe_search_roots(roots)


def _build_folder_index(force: bool = False) -> dict:
    """Build {normalized_folder_name: [full_paths]}."""
    global _FOLDER_INDEX
    if _FOLDER_INDEX is not None and not force:
        return _FOLDER_INDEX

    with _FOLDER_INDEX_LOCK:
        if _FOLDER_INDEX is not None and not force:
            return _FOLDER_INDEX

        # Try cache
        if not force and os.path.exists(_FOLDER_INDEX_CACHE):
            try:
                age = time.time() - os.path.getmtime(_FOLDER_INDEX_CACHE)
                if age < _FOLDER_INDEX_MAX_AGE_SEC:
                    with open(_FOLDER_INDEX_CACHE, "rb") as f:
                        _FOLDER_INDEX = pickle.load(f)
                    print(f"[FolderIndex] loaded cache "
                          f"({len(_FOLDER_INDEX)} names, {age/3600:.1f}h old)")
                    return _FOLDER_INDEX
            except Exception as e:
                print(f"[FolderIndex] cache load failed: {e}")

        print("[FolderIndex] building folder index (this may take a while)...")
        t0 = time.time()

        index = {}
        count = 0
        access_errors = 0

        def record_error(error):
            nonlocal access_errors
            access_errors += 1

        for root in _folder_index_roots():
            if not root or not os.path.isdir(root):
                continue
            print(f"[FolderIndex] scanning {root}")
            for dirpath, dirnames, _filenames in os.walk(
                root, topdown=True, onerror=record_error
            ):
                folder_name = os.path.basename(dirpath.rstrip(os.sep))
                if folder_name:
                    key = _norm_name(folder_name)
                    if key:
                        index.setdefault(key, []).append(dirpath)
                        count += 1
                        if count % 100000 == 0:
                            print(f"[FolderIndex] scanned {count} folders")
                dirnames[:] = [
                    directory for directory in dirnames
                    if not _is_reparse_directory(os.path.join(dirpath, directory))
                ]

        _FOLDER_INDEX = index
        print(
            f"[FolderIndex] indexed {count} folders in {time.time()-t0:.1f}s; "
            f"{access_errors} inaccessible directories skipped"
        )

        try:
            with open(_FOLDER_INDEX_CACHE, "wb") as f:
                pickle.dump(index, f)
        except Exception as e:
            print(f"[FolderIndex] cache save failed: {e}")

        return _FOLDER_INDEX


def _find_folder_in_index(name: str) -> list:
    """Return paths to folders matching `name`."""
    if not name:
        return []

    key = _norm_name(name)
    if not key:
        return []

    if not ensure_folder_index_ready():
        raise IndexBuildingError("The whole-computer folder index is still building.")
    index = _FOLDER_INDEX

    # 1. Exact match
    if key in index:
        return list(index[key])

    # 2. Fuzzy — guarded against short keys
    out = []
    seen = set()
    for k, paths in index.items():
        if len(k) < 3:
            continue
        shorter = min(len(key), len(k))
        longer = max(len(key), len(k))
        if longer == 0 or shorter / longer < 0.6:
            continue
        if key in k or k in key:
            for p in paths:
                if p not in seen:
                    seen.add(p)
                    out.append(p)
    return out


def _find_in_index(name: str) -> list:
    """
    Fuzzy lookup for FILES — treats spaces/underscores/hyphens as equivalent.
    Single/two-letter keys are EXCLUDED from fuzzy matching.
    """
    if not name:
        return []

    query = _norm_name(name)
    query_no_ext = _norm_name(os.path.splitext(name)[0])

    MIN_FUZZY = 3
    MIN_RATIO = 0.6

    if not ensure_file_index_ready():
        raise IndexBuildingError("The whole-computer file index is still building.")
    index = _FILE_INDEX

    norm_index = {}
    for key, paths in index.items():
        nk = _norm_name(key)
        if not nk:
            continue
        norm_index.setdefault(nk, []).extend(paths)

    seen = set()
    out = []

    def _add(paths):
        for p in paths:
            if p not in seen:
                seen.add(p)
                out.append(p)

    if query in norm_index:
        _add(norm_index[query])
    if query_no_ext != query and query_no_ext in norm_index:
        _add(norm_index[query_no_ext])
    if out:
        if _DEBUG_RESOLVER:
            print(f"[Index] '{name}' exact matches: {len(out)}")
        return out

    if len(query) < MIN_FUZZY and len(query_no_ext) < MIN_FUZZY:
        if _DEBUG_RESOLVER:
            print(f"[Index] '{name}' too short for fuzzy, returning []")
        return []

    for nk, paths in norm_index.items():
        if len(nk) < MIN_FUZZY:
            continue
        shorter = min(len(query), len(nk))
        longer = max(len(query), len(nk))
        if longer == 0:
            continue
        if shorter / longer < MIN_RATIO:
            continue
        if query in nk or nk in query:
            _add(paths)

    if _DEBUG_RESOLVER:
        print(f"[Index] '{name}' fuzzy matches: {len(out)}")

    return out


def _pick_best_match(matches: list, original_query: str) -> str:
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    query = _norm_name(original_query)
    query_stem = _norm_name(os.path.splitext(original_query)[0])

    def score(path):
        fn = os.path.basename(path).lower()
        stem = os.path.splitext(fn)[0]
        ext = os.path.splitext(fn)[1]
        stem_norm = _norm_name(stem)

        s = 0
        if stem_norm == query_stem:
            s -= 1000
        elif stem_norm == query:
            s -= 1000
        elif stem_norm.startswith(query) or query.startswith(stem_norm):
            s -= 500

        if os.path.isdir(path):
            s -= 200

        ext_priority = {
            ".py": 0, ".js": 0, ".ts": 0, ".java": 0, ".c": 0, ".cpp": 0,
            ".cs": 0, ".go": 0, ".rs": 0, ".rb": 0, ".php": 0, ".dart": 0,
            ".txt": 1, ".md": 1, ".json": 1, ".yaml": 1, ".yml": 1,
            ".html": 1, ".css": 1, ".sql": 1, ".env": 1, ".ini": 1,
            ".pdf": 2, ".docx": 2, ".xlsx": 2, ".pptx": 2,
            ".jpg": 3, ".jpeg": 3, ".png": 3, ".gif": 3, ".mp4": 3,
            ".mp3": 3, ".zip": 4, ".rar": 4, ".7z": 4,
            ".exe": 5, ".msi": 5, ".bat": 5, ".cmd": 5,
        }
        s += ext_priority.get(ext, 6)
        s += len(path) / 1000.0
        try:
            s -= min(os.path.getmtime(path) / 1e12, 0.1)
        except Exception:
            pass
        return s

    matches.sort(key=score)
    return matches[0]


def _find_editor(name: str = None) -> str:
    if not name:
        try:
            import config as _cfg
            name = getattr(_cfg, "DEFAULT_EDITOR", None)
        except Exception:
            name = None
        if not name:
            name = "vs code"

    key = re.sub(r"\s+", " ", name.strip().lower())
    key = _EDITOR_ALIASES.get(key, key)

    paths = _EDITOR_PATHS.get(key, [])
    for p in paths:
        if not p:
            continue
        if p in ("notepad.exe", "write.exe"):
            return p
        if os.path.isabs(p) and os.path.exists(p):
            return p

    if key in ("vs code", "notepad++", "sublime"):
        try:
            found = _resolve_app_path(key) or _resolve_app_path(name)
            if found:
                return found
        except Exception:
            pass

    import shutil as _sh
    found = _sh.which(key) or _sh.which(key + ".exe")
    return found


def _guess_file_path(name: str) -> str:
    """
    Find a file or folder by name.
    Priority: folders > shortcuts > files when query has no extension.
    Uses the folder index as a fallback for deeply-nested folders.
    """
    if not name:
        return None

    name = name.strip().strip('"').strip("'")
    name = re.sub(r"\s+(file|document|doc|folder)$", "", name, flags=re.IGNORECASE).strip()

    if not name:
        return None

    if os.path.isabs(name) and os.path.exists(name):
        return name

    if os.path.exists(name):
        return os.path.abspath(name)

    target_norm = _norm_name(name)
    has_extension = "." in os.path.basename(name)

    # Scan roots, collecting folders / files / shortcuts separately
    folder_matches = []
    file_matches = []
    lnk_matches = []

    for root in _search_roots():
        if not root or not os.path.isdir(root):
            continue

        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            if os.path.isdir(candidate):
                folder_matches.append(candidate)
            else:
                file_matches.append(candidate)
            continue

        for extra_ext in (".lnk", ".url"):
            if os.path.exists(candidate + extra_ext):
                lnk_matches.append(candidate + extra_ext)

        try:
            for entry in os.listdir(root):
                entry_base = os.path.splitext(entry)[0]
                if _norm_name(entry) == target_norm or _norm_name(entry_base) == target_norm:
                    full = os.path.join(root, entry)
                    if os.path.isdir(full):
                        folder_matches.append(full)
                    elif full.lower().endswith((".lnk", ".url")):
                        lnk_matches.append(full)
                    else:
                        file_matches.append(full)
        except Exception:
            continue

    # Choose based on whether the query had an extension
    if not has_extension:
        if folder_matches:
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> folder {folder_matches[0]}")
            return folder_matches[0]
        if lnk_matches:
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> lnk {lnk_matches[0]}")
            return lnk_matches[0]
        if file_matches:
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> file {file_matches[0]}")
            return file_matches[0]
    else:
        if file_matches:
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> file {file_matches[0]}")
            return file_matches[0]
        if lnk_matches:
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> lnk {lnk_matches[0]}")
            return lnk_matches[0]
        if folder_matches:
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> folder {folder_matches[0]}")
            return folder_matches[0]

    # Extension guessing (only if query has no dot)
    if not has_extension:
        for ext in _COMMON_EXTENSIONS + [".lnk", ".url"]:
            for root in _search_roots():
                if not root or not os.path.isdir(root):
                    continue
                candidate = os.path.join(root, name + ext)
                if os.path.exists(candidate):
                    if _DEBUG_RESOLVER:
                        print(f"[Resolver] '{name}' -> ext-guess {candidate}")
                    return candidate

    # Folder index — check folders specifically (higher priority)
    if not has_extension:
        try:
            folder_matches = _find_folder_in_index(name)
        except Exception as e:
            folder_matches = []
            if _DEBUG_RESOLVER:
                print(f"[Resolver] folder index error: {e}")

        if folder_matches:
            if len(folder_matches) == 1:
                if _DEBUG_RESOLVER:
                    print(f"[Resolver] '{name}' -> folder-index {folder_matches[0]}")
                return folder_matches[0]
            # Multiple — pick the shortest path (usually the most specific)
            folder_matches.sort(key=lambda p: (len(p), p))
            if _DEBUG_RESOLVER:
                print(f"[Resolver] '{name}' -> folder-index "
                      f"(best of {len(folder_matches)}) {folder_matches[0]}")
            return folder_matches[0]

    # File index fallback
    matches = _find_in_index(name)
    if not matches:
        if _DEBUG_RESOLVER:
            print(f"[Resolver] '{name}' -> no match")
        return None

    index_folders = [m for m in matches if os.path.isdir(m)]
    index_files = [m for m in matches if not os.path.isdir(m)]

    if not has_extension and index_folders:
        best = _pick_best_match(index_folders, name)
        if _DEBUG_RESOLVER:
            print(f"[Resolver] '{name}' -> index folder {best}")
        return best
    if index_files:
        best = _pick_best_match(index_files, name)
        if _DEBUG_RESOLVER:
            print(f"[Resolver] '{name}' -> index file {best}")
        return best
    if index_folders:
        best = _pick_best_match(index_folders, name)
        if _DEBUG_RESOLVER:
            print(f"[Resolver] '{name}' -> index folder (fallback) {best}")
        return best

    if _DEBUG_RESOLVER:
        print(f"[Resolver] '{name}' -> no match")
    return None


def _open_file_in_editor(file_path: str, editor_name: str = None) -> bool:
    if not file_path or not os.path.exists(file_path):
        return False

    if not editor_name:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in _EXTENSION_OVERRIDES:
            override = _EXTENSION_OVERRIDES[ext]
            if override is not None:
                try:
                    subprocess.Popen([override, file_path], shell=False)
                    print(f"[FileOpener] override used for {ext}: {override}")
                    return True
                except Exception as e:
                    print(f"[FileOpener] override launch failed for {ext}: {e}")

    editor_exe = _find_editor(editor_name)
    try:
        if editor_exe:
            if editor_exe in ("notepad.exe", "write.exe"):
                subprocess.Popen([editor_exe, file_path], shell=False)
                return True
            if os.path.isabs(editor_exe) and os.path.exists(editor_exe):
                subprocess.Popen([editor_exe, file_path], shell=False)
                return True
            subprocess.Popen(["cmd.exe", "/c", "start", "", editor_exe, file_path],
                             shell=False)
            return True

        os.startfile(file_path)
        return True
    except Exception as e:
        print(f"[FileOpener] launch failed: {e}")
        try:
            os.startfile(file_path)
            return True
        except Exception as e2:
            print(f"[FileOpener] os.startfile failed too: {e2}")
            return False


def _clean_display_name(file_path: str) -> str:
    base = os.path.basename(file_path)
    for ext in (".lnk", ".url"):
        if base.lower().endswith(ext):
            return base[:-len(ext)]
    return base


def _open_resolved_file(file_path: str, requested_editor: str = None) -> str:
    """Open an already-resolved file/folder and return the response string."""
    if os.path.isdir(file_path) and not requested_editor:
        try:
            os.startfile(file_path)
            display_name = os.path.basename(file_path)
            try:
                parent = os.path.dirname(file_path)
                for entry in os.listdir(parent):
                    if entry.lower() == display_name.lower() and entry != display_name:
                        display_name = entry
                        break
            except Exception:
                pass
            return f"Opening folder {display_name}."
        except Exception as e:
            return f"I found the folder but couldn't open it: {e}"

    if os.path.isdir(file_path) and requested_editor:
        # Treat "file explorer" / "explorer" as Windows Explorer
        if requested_editor.lower() in (
            "file explorer", "explorer", "windows explorer",
            "my computer", "this pc",
        ):
            try:
                os.startfile(file_path)
                display_name = os.path.basename(file_path)
                try:
                    parent = os.path.dirname(file_path)
                    for entry in os.listdir(parent):
                        if entry.lower() == display_name.lower() and entry != display_name:
                            display_name = entry
                            break
                except Exception:
                    pass
                return f"Opening {display_name} in File Explorer."
            except Exception as e:
                return f"I found the folder but couldn't open it: {e}"

        editor_exe = _find_editor(requested_editor)
        if editor_exe and os.path.isabs(editor_exe) and os.path.exists(editor_exe):
            try:
                subprocess.Popen([editor_exe, file_path], shell=False)
                return f"Opening folder {os.path.basename(file_path)} in {requested_editor}."
            except Exception as e:
                print(f"[FileOpener] folder-in-editor failed: {e}")
        try:
            os.startfile(file_path)
            return f"Opening folder {os.path.basename(file_path)} (editor not available)."
        except Exception as e:
            return f"I found the folder but couldn't open it: {e}"

    if file_path.lower().endswith((".lnk", ".url")) and not requested_editor:
        try:
            os.startfile(file_path)
            return f"Opening {_clean_display_name(file_path)}."
        except Exception as e:
            return f"I found it but couldn't open it: {e}"

    ok = _open_file_in_editor(file_path, requested_editor)
    if not ok:
        return f"I found {os.path.basename(file_path)} but could not open it."

    editor_label = requested_editor or "your default app"
    return f"Opening {os.path.basename(file_path)} in {editor_label}."


def check_open_file_command(command: str):
    """
    Handles: open/edit/launch/show me <file|folder> [in <editor>] [on desktop]
    Special: "open X in file explorer" routes to Explorer explicitly.
    """
    cmd = command.strip()

    cmd_clean = re.sub(
        r"\b(?:on|in|from)\s+(?:the\s+)?(?:desktop|documents|downloads)\b",
        " ", cmd, flags=re.IGNORECASE,
    )
    cmd_clean = re.sub(r"\s+", " ", cmd_clean).strip()

    # ---------- Special: "open X in file explorer" ----------
    explorer_match = re.match(
        r"^(?:open|show(?:\s+me)?|launch)\s+(?:the\s+)?(?:folder\s+)?(.+?)\s+in\s+(?:the\s+)?(?:file\s+)?explorer\b.*$",
        cmd_clean,
        re.IGNORECASE,
    )
    if explorer_match:
        target = explorer_match.group(1).strip().strip('"').strip("'")
        target = re.sub(r"\s+(folder|directory|dir)$", "", target, flags=re.IGNORECASE).strip()
        if target:
            path = _guess_file_path(target)
            if path:
                try:
                    os.startfile(path)
                    display_name = os.path.basename(path)
                    try:
                        parent = os.path.dirname(path)
                        for entry in os.listdir(parent):
                            if entry.lower() == display_name.lower() and entry != display_name:
                                display_name = entry
                                break
                    except Exception:
                        pass
                    return f"Opening {display_name} in File Explorer."
                except Exception as e:
                    return f"I found {display_name} but could not open it: {e}"
            return f"I could not find '{target}'."

    # ---------- Normal file/folder opening ----------
    match = re.match(
        r"^(?:open|edit|launch|show(?:\s+me)?)\s+(?:the\s+)?(?:file|folder|directory|dir)?\s*(.+?)(?:\s+in\s+(.+))?$",
        cmd_clean,
        re.IGNORECASE,
    )
    if not match:
        return None

    raw_target = match.group(1).strip().strip('"').strip("'")
    requested_editor = (match.group(2) or "").strip().lower() or None

    if not raw_target:
        return None

    if _DEBUG_RESOLVER:
        print(f"[OpenFile] cmd='{cmd}' target='{raw_target}' editor={requested_editor}")

    target_lower = raw_target.lower()

    if re.fullmatch(r"(?:it|that|this)(?:\s+instead)?", target_lower):
        return None

    if target_lower in {
        "it", "this", "that", "the previous one", "previous one",
        "that folder", "this folder",
        "the project i'm working on", "the project i am working on",
        "project i'm working on", "project i am working on",
    }:
        return None

    if target_lower in APP_MAPPINGS:
        return None
    if "." not in os.path.basename(target_lower) and target_lower in {
        "youtube", "google", "github", "chatgpt", "spotify",
        "chrome", "firefox", "edge", "brave",
    }:
        return None
    if target_lower.startswith(("http://", "https://", "www.")):
        return None

    # Direct resolution
    file_path = _guess_file_path(raw_target)
    if file_path:
        resolved_stem = os.path.splitext(os.path.basename(file_path))[0]
        q = _norm_name(raw_target)
        r = _norm_name(resolved_stem)
        q_parts = set(q.split())
        r_parts = set(r.split())
        overlap = q_parts & r_parts

        ok = (q == r or q in r or r in q or bool(overlap))
        if ok:
            return _open_resolved_file(file_path, requested_editor)
        if _DEBUG_RESOLVER:
            print(f"[OpenFile] sanity check failed: query='{q}' resolved='{r}'")

    # Fallback: file index search with ambiguity detection
    matches = _find_in_index(raw_target)

    if not matches:
        base_stem = os.path.splitext(raw_target)[0]
        if base_stem and base_stem != raw_target:
            matches = _find_in_index(base_stem)

    if not matches:
        explicit_file_request = (
            bool(os.path.splitext(os.path.basename(raw_target))[1])
            or bool(re.search(r"\b(file|folder|directory|dir)\b", cmd_clean, re.I))
        )
        if explicit_file_request and cmd_clean.lower().startswith(
            ("open ", "edit ", "launch ", "show ")
        ):
            return f"I could not find '{raw_target}'. Try the full filename with extension."
        return None

    seen = set()
    unique = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique.append(m)

    if len(unique) == 1:
        return _open_resolved_file(unique[0], requested_editor)

    query_norm = _norm_name(raw_target)

    exact = [m for m in unique if os.path.basename(m).lower() == raw_target.lower()]
    if len(exact) == 1:
        return _open_resolved_file(exact[0], requested_editor)

    exact_stem = [m for m in unique
                  if _norm_name(os.path.splitext(os.path.basename(m))[0]) == query_norm]
    if len(exact_stem) == 1:
        return _open_resolved_file(exact_stem[0], requested_editor)

    exact_with_ext = [m for m in unique
                      if _norm_name(os.path.basename(m)) == query_norm]
    if len(exact_with_ext) == 1:
        return _open_resolved_file(exact_with_ext[0], requested_editor)

    folder_matches = [m for m in unique if os.path.isdir(m)]
    if len(folder_matches) == 1:
        return _open_resolved_file(folder_matches[0], requested_editor)

    short_list = unique[:5]
    lines = [f"I found {len(unique)} matches for '{raw_target}'. Which one?"]
    for i, p in enumerate(short_list, 1):
        lines.append(f"{i}. {p}")
    if len(unique) > 5:
        lines.append(f"... and {len(unique) - 5} more.")
    lines.append("Please say the full filename.")
    return " ".join(lines)


# -------------------------------------------------------------
# FILE OPERATIONS
# -------------------------------------------------------------
def check_recent_file_command(command: str):
    cmd = command.lower().strip()

    recent_triggers = [
        "open my most recent file", "open the most recent file",
        "open the latest file", "open my latest file",
        "open the newest file", "open my newest file",
        "open last modified file", "open the last modified file",
        "what is my most recent file", "what is my latest file",
        "what's my latest file", "what's my most recent file",
    ]
    if not any(t in cmd for t in recent_triggers):
        return None

    folder = None
    for fname in ("desktop", "documents", "downloads", "pictures"):
        if re.search(rf"\b(?:in|from|on)\s+(?:the\s+)?{fname}\b", cmd):
            folder = os.path.join(os.path.expanduser("~"), fname.title())
            break

    requested_editor = None
    m = re.search(r"\bin\s+(vs code|vscode|notepad\+\+|notepad|sublime)\b", cmd)
    if m:
        requested_editor = m.group(1).lower()

    roots = [folder] if (folder and os.path.isdir(folder)) else _search_roots()

    newest = None
    newest_mtime = 0
    skip_exts = {".tmp", ".log", ".cache", ".pyc", ".pdb"}

    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                full = os.path.join(root, entry)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(entry)[1].lower()
                if ext in skip_exts:
                    continue
                try:
                    mt = os.path.getmtime(full)
                except Exception:
                    continue
                if mt > newest_mtime:
                    newest_mtime = mt
                    newest = full
        except Exception:
            continue

    if not newest:
        return "I could not find any recent files."

    if cmd.startswith("what"):
        when = datetime.datetime.fromtimestamp(newest_mtime).strftime("%Y-%m-%d %H:%M")
        return f"Your most recent file is {os.path.basename(newest)}, modified {when}."

    ok = _open_file_in_editor(newest, requested_editor)
    if not ok:
        return f"Found {os.path.basename(newest)} but could not open it."
    return f"Opening {os.path.basename(newest)} (most recent)."


def check_create_file_command(command: str):
    cmd = command.strip()

    m = re.match(
        r"^(?:create|make|new)\s+(?:(?:a|new)\s+)?"
        r"(?:(?:file|script|document)\s+(?:(?:called|named)\s+)?"
        r"(.+?)(?:\s+in\s+(.+))?|"
        r"(?:called|named)\s+(.+\.\w+?)|(.+\.\w+?))$",
        cmd,
        re.IGNORECASE,
    )
    if not m:
        return None

    raw_name = next((m.group(index) for index in (1, 3, 4) if m.group(index)), "")
    raw_name = raw_name.strip().strip('"').strip("'")
    requested_editor = None

    if not raw_name:
        return None

    if any(char in raw_name for char in '<>:"/\\|?*') or raw_name in {".", ".."}:
        return "I could not create that file because the filename is invalid."

    if raw_name.lower() in {
        "a folder", "the folder", "a new folder",
        "a directory", "the directory",
    }:
        return None

    folder = m.group(2).strip().strip('"').strip("'") if m.group(2) else None
    filename = raw_name

    if folder:
        known_folders = {
            "desktop": "Desktop",
            "documents": "Documents",
            "downloads": "Downloads",
        }
        if folder.lower() in known_folders:
            folder = os.path.join(
                os.path.expanduser("~"),
                known_folders[folder.lower()],
            )
        else:
            resolved_folder = _guess_file_path(folder)
            if not resolved_folder or not os.path.isdir(resolved_folder):
                return "I could not find the requested destination folder."
            folder = resolved_folder

    for fname in ("desktop", "documents", "downloads"):
        pattern = rf"\b(?:on|in|from)\s+(?:the\s+)?{fname}\b"
        if re.search(pattern, cmd, re.IGNORECASE):
            folder = os.path.join(os.path.expanduser("~"), fname.title())
            filename = re.sub(pattern, " ", filename, flags=re.IGNORECASE).strip()
            break

    if folder is None:
        m2 = re.search(r"\bin\s+([a-zA-Z0-9_\- ]+?)(?:\s+in\s+|$)", filename)
        if m2:
            possible_folder = m2.group(1).strip()
            candidate_folder = _guess_file_path(possible_folder)
            if candidate_folder and os.path.isdir(candidate_folder):
                folder = candidate_folder
                filename = filename.replace(m2.group(0), " ").strip()

    filename = re.sub(r"\s+(file|document)$", "", filename, flags=re.IGNORECASE).strip()

    if "." not in filename:
        filename += ".py"

    if not folder:
        folder = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(folder, exist_ok=True)

    target_path = os.path.join(folder, filename)

    if os.path.exists(target_path):
        return f"A file named {filename} already exists. Opening it."

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write("")
    except Exception as e:
        return f"I could not create {filename}: {e}"

    ok = _open_file_in_editor(target_path, requested_editor)
    if not ok:
        return f"Created {filename} but could not open it in an editor."
    return f"Created {filename} and opened it."


def check_search_in_files_command(command: str):
    cmd = command.strip()

    m = re.match(
        r"^(?:search(?:\s+inside)?\s+files\s+for|find\s+in\s+files|"
        r"grep(?:\s+for)?|search\s+for\s+text)\s+(.+)$",
        cmd,
        re.IGNORECASE,
    )
    if not m:
        return None

    query = m.group(1).strip().strip('"').strip("'")
    if not query or len(query) < 2:
        return "What should I search for?"

    folder = None
    for hint in ("desktop", "documents", "downloads"):
        if re.search(rf"\bin\s+{hint}\b", cmd, re.IGNORECASE):
            folder = os.path.join(os.path.expanduser("~"), hint.title())
            query = re.sub(rf"\s+in\s+{hint}\b", " ", query, flags=re.IGNORECASE).strip()
            break

    if not folder:
        folder = os.getcwd()

    results = []
    max_results = 50
    max_file_bytes = 2 * 1024 * 1024
    text_exts = {
        ".py", ".txt", ".md", ".json", ".js", ".ts", ".html", ".css",
        ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb",
        ".php", ".sh", ".bat", ".ps1", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".log", ".xml", ".sql", ".env", ".csv",
        ".dart", ".swift", ".kt", ".lua",
    }

    try:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in {
                    "venv", ".venv", "__pycache__", ".git", "node_modules",
                    ".cache", "dist", "build", ".vscode",
                } and not d.startswith(".")
            ]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in text_exts:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(full) > max_file_bytes:
                        continue
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if query.lower() in line.lower():
                                results.append((full, lineno, line.strip()[:120]))
                                if len(results) >= max_results:
                                    break
                    if len(results) >= max_results:
                        break
                except Exception:
                    continue
            if len(results) >= max_results:
                break
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return f"No matches for '{query}' in {os.path.basename(folder)}."

    display_lines = []
    current_file = None
    for path, lineno, text in results[:25]:
        if path != current_file:
            current_file = path
            display_lines.append(f"\n{os.path.basename(path)}:")
        display_lines.append(f"  line {lineno}: {text}")

    header = f"Found {len(results)} match(es) for '{query}':"
    body = "\n".join(display_lines)
    if len(results) > 25:
        body += f"\n... and {len(results)-25} more."

    print(header)
    for path, lineno, text in results:
        print(f"  {path}:{lineno}: {text}")

    return header + body


def _resolve_destination(dst_name: str):
    special = {
        "desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
        "documents": os.path.join(os.path.expanduser("~"), "Documents"),
        "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
        "pictures": os.path.join(os.path.expanduser("~"), "Pictures"),
        "videos": os.path.join(os.path.expanduser("~"), "Videos"),
        "music": os.path.join(os.path.expanduser("~"), "Music"),
    }
    dst_clean = dst_name.lower().strip()
    if dst_clean in special:
        return special[dst_clean]
    dst = _guess_file_path(dst_name)
    if dst and not os.path.isdir(dst):
        dst = os.path.dirname(dst)
    return dst if (dst and os.path.isdir(dst)) else None


def check_move_file_command(command: str):
    cmd = command.strip()

    rename_match = re.match(
        r"^rename\s+(?:the\s+)?(?:file\s+)?(.+?)\s+to\s+(.+)$",
        cmd, re.IGNORECASE,
    )
    if rename_match:
        src_name = rename_match.group(1).strip().strip('"').strip("'")
        new_name = rename_match.group(2).strip().strip('"').strip("'")
        src = _guess_file_path(src_name)
        if not src:
            return f"I could not find {src_name}."
        if not os.path.isfile(src):
            return f"{src_name} is not a file."
        dst = os.path.join(os.path.dirname(src), new_name)
        if os.path.exists(dst):
            return f"A file named {new_name} already exists in that folder."
        try:
            os.rename(src, dst)
            return f"Renamed {os.path.basename(src)} to {os.path.basename(dst)}."
        except Exception as e:
            return f"I could not rename it: {e}"

    move_match = re.match(
        r"^move\s+(?:the\s+)?(?:file\s+)?(.+?)\s+to\s+(.+)$",
        cmd, re.IGNORECASE,
    )
    if not move_match:
        return None

    src_name = move_match.group(1).strip().strip('"').strip("'")
    dst_name = move_match.group(2).strip().strip('"').strip("'")
    if not src_name or not dst_name:
        return None

    src = _guess_file_path(src_name)
    if not src:
        return f"I could not find {src_name}."

    dst = _resolve_destination(dst_name)
    if not dst:
        return f"I could not find the destination folder '{dst_name}'."

    final_path = os.path.join(dst, os.path.basename(src))
    if os.path.exists(final_path):
        return f"A file named {os.path.basename(src)} already exists in {dst_name}."

    try:
        shutil.move(src, final_path)
        return f"Moved {os.path.basename(src)} to {os.path.basename(dst)}."
    except Exception as e:
        return f"I could not move it: {e}"


def check_copy_file_command(command: str):
    cmd = command.strip()

    copy_match = re.match(
        r"^copy\s+(?:the\s+)?(?:file\s+)?(.+?)\s+to\s+(.+)$",
        cmd, re.IGNORECASE,
    )
    if not copy_match:
        return None

    src_name = copy_match.group(1).strip().strip('"').strip("'")
    dst_name = copy_match.group(2).strip().strip('"').strip("'")
    if not src_name or not dst_name:
        return None

    src = _guess_file_path(src_name)
    if not src:
        return f"I could not find {src_name}."

    dst = _resolve_destination(dst_name)
    if not dst:
        return f"I could not find the destination folder '{dst_name}'."

    final_path = os.path.join(dst, os.path.basename(src))
    if os.path.exists(final_path):
        return f"A file named {os.path.basename(src)} already exists in {dst_name}."

    try:
        shutil.copy2(src, final_path)
        return f"Copied {os.path.basename(src)} to {os.path.basename(dst)}."
    except Exception as e:
        return f"I could not copy it: {e}"


# -------------------------------------------------------------
# GMAIL INBOX
# -------------------------------------------------------------
def check_inbox_command(command: str):
    cmd = command.lower().strip()

    # SEND EMAIL (Matches "send <msg> to <email>", "send email to <email>", "email <email> <msg>")
    email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', command)
    if email_match and any(kw in cmd for kw in ("send", "mail", "email", "write")):
        recipient = email_match.group(0).strip()
        body = ""
        to_match = re.search(r'^(?:send\s+)?(.*?)\s+to\s+' + re.escape(recipient), command, re.I)
        if to_match and to_match.group(1).strip():
            cand = to_match.group(1).strip()
            cand = re.sub(r'^(?:email|mail|message)\s+', '', cand, flags=re.I).strip()
            if cand and cand.lower() not in ('send', 'email', 'mail', 'message', 'a message', 'an email', 'an', 'a'):
                body = cand

        if not body:
            after_match = re.search(re.escape(recipient) + r'\s*(?:saying|that|with\s+(?:message|body)|:)?\s*(.*)', command, re.I)
            if after_match and after_match.group(1).strip():
                body = after_match.group(1).strip()

        if not body:
            body = "Hello from Ultron."

        from inbox import send_email, get_active_account_email
        subject = "I am Ultron - Murasoli's AI Assistant"
        from_email = get_active_account_email()
        res = send_email(recipient, subject=subject, body=body)

        return (
            f"[ULTRON GMAIL DISPATCH]\n"
            f"This is the message I am sending automatically:\n"
            f"- Recipient: {recipient}\n"
            f"- From: {from_email}\n"
            f"- Subject: {subject}\n"
            f"- Content: \"{body}\"\n\n"
            f"{res}"
        )




    # READ EMAIL CONTENT BY ACTIVE INDEX (Bare number or #number)
    try:
        from inbox import _last_listed_emails
        if _last_listed_emails and re.fullmatch(r"#?(\d+)", cmd):
            idx_val = int(re.fullmatch(r"#?(\d+)", cmd).group(1))
            if 1 <= idx_val <= len(_last_listed_emails):
                from inbox import read_email_content
                return read_email_content(str(idx_val))
    except Exception:
        pass

    # SIMPLE "EMAIL 4", "MAIL 4", "MESSAGE 4", "#4"
    m_simple = re.fullmatch(r"(?:email|mail|message)\s+#?(\d+)", cmd)
    if m_simple:
        from inbox import read_email_content
        return read_email_content(m_simple.group(1))

    # INTENT PHRASES WITH NUMBER
    # e.g., "what is the message for 4", "what is the content of 4", "what does email 4 say",
    # "read email 4", "read 4", "show 4", "open email 4", "tell me about email 4", etc.
    m_idx = re.search(
        r"(?:what(?:'s|\s+is)\s+the\s+(?:message|content|body|detail|text)\s+(?:for|of|in|about)\s+(?:email\s+|mail\s+|message\s+)?#?(\d+)|"
        r"what\s+does\s+(?:email\s+|mail\s+|message\s+)?#?(\d+)\s+say|"
        r"(?:read|show|open|view|display|check|tell\s+me\s+about)\s+(?:the\s+)?(?:email|mail|message)\s+#?(\d+)|"
        r"(?:read|show|view|display)\s+#?(\d+)|"
        r"content\s+of\s+(?:email\s+|mail\s+|message\s+)?#?(\d+)|"
        r"(?:email|mail|message)\s+#?(\d+)\s+(?:message|content|body))",
        cmd,
        re.I
    )
    if m_idx:
        num = next(g for g in m_idx.groups() if g is not None)
        from inbox import read_email_content
        return read_email_content(num)

    # SENDER PHRASES
    # e.g., "what is the message from samruka", "read email from google", "show email from sam@samruka.com"
    m_sender = re.search(
        r"(?:what(?:'s|\s+is)\s+the\s+(?:message|content|body)\s+(?:from|for|of)\s+(.+?)|"
        r"(?:read|show|open|view)\s+(?:the\s+)?(?:email|mail|message)\s+from\s+(.+?)|"
        r"what\s+does\s+(?:the\s+)?(?:email|mail|message)\s+from\s+(.+?)\s+say)$",
        cmd,
        re.I
    )
    if m_sender:
        target_sender = next(g for g in m_sender.groups() if g is not None).strip().strip('?.!').strip()
        if target_sender and target_sender.lower() not in ('today', 'yesterday') and not target_sender.isdigit():
            from inbox import read_email_content
            return read_email_content(target_sender)

    # WHICH EMAIL IS ACTIVE
    if any(phrase in cmd for phrase in [
        "which email", "what email", "current email", "active email", "who am i emailing from"
    ]):
        from inbox import get_active_account_email
        current = get_active_account_email()
        return f"Ultron is currently using {current} as the active email account."

    # SWITCH ACTIVE EMAIL
    if any(phrase in cmd for phrase in [
        "switch email", "change email", "switch account", "change account", "toggle email"
    ]):
        from inbox import switch_active_account
        new_email = switch_active_account()
        return f"Switched active email to {new_email}."

    if any(t in cmd for t in [
        "how many unread emails", "how many emails do i have",
        "how many emails", "email count", "unread email count",
    ]):
        try:
            from inbox import get_unread_count
            n = get_unread_count(hours=24)
            if n == 0:
                return "You have no unread emails in the last 24 hours."
            plural = "email" if n == 1 else "emails"
            return f"You have {n} unread {plural} in the last 24 hours."
        except Exception as e:
            return f"I could not check your inbox: {e}"

    if any(t in cmd for t in ["spam mail", "spam email", "junk mail", "junk email",
                              "any spam", "check spam", "spam folder"]):
        try:
            from inbox import get_spam_summary
            return get_spam_summary(limit=10)
        except Exception as e:
            return f"I could not check Spam: {e}"

    for category in ["promotions", "promotional", "social", "social media",
                     "updates", "forums", "primary"]:
        if f"{category} mail" in cmd or f"{category} email" in cmd or f"any {category}" in cmd:
            key = {"promotional": "promotions", "social media": "social"}.get(category, category)
            try:
                from inbox import get_category_summary
                return get_category_summary(key, limit=10)
            except Exception as e:
                return f"I could not check {category} mail: {e}"

    if any(t in cmd for t in [
        "company mails", "company emails", "which companies",
        "what companies", "companies emailed", "companies wrote",
    ]):
        try:
            from inbox import get_company_breakdown
            return get_company_breakdown(hours=24)
        except Exception as e:
            return f"I could not check company mails: {e}"

    if any(t in cmd for t in ["emails with attachments", "mail with attachment",
                              "any attachments", "attachment emails"]):
        try:
            from inbox import get_attachments_summary
            return get_attachments_summary(hours=48, limit=5)
        except Exception as e:
            return f"I could not check attachments: {e}"

    if any(t in cmd for t in ["important emails", "urgent emails", "important mail",
                              "urgent mail", "priority emails"]):
        try:
            from inbox import get_important_summary
            return get_important_summary(limit=5)
        except Exception as e:
            return f"I could not check important mail: {e}"

    if any(t in cmd for t in ["oldest unread", "oldest email", "oldest mail"]):
        try:
            from inbox import get_oldest_unread
            return get_oldest_unread()
        except Exception as e:
            return f"I could not fetch that: {e}"

    for date_word in ["today", "yesterday"]:
        if f"emails {date_word}" in cmd or f"mail {date_word}" in cmd:
            try:
                from inbox import get_emails_on_date
                return get_emails_on_date(date_word)
            except Exception as e:
                return f"I could not fetch {date_word}'s mail: {e}"

    m = re.search(r"read (?:the )?email from (.+)$", cmd)
    if m:
        sender = m.group(1).strip().strip("?.!").strip()
        if sender and len(sender) >= 2:
            try:
                from inbox import read_specific_email
                return read_specific_email(sender)
            except Exception as e:
                return f"I could not read that email: {e}"

    m = re.search(r"emails? from (.+)$", cmd)
    if m:
        sender = m.group(1).strip().strip("?.!").strip()
        if sender and len(sender) >= 2 and sender not in ("today", "yesterday"):
            try:
                from inbox import get_emails_from
                results = get_emails_from(sender, limit=5)
                if not results:
                    return f"No unread emails from {sender}."
                total = len(results)
                plural = "email" if total == 1 else "emails"
                lines = [f"You have {total} unread {plural} from {sender}."]
                for i, e in enumerate(results, 1):
                    subj = e["subject"][:70]
                    lines.append(f"{i}. {subj}")
                return " ".join(lines)
            except Exception as e:
                return f"I could not search those emails: {e}"

    m = re.search(r"emails? about (.+)$", cmd)
    if m:
        topic = m.group(1).strip().strip("?.!").strip()
        if topic:
            try:
                from inbox import get_emails_about
                results = get_emails_about(topic, limit=5)
                if not results:
                    return f"No unread emails about {topic}."
                total = len(results)
                plural = "email" if total == 1 else "emails"
                lines = [f"You have {total} unread {plural} about {topic}."]
                for i, e in enumerate(results, 1):
                    subj = e["subject"][:70]
                    lines.append(f"{i}. From {e['from']}: {subj}")
                return " ".join(lines)
            except Exception as e:
                return f"I could not search those emails: {e}"

    if any(t in cmd for t in ["read my emails", "read my messages",
                              "read out my emails", "read all my emails"]):
        try:
            from inbox import get_detailed_summary
            return get_detailed_summary(hours=72, limit=10)
        except Exception as e:
            return f"I could not read your inbox: {e}"

    triggers = [
        "what are my messages", "what are my emails", "what's in my inbox",
        "check my email", "check my emails", "check my inbox",
        "any new emails", "any new messages", "any unread emails",
        "my emails", "my inbox", "any emails",
    ]
    if any(t in cmd for t in triggers):
        try:
            from inbox import get_email_summary
            return get_email_summary(hours=24, limit=10)
        except Exception as e:
            return f"I could not check your inbox: {e}"

    return None


# -------------------------------------------------------------
# LAUNCH APP (universal)
# -------------------------------------------------------------
def launch_any_app(target: str) -> bool:
    key = target.strip().lower()
    if not key:
        return False

    if key in APP_MAPPINGS:
        cmd = APP_MAPPINGS[key]
        if cmd.startswith("http"):
            webbrowser.open(cmd)
            return True
        return launch_app(key, cmd)

    path = _resolve_app_path(key)
    if path:
        try:
            if path.lower().endswith(".lnk"):
                os.startfile(path)
            else:
                subprocess.Popen([path], shell=False)
            return True
        except Exception as e:
            print(f"[AppResolver] launch failed for '{path}': {e}")
    return False


def launch_app_with_path(app_name: str, target_path: str) -> bool:
    executable = _resolve_app_path(app_name)
    if not executable:
        return False
    try:
        if executable.lower().endswith(".lnk"):
            os.startfile(executable, arguments=f'"{target_path}"')
        else:
            subprocess.Popen([executable, target_path], shell=False)
        return True
    except OSError as error:
        print(f"[AppResolver] launch with target failed ({type(error).__name__}).")
        return False


# -------------------------------------------------------------
# CAMERA CONTROL
# -------------------------------------------------------------
def _camera_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), "Desktop", "UltronCaptures")
    os.makedirs(path, exist_ok=True)
    return path


def take_photo() -> str:
    if not CV2_AVAILABLE:
        return "Camera module is not installed. Run: pip install opencv-python"

    with _camera_lock:
        cap = None
        try:
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not cap.isOpened():
                return "I could not access the webcam. It may be in use by another app."

            for _ in range(5):
                cap.read()
                time.sleep(0.05)

            ok, frame = cap.read()
            if not ok or frame is None:
                return "I could not capture an image from the webcam."

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(_camera_dir(), f"ultron_photo_{timestamp}.jpg")
            cv2.imwrite(filepath, frame)
            return f"Photo captured and saved to {filepath}."
        except Exception as error:
            return f"Camera error: {error}"
        finally:
            if cap is not None:
                cap.release()


def _record_loop(seconds: int = 0):
    global _camera_recording
    cap = None
    writer = None
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            _camera_recording = False
            return

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
        fps = 20.0

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(_camera_dir(), f"ultron_video_{timestamp}.avi")
        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))

        start = time.time()
        while _camera_recording:
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(frame)
            if seconds and (time.time() - start) >= seconds:
                break

        _camera_recording = False
    except Exception as error:
        print(f"[Camera record error]: {error}")
        _camera_recording = False
    finally:
        if writer is not None:
            writer.release()
        if cap is not None:
            cap.release()


def start_recording(seconds: int = 0) -> str:
    global _camera_recording, _camera_thread
    if not CV2_AVAILABLE:
        return "Camera module is not installed. Run: pip install opencv-python"
    if _camera_recording:
        return "I am already recording."

    _camera_recording = True
    _camera_thread = threading.Thread(target=_record_loop, args=(seconds,), daemon=True)
    _camera_thread.start()

    if seconds:
        return f"Recording video for {seconds} seconds."
    return "Recording video. Say 'stop recording' when you are done."


def stop_recording() -> str:
    global _camera_recording
    if not _camera_recording:
        return "I am not recording anything right now."
    _camera_recording = False
    return "Stopping camera recording."


def camera_is_recording() -> bool:
    return _camera_recording


def show_camera_preview(seconds: int = 15):
    if not CV2_AVAILABLE:
        return "Camera module is not installed. Run: pip install opencv-python"

    def loop():
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return
        start = time.time()
        window_name = "ULTRON // CAMERA FEED"
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                cv2.putText(frame, "ULTRON CAM", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.imshow(window_name, frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                if seconds and (time.time() - start) >= seconds:
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()

    threading.Thread(target=loop, daemon=True).start()
    return f"Camera feed opened for {seconds} seconds. Press Q in the window to close early."


def check_camera_command(command: str):
    cmd = command.lower().strip()

    if any(phrase in cmd for phrase in [
        "camera preview", "show camera", "open camera preview",
        "camera feed", "show me camera", "see camera",
    ]):
        match = re.search(r"(\d+)\s*(?:second|sec|s)\b", cmd)
        seconds = int(match.group(1)) if match else 15
        return show_camera_preview(seconds)

    if any(phrase in cmd for phrase in [
        "take a photo", "take photo", "take a picture", "take picture",
        "click a photo", "click a picture", "capture photo", "capture image",
        "take selfie", "take a selfie", "snap a photo", "snap a picture",
        "take an image", "capture a photo",
    ]):
        return take_photo()

    if any(phrase in cmd for phrase in [
        "stop recording", "stop video", "stop camera recording",
        "end recording", "finish recording", "stop the recording",
    ]):
        return stop_recording()

    if any(phrase in cmd for phrase in [
        "record video", "record a video", "start recording",
        "record my video", "start video recording", "record the video",
    ]):
        match = re.search(r"(\d+)\s*(?:second|sec|s)\b", cmd)
        seconds = int(match.group(1)) if match else 0
        return start_recording(seconds)

    if any(phrase in cmd for phrase in [
        "is the camera recording", "are you recording", "camera status",
        "am i recording",
    ]):
        return "Yes, the camera is recording." if camera_is_recording() else "No, the camera is not recording."

    return None


# -------------------------------------------------------------
# POWER CONTROL
# -------------------------------------------------------------
def _schedule_power_action(action: str, delay: int = 10) -> bool:
    try:
        if action == "shutdown":
            args = ["shutdown", "/s", "/t", str(delay)]
            if delay > 0:
                args += ["/c", "ULTRON: system shutdown initiated."]
        elif action == "restart":
            args = ["shutdown", "/r", "/t", str(delay)]
            if delay > 0:
                args += ["/c", "ULTRON: system restart initiated."]
        elif action == "logoff":
            args = ["shutdown", "/l"]
        elif action == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], shell=False)
            return True
        elif action == "hibernate":
            subprocess.Popen(["shutdown", "/h"], shell=False)
            return True
        else:
            return False

        result = subprocess.run(args, capture_output=True, text=True, check=False, shell=False)
        return result.returncode == 0
    except Exception as error:
        print(f"[Power Error]: {error}")
        return False


def _abort_power_action() -> bool:
    try:
        result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True,
                                check=False, shell=False)
        return result.returncode == 0
    except Exception:
        return False


def _confirm_power_action(action: str) -> str:
    global _pending_power_action, _pending_power_expires
    with _power_lock:
        _pending_power_action = action
        _pending_power_expires = time.time() + POWER_CONFIRM_WINDOW_SEC
    return (
        f"This will {action} your Windows session. "
        f"Say 'confirm {action}' within {POWER_CONFIRM_WINDOW_SEC} seconds to proceed."
    )


def _execute_confirmed_power_action(action: str) -> str:
    if action in ("shutdown", "restart"):
        if _schedule_power_action(action, delay=10):
            return (
                f"{'Shutting down' if action == 'shutdown' else 'Restarting'} "
                "in 10 seconds. Say 'cancel shutdown' to abort."
            )
    elif _schedule_power_action(action):
        return {
            "logoff": "Signing out now.",
            "sleep": "Putting the system to sleep.",
            "hibernate": "Hibernating now.",
        }.get(action, f"Confirmed: {action}.")
    return f"I could not {action} the system."


def _is_power_confirmation(command: str, action: str) -> bool:
    normalized = re.sub(r"[.!?]+$", "", command.strip().lower())
    return normalized in {
        "yes",
        "yes please",
        "yeah",
        "yeah please",
        "yep",
        "confirm",
        f"confirm {action}",
        "do it",
        "go ahead",
        "proceed",
        "affirmative",
    }


def _is_power_cancellation(command: str) -> bool:
    normalized = re.sub(r"[.!?]+$", "", command.strip().lower())
    return normalized in {"no", "nope", "cancel", "stop", "don't", "do not", "abort"}


def check_power_command(command: str):
    global _pending_power_action, _pending_power_expires
    cmd = command.lower().strip()

    if any(phrase in cmd for phrase in [
        "cancel shutdown", "cancel restart", "cancel reboot",
        "abort shutdown", "abort restart", "abort the shutdown",
        "stop shutdown", "stop restart",
    ]):
        aborted = _abort_power_action()
        with _power_lock:
            _pending_power_action = None
            _pending_power_expires = 0.0
        return "Shutdown cancelled." if aborted else "There was no shutdown to cancel."

    with _power_lock:
        pending = _pending_power_action
        expires = _pending_power_expires

    if pending:
        if time.time() > expires:
            with _power_lock:
                _pending_power_action = None
                _pending_power_expires = 0.0
            if _is_power_confirmation(cmd, pending):
                return "That confirmation expired. Please ask again."
        else:
            if _is_power_confirmation(cmd, pending):
                with _power_lock:
                    _pending_power_action = None
                    _pending_power_expires = 0.0
                return _execute_confirmed_power_action(pending)
            if _is_power_cancellation(cmd):
                with _power_lock:
                    _pending_power_action = None
                    _pending_power_expires = 0.0
                return "Cancelled. Nothing will happen."

    wants_shutdown = any(phrase in cmd for phrase in [
        "shut down", "shutdown", "power off", "turn off the computer",
        "turn off the pc", "power down",
    ]) or cmd in ("shut down", "shutdown", "power off", "power down")

    wants_restart = any(phrase in cmd for phrase in [
        "restart the computer", "restart the pc", "restart computer",
        "restart pc", "reboot", "restart system", "restart windows",
        "restart the system", "restart the laptop", "reboot the computer",
        "reboot the pc", "reboot the system",
    ]) or cmd in ("restart", "reboot", "restart the computer", "reboot the computer",
                  "restart computer", "reboot the pc", "restart the pc")

    wants_logoff = any(phrase in cmd for phrase in [
        "log off", "logoff", "sign out", "signout",
    ]) or cmd in ("log off", "logoff", "sign out")

    if wants_shutdown:
        return _confirm_power_action("shutdown")

    if wants_restart:
        return _confirm_power_action("restart")

    if wants_logoff:
        return _confirm_power_action("logoff")

    if "hibernate" in cmd:
        return _confirm_power_action("hibernate")

    if cmd in ("sleep", "go to sleep") or "go to sleep" in cmd:
        return _confirm_power_action("sleep")

    return None


try:
    from pycaw.pycaw import AudioUtilities
    PYCAW_AVAILABLE = True
except Exception:
    PYCAW_AVAILABLE = False


# -------------------------------------------------------------
# 1. NAME & IDENTITY
# -------------------------------------------------------------
def check_name_command(command: str):
    patterns = [r"my name is (.+)", r"i am (.+)", r"call me (.+)"]
    for pattern in patterns:
        match = re.search(pattern, command, re.IGNORECASE)
        if match:
            name = match.group(1).strip().rstrip(".!?")
            remember_name(name)
            return f"Understood. I will address you as {name}."
    return None


def check_memory_command(command: str):
    cmd = command.lower().strip()
    if cmd in ["what is my name", "what's my name", "do you know my name", "who am i"]:
        name = get_name()
        if name:
            return f"You are {name}."
        return "I do not know your identity yet."
    return None


# -------------------------------------------------------------
# 2. AUDIO & VOLUME CONTROL
# -------------------------------------------------------------
def set_system_volume(level: int):
    if not PYCAW_AVAILABLE:
        return False
    try:
        speakers = AudioUtilities.GetSpeakers()
        vol = speakers.EndpointVolume
        scalar = max(0.0, min(1.0, level / 100.0))
        vol.SetMasterVolumeLevelScalar(scalar, None)
        return True
    except Exception as e:
        print("Volume control error:", e)
        return False


def mute_system_volume(mute: bool = True):
    if not PYCAW_AVAILABLE:
        return False
    try:
        speakers = AudioUtilities.GetSpeakers()
        vol = speakers.EndpointVolume
        vol.SetMute(1 if mute else 0, None)
        return True
    except Exception as e:
        print("Mute error:", e)
        return False


def check_volume_command(command: str):
    cmd = command.lower().strip()

    if re.search(r"\bunmute\b", cmd):
        if not mute_system_volume(False):
            return "Windows could not unmute the system audio."
        return "System audio unmuted."

    if re.search(r"\bmute\b", cmd):
        if not mute_system_volume(True):
            return "Windows could not mute the system audio."
        return "System audio muted."

    vol_match = re.search(r"(?:set|change)?\s*volume to (\d+)", cmd)
    if vol_match:
        pct = int(vol_match.group(1))
        set_system_volume(pct)
        return f"Volume set to {pct} percent."

    if re.search(r"\b(?:increase|raise)\s+(?:the\s+)?(?:volume|sound)\b", cmd) or re.search(
        r"\b(?:volume|sound)\s+up\b", cmd
    ):
        for _ in range(5):
            pyautogui.press("volumeup")
        return "Increasing volume."

    if re.search(r"\b(?:decrease|lower)\s+(?:the\s+)?(?:volume|sound)\b", cmd) or re.search(
        r"\b(?:volume|sound)\s+down\b", cmd
    ):
        for _ in range(5):
            pyautogui.press("volumedown")
        return "Decreasing volume."

    return None


# -------------------------------------------------------------
# 3. YOUTUBE, BROWSER & WEB COMMANDS
# -------------------------------------------------------------
def check_web_command(command: str):
    cmd = command.lower().strip()

    requested_browser = None
    match_browser = _BROWSER_SUFFIX_RE.search(cmd)
    if match_browser:
        requested_browser = match_browser.group(1).strip()
        cmd = _BROWSER_SUFFIX_RE.sub(" ", cmd, count=1)
        cmd = re.sub(r"\s+", " ", cmd).strip()

    if "play " in cmd or "youtube" in cmd:
        match = re.search(r"(?:open\s+.*?(?:and\s+)?)?play\s+(.+?)(?:\s+(?:on|in)\s+youtube)?$", cmd)
        if not match:
            match = re.search(r"(?:search\s+(?:for\s+)?|find\s+)?(.+?)\s+(?:on|in)\s+youtube", cmd)

        if match:
            song = match.group(1).strip()
            song = re.sub(r"^(youtube\s+(?:and\s+)?|chrome\s+)", "", song).strip()
            song = re.sub(r"\b(song|video|track|music)\b", "", song).strip()
            if not song:
                song = match.group(1).strip()

            if requested_browser:
                url = f"https://www.youtube.com/results?search_query={quote_plus(song)}"
                ok = _open_url_in_browser(url, requested_browser)
                if ok:
                    return f"Opening {song} on YouTube in {requested_browser}."
                return f"I couldn't open YouTube in {requested_browser}."

            try:
                import pywhatkit
                pywhatkit.playonyt(song)
                return f"Playing {song} on YouTube."
            except Exception:
                url = f"https://www.youtube.com/results?search_query={quote_plus(song)}"
                if _open_url_in_browser(url, None):
                    return f"Opening {song} on YouTube."
                return "I couldn't open YouTube."

        if cmd in ["open youtube", "launch youtube", "go to youtube", "youtube"]:
            ok = _open_url_in_browser("https://www.youtube.com", requested_browser)
            if requested_browser and ok:
                return f"Opening YouTube in {requested_browser}."
            if requested_browser:
                return f"I couldn't open YouTube in {requested_browser}."
            if not ok:
                return "I couldn't open YouTube."
            return "Opening YouTube in your browser."

    if cmd.startswith("search for ") or cmd.startswith("search "):
        query = cmd.replace("search for ", "").replace("search ", "").strip()
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        ok = _open_url_in_browser(url, requested_browser)
        if requested_browser and ok:
            return f"Searching Google for {query} in {requested_browser}."
        if requested_browser:
            return f"I couldn't search in {requested_browser}: that browser isn't available."
        if not ok:
            return "I couldn't open the web search."
        return f"Searching Google for {query}."

    if cmd.startswith("open website ") or cmd.startswith("open http"):
        site = cmd.replace("open website ", "").strip()
        if re.fullmatch(
            r"(?:this|the\s+)?website\s+(?:i['’]?m|i\s+am)\s+"
            r"(?:currently\s+)?working\s+with",
            site,
            re.I,
        ):
            return None
        url = site if site.startswith("http") else f"https://{site}"
        ok = _open_url_in_browser(url, requested_browser)
        if requested_browser and ok:
            return f"Opening {site} in {requested_browser}."
        if requested_browser:
            return f"I couldn't open {site} in {requested_browser}."
        if not ok:
            return f"I couldn't open {site}."
        return f"Opening {site}."

    if cmd.startswith("open "):
        target = cmd.removeprefix("open ").strip()
        known_url = known_website_url(target)
        if known_url:
            if _open_url_in_browser(known_url, requested_browser):
                return (
                    f"Opening {target} in {requested_browser}."
                    if requested_browser else f"Opening {target}."
                )
            if requested_browser:
                return f"I couldn't open {target}: {requested_browser} isn't available."

    if cmd.startswith("open ") and requested_browser:
        target = cmd.replace("open ", "").strip()
        known = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "github": "https://www.github.com",
            "stackoverflow": "https://www.stackoverflow.com",
            "gmail": "https://mail.google.com",
            "maps": "https://maps.google.com",
            "chatgpt": "https://chatgpt.com",
            "reddit": "https://www.reddit.com",
            "twitter": "https://www.twitter.com",
            "x": "https://www.x.com",
            "wikipedia": "https://www.wikipedia.org",
            "amazon": "https://www.amazon.in",
            "flipkart": "https://www.flipkart.com",
        }
        if target in known:
            ok = _open_url_in_browser(known[target], requested_browser)
            if ok:
                return f"Opening {target} in {requested_browser}."
            return f"I couldn't open {target}: {requested_browser} isn't available."

    return None


# -------------------------------------------------------------
# 4. APP LAUNCHING & CLOSING
# -------------------------------------------------------------
OFFICE_ROOT = os.path.join(
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    "Microsoft Office",
    "root",
    "Office16",
)
WORD_EXE = os.path.join(OFFICE_ROOT, "WINWORD.EXE")
EXCEL_EXE = os.path.join(OFFICE_ROOT, "EXCEL.EXE")
POWERPOINT_EXE = os.path.join(OFFICE_ROOT, "POWERPNT.EXE")
OUTLOOK_EXE = os.path.join(OFFICE_ROOT, "OUTLOOK.EXE")
ONENOTE_EXE = os.path.join(OFFICE_ROOT, "ONENOTE.EXE")
ACCESS_EXE = os.path.join(OFFICE_ROOT, "MSACCESS.EXE")
PUBLISHER_EXE = os.path.join(OFFICE_ROOT, "MSPUB.EXE")
LYNC_EXE = os.path.join(OFFICE_ROOT, "lync.exe")


def _browser_launch(name):
    exe_map = {
        "brave": BRAVE_EXE, "brave browser": BRAVE_EXE,
        "chrome": CHROME_EXE, "google chrome": CHROME_EXE,
        "firefox": FIREFOX_EXE, "mozilla firefox": FIREFOX_EXE,
        "edge": EDGE_EXE, "microsoft edge": EDGE_EXE,
        "opera": OPERA_EXE, "opera gx": OPERA_EXE,
    }
    exe = exe_map.get(name)
    if exe and os.path.exists(exe):
        return exe
    fallback = {
        "brave": "start brave", "brave browser": "start brave",
        "chrome": "start chrome", "google chrome": "start chrome",
        "firefox": "start firefox", "mozilla firefox": "start firefox",
        "edge": "start msedge", "microsoft edge": "start msedge",
        "opera": "start opera", "opera gx": "start operagx",
    }
    return fallback.get(name, f"start {name}")


_VSCODE_PATH = _find_vscode_path()

APP_MAPPINGS = {
    "chrome": _browser_launch("chrome"),
    "google chrome": _browser_launch("chrome"),
    "brave": _browser_launch("brave"),
    "brave browser": _browser_launch("brave"),
    "firefox": _browser_launch("firefox"),
    "mozilla firefox": _browser_launch("firefox"),
    "edge": _browser_launch("edge"),
    "microsoft edge": _browser_launch("edge"),
    "opera": _browser_launch("opera"),
    "opera gx": _browser_launch("opera"),
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "settings": "start ms-settings:",
    "command prompt": "start cmd",
    "terminal": "start wt",
    "code": _VSCODE_PATH if _VSCODE_PATH else "code",
    "vs code": _VSCODE_PATH if _VSCODE_PATH else "code",
    "vscode": _VSCODE_PATH if _VSCODE_PATH else "code",
    "visual studio code": _VSCODE_PATH if _VSCODE_PATH else "code",
    "spotify": "start spotify:",
    "copilot": "shell:AppsFolder\\Microsoft.Copilot_8wekyb3d8bbwe!App",
    "microsoft copilot": "shell:AppsFolder\\Microsoft.Copilot_8wekyb3d8bbwe!App",
    "microsoft 365 copilot": "shell:AppsFolder\\Microsoft.MicrosoftOfficeHub_8wekyb3d8bbwe!Microsoft.MicrosoftOfficeHub",
    "microsoft store": "ms-windows-store:",
    "store": "ms-windows-store:",
    "whatsapp": "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "whatsapp desktop": "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "whats app": "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "youtube": "https://www.youtube.com",
    "camera": "start microsoft.windows.camera:",
    "webcam": "start microsoft.windows.camera:",
    "windows camera": "start microsoft.windows.camera:",
    "word": WORD_EXE if os.path.exists(WORD_EXE) else "winword.exe",
    "microsoft word": WORD_EXE if os.path.exists(WORD_EXE) else "winword.exe",
    "word document": WORD_EXE if os.path.exists(WORD_EXE) else "winword.exe",
    "ms word": WORD_EXE if os.path.exists(WORD_EXE) else "winword.exe",
    "excel": EXCEL_EXE if os.path.exists(EXCEL_EXE) else "excel.exe",
    "microsoft excel": EXCEL_EXE if os.path.exists(EXCEL_EXE) else "excel.exe",
    "powerpoint": POWERPOINT_EXE if os.path.exists(POWERPOINT_EXE) else "powerpnt.exe",
    "microsoft powerpoint": POWERPOINT_EXE if os.path.exists(POWERPOINT_EXE) else "powerpnt.exe",
    "outlook": OUTLOOK_EXE if os.path.exists(OUTLOOK_EXE) else "outlook.exe",
    "microsoft outlook": OUTLOOK_EXE if os.path.exists(OUTLOOK_EXE) else "outlook.exe",
    "onenote": ONENOTE_EXE if os.path.exists(ONENOTE_EXE) else "onenote.exe",
    "one note": ONENOTE_EXE if os.path.exists(ONENOTE_EXE) else "onenote.exe",
    "access": ACCESS_EXE if os.path.exists(ACCESS_EXE) else "msaccess.exe",
    "microsoft access": ACCESS_EXE if os.path.exists(ACCESS_EXE) else "msaccess.exe",
    "publisher": PUBLISHER_EXE if os.path.exists(PUBLISHER_EXE) else "mspub.exe",
    "microsoft publisher": PUBLISHER_EXE if os.path.exists(PUBLISHER_EXE) else "mspub.exe",
    "lync": LYNC_EXE if os.path.exists(LYNC_EXE) else "lync.exe",
    "skype for business": LYNC_EXE if os.path.exists(LYNC_EXE) else "lync.exe",
}

PROCESS_NAMES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "brave": "brave.exe",
    "brave browser": "brave.exe",
    "firefox": "firefox.exe",
    "mozilla firefox": "firefox.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "opera": "opera.exe",
    "opera gx": "opera.exe",
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "calc": "CalculatorApp.exe",
    "code": "Code.exe",
    "vs code": "Code.exe",
    "vscode": "Code.exe",
    "visual studio code": "Code.exe",
    "spotify": "Spotify.exe",
    "task manager": "Taskmgr.exe",
    "camera": "WindowsCamera.exe",
    "webcam": "WindowsCamera.exe",
    "word": "WINWORD.EXE",
    "microsoft word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "outlook": "OUTLOOK.EXE",
    "onenote": "ONENOTE.EXE",
    "one note": "ONENOTE.EXE",
    "access": "MSACCESS.EXE",
    "publisher": "MSPUB.EXE",
    "lync": "lync.exe",
    "skype for business": "lync.exe",
}


def close_app_by_name(app_name: str) -> bool:
    key = app_name.strip().lower()
    target = PROCESS_NAMES.get(key, None)
    if target is None:
        guesses = set()
        words = key.split()
        if words:
            guesses.add(words[0] + ".exe")
            guesses.add("".join(words) + ".exe")
            guesses.add(words[-1] + ".exe")
        target_names = guesses
    else:
        if not target.endswith(".exe"):
            target += ".exe"
        target_names = {target}

    killed = False
    for proc in psutil.process_iter(['name']):
        try:
            pname = (proc.info['name'] or "").lower()
            if pname in target_names:
                proc.kill()
                killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


def launch_app(target: str, launch_command: str) -> bool:
    try:
        if os.path.isabs(launch_command) and os.path.exists(launch_command):
            subprocess.Popen([launch_command], shell=False)
            return True
        if launch_command.startswith(("ms-", "shell:")):
            subprocess.Popen(["explorer.exe", launch_command], shell=False)
        elif launch_command.startswith("start "):
            subprocess.Popen(["cmd.exe", "/c", "start", "", launch_command[6:]], shell=False)
        else:
            subprocess.Popen([launch_command], shell=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def enter_calculator_expression(expression: str) -> bool:
    try:
        windows = pygetwindow.getWindowsWithTitle("Calculator")
        if not windows:
            return False
        window = windows[-1]
        if window.isMinimized:
            window.restore()
        hwnd = getattr(window, "_hWnd", None)
        if hwnd:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        window.activate()
        time.sleep(0.3)
        display_x = window.left + max(160, window.width // 2)
        display_y = window.top + min(170, max(110, window.height // 4))
        pyautogui.click(display_x, display_y)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        pyautogui.write(expression, interval=0.08)
        pyautogui.press("enter")
        time.sleep(0.2)
        pyperclip.copy(expression)
        pyautogui.click(display_x, display_y)
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        return True
    except Exception as error:
        print(f"[Calculator Error]: {error}")
        return False


def check_app_command(command: str):
    cmd = command.lower().strip()

    direct_nested_match = re.match(r"create\s+(?:a\s+)?(.+?)\s+inside\s+(.+)$", cmd)
    if direct_nested_match:
        child_name = direct_nested_match.group(1).strip().strip('"\'')
        parent_name = direct_nested_match.group(2).strip().strip('"\'')
        if child_name.startswith("folder "):
            child_name = child_name[7:].strip()
        if (not child_name or not parent_name or
                any(char in child_name for char in '<>:"/\\|?*') or
                any(char in parent_name for char in '<>:"|?*')):
            return "I could not create that folder because the name is invalid."
        folder_name = str(Path(parent_name) / child_name)
        try:
            folder_path = Path.cwd() / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)
            return f"Folder created at {folder_path}."
        except OSError as error:
            return f"I could not create the folder: {error}."

    folder_match = re.match(r"create\s+(?:a\s+)?folder(?:\s+(?:called|named))?\s+(.+)$", cmd)
    if folder_match:
        folder_name = folder_match.group(1).strip().strip('"\'')
        nested_match = re.match(r"(.+?)\s+inside\s+(.+)$", folder_name)
        if nested_match:
            child_name = nested_match.group(1).strip().strip('"\'')
            parent_name = nested_match.group(2).strip().strip('"\'')
            if (not child_name or not parent_name or
                    any(char in child_name for char in '<>:"/\\|?*') or
                    any(char in parent_name for char in '<>:"|?*')):
                return "I could not create that folder because the name is invalid."
            folder_name = str(Path(parent_name) / child_name)
        if not folder_name or any(char in folder_name for char in '<>:"|?*'):
            return "I could not create that folder because the name is invalid."
        folder_path = Path.cwd() / folder_name
        try:
            folder_path.mkdir(parents=True, exist_ok=True)
            return f"Folder created at {folder_path}."
        except OSError as error:
            return f"I could not create the folder: {error}."

    calculator_match = re.search(
        r"open\s+(?:the\s+)?calculator\s+(?:and\s+)?"
        r"(?:(?:add|calculate|compute|solve|subtract|multiply|divide)\s+)?(.+)$",
        cmd,
    )
    if calculator_match:
        expression = calculator_match.group(1).strip().rstrip("?.")
        try:
            result = calculate_expression(expression)
            if launch_app("calculator", APP_MAPPINGS["calculator"]):
                time.sleep(1.0)
                enter_calculator_expression(expression)
            return f"Opening Calculator. The result of {expression} is {result:g}."
        except (SyntaxError, ValueError, ZeroDivisionError):
            launch_app("calculator", APP_MAPPINGS["calculator"])
            return "I opened Calculator, but I could not understand that arithmetic expression."

    if cmd.startswith("close "):
        target = cmd.replace("close ", "").strip()
        if close_app_by_name(target):
            return f"Terminated {target}."
        return f"Could not find running process for {target}."

    if cmd.startswith("open "):
        target = cmd.replace("open ", "").strip()
        if not target:
            return None
        if target in {"it", "this", "that", "the previous one", "previous one"} \
                or "project" in target:
            return None

        if target == "youtube":
            webbrowser.open("https://www.youtube.com")
            return "Opening YouTube."

        if launch_any_app(target):
            friendly = target.replace("-", " ").title()
            return f"Opening {friendly}."

        if target in APP_MAPPINGS:
            return f"Unable to open {target}. Windows could not start that application."
        return None

    return None


# -------------------------------------------------------------
# DOC AGENT / CODETANTRA COMPANION
# -------------------------------------------------------------
def check_code_agent_command(command: str):
    cmd = (command or "").strip().lower()
    if re.search(r"\b(?:doc\s*agent|document\s*agent|open\s*doc|open\s*document|show\s*document|lab\s*experiment|lab\s*doc|view\s*code|codetantra\s*helper)\b", cmd):
        try:
            from doc_agent import open_doc_agent
            open_doc_agent()
            return "Ultron Document Agent is online. Your lab experiments and documents are ready on top of CodeTantra."
        except Exception as e:
            return f"Error opening Document Agent: {e}"

    if re.search(r"\b(?:code\s*agent|code\s*typer|auto\s*type|type\s*code|codetantra|ghost\s*typer|inject\s*code)\b", cmd):
        try:
            from code_agent import open_code_agent
            open_code_agent()
            try:
                from doc_agent import open_doc_agent
                open_doc_agent()
            except Exception:
                pass
            return "Ultron Code Agent is online. You can load your code, select your CodeTantra space, and start the countdown or press F8."
        except Exception as e:
            return f"Code Agent initialized. Press F8 in CodeTantra to type from clipboard: {e}"
    return None


# -------------------------------------------------------------
# 5. WINDOWS SYSTEM CONTROLS
# -------------------------------------------------------------
def check_windows_system_command(command: str):
    cmd = command.lower().strip()

    if "bluetooth" in cmd and any(word in cmd for word in ["turn on", "enable"]):
        if set_bluetooth_enabled(True):
            return "Bluetooth adapter enabled. If the radio switch is off, Windows requires turning it on from Quick Settings."
        return "I could not enable the Bluetooth adapter. Windows may require administrator permission."

    if ("hotspot" in cmd or "mobile hotspot" in cmd) and any(word in cmd for word in ["turn on", "enable"]):
        if set_hotspot_enabled(True):
            return "Hotspot enabled."
        return "Hotspot cannot be enabled automatically because this Wi-Fi adapter reports hosted-network support is unavailable."

    if any(label in cmd for label in ["wifi", "wi-fi"]) and any(word in cmd for word in ["turn on", "enable"]):
        subprocess.Popen(["explorer.exe", "ms-settings:network-wifi"], shell=False)
        return "Wi-Fi settings opened. Direct Wi-Fi toggling is restricted by Windows."

    if "lock screen" in cmd or "lock computer" in cmd or "lock pc" in cmd:
        subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
        return "Locking system workstation."

    brightness_match = re.search(r"(?:increase|raise|turn up|brighten|set).*brightness(?:\s+to)?\s*(\d+)?", cmd)
    if brightness_match:
        target = brightness_match.group(1)
        if target:
            value = int(target)
            set_brightness(value)
            return f"Brightness set to {value} percent."
        current = get_current_brightness()
        new_value = min(100, current + 10)
        set_brightness(new_value)
        return f"Brightness increased to {new_value} percent."

    brightness_down_match = re.search(r"(?:decrease|lower|dim|turn down).*brightness(?:\s+to)?\s*(\d+)?", cmd)
    if brightness_down_match:
        target = brightness_down_match.group(1)
        if target:
            value = int(target)
            set_brightness(value)
            return f"Brightness set to {value} percent."
        current = get_current_brightness()
        new_value = max(0, current - 10)
        set_brightness(new_value)
        return f"Brightness decreased to {new_value} percent."

    brightness_value_match = re.search(r"brightness(?:\s+to)?\s*(\d+)", cmd)
    if brightness_value_match:
        value = int(brightness_value_match.group(1))
        set_brightness(value)
        return f"Brightness set to {value} percent."

    if "screen record" in cmd or "record screen" in cmd or "screen recorder" in cmd or "record my screen" in cmd:
        pyautogui.hotkey('win', 'alt', 'r')
        return "Starting screen recording."

    if "stop screen recording" in cmd:
        pyautogui.hotkey('win', 'alt', 'r')
        return "Stopping screen recording."

    if "minimize all" in cmd or "show desktop" in cmd:
        pyautogui.hotkey('win', 'd')
        return "Displaying desktop."

    if "screenshot" in cmd or "take a screenshot" in cmd or "capture screen" in cmd:
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop", "ultron_screenshot.png")
        pyautogui.screenshot(desktop_path)
        return "Screenshot captured and saved to your desktop."

    if "battery" in cmd or "power level" in cmd:
        battery = psutil.sensors_battery()
        if battery:
            plugged = "plugged in" if battery.power_plugged else "on battery power"
            return f"Battery is at {battery.percent} percent and currently {plugged}."
        return "No battery detected on this system."

    if "system status" in cmd or "ram usage" in cmd or "cpu usage" in cmd:
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        return f"CPU usage is at {cpu} percent. Memory utilization is at {ram} percent."

    if "pause music" in cmd or "play music" in cmd or "pause video" in cmd:
        pyautogui.press("playpause")
        return "Toggling media playback."

    if "next song" in cmd or "next track" in cmd:
        pyautogui.press("nexttrack")
        return "Skipping to next track."

    return None


# -------------------------------------------------------------
# MASTER ROUTER
# -------------------------------------------------------------
def _run_intent(intent, command=""):
    normalized_command = normalize_command(command)
    if intent.intent == Intent.CREATE_FOLDER:
        return check_app_command(normalized_command)
    if intent.intent == Intent.CREATE_FILE:
        return check_create_file_command(normalized_command)
    if intent.intent == Intent.MOVE_FILE:
        return check_move_file_command(normalized_command)
    if intent.intent == Intent.COPY_FILE:
        return check_copy_file_command(normalized_command)
    if intent.intent == Intent.RENAME_FILE:
        return check_move_file_command(normalized_command)
    if intent.intent == Intent.VOLUME_CONTROL:
        return check_volume_command(normalized_command)
    if intent.intent in {Intent.BRIGHTNESS_CONTROL, Intent.SCREENSHOT}:
        return check_windows_system_command(normalized_command)

    if intent.intent == Intent.OPEN_URL:
        if intent.target == "current website":
            return "I don't have a recently opened website to return to yet."
        url = known_website_url(intent.target)
        if not url:
            target = intent.target.strip()
            if target.lower().startswith("www."):
                target = target[4:]
            if "://" not in target and "." not in target:
                return None
            url = target if "://" in target else f"https://{target}"
        if _open_url_in_browser(url, intent.browser or None):
            destination = {
                "youtube": "YouTube",
            }.get(intent.target.lower(), intent.target or url)
            if intent.browser:
                return f"Opening {destination} in {intent.browser}."
            return f"Opening {destination}."
        if intent.browser:
            return f"I couldn't open {intent.target}: {intent.browser} isn't available."
        return "I couldn't open that website."

    if intent.intent == Intent.SEARCH_WEB:
        if not intent.target:
            return "What should I search the web for?"
        url = f"https://www.google.com/search?q={quote_plus(intent.target)}"
        if _open_url_in_browser(url, intent.browser or None):
            return f"Searching the web for {intent.target}."
        if intent.browser:
            return f"I couldn't search in {intent.browser}: that browser isn't available."
        return "I couldn't open the web search."

    if intent.intent == Intent.OPEN_APPLICATION:
        if launch_any_app(intent.target):
            return f"Opening {intent.target}."
        return (
            f"{intent.target} wasn't resolved as an installed application. "
            "I did not search for or open a similarly named file."
        )

    if intent.intent == Intent.CLOSE_APPLICATION:
        if close_app_by_name(intent.target):
            return f"Closed {intent.target}."
        return f"Could not find a running application named {intent.target}."

    if intent.intent == Intent.SEARCH_LOCAL:
        matches = _find_in_index(intent.target)
        if not matches:
            return f"I couldn't find {intent.target} on this computer."
        return "Found: " + "; ".join(matches[:5])

    if intent.intent == Intent.SHOW_PROJECT_FOLDER:
        target = intent.target
        if target and os.path.isdir(target):
            return f"Your current project folder is {os.path.abspath(target)}."
        return f"Your current project folder is {os.path.dirname(os.path.abspath(__file__))}."

    if intent.intent == Intent.OPEN_PROJECT:
        target = intent.target.strip().strip("\"'")
        if target.lower() in {
            "", "it", "that", "this", "the project i'm working on",
            "the project i am working on",
        }:
            return None

        if os.path.isdir(target):
            folder = os.path.abspath(target)
        elif os.path.isabs(target):
            return f"The requested project folder isn't available at {target}."
        elif target.lower() in {"ultron", "ultron personal ai assistant"}:
            folder = os.path.dirname(os.path.abspath(__file__))
        else:
            folder_name = re.sub(r"\s+project$", "", target, flags=re.I).strip()
            folders = list(dict.fromkeys(_find_folder_in_index(folder_name)))
            if not folders:
                return f"I couldn't find the {target} project folder."
            if len(folders) > 1:
                return (
                    f"I found multiple folders for {target}. "
                    "Specify the project path or its parent folder."
                )
            folder = folders[0]

        editor = intent.application or "VS Code"
        if not launch_app_with_path(editor, folder):
            return f"I found {folder}, but couldn't open it in {editor}."
        return f"Opening {os.path.basename(folder)} in {editor}."

    if intent.intent == Intent.OPEN_FILE:
        if os.path.isabs(intent.target):
            if not os.path.isfile(intent.target):
                return f"The requested file isn't available at {intent.target}."
        return check_open_file_command(f"open {intent.target}")

    if intent.intent == Intent.OPEN_FOLDER:
        target = intent.target.strip().strip("\"'")
        if target.lower() in {"it", "that", "this", "that folder", "this folder"}:
            return None
        if os.path.isdir(target):
            try:
                os.startfile(os.path.abspath(target))
                return f"Opening folder {os.path.basename(target)}."
            except OSError:
                return f"I found {target}, but couldn't open it."
        if os.path.isabs(target):
            return f"The requested folder isn't available at {target}."
        return check_open_file_command(f"open folder {target}")

    return None


def handle_command(command: str, context: str = ""):
    try:
        code_res = check_code_agent_command(command)
        if code_res:
            return code_res
        inbox_res = check_inbox_command(command)
        if inbox_res:
            return inbox_res
        classified = classify_intent(command, context=context)
        command = normalize_command(command)
        routed = _run_intent(classified, command)
        if routed:
            return routed

        if re.match(r"^open\s+(?:the\s+)?calculator\b", command):
            calculator_response = check_app_command(command)
            if calculator_response:
                return calculator_response

        for handler in [
            check_name_command,
            check_memory_command,
            check_volume_command,
            check_inbox_command,
            check_recent_file_command,
            check_create_file_command,
            check_move_file_command,
            check_copy_file_command,
            check_web_command,
            check_camera_command,
            check_power_command,
            check_app_command,
            check_search_in_files_command,
            check_open_file_command,
            check_windows_system_command,
        ]:
            res = handler(command)
            if res:
                return res
        return None
    except IndexBuildingError:
        return INDEX_BUILDING_MESSAGE