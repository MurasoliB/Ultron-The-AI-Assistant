"""
Test token expansion and code request detection in brain.py.
"""
import unittest
from unittest.mock import patch, MagicMock
from brain import _is_code_request, ask_groq


class TestBrainTokens(unittest.TestCase):
    def test_is_code_request_detection(self):
        # Code generation prompts
        self.assertTrue(_is_code_request("generate a python script for scraping"))
        self.assertTrue(_is_code_request("write a calculator in c++"))
        self.assertTrue(_is_code_request("build a react todo app"))
        self.assertTrue(_is_code_request("create a flask backend with sqlite"))
        self.assertTrue(_is_code_request("solve binary search tree algorithm"))
        self.assertTrue(_is_code_request("write html css js portfolio"))
        self.assertTrue(_is_code_request("```print('hello')```"))
        self.assertTrue(_is_code_request("create a function to sort an array"))

        # General conversational prompts
        self.assertFalse(_is_code_request("what is the weather today"))
        self.assertFalse(_is_code_request("who was the 16th president of the united states"))
        self.assertFalse(_is_code_request("how are you doing"))

    @patch("brain.groq_client")
    def test_ask_groq_code_request_max_tokens(self, mock_client):
        mock_choice = MagicMock()
        mock_choice.message.content = "```python\ndef test():\n    return True\n```"
        mock_choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        res = ask_groq("write a python function to add two numbers")
        self.assertIn("def test()", res)

        # Check call arguments: max_tokens must be 16384 for code
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["max_tokens"], 16384)
        self.assertEqual(call_kwargs["temperature"], 0.2)

    @patch("brain.groq_client")
    def test_ask_groq_general_max_tokens(self, mock_client):
        mock_choice = MagicMock()
        mock_choice.message.content = "The weather is sunny."
        mock_choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        res = ask_groq("what is the weather today")
        self.assertEqual(res, "The weather is sunny.")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["max_tokens"], 4096)


if __name__ == "__main__":
    unittest.main()
