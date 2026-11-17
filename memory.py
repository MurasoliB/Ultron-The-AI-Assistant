import json
import os
import threading
from pathlib import Path


MEMORY_FILE = Path(__file__).resolve().parent / "memory" / "memory.json"
_memory_lock = threading.RLock()


def load_memory():
    with _memory_lock:
        if not MEMORY_FILE.exists():
            return {"name": None, "facts": []}
        try:
            with MEMORY_FILE.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Ultron's long-term memory could not be read.") from error
        if not isinstance(data, dict):
            raise RuntimeError("Ultron's long-term memory has an invalid format.")
        data.setdefault("name", None)
        data.setdefault("facts", [])
        return data


def save_memory(memory):
    if not isinstance(memory, dict):
        raise TypeError("Memory must be stored as a dictionary.")
    with _memory_lock:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = MEMORY_FILE.with_suffix(".tmp")
        try:
            with temporary_file.open("w", encoding="utf-8") as file:
                json.dump(memory, file, indent=2, ensure_ascii=False)
            os.replace(temporary_file, MEMORY_FILE)
        except OSError as error:
            try:
                temporary_file.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("Ultron's long-term memory could not be saved.") from error


def remember_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A non-empty name is required.")
    memory = load_memory()
    memory["name"] = name.strip()
    save_memory(memory)


def get_name():
    return load_memory().get("name")


def remember_fact(fact):
    if not isinstance(fact, str) or not fact.strip():
        raise ValueError("A non-empty memory fact is required.")
    fact = fact.strip()
    memory = load_memory()
    facts = memory.get("facts")
    if not isinstance(facts, list):
        facts = []
    if fact not in facts:
        facts.append(fact)
    memory["facts"] = facts[-100:]
    save_memory(memory)


def get_facts():
    facts = load_memory().get("facts", [])
    return [fact for fact in facts if isinstance(fact, str)]
