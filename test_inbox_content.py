"""
Test email message reading by index and query using unittest.
"""
import unittest
from inbox import (
    HTMLTextExtractor,
    set_last_listed_emails,
    get_last_listed_emails,
    read_email_content,
)
from commands import check_inbox_command, handle_command
from tool_registry import tool_registry


class TestInboxContent(unittest.TestCase):
    def test_html_extractor(self):
        raw_html = """
        <html>
          <head><style>p { color: red; }</style></head>
          <body>
            <h2>Account Notification</h2>
            <p>Dear Murasoli,</p>
            <p>Your verification code is <b>749201</b>.</p>
            <br>
            <p>Thank you,<br>Samruka Team</p>
          </body>
        </html>
        """
        extractor = HTMLTextExtractor()
        extractor.feed(raw_html)
        text = extractor.get_text()
        self.assertIn("Account Notification", text)
        self.assertIn("749201", text)
        self.assertIn("Samruka Team", text)
        self.assertNotIn("color: red", text)

    def test_read_email_content_from_cache(self):
        mock_emails = [
            {"uid": "101", "from": "Google", "sender_email": "no-reply@google.com", "sender_domain": "google.com", "subject": "Security alert", "date": "2026-10-21 10:00", "body": "New login detected on Windows.", "folder": "INBOX"},
            {"uid": "102", "from": "GitHub", "sender_email": "notifications@github.com", "sender_domain": "github.com", "subject": "New release v1.0", "date": "2026-10-21 11:00", "body": "Release notes for ULTRON.", "folder": "INBOX"},
            {"uid": "103", "from": "LinkedIn", "sender_email": "updates@linkedin.com", "sender_domain": "linkedin.com", "subject": "3 new job alerts", "date": "2026-10-21 12:00", "body": "Jobs matching AI Engineer.", "folder": "INBOX"},
            {"uid": "104", "from": "Samruka", "sender_email": "sam@samruka.com", "sender_domain": "samruka.com", "subject": "Welcome to Samruka Platform", "date": "2026-10-21 13:00", "body": "Hello Murasoli,\nYour account is active. Your access key is SR-9941.", "folder": "INBOX"},
        ]
        set_last_listed_emails(mock_emails)

        # 1. Test reading item 4
        res = read_email_content("4")
        self.assertIn("[GMAIL MESSAGE #4]", res)
        self.assertIn("Samruka", res)
        self.assertIn("sam@samruka.com", res)
        self.assertIn("Welcome to Samruka Platform", res)
        self.assertIn("SR-9941", res)

        # 2. Test reading by sender keyword
        res_sender = read_email_content("samruka")
        self.assertIn("Samruka", res_sender)
        self.assertIn("SR-9941", res_sender)

    def test_command_matching_for_index(self):
        mock_emails = [
            {"uid": "101", "from": "Google", "sender_email": "no-reply@google.com", "sender_domain": "google.com", "subject": "Security alert", "date": "2026-10-21 10:00", "body": "New login detected on Windows.", "folder": "INBOX"},
            {"uid": "102", "from": "GitHub", "sender_email": "notifications@github.com", "sender_domain": "github.com", "subject": "New release v1.0", "date": "2026-10-21 11:00", "body": "Release notes for ULTRON.", "folder": "INBOX"},
            {"uid": "103", "from": "LinkedIn", "sender_email": "updates@linkedin.com", "sender_domain": "linkedin.com", "subject": "3 new job alerts", "date": "2026-10-21 12:00", "body": "Jobs matching AI Engineer.", "folder": "INBOX"},
            {"uid": "104", "from": "Samruka", "sender_email": "sam@samruka.com", "sender_domain": "samruka.com", "subject": "Welcome to Samruka Platform", "date": "2026-10-21 13:00", "body": "Hello Murasoli,\nYour account is active. Your access key is SR-9941.", "folder": "INBOX"},
        ]
        set_last_listed_emails(mock_emails)

        # "what is the message for 4"
        r1 = check_inbox_command("what is the message for 4")
        self.assertIsNotNone(r1)
        self.assertIn("[GMAIL MESSAGE #4]", r1)
        self.assertIn("SR-9941", r1)

        # "what is the content of 4"
        r2 = check_inbox_command("what is the content of 4")
        self.assertIsNotNone(r2)
        self.assertIn("[GMAIL MESSAGE #4]", r2)

        # "what does email 4 say"
        r3 = check_inbox_command("what does email 4 say")
        self.assertIsNotNone(r3)
        self.assertIn("[GMAIL MESSAGE #4]", r3)

        # "read email 4"
        r4 = check_inbox_command("read email 4")
        self.assertIsNotNone(r4)
        self.assertIn("[GMAIL MESSAGE #4]", r4)

        # "show 4"
        r5 = check_inbox_command("show 4")
        self.assertIsNotNone(r5)
        self.assertIn("[GMAIL MESSAGE #4]", r5)

        # "open email 4"
        r6 = check_inbox_command("open email 4")
        self.assertIsNotNone(r6)
        self.assertIn("[GMAIL MESSAGE #4]", r6)

        # Bare number "4"
        r7 = check_inbox_command("4")
        self.assertIsNotNone(r7)
        self.assertIn("[GMAIL MESSAGE #4]", r7)

        # "#4"
        r8 = check_inbox_command("#4")
        self.assertIsNotNone(r8)
        self.assertIn("[GMAIL MESSAGE #4]", r8)

        # handle_command integration
        r9 = handle_command("what is the message for 4")
        self.assertIsNotNone(r9)
        self.assertIn("[GMAIL MESSAGE #4]", r9)
        self.assertIn("SR-9941", r9)

    def test_tool_registry_read_email_action(self):
        mock_emails = [
            {"uid": "101", "from": "Google", "sender_email": "no-reply@google.com", "sender_domain": "google.com", "subject": "Security alert", "date": "2026-10-21 10:00", "body": "New login detected.", "folder": "INBOX"},
            {"uid": "102", "from": "GitHub", "sender_email": "notifications@github.com", "sender_domain": "github.com", "subject": "New release v1.0", "date": "2026-10-21 11:00", "body": "Release notes.", "folder": "INBOX"},
            {"uid": "103", "from": "LinkedIn", "sender_email": "updates@linkedin.com", "sender_domain": "linkedin.com", "subject": "Job alert", "date": "2026-10-21 12:00", "body": "Jobs matching AI.", "folder": "INBOX"},
            {"uid": "104", "from": "Samruka", "sender_email": "sam@samruka.com", "sender_domain": "samruka.com", "subject": "Welcome to Samruka Platform", "date": "2026-10-21 13:00", "body": "Access key SR-9941.", "folder": "INBOX"},
        ]
        set_last_listed_emails(mock_emails)

        response = tool_registry.execute_response_action("Here is the requested email:\n[[ACTION:read_email:4]]")
        self.assertIn("[GMAIL MESSAGE #4]", response)
        self.assertIn("SR-9941", response)


if __name__ == "__main__":
    unittest.main()

