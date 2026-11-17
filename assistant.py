import re
import threading

from brain import ask_ai
from conversation import conversation_manager
from memory import remember_fact
from tool_registry import tool_registry


_REMEMBER_REQUEST = re.compile(
    r"^(?:please\s+)?(?:remember(?:\s+that)?|don't\s+forget(?:\s+that)?)\s+(.+)$",
    re.IGNORECASE,
)
_request_lock = threading.Lock()


def process_message(message: str, provider: str = "auto") -> str:
    with _request_lock:
        return _process_message(message, provider)


def _process_message(message: str, provider: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""

    previous_history = conversation_manager.history()
    context = conversation_manager.context()
    conversation_manager.add_user_message(text)

    try:
        confirmation_response = tool_registry.resolve_confirmation(text)
        remember_match = _REMEMBER_REQUEST.match(text)
        if confirmation_response is not None:
            response = confirmation_response
        elif remember_match:
            fact = remember_match.group(1).strip().rstrip(".!?")
            remember_fact(fact)
            response = "Noted. I’ll remember that."
        else:
            response = None
            if not _is_compound_request(text):
                response = tool_registry.execute_native_command(text, context=context)
            if not response:
                response = ask_ai(
                    text,
                    provider,
                    history=previous_history,
                    context=context,
                )
                response = tool_registry.execute_response_action(response or "")
            if not response:
                response = "I didn't get a usable response. Try that once more."
    except Exception as error:
        print(f"[ULTRON] Request failed ({type(error).__name__}).")
        response = (
            "I couldn't complete that request. Check its current state before "
            "retrying, in case part of it already succeeded."
        )

    conversation_manager.add_assistant_message(response)
    return response


def _is_compound_request(text: str) -> bool:
    # Native handlers intentionally execute only one complete command.
    # Let the AI parse joined requests so no question/action is silently dropped.
    return bool(re.search(r"\b(?:and then|and also|also|then|and)\b", text, re.I))
