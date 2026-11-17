# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One value the chat's file cannot hold never stops the chat: later work is admitted without a relaunch.

Every tool call is journaled before it is admitted (``tool_finalization.journal_tool``), and the journal's encoder
refuses a number that is not finite and text that is not valid UTF-8 (``session_journal.ENCODER``,
``allow_nan=False``, then ``encode("utf-8")``). A result or a row holding one such value stays in the chat's state,
so every later save fails on it, and admission answers "The invocation journal could not be saved; no native work
was admitted" for every call until Angelo is started again and reads the file as last saved.

Evidence: the showcase pilot (build 06259fa5bb0, Linux, 2026-11-14): after a render job failed with a CUDA error
the chat refused all further work with that sentence until a relaunch, while the connected app reconnected by
itself. Which value the failed job left is not established here; the class is any such value from any tool: a
tool result can carry a measured number (a division by a zero duration, an unknown remaining time as infinity,
NaN from a failed render's statistics) or text from outside (a file name Linux allows that is not UTF-8, which
Python reads as lone surrogates).

Pinned, through the controller's own admission, dispatch and finalization with a stand-in executor: after a read
whose result holds such a value (each kind, nested as results nest), after a chat row that does and after a call
whose ID does, the next read and the next edit are admitted and answered, and the chat's file loads again with
their results. A save the disk refuses is still a refusal of that call, and the call after it is admitted once the
disk takes the write.

Recovery keeps the chat's other guarantees, checked together on one chat (the maintainer's external audit,
2026-11-14): an edit made before the value arrived and one made after are each applied once; asked again with
the same arguments they are replayed, with other arguments refused; after the chat is closed and read again as a
relaunch reads it (``SessionStore.load``: the ledger open, every row and every call's result record kept, no
unverified-history notice) the same holds, and a new edit is admitted and saved.

The store itself (``SessionStore.save`` and ``compact``, whoever calls them): a state holding such a value in a
field, a row or a result is saved, with that value made saveable (a surrogate pair held as two code units becomes
its character, a lone surrogate U+FFFD, a number that is not finite null, a reference to a container that holds it
null) and everything else as it was; the state in memory is the state the file holds; a state with nothing of the
kind is appended as before, never rewritten. The ledger refuses such a key when an ID is reserved
(``invocations._key``) and the task memory's own whitelist drops such text (``memory_spec.text``), so neither is a
case here. And a refusal names its cause's kind (never its text, which can hold a path): the pilot's report could
not say which failure it had met.

The class is every value the encoder refuses, whichever error it raises for it (the independent review of
40d87c53184, 2026-11-15, probed an integer past Python's digit limit and a number that is not finite used as a
key, both ``ValueError``, and named the ``TypeError`` values): bytes, a set, a number or an array of a library's
own kind, a path, a mapping that is not a dict, any other object, a key that is none of text, a number, a boolean
or None, and containers nested past the encoder's stack (``RecursionError``). Each is saved in its plain form
(``FOREIGN``; an object with none as null), one test per kind at the store and a whole session driven through each
(``test_a_session_goes_on_through_every_kind_of_value``): an edit before it, the value arriving in a row a part of
Angelo wrote from Python values, a read and an edit after it, each edit applied once and replayed when asked
again, the chat closed and read again with a verified history, and a new edit there. Admission never raises for a
save's failure, whatever its type: the call is refused with the failure's kind and the next is admitted. What a
save changed is recorded by kind and count, never by value (``SessionStore.made_saveable`` and one printed
line). Only what the file holds of the state is changed: a runtime value under another key is the same object
afterwards.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import decimal
import errno
import fractions
import io
import json
import math
import numbers
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from agent_runtime_fixtures import AgentRuntimeFixture
from angelo_agent import session_journal, session_saveable
from angelo_agent.controller import Controller
from angelo_agent.session_journal import JournalTaken
from angelo_agent.sessions import HISTORY_WARNING, SessionStore, read_transcript

CUBE = {"name": "Cube", "size": 1, "location": [0, 0, 0]}
REFUSAL = "invocation journal could not be saved"
NOT_UTF8 = "render-\udcff\udcfe.png"   # A Linux file name with two bytes that are not UTF-8, as Python reads it.
# What a tool's result can hold that the file cannot, where results hold values: top level, nested, in a list.
UNSAVEABLE = {
    "not a number": {"success": True, "objects": [], "statistics": {"seconds_per_sample": float("nan")}},
    "infinity": {"success": True, "objects": [], "seconds_left": float("inf")},
    "negative infinity": {"success": True, "objects": [{"name": "Cube", "bounds": [0.0, float("-inf"), 1.0]}]},
    "text that is not UTF-8": {"success": True, "objects": [], "files": [{"name": NOT_UTF8}]},
    "a key that is not UTF-8": {"success": True, "objects": [], "outputs": {NOT_UTF8: 1}},
}


class Measured:
    """A number of a library's own kind (numpy's scalars are such): a number by registration, not Python's int."""

    def __init__(self, value):
        self.value = value

    def __int__(self):
        return self.value

    __index__ = __int__


numbers.Integral.register(Measured)


class Samples:
    """An array of a library's own kind: it converts itself (numpy's ``tolist``)."""

    def __init__(self, *values):
        self.values = values

    def tolist(self):
        return list(self.values)


class Vector:
    """Sized and iterable, and not a list (Blender's vectors, colours and property arrays are such)."""

    def __init__(self, *values):
        self.values = values

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        return iter(self.values)


class Handle:
    """Any other object: no plain value stands for it."""


BIG = 10 ** 5000   # Past the 4300 digits Python writes an integer in: the encoder raises ValueError for it.
RENDERS = Path("renders") / "frame.png"
# Every other kind of value the encoder refuses: name -> (one of it, what the file then holds in its place).
FOREIGN = {
    "bytes": (lambda: b"render-\xff\xfe.png", "render-\ufffd\ufffd.png"),
    "a bytearray": (lambda: bytearray(b"frame"), "frame"),
    "a set": (lambda: {3, 1, 2}, [1, 2, 3]),
    "a frozen set of text": (lambda: frozenset({"b", "a"}), ["a", "b"]),
    "a set whose values have no order": (lambda: {1, "a"}, [1, "a"]),
    "an integer past the digit limit": (lambda: BIG, hex(BIG)),
    "a negative integer past it": (lambda: -BIG, hex(-BIG)),
    "a path": (lambda: RENDERS, str(RENDERS)),
    "a fraction": (lambda: fractions.Fraction(1, 2), 0.5),
    "a decimal": (lambda: decimal.Decimal("1.5"), 1.5),
    "a library's integer": (lambda: Measured(7), 7),
    "an array that converts itself": (lambda: Samples(1.0, float("nan"), b"x"), [1.0, None, "x"]),
    "a vector": (lambda: Vector(0.0, 1.0, Measured(2)), [0.0, 1.0, 2]),
    "a mapping that is not a dict": (lambda: types.MappingProxyType({"a": {1}}), {"a": [1]}),
    "a complex number": (lambda: 1j, None),
    "any other object": (lambda: Handle(), None),
    "a tuple of them": (lambda: (b"a", {1}, Handle()), ["a", [1], None]),
    "keys that are not finite numbers": (lambda: {float("nan"): 1, float("inf"): 2, float("-inf"): 3},
                                         {"nan": 1, "inf": 2, "-inf": 3}),
    "an integer key past the digit limit": (lambda: {BIG: 1}, {hex(BIG): 1}),
    "a bytes key": (lambda: {b"name": 1}, {"name": 1}),
    "tuple keys": (lambda: {(1, 2): "cell", (1, (2, 3)): "nested"}, {"[1, 2]": "cell", "[1, [2, 3]]": "nested"}),
    "a library's integer as a key": (lambda: {Measured(7): "seven"}, {"7": "seven"}),
    "an object key": (lambda: {Handle(): 1}, {"<Handle>": 1}),
}


def nested(levels):
    """Lists nested ``levels`` deep, built without recursion."""
    value = []
    for _ in range(levels):
        value = [value]
    return value


def depth_of(value):
    """``(how many lists deep value goes down its first items, what is at the bottom)``."""
    depth = 0
    while isinstance(value, list) and value:
        depth, value = depth + 1, value[0]
    return depth, value


def reference(detail):
    """A row's reference record as a load keeps it (``session_validation.messages``), with one more fact."""
    return {"path": "/references/board.png", "name": "board.png", "kind": "image", "detail": detail}


class UnsaveableValues(AgentRuntimeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.next_result = None
        self.creates = 0
        self.executor.execute = Mock(side_effect=self.execute)

    def execute(self, name, arguments):
        if name == "angelo_create_cube":
            self.creates += 1
            return {"success": True, "verified": {"object_count": self.creates}, "changes": {
                "added": [{"name": arguments["name"], "type": "MESH"}], "removed": [], "modified": []}}
        result, self.next_result = self.next_result, None
        return result or {"success": True, "objects": [], "object_count": self.creates}

    def invoke(self, identity, name="angelo_inspect_scene", arguments=None, c=None):
        c, replies = c or self.c, []
        c.request_tool(identity, name, {} if arguments is None else arguments, replies.append)
        self.pump(c)
        self.assertEqual(len(replies), 1, identity)
        return replies[0]

    def reopened(self):
        """The chat closed and read again, as a relaunch reads it."""
        self.c.close()
        resumed = Controller(self.directory.name, session_id=self.c.state["id"])
        self.controllers.append(resumed)
        resumed.document, resumed.busy = self.c.document, True
        return resumed

    def later_work_is_admitted(self):
        read = self.invoke("read-after")
        self.assertNotIn(REFUSAL, str(read.get("error", "")))
        self.assertTrue(read.get("success"), read)
        edit = self.invoke("edit-after", "angelo_create_cube", CUBE)
        self.assertNotIn(REFUSAL, str(edit.get("error", "")))
        self.assertTrue(edit.get("success"), edit)
        self.assertEqual(self.creates, 1)
        # The file as a relaunch would read it: it loads, and holds the later calls' results.
        reloaded = Controller(self.directory.name, session_id=self.c.state["id"])
        for identity in ("read-after", "edit-after"):
            self.assertIn(self.c.state["id"] + "/" + identity, reloaded.state["results"], identity)

    def test_a_result_the_file_cannot_hold_does_not_stop_later_work(self):
        for kind, result in UNSAVEABLE.items():
            with self.subTest(kind=kind):
                self.c = self.new_controller()
                self.creates = 0
                self.next_result = result
                answered = self.invoke("read-unsaveable")
                self.assertIsInstance(answered, dict)   # The call itself is answered, whatever its record keeps.
                self.later_work_is_admitted()

    def test_a_chat_row_the_file_cannot_hold_does_not_stop_later_work(self):
        for text in ("The render wrote " + NOT_UTF8, "Seconds left: \ud800"):
            with self.subTest(text=text):
                self.c = self.new_controller()
                self.creates = 0
                self.c.emit("message", {"role": "error", "text": text})
                self.c.pump_events()
                self.later_work_is_admitted()

    def test_a_call_whose_id_the_file_cannot_hold_does_not_stop_later_work(self):
        """A connected app or a model names its calls; JSON lets an ID hold half of a character."""
        for identity in ("read-\udc80", "read-\ud83d-half"):
            with self.subTest(identity=identity):
                self.c = self.new_controller()
                self.creates = 0
                self.assertIsInstance(self.invoke(identity), dict)
                self.later_work_is_admitted()
        # An edit's ID is a permanent mutation ID: one the ledger cannot hold is refused, that call only.
        self.c = self.new_controller()
        self.creates = 0
        refused = self.invoke("edit-\udc80", "angelo_create_cube", CUBE)
        self.assertTrue(refused.get("error"), refused)
        self.assertNotIn(REFUSAL, refused["error"])
        self.assertEqual(self.creates, 0)
        self.later_work_is_admitted()

    def test_recovery_duplicate_protection_later_work_and_reload_hold_together(self):
        second = dict(CUBE, name="Second cube")
        arrivals = {
            "a result": lambda: (setattr(self, "next_result", UNSAVEABLE["text that is not UTF-8"]),
                                 self.invoke("read-unsaveable")),
            "a result's key": lambda: (setattr(self, "next_result", UNSAVEABLE["a key that is not UTF-8"]),
                                       self.invoke("read-unsaveable")),
            "a chat row": lambda: (self.c.emit("message", {"role": "error", "text": "The render wrote " + NOT_UTF8}),
                                   self.c.pump_events()),
        }
        for kind, arrive in arrivals.items():
            with self.subTest(kind=kind):
                self.c = self.new_controller()
                self.creates = 0
                key = self.c.state["id"] + "/"
                # An edit before the value arrives, the value, then a read and an edit after it.
                self.assertTrue(self.invoke("edit-before", "angelo_create_cube", CUBE).get("success"))
                arrive()
                self.assertTrue(self.invoke("read-after").get("success"))
                self.assertTrue(self.invoke("edit-after", "angelo_create_cube", second).get("success"))
                self.assertEqual(self.creates, 2)
                self.assertTrue(encodes({part: self.c.state[part] for part in ("messages", "results", "invocations")}))
                # Duplicate protection in the same chat: each edit was applied once.
                for identity, arguments in (("edit-before", CUBE), ("edit-after", second)):
                    self.assertTrue(self.invoke(identity, "angelo_create_cube", arguments).get("replayed"), identity)
                    changed = self.invoke(identity, "angelo_create_cube", dict(arguments, name="Another cube"))
                    self.assertIn("different", changed.get("error", ""), identity)
                self.assertEqual(self.creates, 2)
                # Read again as a relaunch reads it: verified history, an open ledger, every call's record.
                resumed = self.reopened()
                self.assertFalse(resumed.state["invocations"]["closed"])
                texts = [str(row.get("text", "")) for row in resumed.state["messages"]]
                self.assertFalse(any(HISTORY_WARNING in text for text in texts), "the history was not verified")
                if kind == "a chat row":
                    self.assertIn("The render wrote render-\ufffd\ufffd.png", texts)
                self.assertEqual(set(resumed.state["invocations"]["entries"]),
                                 {key + "edit-before", key + "edit-after"})
                for identity in ("edit-before", "read-after", "edit-after"):
                    self.assertIn(key + identity, resumed.state["results"], identity)
                if kind != "a chat row":
                    kept = json.dumps(resumed.state["results"][key + "read-unsaveable"]["result"], ensure_ascii=False)
                    self.assertIn("render-\ufffd\ufffd.png", kept)
                # And the guarantees hold there too.
                for identity, arguments in (("edit-before", CUBE), ("edit-after", second)):
                    self.assertTrue(self.invoke(identity, "angelo_create_cube", arguments, c=resumed).get("replayed"))
                    changed = self.invoke(identity, "angelo_create_cube", dict(arguments, name="Another"), c=resumed)
                    self.assertIn("different", changed.get("error", ""), identity)
                self.assertEqual(self.creates, 2)
                third = self.invoke("edit-reopened", "angelo_create_cube", dict(CUBE, name="Third cube"), c=resumed)
                self.assertTrue(third.get("success"), third)
                self.assertEqual(self.creates, 3)
                self.assertTrue(self.invoke("read-reopened", c=resumed).get("success"))
                saved = read_transcript(resumed.store.path)
                self.assertIn(key + "edit-reopened", saved["results"])
                self.assertIn(key + "edit-reopened", saved["invocations"]["entries"])

    def arrive_in_a_row(self, value):
        """A row a part of Angelo wrote from Python values: its reference records reach the state as they are."""
        self.c.emit("message", {"role": "error", "text": "The reference could not be read",
                                "references": [reference(value)]})
        self.c.pump_events()

    def test_a_session_goes_on_through_every_kind_of_value(self):
        """Recovery, duplicate protection, later work and the reload together, for every kind the encoder refuses."""
        second = dict(CUBE, name="Second cube")
        kinds = dict(FOREIGN, **{"lists nested past the encoder's stack": (lambda: nested(1_000_000), None)})
        for kind, (make, held) in kinds.items():
            with self.subTest(kind=kind):
                self.c = self.new_controller()
                self.creates = 0
                key = self.c.state["id"] + "/"
                self.assertTrue(self.invoke("edit-before", "angelo_create_cube", CUBE).get("success"))
                self.arrive_in_a_row(make())
                self.assertTrue(self.invoke("read-after").get("success"))
                self.assertTrue(self.invoke("edit-after", "angelo_create_cube", second).get("success"))
                self.assertEqual(self.creates, 2)
                self.assertTrue(encodes({part: self.c.state[part] for part in ("messages", "results", "invocations")}))
                self.assertTrue(self.c.store.made_saveable, "what was changed was not recorded")
                for identity, arguments in (("edit-before", CUBE), ("edit-after", second)):
                    self.assertTrue(self.invoke(identity, "angelo_create_cube", arguments).get("replayed"), identity)
                    changed = self.invoke(identity, "angelo_create_cube", dict(arguments, name="Another cube"))
                    self.assertIn("different", changed.get("error", ""), identity)
                self.assertEqual(self.creates, 2)
                # Read again as a relaunch reads it: verified history, an open ledger, the row in its plain form.
                resumed = self.reopened()
                self.assertFalse(resumed.state["invocations"]["closed"])
                texts = [str(row.get("text", "")) for row in resumed.state["messages"]]
                self.assertFalse(any(HISTORY_WARNING in text for text in texts), "the history was not verified")
                rows = [row for row in resumed.state["messages"] if row.get("references")]
                self.assertEqual(len(rows), 1)
                if held is None and kind.startswith("lists"):
                    depth, bottom = depth_of(rows[0]["references"][0]["detail"])
                    self.assertIsNone(bottom)
                    self.assertTrue(100 < depth <= session_saveable.DEEPEST, depth)
                else:
                    self.assertEqual(rows[0]["references"], [reference(held)])
                self.assertEqual(set(resumed.state["invocations"]["entries"]),
                                 {key + "edit-before", key + "edit-after"})
                for identity in ("edit-before", "read-after", "edit-after"):
                    self.assertIn(key + identity, resumed.state["results"], identity)
                for identity, arguments in (("edit-before", CUBE), ("edit-after", second)):
                    self.assertTrue(self.invoke(identity, "angelo_create_cube", arguments, c=resumed).get("replayed"))
                self.assertEqual(self.creates, 2)
                third = self.invoke("edit-reopened", "angelo_create_cube", dict(CUBE, name="Third cube"), c=resumed)
                self.assertTrue(third.get("success"), third)
                self.assertEqual(self.creates, 3)
                saved = read_transcript(resumed.store.path)
                self.assertIn(key + "edit-reopened", saved["results"])
                self.assertIn(key + "edit-reopened", saved["invocations"]["entries"])

    def test_a_result_json_has_no_form_for_is_one_answer_and_later_work_goes_on(self):
        """A tool's own result is checked before it reaches the state (``Controller.finish_tool``): the call is
        answered, with the value or with the failure to hold it, and the chat goes on either way."""
        for kind, (make, _) in FOREIGN.items():
            with self.subTest(kind=kind):
                self.c = self.new_controller()
                self.creates = 0
                self.next_result = {"success": True, "objects": [], "measured": make()}
                self.assertIsInstance(self.invoke("read-unsaveable"), dict)
                self.later_work_is_admitted()

    def test_admission_never_raises_for_a_save_that_fails(self):
        """Whatever a save raises, the call it was for is refused with the failure's kind, and the next is admitted."""
        folder = "/Users/someone/Library/Application Support/Angelo"
        for error in (TypeError("Object of type Handle is not JSON serializable: " + folder),
                      RecursionError("maximum recursion depth exceeded: " + folder),
                      OverflowError("int too large: " + folder), RuntimeError("the store broke: " + folder),
                      KeyError(folder), AttributeError("no attribute: " + folder)):
            with self.subTest(error=type(error).__name__):
                self.c = self.new_controller()
                self.creates = 0
                answers = []
                with patch.object(SessionStore, "save", side_effect=error):   # Admission's save only: no pump inside.
                    self.c.request_tool("read-refused", "angelo_inspect_scene", {}, answers.append)
                    self.c.request_tool("edit-refused", "angelo_create_cube", CUBE, answers.append)
                self.pump()
                self.assertEqual(len(answers), 2)
                for answer in answers:
                    self.assertIn(REFUSAL, answer.get("error", ""))
                    self.assertEqual(answer.get("journal_error"), type(error).__name__)
                    self.assertNotIn("someone", json.dumps(answer))
                self.assertEqual(self.creates, 0)
                self.later_work_is_admitted()

    def test_a_save_the_disk_refuses_is_one_refusal_not_the_end_of_the_chat(self):
        with patch.object(SessionStore, "save", side_effect=OSError(28, "No space left on device")):
            refused = self.invoke("read-while-full")
        self.assertIn(REFUSAL, refused.get("error", ""))
        self.later_work_is_admitted()

    def test_a_refusal_names_the_kind_of_its_cause_and_never_its_text(self):
        folder = "/Users/someone/Library/Application Support/Angelo"
        causes = {"OSError ENOSPC": OSError(errno.ENOSPC, "No space left on device", folder + "/chat.json"),
                  "PermissionError EACCES": PermissionError(errno.EACCES, "Permission denied", folder + "/chat.json"),
                  "JournalTaken": JournalTaken("This chat's file is not the one its store last wrote: " + folder),
                  "ValueError": ValueError("Out of range float values are not JSON compliant: " + folder)}
        for kind, error in causes.items():
            with self.subTest(kind=kind):
                self.c = self.new_controller()
                with patch.object(SessionStore, "save", side_effect=error):
                    refused = self.invoke("read-refused")
                self.assertIn(REFUSAL, refused.get("error", ""))
                self.assertEqual(refused.get("journal_error"), kind)
                self.assertNotIn("someone", json.dumps(refused))


def encodes(value):
    """True when the chat's file can hold ``value``: finite JSON whose text is UTF-8."""
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        return False
    return True


def activity(result):
    """A tool's row as the controller keeps it (a load drops an activity row without these: session_validation)."""
    return {"role": "activity", "call_id": "call-1", "tool": "angelo_inspect_scene", "text": "", "arguments": {},
            "result": result, "status": "completed"}


def record(result):
    """A call's result record as the controller writes it (a load keeps one with a document and a result)."""
    return {"document": "('document', 1)", "invocation_digest": "%064x" % 1, "phase": "completed", "result": result,
            "approved": False, "automatic": True}


PAIR = "\ud83d\ude00"            # One character held as its two UTF-16 code units, as a JSON escape can leave it.
KEPT = "Ordinary text stays: caf\u00e9, \u65e5\u672c\u8a9e, \U0001f600, a question mark? and \ufffd itself."
# Where a chat's state holds values, each with one the file cannot hold: (how it is put in, what the file then holds).
PLACES = {
    "a field": (lambda state: state.update(model="model-" + NOT_UTF8),
                lambda saved: saved["model"] == "model-render-\ufffd\ufffd.png"),
    "a row's text": (lambda state: state["messages"].append({"role": "error", "text": "Half of one: \ud83d"}),
                     lambda saved: saved["messages"][-1]["text"] == "Half of one: \ufffd"),
    "both halves of one character": (
        lambda state: state["messages"].append({"role": "assistant", "text": "Done " + PAIR}),
        lambda saved: saved["messages"][-1]["text"] == "Done \U0001f600"),
    "a number in a row": (
        lambda state: state["messages"].append(activity({"seconds": float("nan"),
                                                         "bounds": [0.0, float("-inf"), (1.0, float("inf"))]})),
        lambda saved: saved["messages"][-1]["result"] == {"seconds": None, "bounds": [0.0, None, [1.0, None]]}),
    "a result's nested key and value": (
        lambda state: state["results"].update({state["id"] + "/call": record(
            {"outputs": {NOT_UTF8: 1, "plain": [NOT_UTF8]}})}),
        lambda saved: saved["results"][saved["id"] + "/call"]["result"]["outputs"]
        == {"render-\ufffd\ufffd.png": 1, "plain": ["render-\ufffd\ufffd.png"]}),
    "a result's own key": (
        lambda state: state["results"].update({state["id"] + "/call-\udc80": record({"success": True})}),
        lambda saved: list(saved["results"]) == [saved["id"] + "/kept", saved["id"] + "/call-\ufffd"]),
}


def contains_itself(state):
    row = activity({"objects": []})
    row["result"]["objects"].append(row["result"])
    state["messages"].append(row)


class TheStoreSavesWhatItCan(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)

    def chat(self):
        """A saved chat with one ordinary row and result, and its store."""
        store = SessionStore(self.directory, str(uuid.uuid4()))
        state = store.new("claude_subscription")
        state["messages"].append({"role": "user", "text": KEPT})
        state["results"][state["id"] + "/kept"] = record({"success": True, "note": KEPT, "size": 1.5, "count": 3})
        store.save(state)
        return store, state

    def reloaded(self, state):
        """The document the file holds, unvalidated."""
        return read_transcript(SessionStore(self.directory, state["id"]).path)

    def loaded(self, state):
        """The chat as a relaunch reads it."""
        return SessionStore(self.directory, state["id"]).load()

    def assert_saved(self, state, check):
        self.assertTrue(encodes(state), "the state in memory still holds what the file cannot")
        saved = self.reloaded(state)
        for part in ("messages", "results", "invocations"):
            self.assertEqual(saved[part], json.loads(json.dumps(state[part])), part)
        self.assertEqual(saved["model"], state["model"])
        self.assertTrue(check(saved), saved)
        # And a relaunch reads all of it: no row, result record or field fails the load's validation.
        loaded = self.loaded(state)
        for part in ("messages", "results", "invocations", "model"):
            self.assertEqual(loaded[part], saved[part], part)
        # What was saveable is exactly as it was.
        self.assertEqual(saved["messages"][0], {"role": "user", "text": KEPT})
        self.assertEqual(saved["results"][state["id"] + "/kept"]["result"],
                         {"success": True, "note": KEPT, "size": 1.5, "count": 3})

    def test_a_save_holds_everything_but_the_value_the_file_cannot(self):
        for place, (put, check) in PLACES.items():
            for write in ("save", "compact"):
                with self.subTest(place=place, write=write):
                    store, state = self.chat()
                    put(state)
                    self.assertFalse(encodes(state), "the case holds nothing the file cannot")
                    getattr(store, write)(state)
                    self.assert_saved(state, check)
                    # And the chat goes on: the next ordinary save appends to that file.
                    state["messages"].append({"role": "user", "text": "next"})
                    store.save(state)
                    self.assertEqual(store.last_write, "append")
                    self.assertEqual(self.reloaded(state)["messages"][-1], {"role": "user", "text": "next"})

    def test_a_value_that_holds_itself_is_saved_without_the_reference(self):
        store, state = self.chat()
        contains_itself(state)
        store.save(state)
        self.assertEqual(self.reloaded(state)["messages"][-1]["result"], {"objects": [None]})

    def test_rows_keep_their_identity(self):
        """The controller holds rows by index and by object: a row made saveable is the same object, in place."""
        store, state = self.chat()
        row = {"role": "error", "text": NOT_UTF8, "details": {"files": [NOT_UTF8]}}
        state["messages"].append(row)
        rows, details = state["messages"], row["details"]
        store.save(state)
        self.assertIs(state["messages"], rows)
        self.assertIs(state["messages"][-1], row)
        self.assertIs(row["details"], details)
        self.assertEqual(row["text"], "render-\ufffd\ufffd.png")

    def test_a_state_the_file_can_hold_is_appended_as_before(self):
        store, state = self.chat()
        with patch.object(SessionStore, "_rewrite", side_effect=AssertionError("an ordinary save rewrote the file")):
            for index in range(3):
                state["messages"].append({"role": "user", "text": KEPT + str(index)})
                store.save(state)
                self.assertEqual(store.last_write, "append")
        self.assertEqual(self.reloaded(state)["messages"][-1], {"role": "user", "text": KEPT + "2"})

    def test_an_error_that_is_not_a_value_of_the_state_is_raised_as_it_was(self):
        store, state = self.chat()
        state["messages"].append({"role": "user", "text": "next"})
        for error in (ValueError, TypeError, RecursionError, OverflowError):
            with self.subTest(error=error.__name__):
                with patch.object(SessionStore, "_fields", side_effect=error("not a value of the state")):
                    with self.assertRaisesRegex(error, "not a value of the state"):
                        store.save(state)
                self.assertFalse(getattr(store, "made_saveable", None))
        store.save(state)   # And nothing was changed or lost by the attempts.
        self.assertEqual(self.reloaded(state)["messages"][-1], {"role": "user", "text": "next"})

    def test_every_kind_of_value_the_file_cannot_hold_is_saved_in_its_plain_form(self):
        for kind, (make, held) in FOREIGN.items():
            for write in ("save", "compact"):
                with self.subTest(kind=kind, write=write):
                    store, state = self.chat()
                    state["messages"].append(activity({"value": make(), "note": KEPT}))
                    state["results"][state["id"] + "/call"] = record({"nested": [{"value": make()}]})
                    self.assertFalse(encodes(state), "the case holds nothing the file cannot")
                    getattr(store, write)(state)
                    self.assert_saved(state, lambda saved: (
                        saved["messages"][-1]["result"] == {"value": held, "note": KEPT}
                        and saved["results"][saved["id"] + "/call"]["result"] == {"nested": [{"value": held}]}))
                    self.assertEqual(sum(store.made_saveable.values()) % 2, 0, "the same value, twice")
                    state["messages"].append({"role": "user", "text": "next"})
                    store.save(state)
                    self.assertEqual(store.last_write, "append")

    def test_a_number_of_numpys_is_saved_as_pythons(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy is not installed here (Blender's Python has it)")
        values = {"an integer": (numpy.int64(3), 3), "a float": (numpy.float32(0.5), 0.5),
                  "a boolean": (numpy.bool_(True), True), "not a number": (numpy.float64("nan"), None),
                  "a small float not a number": (numpy.float32("nan"), None),
                  "an array": (numpy.array([[1, 2], [3, 4]]), [[1, 2], [3, 4]]),
                  "an array of floats": (numpy.array([0.5, float("inf")]), [0.5, None]),
                  "text": (numpy.str_("name"), "name"), "an integer key": ({numpy.int64(3): "three"}, {"3": "three"})}
        for kind, (value, held) in values.items():
            with self.subTest(kind=kind):
                store, state = self.chat()
                state["messages"].append(activity({"value": value, "note": KEPT}))
                store.save(state)
                self.assert_saved(state, lambda saved: saved["messages"][-1]["result"] == {"value": held, "note": KEPT})

    def test_a_value_nested_past_the_encoders_stack_is_saved_to_a_bounded_depth(self):
        store, state = self.chat()
        state["messages"].append(activity({"value": nested(1_000_000), "note": KEPT}))
        store.save(state)
        kept = self.reloaded(state)["messages"][-1]["result"]
        self.assertEqual(kept["note"], KEPT)
        depth, bottom = depth_of(kept["value"])
        self.assertIsNone(bottom)
        self.assertTrue(100 < depth <= session_saveable.DEEPEST, depth)
        self.assertEqual(store.made_saveable, {"depth": 1})
        # A value the encoder holds is never cut, however deep, when another value is made saveable beside it.
        store, state = self.chat()
        state["messages"].append(activity({"value": nested(session_saveable.DEEPEST + 50), "left": float("nan")}))
        store.save(state)
        kept = self.reloaded(state)["messages"][-1]["result"]
        self.assertEqual(depth_of(kept["value"]), (session_saveable.DEEPEST + 50, []))
        self.assertIsNone(kept["left"])
        self.assertEqual(store.made_saveable, {"nonfinite": 1})

    def test_what_a_save_changed_is_recorded_by_kind_and_count_and_never_by_value(self):
        store, state = self.chat()
        self.assertFalse(getattr(store, "made_saveable", None))
        state["messages"].append(activity({"file": b"secret-frame.png", "left": float("inf"), "handle": Handle(),
                                           "name": NOT_UTF8, "sizes": {1, 2}, "id": BIG, b"secret-key": 1,
                                           "self": None}))
        state["messages"][-1]["result"]["self"] = state["messages"][-1]["result"]
        kinds = {"converted:bytes": 1, "nonfinite": 1, "dropped:Handle": 1, "text": 1, "converted:set": 1,
                 "integer": 1, "key": 1, "cycle": 1}
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            store.save(state)
        self.assertEqual(store.made_saveable, kinds)
        line = printed.getvalue()
        self.assertIn("8 value(s)", line)
        for kind in kinds:
            self.assertIn(kind, line)
        for value in ("secret", "render", "frame", hex(BIG)[:12]):
            self.assertNotIn(value, line)
        # A second save that changes a value adds to the record; an ordinary one adds and prints nothing.
        state["messages"].append(activity({"left": float("nan")}))
        with contextlib.redirect_stdout(io.StringIO()):
            store.save(state)
        self.assertEqual(store.made_saveable, dict(kinds, nonfinite=2))
        state["messages"].append({"role": "user", "text": "next"})
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            store.save(state)
        self.assertNotIn("made saveable", printed.getvalue())
        self.assertEqual(store.made_saveable, dict(kinds, nonfinite=2))
        self.assertEqual(session_saveable.make_saveable(state), {})

    def test_only_what_the_file_holds_of_the_state_is_changed(self):
        """A chat's state can hold runtime values under keys no save writes (``SessionStore._fields`` is a
        whitelist): making the saved part saveable never touches them."""
        store, state = self.chat()
        written = {key for key, _ in store._fields(state)} | session_journal.STRUCTURED
        self.assertLessEqual(written, set(session_saveable.SAVED), "a saved field make_saveable does not clean")
        self.assertLessEqual(set(session_saveable.SAVED) - written, {"document_file", "approval_version"})
        runtime = state["runtime"] = {"handle": Handle(), "sizes": {1, 2}, "left": float("nan")}
        handle, sizes = runtime["handle"], runtime["sizes"]
        state["messages"].append(activity({"handle": Handle()}))
        store.save(state)
        self.assertIs(state["runtime"], runtime)
        self.assertIs(runtime["handle"], handle)
        self.assertIs(runtime["sizes"], sizes)
        self.assertTrue(math.isnan(runtime["left"]))
        saved = self.reloaded(state)
        self.assertNotIn("runtime", saved)
        self.assertEqual(saved["messages"][-1]["result"], {"handle": None})
        self.assertEqual(store.made_saveable, {"dropped:Handle": 1})

    def test_numbers_that_are_finite_are_never_changed(self):
        store, state = self.chat()
        values = [0.0, -0.0, 1e308, -1e-308, 5e-324, 2 ** 70, -1, True, False, None, math.pi, 10 ** 4000, -(10 ** 4000)]
        state["messages"].append(activity({"name": NOT_UTF8, "values": list(values)}))
        store.save(state)
        self.assertEqual(self.reloaded(state)["messages"][-1]["result"],
                         {"name": "render-\ufffd\ufffd.png", "values": json.loads(json.dumps(values))})


if __name__ == "__main__":
    unittest.main()
