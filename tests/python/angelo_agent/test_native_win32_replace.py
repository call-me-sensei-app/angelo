# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``win32_replace``: a POSIX-semantics replace on Windows, and ``os.replace`` where that is unsupported.

The rename itself runs only on Windows (``windows_journal_replace_check.py`` checks it on a real host); here the
structure it passes, its error mapping and its fallback are pinned. ``test_native_journal_guard`` drives a chat's
store through both paths.
"""

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import win32_replace  # noqa: E402


class RenameInfoTests(unittest.TestCase):
    def test_the_structure_is_file_rename_info(self):
        info = win32_replace.rename_info("C:\\angelo\\chat.json")
        fields = {name: getattr(type(info), name).offset for name in ("flags", "root", "length", "name")}
        pointer = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(fields, {"flags": 0, "root": pointer, "length": 2 * pointer, "name": 2 * pointer + 4})
        self.assertEqual(info.flags, 0x1 | 0x2)   # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        self.assertEqual(info.length, len("C:\\angelo\\chat.json".encode("utf-16-le")))
        self.assertEqual(self.name_bytes(info), "C:\\angelo\\chat.json".encode("utf-16-le"))
        self.assertIsNone(info.root)

    @staticmethod
    def name_bytes(info):
        """The structure's name, its terminator excluded, exactly as Windows reads it: ``length`` bytes at its offset."""
        start = type(info).name.offset
        return ctypes.string_at(ctypes.addressof(info) + start, info.length)

    def test_lengths_and_buffers_are_utf_16_code_units_not_python_characters(self):
        """External audit 3 #2: one emoji made the declared name end in ``.jso``; more overran the buffer."""
        names = ["C:\\Users\\a\\Chats \U0001F600\\chat.json",                      # One emoji (audit repro).
                 "C:\\\U0001F3A8\U0001F5BC\\\U0001D11E\U00020000 \U0001F468\u200d\U0001F469\\c.json",   # Several.
                 "D:\\caf\u00e9\\\u65e5\u672c\\chat.json",                                  # BMP only: one unit each.
                 "C:\\" + "\U0001F600" * 300 + "\\chat.json",                            # Long: no overrun.
                 "C:\\lone \udc80\\chat.json"]                                           # A lone surrogate.
        for name in names:
            with self.subTest(name=name):
                encoded = name.encode("utf-16-le", "surrogatepass")
                info = win32_replace.rename_info(name)
                self.assertEqual(info.length, len(encoded))
                self.assertEqual(self.name_bytes(info), encoded)
                self.assertEqual(self.name_bytes(info).decode("utf-16-le", "surrogatepass"), name)   # All of it.
                start = type(info).name.offset
                self.assertEqual(ctypes.string_at(ctypes.addressof(info) + start + info.length, 2), b"\0\0")
                self.assertGreaterEqual(ctypes.sizeof(info), start + info.length + 2)   # Terminator inside it.
                self.assertEqual(type(info).name.size, len(encoded) + 2)   # The buffer: the name and its terminator.


class ErrorTests(unittest.TestCase):
    def test_unsupported_codes_fall_back_and_in_use_codes_are_retried_as_permission_errors(self):
        for code in (1, 50, 87):
            with self.assertRaises(win32_replace.Unsupported):
                win32_replace._raise(code, "a", "b")
        for code in (5, 32):
            with self.assertRaises(PermissionError):
                win32_replace._raise(code, "a", "b")
        with self.assertRaises(FileNotFoundError):
            win32_replace._raise(2, "a", "b")
        with self.assertRaises(PermissionError):   # Opening the source: never read as unsupported semantics.
            win32_replace._raise(5, "a", "b", opening=True)
        with self.assertRaises(OSError):
            win32_replace._raise(87, "a", "b", opening=True)


class FallbackTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source, self.target = Path(folder.name, "new.tmp"), Path(folder.name, "chat.json")
        self.source.write_bytes(b"new")
        self.target.write_bytes(b"old")
        for name, value in (("_logged", False), ("last", None)):
            patcher = patch.object(win32_replace, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_off_windows_or_unsupported_it_replaces_classically_and_logs_once(self):
        with patch("sys.stderr") as stderr:
            win32_replace.replace(self.source, self.target)
            self.source.write_bytes(b"newer")
            win32_replace.replace(self.source, self.target)
        self.assertEqual((win32_replace.last, self.target.read_bytes()), (win32_replace.CLASSIC, b"newer"))
        self.assertEqual(sum("POSIX semantics" in str(call) for call in stderr.write.call_args_list), 1)

    def test_posix_semantics_when_supported_and_its_refusal_raised(self):
        calls = []
        with patch.object(win32_replace, "_posix", lambda source, target: calls.append((source, target))):
            win32_replace.replace(self.source, self.target)
        self.assertEqual((calls, win32_replace.last), ([(str(self.source), str(self.target))], win32_replace.POSIX))
        refused = PermissionError(13, "[WinError 32] the file is in use")
        with patch.object(win32_replace, "_posix", side_effect=refused), patch.object(os, "replace") as classic:
            with self.assertRaises(PermissionError):
                win32_replace.replace(self.source, self.target)
        classic.assert_not_called()   # Only unsupported semantics fall back: an in-use refusal is the caller's retry.


if __name__ == "__main__":
    unittest.main()
