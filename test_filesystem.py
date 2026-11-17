import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import assistant
import commands
import config
import filesystem_tools as fs
from conversation import ConversationManager
from tool_registry import ToolRegistry


class FileSystemControlTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.folders = {
            name: self.root / name.title()
            for name in ("desktop", "documents", "downloads", "pictures", "videos", "music")
        }
        for folder in self.folders.values():
            folder.mkdir()
        fs.filesystem_context.clear()
        self.folder_patch = patch.object(
            fs,
            "_known_folder_path",
            side_effect=lambda name: self.folders[name],
        )
        self.folder_patch.start()
        self.registry = ToolRegistry()
        self.index_patches = [
            patch.object(config, "FILE_SEARCH_ROOTS", []),
            patch("commands.ensure_folder_index_ready", return_value=True),
            patch("commands.ensure_file_index_ready", return_value=True),
            patch("commands._find_folder_in_index", return_value=[]),
            patch("commands._find_in_index", return_value=[]),
        ]
        for index_patch in self.index_patches:
            index_patch.start()

    def tearDown(self):
        for index_patch in reversed(self.index_patches):
            index_patch.stop()
        self.folder_patch.stop()
        fs.filesystem_context.clear()
        self.temp_dir.cleanup()

    def command(self, text):
        return self.registry.execute_native_command(text)

    def test_create_folder_uses_exact_known_and_absolute_destination(self):
        response = self.command("Create a folder named Test1 on Desktop.")
        expected = self.folders["desktop"] / "Test1"
        self.assertTrue(expected.is_dir())
        self.assertIn(str(expected), response)

        absolute_parent = self.root / "Folder With Spaces"
        absolute_parent.mkdir()
        response = self.command(
            f'Create a folder called "Projects" inside "{absolute_parent}"'
        )
        self.assertTrue((absolute_parent / "Projects").is_dir())
        self.assertIn(str(absolute_parent / "Projects"), response)

    def test_create_folder_inside_relative_project_folder_and_delete_by_reference(self):
        project = self.root / "JARVIS"
        subjects = project / "subjects"
        subjects.mkdir(parents=True)

        with patch.object(fs, "_PROJECT_ROOT", project):
            response = self.command("create folder ITC inside subjects")
            target = subjects / "ITC"
            self.assertTrue(target.is_dir())
            self.assertIn(str(target), response)

            prompt = self.command("delete it")
            self.assertIn(str(target), prompt)
            result = self.registry.resolve_confirmation("yes")

        self.assertIn(str(target), result)
        self.assertFalse(target.exists())

    def test_delete_folder_by_name_and_relative_parent(self):
        project = self.root / "JARVIS"
        subjects = project / "subjects"
        target = subjects / "ITC"
        target.mkdir(parents=True)

        with patch.object(fs, "_PROJECT_ROOT", project):
            prompt = self.command("delete folder ITC inside subjects")
            self.assertIn(str(target), prompt)
            self.assertTrue(target.is_dir())
            result = self.registry.resolve_confirmation("yes")

        self.assertIn(str(target), result)
        self.assertFalse(target.exists())

    def test_relative_parent_lookup_uses_indexed_full_path(self):
        project = self.root / "JARVIS"
        target_parent = self.root / "MY DRIVE" / "15" / "KEC IT" / "Subjects" / "Theory"
        target = target_parent / "ADS"
        target.mkdir(parents=True)
        fake_folder_index = lambda query: (
            [str(target_parent)] if query.casefold() == "theory"
            else [str(target_parent), str(self.root / "Other" / "Theory")]
            if query.casefold() == "theory" else []
        )

        with patch.object(fs, "_PROJECT_ROOT", project), \
             patch("commands._find_folder_in_index", side_effect=fake_folder_index):
            prompt = self.command("delete folder ADS inside KEC IT/Subjects/Theory")

        self.assertIn(str(target), prompt)
        self.assertTrue(target.is_dir())
        result = self.registry.resolve_confirmation("yes")
        self.assertIn(str(target), result)
        self.assertFalse(target.exists())

    def test_configured_data_roots_resolve_parent_while_full_index_builds(self):
        root = self.root / "MY DRIVE"
        parent = root / "15" / "KEC IT" / "Subjects" / "Theory"
        target = parent / "ADS"
        target.mkdir(parents=True)

        with patch.object(config, "FILE_SEARCH_ROOTS", [str(root)]), \
             patch.object(commands, "ensure_folder_index_ready", return_value=False):
            prompt = self.command("delete folder ADS inside theory")

        self.assertIn(str(target), prompt)
        self.assertTrue(target.is_dir())
        result = self.registry.resolve_confirmation("yes")
        self.assertIn(str(target), result)
        self.assertFalse(target.exists())

    def test_recent_full_path_resolves_short_parent_follow_up_without_reindexing(self):
        parent = self.root / "MY DRIVE" / "15" / "KEC IT" / "Subjects" / "Theory"
        parent.mkdir(parents=True)
        target = parent / "ADS"
        self.command(f"create folder ADS inside {parent}")

        prompt = self.command("delete folder ADS inside theory")

        self.assertIn(str(target), prompt)
        self.assertTrue(target.is_dir())
        result = self.registry.resolve_confirmation("yes")
        self.assertIn(str(target), result)
        self.assertFalse(target.exists())

    def test_index_lookup_starts_background_scan_without_blocking_filesystem_commands(self):
        scan_started = threading.Event()
        release_scan = threading.Event()
        scan_finished = threading.Event()

        def slow_scan():
            scan_started.set()
            try:
                release_scan.wait(timeout=5)
                return {}
            finally:
                scan_finished.set()

        self.index_patches[1].stop()
        with patch.object(commands, "_FOLDER_INDEX", None), \
             patch.object(commands, "_load_index_cache", return_value=(None, False)), \
             patch.object(commands, "_build_folder_index", side_effect=slow_scan):
            response = self.command("create folder NewFolder inside a_remote_parent")
            self.assertIn("indexing accessible drives", response.lower())
            self.assertTrue(scan_started.wait(timeout=1))
            release_scan.set()
            self.assertTrue(scan_finished.wait(timeout=1))

    def test_create_folder_without_destination_asks_instead_of_using_jarvis(self):
        response = self.command("create folder ITC")

        self.assertIn("Where should I create", response)
        self.assertFalse((Path(__file__).resolve().parent / "ITC").exists())

    def test_jarvis_folder_alias_targets_project_folder_not_a_nested_guess(self):
        project = Path(__file__).resolve().parent
        response = self.command("Create a folder named isolated_test inside jarvis folder")
        target = project / "isolated_test"
        try:
            self.assertIn(str(target), response)
            self.assertTrue(target.is_dir())
        finally:
            if target.exists():
                target.rmdir()

    def test_create_without_destination_asks_instead_of_using_project_directory(self):
        response = self.command("Create a folder named Unplaced")

        self.assertIn("Where should I create", response)
        self.assertFalse((Path(__file__).resolve().parent / "Unplaced").exists())

    def test_create_file_preserves_requested_name_and_location(self):
        folder = self.folders["desktop"] / "Test"
        folder.mkdir()
        response = self.command(f"Create a file named notes.txt in {folder}")

        target = folder / "notes.txt"
        self.assertTrue(target.is_file())
        self.assertIn(str(target), response)
        with self.assertRaises(FileExistsError):
            fs.create_file(target)

    def test_rename_copy_move_and_delete_copy_follow_context_paths(self):
        desktop = self.folders["desktop"]
        documents = self.folders["documents"]
        downloads = self.folders["downloads"]

        self.command("Create a folder named Alpha on Desktop")
        self.command("Rename it to Beta")
        original = desktop / "Beta"
        self.assertTrue(original.is_dir())

        self.command("Copy it to Documents")
        copied = documents / "Beta"
        self.assertTrue(copied.is_dir())

        self.command("Move it to Downloads")
        moved = downloads / "Beta"
        self.assertTrue(moved.is_dir())
        self.assertTrue(copied.is_dir())

        prompt = self.command("Delete the copy")
        self.assertIn(str(copied), prompt)
        self.assertTrue(copied.is_dir())
        result = self.registry.resolve_confirmation("yes")
        self.assertIn(str(copied), result)
        self.assertFalse(copied.exists())
        self.assertTrue(moved.is_dir())

    def test_full_requested_file_workflow_resolves_current_item(self):
        desktop = self.folders["desktop"]
        downloads = self.folders["downloads"]
        documents = self.folders["documents"]

        self.command("Create a folder named Test1 on Desktop")
        self.command("Rename Test1 to Test2")
        self.command("Copy Test2 to Documents")
        self.command("Move Test2 to Downloads")
        self.assertTrue((documents / "Test2").is_dir())
        self.assertTrue((downloads / "Test2").is_dir())

        self.command("Create a file named hello.txt in Test2")
        hello = downloads / "Test2" / "hello.txt"
        self.assertTrue(hello.is_file())
        self.command("Rename hello.txt to welcome.txt")
        welcome = downloads / "Test2" / "welcome.txt"
        self.assertTrue(welcome.is_file())
        self.command("Copy welcome.txt to Desktop")
        desktop_copy = desktop / "welcome.txt"
        self.assertTrue(desktop_copy.is_file())

        prompt = self.command("Delete welcome.txt")
        self.assertIn(str(desktop_copy), prompt)
        self.registry.resolve_confirmation("confirm")
        self.assertFalse(desktop_copy.exists())

        prompt = self.command("Delete Test2")
        self.assertIn(str(downloads / "Test2"), prompt)
        self.registry.resolve_confirmation("do it")
        self.assertFalse((downloads / "Test2").exists())
        self.assertTrue((documents / "Test2").exists())

    def test_copy_paste_with_explicit_and_contextual_sources(self):
        desktop = self.folders["desktop"]
        documents = self.folders["documents"]
        downloads = self.folders["downloads"]
        source = desktop / "folder with spaces"
        source.mkdir()

        response = self.command(f'Copy "{source}" and paste it to Documents')
        self.assertTrue((documents / source.name).is_dir())
        self.assertIn(str(documents / source.name), response)

        self.command("Copy this folder and paste it in Downloads")
        self.assertTrue((downloads / source.name).is_dir())

    def test_copy_without_destination_then_paste_uses_selected_source(self):
        source = self.folders["desktop"] / "Selected"
        source.mkdir()
        response = self.command(f"Copy {source}")
        self.assertIn("Where should I copy", response)

        response = self.command("Paste it into Documents")
        self.assertTrue((self.folders["documents"] / "Selected").is_dir())
        self.assertIn("copied successfully", response.lower())

    def test_delete_requires_exact_confirmation_and_no_cancels(self):
        target = self.folders["desktop"] / "Keep"
        target.mkdir()

        prompt = self.command("Delete folder Keep from Desktop")
        self.assertIn(str(target), prompt)
        self.assertTrue(target.is_dir())
        self.assertEqual(self.registry.resolve_confirmation("no"), "Cancelled. Nothing was changed.")
        self.assertTrue(target.is_dir())

        prompt = self.command("Delete folder Keep from Desktop")
        self.assertIn(str(target), prompt)
        result = self.registry.resolve_confirmation("yes")
        self.assertIn("Folder deleted successfully", result)
        self.assertFalse(target.exists())

    def test_yes_after_delete_prompt_executes_through_assistant_pipeline(self):
        target = self.folders["desktop"] / "AssistantDelete"
        target.mkdir()
        manager = ConversationManager()

        with patch.object(assistant, "conversation_manager", manager), \
             patch.object(fs, "_known_folder_path", side_effect=lambda name: self.folders[name]):
            prompt = assistant.process_message("Delete folder AssistantDelete from Desktop")
            self.assertIn(str(target), prompt)
            self.assertTrue(target.exists())
            result = assistant.process_message("yes")

        self.assertIn("Folder deleted successfully", result)
        self.assertFalse(target.exists())

    def test_copy_conflict_requires_overwrite_confirmation(self):
        source = self.folders["desktop"] / "Conflict"
        source.mkdir()
        destination = self.folders["documents"] / "Conflict"
        destination.mkdir()
        (destination / "old.txt").write_text("old", encoding="utf-8")
        (source / "new.txt").write_text("new", encoding="utf-8")

        prompt = self.command(f"Copy {source} to Documents")
        self.assertIn(str(destination), prompt)
        self.assertTrue((destination / "old.txt").exists())
        response = self.registry.resolve_confirmation("confirm")
        self.assertIn("copied successfully", response.lower())
        self.assertFalse((destination / "old.txt").exists())
        self.assertEqual((destination / "new.txt").read_text(encoding="utf-8"), "new")

    def test_missing_source_and_destination_never_guess_another_path(self):
        response = self.command("Copy absent-folder to Documents")
        self.assertIn("couldn't locate the source", response.lower())
        self.assertFalse((self.folders["documents"] / "absent-folder").exists())

        response = self.command("Create a file named no-parent.txt in MissingFolder")
        self.assertIn("couldn't complete", response.lower())
        self.assertFalse((self.root / "MissingFolder" / "no-parent.txt").exists())

    def test_list_directory_reports_actual_contents(self):
        target = self.folders["desktop"]
        (target / "note.txt").write_text("hello", encoding="utf-8")
        (target / "Folder").mkdir()

        result = self.command(f"List {target}")
        self.assertIn("[File] note.txt", result)
        self.assertIn("[Folder] Folder", result)

    def test_environment_variable_and_relative_path_resolution(self):
        with patch.dict(os.environ, {"ULTRON_TEST_ROOT": str(self.root)}):
            self.assertEqual(
                fs.resolve_path("%ULTRON_TEST_ROOT%\\Desktop"),
                self.folders["desktop"],
            )
        self.assertEqual(fs.resolve_path("Desktop"), self.folders["desktop"])

    def test_search_roots_discover_all_logical_drive_letters(self):
        drive_mask = (1 << 2) | (1 << 12) | (1 << 25)
        with patch(
            "commands.ctypes.windll.kernel32.GetLogicalDrives",
            return_value=drive_mask,
        ):
            self.assertEqual(
                commands._available_drive_roots(),
                ["C:\\", "M:\\", "Z:\\"],
            )

    def test_drive_indexes_include_deep_hidden_and_system_named_paths(self):
        root = self.root / "scan"
        deep_folder = root / "Windows" / ".hidden" / "level1" / "level2" / "level3" / "level4" / "level5" / "level6" / "level7" / "level8" / "level9"
        deep_folder.mkdir(parents=True)
        (deep_folder / "report.tmp").write_text("metadata only", encoding="utf-8")
        file_cache = self.root / "files.pkl"
        folder_cache = self.root / "folders.pkl"

        with patch.object(commands, "_search_roots", return_value=[str(root)]), \
             patch.object(commands, "_folder_index_roots", return_value=[str(root)]), \
             patch.object(commands, "_FILE_INDEX_CACHE", str(file_cache)), \
             patch.object(commands, "_FOLDER_INDEX_CACHE", str(folder_cache)), \
             patch.object(commands, "_FILE_INDEX", None), \
             patch.object(commands, "_FOLDER_INDEX", None):
            file_index = commands._build_file_index(force=True)
            folder_index = commands._build_folder_index(force=True)
            self.assertIn(str(deep_folder / "report.tmp"), file_index["report"])
            self.assertIn(
                str(deep_folder),
                folder_index[commands._norm_name("level9")],
            )

    def test_multiple_deletion_requires_one_confirmation_for_exact_items(self):
        first = self.folders["desktop"] / "One"
        second = self.folders["desktop"] / "Two"
        first.mkdir()
        second.write_text("file", encoding="utf-8")

        prompt = self.command("Delete One and Two from Desktop")
        self.assertIn(str(first), prompt)
        self.assertIn(str(second), prompt)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.registry.resolve_confirmation("yes")
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_confirmation_expiration_prevents_execution(self):
        target = self.folders["desktop"] / "Expired"
        target.mkdir()
        self.command("Delete folder Expired from Desktop")
        with patch("tool_registry.time.monotonic", return_value=10**9):
            result = self.registry.resolve_confirmation("yes")

        self.assertIn("expired", result.lower())
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
