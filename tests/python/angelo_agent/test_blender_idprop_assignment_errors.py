# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A custom-property assignment that fails raises its error and leaves the property as it was.

Stock Blender 5.2 (and 5.3, main) has four ways a custom-property assignment (``idprop_py_api.cc``) leaves the
interpreter or the heap in a bad state:

* An existing array property given a sequence with an item that does not convert (``[1, 2**40, 3]`` for an int
  array, ``10**400`` for a float one) skipped that item with ``continue``: the other items were written, the
  assignment reported success, and the next unrelated call raised ``SystemError: ... returned a result with an
  exception set``. Statically typed (``bpy.props`` vector) and ordinary custom properties alike.
* A buffer (``array.array``, NumPy) longer than an existing array of its type was copied past the array's end (a heap
  overflow): the check compared bytes with items, in an assert only.
* A string that cannot be encoded (a lone surrogate, ``"\\ud800"``) went on with a null value: a new property was
  stored empty with the error still set, an existing one crashed (SIGSEGV).
* A mapping whose ``len()`` disagreed with its keys passed a null item on and crashed (SIGSEGV).

Angelo converts every item before writing, compares a buffer's item count, stops on an unencodable string and walks
what ``keys()`` and ``values()`` returned. Each case runs in its own child process of this binary, so a crash fails
that case and the others still report. Checks: the right exception at the assignment, nothing pending at the next
call, the old value kept (or, for a buffer of another length, a new property of that length), and assignments that
should work still do.

Background only. No add-on, provider, account or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import array
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import bpy

CHILD_FLAG = "--angelo-idprop-case"


class AngeloIdpropGroup(bpy.types.PropertyGroup):
    int_vector: bpy.props.IntVectorProperty(size=3)
    float_vector: bpy.props.FloatVectorProperty(size=3)


def _setup():
    bpy.utils.register_class(AngeloIdpropGroup)
    bpy.types.Scene.angelo_idprop = bpy.props.PointerProperty(type=AngeloIdpropGroup)
    return bpy.context.scene.angelo_idprop, bpy.data.objects.new("Holder", None)


def _assign(target, key, value):
    """Assign, then make an unrelated call: returns (error type name or None, pending SystemError or None)."""
    try:
        target[key] = value
        error = None
    except Exception as caught:
        error = type(caught).__name__
    try:
        len([1, 2, 3])
        bpy.data.objects.get("nothing")
        pending = None
    except SystemError as caught:
        pending = str(caught)
    return error, pending


def _expect(results, name, target, key, value, error, kept):
    got_error, pending = _assign(target, key, value)
    stored = target.get(key)
    stored = list(stored) if hasattr(stored, "__len__") and not isinstance(stored, str) else stored
    results[name + "_raises"] = "ok" if got_error == error else "raised %r, expected %r" % (got_error, error)
    results[name + "_nothing_pending"] = "ok" if pending is None else pending
    results[name + "_value"] = "ok" if stored == kept else "stored %r, expected %r" % (stored, kept)


def case_static_int_vector_overflow():
    group, _holder = _setup()
    group.int_vector = (7, 8, 9)
    results = {}
    _expect(results, "static_int", group, "int_vector", [1, 2 ** 40, 3], "TypeError", [7, 8, 9])
    _expect(results, "static_int_in_range_still_assigns", group, "int_vector", [1, 2, 3], None, [1, 2, 3])
    return results


def case_static_float_vector_overflow():
    group, _holder = _setup()
    group.float_vector = (7.0, 8.0, 9.0)
    results = {}
    _expect(results, "static_float", group, "float_vector", [1.0, 10 ** 400, 3.0], "TypeError", [7.0, 8.0, 9.0])
    _expect(results, "static_float_from_ints_still_converts", group, "float_vector", [1, 2, 3], None,
            [1.0, 2.0, 3.0])
    return results


def case_dynamic_array_overflow():
    _group, holder = _setup()
    results = {}
    holder["ints"] = [7, 8, 9]
    _expect(results, "int_array", holder, "ints", [1, 2 ** 40, 3], "OverflowError", [7, 8, 9])
    holder["doubles"] = [7.0, 8.0, 9.0]
    _expect(results, "double_array", holder, "doubles", [1.0, 10 ** 400, 3.0], "OverflowError", [7.0, 8.0, 9.0])
    _expect(results, "int_array_in_range_still_assigns", holder, "ints", [4, 5, 6], None, [4, 5, 6])
    return results


def case_buffer_lengths():
    group, holder = _setup()
    results = {}
    holder["ints"] = [7, 8, 9]
    _expect(results, "longer_buffer_replaces", holder, "ints", array.array("i", range(1000)), None,
            list(range(1000)))
    holder["ints2"] = [7, 8, 9]
    _expect(results, "same_length_buffer_copies", holder, "ints2", array.array("i", (4, 5, 6)), None, [4, 5, 6])
    group.int_vector = (7, 8, 9)
    _expect(results, "static_same_length_buffer", group, "int_vector", array.array("i", (1, 2, 3)), None,
            [1, 2, 3])
    _expect(results, "static_other_length_buffer", group, "int_vector", array.array("i", range(5)), "TypeError",
            [1, 2, 3])
    return results


def case_unencodable_string():
    _group, holder = _setup()
    results = {}
    _expect(results, "new_string", holder, "text_new", "a\ud800b", "UnicodeEncodeError", None)
    holder["text"] = "old"
    _expect(results, "existing_string", holder, "text", "a\ud800b", "UnicodeEncodeError", "old")
    _expect(results, "existing_string_still_assigns", holder, "text", "new", None, "new")
    return results


class _LyingLength(dict):
    def __len__(self):
        return 3


class _UnevenMapping(dict):
    """A mapping whose keys() and values() differ in length. A dict subclass, so it is converted as
    a mapping: a plain class with ``__getitem__`` passes Python's sequence check and would be read
    as an endless sequence."""

    def keys(self):
        return ["a", "b"]

    def values(self):
        return [1]


def case_mappings():
    _group, holder = _setup()
    results = {}
    error, pending = _assign(holder, "lying", _LyingLength(a=1))
    results["lying_len_assigns"] = "ok" if error is None and holder["lying"].to_dict() == {"a": 1} else repr(error)
    results["lying_len_nothing_pending"] = "ok" if pending is None else pending
    error, pending = _assign(holder, "uneven", _UnevenMapping())
    results["uneven_raises"] = "ok" if error == "ValueError" else repr(error)
    results["uneven_nothing_pending"] = "ok" if pending is None else pending
    results["uneven_not_stored"] = "ok" if "uneven" not in holder else "stored"
    error, pending = _assign(holder, "plain", {"x": 1.5})
    results["dict_still_assigns"] = "ok" if error is None and holder["plain"].to_dict() == {"x": 1.5} else repr(error)
    results["dict_still_assigns_nothing_pending"] = "ok" if pending is None else pending
    return results


CASES = {name[len("case_"):]: fn for name, fn in globals().items() if name.startswith("case_")}


def run_child(name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        results = CASES[name]()
    except Exception as error:
        results = {"setup": "%s: %s" % (type(error).__name__, error)}
    print("ANGELO_IDPROP_CASE " + json.dumps({"case": name, "results": results}), flush=True)


def run_parent():
    assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
    assert bpy.app.background, "Run with --background"
    out = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-idprop-assignment-errors"))
    out.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "cases": {}, "failed": [], "binary": bpy.app.binary_path, "paid_requests": 0}
    for name in CASES:
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "3",
                   "--python", __file__, "--", CHILD_FLAG, name]
        try:
            child = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            report["cases"][name] = {"exit": "timeout"}
            report["failed"].append(name)
            continue
        lines = [line for line in child.stdout.splitlines() if line.startswith("ANGELO_IDPROP_CASE ")]
        results = json.loads(lines[-1].split(" ", 1)[1])["results"] if lines else {}
        ok = child.returncode == 0 and bool(results) and all(value == "ok" for value in results.values())
        report["cases"][name] = {"exit": child.returncode, "results": results,
                                 "stderr_tail": child.stderr[-600:] if not ok else ""}
        if not ok:
            report["failed"].append(name)
    report["success"] = bool(report["cases"]) and not report["failed"]
    (out / "idprop-assignment-errors.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_IDPROP_ASSIGNMENT_ERRORS", json.dumps({"success": report["success"], "failed": report["failed"]}),
          flush=True)


if CHILD_FLAG in sys.argv:
    run_child(sys.argv[sys.argv.index(CHILD_FLAG) + 1])
else:
    run_parent()
