import os
import re
from dotenv import load_dotenv
from groq import Groq
from google import genai
import config

load_dotenv()

# 1. Initialize Groq
groq_api_key = os.getenv("GROQ_API_KEY")
groq_client = None
if groq_api_key:
    groq_client = Groq(api_key=groq_api_key)

# 2. Initialize Gemini
gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = None
if gemini_api_key:
    gemini_client = genai.Client(api_key=gemini_api_key)

# Candidate Gemini fallback models
GEMINI_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.6-flash"
]

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]


def _is_code_request(message: str) -> bool:
    """Check if the user request involves code generation, scripting, or technical detail."""
    if not message:
        return False
    m = message.lower()
    if "```" in message:
        return True

    code_terms = [
        "code", "program", "script", "python", "java", "javascript", "js", "ts",
        "typescript", "c++", "cpp", "c#", "csharp", "rust", "golang", "go",
        "html", "css", "sql", "php", "ruby", "swift", "kotlin", "dart",
        "react", "vue", "angular", "node", "express", "flask", "django", "fastapi",
        "spring", "flutter", "function", "class", "algorithm", "method", "loop",
        "array", "object", "develop", "build", "create", "implement", "generate",
        "write a", "write me", "app", "application", "website", "webpage",
        "component", "file", "api", "backend", "frontend", "database", "query",
        "regex", "bug", "debug", "refactor", "unit test", "test case", "docker",
        "yaml", "json", "xml", "syntax", "compiler", "terminal", "powershell",
        "bash", "shell", "git", "solve", "calculator"
    ]
    for term in code_terms:
        if re.search(rf"\b{re.escape(term)}\b", m):
            return True
    return False


def ask_groq(message: str, history=None, context: str = "") -> str:
    """Query lightning-fast Groq LPU engine with expanded token allowance and auto-continuation."""
    if not groq_client:
        raise ValueError("Groq client not initialized.")

    messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
    if context:
        messages.append({
            "role": "system",
            "content": f"Relevant conversation and memory context:\n{context}",
        })
    for item in history or []:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({
                "role": item["role"],
                "content": item["content"],
            })
    messages.append({"role": "user", "content": message})

    code_request = _is_code_request(message)
    # Increased token limits: 16,384 tokens for code/technical requests, 4,096 for general conversation
    max_toks = 16384 if code_request else 4096
    temperature = 0.2 if code_request else 0.7

    last_err = None
    for m in GROQ_MODELS:
        try:
            full_content = ""
            current_messages = list(messages)

            # Auto-continue if the model stops before finishing the code
            for _ in range(4):
                response = groq_client.chat.completions.create(
                    model=m,
                    messages=current_messages,
                    max_tokens=max_toks,
                    temperature=temperature
                )
                choice = response.choices[0]
                chunk = choice.message.content or ""
                finish_reason = choice.finish_reason

                full_content += chunk

                # If the generation finished completely and code blocks are balanced, we are done
                if finish_reason != "length" and full_content.count("```") % 2 == 0:
                    break

                # If truncated mid-response or open code block remains, request continuation
                current_messages.append({"role": "assistant", "content": chunk})
                current_messages.append({
                    "role": "user",
                    "content": "Continue generating the rest of the code directly from where you stopped. Do not repeat code already output, continue seamlessly until finished."
                })

            # Ensure all code blocks are properly closed
            if full_content.count("```") % 2 != 0:
                full_content += "\n```"

            return full_content.strip()
        except Exception as err:
            last_err = err
            continue

    raise last_err if last_err else RuntimeError("All Groq models failed.")


def ask_gemini(message: str, history=None, context: str = "") -> str:
    """Query the fast Gemini Flash model with expanded token allowance."""
    if not gemini_client:
        raise ValueError("Gemini client not initialized.")

    transcript = "\n".join(
        f"{'User' if item.get('role') == 'user' else 'Ultron'}: "
        f"{item.get('content', '')}"
        for item in history or []
        if item.get("role") in ("user", "assistant") and item.get("content")
    )
    context_block = (
        f"\n\nRelevant conversation and memory context:\n{context}"
        if context else ""
    )
    history_block = f"\n\nRecent conversation:\n{transcript}" if transcript else ""
    full_prompt = (
        f"{config.SYSTEM_PROMPT}{context_block}{history_block}"
        f"\n\nUser: {message}\nUltron:"
    )
    last_err = None

    code_request = _is_code_request(message)
    # Increased token limits: 8,192 tokens for code/technical requests, 4,096 for general conversation
    max_toks = 8192 if code_request else 4096
    temperature = 0.2 if code_request else 0.7

    for m in GEMINI_MODELS:
        try:
            full_content = ""
            current_prompt = full_prompt

            for _ in range(4):
                response = gemini_client.models.generate_content(
                    model=m,
                    contents=current_prompt,
                    config={
                        "max_output_tokens": max_toks,
                        "temperature": temperature,
                    }
                )
                chunk = response.text or ""
                full_content += chunk

                is_truncated = False
                if response.candidates:
                    fr = str(getattr(response.candidates[0], "finish_reason", "")).upper()
                    if "MAX_TOKENS" in fr or "LENGTH" in fr:
                        is_truncated = True

                if not is_truncated and full_content.count("```") % 2 == 0:
                    break

                current_prompt += f"{chunk}\n\n[System: Continue generating the rest of the code directly from where you stopped. Do not repeat code already written, continue until finished.]\nUltron:"

            if full_content.count("```") % 2 != 0:
                full_content += "\n```"

            return full_content.strip()
        except Exception as e:
            last_err = e
            continue

    raise last_err if last_err else RuntimeError("All Gemini models failed.")


def ask_ai(
    message: str,
    provider: str = "auto",
    history=None,
    context: str = "",
) -> str:
    """
    Query the selected provider. Auto mode tries Groq and falls back to Gemini.
    """
    provider = provider.lower().strip()
    def ask_with_context(function):
        if history is None and not context:
            return function(message)
        return function(message, history=history, context=context)

    if provider == "groq":
        return ask_with_context(ask_groq)
    if provider in ["gemini", "geminai"]:
        return ask_with_context(ask_gemini)
    if provider not in ["", "auto"]:
        raise ValueError(f"Unknown AI provider: {provider}")

    # 1. Primary: Groq
    if groq_client:
        try:
            return ask_with_context(ask_groq)
        except Exception as groq_err:
            print(f"WARN [Brain]: Groq request failed ({groq_err}). Falling back to Gemini...")

    # 2. Secondary / Fallback: Gemini
    if gemini_client:
        try:
            return ask_with_context(ask_gemini)
        except Exception as gemini_err:
            print(f"ERROR [Brain]: Gemini also failed ({gemini_err}).")
            raise gemini_err

    raise RuntimeError("No AI engine (Groq or Gemini) is currently configured or reachable.")
