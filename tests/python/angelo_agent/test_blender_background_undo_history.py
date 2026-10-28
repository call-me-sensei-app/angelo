# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background Undo history after the shared headless start-up: each load starts that file's own, never another's.

Run in Angelo, in background (``--factory-startup``)::

    <Angelo> --background --factory-startup --python tests/python/angelo_agent/test_blender_background_undo_history.py

Background Blender keeps no Undo history: each load frees it and only a window's load starts it again, so native
commits refused in the headless child and in parallel-agent workers. Their shared start-up
(``headless_main._prepare_preferences``) asks the binary to keep it (``_angelo_undo.keep_background_document``). Checked
here, in one background process, through Blender's own Undo operators:

* before the start-up, a loaded file has no history (the gap, so the check below is not vacuous);
* the start-up forces Global Undo and a bounded step count (the person's 0 would refuse every commit);
* after each load (a file, another file, the first again, ``revert_mainfile``), each following unsaved edits: the
  file reads as unmodified, Undo has nothing before the loaded state, and one pushed edit undoes to exactly the
  loaded file, never to an earlier file's data;
* a save of the edited open file reads as saved (before, only a window's event loop reset it, so a background
  document read as unsaved for good once modified); a copy does not, as in a window, until its owner marks it
  (``program_undo.background_document_saved``, the headless child's own save).

Writes ``background-undo-history.json`` in ``ANGELO_QA_DIR``. The real headless child runs in
test_blender_headless_native_commits.py.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-background-undo-history-qa"))
OUT.mkdir(parents=True, exist_ok=True)

from angelo_agent import headless_main  # noqa: E402

checks = {}


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


def window():
    return bpy.context.window_manager.windows[0]


def can_undo():
    with bpy.context.temp_override(window=window()):
        return bpy.ops.ed.undo.poll()


def has_history():
    """Whether an Undo stack exists (``ed.undo_history`` polls exactly that, with a screen)."""
    with bpy.context.temp_override(window=window()):
        return bpy.ops.ed.undo_history.poll()


def undo():
    with bpy.context.temp_override(window=window()):
        return bpy.ops.ed.undo()


def names():
    return {obj.name for obj in bpy.data.objects}


def make(path, marker):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.collection.objects.link(bpy.data.objects.new(marker, None))
    bpy.ops.wm.save_as_mainfile(filepath=str(path))


def load(path):
    return bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)


def edit_once(label):
    """One edit with its own Undo step, as a committed edit has."""
    bpy.context.scene.collection.objects.link(bpy.data.objects.new(label, None))
    with bpy.context.temp_override(window=window()):
        bpy.ops.ed.undo_push(message=label)


def history_is_this_file(label, expected):
    """Unmodified, nothing to undo before the loaded state, and one edit undoes to exactly ``expected``."""
    check(label + ": the loaded file reads as unmodified", not bpy.data.is_dirty)
    check(label + ": the load started its history", has_history())
    check(label + ": nothing to undo before the loaded state", not can_undo())
    edit_once("Edit")
    check(label + ": an edit has its Undo step", can_undo())
    check(label + ": Undo returns exactly the loaded file", "FINISHED" in undo() and names() == expected, names())
    check(label + ": and nothing earlier is left to undo", not can_undo())


def main():
    first, second = OUT / "first.blend", OUT / "second.blend"
    make(first, "OnlyInFirst")
    make(second, "OnlyInSecond")

    load(first)
    check("before the start-up, background Blender keeps no history after a load", not has_history())

    edit = bpy.context.preferences.edit
    edit.undo_steps = 0   # The person's preferences may say so; a native commit refuses with 0.
    headless_main._prepare_preferences(bpy)
    check("the start-up turns Global Undo on with a bounded step count",
          edit.use_global_undo and edit.undo_steps == headless_main.UNDO_STEPS > 0, edit.undo_steps)

    load(first)
    history_is_this_file("first file", {"OnlyInFirst"})
    load(second)
    history_is_this_file("another file", {"OnlyInSecond"})
    load(first)
    history_is_this_file("the first file again", {"OnlyInFirst"})
    edit_once("Unsaved")
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.revert_mainfile(use_scripts=False)
    history_is_this_file("after revert", {"OnlyInFirst"})
    edit_once("Saved")
    check("an edit reads as unsaved", bpy.data.is_dirty)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "copy.blend"), copy=True)
    check("a copy of it is not the open file, so it still reads as unsaved (as in a window)", bpy.data.is_dirty)
    from angelo_agent.program_undo import background_document_saved
    check("its owner marks a copy it placed as the saved state", background_document_saved()
          and not bpy.data.is_dirty)
    edit_once("SavedAgain")
    check("another edit reads as unsaved", bpy.data.is_dirty)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "saved.blend"))
    check("a save of the open file reads as saved", not bpy.data.is_dirty)

    passed = all(checks.values())
    report = {"count": len(checks), "checks": checks, "binary_path": bpy.app.binary_path,
              "version": bpy.app.version_string, "success": passed}
    (OUT / "background-undo-history.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_BACKGROUND_UNDO_HISTORY_" + ("OK" if passed else "FAILED"), flush=True)
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
