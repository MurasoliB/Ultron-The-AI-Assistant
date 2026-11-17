import time
import unittest
from unittest.mock import Mock, patch
from code_agent import CodeTyper, SPEED_PRESETS
from commands import handle_command


class TestCodeAgent(unittest.TestCase):
    def setUp(self):
        self.typer = CodeTyper()

    def test_speed_presets_exist(self):
        for mode in ["TURBO", "FAST", "STEALTH"]:
            self.assertIn(mode, SPEED_PRESETS)
            self.assertIn("char_delay", SPEED_PRESETS[mode])
            self.assertIn("line_delay", SPEED_PRESETS[mode])

    def test_prepare_code_normalizes_crlf(self):
        raw = "line1\r\nline2\rline3\nline4"
        lines = self.typer._prepare_code(raw)
        self.assertEqual(lines, ["line1", "line2", "line3", "line4"])

    def test_prepare_code_expands_tabs(self):
        raw = "def solve():\n\tx = 1\n\t\ty = 2"
        self.typer.expand_tabs = True
        self.typer.tab_spaces = 4
        lines = self.typer._prepare_code(raw)
        self.assertEqual(lines[0], "def solve():")
        self.assertEqual(lines[1], "    x = 1")
        self.assertEqual(lines[2], "        y = 2")

    def test_abort_signals_stop_event(self):
        self.typer.is_typing = True
        self.typer.abort()
        self.assertTrue(self.typer._stop_event.is_set())

    def test_start_typing_empty_code_returns_false(self):
        self.typer.code_text = "   "
        started = self.typer.start_typing()
        self.assertFalse(started)

    def test_handle_command_triggers_code_agent(self):
        with patch("code_agent.open_code_agent") as mock_open:
            res = handle_command("open code typer")
            self.assertIsNotNone(res)
            self.assertIn("Code Agent", res)
            mock_open.assert_called_once()

    def test_handle_command_codetantra_keyword(self):
        with patch("code_agent.open_code_agent") as mock_open:
            res = handle_command("type codetantra code")
            self.assertIsNotNone(res)
            self.assertIn("Code Agent", res)
            mock_open.assert_called_once()

    def test_is_admin_returns_bool(self):
        from code_agent import is_admin
        self.assertIsInstance(is_admin(), bool)

    def test_focus_target_window_handles_missing_window(self):
        from code_agent import focus_target_window
        # Random non-existent title should return False without crashing
        res = focus_target_window(title_keywords=("__non_existent_window_123456__",))
        self.assertFalse(res)

    def test_press_char_invokes_keyboard(self):
        with patch("code_agent.hardware_press_char") as mock_hw:
            mock_hw.return_value = True
            self.typer.injection_mode = "SCANCODE"
            self.typer._press_char("A")
            mock_hw.assert_called_once_with("A")

    def test_arm_click_to_inject_sets_armed(self):
        self.typer.code_text = "int main() {}"
        res = self.typer.arm_click_to_inject()
        self.assertTrue(res)
        self.assertTrue(self.typer._armed)
        self.typer.disarm()
        self.assertFalse(self.typer._armed)

    def test_unicode_mode_invokes_send_unicode(self):
        with patch("code_agent.send_unicode_char") as mock_uni:
            mock_uni.return_value = True
            self.typer.injection_mode = "UNICODE"
            self.typer._press_char("X")
            mock_uni.assert_called_once_with("X")

    # ---------------- Focus guarantee & honest reporting ----------------

    def test_pick_target_window_no_match_returns_none(self):
        from code_agent import pick_target_window
        self.assertIsNone(pick_target_window(title_keywords=("__non_existent_window_123456__",)))

    def test_focus_is_ours_returns_bool(self):
        self.assertIsInstance(self.typer._focus_is_ours(), bool)

    def test_secure_focus_proceeds_when_other_window_focused(self):
        # If another app owns the keyboard focus, typing must proceed immediately
        with patch.object(self.typer, "_focus_is_ours", return_value=False):
            self.assertTrue(self.typer._secure_focus_for_typing(timeout=0.5))

    def test_secure_focus_aborts_when_focus_stuck_on_ultron(self):
        # When focus can never leave Ultron, it must report failure, not type into the void
        with patch.object(self.typer, "_focus_is_ours", return_value=True), \
             patch("code_agent.focus_target_window", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows"), \
             patch.object(self.typer, "_restore_own_windows") as mock_restore:
            self.assertFalse(self.typer._secure_focus_for_typing(timeout=0.3))
            mock_restore.assert_called_once()

    def test_click_editor_skips_when_no_target_window(self):
        # No CodeTantra window around -> the mouse must NOT be moved at all
        with patch("code_agent.pick_target_window", return_value=None), \
             patch("code_agent.pyautogui.moveTo") as mock_move:
            self.assertFalse(self.typer._click_editor_into_place())
            mock_move.assert_not_called()

    def test_click_editor_skips_right_after_user_click_inside_target(self):
        # The user's own recent click inside CodeTantra already placed the caret,
        # so a synthetic click would become a double-click (word selection)
        self.typer._last_user_click_ts = time.time()
        self.typer._last_user_click_pos = (100, 100)
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=True), \
             patch("code_agent.pyautogui.moveTo") as mock_move:
            self.assertFalse(self.typer._click_editor_into_place())
            mock_move.assert_not_called()

    def test_user_recently_clicked_ignores_old_clicks(self):
        # A click older than 2s (e.g. the INJECT button press) must not block auto-click
        self.typer._last_user_click_ts = time.time() - 5.0
        self.typer._last_user_click_pos = (10, 10)
        self.assertFalse(self.typer._user_recently_clicked_in(4242))

    def test_click_editor_moves_mouse_and_clicks(self):
        # Full happy path: activate -> UIA stage fails -> move mouse in -> editor took focus
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows"), \
             patch("code_agent._activate_window", return_value=True), \
             patch.object(self.typer, "_focus_editor_via_uia", return_value=False), \
             patch.object(self.typer, "_editor_click_candidates", return_value=[(300, 400)]), \
             patch("code_agent._window_at_point", return_value=4242), \
             patch.object(self.typer, "_editor_focused", return_value=True), \
             patch("code_agent.pyautogui.moveTo") as mock_move, \
             patch("code_agent.pyautogui.click") as mock_click:
            self.assertTrue(self.typer._click_editor_into_place())
            mock_move.assert_called_once_with(300, 400, duration=0.30)
            mock_click.assert_called_once_with(300, 400)

    def test_click_editor_retries_next_candidate_when_click_missed(self):
        # Clicking the question panel / a wrong widget must trigger a corrected 2nd click
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows"), \
             patch("code_agent._activate_window", return_value=True), \
             patch.object(self.typer, "_focus_editor_via_uia", return_value=False), \
             patch.object(self.typer, "_editor_click_candidates",
                          return_value=[(100, 100), (500, 400)]), \
             patch("code_agent._window_at_point", return_value=4242), \
             patch.object(self.typer, "_editor_focused", side_effect=[False, True]), \
             patch("code_agent.pyautogui.moveTo") as mock_move, \
             patch("code_agent.pyautogui.click") as mock_click:
            self.assertTrue(self.typer._click_editor_into_place())
            self.assertEqual(mock_move.call_count, 2)
            self.assertEqual(mock_click.call_count, 2)

    def test_click_editor_stops_after_two_unverifiable_clicks(self):
        # When focus can never be verified it must NOT keep clicking around the app
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows"), \
             patch("code_agent._activate_window", return_value=True), \
             patch.object(self.typer, "_focus_editor_via_uia", return_value=False), \
             patch.object(self.typer, "_editor_click_candidates",
                          return_value=[(100, 100), (500, 400), (900, 700)]), \
             patch("code_agent._window_at_point", return_value=4242), \
             patch.object(self.typer, "_editor_focused", return_value=None), \
             patch("code_agent._uia_module", return_value=object()), \
             patch("code_agent.pyautogui.moveTo") as mock_move:
            self.assertTrue(self.typer._click_editor_into_place())
            self.assertEqual(mock_move.call_count, 2)  # stopped after two unknowns

    def test_click_editor_raises_target_when_point_obscured(self):
        # Ultron overlay floats over the point -> raise CodeTantra, then click
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows"), \
             patch("code_agent._activate_window", return_value=True), \
             patch.object(self.typer, "_focus_editor_via_uia", return_value=False), \
             patch.object(self.typer, "_editor_click_candidates", return_value=[(100, 100)]), \
             patch("code_agent._window_at_point", side_effect=[999, 4242]), \
             patch("code_agent._raise_above", return_value=True) as mock_raise, \
             patch("code_agent._is_topmost", return_value=False), \
             patch("code_agent._lower_back") as mock_lower, \
             patch.object(self.typer, "_editor_focused", return_value=True), \
             patch("code_agent.pyautogui.moveTo"), \
             patch("code_agent.pyautogui.click") as mock_click:
            self.assertTrue(self.typer._click_editor_into_place())
            mock_raise.assert_called_once_with(4242)
            mock_lower.assert_called_once_with(4242)
            self.assertEqual(mock_click.call_count, 1)

    def test_click_editor_skips_point_still_owned_by_other_window(self):
        # If another window keeps owning the pixel, do NOT click into it
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows"), \
             patch("code_agent._activate_window", return_value=True), \
             patch.object(self.typer, "_focus_editor_via_uia", return_value=False), \
             patch.object(self.typer, "_editor_click_candidates", return_value=[(100, 100)]), \
             patch("code_agent._window_at_point", return_value=999), \
             patch("code_agent._raise_above", return_value=True), \
             patch("code_agent._lower_back") as mock_lower, \
             patch.object(self.typer, "_editor_focused") as mock_focus, \
             patch("code_agent.pyautogui.moveTo"), \
             patch("code_agent.pyautogui.click") as mock_click:
            self.assertTrue(self.typer._click_editor_into_place())
            mock_click.assert_not_called()
            mock_focus.assert_not_called()
            mock_lower.assert_called_once_with(4242)

    def test_click_editor_prefers_uia_focus_over_mouse(self):
        # Stage 1 (UIA SetFocus) succeeding must skip the mouse entirely
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch.object(self.typer, "_user_recently_clicked_in", return_value=False), \
             patch.object(self.typer, "_minimize_own_windows") as mock_min, \
             patch("code_agent._activate_window", return_value=True), \
             patch.object(self.typer, "_focus_editor_via_uia", return_value=True), \
             patch("code_agent.pyautogui.moveTo") as mock_move:
            self.assertTrue(self.typer._click_editor_into_place())
            mock_min.assert_called_once()
            mock_move.assert_not_called()

    def test_focus_editor_via_uia_false_without_uia_module(self):
        with patch("code_agent._uia_module", return_value=None):
            self.assertFalse(self.typer._focus_editor_via_uia(4242))

    def test_run_typing_process_replants_focus_mid_typing(self):
        # A kiosk app that steals the caret between lines must be corrected:
        # line 1 types, line 2 detects the stolen caret and replants it
        self.typer.code_text = "line one alpha\nline two beta"
        self.typer.countdown_seconds = 0
        self.typer.auto_click_editor = False
        with patch.object(self.typer, "_secure_focus_for_typing", return_value=True), \
             patch("code_agent.is_admin", return_value=True), \
             patch.object(self.typer, "_press_char", return_value=True), \
             patch("code_agent.pick_target_window", return_value=4242), \
             patch("code_agent._uia_module", return_value=object()), \
             patch.object(self.typer, "_editor_focused", side_effect=[False]) as mock_chk, \
             patch.object(self.typer, "_reassert_editor_focus", return_value=True) as mock_re, \
             patch.object(self.typer, "_verify_text_visible", return_value=True), \
             patch.object(self.typer, "_restore_own_windows"), \
             patch.object(self.typer, "_beep"):
            self.typer._run_typing_process()
        self.assertTrue(mock_chk.called)
        mock_re.assert_called_once_with(4242)
        self.assertEqual(self.typer._chars_sent, 28)  # 14 + newline + 13 chars delivered

    def test_editor_click_candidates_clamp_inside_window(self):
        # Geometry candidates must land inside the window, clear of title bar and edges
        with patch("code_agent._uia_editor_candidates", return_value=[]), \
             patch("code_agent._window_rect", return_value=(100, 100, 1100, 800)):
            points = self.typer._editor_click_candidates(4242)
            self.assertGreaterEqual(len(points), 3)
            for x, y in points:
                self.assertTrue(130 <= x <= 1070, f"x={x} outside clamps")
                self.assertTrue(150 <= y <= 760, f"y={y} outside clamps")

    def test_editor_focused_true_for_edit_near_click(self):
        from types import SimpleNamespace

        class _Rect:
            left, top, right, bottom = 290, 390, 310, 410

        class _Edit:
            ControlTypeName = "EditControl"
            BoundingRectangle = _Rect()

        fake_auto = SimpleNamespace(GetFocusedControl=lambda: _Edit())
        with patch("code_agent._uia_module", return_value=fake_auto):
            self.assertTrue(self.typer._editor_focused(near=(300, 400)))

    def test_editor_focused_false_for_button(self):
        from types import SimpleNamespace

        class _Button:
            ControlTypeName = "ButtonControl"
            BoundingRectangle = None

        fake_auto = SimpleNamespace(GetFocusedControl=lambda: _Button())
        with patch("code_agent._uia_module", return_value=fake_auto):
            self.assertFalse(self.typer._editor_focused())

    def test_editor_focused_false_when_wrong_window_focused(self):
        # Ultron's own GUI text box is an EditControl too — it must NEVER
        # count as a successful editor click (this was the false "caret ready!")
        from types import SimpleNamespace

        class _Rect:
            left, top, right, bottom = 290, 390, 310, 410

        class _Edit:
            ControlTypeName = "EditControl"
            BoundingRectangle = _Rect()

        fake_auto = SimpleNamespace(GetFocusedControl=lambda: _Edit())
        with patch("code_agent._uia_module", return_value=fake_auto), \
             patch("code_agent._foreground_root", return_value=999):  # NOT CodeTantra
            self.assertFalse(
                self.typer._editor_focused(near=(300, 400), must_window=4242)
            )

    def test_verify_text_visible_true_when_code_on_screen(self):
        from types import SimpleNamespace

        class _Line:
            ControlTypeName = "TextControl"
            Name = "int main() {"

            def GetFirstChildControl(self):
                return None

            def GetNextSiblingControl(self):
                return None

        class _Root:
            ControlTypeName = "WindowControl"
            Name = ""

            def GetFirstChildControl(self):
                return _Line()

            def GetNextSiblingControl(self):
                return None

        fake_auto = SimpleNamespace(
            SetGlobalSearchTimeout=lambda *_: None,
            ControlFromHandle=lambda *_: _Root(),
        )
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch("code_agent._uia_module", return_value=fake_auto):
            self.assertTrue(self.typer._verify_text_visible(["int main() {"]))

    def test_verify_text_visible_false_when_code_absent(self):
        # Keys were delivered but the code is nowhere in the window tree
        from types import SimpleNamespace

        class _Root:
            ControlTypeName = "WindowControl"
            Name = "CodeTantra-SEA"

            def GetFirstChildControl(self):
                return None

            def GetNextSiblingControl(self):
                return None

        fake_auto = SimpleNamespace(
            SetGlobalSearchTimeout=lambda *_: None,
            ControlFromHandle=lambda *_: _Root(),
        )
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch("code_agent._uia_module", return_value=fake_auto):
            self.assertFalse(
                self.typer._verify_text_visible(["def solve(): return 1"])
            )

    def test_verify_text_visible_unknown_without_uia(self):
        with patch("code_agent.pick_target_window", return_value=4242), \
             patch("code_agent._uia_module", return_value=None):
            self.assertIsNone(
                self.typer._verify_text_visible(["def solve(): return 1"])
            )

    def test_press_char_reports_blocked_input(self):
        # When every injection method fails, _press_char must return False so the
        # typer can stop instead of claiming fake success
        with patch("code_agent.send_unicode_char", return_value=False), \
             patch("code_agent.hardware_press_char", return_value=False), \
             patch.object(self.typer._keyboard, "type", side_effect=Exception("blocked")), \
             patch("code_agent.pyautogui.write", side_effect=Exception("blocked")):
            self.typer.injection_mode = "UNICODE"
            self.assertFalse(self.typer._press_char("A"))

    def test_target_requires_admin_handles_no_target(self):
        with patch("code_agent.pick_target_window", return_value=None):
            self.assertFalse(self.typer._target_requires_admin())

    def test_window_is_elevated_none_for_no_hwnd(self):
        from code_agent import window_is_elevated
        self.assertIsNone(window_is_elevated(None))


if __name__ == "__main__":
    unittest.main()

