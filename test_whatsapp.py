import unittest
from unittest.mock import patch

from whatsapp_agent import (
    WhatsAppAgent,
    _diagnostic_hint,
    parse_message_request,
    whatsapp_agent,
)
from whatsapp_desktop import WhatsAppDesktop
from tool_registry import ToolRegistry


class FakeDesktop:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.notifications = []

    def request(self, operation, **values):
        self.requests.append((operation, values))
        if self.responses:
            return self.responses.pop(0)
        if operation == "send":
            return {"status": "sent"}
        return {"status": "ready", "contact": values.get("contact", "Rahul"), "draft_id": "draft-1"}

    def notify(self, operation, **values):
        self.notifications.append((operation, values))


class WhatsAppCommandTests(unittest.TestCase):
    def test_natural_voice_requests_preserve_message_punctuation(self):
        cases = {
            "Hey Ultron, send Rahul a WhatsApp message saying I'll reach college at 9.": (
                "Rahul", "I'll reach college at 9."
            ),
            "Tell Priya on WhatsApp that I'll call her tonight.": (
                "Priya", "I'll call her tonight."
            ),
            "WhatsApp Arun and tell him the meeting starts at 10.": (
                "Arun", "the meeting starts at 10."
            ),
            "Send Karthik: Are you coming to college tomorrow?": (
                "Karthik", "Are you coming to college tomorrow?"
            ),
            "Say hi to Ruby in WhatsApp": ("Ruby", "hi"),
            "Say I'll be late to Rahul on WhatsApp.": ("Rahul", "I'll be late"),
            "send hi to Ruby in whatsapp": ("Ruby", "hi"),
            "Message Rahul on WhatsApp.": ("Rahul", ""),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(parse_message_request(command), expected)

    def test_say_message_to_contact_is_prepared_as_whatsapp_draft(self):
        bridge = FakeDesktop()
        agent = WhatsAppAgent(bridge)

        response = agent.handle_command("Say hi to Ruby in WhatsApp")

        self.assertIn("Message to Ruby is ready: hi", response)
        self.assertIn("Should I send it?", response)
        self.assertEqual([operation for operation, _ in bridge.requests], ["prepare"])
        self.assertEqual(bridge.requests[0][1]["contact"], "Ruby")
        self.assertEqual(bridge.requests[0][1]["message"], "hi")

    def test_message_is_prepared_but_not_sent_until_explicit_yes(self):
        bridge = FakeDesktop()
        agent = WhatsAppAgent(bridge)

        response = agent.handle_command(
            "send Rahul a WhatsApp message saying I'll be late."
        )

        self.assertIn("Should I send it?", response)
        self.assertEqual([op for op, _ in bridge.requests], ["prepare"])
        self.assertEqual(
            bridge.requests[0][1]["message"],
            "I'll be late.",
        )

        response = agent.handle_command("yes")

        self.assertEqual(response, "WhatsApp message sent to Rahul.")
        self.assertEqual([op for op, _ in bridge.requests], ["prepare", "send"])
        self.assertTrue(bridge.requests[1][1]["confirmed"])
        self.assertEqual(bridge.requests[1][1]["draft_id"], "draft-1")

    def test_no_cancels_and_never_sends(self):
        bridge = FakeDesktop()
        agent = WhatsAppAgent(bridge)
        agent.handle_command("send Rahul: Test message")

        response = agent.handle_command("no")

        self.assertIn("not sent", response)
        self.assertEqual([op for op, _ in bridge.requests], ["prepare"])
        self.assertEqual(bridge.notifications[0][0], "cancel")

    def test_duplicate_contacts_require_selection_before_preparing(self):
        bridge = FakeDesktop([
            {"status": "multiple_matches", "matches": [
                {"label": "Rahul — match 1", "name": "Rahul"},
                {"label": "Rahul — match 2", "name": "Rahul"},
            ]},
        ])
        agent = WhatsAppAgent(bridge)

        response = agent.handle_command("send Rahul: Hello")

        self.assertIn("multiple WhatsApp chats", response)
        self.assertEqual([op for op, _ in bridge.requests], ["prepare"])
        bridge.responses.append({"status": "ready", "contact": "Rahul", "draft_id": "draft-2"})
        response = agent.handle_command("2")

        self.assertIn("Should I send it?", response)
        self.assertEqual([op for op, _ in bridge.requests], ["prepare", "prepare"])
        self.assertEqual(bridge.requests[1][1]["choice_index"], 1)

    def test_unknown_contact_and_logged_out_session_never_send(self):
        for result, expected in (
            ({"status": "contact_not_found"}, "couldn't find"),
            (
                {"status": "contact_not_found", "visible_results": ["Ruby", "Alex"]},
                "Chats I can see: Ruby; Alex",
            ),
            ({"status": "logged_out"}, "signed out"),
        ):
            bridge = FakeDesktop([result])
            agent = WhatsAppAgent(bridge)
            response = agent.handle_command("send Rahul: Hello")
            self.assertIn(expected, response)
            self.assertEqual([op for op, _ in bridge.requests], ["prepare"])

    def test_no_message_prompts_and_waits_for_body(self):
        bridge = FakeDesktop()
        agent = WhatsAppAgent(bridge)

        self.assertIn("What would you like", agent.handle_command("message Rahul on WhatsApp"))
        self.assertEqual(bridge.requests, [])

        response = agent.handle_command("I'll be late.")

        self.assertIn("Should I send it?", response)
        self.assertEqual(bridge.requests[0][1]["message"], "I'll be late.")

    def test_test_connection_reports_session_state(self):
        bridge = FakeDesktop([{"status": "logged_in"}])
        response = WhatsAppAgent(bridge).handle_command("test WhatsApp connection")
        self.assertIn("signed in", response)

    def test_registry_routes_whatsapp_before_legacy_command_fallback(self):
        bridge = FakeDesktop()
        registry = ToolRegistry()
        with patch("tool_registry.whatsapp_agent", WhatsAppAgent(bridge)):
            response = registry.execute_native_command("send Rahul: Hello")

        self.assertIn("Should I send it?", response)
        self.assertEqual([operation for operation, _ in bridge.requests], ["prepare"])

    def test_whatsapp_confirmation_takes_priority_without_executing_other_pending_action(self):
        bridge = FakeDesktop()
        agent = WhatsAppAgent(bridge)
        agent.state = {
            "kind": "awaiting_confirmation",
            "contact": "Rahul",
            "message": "Hello",
            "draft_id": "draft-1",
            "expires_at": 10**12,
        }
        registry = ToolRegistry()
        registry.register_action("test_destructive", lambda _value: "destructive action ran")
        registry.request_confirmation("test_destructive", "", "Confirm?")

        from unittest.mock import patch
        with patch("tool_registry.whatsapp_agent", agent):
            response = registry.resolve_confirmation("yes")

        self.assertEqual(response, "WhatsApp message sent to Rahul.")
        self.assertEqual(registry.resolve_confirmation("yes"), "destructive action ran")


class WhatsAppDesktopLayoutTests(unittest.TestCase):
    """Cover the parsing rules the desktop agent relies on."""

    class FakeRect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

        def width(self):
            return self.right - self.left

        def height(self):
            return self.bottom - self.top

    class FakeNode:
        def __init__(self, kind, name, rect):
            self.ControlTypeName = kind
            self.Name = name
            self.BoundingRectangle = rect

        def GetChildren(self):
            return []

    def _text(self, name, rect):
        return self.FakeNode("TextControl", name, rect)

    def _row(self, name, children, rect):
        row = self.FakeNode("DataItemControl", name, rect)
        row.children = children
        row.GetChildren = lambda: children
        return row

    def _rect(self, left, top, right, bottom):
        return self.FakeRect(left, top, right, bottom)

    def test_row_title_uses_the_top_most_wide_text(self):
        agent = WhatsAppDesktop()
        row = self._row(
            "1 unread message Pavan KEC IT-C 3:28 pm Ohh seri da",
            [
                self._text("1 unread message", self._rect(188, 575, 207, 941)),
                self._text("Pavan KEC IT-C", self._rect(188, 574, 511, 605)),
                self._text("3:28 pm", self._rect(455, 578, 524, 599)),
                self._text("Ohh seri da", self._rect(188, 610, 511, 635)),
            ],
            self._rect(81, 557, 542, 653),
        )
        self.assertEqual(agent._row_title(row), "Pavan KEC IT-C")

    def test_row_title_ignores_timestamps_and_falls_back_cleanly(self):
        agent = WhatsAppDesktop()
        row = self._row(
            "Ruby Yesterday",
            [self._text("Yesterday", self._rect(458, 328, 524, 347))],
            self._rect(81, 304, 555, 400),
        )
        self.assertEqual(agent._row_title(row), "Ruby")

    def test_row_title_cuts_the_preview_after_the_timestamp(self):
        agent = WhatsAppDesktop()
        row = self.FakeNode(
            "DataItemControl",
            "+91 93429 09803 (You) 5:25 pm JARVIS end to end check (ok)",
            self._rect(81, 441, 555, 537),
        )
        row.GetChildren = lambda: []
        self.assertEqual(agent._row_title(row), "+91 93429 09803 (You)")

    def test_row_labels_strip_the_unread_badge(self):
        agent = WhatsAppDesktop()
        row = self.FakeNode(
            "DataItemControl",
            "2 unread messages Class Of Chaos 3:54 pm Vicky KEC",
            self._rect(81, 462, 542, 558),
        )
        row.GetChildren = lambda: []
        self.assertEqual(
            agent._row_labels(row),
            ["Class Of Chaos", "Class Of Chaos 3:54 pm Vicky KEC"],
        )

    def test_chat_rows_drop_nested_duplicates_and_narrow_nodes(self):
        agent = WhatsAppDesktop()
        outer = self.FakeNode("DataItemControl", "Ruby Yesterday", self._rect(81, 304, 555, 400))
        inner = self.FakeNode("DataItemControl", "Ruby", self._rect(188, 320, 524, 351))
        narrow = self.FakeNode("DataItemControl", "Pinned chat", self._rect(485, 297, 511, 323))
        outside = self.FakeNode("DataItemControl", "far right", self._rect(900, 300, 1400, 400))
        rows = agent._chat_rows([outer, inner, narrow, outside])
        self.assertEqual([node.Name for node in rows], ["Ruby Yesterday"])

    def test_chat_rows_survive_a_restored_window(self):
        # The desktop app is often restored rather than maximised, which makes
        # its rows about 320px wide instead of 470px. Fixed pixel thresholds
        # silently discarded every row at that size.
        agent = WhatsAppDesktop()
        window = self.FakeNode("WindowControl", "WhatsApp", self._rect(201, 193, 1184, 933))
        rows = [
            self.FakeNode("DataItemControl", "Ruby 5:02 pm hi", self._rect(282, 654, 606, 750)),
            self.FakeNode("DataItemControl", "Vicky KEC 2:21 pm Ok", self._rect(282, 1034, 606, 1130)),
        ]
        found = agent._chat_rows(rows, window)
        self.assertEqual([node.Name for node in found], ["Ruby 5:02 pm hi", "Vicky KEC 2:21 pm Ok"])

    def test_search_box_is_found_when_it_has_lost_its_name(self):
        agent = WhatsAppDesktop()
        window = self.FakeNode("WindowControl", "WhatsApp", self._rect(201, 193, 1184, 933))
        unnamed = self.FakeNode("EditControl", "", self._rect(282, 330, 606, 360))
        composer = self.FakeNode("EditControl", "Type a message to Ruby", self._rect(700, 900, 1150, 930))
        self.assertIs(agent._search_box([unnamed, composer], window), unnamed)
        self.assertIsNone(agent._search_box([composer], window))

    def test_signed_in_session_wins_over_encryption_banner(self):
        agent = WhatsAppDesktop()
        search_box = self.FakeNode(
            "EditControl", "Search or start a new chat", self._rect(166, 142, 530, 168)
        )
        banner = self.FakeNode(
            "TextControl", "Use WhatsApp on your phone to see older messages", self._rect(0, 0, 100, 20)
        )
        self.assertEqual(agent._session_state([search_box, banner]), "logged_in")

    def test_qr_code_screen_reads_as_signed_out(self):
        agent = WhatsAppDesktop()
        qr = self.FakeNode("TextControl", "Scan the QR code to log in", self._rect(0, 0, 100, 20))
        self.assertEqual(agent._session_state([qr]), "logged_out")

    def test_contact_not_found_keeps_the_labels_it_was_given(self):
        error = Exception("unused")
        from whatsapp_desktop import WhatsAppContactNotFound

        error = WhatsAppContactNotFound("Rahul", ["Ruby", ""])
        self.assertEqual(error.labels, ["Ruby"])


class WhatsAppDesktopWiringTests(unittest.TestCase):
    def test_singleton_uses_the_desktop_agent(self):
        self.assertIsInstance(whatsapp_agent.desktop, WhatsAppDesktop)

    def test_prepare_requires_an_open_conversation_before_typing(self):
        desktop = FakeDesktop([{"status": "error", "message": "WhatsApp is closed."}])
        response = WhatsAppAgent(desktop).handle_command("send Rahul: Hello")
        self.assertEqual(response, "WhatsApp is closed.")
        self.assertEqual([op for op, _ in desktop.requests], ["prepare"])

    def test_desktop_failure_is_spoken_instead_of_raised(self):
        class BrokenDesktop:
            def request(self, operation, **values):
                raise TimeoutError("the app never answered")

        response = WhatsAppAgent(BrokenDesktop()).handle_command("send Rahul: Hello")
        self.assertIn("couldn't reach the WhatsApp desktop app", response)

    def test_diagnostic_hint_reads_desktop_facts(self):
        hint = _diagnostic_hint({
            "window": "WhatsApp",
            "process_id": 25876,
            "nodes": 1883,
            "search_found": True,
            "chat_rows": 12,
            "row_titles": ["Ruby", "JioHome"],
            "logged_in": True,
        })
        self.assertIn("window 'WhatsApp'", hint)
        self.assertIn("12 chat rows", hint)
        self.assertIn("Ruby, JioHome", hint)
        self.assertIn("signed in", hint)

    def test_diagnostic_hint_stays_quiet_without_a_report(self):
        self.assertEqual(_diagnostic_hint(None), "")
        self.assertEqual(_diagnostic_hint({}), "")


if __name__ == "__main__":
    unittest.main()
