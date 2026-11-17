# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A second Angelo started in the background on a .blend never writes a chat of the first, in a real Blender.

The showcase's controlled repro (Godot session, build 06259fa5bb0, 2026-11-14): with Angelo open and one call
answered, ``angelo -b some_file.blend --python-expr pass`` in a terminal, as Godot's .blend import runs it on every
save, made the open chat refuse the next call with "The invocation journal could not be saved": the active chat's
file was rewritten the second the background process started, and only for a start with a file and the person's
preferences. ``test_native_two_processes_one_chat`` pins the stores and the controller with stand-in second
processes; this suite starts the real one: this build's own binary, in the background, on a saved file, with this
suite's state folder and without the settings that switch a suite's start-up behaviours off, so it starts as a
person's or an engine's does. And with what made it happen: saved preferences that list Angelo's add-on. Angelo
starts its add-on in its window by a timer, which leaves an entry in the preferences; once those are saved (a
quit does it), every background Angelo that reads them started the whole add-on (the showcase: not with
``--factory-startup``). This suite saves its preferences with that entry first.

Two runs. ``open``: the first Angelo (this process) has the chat open; the chat's file must be byte for byte the
same afterwards and the open chat's next save must succeed. ``closed``: no Angelo has the chat open; a background
Angelo that only opens a file must still leave the person's chat as it was. Each second process records who made
a chat's controller and who saved a chat (``trace-<run>.jsonl``, in the report), whether its preferences listed
Angelo's add-on (a run where they did not proves nothing and fails) and whether the add-on was started from them
(it must not be: ``angelo_background_addons``).

Writes ``second-process-file-load.json`` in ``ANGELO_QA_DIR``; exits 1 on any failed check.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import subprocess
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-second-process-"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui  # noqa: E402
from angelo_agent.headless_main import _prepare_preferences, _register  # noqa: E402

DOCUMENT = OUT / "document.blend"
AUTOMATION = ("ANGELO_DISABLE_DEFAULT_CHAT", "ANGELO_DISABLE_INTRO")   # What a person's start does not set.
# Run in the second process before it reads its file: who makes a chat's controller and who saves a chat.
TRACER = r'''
import json, os, traceback
def note(kind):
    stack = [line.strip().splitlines()[0] for line in traceback.format_stack()[-16:-1]]
    with open(os.environ["ANGELO_QA_TRACE"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "stack": stack}) + "\n")
try:
    import bpy
    from angelo_agent import controller, sessions
    made, saved = controller.Controller.__init__, sessions.SessionStore.save
    def init(self, *args, **kwargs):
        note("controller")
        return made(self, *args, **kwargs)
    def save(self, *args, **kwargs):
        note("save")
        return saved(self, *args, **kwargs)
    controller.Controller.__init__, sessions.SessionStore.save = init, save
    import addon_utils
    listed = "angelo_agent" in bpy.context.preferences.addons
    note("preferences list the add-on" if listed else "preferences without it")
    note("add-on started" if addon_utils.check("angelo_agent")[1] else "add-on not started")
except Exception as error:
    note("no addon: %r" % (error,))
'''
checks = {}
report = {"runs": {}, "checks": checks}


def second_angelo(label):
    """This build in the background on the saved file, as a terminal or an engine starts it; its trace."""
    trace = OUT / ("trace-%s.jsonl" % label)
    env = {key: value for key, value in os.environ.items() if key not in AUTOMATION}
    env["ANGELO_QA_TRACE"] = str(trace)
    done = subprocess.run([bpy.app.binary_path, "-b", "--python-expr", TRACER, str(DOCUMENT), "--python-expr", "pass"],
                          env=env, capture_output=True, text=True, timeout=240)
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    report["runs"][label] = {"exit": done.returncode, "trace": rows, "output_tail": (done.stdout + done.stderr)[-2000:]}
    kinds = [row["kind"] for row in rows]
    checks[label + "_the_second_process_read_preferences_listing_the_addon"] = "preferences list the add-on" in kinds
    checks[label + "_the_addon_was_not_started_from_them"] = "add-on not started" in kinds
    checks[label + "_the_second_process_opened_no_chat"] = "controller" not in kinds and "save" not in kinds
    return rows


def main():
    _prepare_preferences(bpy)
    _register(bpy, ui)
    bpy.ops.wm.save_userpref()   # The preferences a person's Angelo leaves: they list its add-on.
    bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))
    chat = ui.get_controller()   # The active chat, open in this Angelo.
    chat.emit("message", {"role": "user", "text": "Make a cube"})
    chat.pump_events()
    chat.save()
    path = chat.store.path
    before = path.read_bytes()
    second_angelo("open")
    checks["open_chat_file_untouched"] = path.read_bytes() == before
    chat.emit("message", {"role": "user", "text": "And another"})
    chat.pump_events()
    try:
        chat.save()
        checks["open_chat_saves_after_it"] = True
    except OSError as error:
        checks["open_chat_saves_after_it"] = False
        report["open_save_error"] = type(error).__name__
    chat.close()   # No Angelo has the chat open now.
    ui._controllers.clear()
    ui._controller = None
    before = path.read_bytes()
    second_angelo("closed")
    checks["closed_chat_file_untouched"] = path.read_bytes() == before


try:
    main()
except Exception:   # Reported as a failed check, never a silent pass.
    checks["no_error"] = False
    report["error"] = traceback.format_exc()
report["success"] = bool(checks) and all(checks.values())
(OUT / "second-process-file-load.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SECOND_PROCESS_FILE_LOAD", json.dumps({"success": report["success"], "checks": checks}), flush=True)
sys.exit(0 if report["success"] else 1)
