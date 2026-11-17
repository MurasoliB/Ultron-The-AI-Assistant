"""Dedicated folder-opening resolution for ULTRON.

Folder opening previously had no dedicated path: an utterance such as "open
downloads", "go to pictures" or "show me documents" was classified as an
application request and failed, while "show me <name>" was intercepted as a
directory listing. This module is the single place that decides whether an
utterance names a folder and which directory it refers to.

Resolution ladder for a cleaned phrase:
1. pronouns ("it", "that folder") -> last filesystem item
2. shell folders and drive letters ("downloads", "c drive")
3. the ULTRON project folder
4. "<name> in <location>" -> resolve the location, then look inside it
5. absolute paths, then the app-wide name resolver in commands.py
6. the whole-computer folder index
7. an explicit, actionable failure message
"""

import ntpath
import os
import re
import subprocess
from pathlib import Path

import commands
from filesystem_tools import (
    _FOLDER_ALIASES,
    _PROJECT_ROOT,
    filesystem_context,
    resolve_path,
)
from intent_router import Intent, classify_intent


_OPEN_VERB = (
    r"open(?:\s+up)?|show(?:\s+me)?|go\s+to|go|take\s+me\s+to|navigate\s+to|"
    r"switch\s+to|enter|browse|bring\s+up|launch|explore|visit|get\s+to|"
    r"head\s+to|jump\s+to"
)

# Words that only ever describe where something lives, never part of the name.
_FILLER_WORDS = r"(?:folder|folders|directory|directories|dir|place|location|path)"

_LEADING_NOISE = re.compile(
    rf"^(?:please\s+|the\s+|a\s+|an\s+|my\s+|our\s+|this\s+|that\s+|"
    rf"his\s+|her\s+|their\s+|its\s+|your\s+|all\s+|whole\s+|entire\s+|"
    rf"{_FILLER_WORDS}\s+)+",
    re.I,
)
_TRAILING_NOISE = re.compile(
    rf"(?:\s+(?:{_FILLER_WORDS}|one|now|already|please|for\s+me))+",
    re.I,
)
_EXPLICIT_FOLDER_WORD = re.compile(rf"\b(?:{_FILLER_WORDS})\b", re.I)
_EXPLORER_SUFFIX = re.compile(
    r"\s+(?:in|inside|with|using)\s+(?:the\s+)?(?:file\s+)?(?:windows\s+)?explorer\.?$",
    re.I,
)
_LOCATION_SUFFIX = re.compile(
    r"^(?P<name>.+?)\s+(?:in|inside|under|within|from)\s+(?P<place>.+)$",
    re.I,
)
_DRIVE = re.compile(r"^(?:the\s+)?([a-z])(?:\s*:\s*|\s+(?:drive|partition))?\.?$", re.I)
_COMPOUND = re.compile(r"\b(?:and|then|also|plus|after\s+that)\b", re.I)

# Requests that belong to the application, website or file handlers even when
# they start with an open-style verb ("open chrome", "open github").
_NOT_A_FOLDER_INTENT = {
    Intent.OPEN_APPLICATION,
    Intent.OPEN_URL,
    Intent.SEARCH_WEB,
    Intent.OPEN_FILE,
    Intent.CLOSE_APPLICATION,
    Intent.BROWSER_CONTROL,
}

_PRONOUNS = {
    "it", "that", "this", "that folder", "this folder", "the folder",
    "last folder", "previous folder", "the last folder", "the previous folder",
    "folder", "directory", "the directory", "there", "that one", "this one",
}
_PROJECT_PHRASES = {
    "project", "my project", "the project", "this project", "current project",
    "ultron project", "ultron", "jarvis", "jarvis project", "ultron project folder",
}
_COMPUTER_PHRASES = {
    "this pc", "this computer", "my computer", "computer", "pc", "laptop",
    "desktop pc", "the pc", "my pc", "this pc folder", "pc folder",
    "my computer folder",
}
_HOME_PHRASES = {
    "home", "my home", "user profile", "my user profile", "profile",
    "my documents and settings", "root",
}
_RECYCLE_PHRASES = {"recycle bin", "recycle", "trash", "bin"}
# Browsers open web addresses, not folders, so they are never an "open in" app.
_BROWSER_KEYS = {
    "brave", "googlechrome", "chrome", "microsoftedge", "edge",
    "mozillafirefox", "firefox", "opera", "safari",
}

# Loose wording that still means one of the Windows shell folders.
_SHELL_FOLDER_ALIASES = {
    "desktop": "desktop", "desk": "desktop",
    "documents": "documents", "document": "documents", "doc": "documents",
    "docs": "documents", "my docs": "documents",
    "downloads": "downloads", "download": "downloads", "downloaded": "downloads",
    "pictures": "pictures", "picture": "pictures", "photo": "pictures",
    "photos": "pictures", "image": "pictures", "images": "pictures",
    "videos": "videos", "video": "videos", "movies": "videos",
    "music": "music", "song": "music", "songs": "music", "audio": "music",
}
_SHELL_FOLDER_KEYS = (
    "downloads", "documents", "desktop", "pictures", "videos", "music",
)

# Folders that must never be reached through fuzzy or alias matching, because
# they hold installed programs rather than anything the user meant to browse.
_PROGRAM_PREFIXES = tuple(
    dict.fromkeys(
        os.path.normcase(os.path.normpath(path))
        for path in (
            r"C:\Windows",
            r"C:\Program Files",
            r"C:\Program Files (x86)",
            r"C:\ProgramData",
            r"C:\$Recycle.Bin",
            os.path.expandvars(r"%APPDATA%"),
            os.path.expandvars(r"%LOCALAPPDATA%"),
            os.path.expandvars(r"%SystemRoot%"),
        )
        if path
    )
)


def _is_program_location(path: str) -> bool:
    try:
        candidate = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except (OSError, ValueError):
        return True
    return any(
        candidate == prefix or candidate.startswith(prefix + os.sep)
        for prefix in _PROGRAM_PREFIXES
    )


def _clean_phrase(phrase: str) -> str:
    """Strip filler words while keeping the words that identify the folder."""
    text = re.sub(r"\s+", " ", (phrase or "").strip()).strip("\"'“”‘’ .")
    text = _EXPLORER_SUFFIX.sub("", text)
    previous = None
    while text and text != previous:
        previous = text
        text = _LEADING_NOISE.sub("", text)
        text = _TRAILING_NOISE.sub("", text)
    return text.strip("\"'“”‘’ .,?")


def _display_name(path: str) -> str:
    normalized = os.path.normpath(path)
    name = os.path.basename(normalized)
    # Drive roots normalise to "C:\" which has no basename.
    if len(normalized) == 3 and normalized.endswith(":\\"):
        return f"{normalized[0]}: drive"
    return name or path


def parse_open_folder_request(text: str):
    """Return (phrase, explicit_folder_word) when the text asks to open a folder."""
    command = re.sub(r"\s+", " ", (text or "").strip())
    command = re.sub(r"^(?:hey\s+)?(?:ultron|jarvis)[, ]*", "", command, flags=re.I)
    command = command.strip().rstrip(".!?")

    match = re.match(rf"^(?:{_OPEN_VERB})\s+(?P<target>.+)$", command, re.I)
    if not match:
        return None

    return _clean_phrase(match.group("target")), bool(
        _EXPLICIT_FOLDER_WORD.search(match.group("target"))
    )


def _shell_folder_key(phrase: str):
    """Map loose wording such as "download" or "my docs" to a shell folder key."""
    text = _clean_phrase(phrase).casefold()
    if not text:
        return ""
    if text in _HOME_PHRASES:
        return "home"
    if text in _RECYCLE_PHRASES:
        return "recycle"

    for alias, folder_key in sorted(_FOLDER_ALIASES.items(), key=lambda entry: -len(entry[0])):
        if text == alias:
            return folder_key
    if text in _SHELL_FOLDER_ALIASES:
        return _SHELL_FOLDER_ALIASES[text]
    # Singular/plural and minor spelling differences ("download", "pictures").
    # Only a single word can be a variant of a shell folder; anything longer
    # carries extra words that name something else.
    if " " not in text and len(text) >= 4:
        for alias, folder_key in sorted(
            _SHELL_FOLDER_ALIASES.items(), key=lambda entry: -len(entry[0])
        ):
            if alias.startswith(text) or text.startswith(alias):
                return folder_key
    return ""


def _shell_folder_candidate(phrase: str):
    """Resolve a Windows shell folder without scanning the disk."""
    key = _shell_folder_key(phrase)
    if not key:
        return None
    if key == "home":
        home = os.path.expanduser("~")
        return home if os.path.isdir(home) else None
    if key == "recycle":
        recycle = r"C:\$Recycle.Bin"
        return recycle if os.path.isdir(recycle) else None

    try:
        candidate = resolve_path(key)
    except (OSError, ValueError):
        return None
    return str(candidate) if candidate.is_dir() else None


def _drive_candidate(phrase: str):
    match = _DRIVE.match(phrase)
    if not match:
        return None
    root = f"{match.group(1).upper()}:\\"
    return root if os.path.isdir(root) else None


def _explicit_path_candidate(phrase: str):
    if not phrase or not ntpath.isabs(phrase):
        return None
    try:
        candidate = resolve_path(phrase)
    except (OSError, ValueError):
        return None
    return str(candidate) if candidate.is_dir() else None


def _name_resolver_candidate(phrase: str):
    """Use the app-wide resolver, keeping only directories outside program folders."""
    if not phrase:
        return None
    try:
        resolved = commands._guess_file_path(phrase)
    except Exception:
        resolved = None
    if not resolved or not os.path.isdir(resolved):
        return None
    if _is_program_location(resolved) and not ntpath.isabs(phrase):
        return None
    return resolved


def _index_candidates(phrase: str):
    if not phrase:
        return []
    matches = commands._find_folder_in_index(phrase)
    return [
        path for path in dict.fromkeys(matches)
        if os.path.isdir(path) and not _is_program_location(path)
    ]


def _split_location(phrase: str):
    match = _LOCATION_SUFFIX.match(phrase)
    if not match:
        return phrase, ""
    return _clean_phrase(match.group("name")), _clean_phrase(match.group("place"))


def _app_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (name or "").strip().lower())


def _is_launchable(place: str) -> bool:
    """True when `place` names an application a folder can be opened in."""
    key = re.sub(r"[^a-z0-9]+", "", (place or "").casefold())
    if not key or key in _BROWSER_KEYS:
        return False
    if _app_key(place) in commands.APP_MAPPINGS:
        return True
    try:
        if commands._find_editor(place):
            return True
        return bool(commands._resolve_app_path(place))
    except Exception:
        return False


def _split_target(phrase: str):
    """Split into (folder name, containing location, application to open it in)."""
    name, place = _split_location(phrase)
    # A registered application wins over a same-named folder on disk, so
    # "in vs code" means VS Code and "in documents" means the Documents folder.
    if place and _is_launchable(place):
        return name, "", place
    return name, place, ""


def _places_for(location: str):
    """Directories that `location` could refer to, most specific first."""
    places = []
    for wording in (location, f"{location} folder", f"my {location} folder"):
        for resolver in (_shell_folder_candidate, _explicit_path_candidate):
            resolved = resolver(wording)
            if resolved:
                places.append(resolved)
    resolved = _name_resolver_candidate(location)
    if resolved:
        places.append(resolved)
    return list(dict.fromkeys(places))


def _candidates_under(name: str, location: str):
    """Look for `name` inside each folder that `location` could refer to."""
    if not name or not location:
        return []
    seen = []
    for place in _places_for(location):
        for candidate_name in (name, f"{name} folder"):
            child = Path(place) / candidate_name
            if child.is_dir() and str(child) not in seen:
                seen.append(str(child))
    return seen


def _looks_like_folder_name(phrase: str) -> bool:
    if ntpath.isabs(phrase) or any(token in phrase for token in ("\\", "/", ":", "_", "-")):
        return True
    return len(phrase.split()) > 1


def _has_dominant_match(phrase: str, paths: list) -> bool:
    """True when exactly one candidate's own name matches what was asked for."""
    wanted = re.sub(r"[^a-z0-9]+", "", phrase.casefold())
    exact = [
        path for path in paths
        if re.sub(r"[^a-z0-9]+", "", _display_name(path).casefold()) == wanted
    ]
    return len(exact) == 1


def _suggestion(phrase: str, paths: list) -> str:
    if not paths:
        return (
            f"I could not find a folder called '{phrase}'. "
            "Give me the folder name or its full path."
        )
    options = "; ".join(f"{index}. {path}" for index, path in enumerate(paths[:5], 1))
    extra = f", and {len(paths) - 5} more" if len(paths) > 5 else ""
    return (
        f"I found {len(paths)} folders matching '{phrase}'. "
        f"Say the full path or pick one: {options}{extra}."
    )


def _open_computer_shell() -> str:
    try:
        subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
    except OSError as error:
        return f"I couldn't open This PC: {error}"
    return "Opening This PC."


def _real_path(path: str) -> str:
    """Recover the on-disk casing so replies and remembered paths match Windows."""
    try:
        current = os.path.abspath(path)
    except (OSError, ValueError):
        return path
    drive, remainder = os.path.splitdrive(current)
    resolved = drive + os.sep
    for part in (part for part in remainder.split(os.sep) if part):
        try:
            match = next(
                (
                    entry for entry in os.listdir(resolved)
                    if entry.casefold() == part.casefold()
                ),
                None,
            )
        except OSError:
            return current
        if match is None:
            return current
        resolved = os.path.join(resolved, match)
    return resolved


def _reveal(path: str) -> str:
    target = _real_path(path)
    try:
        os.startfile(target)
    except OSError as error:
        return f"I found {target} but couldn't open it: {error}"
    return f"Opening folder {_display_name(target)}."


def _open_in_app(path: str, app: str) -> str:
    """Open a folder inside the application the user named, e.g. VS Code."""
    target = _real_path(path)
    label = f"Opening folder {_display_name(target)} in {app}."

    try:
        if commands.launch_app_with_path(app, target):
            return label
    except Exception as error:
        print(f"[FolderTools] {app} could not open {target}: {type(error).__name__}")

    # Applications registered by name may need their own launch command.
    command = commands.APP_MAPPINGS.get(_app_key(app), "")
    if command and not command.startswith("http"):
        try:
            if os.path.isabs(command) and os.path.exists(command):
                subprocess.Popen([command, target], shell=False)
            else:
                os.startfile(command, arguments=f'"{target}"')
            return label
        except OSError as error:
            print(f"[FolderTools] {app} launch failed for {target}: {error}")

    return f"{app} isn't available for that folder. {_reveal(target)}"


def open_folder_request(text: str, context: str = ""):
    """Open the folder the user asked for.

    Returns a response string when this module handled the utterance, otherwise
    None so the next handler in the native command chain gets its turn.
    """
    parsed = parse_open_folder_request(text)
    if parsed is None:
        return None
    phrase, explicit = parsed

    # Joined requests belong to the conversational handler, which can answer
    # every part instead of running one handler per phrase.
    if _COMPOUND.search(phrase):
        return None

    if phrase.casefold() in _COMPUTER_PHRASES:
        return _open_computer_shell()

    # A bare pronoun or filler word means "the folder we were just working in".
    if not phrase or phrase.casefold() in _PRONOUNS:
        remembered = filesystem_context.resolve_reference(phrase.casefold())
        if remembered:
            target = remembered if os.path.isdir(remembered) else os.path.dirname(remembered)
            if os.path.isdir(target):
                return _reveal(target)
        return "I don't have a folder to reopen yet. Name the folder or give me its full path."

    name, location, app = _split_target(phrase)

    if phrase.casefold() in _PROJECT_PHRASES:
        return _reveal(str(_PROJECT_ROOT))
    if name.casefold() in _PROJECT_PHRASES:
        return _open_in_app(str(_PROJECT_ROOT), app) if app else _reveal(str(_PROJECT_ROOT))

    # Shell folders and drives are decided before applications so that
    # "open desktop" is the Desktop and never Remote Desktop Connection.
    for resolver in (_shell_folder_candidate, _drive_candidate, _explicit_path_candidate):
        located = resolver(phrase)
        if located:
            return _open_in_app(located, app) if app else _reveal(located)

    # An explicit "in <app>" already says this is a folder, so the application
    # and website handlers must not claim the request. Without one, leave those
    # requests to the handlers that own them.
    if not app:
        if classify_intent(text, context=context).intent in _NOT_A_FOLDER_INTENT:
            return None
        # Decline installed application names so "open chrome" still launches Chrome.
        if not explicit and commands._resolve_app_path(phrase):
            return None

    located = _candidates_under(name, location)
    if located:
        return _open_in_app(located[0], app) if app else _reveal(located[0])

    resolved = _name_resolver_candidate(name)
    if resolved:
        return _open_in_app(resolved, app) if app else _reveal(resolved)

    try:
        matches = _index_candidates(name)
    except commands.IndexBuildingError:
        return None

    if matches:
        matches.sort(key=lambda path: (len(path), path))
        # Naming a folder explicitly does not license guessing between
        # equally good candidates; only one clear winner is opened silently.
        if len(matches) > 1 and not _has_dominant_match(name, matches):
            return _suggestion(name, matches)
        return _open_in_app(matches[0], app) if app else _reveal(matches[0])

    if not app and not explicit and not _looks_like_folder_name(phrase):
        # Stay quiet unless this really reads as a folder, so applications and
        # conversational requests keep reaching their own handlers.
        return None

    return _suggestion(name, [])