import re
import threading
from collections import deque
from pathlib import Path

from memory import get_facts, get_name
from intent_router import Intent, classify_intent, known_website_url
from filesystem_tools import filesystem_context


class ConversationManager:
    def __init__(self, max_messages=20):
        self._messages = deque(maxlen=max_messages)
        self._lock = threading.RLock()
        self._current_task = ""
        self._recent_files = deque(maxlen=8)
        self._recent_folders = deque(maxlen=5)
        self._current_website = ""
        self._recent_error = ""

    def history(self):
        with self._lock:
            return [dict(message) for message in self._messages]

    def add_user_message(self, text):
        with self._lock:
            self._messages.append({"role": "user", "content": text})
            intent = classify_intent(text)
            command_intents = {
                Intent.OPEN_APPLICATION,
                Intent.OPEN_URL,
                Intent.OPEN_FOLDER,
                Intent.OPEN_FILE,
                Intent.OPEN_PROJECT,
                Intent.SEARCH_WEB,
                Intent.SEARCH_LOCAL,
            }
            if (
                len(text.split()) > 2 or intent.intent in command_intents
            ) and not re.fullmatch(
                r"(?i)(yes|no|okay|ok|continue|go back|do it again|run it|open that)",
                text.strip().rstrip(".!?"),
            ):
                self._current_task = text.strip()
            for path in re.findall(
                r"""(?i)(?:[a-z]:\\[^\s"'<>|?*]+|(?:[\w.-]+[\\/])+[\w.-]+\.\w+)""",
                text,
            ):
                if path not in self._recent_files:
                    self._recent_files.append(path)
            if intent.intent == Intent.OPEN_URL:
                url = known_website_url(intent.target) or intent.target
                if url.startswith(("http://", "https://")):
                    self._current_website = url

    def add_assistant_message(self, text):
        with self._lock:
            self._messages.append({"role": "assistant", "content": text})
            folder_match = re.search(
                r"(?i)(?:folder created at|opening folder)\s+(.+)$",
                text.strip(),
            )
            if folder_match:
                folder = folder_match.group(1).strip().rstrip(" .!?")
                if Path(folder).is_absolute() and folder not in self._recent_folders:
                    self._recent_folders.append(folder)
            if re.search(r"(?i)\b(error|failed|could not|unable to)\b", text):
                self._recent_error = text

    def context(self):
        with self._lock:
            pieces = []
            project_root = Path(__file__).resolve().parent
            pieces.append(
                f"Current project workspace: ULTRON personal AI assistant "
                f"(folder {project_root.name}; {project_root})"
            )
            if self._current_task:
                pieces.append(f"Current task: {self._current_task}")
            if self._recent_files:
                pieces.append("Recent files: " + ", ".join(self._recent_files))
            if self._recent_folders:
                pieces.append("Recent folders: " + "; ".join(self._recent_folders))
            pieces.extend(filesystem_context.context_lines())
            if self._current_website:
                pieces.append(f"Last requested website: {self._current_website}")
            if self._recent_error:
                pieces.append("Most recent issue: " + self._recent_error)
            name = get_name()
            if name:
                pieces.append(f"User's saved name: {name}")
            facts = get_facts()
            if facts:
                pieces.append("User-approved long-term memories: " + "; ".join(facts))
            return "\n".join(pieces)


conversation_manager = ConversationManager()
