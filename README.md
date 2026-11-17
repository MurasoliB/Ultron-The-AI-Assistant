# ULTRON Desktop AI Assistant

A Python-based desktop voice assistant and Windows automation tool that provides real-time voice interaction, OS-level input emulation, an always-on-top document viewer, automated email management, and deep system controls.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [System Requirements](#system-requirements)
- [Installation Guide](#installation-guide)
  - [Automated Setup (Recommended)](#automated-setup-recommended)
  - [Manual Setup](#manual-setup)
- [Environment Configuration](#environment-configuration)
- [Running the Assistant](#running-the-assistant)
- [Global Keyboard Shortcuts](#global-keyboard-shortcuts)
- [Folder Structure](#folder-structure)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Overview

ULTRON is an intelligent desktop companion designed for Windows 10 and 11. Built with Python, Win32 APIs, Google Gemini, and Groq, it provides low-latency voice synthesis, speech recognition, window management, document viewing, and hardware-level keystroke injection for coding environments and web editors.

---

## Key Features

### 1. Floating Voice HUD (`mic_button.py`)
- Borderless, circular user interface with custom canvas animations.
- Always-on-top window layering using Win32 API (`WS_EX_TOOLWINDOW`).
- Color-coded operational states: idle, listening, processing, speaking, and alert.

### 2. Always-on-Top Document Agent (`doc_agent.py`)
- Floating reader for viewing lab manuals, assignments, and source code.
- High-resolution visual PDF rendering with page navigation and zoom via PyMuPDF.
- Line-numbered code viewer for Java, Python, C, C++, JavaScript, and HTML.

### 3. Keystroke Injection & Typing Automation (`code_agent.py`)
- Emulates keystrokes at the OS level using Windows `SendInput`.
- Sends Unicode and scan codes without relying on standard clipboard paste operations.
- Click-to-inject functionality for transferring code into web-based editors.

### 4. Unified Window Synchronization (`ultron_sync.py`)
- Master hotkey (`F6`) that minimizes or restores all floating components simultaneously.
- Cross-process state coordination with automatic debouncing.
- Windows-level hotkey (`Ctrl + Alt + M`) to launch or focus the application.

### 5. Multi-Tier Voice and Speech Recognition (`voice.py`)
- Text-to-speech using Microsoft Edge Neural TTS or local Piper offline models.
- Speech-to-text with multiple fallbacks: Groq Whisper, Google Speech API, Gemini STT, local Whisper, and Vosk.

### 6. Email Automation (`inbox.py`)
- Automated Gmail handling via IMAP and SMTP.
- Read unread messages, summarize message bodies, and compose outgoing emails.
- Support for switching between primary and secondary accounts.

### 7. System Tools and Desktop Automation (`tools/`, `commands.py`)
- Control volume, brightness, and active windows.
- Launch or close applications (Chrome, VS Code, Calculator, Notepad, etc.).
- Media playback controls and web searching.

---

## Architecture

```
User Voice / Hotkeys / UI
          |
          v
   ultron_sync.py (IPC Coordinator)
     /                     \
    v                       v
mic_button.py (HUD)    doc_agent.py (Viewer)
    \                       /
     v                     v
   brain.py & assistant.py (LLM Brain)
     /        |          |        \
    v         v          v         v
voice.py  code_agent.py inbox.py tools/
```

---

## System Requirements

- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Python Version:** Python 3.10, 3.11, or 3.12 (64-bit)
- **Hardware:**
  - Standard microphone and speakers / headphones
  - 4 GB RAM minimum (8 GB recommended)
- **Network:** Internet access for cloud LLM and TTS APIs (offline modes available)

---

## Installation Guide

### Automated Setup (Recommended)

1. Clone or download the repository:
   ```bash
   git clone https://github.com/MurasoliB/Ultron-The-AI-Assistant.git
   cd Ultron-The-AI-Assistant
   ```

2. Run `setup.bat`:
   - Double-click `setup.bat` or run it from the command line.
   - This script creates a virtual environment, installs dependencies, and prepares `.env`.

3. Configure your API keys in `.env` (see below).

4. Run `start_mic.bat` or `start_ultron_ui.bat`.

---

### Manual Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/MurasoliB/Ultron-The-AI-Assistant.git
   cd Ultron-The-AI-Assistant
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Prepare the configuration file:**
   ```powershell
   copy .env.example .env
   ```

5. **Edit `.env`** with your API credentials.

6. **Start the assistant:**
   ```powershell
   python mic_button.py
   ```

---

## Environment Configuration

Create a `.env` file in the project root based on `.env.example`:

```ini
# Google Gemini API Key (Core reasoning and conversation)
GEMINI_API_KEY=your_gemini_api_key_here

# Groq API Key (High-speed speech transcription)
GROQ_API_KEY=your_groq_api_key_here

# Optional: xAI Key
XAI_API_KEY=your_xai_api_key_here

# Gmail Automation (Optional)
GMAIL_EMAIL=your_email@gmail.com
GMAIL_APP_PASSWORD=your_16_character_app_password

# Voice Synthesis Configuration
ULTRON_TTS_ENGINE=edge
ULTRON_EDGE_VOICE=en-US-ChristopherNeural
ULTRON_EDGE_PITCH=-16Hz
ULTRON_EDGE_RATE=-15%
ULTRON_USE_GOOGLE_STT=1
```

---

## Running the Assistant

| Batch File | Mode | Description |
| :--- | :--- | :--- |
| `start_mic.bat` | Floating HUD | Launches the circular desktop overlay |
| `start_ultron_ui.bat` | Full GUI | Launches the complete desktop interface |
| `start_ultron.bat` | Console | Runs in standard terminal mode |
| `START_ULTRON_ADMIN.bat` | Administrator Mode | Elevates privileges for restricted software input |
| `START_CODE_AGENT.bat` | Document & Code Agent | Opens the standalone floating document reader |

---

## Global Keyboard Shortcuts

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `Ctrl + Alt + M` | Launch Mic | Launches or focuses the floating voice overlay from anywhere |
| `F6` | Master Stealth Toggle | Minimizes both widgets if visible, or restores both if hidden |
| `F7` | Arm Click-to-Inject | Arms code typing; clicking in an editor begins typing |
| `F8` | Type Active Code | Immediately begins hardware keystroke injection |
| `F9` / `Esc` | Abort Typing | Halts keystroke injection immediately |

---

## Folder Structure

```
.
├── .env.example             # Template for API keys and configuration
├── .gitignore               # Excludes secrets, large models, and cache
├── requirements.txt         # Project dependencies
├── setup.bat                # Automated one-click installer
│
├── actions.py               # Action execution handlers
├── assistant.py             # Main assistant orchestration logic
├── brain.py                 # Gemini and Groq model interfaces
├── code_agent.py            # Win32 keystroke injection engine
├── commands.py              # Command detection and parsing
├── config.py                # System settings and configuration
├── conversation.py          # Session history management
├── doc_agent.py             # Always-on-top document and code viewer
├── filesystem_tools.py      # Local file searching and navigation
├── folder_tools.py          # Quick-access folder utilities
├── gate.py                  # Confirmation safety controls
├── inbox.py                 # Gmail automation (IMAP/SMTP)
├── intent_router.py         # Query classification and routing
├── main.py                  # Voice event loop
├── mcp_manager.py           # Model Context Protocol integration
├── mcp_servers.json         # MCP tool definitions
├── memory.py                # Persistent memory manager
├── mic_button.py            # Floating circular HUD interface
├── telemetry.py             # Logging and diagnostics
├── tool_registry.py         # System tool registration
├── ui.py                    # Complete desktop GUI
├── ultron_sync.py           # Cross-process synchronization helper
├── voice.py                 # TTS synthesis and STT transcription
│
├── tools/                   # Modular utilities
│   ├── system.py            # Volume, brightness, and power management
│   ├── web.py               # Web search and scraping
│   └── windows.py           # Window management
│
└── start scripts:
    ├── start_mic.bat
    ├── start_ultron_ui.bat
    ├── start_ultron.bat
    ├── START_ULTRON_ADMIN.bat
    └── setup_local_tts.bat
```

---

## Troubleshooting

1. **PyAudio installation errors:**
   ```powershell
   pip install pipwin
   pipwin install pyaudio
   ```
   Alternatively, install a precompiled wheel matching your Python version.

2. **Keystroke injection blocked by elevated application:**
   Run `START_ULTRON_ADMIN.bat` as Administrator so input emulation has sufficient privileges.

3. **Gmail authentication errors:**
   Ensure 2-Step Verification is active on your Google account and generate an App Password under Security settings.

4. **Microphone not detected:**
   Check Windows Privacy settings to ensure desktop applications have permission to access your microphone.

---

## License

This project is open-source under the [MIT License](LICENSE).
