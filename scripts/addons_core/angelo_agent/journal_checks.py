# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The conversation journal's test-time checks: where saved state differs, and rows changed without a touch.

Neither runs in the product. ``SessionStore._verified`` (``ANGELO_JOURNAL_VERIFY``) reads a save's file back and
names the first difference from memory; ``rows_touched`` (``session_writer.CHECK_TOUCHES``,
``ANGELO_JOURNAL_TOUCHES``) proves the writer's contract on every save: a row the file already holds changed its
bytes only after a ``touch``. A pump's owed save encodes only rows new or touched (``session_writer``), so a change
in place nobody marked would otherwise wait for the scrub. ``enabled`` reads each switch: run_host_tests.py and
run_native_suites.py set both, and a host test file run alone (``python -m unittest``) has them too.

Standard library only; no bpy.
"""

import json
import os
import sys

from . import session_journal as journal

# Every check's switch. A Blender Angelo starts itself (a worker, a headless session) builds its environment from a
# whitelist and turns a check on only by its variable, so each such builder carries these (``carried``): a suite's
# checks then cover the worker that writes a hosted chat's journal too.
SWITCHES = ("ANGELO_JOURNAL_VERIFY", "ANGELO_JOURNAL_TOUCHES")


def enabled(name):
    """A check's switch: the variable ``name`` when set, else on only for a test run by ``unittest`` outside Blender.

    A host test file run alone (the per-file rule) sets nothing, and has the checks as the whole suite does. The
    product never matches: it always runs inside Blender, whose own modules are built into its interpreter
    (``_bpy_path``); so do its worker, headless and benchmark Blenders, which turn a check on only by the variable.
    """
    value = os.environ.get(name)
    if value is not None:
        return value == "1"
    return "_bpy_path" not in sys.builtin_module_names and "unittest" in sys.modules


def carried(environ):
    """The check switches ``environ`` sets ("0" or "1"), for a child Blender's environment; none in the product."""
    return {name: environ[name] for name in SWITCHES if environ.get(name) in ("0", "1")}


def first_difference(saved, expected, path="$"):
    """Where two JSON values first differ, for the journal's verification failures."""
    if type(saved) is not type(expected):
        return "%s: %s on disk, %s in memory" % (path, type(saved).__name__, type(expected).__name__)
    if isinstance(saved, dict):
        for key in list(expected) + [key for key in saved if key not in expected]:
            if key not in saved or key not in expected:
                return "%s.%s: %s" % (path, key, "missing on disk" if key not in saved else "not in memory")
            if saved[key] != expected[key]:
                return first_difference(saved[key], expected[key], "%s.%s" % (path, key))
    elif isinstance(saved, list):
        for index, (left, right) in enumerate(zip(saved, expected)):
            if left != right:
                return first_difference(left, right, "%s[%d]" % (path, index))
        return "%s: %d items on disk, %d in memory" % (path, len(saved), len(expected))
    return "%s: %r on disk, %r in memory" % (path, saved, expected)


def rows_touched(rows, written, touched, length):
    """Raise ``AssertionError`` naming the first of ``rows[:length]`` the file holds (``written``: its bytes as last
    written) that changed in place without a ``touch``: every such change goes through ``Controller._touched``."""
    for index in range(min(len(written), length)):
        if index in touched:
            continue
        data = journal.encode(rows[index])
        if data != written[index]:
            raise AssertionError(
                "Conversation row %d changed in place without a touch (TranscriptRows._touched, SessionStore.touch): %s"
                % (index, first_difference(json.loads(written[index]), json.loads(data), "messages[%d]" % index)))


def results_touched(results, held, touched):
    """Raise ``AssertionError`` naming the first record of ``results`` that replaced the one the file holds (``held``)
    without a mark: every replacement goes through ``SessionStore.touch_result`` (``tool_finalization``)."""
    for key, value in results.items():
        if key not in touched and held.get(key, value) is not value:
            raise AssertionError("Conversation result %r replaced without touch_result (SessionStore.touch_result)"
                                 % (key,))
