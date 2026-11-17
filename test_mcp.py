import unittest
import os
import sys
import json
import tempfile
import sqlite3

from mcp_manager import MCPManager, mcp_manager
from tool_registry import tool_registry


class TestMCPManager(unittest.TestCase):

    def setUp(self):
        self.manager = MCPManager()

    def test_mcp_config_loading(self):
        """Verifies that mcp_servers.json is loaded with configured servers."""
        self.assertIn("fetch", self.manager.servers)
        fetch_cfg = self.manager.servers["fetch"]
        self.assertTrue(any("mcp_server_fetch" in arg for arg in fetch_cfg.get("args", [])))

    def test_zero_key_web_search(self):
        """Verifies that live web search works without any API keys."""
        res = self.manager.web_search("Python official website", max_results=2)
        self.assertIsInstance(res, str)
        self.assertTrue("python" in res.lower())

    def test_web_search_empty_query(self):
        """Verifies handling of empty search queries."""
        res = self.manager.web_search("")
        self.assertEqual(res, "No search query provided.")

    def test_voice_search_command_dispatch(self):
        """Verifies that natural voice commands trigger web search."""
        res = self.manager.handle_command("search the web for Linux kernel")
        self.assertIsNotNone(res)
        self.assertTrue("linux" in res.lower() or "search results" in res.lower())

    def test_web_fetch(self):
        """Verifies web fetching with zero API keys."""
        res = self.manager.web_fetch("https://example.com")
        self.assertIsInstance(res, str)
        self.assertIn("Example Domain", res)

    def test_voice_fetch_command_dispatch(self):
        """Verifies that 'fetch website' commands work."""
        res = self.manager.handle_command("fetch website https://example.com")
        self.assertIsNotNone(res)
        self.assertIn("Example Domain", res)

    def test_sqlite_query(self):
        """Verifies local zero-key SQLite querying."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("CREATE TABLE test_students (id INT, name TEXT, dept TEXT)")
            c.execute("INSERT INTO test_students VALUES (101, 'Arun', 'IT')")
            c.execute("INSERT INTO test_students VALUES (102, 'Bala', 'CSE')")
            conn.commit()
            conn.close()

            res = self.manager.query_sqlite(db_path, "SELECT * FROM test_students WHERE dept = 'IT'")
            self.assertIn("Arun", res)
            self.assertIn("IT", res)
            self.assertNotIn("Bala", res)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_tool_registry_mcp_integration(self):
        """Verifies that tool_registry routes MCP actions properly."""
        self.assertIn("mcp_search", tool_registry._action_handlers)
        self.assertIn("mcp_fetch", tool_registry._action_handlers)

        # Test native command routing through ToolRegistry
        native_res = tool_registry.execute_native_command("search the web for Python programming language")
        self.assertIsNotNone(native_res)
        self.assertTrue("python" in native_res.lower())


if __name__ == "__main__":
    unittest.main()

