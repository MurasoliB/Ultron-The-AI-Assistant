import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import commands
import config
import filesystem_tools as fs
import folder_tools
from intent_router import Intent, classify_intent
from tool_registry import ToolRegistry


class FolderOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.folders = {
            name: self.root / name.title()
            for name in ("desktop", "documents", "downloads", "pictures", "videos", "music")
        }
        for folder in self.folders.values():
            folder.mkdir()
        self.project = self.root / "Projects"
        self.project.mkdir()
        fs.filesystem_context.clear()

        self.patches = [
            patch.object(
                fs, "_known_folder_path",
                side_effect=lambda name: self.folders[name],
            ),
            patch.object(config, "FILE_SEARCH_ROOTS", []),
            patch("commands.ensure_folder_index_ready", return_value=True),
            patch("commands.ensure_file_index_ready", return_value=True),
            patch("commands._find_folder_in_index", return_value=[]),
            patch("commands._find_in_index", return_value=[]),
            patch("folder_tools.os.startfile"),
        ]
        for item in self.patches:
            item.start()
        self.startfile = self.patches[-1].start()
        self.registry = ToolRegistry()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        fs.filesystem_context.clear()
        self.temp_dir.cleanup()

    def command(self, text):
        with patch("folder_tools.subprocess.Popen"):
            return self.registry.execute_native_command(text)

    def opened(self, text):
        self.startfile.reset_mock()
        self.command(text)
        return [call.args[0] for call in self.startfile.call_args_list]

    # -----------------------------------------------------------------
    # Well-known folders work under every phrasing
    # -----------------------------------------------------------------
    def test_every_shell_folder_phrasing_opens_the_same_folder(self):
        phrasings = [
            "open downloads",
            "open the downloads folder",
            "open my downloads folder",
            "open folder downloads",
            "open downloads folder",
            "open download folder",
            "show me downloads",
            "go to downloads",
            "take me to the downloads folder",
            "open the folder downloads",
            "open downloads in explorer",
            "open downloads in file explorer",
        ]
        for text in phrasings:
            with self.subTest(text=text):
                self.assertEqual(
                    self.opened(text),
                    [str(self.folders["downloads"])],
                )

    def test_each_shell_folder_resolves_to_its_own_location(self):
        for name, folder in self.folders.items():
            with self.subTest(folder=name):
                self.assertEqual(self.opened(f"open {name} folder"), [str(folder)])

    def test_shell_folder_is_preferred_over_a_same_named_application(self):
        # "desktop" also matches Remote Desktop Connection on the Start Menu.
        self.assertEqual(self.opened("open desktop"), [str(self.folders["desktop"])])

    def test_absolute_path_and_drive_letter_open(self):
        self.assertEqual(
            self.opened(f"open {self.folders['pictures']}"),
            [str(self.folders["pictures"])],
        )

    def test_ultron_project_folder_resolves_without_the_word_folder(self):
        with patch.object(folder_tools, "_PROJECT_ROOT", str(self.project)):
            self.assertEqual(self.opened("open ultron"), [str(self.project)])

    def test_location_suffix_is_resolved_before_the_name(self):
        (self.folders["documents"] / "Notes").mkdir()
        self.assertEqual(
            self.opened("open notes in documents"),
            [str(self.folders["documents"] / "Notes")],
        )

    # -----------------------------------------------------------------
    # Applications, websites and files keep their own handlers
    # -----------------------------------------------------------------
    def test_application_names_are_left_to_the_application_handler(self):
        with patch("folder_tools.commands._resolve_app_path", return_value=r"C:\Apps\thing.exe"), \
             patch("commands.launch_any_app", return_value=True) as launch:
            response = self.registry.execute_native_command("open thing")
        launch.assert_called_once_with("thing")
        self.assertIn("Opening thing", response)
        self.startfile.assert_not_called()

    def test_known_website_is_left_to_the_browser_handler(self):
        with patch("commands._open_url_in_browser", return_value=True):
            response = self.registry.execute_native_command("open github")
        self.assertIn("Opening github", response)
        self.startfile.assert_not_called()

    def test_single_unknown_word_is_not_treated_as_a_folder(self):
        with patch("folder_tools.commands._resolve_app_path", return_value=""), \
             patch("commands._find_folder_in_index", return_value=[]), \
             patch("commands.launch_any_app", return_value=False):
            self.registry.execute_native_command("open zzzq")
        self.startfile.assert_not_called()

    def test_program_folders_are_never_reached_by_fuzzy_matching(self):
        with patch("folder_tools.commands._find_folder_in_index",
                   return_value=[r"C:\Program Files\Some App"]), \
             patch("folder_tools.commands._guess_file_path", return_value=None):
            response = self.command("open the some app folder")
        self.startfile.assert_not_called()
        self.assertIn("could not find a folder", response)

    # -----------------------------------------------------------------
    # Ambiguity, pronouns and clear failures
    # -----------------------------------------------------------------
    def test_bare_folder_word_asks_instead_of_guessing(self):
        response = self.command("open the folder")
        self.startfile.assert_not_called()
        self.assertIn("Name the folder", response)

    def test_pronoun_reopens_the_last_touched_folder(self):
        (self.root / "Alpha").mkdir()
        fs.filesystem_context.record(str(self.root / "Alpha"), "create", "folder")
        self.assertEqual(self.opened("open it"), [str(self.root / "Alpha")])

    def test_ambiguous_name_lists_the_candidates(self):
        first = self.root / "One" / "Shared"
        second = self.root / "Two" / "Shared"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        # Temporary folders live under %LOCALAPPDATA%, which the resolver
        # normally refuses to reach, so disable that guard for this case.
        with patch("folder_tools._PROGRAM_PREFIXES", ()), \
             patch("folder_tools.commands._guess_file_path", return_value=None), \
             patch("folder_tools.commands._find_folder_in_index",
                   return_value=[str(first), str(second)]):
            response = self.command("open the shared folder")
        self.startfile.assert_not_called()
        self.assertIn("I found 2 folders", response)

    def test_compound_request_is_left_to_the_conversational_handler(self):
        with patch("folder_tools._index_candidates") as index:
            folder_tools.open_folder_request("open downloads and tell me the time")
        index.assert_not_called()

    def test_show_me_is_not_read_as_a_directory_listing(self):
        self.assertIsNone(fs.parse_filesystem_command("show me downloads"))

    # -----------------------------------------------------------------
    # Intent classification
    # -----------------------------------------------------------------
    def test_shell_folders_and_drives_classify_as_open_folder(self):
        cases = [
            "open downloads", "go to downloads", "show me downloads",
            "open the documents folder", "open my pictures",
            "open c drive", "open d:", "take me to the videos folder",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_intent(text).intent, Intent.OPEN_FOLDER)

    def test_applications_and_sites_still_classify_their_own_way(self):
        cases = [
            ("open chrome", Intent.OPEN_APPLICATION),
            ("open notepad", Intent.OPEN_APPLICATION),
            ("open github", Intent.OPEN_URL),
            ("open youtube", Intent.OPEN_URL),
            ("search the web for rain", Intent.SEARCH_WEB),
            ("close chrome", Intent.CLOSE_APPLICATION),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_intent(text).intent, expected)


if __name__ == "__main__":
    unittest.main()