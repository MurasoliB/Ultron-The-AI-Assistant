"""Voice driven WhatsApp assistant for the native desktop app.

Command parsing and the draft-then-confirm conversation live here; the work
itself is done by :mod:`whatsapp_desktop`, which drives the installed
WhatsApp desktop app through Windows UI Automation.
"""

import re
import secrets
import threading
import time

from whatsapp_desktop import WhatsAppDesktop, WhatsAppDesktopError


CANCEL_WORDS = {
    "cancel", "no", "nope", "don't send", "do not send", "stop", "abort",
}
CONFIRM_WORDS = {
    "yes", "yes please", "send it", "confirm", "proceed", "go ahead", "y",
}
CONFIRM_TTL = 60


def parse_message_request(text):
    command = re.sub(r"\s+", " ", (text or "").strip())
    command = re.sub(r"^(?:hey\s+)?(?:ultron|jarvis)[, ]*", "", command, flags=re.I)
    patterns = (
        r"^(?:send|message)\s+(.+?)\s+(?:a\s+)?whatsapp\s+"
        r"(?:message|msg|text)(?:\s+(?:saying|that|:)\s+(.+))?$",
        r"^(?:send|message)\s+(.+?)\s+(?:a\s+)?message\s+on\s+whatsapp"
        r"(?:\s+(?:saying|that|:)\s+(.+))?$",
        r"^tell\s+(.+?)\s+on\s+whatsapp\s+that\s+(.+)$",
        r"^whatsapp\s+(.+?)\s+and\s+tell\s+(?:him|her|them)\s+(.+)$",
        r"^(?:say|send|tell)\s+(.+?)\s+to\s+(.+?)\s+(?:on|in)\s+whatsapp[.!?]*$",
        r"^send\s+(.+?)\s*:\s*(.+)$",
        r"^message\s+(.+?)\s+on\s+whatsapp[.!?]*$",
    )
    for pattern in patterns:
        match = re.match(pattern, command, re.I)
        if match:
            if pattern.startswith(r"^(?:say|send|tell)"):
                message = match.group(1).strip()
                contact = _clean_contact(match.group(2))
            else:
                contact = _clean_contact(match.group(1))
                message = match.group(2).strip() if match.lastindex and match.lastindex >= 2 and match.group(2) else ""
            if contact and not re.search(r"\bwhatsapp\b", contact, re.I):
                return contact, message
    return None


def _clean_contact(value):
    return (value or "").strip().strip("\"'“”‘’").strip().rstrip(".!?")


def _diagnostic_hint(diagnostics):
    """Turn a desktop report into one short, actionable line."""
    if not isinstance(diagnostics, dict) or not diagnostics:
        return ""
    facts = []
    if diagnostics.get("error"):
        return f"Desktop report: {diagnostics['error']}."
    if diagnostics.get("window"):
        facts.append(f"window '{diagnostics['window']}'")
    if diagnostics.get("process_id"):
        facts.append(f"pid {diagnostics['process_id']}")
    if diagnostics.get("nodes") is not None:
        facts.append(f"{diagnostics['nodes']} accessible elements")
    if diagnostics.get("search_found") is not None:
        facts.append(
            "search box found" if diagnostics["search_found"] else "no search box"
        )
    if diagnostics.get("chat_rows") is not None:
        facts.append(f"{diagnostics['chat_rows']} chat rows")
    titles = [title for title in diagnostics.get("row_titles") or [] if title]
    if titles:
        facts.append("rows show " + ", ".join(titles[:4]))
    if diagnostics.get("composer_open"):
        facts.append("a conversation is open")
    if diagnostics.get("logged_in") is not None:
        facts.append("signed in" if diagnostics["logged_in"] else "not signed in")
    if not facts:
        return ""
    return "Desktop report: " + "; ".join(facts) + "."


class WhatsAppAgent:
    def __init__(self, desktop=None):
        self.desktop = desktop or WhatsAppDesktop()
        self.lock = threading.RLock()
        self.state = None

    def awaiting_confirmation(self):
        with self.lock:
            return bool(self.state and self.state["kind"] == "awaiting_confirmation")

    def handle_command(self, text):
        request = parse_message_request(text)
        normalized = re.sub(r"[.!?]+$", "", (text or "").strip().lower())
        with self.lock:
            if normalized in {
                "test whatsapp", "test whatsapp connection",
                "open whatsapp", "open whatsapp app",
                "diagnose whatsapp", "check whatsapp",
            }:
                operation = "diagnose" if normalized in {"diagnose whatsapp", "check whatsapp"} else "test"
                result = self._call(operation)
                if isinstance(result, str):
                    return result
                if operation == "diagnose":
                    report = _diagnostic_hint(result.get("diagnostics"))
                    return report or "I couldn't read anything from the WhatsApp window."
                if result.get("status") == "logged_in":
                    return "WhatsApp is open and signed in on this PC."
                if result.get("status") == "logged_out":
                    return "WhatsApp is open but signed out. Sign in manually; ULTRON will not bypass login."
                return result.get("message") or "WhatsApp is not ready yet."
            if self.state:
                if self.state["kind"] == "awaiting_message":
                    if normalized in CANCEL_WORDS:
                        self.state = None
                        return "Cancelled. No WhatsApp message was prepared."
                    contact = self.state["contact"]
                    self.state = None
                    return self._prepare(contact, text.strip())
                if self.state["kind"] == "choosing_contact":
                    choice = _clean_contact(text)
                    matches = self.state["matches"]
                    if normalized in CANCEL_WORDS:
                        self.state = None
                        return "Cancelled. No WhatsApp message was prepared."
                    if not choice.isdigit() and not any(
                        match["label"].casefold() == choice.casefold()
                        for match in matches
                    ):
                        if not request:
                            return None
                        self.state = None
                        return self._prepare(request[0], request[1])
                    return self._choose_contact(text)
                if self.state["kind"] == "awaiting_confirmation":
                    if time.monotonic() > self.state["expires_at"]:
                        expired = self.state
                        self.state = None
                        self._cancel_draft(expired["draft_id"])
                        return "That WhatsApp confirmation expired. The message was not sent; prepare it again if needed."
                    if normalized in CANCEL_WORDS:
                        cancelled = self.state
                        self.state = None
                        self._cancel_draft(cancelled["draft_id"])
                        return "Cancelled. The WhatsApp message was not sent."
                    if normalized in CONFIRM_WORDS:
                        return self._send()
                    if not request:
                        return None
                    return "The WhatsApp message is still a draft. Say 'yes' to send it or 'no' to cancel."

            if not request:
                return None
            contact, message = request
            if not message:
                self.state = {"kind": "awaiting_message", "contact": contact}
                return f"What would you like me to send to {contact}?"
            return self._prepare(contact, message)

    def _call(self, operation, **values):
        """Run one desktop operation, turning failures into spoken text."""
        try:
            return self.desktop.request(operation, **values)
        except (OSError, RuntimeError, TimeoutError, WhatsAppDesktopError) as error:
            return f"I couldn't reach the WhatsApp desktop app: {error}"

    def _cancel_draft(self, draft_id):
        """Tell the desktop agent to wipe a draft the user declined."""
        if not draft_id:
            return
        try:
            self.desktop.notify("cancel", draft_id=draft_id)
        except (OSError, RuntimeError, WhatsAppDesktopError):
            pass

    def _choose_contact(self, text):
        matches = self.state["matches"]
        choice = _clean_contact(text)
        if choice.isdigit():
            index = int(choice) - 1
            if index < 0 or index >= len(matches):
                return "That contact number is not in the match list. Choose a listed number or say 'cancel'."
        else:
            matching = [
                match for match in matches
                if match["label"].casefold() == choice.casefold()
            ]
            if len(matching) != 1:
                return "Choose one exact contact name from the list, or say 'cancel'."
            index = matches.index(matching[0])
        message = self.state["message"]
        contact = self.state["contact_query"]
        self.state = None
        return self._prepare(contact, message, choice_index=index)

    def _prepare(self, contact, message, choice_index=None):
        job = {
            "contact": contact,
            "message": message,
            "request_id": secrets.token_urlsafe(18),
        }
        if choice_index is not None:
            job["choice_index"] = choice_index
        result = self._call("prepare", **job)
        if isinstance(result, str):
            return result
        status = result.get("status")
        if status == "error":
            return result.get("message") or "I couldn't prepare the WhatsApp message."
        if status == "logged_out":
            return "WhatsApp is signed out. Sign in manually; ULTRON will not bypass login."
        if status == "not_open":
            return "I couldn't open WhatsApp. Open it from the Start menu and retry."
        if status == "not_ready":
            return result.get("message") or "WhatsApp is still loading. Wait for your chats to appear, then retry."
        if status == "contact_not_found":
            visible = result.get("visible_results") or []
            labels = "; ".join(str(label) for label in visible[:5])
            if labels:
                lead = (
                    f"I couldn't match {contact} to a WhatsApp chat. "
                    f"Chats I can see: {labels}."
                )
            else:
                lead = f"I couldn't find the WhatsApp chat {contact}."
            return f"{lead} Nothing was sent. " + _diagnostic_hint(result.get("diagnostics"))
        if status == "multiple_matches":
            matches = result.get("matches") or []
            self.state = {
                "kind": "choosing_contact",
                "contact_query": contact,
                "message": message,
                "matches": matches,
            }
            choices = "; ".join(
                f"{index + 1}: {match['label']}" for index, match in enumerate(matches)
            )
            return f"I found multiple WhatsApp chats matching {contact}: {choices}. Which one should I use?"
        if status != "ready" or not result.get("draft_id"):
            return result.get("message") or "I couldn't prepare the WhatsApp message. Nothing was sent."
        self.state = {
            "kind": "awaiting_confirmation",
            "contact": result.get("contact") or contact,
            "message": message,
            "draft_id": result["draft_id"],
            "expires_at": time.monotonic() + CONFIRM_TTL,
        }
        return (
            f"Message to {self.state['contact']} is ready: {message} "
            "Should I send it?"
        )

    def _send(self):
        state = self.state
        result = self._call(
            "send",
            contact=state["contact"],
            message=state["message"],
            draft_id=state["draft_id"],
            confirmed=True,
        )
        if isinstance(result, str):
            self.state = None
            return f"{result} Check the chat before retrying."
        self.state = None
        if result.get("status") == "sent":
            return f"WhatsApp message sent to {state['contact']}."
        return result.get("message") or "WhatsApp did not confirm that the message was sent."


whatsapp_agent = WhatsAppAgent()
