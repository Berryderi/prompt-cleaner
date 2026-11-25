import ctypes
import unittest

import support  # noqa: F401
import config
import winapi


class HotkeyParseTests(unittest.TestCase):
    def test_default_hotkey(self):
        mods, vk = winapi.parse_hotkey("ctrl+alt+space")
        self.assertEqual(mods, winapi.MOD_CONTROL | winapi.MOD_ALT)
        self.assertEqual(vk, winapi.VK_SPACE)

    def test_letters_function_keys_and_case(self):
        self.assertEqual(winapi.parse_hotkey("Ctrl+Shift+K"), (winapi.MOD_CONTROL | winapi.MOD_SHIFT, ord("K")))
        self.assertEqual(winapi.parse_hotkey("alt+f9")[1], 0x78)

    def test_rejects_bad_specs(self):
        for bad in ["space", "ctrl+", "hyper+space", "ctrl+alt+notakey"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    winapi.parse_hotkey(bad)


class TerminalDetectionTests(unittest.TestCase):
    TERMINALS = config.Config().terminal_processes

    def test_known_terminals(self):
        for name in ["WindowsTerminal.exe", "OpenConsole.exe", "conhost.exe", "cmd.exe", "powershell.exe",
                     "pwsh.exe", "wezterm-gui.exe", "alacritty.exe", "mintty.exe"]:
            with self.subTest(name=name):
                self.assertTrue(winapi.is_terminal(name, self.TERMINALS))

    def test_normal_apps_are_not_terminals(self):
        for name in ["notepad.exe", "chrome.exe", "code.exe", "msedge.exe", "winword.exe"]:
            with self.subTest(name=name):
                self.assertFalse(winapi.is_terminal(name, self.TERMINALS))

    def test_unknown_process_is_treated_as_terminal_so_ctrl_c_is_never_sent(self):
        self.assertTrue(winapi.is_terminal("", self.TERMINALS))

    def test_real_foreground_process_name_is_lowercase_exe(self):
        _, name = winapi.foreground()
        self.assertTrue(name == "" or name.endswith(".exe"))
        self.assertEqual(name, name.lower())


class StructTests(unittest.TestCase):
    def test_input_struct_matches_win32_size(self):
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(winapi.INPUT), expected)


class MenuMaskTests(unittest.TestCase):
    def test_taps_unassigned_key_only_while_alt_or_win_is_held(self):
        from unittest import mock

        sent = []
        with mock.patch.object(winapi, "send_keys", side_effect=sent.append),                 mock.patch.object(winapi, "keys_down", return_value=[winapi.VK_MENU]):
            self.assertTrue(winapi.mask_menu_activation())
        self.assertEqual(sent, [[(winapi.VK_UNASSIGNED, False), (winapi.VK_UNASSIGNED, True)]])
        sent.clear()
        with mock.patch.object(winapi, "send_keys", side_effect=sent.append),                 mock.patch.object(winapi, "keys_down", return_value=[]):
            self.assertFalse(winapi.mask_menu_activation())
        self.assertEqual(sent, [])


class GrabNeverSendsCtrlCToTerminalsTests(unittest.TestCase):
    """app.handle_hotkey must route terminals to the box, not to grab_selection (Ctrl+C = SIGINT)."""

    def test_terminal_goes_to_box_not_ctrl_c(self):
        import types
        from unittest import mock

        import app

        calls = {"grab": 0, "box": 0}
        fake_box = types.SimpleNamespace(ask=lambda: calls.__setitem__("box", calls["box"] + 1))
        cfg = config.Config()
        with mock.patch.object(winapi, "foreground", return_value=(1234, "windowsterminal.exe")), \
                mock.patch.object(winapi, "grab_selection", side_effect=lambda: calls.__setitem__("grab", 1)), \
                mock.patch.object(winapi, "send_chord", side_effect=AssertionError("Ctrl+C sent to a terminal")), \
                mock.patch.dict("sys.modules", {"dictation_box": fake_box}):
            app.handle_hotkey(cfg, cleaner=None)
        self.assertEqual(calls, {"grab": 0, "box": 1})

    def test_normal_app_uses_selection_grab(self):
        from unittest import mock

        import app

        class FakeCleaner:
            def clean(self, text):
                import cleaner
                return cleaner.CleanResult(text.upper(), "rules-only")

        pasted = []
        with mock.patch.object(winapi, "foreground", return_value=(1, "notepad.exe")), \
                mock.patch.object(winapi, "grab_selection", return_value="hello"), \
                mock.patch.object(winapi, "paste_text", side_effect=pasted.append):
            app.handle_hotkey(config.Config(), FakeCleaner())
        self.assertEqual(pasted, ["HELLO"])

    def test_nothing_selected_does_nothing(self):
        from unittest import mock

        import app

        with mock.patch.object(winapi, "foreground", return_value=(1, "notepad.exe")), \
                mock.patch.object(winapi, "grab_selection", return_value=None), \
                mock.patch.object(winapi, "paste_text", side_effect=AssertionError("pasted")), \
                mock.patch.object(winapi, "set_clipboard_text", side_effect=AssertionError("clipboard touched")):
            app.handle_hotkey(config.Config(), cleaner=None)


if __name__ == "__main__":
    unittest.main()
