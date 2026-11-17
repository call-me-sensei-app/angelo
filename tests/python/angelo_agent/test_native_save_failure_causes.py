# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A save that fails says why, in plain words, and what to do next (``save_failure``; showcase finding F9).

"The invocation journal could not be saved; no native work was admitted" named no cause. A showcase session met it
three more times (its own background test renders used the same configuration folder, the two-process cause of
M8) and neither the agent nor the person could tell a full disk from another Angelo holding the chat. The other
reports of a failed chat save were no better: the completion's warning said nothing of why, a bind's said a Python
type name, a restore's and a mode change's gave the operating system's own text, which holds the person's folder.

Pinned:

* every cause has a sentence that names it and one that says what to do, and none carries the error's text or a
  path: another Angelo process (by its number when the lease file gives it), another program's write, a full
  disk, no permission, a read-only disk, a missing folder, a file another program holds, too many open files, a
  disk error, a value the file cannot hold, and anything else by its kind. Windows' own codes are read before
  the errno they are mapped to (a sharing violation is not "no permission");
* every kind the module knows is reached here, so a cause added without its test fails;
* each place that reports a failed chat save states that cause: admission's refusal, a completed call's warning,
  a bind's warning, a restore's stored diagnostic and a memory update's refusal;
* what a person reads says it in their language: the bind's row, the public notes' warning, the mode
  preference's row and the restore gate's notice carry the cause as a sentence translated whole (never the
  operating system's text, which holds the settings folder's path), and every sentence is in the template and
  in every catalogue with its fields.

``test_native_two_processes_one_chat`` pins the other process's number with a real second process.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture  # noqa: E402
from angelo_agent import (checkpoint_restore_ui, document_identity, document_work, save_failure,  # noqa: E402
                          tool_finalization)
from angelo_agent.session_journal import JournalTaken  # noqa: E402
from angelo_agent.session_lease import JournalLeased  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

PERSONAL = "/Users/someone/Library/Application Support/Angelo/sessions/chat.json"
REFUSAL = "invocation journal could not be saved"
CUBE = {"name": "Cube", "size": 1, "location": [0, 0, 0]}


def system(number, *, windows=None):
    """An error as the operating system raises it, with the person's path in it; ``windows``: its Windows code,
    which Python gives only there (a stand-in attribute here, so every platform's test reads the same table)."""
    kind = type("WindowsStandIn", (OSError,), {"winerror": windows}) if windows is not None else OSError
    return kind(number, "the system's own words", PERSONAL)


def leased(holder=None):
    error = JournalLeased("Another Angelo has this chat open: " + PERSONAL)
    if holder is not None:
        error.holder = holder
    return error


# label: (error, kind, words of the cause, words of what to do)
CASES = {
    "disk full": (system(errno.ENOSPC), "disk_full", "is full", "Free some space"),
    "no permission": (system(errno.EACCES), "no_permission", "isn't allowed to write", "write access"),
    "operation not permitted": (system(errno.EPERM), "no_permission", "isn't allowed to write", "write access"),
    "read-only disk": (system(errno.EROFS), "read_only", "read-only", "writable"),
    "folder missing": (system(errno.ENOENT), "missing", "is missing", "Restart Angelo"),
    "folder is a file": (system(errno.ENOTDIR), "missing", "is missing", "Restart Angelo"),
    "file busy": (system(errno.EBUSY), "in_use", "holding one of Angelo's files open", "in a moment"),
    "too many files, process": (system(errno.EMFILE), "too_many_files", "too many files open", "Restart Angelo"),
    "too many files, system": (system(errno.ENFILE), "too_many_files", "too many files open", "Restart Angelo"),
    "disk error": (system(errno.EIO), "disk_error", "disk reported an error", "Check the disk"),
    "network folder gone": (system(errno.ESTALE), "disk_error", "disk reported an error", "Check the disk"),
    "windows sharing violation": (system(errno.EACCES, windows=32), "in_use", "holding one of Angelo's files open",
                                  "in a moment"),
    "windows lock violation": (system(errno.EACCES, windows=33), "in_use", "holding one of Angelo's files open",
                               "in a moment"),
    "windows access denied": (system(errno.EACCES, windows=5), "no_permission", "isn't allowed to write",
                              "write access"),
    "windows write protected": (system(errno.EACCES, windows=19), "read_only", "read-only", "writable"),
    "windows disk full": (system(errno.ENOSPC, windows=112), "disk_full", "is full", "Free some space"),
    "windows path not found": (system(errno.ENOENT, windows=3), "missing", "is missing", "Restart Angelo"),
    "another angelo, numbered": (leased(4321), "other_process", "process (number 4321) has this chat open",
                                 "quit it and open the chat here again"),
    "another angelo": (leased(), "other_process", "Another Angelo process has this chat open",
                       "quit it and open the chat here again"),
    "another writer": (JournalTaken("Another writer changed this chat's file: " + PERSONAL), "other_writer",
                       "changed this chat's file", "Open the chat again"),
    "not a number": (ValueError("Out of range float values are not JSON compliant: " + PERSONAL), "unsaveable",
                     "can't be written to its file (ValueError)", "start a new chat"),
    "not a json type": (TypeError("Object of type Handle is not JSON serializable: " + PERSONAL), "unsaveable",
                        "can't be written to its file (TypeError)", "start a new chat"),
    "too deep": (RecursionError("maximum recursion depth exceeded: " + PERSONAL), "unsaveable",
                 "can't be written to its file (RecursionError)", "start a new chat"),
    "too large": (OverflowError("int too large: " + PERSONAL), "unsaveable",
                  "can't be written to its file (OverflowError)", "start a new chat"),
    "not text": (UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"), "unsaveable",
                 "can't be written to its file (UnicodeEncodeError)", "start a new chat"),
    "another system error": (system(errno.EXDEV), "other", "couldn't write one of its own files (OSError EXDEV)",
                             "restart Angelo"),
    "anything else": (RuntimeError("the store broke: " + PERSONAL), "other",
                      "couldn't write one of its own files (RuntimeError)", "restart Angelo"),
}


class EveryCause(unittest.TestCase):
    def test_each_cause_is_named_with_what_to_do_and_nothing_of_the_person(self):
        for label, (error, kind, cause, to_do) in CASES.items():
            with self.subTest(label=label):
                self.assertEqual(save_failure.kind(error), kind)
                said = save_failure.cause(error)
                self.assertIn(cause, said)
                self.assertIn(to_do, said)
                self.assertLess(said.index(cause), said.index(to_do), "the cause first, then what to do")
                self.assertTrue(said.endswith("."), said)
                for private in ("someone", "Library", "chat.json", "the system's own words"):
                    self.assertNotIn(private, said)

    def test_every_kind_the_module_knows_is_one_of_those_cases(self):
        reached = {kind for _error, kind, _cause, _to_do in CASES.values()}
        self.assertEqual(reached, set(save_failure.SENTENCES))
        said = {save_failure.cause(error).template for error, _kind, _cause, _to_do in CASES.values()}
        self.assertEqual(said, set(save_failure.SENTENCES.values()) | {save_failure.OTHER_PROCESS_NUMBERED})

    def test_a_number_that_is_not_a_process_number_is_left_out(self):
        for holder in (0, -1, "4321", None, True, 2 ** 40):
            with self.subTest(holder=holder):
                error = leased()
                error.holder = holder
                self.assertNotIn("number", save_failure.cause(error))

    def test_the_sentence_keeps_its_template_for_the_persons_language(self):
        said = save_failure.cause(leased(77))
        self.assertEqual((said.template, said.fields), (save_failure.OTHER_PROCESS_NUMBERED, {"pid": 77}))
        self.assertEqual(save_failure.cause(system(errno.ENOSPC)).fields, {})

    def test_the_kind_label_is_the_type_and_the_errno_name_never_the_text(self):
        self.assertEqual(save_failure.label(system(errno.ENOSPC)), "OSError ENOSPC")
        self.assertEqual(save_failure.label(PermissionError(errno.EACCES, "no", PERSONAL)), "PermissionError EACCES")
        self.assertEqual(save_failure.label(leased(9)), "JournalLeased")
        self.assertEqual(save_failure.label(ValueError(PERSONAL)), "ValueError")


class EveryReport(AgentRuntimeFixture, unittest.TestCase):
    """Each place a failed chat save is reported, with a full disk, no permission and another Angelo."""

    FAILURES = (("disk_full", lambda: system(errno.ENOSPC)), ("no_permission", lambda: system(errno.EACCES)),
                ("other_process", lambda: leased(4321)), ("unsaveable", lambda: TypeError("not JSON: " + PERSONAL)))

    def setUp(self):
        super().setUp()
        self.executor.execute = Mock(return_value={"success": True, "objects": [], "object_count": 0})

    def stated(self, text, error):
        self.assertIn(str(save_failure.cause(error)), text)
        self.assertNotIn("someone", text)
        self.assertNotIn("the system's own words", text)

    def test_admissions_refusal(self):
        for kind, make in self.FAILURES:
            with self.subTest(kind=kind):
                self.c, error, answers = self.new_controller(), make(), []
                with patch.object(SessionStore, "save", side_effect=error):
                    self.c.request_tool("refused", "angelo_create_cube", CUBE, answers.append)
                self.pump()
                self.assertEqual(len(answers), 1)
                self.assertIn(REFUSAL, answers[0]["error"])
                self.assertEqual(answers[0]["journal_cause"], kind)
                self.assertEqual(answers[0]["journal_error"], save_failure.label(error))
                self.stated(answers[0]["error"], error)
                self.assertNotIn("someone", json.dumps(answers[0]))

    def test_a_completed_calls_warning(self):
        for kind, make in self.FAILURES:
            with self.subTest(kind=kind):
                self.c, error = self.new_controller(), make()
                with patch.object(self.c, "save", side_effect=error):
                    self.assertFalse(tool_finalization.save_observed_state(self.c))
                self.assertIn("Do not repeat the operation", self.c.persistence_warning)
                self.stated(self.c.persistence_warning, error)
                self.assertTrue(tool_finalization.save_observed_state(self.c))
                self.assertEqual(self.c.persistence_warning, "")

    def test_the_warning_a_result_carries_is_the_one_just_recorded(self):
        self.c = self.new_controller()
        call = SimpleNamespace(id="call-1", document=self.c.document, invocation_digest="d", approved=False,
                               automatic=True, name="angelo_inspect_scene", arguments={})
        error = system(errno.ENOSPC)
        with self.c.lock, patch.object(self.c, "save", side_effect=error), \
                patch.object(self.c, "_record_memory_result"):
            result = tool_finalization.complete_result(self.c, call, {"success": True})
        self.assertFalse(result["journal_durable"])
        self.assertIn("Do not repeat the operation", result["journal_warning"])
        self.stated(result["journal_warning"], error)

    def test_a_binds_warning(self):
        for kind, make in self.FAILURES:
            with self.subTest(kind=kind):
                self.c, error = self.new_controller(), make()
                with patch.object(self.c, "save", side_effect=error):
                    self.assertFalse(document_work.save_quietly(self.c))
                self.stated(self.c.persistence_warning, error)

    def test_a_restores_stored_diagnostic(self):
        for kind, make in self.FAILURES:
            with self.subTest(kind=kind):
                self.c, error = self.new_controller(), make()
                with patch.object(self.c, "save", side_effect=error):
                    failure = checkpoint_restore_ui._save(self.c)
                self.stated(failure, error)
        self.assertIsNone(checkpoint_restore_ui._save(self.c))

    def test_a_memory_updates_refusal(self):
        text = (Path(checkpoint_restore_ui.__file__).parent / "memory_host.py").read_text(encoding="utf-8")
        self.assertIn('"Task memory could not be saved; no memory update was retained. " + save_failure.cause(error)',
                      text)
        self.assertNotIn('raise ValueError("Task memory could not be saved; no memory update was retained") from None',
                         text)


class WhatAPersonReads(AgentRuntimeFixture, unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]
    ADDON = ROOT / "scripts/addons_core/angelo_agent"

    def test_a_binds_row_says_why_in_a_sentence_translated_whole(self):
        for error in (system(errno.ENOSPC), leased(4321)):
            with self.subTest(kind=save_failure.kind(error)):
                self.c = self.new_controller()
                document_work.save_failed(self.c, error)
                document_work.save_failed(self.c, error)   # Said once.
                self.c.pump_events()
                rows = [row for row in self.c.state["messages"]
                        if (row.get("i18n") or [None])[0] == document_identity.SAVE_FAILED]
                self.assertEqual(len(rows), 1)
                why = save_failure.cause(error)
                self.assertEqual(rows[0]["i18n"][1]["why"], [why.template, dict(why.fields)])
                self.assertIn(str(why), rows[0]["text"])
                self.assertIn("may be lost if Angelo quits", rows[0]["text"])
                self.assertNotIn("someone", json.dumps(rows[0]))

    def test_the_other_rows_carry_the_cause_and_never_the_systems_text(self):
        director = (self.ADDON / "director.py").read_text(encoding="utf-8")
        self.assertIn('notice("Mode changed, but the preference could not be saved: {error}", '
                      'error=save_failure.cause(error))', director)
        self.assertNotIn("could not be saved: {error}\", error=str(error)", director)
        notes = (self.ADDON / "project_brief_rebind_ui.py").read_text(encoding="utf-8")
        self.assertIn("but could not be saved. {error} '", notes)
        self.assertIn("error=shown(save_failure.cause(error))", notes)
        self.assertNotIn("could not be saved: {error}", notes)
        gate = (self.ADDON / "checkpoint_restore_ui.py").read_text(encoding="utf-8")
        self.assertIn("safety link could not be saved. {why} The protected safety snapshot", gate)
        self.assertIn("why=save_failure.cause(error)", gate)

    def test_every_sentence_is_in_the_template_and_every_catalogue_with_its_fields(self):
        sys.path.insert(0, str(self.ROOT / "scripts/angelo"))
        self.addCleanup(sys.path.remove, str(self.ROOT / "scripts/angelo"))
        from i18n import catalogs, languages, po, validate
        sentences = set(save_failure.SENTENCES.values()) | {save_failure.OTHER_PROCESS_NUMBERED,
                                                            document_identity.SAVE_FAILED}
        _header, template = po.read(str(self.ROOT / "locale/angelo/angelo.pot"))
        self.assertLessEqual(sentences, {entry.id for entry in template if not entry.obsolete})
        self.assertEqual(len(languages.LANGUAGES), 20)
        for language in languages.LANGUAGES:
            _header, entries = po.read(str(catalogs.catalog_path(language)))
            translated = {entry.id: entry.str for entry in entries if not entry.obsolete}
            for sentence in sorted(sentences):
                with self.subTest(language=language.code, sentence=sentence[:40]):
                    self.assertTrue(translated.get(sentence, "").strip())
                    self.assertEqual(validate.placeholders(translated[sentence]), validate.placeholders(sentence))
                    if "Angelo" in sentence:
                        self.assertIn("Angelo", translated[sentence])


if __name__ == "__main__":
    unittest.main()
