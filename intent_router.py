import re
import ntpath
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    OPEN_APPLICATION = "OPEN_APPLICATION"
    OPEN_URL = "OPEN_URL"
    OPEN_FOLDER = "OPEN_FOLDER"
    OPEN_FILE = "OPEN_FILE"
    OPEN_PROJECT = "OPEN_PROJECT"
    SEARCH_WEB = "SEARCH_WEB"
    SEARCH_LOCAL = "SEARCH_LOCAL"
    CLOSE_APPLICATION = "CLOSE_APPLICATION"
    BROWSER_CONTROL = "BROWSER_CONTROL"
    CREATE_FOLDER = "CREATE_FOLDER"
    CREATE_FILE = "CREATE_FILE"
    MOVE_FILE = "MOVE_FILE"
    COPY_FILE = "COPY_FILE"
    RENAME_FILE = "RENAME_FILE"
    VOLUME_CONTROL = "VOLUME_CONTROL"
    BRIGHTNESS_CONTROL = "BRIGHTNESS_CONTROL"
    SCREENSHOT = "SCREENSHOT"
    SHOW_PROJECT_FOLDER = "SHOW_PROJECT_FOLDER"
    OTHER = "OTHER"


@dataclass(frozen=True)
class CommandIntent:
    intent: Intent
    target: str = ""
    browser: str = ""
    application: str = ""


_BROWSER_RE = re.compile(
    r"\b(?:in|on|using|with)\s+"
    r"(brave(?:\s+browser)?|google\s+chrome|chrome|"
    r"microsoft\s+edge|edge|mozilla\s+firefox|firefox)\s*$",
    re.IGNORECASE,
)
_KNOWN_APPS = {
    "calculator", "calc", "chrome", "google chrome", "brave",
    "brave browser", "firefox", "mozilla firefox", "edge", "microsoft edge",
    "vs code", "vscode", "visual studio code", "code", "notepad",
    "file explorer", "explorer", "task manager", "settings", "terminal",
    "command prompt", "spotify", "copilot", "microsoft store", "word",
    "microsoft word", "excel", "microsoft excel", "powerpoint", "outlook",
    "onenote", "camera",
}
_WEBSITES = {
    "chatgpt": "https://chatgpt.com",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://www.x.com",
    "x": "https://www.x.com",
    "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "flipkart": "https://www.flipkart.com",
}
_OPEN_VERB = (
    r"(?:open(?:\s+up)?|show(?:\s+me)?|launch|start|run|bring\s+up|"
    r"go\s+to|go|take\s+me\s+to|navigate\s+to|switch\s+to|enter|browse|"
    r"visit|explore|get\s+to|head\s+to|jump\s+to)"
)
# Filler that describes the kind of location rather than naming it.
_LOCATION_NOISE = re.compile(
    r"\b(?:folder|folders|directory|directories|dir|place|location|path)\b",
    re.I,
)
_LEADING_NOISE = re.compile(
    r"^(?:please\s+|the\s+|a\s+|an\s+|my\s+|our\s+|all\s+|whole\s+|"
    r"entire\s+|folder\s+|directory\s+|dir\s+)+",
    re.I,
)
# Windows shell folders and drive letters must never be treated as applications.
_SHELL_FOLDERS = {
    "desktop", "documents", "downloads", "pictures", "videos", "music",
    "home", "user profile", "favorites", "recent", "recycle bin",
    "this pc", "this computer", "my computer", "pc", "computer", "laptop",
}
_DRIVE_TARGET = re.compile(r"^(?:the\s+)?[a-z](?:\s*:\s*|\s+(?:drive|partition))?\.?$", re.I)


def _clean_name(value: str) -> str:
    return _LEADING_NOISE.sub("", _LOCATION_NOISE.sub("", value or "")).strip().lower()


def classify_intent(text: str, context: str = "") -> CommandIntent:
    """Classify only high-confidence command forms; leave ambiguous text to existing routing."""
    command = re.sub(r"\s+", " ", (text or "").strip())
    if not command:
        return CommandIntent(Intent.OTHER)
    command = re.sub(r"^(?:hey\s+)?(?:ultron|jarvis)[, ]*", "", command, flags=re.I)
    command = re.sub(r"\byou\s+deal\b|\byou\s+tube\b", "youtube", command, flags=re.I)
    command = re.sub(r"[.!?]+$", "", command).strip()

    recent_folders = []
    recent_line = re.search(r"(?im)^Recent folders:\s*(.+)$", context or "")
    if recent_line:
        recent_folders = [path.strip() for path in recent_line.group(1).split(";") if path.strip()]
    current_workspace = ""
    workspace_match = re.search(
        r"(?im)^Current project workspace:.*?;\s*([A-Za-z]:\\[^)\r\n]+)\)?$",
        context or "",
    )
    if workspace_match:
        current_workspace = workspace_match.group(1).strip()
    current_website = ""
    website_match = re.search(r"(?im)^Last requested website:\s*(https?://\S+)", context or "")
    if website_match:
        current_website = website_match.group(1).strip()

    if re.search(
        r"\b(?:open|show|go to)\b.*\b(?:this|the)\s+website\b.*"
        r"\b(?:currently\s+)?(?:working\s+with|working\s+on)\b",
        command,
        re.I,
    ):
        return CommandIntent(Intent.OPEN_URL, current_website or "current website")

    local_search = re.match(
        r"^(?:find|search(?:\s+for)?)\s+(.+?)\s+"
        r"(?:on|in)\s+(?:my\s+)?(?:computer|pc|laptop|files?)$",
        command,
        re.I,
    )
    if local_search:
        return CommandIntent(Intent.SEARCH_LOCAL, local_search.group(1).strip())

    if re.match(r"^create\s+(?:a\s+)?folder\b", command, re.I):
        return CommandIntent(Intent.CREATE_FOLDER)
    if re.match(r"^(?:create|make)\s+(?:a\s+)?(?:new\s+)?file\b", command, re.I):
        return CommandIntent(Intent.CREATE_FILE)
    if re.match(r"^move\s+(?:the\s+)?(?:file\s+)?", command, re.I):
        return CommandIntent(Intent.MOVE_FILE)
    if re.match(r"^copy\s+(?:the\s+)?(?:file\s+)?", command, re.I):
        return CommandIntent(Intent.COPY_FILE)
    if re.match(r"^rename\s+(?:the\s+)?(?:file\s+)?", command, re.I):
        return CommandIntent(Intent.RENAME_FILE)
    if re.search(r"\b(?:volume|sound)\b", command, re.I) and re.search(
        r"\b(?:increase|raise|up|decrease|lower|down|mute|unmute|set)\b",
        command,
        re.I,
    ):
        return CommandIntent(Intent.VOLUME_CONTROL)
    if re.search(r"\bbrightness\b", command, re.I):
        return CommandIntent(Intent.BRIGHTNESS_CONTROL)
    if re.search(r"\b(?:screenshot|capture screen)\b", command, re.I):
        return CommandIntent(Intent.SCREENSHOT)

    search_browser = ""
    search_browser_match = _BROWSER_RE.search(command)
    search_command = command
    if search_browser_match:
        search_browser = search_browser_match.group(1).strip()
        search_command = command[:search_browser_match.start()].strip()

    web_search = re.match(
        r"^(?:search\s+(?:the\s+)?(?:web|internet|google)|google\s+search)"
        r"\s+(?:for\s+)?(.+)$",
        search_command,
        re.I,
    )
    if web_search:
        return CommandIntent(
            Intent.SEARCH_WEB,
            web_search.group(1).strip(),
            search_browser,
        )

    lowered = command.lower()

    if re.search(
        r"\b(?:show|tell me|what is|what's)\b.*\b(?:current|active|working)\s+project\s+folder\b",
        command,
        re.I,
    ):
        return CommandIntent(Intent.SHOW_PROJECT_FOLDER, current_workspace or "ultron")
    close_match = re.match(r"^close\s+(?:the\s+)?(.+)$", command, re.I)
    if close_match:
        target = close_match.group(1).strip()
        if target.lower() in _KNOWN_APPS:
            return CommandIntent(Intent.CLOSE_APPLICATION, target)

    verb_match = re.match(rf"^{_OPEN_VERB}\s+(.+)$", command, re.I)
    if not verb_match:
        if lowered.startswith(("open folder ", "open the folder ")):
            verb_match = re.match(r"^open\s+(?:the\s+)?folder\s+(.+)$", command, re.I)
        else:
            return CommandIntent(Intent.OTHER)
    target_text = verb_match.group(1).strip()
    if re.search(r"\b(?:and|then|also)\b", target_text, re.I):
        return CommandIntent(Intent.OTHER, target_text)

    browser = ""
    browser_match = _BROWSER_RE.search(target_text)
    if browser_match:
        browser = browser_match.group(1).strip()
        target_text = target_text[:browser_match.start()].strip()

    target_text = re.sub(r"^(?:the\s+)?website\s+", "", target_text, flags=re.I)
    target_text = re.sub(r"^(?:my\s+)?", "", target_text, flags=re.I).strip()
    target_text = re.sub(r"\s+again$", "", target_text, flags=re.I).strip()
    target_text = re.sub(r"^the\s+", "", target_text, flags=re.I).strip()
    # "the downloads folder" and "downloads directory" name the same folder.
    had_explicit_folder_word = bool(_LOCATION_NOISE.search(target_text))
    target_text = _LEADING_NOISE.sub("", _LOCATION_NOISE.sub("", target_text)).strip()
    target_lower = target_text.lower().strip("\"'")

    if target_lower in {"it", "that", "this", "it instead", "that instead"} and browser:
        previous_task = re.search(
            r"(?im)^Current task:\s*(.+)$",
            context or "",
        )
        if previous_task:
            previous = classify_intent(previous_task.group(1), context="")
            if previous.intent == Intent.OPEN_URL:
                return CommandIntent(Intent.OPEN_URL, previous.target, browser)
            if previous.intent == Intent.SEARCH_WEB:
                return CommandIntent(Intent.SEARCH_WEB, previous.target, browser)
        if current_website:
            return CommandIntent(Intent.OPEN_URL, current_website, browser)
        return CommandIntent(Intent.OTHER, target_text, browser)

    followup_editor = re.match(
        r"^(it|that|this|the previous one)\s+in\s+(.+)$",
        target_text,
        re.I,
    )
    if followup_editor:
        editor = followup_editor.group(2).strip()
        if recent_folders:
            return CommandIntent(
                Intent.OPEN_PROJECT, recent_folders[-1], application=editor,
            )
        return CommandIntent(Intent.OPEN_PROJECT, target_text, application=editor)

    if target_lower in {"it", "that", "this", "that folder", "this folder"}:
        # "that folder" names a folder even though the folder word is stripped
        # from the target above; a bare "that" still means the current project.
        if "folder" in target_lower or had_explicit_folder_word:
            return CommandIntent(
                Intent.OPEN_FOLDER,
                recent_folders[-1] if recent_folders else target_text,
                browser,
            )
        return CommandIntent(Intent.OPEN_PROJECT, target_text, browser, "VS Code" if browser == "" and "code" in lowered else "")

    if target_lower in {
        "my project", "project", "the project i'm working on",
        "the project i am working on", "project i'm working on",
        "the project i'm working on",
    }:
        return CommandIntent(
            Intent.OPEN_PROJECT,
            current_workspace or "ultron",
            application="VS Code" if "code" in lowered else "",
        )

    project = re.match(
        r"^(?:the\s+)?(?:(.+?)\s+project|project\s+(.+?))(?:\s+in\s+(.+))?$",
        target_text,
        re.I,
    )
    app_in_folder = re.match(
        r"^(.*?)\s+in\s+(?:my\s+)?(.+?)(?:\s+folder)?$",
        target_text,
        re.I,
    )
    if project:
        name = (project.group(1) or project.group(2) or "").strip()
        editor = project.group(3) or ("VS Code" if "vs code" in lowered or "vscode" in lowered else "")
        return CommandIntent(Intent.OPEN_PROJECT, name, browser, editor.strip())
    if app_in_folder and app_in_folder.group(1).strip().lower() in _KNOWN_APPS:
        app = app_in_folder.group(1).strip()
        folder = app_in_folder.group(2).strip()
        return CommandIntent(Intent.OPEN_PROJECT, folder, browser, app)

    recent_match = next(
        (
            folder for folder in reversed(recent_folders)
            if target_lower == ntpath.basename(folder).lower()
        ),
        "",
    )
    if recent_match:
        return CommandIntent(Intent.OPEN_FOLDER, recent_match, browser)

    # Shell folders and drives are locations, never applications, so they are
    # decided before the website and installed-application fallbacks.
    if _DRIVE_TARGET.match(target_lower):
        return CommandIntent(Intent.OPEN_FOLDER, target_text, browser)
    if target_lower in _SHELL_FOLDERS or (
        had_explicit_folder_word and target_lower
    ):
        return CommandIntent(Intent.OPEN_FOLDER, target_text, browser)

    # "notes in documents" names a location, not an application.
    place_match = re.match(
        r"^(?P<name>.+?)\s+(?:in|inside|under|within)\s+(?:my\s+)?(?P<place>[^,]+)$",
        target_text,
        re.I,
    )
    if place_match and _clean_name(place_match.group("place")) in _SHELL_FOLDERS:
        return CommandIntent(Intent.OPEN_FOLDER, target_text, browser)

    if browser or target_lower in _WEBSITES or target_lower.startswith(("http://", "https://", "www.")):
        return CommandIntent(Intent.OPEN_URL, target_text, browser)
    if re.fullmatch(
        r"(?:[a-z0-9-]+\.)+(?:com|org|net|io|ai|in|co|edu|gov|dev|app|uk)(?:/.*)?",
        target_lower,
    ):
        return CommandIntent(Intent.OPEN_URL, target_text, browser)

    if ntpath.isabs(target_text):
        if re.search(r"\.[a-z0-9]{1,8}$", target_lower):
            return CommandIntent(Intent.OPEN_FILE, target_text)
        return CommandIntent(Intent.OPEN_FOLDER, target_text)

    if target_lower in _KNOWN_APPS:
        return CommandIntent(Intent.OPEN_APPLICATION, target_text)

    if re.search(r"\b(?:folder|directory)\b", target_lower):
        target_text = re.sub(
            r"^(?:the\s+)?folder\s+(?:(?:called|named)\s+)?",
            "",
            target_text,
            flags=re.I,
        ).strip()
        return CommandIntent(Intent.OPEN_FOLDER, target_text)
    if re.search(r"\.[a-z0-9]{1,8}$", target_lower):
        return CommandIntent(Intent.OPEN_FILE, target_text)
    if target_text:
        return CommandIntent(Intent.OPEN_APPLICATION, target_text)
    return CommandIntent(Intent.OTHER, target_text)


def known_website_url(name: str) -> str:
    target = (name or "").strip().lower()
    return _WEBSITES.get(target, "")
