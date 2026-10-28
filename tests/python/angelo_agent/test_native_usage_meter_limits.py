# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The usage ledger always reads back what it wrote, and never writes over a file it could not read.

Regression (release audit of 1.0.4/1.0.5, P2): the writer capped the chat count (``MAX_CHATS``) but not the bytes,
while the reader ignored a file over ``MAX_FILE_BYTES``: 1,000 chats with five connection buckets each came to
1,059,207 bytes, written, silently ignored at the next start and then replaced by the next write. A file the reader
could not read (EACCES, EIO) read as empty the same way and was replaced too. Display history only; provider billing
is unaffected. Developed on ASCII buckets and EACCES; multi-byte names, EIO/EPERM/ESTALE, a later version's file and
the merge after a failed read are held out.
"""

import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import session_kept, usage_meter  # noqa: E402

MODES = ("claude_api", "claude_subscription", "codex_subscription", "openai_api", "team")


def bucket(models=("claude-opus-5-5",)):
    return dict(dict.fromkeys(usage_meter.TOKEN_KEYS + usage_meter.COUNT_KEYS, 123456789), usd=12.345678,
                models=list(models))


def chats(count=1000, modes=MODES, models=("claude-opus-5-5",)):
    return {"chat-%04d-%s" % (number, "x" * 40): {"modes": {mode: bucket(models) for mode in modes},
                                                    "updated": 1000.0 + number,
                                                    "last": dict(bucket(models), mode=modes[0])}
            for number in range(count)}


class Limits(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "usage.json"

    def ledger(self):
        return usage_meter.UsageLedger(self.directory, clock=lambda: 5000.0)

    def test_what_the_writer_writes_the_reader_reads_back(self):
        cases = {"audit": chats(), "multi-byte names": chats(modes=tuple("接続モード%d" % n for n in range(5)),
                                                             models=("モデル-" + "名" * 40,) * 4)}
        for case, saved in cases.items():
            with self.subTest(case=case):
                ledger = self.ledger()
                ledger.chats = json.loads(json.dumps(saved))
                self.assertTrue(ledger.flush())
                self.assertLessEqual(self.path.stat().st_size, usage_meter.MAX_FILE_BYTES)
                again = self.ledger()
                self.assertTrue(again.chats, "read back, never ignored")
                newest = max(saved, key=lambda key: saved[key]["updated"])
                self.assertIn(newest, again.chats)
                kept = sorted(again.chats, key=lambda key: saved[key]["updated"])
                left_out = set(saved) - set(again.chats)
                self.assertTrue(all(saved[key]["updated"] < saved[kept[0]]["updated"] for key in left_out),
                                "only the oldest chats are left out")

    def test_a_file_an_earlier_version_wrote_past_the_limit_is_read_and_trimmed(self):
        document = {"version": 1, "chats": chats()}
        self.path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        self.assertGreater(self.path.stat().st_size, usage_meter.MAX_FILE_BYTES)
        ledger = self.ledger()
        self.assertEqual(len(ledger.chats), 1000)
        self.assertTrue(ledger.flush())
        self.assertLessEqual(self.path.stat().st_size, usage_meter.MAX_FILE_BYTES)
        self.assertTrue(self.ledger().chats)

    def test_a_file_that_cant_be_read_is_never_written_over(self):
        saved = {"version": 1, "chats": chats(count=3)}
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        before = self.path.read_bytes()
        real = os.open
        for number in (errno.EACCES, errno.EIO, errno.EPERM, errno.ESTALE):
            with self.subTest(errno=errno.errorcode[number]):
                failing = [True]

                def opener(path, flags, *args, **kwargs):
                    if failing[0] and os.fspath(path) == os.fspath(self.path):
                        raise OSError(number, os.strerror(number))
                    return real(path, flags, *args, **kwargs)
                with mock.patch("angelo_agent.usage_meter.os.open", opener):
                    ledger = self.ledger()
                    self.assertEqual(ledger.chats, {})
                    ledger.chats["new-chat"] = {"modes": {"claude_api": bucket()}, "updated": 6000.0}
                    self.assertFalse(ledger.flush(), "not written while the file can't be read")
                    self.assertEqual(self.path.read_bytes(), before)
                    failing[0] = False   # Readable again: the next write adds what the file holds.
                    self.assertTrue(ledger.flush())
                again = self.ledger()
                self.assertEqual(set(again.chats), set(saved["chats"]) | {"new-chat"})
                self.path.write_bytes(before)

    def test_a_chat_counted_while_the_file_was_unreadable_adds_to_its_saved_totals(self):
        saved = chats(count=1)
        chat_id = next(iter(saved))
        self.path.write_text(json.dumps({"version": 1, "chats": saved}), encoding="utf-8")
        real = os.open
        with mock.patch("angelo_agent.usage_meter.os.open",
                        lambda path, flags, *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error"))
                        if os.fspath(path) == os.fspath(self.path) else real(path, flags, *a, **k)):
            ledger = self.ledger()
        ledger.chats[chat_id] = {"modes": {"claude_api": dict(bucket(), input=1)}, "updated": 7000.0}
        self.assertTrue(ledger.flush())
        merged = self.ledger().chats[chat_id]
        self.assertEqual(merged["modes"]["claude_api"]["input"], 123456789 + 1)
        self.assertEqual(merged["updated"], 7000.0)
        self.assertIn("codex_subscription", merged["modes"])

    def test_a_later_versions_file_is_kept_and_a_damaged_one_is_kept_beside_it_then_replaced(self):
        large = " " * (usage_meter.LEGACY_FILE_BYTES + 1)
        for text, kept in ((json.dumps({"version": 2, "chats": {}}), True), ("{ damaged", False), ("", False),
                           (json.dumps([1, 2]), False), (json.dumps({"version": 0, "chats": {}}), False),
                           (json.dumps({"version": "1", "chats": {}}), False), (large, False)):
            with self.subTest(text=text[:20]):
                for copy in session_kept.kept_copies(self.path):
                    copy.unlink()
                self.path.write_text(text, encoding="utf-8")
                ledger = self.ledger()
                ledger.chats["new-chat"] = {"modes": {"claude_api": bucket()}, "updated": 6000.0}
                with mock.patch("builtins.print"):
                    self.assertEqual(ledger.flush(), not kept)
                self.assertEqual(self.path.read_text(encoding="utf-8") == text, kept)
                copies = [copy.read_text(encoding="utf-8") for copy in session_kept.kept_copies(self.path)]
                self.assertEqual(copies, [] if kept else [text])

    def test_no_file_reads_as_empty_and_is_written(self):
        ledger = self.ledger()
        self.assertEqual(ledger.chats, {})
        ledger.chats["c"] = {"modes": {"claude_api": bucket()}, "updated": 1.0}
        self.assertTrue(ledger.flush())
        self.assertIn("c", self.ledger().chats)


if __name__ == "__main__":
    unittest.main()
