import tempfile
import unittest
from unittest.mock import mock_open
from pathlib import Path
from unittest.mock import Mock, patch

import assistant
import brain
import commands
import conversation
import memory
from conversation import ConversationManager
from intent_router import classify_intent
from tool_registry import ToolRegistry


class ConversationTests(unittest.TestCase):
    def test_recent_folder_from_tool_result_is_available_for_followups(self):
        manager = ConversationManager()
        manager.add_assistant_message(
            r"Folder created at C:\Users\ELCOT\Projects\TestProject."
        )

        self.assertIn(
            r"Recent folders: C:\Users\ELCOT\Projects\TestProject",
            manager.context(),
        )

    def test_last_requested_website_is_kept_for_browser_followup(self):
        manager = ConversationManager()
        manager.add_user_message("Open ChatGPT")

        self.assertIn("Last requested website: https://chatgpt.com", manager.context())
        self.assertIn("Current task: Open ChatGPT", manager.context())
        followup = classify_intent("Open it in Brave", manager.context())
        self.assertEqual(followup.intent.value, "OPEN_URL")
        self.assertEqual(followup.target, "ChatGPT")
        self.assertEqual(followup.browser, "Brave")

    def test_open_it_in_brave_uses_prior_website_through_assistant_pipeline(self):
        manager = ConversationManager()
        with patch.object(assistant, "conversation_manager", manager), \
             patch("commands._open_url_in_browser", return_value=True) as open_url, \
             patch.object(assistant, "ask_ai") as ask_ai:
            assistant.process_message("Open ChatGPT")
            result = assistant.process_message("Open it in Brave")

        self.assertIn("Brave", result)
        self.assertEqual(
            open_url.call_args_list[-1].args,
            ("https://chatgpt.com", "Brave"),
        )
        ask_ai.assert_not_called()

    def test_history_is_bounded_and_keeps_followup_context(self):
        manager = ConversationManager(max_messages=4)
        manager.add_user_message("Create a Python calculator")
        manager.add_assistant_message("The calculator plan is ready.")
        manager.add_user_message("Add a dark GUI to it")
        manager.add_assistant_message("I will use a dark interface.")
        manager.add_user_message("Run it")

        history = manager.history()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1]["content"], "Run it")
        self.assertIn("Current task: Add a dark GUI to it", manager.context())

    def test_long_term_facts_are_saved_only_when_explicitly_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            with patch.object(memory, "MEMORY_FILE", path):
                memory.remember_fact("Prefers concise spoken replies")
                memory.remember_fact("Prefers concise spoken replies")
                self.assertEqual(
                    memory.get_facts(),
                    ["Prefers concise spoken replies"],
                )

    def test_explicit_remember_request_is_not_sent_to_ai(self):
        manager = ConversationManager()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory, "MEMORY_FILE", Path(directory) / "memory.json"), \
             patch.object(assistant, "conversation_manager", manager), \
             patch.object(assistant.tool_registry, "execute_native_command") as native, \
             patch.object(assistant, "ask_ai") as ask_ai:
            response = assistant.process_message(
                "Remember that I prefer concise answers."
            )
            facts = memory.get_facts()

        self.assertIn("remember", response.lower())
        self.assertEqual(facts, ["I prefer concise answers"])
        native.assert_not_called()
        ask_ai.assert_not_called()

    def test_followup_sends_previous_turns_to_provider(self):
        manager = ConversationManager()
        with patch.object(assistant, "conversation_manager", manager), \
             patch.object(assistant.tool_registry, "execute_native_command", return_value=None), \
             patch.object(assistant, "ask_ai", side_effect=["A calculator plan.", "Adding the GUI."]) as ask_ai, \
             patch.object(assistant.tool_registry, "execute_response_action", side_effect=lambda text: text):
            assistant.process_message("Create a calculator")
            assistant.process_message("Add a GUI to it")

        call = ask_ai.call_args
        self.assertEqual(call.args[0], "Add a GUI to it")
        self.assertEqual(
            call.kwargs["history"],
            [
                {"role": "user", "content": "Create a calculator"},
                {"role": "assistant", "content": "A calculator plan."},
            ],
        )

    def test_groq_receives_turn_history_and_context(self):
        client = Mock()
        client.chat.completions.create.return_value.choices = [
            Mock(message=Mock(content="The GUI is ready."))
        ]
        with patch.object(brain, "groq_client", client), \
             patch.object(brain, "GROQ_MODELS", ["test-model"]):
            response = brain.ask_groq(
                "Make it dark",
                history=[{"role": "user", "content": "Create a calculator"}],
                context="Current task: calculator",
            )

        self.assertEqual(response, "The GUI is ready.")
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-2]["content"], "Create a calculator")
        self.assertEqual(messages[-1]["content"], "Make it dark")
        self.assertIn("Current task: calculator", messages[1]["content"])

    def test_gemini_receives_turn_history_and_context(self):
        client = Mock()
        client.models.generate_content.return_value.text = "The GUI is ready."
        with patch.object(brain, "gemini_client", client), \
             patch.object(brain, "GEMINI_MODELS", ["test-model"]):
            response = brain.ask_gemini(
                "Make it dark",
                history=[{"role": "user", "content": "Create a calculator"}],
                context="Current task: calculator",
            )

        self.assertEqual(response, "The GUI is ready.")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("User: Create a calculator", prompt)
        self.assertIn("Current task: calculator", prompt)
        self.assertIn("User: Make it dark", prompt)

    def test_ai_action_uses_registered_tool_result(self):
        registry = ToolRegistry()
        with patch("tool_registry.execute_action", return_value="Opening VS Code.") as execute:
            result = registry.execute_response_action(
                "Launching now. [[ACTION:open_app:code]]"
            )

        self.assertEqual(result, "Launching now.\nOpening VS Code.")
        execute.assert_called_once_with("open_app", "code")

    def test_multiple_ai_actions_are_all_executed(self):
        registry = ToolRegistry()
        with patch("tool_registry.execute_action", side_effect=[
            "Played the requested song.",
            "Opened YouTube.",
        ]) as execute:
            result = registry.execute_response_action(
                "Your name is Arun. "
                "[[ACTION:play_youtube:Pavalamalli]] "
                "[[ACTION:open_website:youtube.com]]"
            )

        self.assertIn("Your name is Arun.", result)
        self.assertIn("Played the requested song.", result)
        self.assertIn("Opened YouTube.", result)
        self.assertEqual(execute.call_count, 2)

    def test_compound_question_and_action_are_routed_to_ai(self):
        manager = ConversationManager()
        with patch.object(assistant, "conversation_manager", manager), \
             patch.object(conversation, "get_name", return_value="Arun"), \
             patch.object(conversation, "get_facts", return_value=[]), \
             patch.object(assistant.tool_registry, "execute_native_command") as native, \
             patch.object(assistant, "ask_ai", return_value="Name and project answer.") as ask_ai, \
             patch.object(
                 assistant.tool_registry,
                 "execute_response_action",
                 side_effect=lambda text: text,
             ):
            response = assistant.process_message(
                "Say my name and what project am I working with and play Pavalamalli on YouTube"
            )

        self.assertEqual(response, "Name and project answer.")
        native.assert_not_called()
        self.assertIn("User's saved name: Arun", ask_ai.call_args.kwargs["context"])
        self.assertIn(
            "Current project workspace: ULTRON personal AI assistant",
            ask_ai.call_args.kwargs["context"],
        )

    def test_unregistered_ai_action_is_rejected(self):
        registry = ToolRegistry()
        with patch("tool_registry.execute_action") as execute:
            result = registry.execute_response_action(
                "[[ACTION:delete_file:important.txt]]"
            )

        self.assertIn("not an approved", result)
        execute.assert_not_called()

    def test_tools_can_be_extended_without_changing_the_router(self):
        registry = ToolRegistry()
        registry.register_action("project_status", lambda target: f"Project: {target}")

        self.assertEqual(
            registry.execute_response_action(
                "[[ACTION:project_status:ultron]]"
            ),
            "Project: ultron",
        )

    def test_sensitive_tool_requires_explicit_confirmation(self):
        registry = ToolRegistry()
        handler = Mock(return_value="Project deleted.")
        registry.register_action(
            "delete_project",
            handler,
            requires_confirmation=True,
        )

        prompt = registry.execute_response_action(
            "[[ACTION:delete_project:old-project]]"
        )
        self.assertIn("confirm delete_project", prompt)
        self.assertIn("say 'yes'", prompt)
        handler.assert_not_called()
        self.assertIsNone(registry.resolve_confirmation("maybe"))
        handler.assert_not_called()
        self.assertEqual(
            registry.resolve_confirmation("yes"),
            "Project deleted.",
        )
        handler.assert_called_once_with("old-project")


class NaturalCommandAndSafetyTests(unittest.TestCase):
    def tearDown(self):
        commands._pending_power_action = None
        commands._pending_power_expires = 0.0

    def test_common_english_commands_normalize(self):
        self.assertEqual(commands.normalize_command("Ultron, launch VS Code please"), "open vs code")
        self.assertEqual(commands.normalize_command("Please open YouTube"), "open youtube")
        self.assertEqual(commands.normalize_command("turn the volume up"), "volume up")
        self.assertEqual(commands.normalize_command("increase brightness"), "increase brightness")

    def test_creation_request_without_explicit_file_is_not_misrouted(self):
        self.assertIsNone(commands.check_create_file_command("create a Python calculator"))

    def test_contextual_file_reference_is_left_for_conversation_reasoning(self):
        self.assertIsNone(commands.check_open_file_command("open that"))
        self.assertIsNone(commands.check_open_file_command("open the project I'm working on"))

    def test_file_creation_rejects_path_traversal(self):
        self.assertIn(
            "filename is invalid",
            commands.check_create_file_command("create file called ..\\outside.txt"),
        )

    def test_explicit_file_creation_still_uses_existing_file_handler(self):
        with patch("commands.os.makedirs"), \
             patch("builtins.open", mock_open()) as open_file, \
             patch.object(commands, "_open_file_in_editor", return_value=True):
            response = commands.check_create_file_command(
                "create a file called ultron_note.txt"
            )

        self.assertIn("Created ultron_note.txt", response)
        open_file.assert_called_once()

    def test_power_request_requires_confirmation_before_execution(self):
        with patch.object(commands, "_schedule_power_action", return_value=True) as schedule:
            response = commands.check_power_command("shutdown the computer")
            schedule.assert_not_called()
            self.assertIn("confirm shutdown", response.lower())

            confirmed = commands.check_power_command("yes")

        schedule.assert_called_once_with("shutdown", delay=10)
        self.assertIn("10 seconds", confirmed)

    def test_power_action_can_be_cancelled_without_execution(self):
        with patch.object(commands, "_schedule_power_action") as schedule:
            commands.check_power_command("restart the computer")
            response = commands.check_power_command("no")

        schedule.assert_not_called()
        self.assertEqual(response, "Cancelled. Nothing will happen.")

    def test_ambiguous_affirmative_does_not_confirm_power_action(self):
        with patch.object(commands, "_schedule_power_action") as schedule:
            commands.check_power_command("shutdown the computer")
            result = commands.check_power_command("yesterday I said yes")

        schedule.assert_not_called()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
