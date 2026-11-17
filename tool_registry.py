import re
import threading
import time
import json

from actions import execute_action
from commands import INDEX_BUILDING_MESSAGE, IndexBuildingError, handle_command
from filesystem_tools import (
    FileSystemIntent,
    describe_confirmation,
    execute_filesystem_intent,
    filesystem_context,
    overwrite_payload,
    parse_filesystem_command,
    run_filesystem_intent,
)
from folder_tools import open_folder_request
from whatsapp_agent import whatsapp_agent
from mcp_manager import mcp_manager


_ACTION_PATTERN = re.compile(
    r"\[\[ACTION:([a-zA-Z0-9_]+):([^\]]*)\]\]",
)
_SUPPORTED_ACTIONS = {
    "open_app",
    "close_app",
    "play_youtube",
    "web_search",
    "mcp_search",
    "mcp_fetch",
    "send_email",
    "read_email",
    "open_website",
    "open_folder",
    "open_project",
    "set_volume",
    "mute",
    "unmute",
    "screenshot",
    "brightness_set",
    "set_brightness",
    "brightness_up",
    "increase_brightness",
    "brightness_down",
    "decrease_brightness",
    "screen_record",
    "record_screen",
    "lock",
    "show_desktop",
    "media_toggle",
    "media_next",
    "system_stats",
    "hotkey",
}


class ToolRegistry:
    """Routes user intents to existing command and Windows action handlers."""

    def __init__(self):
        self._action_handlers = {
            action_type: (
                lambda target, name=action_type: self._execute_windows_action(
                    name, target
                )
            )
            for action_type in _SUPPORTED_ACTIONS
        }
        self._confirmation_actions = {
            "screenshot",
            "screen_record",
            "record_screen",
            "lock",
        }
        self._pending_confirmation = None
        self._confirmation_lock = threading.Lock()
        self._action_handlers["filesystem_delete"] = self._execute_filesystem_delete
        self._action_handlers["filesystem_overwrite"] = self._execute_filesystem_overwrite
        self._action_handlers["mcp_search"] = lambda query: mcp_manager.web_search(query)
        self._action_handlers["mcp_fetch"] = lambda url: mcp_manager.web_fetch(url)

        def _handle_send_email(payload):
            from inbox import send_email, get_active_account_email
            if isinstance(payload, str):
                delimiter = "|" if "|" in payload else (":" if ":" in payload else None)
                if delimiter:
                    to_addr, body = payload.split(delimiter, 1)
                    subject = "I am Ultron - Murasoli's AI Assistant"
                    res = send_email(to_addr.strip(), subject, body.strip())
                    return (
                        f"[ULTRON GMAIL DISPATCH]\n"
                        f"This is the message I am sending automatically:\n"
                        f"- Recipient: {to_addr.strip()}\n"
                        f"- From: {get_active_account_email()}\n"
                        f"- Subject: {subject}\n"
                        f"- Content: \"{body.strip()}\"\n\n"
                        f"{res}"
                    )

            return "Please specify recipient and message as 'email|message'."
        self._action_handlers["send_email"] = _handle_send_email

        def _handle_read_email(payload):
            from inbox import read_email_content
            return read_email_content(str(payload).strip())
        self._action_handlers["read_email"] = _handle_read_email

    def register_action(self, action_type, handler, requires_confirmation=False):
        if not re.fullmatch(r"[a-zA-Z0-9_]+", action_type):
            raise ValueError("Tool names may contain only letters, digits, and underscores.")
        if not callable(handler):
            raise TypeError("Tool handler must be callable.")
        self._action_handlers[action_type] = handler
        if requires_confirmation:
            self._confirmation_actions.add(action_type)
        else:
            self._confirmation_actions.discard(action_type)

    def execute_native_command(self, command, context=""):
        result = mcp_manager.handle_command(command)
        if result is not None:
            return result
        result = whatsapp_agent.handle_command(command)
        if result is not None:
            return result
        result = open_folder_request(command, context=context)
        if result is not None:
            return result
        result = self.execute_filesystem_command(command, context=context)
        if result is not None:
            return result
        return handle_command(command, context=context)

    def execute_filesystem_command(self, command, context=""):
        try:
            intent = parse_filesystem_command(command)
            if intent is None:
                return None
            print(f"[INTENT] {intent.intent.upper()}")
            if intent.intent == "delete" or intent.requires_confirmation:
                action = "filesystem_delete" if intent.intent == "delete" else "filesystem_overwrite"
                payload = intent.to_json()
                return self.request_confirmation(
                    action,
                    payload,
                    describe_confirmation(intent),
                )
            result = run_filesystem_intent(intent)
            if result is not None:
                return result
            return "I couldn't resolve that filesystem request. Specify the exact path."
        except IndexBuildingError:
            return INDEX_BUILDING_MESSAGE
        except (ValueError, OSError) as error:
            print(f"[Filesystem intent failed] {type(error).__name__}: {error}")
            return f"I couldn't resolve that filesystem request: {error}"

    @staticmethod
    def _execute_filesystem_delete(payload):
        intent = FileSystemIntent.from_json(payload)
        return run_filesystem_intent(intent, overwrite=True)

    @staticmethod
    def _execute_filesystem_overwrite(payload):
        return overwrite_payload(payload)

    def request_confirmation(self, action_type, target, prompt):
        if action_type not in self._action_handlers:
            raise ValueError(f"Unregistered confirmation action: {action_type}")
        with self._confirmation_lock:
            self._pending_confirmation = (
                action_type,
                target,
                time.monotonic() + 60,
                prompt,
            )
        print("[CONFIRMATION] REQUIRED")
        return prompt

    @staticmethod
    def _execute_windows_action(action_type, target):
        return execute_action(action_type, target)

    def execute_response_action(self, response):
        matches = list(_ACTION_PATTERN.finditer(response))
        if not matches:
            return response.strip()

        clean_response = _ACTION_PATTERN.sub("", response).strip()
        if len(matches) > 5:
            return (
                "I found too many separate actions in that request. "
                "Please split it into smaller steps."
            )

        results = []
        for match in matches:
            action_type, target = match.groups()
            result = self._execute_action(action_type, target.strip())
            if result:
                results.append(result)

        if clean_response and results:
            return f"{clean_response}\n" + "\n".join(results)
        if results:
            return "\n".join(results)
        return clean_response or "The requested action did not report a result."

    def _execute_action(self, action_type, target):
        handler = self._action_handlers.get(action_type)
        if handler is None:
            return "I did not execute that action because it is not an approved Ultron tool."

        if action_type in self._confirmation_actions:
            return self.request_confirmation(
                action_type,
                target,
                f"Confirmation required: say 'yes' or 'confirm {action_type}' "
                "to proceed, or 'no' to cancel.",
            )

        return self._execute_registered(handler, action_type, target, "")

    def resolve_confirmation(self, command):
        normalized = re.sub(r"[.!?]+$", "", command.strip().lower())
        if (
            whatsapp_agent.awaiting_confirmation()
            and (
                self._is_affirmative(normalized)
                or normalized in {"cancel", "no", "nope", "don't send", "do not send"}
            )
        ):
            return whatsapp_agent.handle_command(command)
        with self._confirmation_lock:
            pending = self._pending_confirmation
            if not pending:
                return None
            action_type, target, expires_at, _prompt = pending
            if time.monotonic() > expires_at:
                self._pending_confirmation = None
                if self._is_affirmative(normalized):
                    return "That confirmation expired. Please request the action again."
                return None
            if normalized in {"cancel", "no", "nope", "don't", "do not"}:
                self._pending_confirmation = None
                print("[CONFIRMATION] NO")
                return "Cancelled. Nothing was changed."
            if not self._is_affirmative(normalized) and normalized != f"confirm {action_type}":
                return None
            self._pending_confirmation = None

        print("[CONFIRMATION] YES")
        handler = self._action_handlers[action_type]
        return self._execute_registered(
            handler,
            action_type,
            target,
            "",
        )

    @staticmethod
    def _is_affirmative(command):
        return command in {
            "yes", "yes please", "confirm", "confirmed", "do it",
            "proceed", "go ahead", "confirm delete",
        }

    @staticmethod
    def _execute_registered(handler, action_type, target, fallback):
        try:
            result = handler(target)
        except (FileNotFoundError, NotADirectoryError):
            return "The requested application or file is not available at that location."
        except PermissionError:
            return "Windows denied permission for that action. Nothing else was changed."
        except Exception as error:
            print(f"[Tool error] {action_type}: {type(error).__name__}")
            return "I couldn't complete that Windows action. Nothing else was changed."

        if result:
            return result
        return fallback or "The requested action did not report a result."


tool_registry = ToolRegistry()
