import re

from voice import speak, listen
from assistant import process_message
from memory import get_name
import config

# -------------------------------------------------------------
# STARTUP SEQUENCE
# -------------------------------------------------------------
name = get_name()

if name:
    speak(f"Greetings, {name}. Ultron systems are initialized.")
else:
    speak("Ultron systems are online.")

speak("State your command or say Hey Ultron.")


# -------------------------------------------------------------
# MAIN EVENT LOOP
# -------------------------------------------------------------
while True:
    try:
        heard = listen()

        if not heard:
            continue

        # Exit command
        heard_lower = heard.casefold()
        if heard_lower in ["exit", "quit", "shutdown assistant", "goodbye", "sleep ultron"]:
            speak("Powering down Ultron systems. Until next time.")
            break

        # Check for wake word
        has_wake_word = any(wake in heard_lower for wake in config.WAKE_WORDS)

        if not has_wake_word:
            continue

        # Clean prompt by removing wake words
        clean_command = heard
        for wake in config.WAKE_WORDS:
            clean_command = re.sub(re.escape(wake), "", clean_command, flags=re.IGNORECASE)
        clean_command = clean_command.strip()

        # If user only said the wake word
        if not clean_command:
            speak("I am listening.")
            continue

        print(f"[Processing]: '{clean_command}'")

        response = process_message(clean_command, provider="auto")
        speak(response)

    except KeyboardInterrupt:
        print("\nTerminated by user.")
        break
    except Exception as general_err:
        print(f"[System Error]: {general_err}")