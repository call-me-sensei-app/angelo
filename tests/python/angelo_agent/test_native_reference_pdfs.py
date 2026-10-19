# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Attached PDFs: text extracted once per file, failures included, and never on Blender's main thread.

A stand-in ``pdftotext`` (patched ``child_process.run``) records each call and
the thread it ran on; no real PDF tool or provider is used.
"""

import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import references as references_module  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

DOC = ("reference-pdf-fixture", 3)
PDF = b"%PDF-1.4\n% fixture bytes, never parsed: the extractor is a stand-in\n"


class FakeExtractor:
    """``child_process.run`` for pdftotext: an outcome per call, recording the arguments and the thread."""

    def __init__(self, outcome="text", text="Extracted PDF text", gate=None):
        self.outcome, self.text, self.calls, self.lock, self.gate = outcome, text, [], threading.Lock(), gate

    def __call__(self, arguments, **options):
        with self.lock:
            self.calls.append({"arguments": arguments, "options": options, "thread": threading.current_thread()})
        if self.gate is not None:
            self.gate.wait(10)   # Still extracting until the test releases it.
        if self.outcome == "timeout":
            raise subprocess.TimeoutExpired(arguments, options.get("timeout"))
        if self.outcome == "failed":
            return subprocess.CompletedProcess(arguments, 1, b"", b"Syntax Error")
        return subprocess.CompletedProcess(arguments, 0, self.text.encode(), b"")


class ReferencePdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        which = patch.object(references_module.shutil, "which", return_value="/usr/bin/pdftotext")
        which.start()
        self.addCleanup(which.stop)

    def extractor(self, outcome="text"):
        fake = FakeExtractor(outcome)
        context = patch.object(references_module.child_process, "run", fake)
        context.start()
        self.addCleanup(context.stop)
        return fake

    def attach(self, store, name="brief.pdf"):
        source = self.directory / name
        source.write_bytes(PDF)
        return store.import_images([source])[0]

    def store(self):
        return ReferenceStore(self.directory, str(uuid.uuid4()))

    def test_a_failed_pdf_is_extracted_once_per_file_and_its_failure_is_cached(self):
        fake, store = self.extractor("failed"), self.store()
        record = self.attach(store)
        for _ in range(3):   # Three turns prime it: one extraction.
            with self.assertRaisesRegex(ValueError, "Couldn't extract this PDF's text"):
                store.content(record)
        self.assertEqual(len(fake.calls), 1)

    def test_a_timed_out_pdf_is_not_retried_each_turn_and_keeps_its_30_second_bound(self):
        fake, store = self.extractor("timeout"), self.store()
        record = self.attach(store)
        for _ in range(3):   # Each turn's priming waited up to 30 s again.
            with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                store.content(record)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["options"]["timeout"], 30)
        with self.assertRaisesRegex(ValueError, "timed out"):
            store.content(record)

    def test_priming_reads_each_historical_pdf_once_and_skips_what_failed(self):
        fake, store = self.extractor("timeout"), self.store()
        record = self.attach(store)
        text = self.attach(store, "notes.txt")
        Path(store.checked_path(text)).write_text("plain notes", encoding="utf-8")
        messages = [{"role": "user", "text": "See the brief", "references": [record, text]}]
        for _ in range(3):
            store.prime(messages)   # The provider worker, before each turn's model connection.
        self.assertEqual(len(fake.calls), 1)

    def test_a_changed_file_is_extracted_again(self):
        fake, store = self.extractor("failed"), self.store()
        record = self.attach(store)
        with self.assertRaises(ValueError):
            store.content(record)
        Path(store.checked_path(record)).write_bytes(PDF + b"% replaced\n")
        with self.assertRaises(ValueError):
            store.content(record)
        self.assertEqual(len(fake.calls), 2, "A file of another size or time is another file")

    def test_extracted_text_is_cached(self):
        fake, store = self.extractor(), self.store()
        record = self.attach(store)
        self.assertEqual(store.read(record, 0, 9)["text"], "Extracted")
        self.assertEqual(store.read(record, 10, 3)["text"], "PDF")
        self.assertEqual(len(fake.calls), 1)

    def test_a_missing_extractor_is_not_cached_as_the_files_failure(self):
        fake, store = self.extractor(), self.store()
        record = self.attach(store)
        with patch.object(references_module.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "requires installed pdftotext"):
                store.content(record)
        self.assertEqual(store.content(record), "Extracted PDF text", "Installing pdftotext takes effect")
        self.assertEqual(len(fake.calls), 1)


class ToolReadThreadTests(unittest.TestCase):
    """``angelo_read_reference`` runs in the UI pump on Blender's main thread: it must never run pdftotext."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.controller = Controller(self.directory)
        self.addCleanup(self.controller.close)
        self.controller.set_document(DOC)
        self.controller.busy = True
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        for context in (patch.object(references_module.shutil, "which", return_value="/usr/bin/pdftotext"),
                        patch.object(references_module.child_process, "run", FakeExtractor(gate=self.release))):
            context.start()
            self.addCleanup(context.stop)
        self.fake = references_module.child_process.run
        source = self.directory / "old-brief.pdf"
        source.write_bytes(PDF)
        self.record = self.controller.references.import_images([str(source)])[0]
        # Older than the 80 messages the provider worker primes: never extracted before the tool reads it.
        self.controller.state["messages"].append({"role": "user", "text": "Brief", "references": [self.record]})

    def read(self, suffix):
        answers = []
        self.controller.request_tool(suffix, "angelo_read_reference",
                                     {"reference": self.record["id"], "start": 0, "length": 9}, answers.append)
        self.controller.pump_tools(object.__new__(type("Executor", (), {})), DOC,
                                   budget=ToolDispatchBudget(seconds=60))
        return answers[-1]

    def test_an_unprimed_pdf_is_extracted_off_the_main_thread_and_read_on_the_next_call(self):
        self.assertIs(threading.current_thread(), threading.main_thread())
        first = self.read("first")
        self.assertIs(first.get("ready"), False, first)
        self.assertIn("call angelo_read_reference again", first["note"])
        self.assertIs(self.read("while-extracting").get("ready"), False, "One extraction, still running")
        self.release.set()
        deadline = time.monotonic() + 10
        while not self.fake.calls or self.controller.references.extracting():
            self.assertLess(time.monotonic(), deadline, "The background extraction never finished")
            time.sleep(0.01)
        second = self.read("second")
        self.assertEqual(second["text"], "Extracted")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertTrue(all(call["thread"] is not threading.main_thread() for call in self.fake.calls),
                        "pdftotext ran on Blender's main thread")


if __name__ == "__main__":
    unittest.main()
