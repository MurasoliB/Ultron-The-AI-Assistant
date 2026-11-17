"""Native Windows UI Automation agent for the WhatsApp desktop app.

The Microsoft Store build of WhatsApp is a WebView2 host, but it still exposes
its whole interface through UI Automation: the search box, every chat row, the
conversation header, the message composer and the send button are real
automation elements with stable accessible names. Driving those elements is
faster and more durable than scripting the page, so no browser, extension or
loopback service is involved.
"""

import re
import secrets
import subprocess
import threading
import time

import psutil
import pyperclip
import uiautomation as auto


PROCESS_NAME = "WhatsApp.Root.exe"
WINDOW_NAME = "WhatsApp"
WINDOW_CLASS = "WinUIDesktopWin32WindowClass"
AUMID = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
SEARCH_PLACEHOLDER = "Search or start a new chat"
COMPOSER_PREFIX = "Type a message to"
DRAFT_TTL = 60

SEARCH_BOX_BAND = (100, 560, 100, 200)
CHAT_LIST_BAND = (40, 760, 200, 1400)
MIN_ROW_WIDTH = 200
ROW_WIDTH_RATIO = 0.7
LIST_WIDTH_RATIO = 0.45
NO_FLOOR = 10**6
CACHE_TTL = 1.5
FOCUS_SETTLE = 0.2
POLL_INTERVAL = 0.15
READY_POLL = 0.3
APP_DOCUMENT_ID = "RootWebArea"
SKIPPED_CONTAINERS = {
    "ToolBarControl",
    "TabControl",
    "MenuBarControl",
    "MenuControl",
}
_LIST_CONTAINER = re.compile(r"^(chat list|search results)", re.I)
MAX_DEPTH = 40
SECTION_LABELS = {
    "chats", "messages", "groups", "calls", "channels", "communities",
    "status", "meta ai", "media", "archived",
}

_TIME_LABEL = re.compile(
    r"^(?:"
    r"\d{1,2}:\d{2}\s*(?:am|pm)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|yesterday|today|last week|now"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r")[\s!]*$",
    re.I,
)
_UNREAD_PREFIX = re.compile(r"^\d+\s+unread\s+messages?\s+", re.I)
_TIME_TOKEN = re.compile(
    r"(?:^|\s)(?:"
    r"\d{1,2}:\d{2}\s*(?:am|pm)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|yesterday|today|last week"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r")(?=\s|$)",
    re.I,
)
_LOGIN_MARKERS = (
    "scan the qr code",
    "link your phone",
    "log in to whatsapp",
    "how to log in",
    "phone number to get started",
)
_CHAT_LIST_HEADER = re.compile(r"search or start a new chat", re.I)


class WhatsAppDesktopError(RuntimeError):
    """Raised when the desktop app cannot be reached or driven."""


_thread_state = threading.local()


def _ensure_com():
    """Initialize COM on the calling thread before touching UI Automation.

    COM apartments belong to a thread, and ULTRON runs commands on worker
    threads that never imported uiautomation, so a thread that has not called
    CoInitialize fails with "CoInitialize has not been called". Initializing
    once per thread and keeping it matches how those long lived threads work.
    """
    if getattr(_thread_state, "com_ready", False):
        return
    try:
        import pythoncom

        pythoncom.CoInitialize()
        _thread_state.com_owner = None
    except Exception:
        _thread_state.com_owner = auto.UIAutomationInitializerInThread()
    _thread_state.com_ready = True


def _rect(node):
    """Return a node's bounding rectangle, or None when it is not laid out."""
    try:
        rectangle = node.BoundingRectangle
    except Exception:
        return None
    if rectangle.width() <= 0 or rectangle.height() <= 0:
        return None
    return rectangle


def _name(node):
    try:
        return (node.Name or "").strip()
    except Exception:
        return ""


def _control_type(node):
    """Read a node's control type, reusing the value the walk already fetched."""
    cached = getattr(node, "_wa_type", None)
    if cached is not None:
        return cached
    try:
        return node.ControlTypeName
    except Exception:
        return ""


def _in_band(rectangle, band):
    left, right, top, bottom = band
    return (
        left < rectangle.left < right
        and top < rectangle.top < bottom
        and rectangle.width() > 20
    )


def _contains(outer, inner):
    """Report whether inner sits wholly inside outer."""
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and outer.right >= inner.right
        and outer.bottom >= inner.bottom
    )


class WhatsAppDesktop:
    """Drives the WhatsApp desktop app through UI Automation."""

    def __init__(self, process_name=PROCESS_NAME, auto_launch=True, search_timeout=2):
        self.process_name = process_name
        self.auto_launch = auto_launch
        self.search_timeout = search_timeout
        self.lock = threading.RLock()
        self.drafts = {}
        self.last_report = {}
        self.visible_labels = []
        self._cache = None
        self._cache_expires = 0.0
        self._app = None
        self._app_pid = None

    # -- process and window plumbing ------------------------------------------------

    def _pid(self):
        """Return the pid of the running WhatsApp host process, if any."""
        for process in psutil.process_iter(["pid", "name"]):
            try:
                if (process.info["name"] or "").lower() == self.process_name.lower():
                    return process.info["pid"]
            except Exception:
                continue
        return None

    def _launch(self):
        """Start the desktop app through its registered application id."""
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{AUMID}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise WhatsAppDesktopError(f"WhatsApp could not be started: {error}") from error
        deadline = time.time() + 45
        while time.time() < deadline:
            if self._pid():
                time.sleep(6)
                return True
            time.sleep(1)
        raise WhatsAppDesktopError(
            "WhatsApp did not start within 45 seconds. Open it from the Start "
            "menu and retry."
        )

    def _window(self, activate=False, timeout=8):
        """Return the WhatsApp window control, optionally bringing it forward.

        The window is found by enumerating the desktop and comparing process
        ids. Passing ProcessId to uiautomation does not filter anything, since
        extra search properties are kept only for debug output, and a search
        without a name would happily return an unrelated window.
        """
        _ensure_com()
        pid = self._pid()
        launched = False
        if pid is None:
            if not self.auto_launch:
                raise WhatsAppDesktopError(
                    "The WhatsApp desktop app is not running. Open it from the "
                    "Start menu and retry."
                )
            self._launch()
            launched = True
            pid = self._pid()
        window = self._find_window(pid, timeout=45 if launched else timeout)
        if window is None and self._restore_window(pid):
            window = self._find_window(pid, timeout=timeout)
        if window is not None and not self._has_content(window):
            # A minimised window keeps its handle but publishes no tree at all,
            # so it is restored and rechecked before the app is called dead.
            if self._restore_window(pid):
                self._focus(window)
                window = self._find_window(pid, timeout=3)
            if window is not None and not self._has_content(window):
                self.restart()
                window = self._find_window(self._pid(), timeout=45)
        if window is None:
            raise WhatsAppDesktopError(
                "WhatsApp is running but has no window on this desktop. Open it "
                "from the taskbar or the Start menu and retry."
            )
        if activate:
            self._focus(window)
        return window

    def _has_content(self, window):
        """Report whether the window still hosts a live interface.

        A WhatsApp process can survive with a window that exposes nothing at
        all after an unclean shutdown. Such a window will never build an
        accessibility tree, so it is cheaper and safer to relaunch the app than
        to keep polling a shell.
        """
        try:
            return bool(window.GetChildren())
        except Exception:
            return False

    def restart(self):
        """Relaunch WhatsApp and wait for its interface to come back."""
        pid = self._pid()
        self._app = None
        self._app_pid = None
        if pid:
            try:
                psutil.Process(pid).kill()
            except Exception:
                pass
            deadline = time.time() + 20
            while time.time() < deadline and self._pid():
                time.sleep(0.5)
        self._launch()

    def _find_window(self, pid, timeout=8):
        """Return the WhatsApp window owned by pid, or None."""
        deadline = time.time() + timeout
        while True:
            best = None
            best_area = 0
            for child in auto.GetRootControl().GetChildren():
                try:
                    if child.ProcessId != pid:
                        continue
                    name = _name(child)
                    class_name = child.ClassName or ""
                except Exception:
                    continue
                # The name has to match exactly. A browser window with WhatsApp
                # open in a tab is named after the tab, and picking it would
                # drive the wrong interface. This is checked before the
                # rectangle because a minimised window has no size yet and is
                # still the window worth restoring.
                if (
                    name.strip().casefold() == WINDOW_NAME.casefold()
                    or class_name == WINDOW_CLASS
                ):
                    return child
                rectangle = _rect(child)
                if rectangle is None:
                    continue
                area = rectangle.width() * rectangle.height()
                if area > best_area:
                    best, best_area = child, area
            if best is not None and best_area > 200000:
                return best
            if time.time() >= deadline:
                return None
            time.sleep(0.5)

    def _restore_window(self, pid):
        """Bring back a WhatsApp window that is running but hidden."""
        try:
            import win32con
            import win32gui
            import win32process
        except ImportError:
            return False
        hidden = []

        def collect(handle, _extra):
            if win32gui.IsWindowVisible(handle):
                return True
            try:
                if win32process.GetWindowThreadProcessId(handle)[1] == pid:
                    hidden.append(handle)
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(collect, None)
        except Exception:
            return False
        for handle in hidden:
            try:
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            except Exception:
                continue
        return bool(hidden)

    def _focus(self, window):
        """Raise the window so synthetic clicks and keystrokes reach WhatsApp."""
        try:
            window.SetActive()
        except Exception:
            pass
        time.sleep(FOCUS_SETTLE)

    def _keys(self, *keys):
        """Send keystrokes without uiautomation's per key pause.

        SendKeys waits half a second after every key by default, which is four
        seconds of dead time in a send and buys nothing: the keys go to the
        window that was just activated.
        """
        for key in keys:
            auto.SendKeys(key, waitTime=0)

    def _nodes(self, root, max_depth=MAX_DEPTH, fresh=False):
        """Depth first walk over the automation tree below root.

        Every node costs a cross process call into the web view, so the walk
        starts at the app's own document rather than at the window. WhatsApp is
        hosted by a WebView2 that publishes its browser chrome - tab strip,
        address bar, shadow overlays - and the host window duplicates the whole
        tree, which together are more expensive to read than the app itself.
        The result is cached briefly; callers that act on the app pass
        fresh=True or call _invalidate.
        """
        _ensure_com()
        if (
            not fresh
            and self._cache is not None
            and time.monotonic() < self._cache_expires
        ):
            return self._cache
        collected = []
        stack = [(self._app_root(root), 0)]
        while stack:
            current, depth = stack.pop()
            try:
                kind = current.ControlTypeName
            except Exception:
                continue
            try:
                current._wa_type = kind
            except Exception:
                pass
            if kind in SKIPPED_CONTAINERS:
                continue
            try:
                children = current.GetChildren()
            except Exception:
                continue
            for child in children:
                collected.append(child)
                if depth + 1 < max_depth:
                    stack.append((child, depth + 1))
        self._cache = collected
        self._cache_expires = time.monotonic() + CACHE_TTL
        return collected

    def _app_root(self, window, max_depth=20):
        """Return the document that hosts the WhatsApp interface.

        Reading the automation id costs a call, so it is only done for document
        nodes, of which there are a handful. The answer is remembered because
        the descent is a few dozen calls that would otherwise be repeated before
        every walk, and it only changes when the app itself restarts. Falling
        back to the window keeps the caller working if a build ever renames it.
        """
        pid = self._pid()
        if self._app is not None and self._app_pid == pid:
            return self._app
        found = None
        try:
            stack = [(window, 0)]
            while stack:
                current, depth = stack.pop()
                try:
                    if current.AutomationId == APP_DOCUMENT_ID:
                        found = current
                        break
                except Exception:
                    continue
                if depth >= max_depth:
                    continue
                try:
                    for child in current.GetChildren():
                        stack.append((child, depth + 1))
                except Exception:
                    continue
        except Exception:
            found = None
        self._app = found
        self._app_pid = pid
        return found or window

    def _invalidate(self):
        """Force the next walk to hit the app again."""
        self._cache = None
        self._cache_expires = 0.0

    def _unique(self, items):
        """Drop the repeated copies the WebView2 host publishes for one element.

        Accepts a node or a (node, rectangle) pair. Reading a name and a
        rectangle costs a call each, so this is only ever used on a handful of
        candidates, never on a whole walk.
        """
        seen = set()
        kept = []
        for item in items:
            node, rectangle = item if isinstance(item, tuple) else (item, None)
            if rectangle is None:
                rectangle = _rect(node)
            key = (
                _control_type(node),
                _name(node),
                None if rectangle is None else (
                    rectangle.left,
                    rectangle.top,
                    rectangle.width(),
                    rectangle.height(),
                ),
            )
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        return kept

    def _descendants(self, node, max_depth=10):
        collected = []
        stack = [(node, 0)]
        while stack:
            current, depth = stack.pop()
            if depth >= max_depth:
                continue
            try:
                children = current.GetChildren()
            except Exception:
                continue
            for child in children:
                collected.append(child)
                stack.append((child, depth + 1))
        return collected

    def _wait_for(self, probe, timeout=12, interval=POLL_INTERVAL):
        """Poll probe until it returns a truthy value or the timeout expires."""
        deadline = time.time() + timeout
        while True:
            self._invalidate()
            value = probe()
            if value:
                return value
            if time.time() >= deadline:
                return None
            time.sleep(interval)

    # -- element lookups -----------------------------------------------------------

    def _search_box(self, nodes, window=None):
        """Return the chat search input.

        The placeholder is only exposed while the field is empty; once text is
        typed the element keeps its geometry but loses the name, so the box is
        recognised by its position near the top of the chat column.
        """
        band = self._search_band(window)
        for node in nodes:
            if _control_type(node) != "EditControl":
                continue
            rectangle = _rect(node)
            if rectangle is None:
                continue
            if _in_band(rectangle, band) or _name(node) == SEARCH_PLACEHOLDER:
                return node
        return None

    def _search_band(self, window):
        """Region holding the search box, relative to the window size."""
        rectangle = _rect(window) if window is not None else None
        if rectangle is None:
            return SEARCH_BOX_BAND
        return (
            rectangle.left,
            rectangle.left + int(rectangle.width() * LIST_WIDTH_RATIO),
            rectangle.top,
            rectangle.top + int(rectangle.height() * 0.35),
        )

    def _composer(self, nodes):
        """Return the message composer of the open conversation."""
        for node in nodes:
            if _control_type(node) == "EditControl" and _name(node).startswith(COMPOSER_PREFIX):
                return node
        return None

    def _composer_value(self, node):
        if node is None:
            return None
        try:
            return node.GetValuePattern().Value
        except Exception:
            return None

    def _send_button(self, nodes):
        for node in nodes:
            if _control_type(node) == "ButtonControl" and _name(node) == "Send":
                if _rect(node) is not None:
                    return node
        return None

    def _row_title(self, row):
        """Read a chat row's title.

        WhatsApp builds the accessible name as title, then time, then preview,
        so the title is what precedes the first timestamp. Falling back to the
        topmost wide text inside the row covers rows whose name carries no time
        at all.
        """
        name = _UNREAD_PREFIX.sub("", _name(row)).strip()
        if name:
            match = _TIME_TOKEN.search(name)
            if match and match.start() > 0:
                return name[: match.start()].strip()
            if not match:
                return name
        return self._row_text(row)

    def _row_text(self, row):
        """Read the topmost wide text inside a row."""
        row_rect = _rect(row)
        band_bottom = None if row_rect is None else row_rect.top + row_rect.height() * 0.5
        candidates = []
        for node in self._descendants(row, 8):
            if _control_type(node) != "TextControl":
                continue
            rectangle = _rect(node)
            if rectangle is None:
                continue
            if band_bottom is not None and rectangle.top > band_bottom:
                continue
            if rectangle.height() < 16 or rectangle.width() < 30:
                continue
            label = _name(node)
            if not label or _TIME_LABEL.match(label) or _UNREAD_PREFIX.match(label):
                continue
            candidates.append((rectangle.top, rectangle.left, label))
        if not candidates:
            return ""
        topmost = min(top for top, _left, _label in candidates)
        finalists = [
            (left, label)
            for top, left, label in candidates
            if top <= topmost + 5
        ]
        return min(finalists)[1]

    def _row_labels(self, row):
        """Candidate names a contact query may match against for one chat row."""
        row_name = _name(row)
        candidates = []
        if row_name:
            candidates.append(_UNREAD_PREFIX.sub("", row_name))
        title = self._row_title(row)
        if title:
            candidates.insert(0, title)
        return [candidate for candidate in candidates if candidate]

    def _chat_rows(self, nodes, window=None):
        """Return the chat rows of the left column or of the search results.

        The rows are taken from the list container WhatsApp labels "Chat list"
        or "Search results." rather than from raw coordinates, because that pane
        is a fixed pixel width and does not scale with the window, so a
        coordinate share of the window either misses every row or reaches into
        the conversation. Inside a container, the second list item WhatsApp
        nests in each row for its title and timestamp is dropped by comparing
        widths and containment.
        """
        grids = self._unique([
            node
            for node in nodes
            if _control_type(node) == "DataGridControl" and _LIST_CONTAINER.match(_name(node))
        ])
        if grids:
            rows = []
            for grid in grids:
                grid_rect = _rect(grid)
                if grid_rect is None:
                    continue
                # The rows are already in the collected walk, so they are read
                # from there instead of walking the grid a second time.
                for row in nodes:
                    if _control_type(row) != "DataItemControl":
                        continue
                    rectangle = _rect(row)
                    if rectangle is None or not rectangle.width() > 20:
                        continue
                    if not _contains(grid_rect, rectangle):
                        continue
                    rows.append((row, rectangle))
            return self._keep_outermost(self._unique(rows))

        left, right, top, bottom = self._list_band(window)
        fallback = [
            (node, rectangle)
            for node, rectangle in (
                (node, _rect(node))
                for node in nodes
                if _control_type(node) == "DataItemControl"
            )
            if rectangle is not None
            and rectangle.width() > 20
            and left <= rectangle.left <= right
            and top <= rectangle.top <= bottom
        ]
        return self._keep_outermost(self._unique(fallback))

    def _keep_outermost(self, candidates):
        """Drop narrow and nested list items, keeping one entry per chat."""
        if not candidates:
            return []
        widest = max(rectangle.width() for _, rectangle in candidates)
        floor = max(MIN_ROW_WIDTH, widest * ROW_WIDTH_RATIO)
        candidates = [
            (node, rectangle)
            for node, rectangle in candidates
            if rectangle.width() >= floor
        ]
        kept = []
        ordered = sorted(
            candidates, key=lambda item: item[1].width() * item[1].height(), reverse=True
        )
        for node, rectangle in ordered:
            if any(_contains(outer, rectangle) for _, outer in kept):
                continue
            kept.append((node, rectangle))
        kept.sort(key=lambda item: item[1].top)
        return [node for node, _ in kept]

    def _list_band(self, window):
        """Left column bounds for the current window size.

        Only the horizontal range is bounded. The web view reports unclipped
        page coordinates, so rows can sit far below the window's bottom edge
        and a vertical limit would throw most of the chat list away. Rows are
        virtualised by the app anyway, so everything reported is on screen.
        """
        rectangle = _rect(window) if window is not None else None
        if rectangle is None:
            return CHAT_LIST_BAND
        return (
            rectangle.left,
            rectangle.left + int(rectangle.width() * LIST_WIDTH_RATIO),
            0,
            NO_FLOOR,
        )

    # -- low level input -----------------------------------------------------------

    def _paste_into(self, element, text):
        """Put text into a focused WhatsApp field through the clipboard.

        The composer is a contenteditable that ignores UI Automation value
        writes, so the clipboard is the only reliable way to enter text. It also
        keeps punctuation and emoji intact, which synthetic keystrokes would not.
        """
        element.Click()
        time.sleep(FOCUS_SETTLE)
        self._keys("{Ctrl}a", "{BACK}")
        pyperclip.copy(text)
        self._keys("{Ctrl}v")
        self._invalidate()

    def _clear_composer(self):
        window = self._window(activate=True)
        nodes = self._nodes(window)
        composer = self._composer(nodes)
        if composer is None:
            return False
        composer.Click()
        self._keys("{Ctrl}a", "{BACK}")
        return True

    # -- chat navigation -----------------------------------------------------------

    def _await_ready(self, window, timeout=45):
        """Wait until WhatsApp publishes its chat list, then return the tree.

        A freshly launched app needs a few seconds before its web view builds an
        accessibility tree, so a single sample would report "still loading" for
        an app that is about to be perfectly usable.
        """
        deadline = time.time() + timeout
        while True:
            nodes = self._nodes(window)
            state = self._session_state(nodes, window)
            if state != "not_ready" or time.time() >= deadline:
                return nodes, state
            time.sleep(READY_POLL)

    def _session_state(self, nodes, window=None):
        """Classify the app as logged in, logged out, or still loading.

        The chat list, its search box and the composer only exist once the
        account is signed in, so those are checked before the QR code wording
        is trusted; the encryption banner also says "use WhatsApp on your
        phone", which used to read as a login screen.
        """
        if self._composer(nodes) is not None or self._search_box(nodes, window) is not None:
            return "logged_in"
        if self._chat_rows(nodes, window):
            return "logged_in"
        names = [_name(node).lower() for node in nodes]
        blob = " | ".join(name for name in names if name)
        if any(marker in blob for marker in _LOGIN_MARKERS):
            return "logged_out"
        return "not_ready"

    def _find_rows(self, contact, choice_index=None):
        """Search for a contact and return the rows WhatsApp offers.

        Matching uses the row's own accessible name with any unread badge
        removed, because WhatsApp builds that name from title, time and preview
        and the title always leads.
        """
        window = self._window(activate=True)
        nodes, state = self._await_ready(window)
        if state != "logged_in":
            raise WhatsAppContactNotFound(contact, self._chat_list_labels(window))
        nodes = self._nodes(window)
        rows = self._chat_rows(nodes, window)
        search_box = self._search_box(nodes, window)
        if search_box is None:
            raise WhatsAppDesktopError(
                "WhatsApp's search box was not found, so the contact could not "
                "be looked up. Switch to the Chats tab and retry."
            )
        self._paste_into(search_box, contact)

        def probe():
            return self._chat_rows(self._nodes(window), window) or None

        rows = self._wait_for(probe, timeout=10) or []
        wanted = contact.casefold()
        matches = []
        for row in rows:
            labels = self._row_labels(row)
            if any(label.casefold().startswith(wanted) for label in labels):
                matches.append((row, self._row_title(row) or labels[0]))

        if not matches:
            self.visible_labels = [
                self._row_title(row) or _UNREAD_PREFIX.sub("", _name(row))
                for row in rows
                if self._row_title(row)
            ]
        if choice_index is not None:
            if not 0 <= choice_index < len(matches):
                return [], []
            matches = [matches[choice_index]]
        return [row for row, _ in matches], [title for _, title in matches]

    def _close_search(self):
        """Leave the search overlay so the chat list and composer are usable."""
        try:
            window = self._window(activate=True)
        except WhatsAppDesktopError:
            return
        self._focus(window)
        self._keys("{ESC}")

    def _open_chat(self, contact, choice_index=None):
        rows, labels = self._find_rows(contact, choice_index)
        if not rows:
            self._close_search()
            raise WhatsAppContactNotFound(contact, labels or self._chat_list_labels())
        if len(rows) > 1 and choice_index is None:
            self._close_search()
            return None, labels
        window = self._window(activate=True)
        rows[0].Click()

        def composer_ready():
            return self._composer(self._nodes(window))

        composer = self._wait_for(composer_ready, timeout=15)
        if composer is None:
            self._close_search()
            raise WhatsAppDesktopError(
                f"The chat with {labels[0] if labels else contact} did not open. "
                "Retry in a moment."
            )
        title = _name(composer)
        if title.startswith(COMPOSER_PREFIX):
            title = title[len(COMPOSER_PREFIX):].strip()
        return title, labels

    def _chat_list_labels(self, window=None):
        """List the chats behind the chat list, used when a search finds nothing."""
        try:
            window = window or self._window()
            rows = self._chat_rows(self._nodes(window), window)
        except WhatsAppDesktopError:
            return []
        labels = []
        for row in rows:
            label = self._row_title(row) or _UNREAD_PREFIX.sub("", _name(row))
            if label and label.casefold() not in SECTION_LABELS:
                labels.append(label)
        return labels

    # -- operations ----------------------------------------------------------------

    def prepare(self, contact, message, request_id=None, choice_index=None):
        """Open a chat and leave the message sitting in the composer."""
        message = (message or "").strip()
        if not message:
            return {"status": "error", "message": "The message text was empty."}
        with self.lock:
            try:
                title, labels = self._open_chat(contact, choice_index)
            except WhatsAppContactNotFound as error:
                return {
                    "status": "contact_not_found",
                    "visible_results": error.labels,
                    "diagnostics": self.last_report,
                }
            if title is None:
                return {"status": "multiple_matches", "matches": [
                    {"label": label, "name": label} for label in labels
                ]}

            window = self._window()
            nodes = self._nodes(window)
            if self._session_state(nodes, window) != "logged_in":
                return {
                    "status": "logged_out",
                    "message": "WhatsApp is logged out.",
                }
            composer = self._composer(nodes)
            if composer is None:
                return {"status": "not_ready", "message": "WhatsApp is still loading."}
            self._paste_into(composer, message)

            written = self._poll_value(composer, message)
            if not written:
                written = self._wait_for(
                    lambda: self._value_equals(self._composer(self._nodes(window)), message),
                    timeout=8,
                )
            if not written:
                self._clear_composer()
                return {
                    "status": "not_ready",
                    "message": "WhatsApp did not accept the typed message, so nothing was sent.",
                }
            draft_id = request_id or secrets.token_urlsafe(18)
            self.drafts[draft_id] = {
                "contact": title or contact,
                "message": message,
                "created": time.time(),
            }
            self._expire_drafts()
            return {"status": "ready", "contact": title or contact, "draft_id": draft_id}

    def _value_equals(self, composer, expected):
        value = self._composer_value(composer)
        return value is not None and value.strip() == expected.strip()

    def _poll_value(self, composer, expected, timeout=1.0):
        """Wait for a held composer element to report the expected text.

        Reading the value of an element the walk already found costs a single
        call, where re-reading the app costs a walk. The held element is still
        used as the first check on every slower path, so a stale reference
        degrades to the old behaviour instead of failing.
        """
        deadline = time.monotonic() + timeout
        while True:
            if self._value_equals(composer, expected):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _expire_drafts(self):
        cutoff = time.time() - DRAFT_TTL
        for draft_id in [key for key, draft in self.drafts.items() if draft["created"] < cutoff]:
            self.drafts.pop(draft_id, None)

    def send(self, contact=None, message=None, draft_id=None, confirmed=False):
        """Press WhatsApp's send button for a prepared draft."""
        if not confirmed:
            return {"status": "error", "message": "Sending requires confirmation."}
        with self.lock:
            self._expire_drafts()
            draft = self.drafts.pop(draft_id or "", None)
            if draft is None:
                return {
                    "status": "error",
                    "message": "The prepared WhatsApp draft expired. Prepare it again.",
                }
            window = self._window(activate=True)
            composer = self._composer(self._nodes(window))
            if not self._value_equals(composer, draft["message"]):
                composer = self._composer(self._nodes(window))
                if composer is not None:
                    self._paste_into(composer, draft["message"])
            send_button = self._send_button(self._nodes(window))
            if send_button is None:
                send_button = self._wait_for(
                    lambda: self._send_button(self._nodes(window)), timeout=8
                )
            if send_button is None:
                self._clear_composer()
                return {
                    "status": "error",
                    "message": "WhatsApp never enabled its send button, so nothing was sent.",
                }
            send_button.Click()

            def cleared():
                # A sent message empties the composer, so the held element
                # answers the question without reading the conversation again.
                if composer is not None:
                    value = self._composer_value(composer)
                    if value is not None and not value.strip():
                        return True
                return self._landed(window, draft["message"])

            if not self._wait_for(cleared, timeout=15):
                return {
                    "status": "error",
                    "message": "WhatsApp did not confirm the send. Check the chat before retrying.",
                }
            return {"status": "sent", "contact": draft["contact"]}

    def _landed(self, window, message):
        """Report whether the sent text is now in the conversation."""
        nodes = self._nodes(window)
        composer = self._composer(nodes)
        if composer is None:
            return True
        value = self._composer_value(composer)
        if value is None or not value.strip():
            return True
        return any(message in _name(node) for node in nodes)

    def cancel(self, draft_id=None):
        """Drop a prepared draft and wipe it from the composer."""
        with self.lock:
            draft = self.drafts.pop(draft_id or "", None)
            if draft is None:
                return {"status": "cancelled"}
            try:
                window = self._window()
                if self._value_equals(self._composer(self._nodes(window)), draft["message"]):
                    self._clear_composer()
            except WhatsAppDesktopError:
                pass
            return {"status": "cancelled"}

    def test(self):
        """Report whether the app is open and signed in."""
        try:
            window = self._window(activate=True)
        except WhatsAppDesktopError as error:
            return {"status": "not_open", "message": str(error)}
        _nodes, state = self._await_ready(window)
        if state == "logged_out":
            return {
                "status": "logged_out",
                "message": "WhatsApp is open but signed out. Sign in manually; "
                "ULTRON will not bypass login.",
            }
        if state == "logged_in":
            return {"status": "logged_in"}
        return {
            "status": "not_ready",
            "message": "WhatsApp is still loading. Wait for your chats to appear, then retry.",
        }

    def diagnose(self):
        """Describe what the app currently exposes, for troubleshooting."""
        try:
            window = self._window(activate=True)
        except WhatsAppDesktopError as error:
            return {"status": "not_open", "diagnostics": {"error": str(error)}}
        nodes, state = self._await_ready(window)
        rows = self._chat_rows(nodes, window)
        unread = [
            self._row_title(row)
            for row in rows
            if _UNREAD_PREFIX.match(_name(row)) and self._row_title(row)
        ]
        titles = [
            self._row_title(row) or _name(row)
            for row in rows[:6]
            if (self._row_title(row) or "").casefold() not in SECTION_LABELS
        ]
        report = {
            "window": _name(window),
            "process_id": self._pid(),
            "nodes": len(nodes),
            "search_found": self._search_box(nodes, window) is not None,
            "chat_rows": len(rows),
            "row_titles": titles,
            "unread": unread,
            "composer_open": self._composer(nodes) is not None,
            "logged_in": state == "logged_in",
        }
        self.last_report = report
        return {"status": "ok", "diagnostics": report}

    def list_chats(self, limit=15):
        """Return the chat rows currently visible in the left column."""
        try:
            window = self._window()
        except WhatsAppDesktopError as error:
            raise WhatsAppDesktopError(str(error)) from error
        rows = self._chat_rows(self._await_ready(window)[0], window)
        chats = []
        for row in rows[:limit]:
            title = self._row_title(row) or _UNREAD_PREFIX.sub("", _name(row))
            if not title or title.casefold() in SECTION_LABELS:
                continue
            chats.append({
                "title": title,
                "unread": _UNREAD_PREFIX.match(_name(row)) is not None,
            })
        return chats

    # -- compatibility surface used by whatsapp_agent ------------------------------

    def request(self, operation, **values):
        """Dispatch one agent operation by name."""
        operations = {
            "test": lambda: self.test(),
            "diagnose": lambda: self.diagnose(),
            "prepare": lambda: self.prepare(**values),
            "send": lambda: self.send(**values),
        }
        handler = operations.get(operation)
        if handler is None:
            return {"status": "error", "message": f"Unknown operation: {operation}"}
        try:
            return handler()
        except WhatsAppDesktopError as error:
            return {"status": "error", "message": str(error)}

    def notify(self, operation, **values):
        """Handle a fire and forget instruction such as cancelling a draft."""
        if operation == "cancel":
            return self.cancel(**values)
        return {"status": "ignored", "operation": operation}


class WhatsAppContactNotFound(WhatsAppDesktopError):
    """Raised when WhatsApp offers no chat matching the requested contact."""

    def __init__(self, contact, labels=None):
        self.contact = contact
        self.labels = [label for label in (labels or []) if label]
        super().__init__(f"No WhatsApp chat matched {contact}.")