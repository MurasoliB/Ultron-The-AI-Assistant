"""
ULTRON // ALWAYS-ON-TOP DOCUMENT & CODE AGENT (CODETANTRA & LAB COMPANION)
========================================================================
Floating, always-on-top HUD designed for lab exams, CodeTantra, and web IDEs.

Key Capabilities:
1. Always on top of anything (CodeTantra test screens, browsers, full-screen IDEs).
2. Document Access: Opens & parses PDF, Word (.docx), Java, Python, C, C++, Web, and text files.
3. Built-in Lab Navigator: Instantly browse lab experiments (e.g. KEC IT Lab Experiments).
4. CodeTantra Companion:
   - "See the code": High-contrast, line-numbered, searchable code viewer with line-prompter.
   - "Enter the code": Click-to-Inject OS-level ghost typing (bypasses paste detection & tab switch).
5. Stealth Controls:
   - Opacity slider (20% to 100% ghost transparency).
   - Collapse to mini floating pill (34px tall).
   - Global Panic / Peek hotkey [F7] to instantly hide/reveal the window.
"""

import os
import sys

# Auto-re-exec inside JARVIS venv if invoked with system Python
_base_dir = os.path.dirname(os.path.abspath(__file__))
_venv_python = os.path.join(_base_dir, "venv", "Scripts", "python.exe")
_venv_pythonw = os.path.join(_base_dir, "venv", "Scripts", "pythonw.exe")
_in_venv = sys.prefix.lower().startswith(os.path.join(_base_dir, "venv").lower())
if not _in_venv and os.path.isfile(_venv_python) and "--no-reexec" not in sys.argv:
    import subprocess
    _target_py = _venv_pythonw if (os.path.isfile(_venv_pythonw) and "pythonw" in sys.executable.lower()) else _venv_python
    subprocess.Popen([_target_py] + sys.argv + ["--no-reexec"])
    sys.exit(0)

import json
import time
import threading
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pyperclip
from pynput.keyboard import Controller, Key, KeyCode, Listener
from pynput import mouse
import ultron_sync

# Import the battle-tested CodeTyper engine from code_agent
from code_agent import code_agent_typer, SPEED_PRESETS

# Win32 Pinning & Z-Order declarations for permanent topmost over fullscreen CodeTantra
if sys.platform == "win32":
    user32 = ctypes.windll.user32

    # Attach process and thread to interactive desktop winsta0\default
    try:
        _hwinsta = user32.OpenWindowStationW("winsta0", False, 0x10000000)
        if _hwinsta:
            user32.SetProcessWindowStation(_hwinsta)
        _hdesk = user32.OpenDesktopW("default", 0, False, 0x10000000)
        if _hdesk:
            user32.SetThreadDesktop(_hdesk)
    except Exception:
        pass
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND

    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    TOPMOST_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW

try:
    import pymupdf
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


# Default Lab Directory bookmarks
DEFAULT_LAB_DIRS = [
    r"C:\MY DRIVE\15\KEC IT\Subjects\Lab Experiments",
    os.path.expanduser(r"~\Documents"),
    os.path.expanduser(r"~\Desktop"),
]

HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".ultron_doc_history.json")


# ---------------------------------------------------------------------------
# DOCUMENT PARSING ENGINE
# ---------------------------------------------------------------------------
class DocReader:
    """Extracts clean text and metadata from PDF, DOCX, and source code files."""

    SUPPORTED_EXTS = {
        ".java": "Java",
        ".py": "Python",
        ".c": "C Source",
        ".cpp": "C++ Source",
        ".h": "C/C++ Header",
        ".cs": "C# Source",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "React TSX",
        ".jsx": "React JSX",
        ".html": "HTML Document",
        ".css": "CSS Stylesheet",
        ".sql": "SQL Script",
        ".json": "JSON Data",
        ".txt": "Text File",
        ".md": "Markdown",
        ".pdf": "PDF Document",
        ".docx": "Word Document",
        ".doc": "Word Document",
    }

    @staticmethod
    def get_file_icon(ext: str) -> str:
        ext = ext.lower()
        if ext == ".java":
            return "☕"
        if ext == ".py":
            return "🐍"
        if ext in (".c", ".cpp", ".h", ".cs"):
            return "⚡"
        if ext in (".js", ".ts", ".tsx", ".jsx", ".html", ".css"):
            return "🌐"
        if ext == ".pdf":
            return "📑"
        if ext in (".docx", ".doc"):
            return "📄"
        if ext == ".sql":
            return "🗄️"
        return "📝"

    @classmethod
    def read_file(cls, path: str):
        """Returns dict: {'text': str, 'ext': str, 'type': str, 'pages': int or None, 'error': str or None}"""
        if not os.path.isfile(path):
            return {"text": "", "ext": "", "type": "Unknown", "pages": None, "error": "File does not exist."}

        ext = os.path.splitext(path)[1].lower()
        file_type = cls.SUPPORTED_EXTS.get(ext, "Plain Text")

        try:
            # 1. PDF Documents
            if ext == ".pdf":
                if PYMUPDF_AVAILABLE:
                    doc = pymupdf.open(path)
                    num_pages = len(doc)
                    pages_text = []
                    for i in range(num_pages):
                        extracted = doc[i].get_text() or ""
                        pages_text.append(f"// ==================== [PAGE {i + 1} of {num_pages}] ====================\n{extracted.strip()}")
                    full_text = "\n\n".join(pages_text)
                    return {"text": full_text, "ext": ext, "type": file_type, "pages": num_pages, "error": None}
                elif PYPDF_AVAILABLE:
                    reader = pypdf.PdfReader(path)
                    pages_text = []
                    num_pages = len(reader.pages)
                    for i, page in enumerate(reader.pages):
                        extracted = page.extract_text() or ""
                        pages_text.append(f"// ==================== [PAGE {i + 1} of {num_pages}] ====================\n{extracted.strip()}")
                    full_text = "\n\n".join(pages_text)
                    return {"text": full_text, "ext": ext, "type": file_type, "pages": num_pages, "error": None}
                else:
                    return {
                        "text": "[PyMuPDF not installed: unable to parse PDF]",
                        "ext": ext,
                        "type": file_type,
                        "pages": 0,
                        "error": "PyMuPDF missing"
                    }

            # 2. Word Documents (.docx)
            if ext == ".docx":
                if not DOCX_AVAILABLE:
                    return {
                        "text": "[python-docx not installed: unable to parse DOCX]",
                        "ext": ext,
                        "type": file_type,
                        "pages": None,
                        "error": "python-docx package missing"
                    }
                doc = docx.Document(path)
                lines = []
                for p in doc.paragraphs:
                    if p.text.strip():
                        lines.append(p.text)
                for table in doc.tables:
                    lines.append("\n// --- Table Data ---")
                    for row in table.rows:
                        row_vals = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                        lines.append(" | ".join(row_vals))
                full_text = "\n".join(lines)
                return {"text": full_text, "ext": ext, "type": file_type, "pages": None, "error": None}

            # 3. Source Code / Plain Text Files
            encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
            for enc in encodings:
                try:
                    with open(path, "r", encoding=enc) as f:
                        text = f.read()
                    return {"text": text, "ext": ext, "type": file_type, "pages": None, "error": None}
                except UnicodeDecodeError:
                    continue

            return {"text": "", "ext": ext, "type": file_type, "pages": None, "error": "Could not decode file with supported encodings."}

        except Exception as e:
            return {"text": f"[Error reading file: {e}]", "ext": ext, "type": file_type, "pages": None, "error": str(e)}

    @classmethod
    def scan_folder(cls, folder_path: str, max_files: int = 400):
        """Scans directory recursively for documents and code files."""
        if not os.path.isdir(folder_path):
            return []

        ignored_dirs = {
            "node_modules", "target", "build", "dist", "__pycache__",
            "venv", ".venv", "env", "site-packages", ".git", ".vscode",
            ".idea", "AppData", "cache", "bin", "obj"
        }

        results = []
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ignored_dirs]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in cls.SUPPORTED_EXTS:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, folder_path)
                    results.append({
                        "name": file,
                        "rel_path": rel_path,
                        "full_path": full_path,
                        "ext": ext,
                        "icon": cls.get_file_icon(ext),
                    })
                    if len(results) >= max_files:
                        break
            if len(results) >= max_files:
                break
        return sorted(results, key=lambda x: (x["ext"] not in (".java", ".py", ".c", ".cpp"), x["rel_path"]))


# ---------------------------------------------------------------------------
# DOCUMENT HISTORY & PREFERENCES
# ---------------------------------------------------------------------------
class DocHistory:
    """Manages recently opened files and folders."""

    def __init__(self):
        self.recent_files = []
        self.recent_folders = []
        self.pinned_folders = []
        self.load()

    def load(self):
        # Auto-detect existing lab folders
        for default_dir in DEFAULT_LAB_DIRS:
            if os.path.isdir(default_dir) and default_dir not in self.pinned_folders:
                self.pinned_folders.append(default_dir)

        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.recent_files = [f for f in data.get("recent_files", []) if os.path.isfile(f)]
                self.recent_folders = [f for f in data.get("recent_folders", []) if os.path.isdir(f)]
                for p in data.get("pinned_folders", []):
                    if os.path.isdir(p) and p not in self.pinned_folders:
                        self.pinned_folders.append(p)
            except Exception:
                pass

    def save(self):
        try:
            data = {
                "recent_files": self.recent_files[:20],
                "recent_folders": self.recent_folders[:10],
                "pinned_folders": self.pinned_folders[:10],
            }
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def add_file(self, path: str):
        if not path or not os.path.isfile(path):
            return
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        folder = os.path.dirname(path)
        self.add_folder(folder)
        self.save()

    def add_folder(self, folder: str):
        if not folder or not os.path.isdir(folder):
            return
        if folder in self.recent_folders:
            self.recent_folders.remove(folder)
        self.recent_folders.insert(0, folder)
        self.save()


# ---------------------------------------------------------------------------
# ALWAYS-ON-TOP DOCUMENT & CODE AGENT WINDOW
# ---------------------------------------------------------------------------
class DocAgentWindow:
    """Always-On-Top Document & Code Viewer with CodeTantra Ghost Typing."""

    def __init__(self, parent_root=None):
        self.parent_root = parent_root
        self.history = DocHistory()
        self.current_file = None
        self.current_folder = None
        self.scanned_files = []
        self.is_collapsed = False
        self.opacity = 0.92
        self.font_size = 11
        self._highlighted_line_idx = None
        self._search_matches = []
        self._current_match_idx = -1
        self._is_hidden = False

        # Visual PDF with Images State
        self.pdf_doc = None
        self.pdf_current_page = 0
        self.pdf_zoom = 1.10
        self.pdf_photo = None
        self.current_view_mode = "CODE"  # "CODE" or "PDF_VISUAL"

        # Build UI: Always create an independent top-level window so clicking external apps never minimizes it
        try:
            self.window = tk.Tk()
        except Exception:
            self.window = tk.Toplevel()

        self.window.title("ULTRON // ALWAYS-ON-TOP DOC AGENT")
        self.window.geometry("860x600+180+80")
        self.window.minsize(450, 200)

        # Style & Attributes: Permanent borderless overlay like mic_button.py (never minimizes in CodeTantra)
        self._apply_dark_theme()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", self.opacity)

        # Build Layout
        self._build_header()
        self._build_toolbar()
        self._build_body()
        self._build_status_bar()

        # Connect CodeTyper callbacks
        code_agent_typer.on_progress = self._on_type_progress
        code_agent_typer.on_status_change = self._on_type_status

        # Global Hotkey Listener (F7 = Arm, F8 = Type All, F9 = Abort, F6 = Hide/Show)
        self._init_global_hotkeys()

        # Bind events
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self._setup_drag_window()

        # Keyboard page navigation for visual documents
        self.window.bind("<Prior>", lambda e: self.pdf_prev_page() if self.current_view_mode == "PDF_VISUAL" else None)
        self.window.bind("<Next>", lambda e: self.pdf_next_page() if self.current_view_mode == "PDF_VISUAL" else None)

        # Style & Attributes: Permanent borderless overlay like mic_button.py (never minimizes in CodeTantra)
        if sys.platform == "win32":
            try:
                self.window.update_idletasks()
                hwnd = self._get_top_hwnd()
                if hwnd:
                    GWL_EXSTYLE = -20
                    WS_EX_TOOLWINDOW = 0x00000080
                    WS_EX_TOPMOST = 0x00000008
                    cur_ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur_ex | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
            except Exception:
                pass

        # Native keepalive identical to mic_button.py (never minimizes when clicking CodeTantra)
        self.window.after(1000, self._keep_on_top)

        # Bring to front & focus
        self.window.lift()
        self.window.focus_force()

        # Automatically open most recent file or lab experiments folder
        self._auto_load_initial_content()

    def _apply_dark_theme(self):
        self.bg_dark = "#0e1117"
        self.bg_panel = "#151821"
        self.bg_editor = "#181b26"
        self.bg_active = "#1f2433"
        self.fg_bright = "#e6edf3"
        self.fg_muted = "#7d8590"
        self.neon_cyan = "#00e5ff"
        self.neon_red = "#ff2a4b"
        self.neon_gold = "#ffb300"
        self.border_col = "#2a3142"

        self.window.configure(bg=self.bg_dark)

    def _build_header(self):
        """Top draggable title bar with stealth and window controls."""
        self.header_frame = tk.Frame(self.window, bg=self.bg_panel, height=36, relief=tk.FLAT)
        self.header_frame.pack(fill=tk.X, side=tk.TOP)
        self.header_frame.pack_propagate(False)

        # Title / Badge
        title_box = tk.Frame(self.header_frame, bg=self.bg_panel)
        title_box.pack(side=tk.LEFT, padx=8, pady=4)

        icon_lbl = tk.Label(title_box, text="📑", font=("Segoe UI", 12), bg=self.bg_panel, fg=self.neon_cyan)
        icon_lbl.pack(side=tk.LEFT, padx=(0, 4))

        self.title_lbl = tk.Label(
            title_box,
            text="ULTRON // DOC AGENT",
            font=("Consolas", 10, "bold"),
            bg=self.bg_panel,
            fg=self.neon_cyan
        )
        self.title_lbl.pack(side=tk.LEFT)

        tag_lbl = tk.Label(
            title_box,
            text="[ALWAYS-ON-TOP]",
            font=("Consolas", 8, "bold"),
            bg=self.bg_panel,
            fg=self.neon_gold
        )
        tag_lbl.pack(side=tk.LEFT, padx=6)

        hotkey_badge = tk.Label(
            title_box,
            text="[F6: Hide/Show All] [F7: Arm] [F8: Type] [F9: Stop]",
            font=("Consolas", 8),
            bg=self.bg_active,
            fg=self.fg_muted,
            padx=4, pady=1
        )
        hotkey_badge.pack(side=tk.LEFT, padx=4)

        # Window Controls (Right side)
        right_box = tk.Frame(self.header_frame, bg=self.bg_panel)
        right_box.pack(side=tk.RIGHT, padx=6)

        # Close / Hide Button
        close_btn = tk.Button(
            right_box, text="✕", font=("Consolas", 10, "bold"),
            bg=self.bg_panel, fg=self.neon_red, activebackground=self.neon_red, activeforeground="#ffffff",
            bd=0, padx=8, pady=2, cursor="hand2", command=self.hide
        )
        close_btn.pack(side=tk.RIGHT, padx=2)

        # Collapse / Expand Button
        self.collapse_btn = tk.Button(
            right_box, text="▲ Collapse", font=("Consolas", 8, "bold"),
            bg=self.bg_active, fg=self.fg_bright, bd=0, padx=6, pady=2,
            cursor="hand2", command=self.toggle_collapse
        )
        self.collapse_btn.pack(side=tk.RIGHT, padx=4)

        # Opacity Slider
        opacity_box = tk.Frame(right_box, bg=self.bg_panel)
        opacity_box.pack(side=tk.RIGHT, padx=8)

        tk.Label(opacity_box, text="Ghost:", font=("Consolas", 8), bg=self.bg_panel, fg=self.fg_muted).pack(side=tk.LEFT, padx=2)
        self.opacity_slider = tk.Scale(
            opacity_box, from_=25, to=100, orient=tk.HORIZONTAL,
            showvalue=False, length=75, sliderlength=12, width=10,
            bg=self.bg_panel, fg=self.neon_cyan, highlightthickness=0, bd=0,
            command=self._on_opacity_change
        )
        self.opacity_slider.set(int(self.opacity * 100))
        self.opacity_slider.pack(side=tk.LEFT)

        self.opacity_lbl = tk.Label(opacity_box, text=f"{int(self.opacity * 100)}%", font=("Consolas", 8), bg=self.bg_panel, fg=self.fg_muted, width=4)
        self.opacity_lbl.pack(side=tk.LEFT)

    def _build_toolbar(self):
        """Action buttons: Open File, Open Folder, CodeTantra Inject, Search, Zoom."""
        self.toolbar = tk.Frame(self.window, bg=self.bg_active, height=36, bd=0)
        self.toolbar.pack(fill=tk.X, side=tk.TOP)

        # Left tools: Open actions
        open_file_btn = tk.Button(
            self.toolbar, text="📂 Open File", font=("Segoe UI", 9, "bold"),
            bg="#1e293b", fg=self.fg_bright, activebackground=self.neon_cyan, activeforeground="#000000",
            bd=0, padx=8, pady=3, cursor="hand2", command=self.browse_file
        )
        open_file_btn.pack(side=tk.LEFT, padx=(6, 3), pady=4)

        open_folder_btn = tk.Button(
            self.toolbar, text="📁 Lab Folder", font=("Segoe UI", 9),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=8, pady=3,
            cursor="hand2", command=self.browse_folder
        )
        open_folder_btn.pack(side=tk.LEFT, padx=3, pady=4)

        # Recent Docs Dropdown Button
        self.recent_btn = tk.Menubutton(
            self.toolbar, text="⭐ Recent ▼", font=("Segoe UI", 9),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=8, pady=3,
            cursor="hand2", relief=tk.FLAT
        )
        self.recent_menu = tk.Menu(self.recent_btn, tearoff=0, bg=self.bg_panel, fg=self.fg_bright, activebackground=self.neon_cyan, activeforeground="#000000")
        self.recent_btn.config(menu=self.recent_menu)
        self.recent_btn.pack(side=tk.LEFT, padx=3, pady=4)
        self._refresh_recent_menu()

        # Separator
        sep1 = tk.Frame(self.toolbar, bg=self.border_col, width=1)
        sep1.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)

        # CodeTantra Actions
        self.inject_btn = tk.Button(
            self.toolbar, text="🎯 Click-to-Inject in CodeTantra", font=("Segoe UI", 9, "bold"),
            bg="#0284c7", fg="#ffffff", activebackground=self.neon_cyan, activeforeground="#000000",
            bd=0, padx=10, pady=3, cursor="hand2", command=self.arm_click_inject
        )
        self.inject_btn.pack(side=tk.LEFT, padx=3, pady=4)

        self.type_all_btn = tk.Button(
            self.toolbar, text="⚡ Type (F8)", font=("Segoe UI", 9, "bold"),
            bg="#166534", fg="#ffffff", activebackground="#22c55e",
            bd=0, padx=8, pady=3, cursor="hand2", command=self.type_all_code
        )
        self.type_all_btn.pack(side=tk.LEFT, padx=3, pady=4)

        copy_btn = tk.Button(
            self.toolbar, text="📋 Copy", font=("Segoe UI", 9),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=6, pady=3,
            cursor="hand2", command=self.copy_clean_code
        )
        copy_btn.pack(side=tk.LEFT, padx=3, pady=4)

        self.stop_btn = tk.Button(
            self.toolbar, text="⏹ Stop (F9)", font=("Segoe UI", 9, "bold"),
            bg="#991b1b", fg="#ffffff", activebackground=self.neon_red,
            bd=0, padx=6, pady=3, cursor="hand2", command=self.abort_typing
        )
        self.stop_btn.pack(side=tk.LEFT, padx=3, pady=4)

        # Mode Toggle Button (Visual PDF with images vs Code View)
        self.view_mode_btn = tk.Button(
            self.toolbar, text="🖼️ Visual PDF (Images)", font=("Segoe UI", 9, "bold"),
            bg="#2563eb", fg="#ffffff", activebackground=self.neon_cyan, activeforeground="#000000",
            bd=0, padx=8, pady=3, cursor="hand2", command=self.toggle_view_mode
        )

        # Right tools: Search & Font Size
        right_toolbar = tk.Frame(self.toolbar, bg=self.bg_active)
        right_toolbar.pack(side=tk.RIGHT, padx=6)

        # Font zoom
        zoom_in_btn = tk.Button(
            right_toolbar, text="+", font=("Consolas", 9, "bold"),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=6, pady=2,
            cursor="hand2", command=self.font_zoom_in
        )
        zoom_in_btn.pack(side=tk.RIGHT, padx=2)

        self.font_lbl = tk.Label(right_toolbar, text=f"{self.font_size}pt", font=("Consolas", 8), bg=self.bg_active, fg=self.fg_muted)
        self.font_lbl.pack(side=tk.RIGHT, padx=2)

        zoom_out_btn = tk.Button(
            right_toolbar, text="-", font=("Consolas", 9, "bold"),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=6, pady=2,
            cursor="hand2", command=self.font_zoom_out
        )
        zoom_out_btn.pack(side=tk.RIGHT, padx=2)

        # Search Bar
        search_frame = tk.Frame(right_toolbar, bg="#1e293b", padx=4, pady=1)
        search_frame.pack(side=tk.RIGHT, padx=6)

        search_icon = tk.Label(search_frame, text="🔍", font=("Segoe UI", 8), bg="#1e293b", fg=self.fg_muted)
        search_icon.pack(side=tk.LEFT)

        self.search_entry = tk.Entry(
            search_frame, font=("Consolas", 9), bg="#1e293b", fg=self.fg_bright,
            insertbackground=self.neon_cyan, bd=0, width=14
        )
        self.search_entry.pack(side=tk.LEFT, padx=2)
        self.search_entry.bind("<Return>", lambda e: self.search_next())
        self.search_entry.bind("<KeyRelease>", lambda e: self._on_search_change())

        search_next_btn = tk.Button(
            search_frame, text="▼", font=("Segoe UI", 7),
            bg="#1e293b", fg=self.neon_cyan, bd=0, padx=2,
            cursor="hand2", command=self.search_next
        )
        search_next_btn.pack(side=tk.LEFT)

    def _build_body(self):
        """Split view: Left Sidebar (Lab Files) + Right Panel (Document & Code Viewer)."""
        self.body_frame = tk.Frame(self.window, bg=self.bg_dark)
        self.body_frame.pack(fill=tk.BOTH, expand=True)

        # Collapsible Sidebar (Left)
        self.sidebar_frame = tk.Frame(self.body_frame, bg=self.bg_panel, width=220)
        self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar_frame.pack_propagate(False)

        # Sidebar Header
        sidebar_hdr = tk.Frame(self.sidebar_frame, bg=self.bg_panel, height=28)
        sidebar_hdr.pack(fill=tk.X, side=tk.TOP, padx=6, pady=(4, 2))

        self.sidebar_title_lbl = tk.Label(
            sidebar_hdr, text="LAB FILES", font=("Consolas", 9, "bold"),
            bg=self.bg_panel, fg=self.neon_cyan
        )
        self.sidebar_title_lbl.pack(side=tk.LEFT)

        self.sidebar_toggle_btn = tk.Button(
            sidebar_hdr, text="◀ Hide", font=("Consolas", 8),
            bg=self.bg_active, fg=self.fg_muted, bd=0, padx=4,
            cursor="hand2", command=self.toggle_sidebar
        )
        self.sidebar_toggle_btn.pack(side=tk.RIGHT)

        # File Listbox with Scrollbar
        list_box_frame = tk.Frame(self.sidebar_frame, bg=self.bg_panel)
        list_box_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        list_scroll = tk.Scrollbar(list_box_frame, bg=self.bg_panel)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_listbox = tk.Listbox(
            list_box_frame, bg=self.bg_editor, fg=self.fg_bright,
            selectbackground=self.neon_cyan, selectforeground="#000000",
            font=("Segoe UI", 9), bd=0, highlightthickness=1,
            highlightbackground=self.border_col, highlightcolor=self.neon_cyan,
            yscrollcommand=list_scroll.set
        )
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.config(command=self.file_listbox.yview)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)

        # Mini toggle pill when sidebar is hidden
        self.sidebar_mini_btn = tk.Button(
            self.body_frame, text="▶\nF\nI\nL\nE\nS", font=("Consolas", 7, "bold"),
            bg=self.bg_panel, fg=self.neon_cyan, bd=0, padx=2,
            cursor="hand2", command=self.toggle_sidebar
        )

        # Right Panel: Code & Document Viewer (Hosts Code View and Visual PDF View)
        self.editor_frame = tk.Frame(self.body_frame, bg=self.bg_dark)
        self.editor_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Container 1: Code & Text View (Line Numbers, Text Area)
        self.code_view_frame = tk.Frame(self.editor_frame, bg=self.bg_dark)
        self.code_view_frame.pack(fill=tk.BOTH, expand=True)

        x_scroll = tk.Scrollbar(self.code_view_frame, orient=tk.HORIZONTAL, bg=self.bg_dark)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        y_scroll = tk.Scrollbar(self.code_view_frame, orient=tk.VERTICAL, bg=self.bg_dark)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        viewer_box = tk.Frame(self.code_view_frame, bg=self.bg_editor)
        viewer_box.pack(fill=tk.BOTH, expand=True)

        self.line_gutter = tk.Text(
            viewer_box, width=5, bg="#11131a", fg=self.fg_muted,
            font=("Consolas", self.font_size), bd=0, padx=4, pady=6,
            takefocus=0, state=tk.DISABLED, wrap=tk.NONE
        )
        self.line_gutter.pack(side=tk.LEFT, fill=tk.Y)

        self.code_text = tk.Text(
            viewer_box, bg=self.bg_editor, fg=self.fg_bright,
            font=("Consolas", self.font_size), bd=0, padx=8, pady=6,
            insertbackground=self.neon_cyan, selectbackground="#264f78",
            selectforeground="#ffffff", wrap=tk.NONE,
            xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set,
            undo=True
        )
        self.code_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _on_y_scroll(*args):
            self.code_text.yview(*args)
            self.line_gutter.yview(*args)

        y_scroll.config(command=_on_y_scroll)
        x_scroll.config(command=self.code_text.xview)

        def _sync_scroll(event=None):
            self.line_gutter.yview_moveto(self.code_text.yview()[0])

        self.code_text.bind("<KeyRelease>", self._on_text_modified)
        self.code_text.bind("<MouseWheel>", lambda e: self.window.after(10, _sync_scroll))
        self.code_text.bind("<Button-1>", self._on_click_line)
        self.code_text.bind("<Up>", self._on_arrow_navigate)
        self.code_text.bind("<Down>", self._on_arrow_navigate)

        self.code_text.tag_configure("active_line", background="#1e3a5f", foreground="#ffffff")
        self.code_text.tag_configure("search_match", background=self.neon_gold, foreground="#000000")
        self.code_text.tag_configure("search_current", background=self.neon_cyan, foreground="#000000")

        # Container 2: Visual PDF Viewer (Full Pages with Images, Diagrams & Schematics)
        self.pdf_view_frame = tk.Frame(self.editor_frame, bg=self.bg_dark)

        # PDF Navigation Bar
        pdf_nav_bar = tk.Frame(self.pdf_view_frame, bg=self.bg_panel, height=34)
        pdf_nav_bar.pack(fill=tk.X, side=tk.TOP)

        self.pdf_prev_btn = tk.Button(
            pdf_nav_bar, text="◀ Prev Page", font=("Segoe UI", 8, "bold"),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=8, pady=2,
            cursor="hand2", command=self.pdf_prev_page
        )
        self.pdf_prev_btn.pack(side=tk.LEFT, padx=(6, 3), pady=4)

        self.pdf_page_lbl = tk.Label(
            pdf_nav_bar, text="Page 1 of 1", font=("Consolas", 9, "bold"),
            bg=self.bg_panel, fg=self.neon_cyan, width=16
        )
        self.pdf_page_lbl.pack(side=tk.LEFT, padx=3)

        self.pdf_next_btn = tk.Button(
            pdf_nav_bar, text="Next Page ▶", font=("Segoe UI", 8, "bold"),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=8, pady=2,
            cursor="hand2", command=self.pdf_next_page
        )
        self.pdf_next_btn.pack(side=tk.LEFT, padx=3, pady=4)

        sep_pdf = tk.Frame(pdf_nav_bar, bg=self.border_col, width=1)
        sep_pdf.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)

        tk.Button(
            pdf_nav_bar, text="🔍 -", font=("Consolas", 8, "bold"),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=6, pady=2,
            cursor="hand2", command=self.pdf_zoom_out
        ).pack(side=tk.LEFT, padx=2)

        self.pdf_zoom_lbl = tk.Label(
            pdf_nav_bar, text="100%", font=("Consolas", 8),
            bg=self.bg_panel, fg=self.fg_muted, width=5
        )
        self.pdf_zoom_lbl.pack(side=tk.LEFT, padx=2)

        tk.Button(
            pdf_nav_bar, text="🔍 +", font=("Consolas", 8, "bold"),
            bg="#1e293b", fg=self.fg_bright, bd=0, padx=6, pady=2,
            cursor="hand2", command=self.pdf_zoom_in
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            pdf_nav_bar, text="↔ Fit Width", font=("Segoe UI", 8),
            bg="#1e293b", fg=self.neon_cyan, bd=0, padx=6, pady=2,
            cursor="hand2", command=self.pdf_fit_width
        ).pack(side=tk.LEFT, padx=4)

        self.pdf_to_code_btn = tk.Button(
            pdf_nav_bar, text="📝 Switch to Code View", font=("Segoe UI", 8, "bold"),
            bg="#0f766e", fg="#ffffff", activebackground=self.neon_cyan, activeforeground="#000000",
            bd=0, padx=8, pady=2, cursor="hand2", command=self.show_code_view
        )
        self.pdf_to_code_btn.pack(side=tk.RIGHT, padx=6, pady=4)

        # PDF Canvas Area
        pdf_canvas_container = tk.Frame(self.pdf_view_frame, bg=self.bg_dark)
        pdf_canvas_container.pack(fill=tk.BOTH, expand=True)

        pdf_x_scroll = tk.Scrollbar(pdf_canvas_container, orient=tk.HORIZONTAL, bg=self.bg_dark)
        pdf_x_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        pdf_y_scroll = tk.Scrollbar(pdf_canvas_container, orient=tk.VERTICAL, bg=self.bg_dark)
        pdf_y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.pdf_canvas = tk.Canvas(
            pdf_canvas_container, bg="#0d0f14", bd=0, highlightthickness=0,
            xscrollcommand=pdf_x_scroll.set, yscrollcommand=pdf_y_scroll.set
        )
        self.pdf_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        pdf_y_scroll.config(command=self.pdf_canvas.yview)
        pdf_x_scroll.config(command=self.pdf_canvas.xview)

        self.pdf_canvas.bind("<MouseWheel>", self._on_pdf_mousewheel)
        self.pdf_canvas.bind("<Configure>", lambda e: self._on_canvas_configure())

    def _build_status_bar(self):
        """Bottom info bar: Active File, Line Count, Injection Status, Resize Grip."""
        self.status_bar = tk.Frame(self.window, bg=self.bg_panel, height=24)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Bottom-right resize grip for smooth resizing of borderless overlay
        self.resize_grip = tk.Label(
            self.status_bar, text="◢", font=("Consolas", 10),
            bg=self.bg_panel, fg=self.neon_cyan, cursor="size_nw_se"
        )
        self.resize_grip.pack(side=tk.RIGHT, padx=(2, 4), pady=1)

        self._resize_data = {"x": 0, "y": 0, "w": 0, "h": 0}

        def _start_resize(event):
            self._resize_data["x"] = event.x_root
            self._resize_data["y"] = event.y_root
            self._resize_data["w"] = self.window.winfo_width()
            self._resize_data["h"] = self.window.winfo_height()

        def _do_resize(event):
            dx = event.x_root - self._resize_data["x"]
            dy = event.y_root - self._resize_data["y"]
            nw = max(450, self._resize_data["w"] + dx)
            nh = max(200, self._resize_data["h"] + dy)
            self.window.geometry(f"{nw}x{nh}")

        self.resize_grip.bind("<Button-1>", _start_resize)
        self.resize_grip.bind("<B1-Motion>", _do_resize)

        self.inject_status_lbl = tk.Label(
            self.status_bar, text="Ready",
            font=("Consolas", 8, "bold"), bg=self.bg_panel, fg=self.neon_cyan
        )
        self.inject_status_lbl.pack(side=tk.RIGHT, padx=8)

        self.file_info_lbl = tk.Label(
            self.status_bar, text="No document loaded. Click [📂 Open File] or select from sidebar.",
            font=("Segoe UI", 8), bg=self.bg_panel, fg=self.fg_muted
        )
        self.file_info_lbl.pack(side=tk.LEFT, padx=8)

    # -----------------------------------------------------------------------
    # WINDOW CONTROLS & GHOST MODE
    # -----------------------------------------------------------------------
    def _setup_drag_window(self):
        """Allows dragging the window seamlessly by clicking anywhere on the header."""
        self._drag_data = {"x": 0, "y": 0}

        def _start_drag(event):
            self._drag_data["x"] = event.x
            self._drag_data["y"] = event.y

        def _do_drag(event):
            x = self.window.winfo_x() + (event.x - self._drag_data["x"])
            y = self.window.winfo_y() + (event.y - self._drag_data["y"])
            self.window.geometry(f"+{x}+{y}")

        self.header_frame.bind("<Button-1>", _start_drag)
        self.header_frame.bind("<B1-Motion>", _do_drag)
        self.title_lbl.bind("<Button-1>", _start_drag)
        self.title_lbl.bind("<B1-Motion>", _do_drag)

    def _keep_on_top(self):
        """Topmost state is persistent via Tkinter attributes; avoids flickering SetWindowPos loops."""
        pass

    def _on_opacity_change(self, val):
        try:
            self.opacity = float(val) / 100.0
            self.window.attributes("-alpha", self.opacity)
            self.opacity_lbl.config(text=f"{int(val)}%")
        except Exception:
            pass

    def toggle_collapse(self):
        """Shrinks window to a 34px-tall horizontal strip so it occupies almost zero space."""
        if not self.is_collapsed:
            self.is_collapsed = True
            self._prev_geom = self.window.geometry()
            self.toolbar.pack_forget()
            self.body_frame.pack_forget()
            self.status_bar.pack_forget()
            w = max(380, self.window.winfo_width())
            x = self.window.winfo_x()
            y = self.window.winfo_y()
            self.window.geometry(f"{w}x36+{x}+{y}")
            self.collapse_btn.config(text="▼ Expand")
        else:
            self.is_collapsed = False
            self.toolbar.pack(fill=tk.X, side=tk.TOP, after=self.header_frame)
            self.body_frame.pack(fill=tk.BOTH, expand=True, after=self.toolbar)
            self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)
            if hasattr(self, "_prev_geom") and self._prev_geom:
                self.window.geometry(self._prev_geom)
            else:
                self.window.geometry("860x600")
            self.collapse_btn.config(text="▲ Collapse")

    def toggle_sidebar(self):
        """Shows or hides the lab files navigation panel."""
        if self.sidebar_frame.winfo_ismapped():
            self.sidebar_frame.pack_forget()
            self.sidebar_mini_btn.pack(side=tk.LEFT, fill=tk.Y, before=self.editor_frame)
        else:
            self.sidebar_mini_btn.pack_forget()
            self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y, before=self.editor_frame)

    def hide(self):
        """Instantly hides window into stealth mode."""
        self._is_hidden = True
        self.window.withdraw()

    def show(self):
        """Restores window from stealth mode."""
        self._is_hidden = False
        self.window.deiconify()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.lift()
        self._pin_topmost()

    def toggle_visibility(self):
        """Unified toggle: minimizes or restores both Mic and Doc Agent together."""
        ultron_sync.toggle_all(self)

    def _get_top_hwnd(self):
        """Gets native Win32 HWND."""
        if sys.platform != "win32":
            return None
        try:
            wid = self.window.winfo_id()
            if wid:
                top = user32.GetAncestor(wid, 2)  # GA_ROOT = 2
                return top if top else wid
            return user32.FindWindowW(None, "ULTRON // ALWAYS-ON-TOP DOC AGENT")
        except Exception:
            return None

    def _keep_on_top(self):
        """Native keepalive matching mic_button.py: never minimizes when user interacts with CodeTantra."""
        try:
            if self.window.winfo_exists() and not self._is_hidden:
                self.window.attributes("-topmost", True)
                self.window.after(2000, self._keep_on_top)
            else:
                self.window.after(1000, self._keep_on_top)
        except Exception:
            pass

    def _pin_topmost(self):
        """Ensures topmost attribute is active."""
        try:
            if self.window.winfo_exists() and not self._is_hidden:
                self.window.attributes("-topmost", True)
        except Exception:
            pass

    def _topmost_loop(self):
        self._keep_on_top()

    def _guard_anti_minimize(self):
        self._pin_topmost()

    def _restore_if_minimized(self):
        self._pin_topmost()

    def _init_global_hotkeys(self):
        """Listens globally for F7 (Arm Inject), F8 (Type All), F9 (Abort), and F6 (Stealth Peek)."""
        def _on_press(key):
            try:
                if key == Key.f7:
                    # F7: Arm Click-to-Inject into CodeTantra
                    self.window.after(0, self.arm_click_inject)
                elif key == Key.f8:
                    # F8: Start Typing immediately
                    self.window.after(0, self.type_all_code)
                elif key in (Key.f9, Key.esc):
                    # F9 / ESC: Immediately abort ghost typing
                    if code_agent_typer.is_typing or getattr(code_agent_typer, "_armed", False):
                        self.window.after(0, self.abort_typing)
                elif key == Key.f6:
                    # F6: Stealth Panic Peek (Hide / Reveal)
                    self.window.after(0, self.toggle_visibility)
            except Exception:
                pass

        try:
            listener = Listener(on_press=_on_press)
            listener.daemon = True
            listener.start()
        except Exception as e:
            print(f"[DocAgent] Hotkey listener warning: {e}")

    # -----------------------------------------------------------------------
    # DOCUMENT MANAGEMENT & VIEWING
    # -----------------------------------------------------------------------
    def _auto_load_initial_content(self):
        """Loads last opened file, or scans default lab folder."""
        # 1. Try most recent file
        if self.history.recent_files:
            latest = self.history.recent_files[0]
            if os.path.isfile(latest):
                self.load_file(latest)
                return

        # 2. Try default KEC Lab Experiments folder
        for lab_dir in self.history.pinned_folders:
            if os.path.isdir(lab_dir):
                self.load_folder(lab_dir)
                return

    def browse_file(self):
        """Opens file dialog for any supported document or code file."""
        init_dir = self.current_folder or os.path.expanduser("~")
        path = filedialog.askopenfilename(
            parent=self.window,
            initialdir=init_dir,
            title="Select Lab Document or Code File",
            filetypes=[
                ("All Supported Files", "*.java;*.py;*.c;*.cpp;*.cs;*.js;*.ts;*.tsx;*.docx;*.pdf;*.txt;*.html;*.css;*.json;*.md"),
                ("Source Code Files", "*.java;*.py;*.c;*.cpp;*.cs;*.js;*.ts;*.tsx;*.html;*.css;*.sql"),
                ("Word & PDF Documents", "*.docx;*.pdf;*.doc;*.txt;*.md"),
                ("All Files", "*.*")
            ]
        )
        if path:
            self.load_file(path)

    def browse_folder(self):
        """Opens folder dialog to load an entire lab directory."""
        init_dir = self.current_folder or os.path.expanduser("~")
        folder = filedialog.askdirectory(parent=self.window, initialdir=init_dir, title="Select Lab Experiments Folder")
        if folder:
            self.load_folder(folder)

    def load_file(self, file_path: str):
        """Reads and displays document in the viewer."""
        if not os.path.isfile(file_path):
            return

        res = DocReader.read_file(file_path)
        if res.get("error") and not res.get("text"):
            messagebox.showerror("Error Reading File", f"Could not read {os.path.basename(file_path)}:\n{res['error']}", parent=self.window)
            return

        self.current_file = file_path
        folder = os.path.dirname(file_path)
        is_inside = False
        if self.current_folder and os.path.isdir(self.current_folder):
            try:
                is_inside = os.path.commonpath([self.current_folder, file_path]) == os.path.abspath(self.current_folder)
            except Exception:
                is_inside = False

        if not is_inside:
            self.current_folder = folder
            self._refresh_file_listbox()

        # Update Viewer Content
        text = res["text"]
        self.code_text.delete("1.0", tk.END)
        self.code_text.insert("1.0", text)
        self._update_line_gutter()

        # Update Status & History
        self.history.add_file(file_path)
        self._refresh_recent_menu()

        lines = text.count("\n") + 1
        chars = len(text)
        meta_str = f"Lines: {lines} | Size: {chars} chars"
        if res.get("pages"):
            meta_str = f"Pages: {res['pages']} | {meta_str}"

        self.file_info_lbl.config(
            text=f"📄 {os.path.basename(file_path)} ({res['type']}) — {meta_str}",
            fg=self.fg_bright
        )
        self.title_lbl.config(text=f"ULTRON // {os.path.basename(file_path)}")

        # Select matching item in listbox
        self._highlight_active_file_in_listbox(file_path)

        # PDF vs Code/Doc Handling (Visual PDF with images vs Monospaced Code)
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf" and PYMUPDF_AVAILABLE and PIL_AVAILABLE:
            try:
                if self.pdf_doc:
                    try:
                        self.pdf_doc.close()
                    except Exception:
                        pass
                self.pdf_doc = pymupdf.open(file_path)
                self.pdf_current_page = 0
                self.pdf_zoom = 1.10
                self.view_mode_btn.pack(side=tk.LEFT, padx=3, pady=4, after=self.stop_btn)
                self.show_pdf_view()
                return
            except Exception as e:
                print(f"[DocAgent] Failed opening visual PDF: {e}")

        # Non-PDF or fallback: Show code view
        if hasattr(self, "view_mode_btn"):
            self.view_mode_btn.pack_forget()
        self.show_code_view()

    def load_folder(self, folder_path: str):
        """Scans folder and populates sidebar file list."""
        if not os.path.isdir(folder_path):
            return

        self.current_folder = folder_path
        self.history.add_folder(folder_path)
        self._refresh_file_listbox()

        folder_name = os.path.basename(folder_path.rstrip("\\/")) or folder_path
        self.sidebar_title_lbl.config(text=f"📁 {folder_name[:16]}")
        self.file_info_lbl.config(
            text=f"Scanned {len(self.scanned_files)} files in {folder_name}. Select a file to view.",
            fg=self.fg_muted
        )

        # If files exist, open the first code file automatically
        if self.scanned_files:
            first = self.scanned_files[0]
            self.load_file(first["full_path"])

    def _refresh_file_listbox(self):
        """Populates sidebar listbox with scanned files."""
        self.file_listbox.delete(0, tk.END)
        if not self.current_folder or not os.path.isdir(self.current_folder):
            return

        self.scanned_files = DocReader.scan_folder(self.current_folder)
        for item in self.scanned_files:
            self.file_listbox.insert(tk.END, f"{item['icon']} {item['rel_path']}")

    def _highlight_active_file_in_listbox(self, file_path: str):
        for idx, item in enumerate(self.scanned_files):
            if os.path.abspath(item["full_path"]) == os.path.abspath(file_path):
                self.file_listbox.selection_clear(0, tk.END)
                self.file_listbox.selection_set(idx)
                self.file_listbox.see(idx)
                break

    def _on_file_select(self, event=None):
        sel = self.file_listbox.curselection()
        if sel and len(sel) > 0:
            idx = sel[0]
            if 0 <= idx < len(self.scanned_files):
                path = self.scanned_files[idx]["full_path"]
                if path != self.current_file:
                    self.load_file(path)

    def _refresh_recent_menu(self):
        """Rebuilds the Recent Documents dropdown menu."""
        self.recent_menu.delete(0, tk.END)
        if not self.history.recent_files:
            self.recent_menu.add_command(label="No recent documents", state=tk.DISABLED)
            return

        for path in self.history.recent_files[:12]:
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            icon = DocReader.get_file_icon(ext)
            self.recent_menu.add_command(
                label=f"{icon} {name}",
                command=lambda p=path: self.load_file(p)
            )

        self.recent_menu.add_separator()
        for folder in self.history.pinned_folders:
            fname = os.path.basename(folder.rstrip("\\/"))
            self.recent_menu.add_command(
                label=f"📁 Open Folder: {fname}",
                command=lambda f=folder: self.load_folder(f)
            )

    # -----------------------------------------------------------------------
    # VISUAL PDF VIEWER (FULL PAGES WITH IMAGES & DIAGRAMS)
    # -----------------------------------------------------------------------
    def show_pdf_view(self):
        """Switches right panel to high-resolution Visual PDF view with images."""
        self.current_view_mode = "PDF_VISUAL"
        self.code_view_frame.pack_forget()
        self.pdf_view_frame.pack(fill=tk.BOTH, expand=True)
        self.view_mode_btn.config(
            text="📝 Switch to Code View",
            bg="#0f766e"
        )
        self._render_pdf_page()

    def show_code_view(self):
        """Switches right panel to monospaced Code/Text view with line numbers."""
        self.current_view_mode = "CODE"
        self.pdf_view_frame.pack_forget()
        self.code_view_frame.pack(fill=tk.BOTH, expand=True)
        if self.current_file and self.current_file.lower().endswith(".pdf"):
            self.view_mode_btn.config(
                text="🖼️ View PDF with Images",
                bg="#2563eb"
            )

    def toggle_view_mode(self):
        if self.current_view_mode == "PDF_VISUAL":
            self.show_code_view()
        else:
            self.show_pdf_view()

    def _render_pdf_page(self):
        """Renders the current PDF page with all its images, diagrams, and text."""
        if not self.pdf_doc or not PYMUPDF_AVAILABLE or not PIL_AVAILABLE:
            return
        try:
            total = len(self.pdf_doc)
            if self.pdf_current_page < 0:
                self.pdf_current_page = 0
            if self.pdf_current_page >= total:
                self.pdf_current_page = total - 1

            page = self.pdf_doc[self.pdf_current_page]
            dpi = max(72, int(115 * self.pdf_zoom))
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            self.pdf_photo = ImageTk.PhotoImage(img)

            self.pdf_canvas.delete("all")

            # Center page in canvas
            self.window.update_idletasks()
            cw = self.pdf_canvas.winfo_width()
            if cw <= 1:
                cw = 600
            x = max(20, (cw - pix.width) // 2) if cw > pix.width else 20
            y = 20

            # Render drop shadow
            self.pdf_canvas.create_rectangle(
                x + 4, y + 4, x + pix.width + 4, y + pix.height + 4,
                fill="#05070a", outline=""
            )
            # Render white sheet background
            self.pdf_canvas.create_rectangle(
                x - 1, y - 1, x + pix.width + 1, y + pix.height + 1,
                fill="#ffffff", outline="#30363d", width=1
            )
            # Draw rendered page image (contains all text + images + vector diagrams)
            self.pdf_canvas.create_image(x, y, anchor=tk.NW, image=self.pdf_photo)

            scroll_w = max(cw, x + pix.width + 40)
            scroll_h = y + pix.height + 40
            self.pdf_canvas.config(scrollregion=(0, 0, scroll_w, scroll_h))
            self.pdf_canvas.yview_moveto(0.0)

            # Update indicators
            self.pdf_page_lbl.config(text=f"Page {self.pdf_current_page + 1} of {total}")
            self.pdf_zoom_lbl.config(text=f"{int(self.pdf_zoom * 100)}%")
            self.pdf_prev_btn.config(state=tk.NORMAL if self.pdf_current_page > 0 else tk.DISABLED)
            self.pdf_next_btn.config(state=tk.NORMAL if self.pdf_current_page < total - 1 else tk.DISABLED)

            fname = os.path.basename(self.current_file) if self.current_file else "PDF"
            self.file_info_lbl.config(
                text=f"📑 {fname} — Page {self.pdf_current_page + 1}/{total} (Visual with Images, {pix.width}x{pix.height}px)",
                fg=self.fg_bright
            )
        except Exception as e:
            print(f"[DocAgent] Error rendering PDF page: {e}")

    def pdf_prev_page(self):
        if self.pdf_doc and self.pdf_current_page > 0:
            self.pdf_current_page -= 1
            self._render_pdf_page()

    def pdf_next_page(self):
        if self.pdf_doc and self.pdf_current_page < len(self.pdf_doc) - 1:
            self.pdf_current_page += 1
            self._render_pdf_page()

    def pdf_zoom_in(self):
        if self.pdf_zoom < 2.5:
            self.pdf_zoom = round(self.pdf_zoom + 0.15, 2)
            self._render_pdf_page()

    def pdf_zoom_out(self):
        if self.pdf_zoom > 0.4:
            self.pdf_zoom = round(self.pdf_zoom - 0.15, 2)
            self._render_pdf_page()

    def pdf_fit_width(self):
        if not self.pdf_doc:
            return
        try:
            page = self.pdf_doc[self.pdf_current_page]
            cw = max(300, self.pdf_canvas.winfo_width() - 60)
            base_w = page.rect.width
            if base_w > 0:
                target_zoom = (cw / base_w) * (72.0 / 115.0)
                self.pdf_zoom = max(0.4, min(2.5, round(target_zoom, 2)))
                self._render_pdf_page()
        except Exception:
            pass

    def _on_pdf_mousewheel(self, event):
        if event.delta:
            units = int(-1 * (event.delta / 40))
            self.pdf_canvas.yview_scroll(units, "units")

    def _on_canvas_configure(self):
        if self.current_view_mode == "PDF_VISUAL" and self.pdf_doc:
            self.window.after(50, self._recenter_pdf_page)

    def _recenter_pdf_page(self):
        try:
            cw = self.pdf_canvas.winfo_width()
            bbox = self.pdf_canvas.bbox(tk.ALL)
            if bbox and self.pdf_photo:
                pw = self.pdf_photo.width()
                ph = self.pdf_photo.height()
                x = max(20, (cw - pw) // 2) if cw > pw else 20
                y = 20
                self.pdf_canvas.delete("all")
                self.pdf_canvas.create_rectangle(
                    x + 4, y + 4, x + pw + 4, y + ph + 4,
                    fill="#05070a", outline=""
                )
                self.pdf_canvas.create_rectangle(
                    x - 1, y - 1, x + pw + 1, y + ph + 1,
                    fill="#ffffff", outline="#30363d", width=1
                )
                self.pdf_canvas.create_image(x, y, anchor=tk.NW, image=self.pdf_photo)
                self.pdf_canvas.config(scrollregion=(0, 0, max(cw, x + pw + 40), y + ph + 40))
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # LINE NUMBERS & TELEPROMPTER NAVIGATION
    # -----------------------------------------------------------------------
    def _update_line_gutter(self):
        """Synchronizes line numbers in the gutter."""
        content = self.code_text.get("1.0", tk.END)
        num_lines = content.count("\n")
        gutter_text = "\n".join(str(i) for i in range(1, num_lines + 1))

        self.line_gutter.config(state=tk.NORMAL)
        self.line_gutter.delete("1.0", tk.END)
        self.line_gutter.insert("1.0", gutter_text)
        self.line_gutter.config(state=tk.DISABLED)

    def _on_text_modified(self, event=None):
        self._update_line_gutter()

    def _on_click_line(self, event=None):
        """Highlights clicked line as teleprompter focus."""
        self.window.after(20, self._sync_active_line_tag)

    def _on_arrow_navigate(self, event=None):
        self.window.after(20, self._sync_active_line_tag)

    def _sync_active_line_tag(self):
        try:
            self.code_text.tag_remove("active_line", "1.0", tk.END)
            insert_idx = self.code_text.index(tk.INSERT)
            line_num = insert_idx.split(".")[0]
            start = f"{line_num}.0"
            end = f"{line_num}.end+1c"
            self.code_text.tag_add("active_line", start, end)
        except Exception:
            pass

    def font_zoom_in(self):
        if self.font_size < 24:
            self.font_size += 1
            self._apply_font_size()

    def font_zoom_out(self):
        if self.font_size > 8:
            self.font_size -= 1
            self._apply_font_size()

    def _apply_font_size(self):
        f = ("Consolas", self.font_size)
        self.code_text.config(font=f)
        self.line_gutter.config(font=f)
        self.font_lbl.config(text=f"{self.font_size}pt")

    # -----------------------------------------------------------------------
    # IN-DOCUMENT SEARCH
    # -----------------------------------------------------------------------
    def _on_search_change(self):
        query = self.search_entry.get().strip()
        self.code_text.tag_remove("search_match", "1.0", tk.END)
        self.code_text.tag_remove("search_current", "1.0", tk.END)
        self._search_matches = []
        self._current_match_idx = -1

        if not query or len(query) < 2:
            return

        start = "1.0"
        while True:
            pos = self.code_text.search(query, start, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            self.code_text.tag_add("search_match", pos, end)
            self._search_matches.append((pos, end))
            start = end

        if self._search_matches:
            self.search_next()

    def search_next(self):
        if not self._search_matches:
            self._on_search_change()
            if not self._search_matches:
                return

        self._current_match_idx = (self._current_match_idx + 1) % len(self._search_matches)
        pos, end = self._search_matches[self._current_match_idx]

        self.code_text.tag_remove("search_current", "1.0", tk.END)
        self.code_text.tag_add("search_current", pos, end)
        self.code_text.see(pos)
        self.code_text.mark_set(tk.INSERT, pos)
        self._sync_active_line_tag()

    # -----------------------------------------------------------------------
    # CODETANTRA GHOST TYPING & CODE INJECTION
    # -----------------------------------------------------------------------
    def _get_selected_or_all_code(self) -> str:
        """Returns highlighted code snippet, or entire document code."""
        if self.current_view_mode == "CODE":
            try:
                sel = self.code_text.get(tk.SEL_FIRST, tk.SEL_LAST)
                if sel.strip():
                    return sel.strip()
            except tk.TclError:
                pass
            return self.code_text.get("1.0", tk.END).strip()

        # In PDF Visual Mode: Grab code/text from active page
        if self.current_file and self.current_file.lower().endswith(".pdf") and self.pdf_doc:
            try:
                page_text = self.pdf_doc[self.pdf_current_page].get_text().strip()
                if page_text:
                    return page_text
            except Exception:
                pass

        return self.code_text.get("1.0", tk.END).strip()

    def arm_click_inject(self):
        """Arms Ultron to type code the moment user clicks inside CodeTantra."""
        code = self._get_selected_or_all_code()
        if not code:
            messagebox.showwarning("No Code", "Please load a document or enter code first.", parent=self.window)
            return

        success = code_agent_typer.arm_click_to_inject(code)
        if success:
            self.inject_status_lbl.config(
                text="🎯 ARMED! Click inside CodeTantra editor...",
                fg=self.neon_gold
            )
            self.inject_btn.config(bg=self.neon_gold, fg="#000000", text="🎯 ARMED (Click CodeTantra)")

    def type_all_code(self):
        """Immediately starts typing code into CodeTantra with countdown."""
        code = self._get_selected_or_all_code()
        if not code:
            messagebox.showwarning("No Code", "Please load a document or enter code first.", parent=self.window)
            return

        code_agent_typer.code_text = code
        started = code_agent_typer.start_typing(countdown=2)
        if started:
            self.inject_status_lbl.config(text="⚡ Typing in progress...", fg=self.neon_cyan)

    def abort_typing(self):
        """Immediately halts ghost typing."""
        code_agent_typer.abort()
        self.inject_status_lbl.config(text="⏹ Aborted", fg=self.neon_red)
        self.inject_btn.config(bg="#0284c7", fg="#ffffff", text="🎯 Click-to-Inject in CodeTantra")

    def copy_clean_code(self):
        """Copies code to clipboard with normalized spaces (no tabs)."""
        code = self._get_selected_or_all_code()
        if not code:
            return
        lines = code_agent_typer._prepare_code(code)
        clean = "\n".join(lines)
        pyperclip.copy(clean)
        self.inject_status_lbl.config(text="📋 Clean Code Copied to Clipboard!", fg=self.neon_cyan)
        self.window.after(2500, lambda: self.inject_status_lbl.config(text="Ready", fg=self.fg_muted))

    def _on_type_progress(self, line_idx, total_lines, pct, status_str):
        self.window.after(0, lambda: self.inject_status_lbl.config(
            text=f"Typing: Line {line_idx}/{total_lines} ({pct}%)",
            fg=self.neon_cyan
        ))

    def _on_type_status(self, status_str):
        def _update():
            self.inject_status_lbl.config(text=status_str[:40])
            if "ARMED" not in status_str:
                self.inject_btn.config(bg="#0284c7", fg="#ffffff", text="🎯 Click-to-Inject in CodeTantra")
        self.window.after(0, _update)


# ---------------------------------------------------------------------------
# SINGLETON LIFECYCLE
# ---------------------------------------------------------------------------
_doc_agent_instance = None
_doc_agent_lock = threading.Lock()


def open_doc_agent(parent_root=None, file_path=None):
    """Opens or restores the Always-On-Top Document Agent window."""
    global _doc_agent_instance
    with _doc_agent_lock:
        if _doc_agent_instance is not None:
            try:
                if _doc_agent_instance.window.winfo_exists():
                    _doc_agent_instance.show()
                    if file_path:
                        _doc_agent_instance.load_file(file_path)
                    return _doc_agent_instance
            except Exception:
                _doc_agent_instance = None

        _doc_agent_instance = DocAgentWindow(parent_root=parent_root)
        if file_path:
            _doc_agent_instance.load_file(file_path)
        return _doc_agent_instance


def get_doc_agent_instance():
    return _doc_agent_instance


def _kill_stale_instances():
    """Ensures no stale zombie doc_agent processes remain running in the background."""
    try:
        import psutil
        curr_pid = os.getpid()
        parent_pid = os.getppid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = proc.info['pid']
                if pid in (curr_pid, parent_pid):
                    continue
                name = (proc.info['name'] or '').lower()
                if name not in ('python.exe', 'pythonw.exe'):
                    continue
                cmdline = proc.info.get('cmdline') or []
                is_doc = any(arg.lower().endswith('doc_agent.py') for arg in cmdline)
                if is_doc:
                    proc.kill()
            except Exception:
                pass
    except Exception:
        pass


if __name__ == "__main__":
    _kill_stale_instances()
    app = open_doc_agent()
    app.window.mainloop()
