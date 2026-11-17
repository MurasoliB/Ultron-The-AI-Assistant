import os
import unittest
import tempfile
from doc_agent import DocReader, DocHistory, DEFAULT_LAB_DIRS

class TestDocAgent(unittest.TestCase):

    def test_supported_extensions(self):
        for ext in [".java", ".py", ".c", ".cpp", ".docx", ".pdf", ".txt", ".md", ".json"]:
            self.assertIn(ext, DocReader.SUPPORTED_EXTS)

    def test_file_icons(self):
        self.assertEqual(DocReader.get_file_icon(".java"), "☕")
        self.assertEqual(DocReader.get_file_icon(".py"), "🐍")
        self.assertEqual(DocReader.get_file_icon(".c"), "⚡")
        self.assertEqual(DocReader.get_file_icon(".pdf"), "📑")
        self.assertEqual(DocReader.get_file_icon(".docx"), "📄")

    def test_read_code_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".java", delete=False, encoding="utf-8") as f:
            f.write("public class Solution { public static void main(String[] args) {} }")
            temp_path = f.name

        try:
            res = DocReader.read_file(temp_path)
            self.assertIsNone(res["error"])
            self.assertEqual(res["type"], "Java")
            self.assertIn("public class Solution", res["text"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_read_non_existent_file(self):
        res = DocReader.read_file("__not_existing_file_12345.java")
        self.assertIsNotNone(res["error"])

    def test_scan_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "test1.py")
            file2 = os.path.join(tmpdir, "test2.java")
            with open(file1, "w") as f:
                f.write("print('hello')")
            with open(file2, "w") as f:
                f.write("class Test {}")

            results = DocReader.scan_folder(tmpdir)
            self.assertEqual(len(results), 2)
            names = [r["name"] for r in results]
            self.assertIn("test1.py", names)
            self.assertIn("test2.java", names)

    def test_history_records_files(self):
        history = DocHistory()
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("x = 1")
            temp_path = f.name

        try:
            history.add_file(temp_path)
            self.assertIn(temp_path, history.recent_files)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

if __name__ == "__main__":
    unittest.main()

