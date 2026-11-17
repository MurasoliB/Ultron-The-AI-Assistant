import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from actions import execute_action
from brain import ask_ai
from commands import check_windows_system_command, check_app_command, handle_command, normalize_command
from intent_router import Intent, classify_intent
import voice
import commands
from mic_button import MicButton


class UltronControlTests(unittest.TestCase):
    def test_mic_ring_uses_original_emotion_color(self):
        mic = MicButton.__new__(MicButton)

        self.assertEqual(mic._get_language_ring_color("#ff0033"), "#ff0033")
        self.assertEqual(mic._get_language_ring_color("#00f0ff"), "#00f0ff")

    def test_intent_classifier_separates_apps_sites_projects_and_searches(self):
        context = (
            "Current project workspace: ULTRON personal AI assistant "
            "(folder jarvis; C:\\Users\\ELCOT\\jarvis)\n"
            "Recent folders: C:\\Users\\ELCOT\\jarvis\\TestProject"
        )
        cases = [
            ("Open Calculator", Intent.OPEN_APPLICATION),
            ("Open Chrome", Intent.OPEN_APPLICATION),
            ("Open ChatGPT in Brave", Intent.OPEN_URL),
            ("Go to ChatGPT using Brave", Intent.OPEN_URL),
            ("Take me to ChatGPT in Brave", Intent.OPEN_URL),
            ("Open ChatGPT", Intent.OPEN_URL),
            ("Open example.com", Intent.OPEN_URL),
            ("Open YouTube in Chrome", Intent.OPEN_URL),
            ("Open VS Code", Intent.OPEN_APPLICATION),
            ("Open VS Code in my ULTRON folder", Intent.OPEN_PROJECT),
            ("Open my ULTRON project in VS Code", Intent.OPEN_PROJECT),
            ("Open the calculator project", Intent.OPEN_PROJECT),
            ("Open that folder", Intent.OPEN_FOLDER),
            ("Search the web for Python tutorials", Intent.SEARCH_WEB),
            ("Find calculator.py on my computer", Intent.SEARCH_LOCAL),
            ("Create a folder called TestProject", Intent.CREATE_FOLDER),
            ("Open TestProject", Intent.OPEN_FOLDER),
            ("Open it in VS Code", Intent.OPEN_PROJECT),
            ("Close VS Code", Intent.CLOSE_APPLICATION),
            ("Open VS Code again", Intent.OPEN_APPLICATION),
            ("Increase the volume", Intent.VOLUME_CONTROL),
            ("Take a screenshot", Intent.SCREENSHOT),
            ("Show me my current project folder", Intent.SHOW_PROJECT_FOLDER),
            ("Open VS Code in C:\\Projects\\ULTRON", Intent.OPEN_PROJECT),
            ("Open C:\\Projects\\ULTRON", Intent.OPEN_FOLDER),
            ("Open Photoshop", Intent.OPEN_APPLICATION),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(classify_intent(command, context).intent, expected)

    def test_open_chatgpt_in_brave_uses_explicit_browser(self):
        with patch(
            "commands._open_url_in_browser",
            return_value=True,
        ) as open_url, patch("commands.webbrowser.open") as default_browser:
            result = handle_command("Open ChatGPT in Brave")

        self.assertIn("Brave", result)
        open_url.assert_called_once_with("https://chatgpt.com", "Brave")
        default_browser.assert_not_called()

    def test_open_chatgpt_without_browser_opens_known_website(self):
        with patch("commands._open_url_in_browser", return_value=True) as open_url:
            result = handle_command("Open ChatGPT")

        self.assertIn("Opening ChatGPT", result)
        open_url.assert_called_once_with("https://chatgpt.com", None)

    def test_open_youtube_in_chrome_opens_directly_in_chrome(self):
        with patch("commands._open_url_in_browser", return_value=True) as open_url:
            result = handle_command("Open YouTube in Chrome")

        self.assertIn("Chrome", result)
        open_url.assert_called_once_with("https://www.youtube.com", "Chrome")

    def test_missing_application_never_falls_back_to_file_search(self):
        with patch("commands.launch_any_app", return_value=False), \
             patch("commands._find_in_index") as search_files:
            result = handle_command("Open Calculator")

        self.assertIn("wasn't resolved as an installed application", result)
        search_files.assert_not_called()

    def test_installed_application_request_uses_application_resolver(self):
        with patch("commands.launch_any_app", return_value=True) as launch, \
             patch("commands._find_in_index") as search_files:
            result = handle_command("Open Chrome")

        self.assertIn("Opening Chrome", result)
        launch.assert_called_once_with("Chrome")
        search_files.assert_not_called()

    def test_explicit_browser_failure_does_not_open_default_browser(self):
        browser_map = {name: None for name in commands.BROWSER_EXE_MAP}
        with patch.dict(commands.BROWSER_EXE_MAP, browser_map), \
             patch("commands._resolve_app_path", return_value=None), \
             patch("commands.subprocess.Popen", side_effect=OSError), \
             patch("commands.webbrowser.open") as default_browser:
            result = handle_command("Open ChatGPT in Brave")

        self.assertIn("Brave isn't available", result)
        default_browser.assert_not_called()

    def test_explicit_browser_can_resolve_start_menu_or_registered_app(self):
        browser_map = {name: None for name in commands.BROWSER_EXE_MAP}
        with patch.dict(commands.BROWSER_EXE_MAP, browser_map), \
             patch("commands._resolve_app_path", return_value=r"C:\Apps\Brave\brave.exe"), \
             patch("commands.os.path.exists", return_value=True), \
             patch("commands.subprocess.Popen") as launch:
            self.assertTrue(
                commands._open_url_in_browser("https://chatgpt.com", "Brave")
            )

        launch.assert_called_once_with(
            [r"C:\Apps\Brave\brave.exe", "https://chatgpt.com"],
            shell=False,
        )

    def test_local_file_search_is_distinct_from_web_search(self):
        with patch("commands._find_in_index", return_value=[r"C:\Projects\calculator.py"]) as find:
            result = handle_command("Find calculator.py on my computer")

        self.assertIn("calculator.py", result)
        find.assert_called_once_with("calculator.py")

    def test_project_request_launches_editor_with_exact_folder(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("commands._resolve_app_path", return_value="code.exe"), \
             patch("commands.subprocess.Popen") as launch:
            result = handle_command(f"Open VS Code in {folder}")

        self.assertIn("in VS Code", result)
        launch.assert_called_once_with(["code.exe", folder], shell=False)

    def test_ultron_project_name_resolves_to_current_workspace(self):
        workspace = os.path.dirname(os.path.abspath(commands.__file__))
        with patch("commands.launch_app_with_path", return_value=True) as launch:
            result = handle_command("Open my ULTRON project in VS Code")

        self.assertIn("VS Code", result)
        launch.assert_called_once_with("VS Code", workspace)

    def test_named_project_uses_folder_index_and_reports_ambiguity(self):
        with patch(
            "commands._find_folder_in_index",
            return_value=[r"C:\Projects\Calculator"],
        ) as find, patch("commands.launch_app_with_path", return_value=True) as launch:
            result = handle_command("Open the calculator project")

        self.assertIn("Calculator", result)
        find.assert_called_once_with("calculator")
        launch.assert_called_once_with("VS Code", r"C:\Projects\Calculator")

        with patch(
            "commands._find_folder_in_index",
            return_value=[r"C:\A\Calculator", r"C:\B\Calculator"],
        ):
            ambiguous = handle_command("Open the calculator project")
        self.assertIn("multiple folders", ambiguous)

    def test_recent_folder_followups_open_folder_or_launch_editor(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = os.path.join(parent, "TestProject")
            os.mkdir(folder)
            context = f"Recent folders: {folder}"

            with patch("commands.os.startfile") as open_folder:
                result = handle_command("Open TestProject", context=context)
            self.assertIn("TestProject", result)
            open_folder.assert_called_once_with(folder)

            with patch("commands.os.startfile") as open_folder:
                result = handle_command("Open that folder", context=context)
            self.assertIn("TestProject", result)
            open_folder.assert_called_once_with(folder)

            with patch("commands.launch_app_with_path", return_value=True) as launch:
                result = handle_command("Open it in VS Code", context=context)
            self.assertIn("VS Code", result)
            launch.assert_called_once_with("VS Code", folder)

    def test_named_browser_web_search_uses_encoded_query(self):
        with patch("commands._open_url_in_browser", return_value=True) as open_url:
            result = handle_command("Search the web for Python tutorials in Brave")

        self.assertIn("Python tutorials", result)
        open_url.assert_called_once_with(
            "https://www.google.com/search?q=Python+tutorials",
            "Brave",
        )

    def test_open_it_in_brave_uses_previous_website_context(self):
        context = "Current task: Open ChatGPT"
        with patch("commands._open_url_in_browser", return_value=True) as open_url:
            result = handle_command("Open it in Brave", context=context)

        self.assertIn("Brave", result)
        open_url.assert_called_once_with("https://chatgpt.com", "Brave")

    def test_open_unknown_bare_name_uses_app_resolver_not_folder_search(self):
        with patch("commands.launch_any_app", return_value=False) as launch, \
             patch("commands._find_in_index") as file_search, \
             patch("commands._find_folder_in_index") as folder_search:
            result = handle_command("Open Photoshop")

        self.assertIn("wasn't resolved as an installed application", result)
        launch.assert_called_once_with("Photoshop")
        file_search.assert_not_called()
        folder_search.assert_not_called()

    def test_ai_website_action_preserves_requested_browser(self):
        with patch("actions._open_url_in_browser", return_value=True) as open_url:
            result = execute_action("open_website", "chatgpt.com|Brave")

        self.assertIn("in Brave", result)
        open_url.assert_called_once_with("https://chatgpt.com", "Brave")

    def test_ai_cannot_route_chatgpt_to_local_application_search(self):
        with patch("actions._open_url_in_browser", return_value=True) as open_url, \
             patch("actions.launch_any_app") as launch_app:
            result = execute_action("open_app", "chatgpt")

        self.assertIn("Opening chatgpt", result)
        open_url.assert_called_once_with("https://chatgpt.com")
        launch_app.assert_not_called()

    def test_ai_project_action_uses_existing_folder_and_editor_tool(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("actions.launch_app_with_path", return_value=True) as launch:
            result = execute_action("open_project", f"{folder}|VS Code")

        self.assertIn("VS Code", result)
        launch.assert_called_once_with("VS Code", folder)

    def test_existing_volume_and_screenshot_controls_remain_routed(self):
        with patch("commands.pyautogui.press") as press:
            self.assertIn("Increasing volume", handle_command("Increase the volume"))
        self.assertEqual(press.call_count, 5)

        with patch("commands.pyautogui.screenshot") as screenshot:
            self.assertIn("Screenshot captured", handle_command("Take a screenshot"))
        screenshot.assert_called_once()

    def test_show_current_project_folder_reports_workspace_path(self):
        result = handle_command("Show me my current project folder")
        self.assertIn(os.path.dirname(os.path.abspath(commands.__file__)), result)

    def test_groq_transcription_uses_english_and_indian_accent_guidance(self):
        client = Mock()
        client.audio.transcriptions.create.return_value.text = "Ultron, open VS Code"
        with patch.object(voice, "_groq_client", client):
            transcript = voice._recognize_groq(b"\x00\x00" * 1600)

        self.assertEqual(transcript, "Ultron, open VS Code")
        options = client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(options["language"], "en")
        self.assertIn("Indian English", options["prompt"])

    def test_piper_starts_playback_before_synthesizing_remaining_chunks(self):
        events = []
        first_chunk = Mock(
            sample_width=2,
            sample_channels=1,
            sample_rate=22050,
            audio_int16_bytes=b"\x01\x00" * 4,
        )
        second_chunk = Mock(
            sample_width=2,
            sample_channels=1,
            sample_rate=22050,
            audio_int16_bytes=b"\x02\x00" * 4,
        )

        def synthesize(*_args, **_kwargs):
            events.append("first chunk synthesized")
            yield first_chunk
            events.append("second chunk synthesized")
            yield second_chunk

        piper_voice = Mock()
        piper_voice.synthesize.side_effect = synthesize
        audio = Mock()
        playback = Mock()
        audio.open.return_value = playback
        playback.write.side_effect = lambda _pcm: events.append("audio played")

        with patch.object(voice, "_load_piper_voice", return_value=piper_voice), \
             patch.object(voice.pyaudio, "PyAudio", return_value=audio), \
             patch.object(voice, "_log_voice_event"):
            self.assertTrue(voice._piper_speak("hello"))

        self.assertEqual(
            events,
            [
                "first chunk synthesized",
                "audio played",
                "second chunk synthesized",
                "audio played",
            ],
        )
        self.assertEqual(playback.write.call_count, 2)
        self.assertEqual(audio.open.call_args.kwargs["rate"], 22050)
        self.assertEqual(
            piper_voice.synthesize.call_args.kwargs["syn_config"].length_scale,
            0.88,
        )

    def test_speak_uses_local_piper_voice(self):
        with patch.object(voice, "_edge_ultron_speak", return_value=False), \
             patch.object(voice, "_piper_speak", return_value=True) as piper_speak, \
             patch.object(voice.time, "sleep"):
            backend = voice.speak("hello")

        piper_speak.assert_called_once_with("hello")
        self.assertEqual(backend, "local-piper")

    def test_speak_does_not_fall_back_to_windows_voice(self):
        with patch.object(voice, "_edge_ultron_speak", return_value=False), \
             patch.object(voice, "_piper_speak", return_value=False), \
             patch.object(voice.time, "sleep"):
            backend = voice.speak("hello")

        self.assertEqual(backend, "failed")

    def test_floating_mic_tests_local_neural_voice(self):
        mic = MicButton.__new__(MicButton)
        mic.root = Mock()
        mic.root.after.side_effect = lambda _delay, callback, *args: callback(*args)
        mic.is_speaking = False

        with patch.object(
                 voice, "speak", return_value="local-piper"
             ) as speak, \
             patch("mic_button.threading.Thread") as thread, \
             patch("mic_button.messagebox.showinfo") as showinfo:
            mic._test_local_neural_voice()
            thread.assert_called_once()
            thread.call_args.kwargs["target"]()
            thread.return_value.start.assert_called_once()

        speak.assert_called_once_with(
            "Sir, this is Ultron. Your local neural voice is online."
        )
        showinfo.assert_called_once()
        self.assertFalse(mic.is_speaking)

    def test_voice_transcription_prefers_groq(self):
        with patch.object(voice, "GEMINI_STT_API_KEY", "gemini-key"), \
             patch.object(voice, "GROQ_API_KEY", "groq-key"), \
             patch.object(voice, "_recognize_gemini", return_value="gemini transcript") as gemini, \
             patch.object(voice, "_recognize_groq", return_value="groq transcript") as groq, \
             patch.object(voice, "_recognize_whisper") as whisper, \
             patch.object(voice, "_recognize_vosk") as vosk:
            result = voice._transcribe_audio(b"audio")

        self.assertEqual(result, "groq transcript")
        groq.assert_called_once_with(b"audio", 16000)
        gemini.assert_not_called()
        whisper.assert_not_called()
        vosk.assert_not_called()

    def test_voice_transcription_uses_groq_then_gemini_then_local(self):
        with patch.object(voice, "GEMINI_STT_API_KEY", "gemini-key"), \
             patch.object(voice, "GROQ_API_KEY", "groq-key"), \
             patch.object(voice, "_recognize_gemini", return_value="gemini transcript") as gemini, \
             patch.object(voice, "_recognize_groq", return_value="") as groq, \
             patch.object(voice, "_recognize_whisper") as whisper, \
             patch.object(voice, "_recognize_vosk") as vosk:
            result = voice._transcribe_audio(b"audio")

        self.assertEqual(result, "gemini transcript")
        gemini.assert_called_once_with(b"audio", 16000)
        groq.assert_called_once_with(b"audio", 16000)
        whisper.assert_not_called()
        vosk.assert_not_called()

    def test_voice_transcription_falls_back_to_local_when_cloud_fails(self):
        with patch.object(voice, "GEMINI_STT_API_KEY", "gemini-key"), \
             patch.object(voice, "GROQ_API_KEY", "groq-key"), \
             patch.object(voice, "_recognize_gemini", return_value=""), \
             patch.object(voice, "_recognize_groq", return_value=""), \
             patch.object(voice, "_recognize_whisper", return_value="local transcript") as whisper, \
             patch.object(voice, "_recognize_vosk") as vosk:
            result = voice._transcribe_audio(b"audio")

        self.assertEqual(result, "local transcript")
        whisper.assert_called_once_with(b"audio", 16000)
        vosk.assert_not_called()

    def test_local_whisper_uses_fast_single_beam_decode(self):
        model = Mock()
        model.transcribe.return_value = ([Mock(text="fast transcript")], None)
        with patch.object(voice, "_load_whisper", return_value=model):
            result = voice._recognize_whisper(b"\x00\x00" * 1600)

        self.assertEqual(result, "fast transcript")
        self.assertEqual(model.transcribe.call_args.kwargs["beam_size"], 1)
        self.assertEqual(model.transcribe.call_args.kwargs["best_of"], 1)

    def test_ultron_dsp_voice_effect_preserves_audio_dimensions(self):
        pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00" * 100
        output = voice.apply_ultron_voice_effect(pcm, 22050)
        self.assertEqual(len(output), len(pcm))
        self.assertNotEqual(output, b"")

    def test_voice_transcription_uses_google_indian_when_no_cloud_keys(self):
        with patch.object(voice, "GEMINI_STT_API_KEY", ""), \
             patch.object(voice, "GROQ_API_KEY", ""), \
             patch.object(voice, "_recognize_google_indian", return_value="namaste ultron") as google, \
             patch.object(voice, "_recognize_whisper") as whisper, \
             patch.object(voice, "_recognize_vosk") as vosk:
            result = voice._transcribe_audio(b"audio")

        self.assertEqual(result, "namaste ultron")
        google.assert_called_once_with(b"audio", 16000)
        whisper.assert_not_called()
        vosk.assert_not_called()

    def test_speak_supports_edge_ultron_voice_when_configured(self):
        with patch.dict(os.environ, {"ULTRON_TTS_ENGINE": "edge"}), \
             patch.object(voice, "_edge_ultron_speak", return_value=True) as edge_speak, \
             patch.object(voice, "_piper_speak") as piper_speak, \
             patch.object(voice.time, "sleep"):
            backend = voice.speak("I am Ultron.")

        self.assertEqual(backend, "edge-ultron")
        edge_speak.assert_called_once_with("I am Ultron.")
        piper_speak.assert_not_called()

    def test_ai_provider_selection_routes_to_groq(self):
        with patch('brain.ask_groq', return_value='groq response') as groq, \
             patch('brain.ask_gemini') as gemini:
            self.assertEqual(ask_ai('hello', 'groq'), 'groq response')
            groq.assert_called_once_with('hello')
            gemini.assert_not_called()

    def test_ai_provider_selection_routes_to_gemini(self):
        with patch('brain.ask_gemini', return_value='gemini response') as gemini, \
             patch('brain.ask_groq') as groq:
            self.assertEqual(ask_ai('hello', 'gemini'), 'gemini response')
            gemini.assert_called_once_with('hello')
            groq.assert_not_called()

    def test_increase_brightness_command(self):
        with patch('commands.set_brightness', return_value=True) as mock_set:
            result = check_windows_system_command('increase brightness to 70')
            self.assertIsNotNone(result)
            self.assertIn('70', result)
            mock_set.assert_called_once_with(70)

    def test_natural_language_normalization(self):
        self.assertEqual(
            normalize_command("Hey Ultron, could you increase brightness to sixty please"),
            "increase brightness to 60"
        )
        self.assertEqual(normalize_command("please launch notepad"), "open notepad")
        self.assertEqual(normalize_command("open you deal"), "open youtube")

    def test_open_youtube_speech_alias(self):
        with patch('commands.webbrowser.open') as mock_open:
            result = handle_command('open you deal')
            self.assertIn('YouTube', result)
            mock_open.assert_called_once_with('https://www.youtube.com')

    def test_calculator_command_opens_and_calculates(self):
        with patch('commands.subprocess.Popen') as mock_popen, \
             patch('commands.time.sleep'), \
             patch('commands.enter_calculator_expression') as mock_enter:
            result = handle_command('open calculator and add 1+2')
            self.assertIn('result of 1+2 is 3', result)
            self.assertTrue(mock_popen.called)
            mock_enter.assert_called_once_with('1+2')

    def test_create_folder_command(self):
        with patch('commands.Path.mkdir') as mock_mkdir:
            result = handle_command('create a folder called projects')
            self.assertIn('Folder created at', result)
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_create_nested_folder_command(self):
        with patch('commands.Path.mkdir'):
            result = handle_command('create folder1 inside folder2')
            normalized_result = result.replace('/', '\\')
            self.assertIn('folder2', normalized_result)
            self.assertIn('folder1', normalized_result)
            self.assertLess(normalized_result.index('folder2'), normalized_result.index('folder1'))

    def test_calculator_input_uses_keyboard_and_mouse(self):
        with patch('commands.pygetwindow.getWindowsWithTitle') as mock_windows, \
             patch('commands.pyautogui.click') as mock_click, \
             patch('commands.pyautogui.hotkey') as mock_hotkey, \
             patch('commands.pyautogui.write') as mock_write, \
             patch('commands.pyautogui.press'), \
             patch('commands.pyperclip.copy'), \
             patch('commands.time.sleep'):
            window = mock_windows.return_value = [type('Window', (), {
                'isMinimized': False, 'left': 0, 'top': 0, 'width': 800, 'height': 600,
                'activate': lambda self: None
            })()]
            from commands import enter_calculator_expression
            self.assertTrue(enter_calculator_expression('1+2'))
            mock_click.assert_called()
            mock_write.assert_called_once_with('1+2', interval=0.08)
            self.assertIn(('ctrl', 'a'), [call.args for call in mock_hotkey.call_args_list])

    def test_calculator_command_supports_spoken_math(self):
           with patch('commands.subprocess.Popen'), patch('commands.time.sleep'), \
               patch('commands.enter_calculator_expression'):
            result = handle_command('open calculator and add 10 plus 5')
            self.assertIn('result of 10 + 5 is 15', result)

    def test_calculator_supports_other_operations(self):
        cases = [
            ('open calculator and subtract 10 minus 3', 'result of 10 - 3 is 7'),
            ('open calculator and multiply 6 times 4', 'result of 6 * 4 is 24'),
            ('open calculator and divide 20 by 5', 'result of 20 / 5 is 4'),
            ('open calculator and calculate (2+3)*4', 'result of (2+3)*4 is 20'),
        ]
        with patch('commands.subprocess.Popen'), patch('commands.time.sleep'), \
             patch('commands.enter_calculator_expression'):
            for command, expected in cases:
                self.assertIn(expected, handle_command(command))

    def test_decrease_brightness_command(self):
        with patch('commands.set_brightness', return_value=True) as mock_set:
            result = check_windows_system_command('decrease brightness to 30')
            self.assertIsNotNone(result)
            self.assertIn('30', result)
            mock_set.assert_called_once_with(30)

    def test_bluetooth_enable_command(self):
        with patch('commands.set_bluetooth_enabled', return_value=True) as mock_set:
            result = check_windows_system_command('turn on bluetooth')
            self.assertIn('enabled', result.lower())
            mock_set.assert_called_once_with(True)

    def test_hotspot_settings_command(self):
        with patch('commands.set_hotspot_enabled', return_value=True) as mock_set:
            result = check_windows_system_command('turn on hotspot')
            self.assertIn('hotspot', result.lower())
            mock_set.assert_called_once_with(True)

    def test_screen_record_command(self):
        with patch('commands.pyautogui.hotkey') as mock_hotkey:
            result = check_windows_system_command('screen record')
            self.assertIsNotNone(result)
            self.assertIn('recording', result.lower())
            mock_hotkey.assert_called_once_with('win', 'alt', 'r')

    def test_open_word_command(self):
        with patch('commands.subprocess.Popen') as mock_popen:
            result = check_app_command('open word')
            self.assertIsNotNone(result)
            self.assertIn('Word', result)
            self.assertTrue(any('winword.exe' in str(call.args[0]).lower() for call in mock_popen.call_args_list))

    def test_open_outlook_command(self):
        with patch('commands.subprocess.Popen') as mock_popen:
            result = check_app_command('open outlook')
            self.assertIn('Outlook', result)
            self.assertTrue(any('outlook.exe' in str(call.args[0]).lower() for call in mock_popen.call_args_list))

    def test_open_copilot_command(self):
        with patch('commands.subprocess.Popen') as mock_popen:
            result = check_app_command('open copilot')
            self.assertIn('Copilot', result)
            mock_popen.assert_called_once_with(
                ['explorer.exe', 'shell:AppsFolder\\Microsoft.Copilot_8wekyb3d8bbwe!App'], shell=False
            )

    def test_open_microsoft_store_command(self):
        with patch('commands.subprocess.Popen') as mock_popen:
            result = check_app_command('open microsoft store')
            self.assertIn('Microsoft Store', result)
            mock_popen.assert_called_once_with(
                ['explorer.exe', 'ms-windows-store:'], shell=False
            )

    def test_open_whatsapp_command(self):
        # WhatsApp is an MSIX package with no whatsapp.exe on PATH, so it can
        # only start through its application id.
        with patch('commands.subprocess.Popen') as mock_popen:
            result = check_app_command('open whatsapp')
            self.assertIn('Whatsapp', result)
            mock_popen.assert_called_once_with(
                ['explorer.exe', 'shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App'],
                shell=False,
            )

    def test_open_app_reports_launch_failure(self):
        with patch('commands.subprocess.Popen', side_effect=OSError):
            result = check_app_command('open notepad')
            self.assertIn('Unable to open', result)

    def test_ai_open_word_uses_word_executable(self):
        with patch('actions.launch_app', return_value=True) as mock_launch:
            result = execute_action('open_app', 'word')
            self.assertIn('Opening word', result)
            self.assertEqual(mock_launch.call_args.args[0], 'word')
            self.assertTrue(mock_launch.call_args.args[1].lower().endswith('winword.exe'))


if __name__ == '__main__':
    unittest.main()
