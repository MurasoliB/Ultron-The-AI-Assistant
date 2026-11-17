import os
import subprocess
import webbrowser
import pyautogui
import psutil
from commands import (
    APP_MAPPINGS,
    close_app_by_name,
    get_current_brightness,
    launch_app,
    launch_app_with_path,
    launch_any_app,
    mute_system_volume,
    set_brightness,
    set_system_volume,
    _open_url_in_browser,
)
from intent_router import known_website_url
from urllib.parse import quote_plus

def execute_action(action_type: str, target: str = "", extra: str = "") -> str:
    """
    Executes a real Windows action decided by Ultron's neural brain.
    """
    try:
        if action_type == "open_app":
            app_name = target.strip()
            if not app_name or len(app_name) > 120 or any(
                char in app_name for char in "\r\n\x00"
            ):
                return "I need a valid application name."
            app_key = app_name.lower()
            website_url = known_website_url(app_key)
            if website_url:
                if not _open_url_in_browser(website_url):
                    return f"I couldn't open {app_name}."
                return f"Opening {app_name}."
            if app_key in APP_MAPPINGS:
                launch_command = APP_MAPPINGS[app_key]
                if launch_command.startswith("http"):
                    webbrowser.open(launch_command)
                    return f"Opening {target}."
                if not launch_app(app_key, launch_command):
                    return f"Unable to open {target}. Windows could not start that application."
                return f"Opening {target}."
            if not launch_any_app(app_name):
                return f"Unable to open {target}. Windows could not start that application."
            return f"Opening {target}."

        elif action_type == "close_app":
            if close_app_by_name(target):
                return f"Closed {target}."
            return f"Could not find running process for {target}."

        elif action_type == "play_youtube":
            try:
                import pywhatkit
                pywhatkit.playonyt(target)
                return f"Playing {target} on YouTube."
            except Exception:
                webbrowser.open(
                    f"https://www.youtube.com/results?search_query={quote_plus(target)}"
                )
                return f"Opening {target} on YouTube."

        elif action_type == "web_search":
            webbrowser.open(f"https://www.google.com/search?q={quote_plus(target)}")
            return f"Searching Google for {target}."

        elif action_type == "open_website":
            site, separator, browser = target.rpartition("|")
            site = site.strip() if separator else target.strip()
            browser = browser.strip() if separator else ""
            url = site if site.startswith(("http://", "https://")) else f"https://{site}"
            if not _open_url_in_browser(url, browser or None):
                if browser:
                    return f"I couldn't open {site}: {browser} isn't available."
                return "I couldn't open that website."
            return f"Opening {site}" + (f" in {browser}." if browser else ".")

        elif action_type == "open_folder":
            folder = os.path.abspath(os.path.expandvars(os.path.expanduser(target.strip())))
            if not os.path.isdir(folder):
                return f"The folder isn't available at {folder}."
            os.startfile(folder)
            return f"Opening folder {os.path.basename(folder)}."

        elif action_type == "open_project":
            folder, separator, editor = target.rpartition("|")
            folder = folder.strip() if separator else target.strip()
            editor = editor.strip() if separator else "VS Code"
            folder = os.path.abspath(os.path.expandvars(os.path.expanduser(folder)))
            if not os.path.isdir(folder):
                return f"The project folder isn't available at {folder}."
            if not launch_app_with_path(editor, folder):
                return f"I found the project folder, but couldn't open it in {editor}."
            return f"Opening {os.path.basename(folder)} in {editor}."

        elif action_type == "set_volume":
            try:
                vol = int(target)
                if not set_system_volume(vol):
                    return "Windows could not change the system volume."
                return f"Volume set to {vol} percent."
            except ValueError:
                return "I need a valid volume percentage between 0 and 100."

        elif action_type == "mute":
            if not mute_system_volume(True):
                return "Windows could not mute the system audio."
            return "System audio muted."

        elif action_type == "unmute":
            if not mute_system_volume(False):
                return "Windows could not unmute the system audio."
            return "System audio unmuted."

        elif action_type == "screenshot":
            path = os.path.join(os.path.expanduser("~"), "Desktop", "ultron_screenshot.png")
            pyautogui.screenshot(path)
            return "Screenshot captured and saved to your desktop."

        elif action_type in ["brightness_set", "set_brightness"]:
            try:
                val = int(target)
                if not set_brightness(val):
                    return "Windows could not change the screen brightness."
                return f"Brightness set to {val} percent."
            except ValueError:
                return "I need a valid brightness percentage between 0 and 100."

        elif action_type in ["brightness_up", "increase_brightness"]:
            current = get_current_brightness()
            new_value = min(100, current + 10)
            if not set_brightness(new_value):
                return "Windows could not change the screen brightness."
            return f"Brightness increased to {new_value} percent."

        elif action_type in ["brightness_down", "decrease_brightness"]:
            current = get_current_brightness()
            new_value = max(0, current - 10)
            if not set_brightness(new_value):
                return "Windows could not change the screen brightness."
            return f"Brightness decreased to {new_value} percent."

        elif action_type in ["screen_record", "record_screen"]:
            pyautogui.hotkey('win', 'alt', 'r')
            return "Starting screen recording."

        elif action_type == "lock":
            subprocess.Popen(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                shell=False,
            )
            return "Workstation locked."

        elif action_type == "show_desktop":
            pyautogui.hotkey('win', 'd')
            return "Displaying desktop."

        elif action_type == "media_toggle":
            pyautogui.press("playpause")
            return "Toggling media playback."

        elif action_type == "media_next":
            pyautogui.press("nexttrack")
            return "Skipping to next track."

        elif action_type == "system_stats":
            battery = psutil.sensors_battery()
            cpu = psutil.cpu_percent(interval=0.3)
            ram = psutil.virtual_memory().percent
            b_str = f"Battery at {battery.percent}%" if battery else "No battery detected"
            return f"CPU load is {cpu} percent, memory at {ram} percent, and {b_str}."

        elif action_type == "hotkey":
            keys = [k.strip() for k in target.split("+")]
            pyautogui.hotkey(*keys)
            return f"Executed shortcut {target}."

        return ""

    except (FileNotFoundError, NotADirectoryError):
        return "The requested application or file is not available at that location."
    except PermissionError:
        return "Windows denied permission for that action. Nothing else was changed."
    except Exception as error:
        print(f"[Action Error] {action_type}: {type(error).__name__}")
        return "I couldn't complete that Windows action. Nothing else was changed."
