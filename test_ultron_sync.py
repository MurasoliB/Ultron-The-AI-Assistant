import unittest
from unittest.mock import MagicMock, patch
import time
import ultron_sync


class TestUltronSync(unittest.TestCase):

    def test_sync_state_read_write(self):
        s1 = ultron_sync.write_sync_state(visible=False)
        self.assertFalse(s1["visible"])
        self.assertGreater(s1["timestamp"], 0)

        s2 = ultron_sync.read_sync_state()
        self.assertFalse(s2["visible"])

        s3 = ultron_sync.write_sync_state(visible=True)
        self.assertTrue(s3["visible"])
        self.assertGreaterEqual(s3["seq"], s1["seq"])

    def test_hide_all_calls_app_hide(self):
        mock_app = MagicMock()
        with patch.object(ultron_sync, "get_mic_hwnd", return_value=None), \
             patch.object(ultron_sync, "get_doc_hwnd", return_value=None):
            ultron_sync.hide_all(calling_app=mock_app)
            mock_app.hide.assert_called_once()
            state = ultron_sync.read_sync_state()
            self.assertFalse(state["visible"])

    def test_show_all_calls_app_show(self):
        mock_app = MagicMock()
        with patch.object(ultron_sync, "get_mic_hwnd", return_value=1234), \
             patch.object(ultron_sync, "get_doc_hwnd", return_value=5678), \
             patch("ultron_sync.user32.ShowWindow") as mock_show, \
             patch("ultron_sync.user32.SetWindowPos") as mock_pos:
            ultron_sync.show_all(calling_app=mock_app)
            mock_app.show.assert_called_once()
            self.assertTrue(mock_show.called)
            state = ultron_sync.read_sync_state()
            self.assertTrue(state["visible"])

    def test_toggle_all_flips_visibility(self):
        mock_app = MagicMock()
        # When any visible -> hide all
        with patch.object(ultron_sync, "are_any_visible", return_value=True), \
             patch.object(ultron_sync, "hide_all") as mock_hide:
            # Force timestamp to be in past to avoid debounce
            ultron_sync.write_sync_state(visible=True)
            ultron_sync.SYNC_FILE = ultron_sync.SYNC_FILE  # reset
            # modify state timestamp to be older
            data = ultron_sync.read_sync_state()
            data["timestamp"] = time.time() - 2.0
            with open(ultron_sync.SYNC_FILE, "w", encoding="utf-8") as f:
                import json
                json.dump(data, f)

            ultron_sync.toggle_all(calling_app=mock_app)
            mock_hide.assert_called_once_with(mock_app)


if __name__ == "__main__":
    unittest.main()

