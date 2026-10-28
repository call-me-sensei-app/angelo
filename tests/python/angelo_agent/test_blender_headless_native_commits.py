# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native commits in a REAL headless child: staged programs and recipes APPLY with one Undo step on every load.

Run with Angelo's bundled Python (no window opens; take ``/tmp/angelo-billed.lock`` on a shared Mac)::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 \\
        tests/python/angelo_agent/test_blender_headless_native_commits.py \\
        --binary <Angelo.app>/Contents/MacOS/Angelo --out <folder ending in .noindex> [--scripts <checkout>/scripts]

A background Blender builds no Undo history when it loads a file, and every native commit needs one active Undo step
(``ED_angelo_undo_begin``), so before the fix every ``angelo_scene_program`` / ``angelo_apply_recipe`` APPLY refused
in the child while typed tools on the legacy Undo path worked (the other headless suites use only those). Each load
the child makes must start the loaded document's own history, as a window's load does. Checked over the real launcher
(``scripts/angelo/angelo_mcp.py``) and child, private HOME, TMPDIR, state and connections:

* the first load (the working copy): the file reads as having no unsaved changes; an APPLY program and an APPLY
  recipe each commit, publish their Undo step and are saved to the output, which then reads as saved;
* a reload (a failed script file makes the child load the last saved state): the file again reads as unmodified and
  an APPLY program commits;
* a restart on the output (the child was killed; the launcher starts one with ``--resume``): the same;
* the output reopens in a separate Blender with every committed edit.

The history is the binary's (``wm_files.cc``, ``_angelo_undo.keep_background_document``): run it on an app built from
the code under test. ``--scripts`` runs another ``scripts`` folder (an installed one with a branch's Python files over
it) with the app's binary and Python, without changing the bundle. Writes ``headless-native-commits.json``.
Contract: doc/angelo/external_mcp.rst, "Headless".
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_blender_headless_mcp import FIXTURE, Client, children_of, sha256  # noqa: E402

CHECK = r'''
import bpy, json
box = bpy.data.objects.get("ProgramBox")
print("CHECK" + json.dumps({"objects": sorted(o.name for o in bpy.data.objects),
                            "box": [round(v, 4) for v in box.dimensions] if box else None}))
'''
HANDLE = re.compile(r"scn:[a-f0-9]{32}:[1-9][0-9]*")


def text(result):
    return json.dumps(result)


def structured(result):
    """The call's JSON result: ``structuredContent``, else the JSON text block the result carries."""
    if result.get("structuredContent"):
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text" and block["text"].startswith("{"):
            try:
                return json.loads(block["text"])
            except ValueError:
                pass
    return {}


def program(name, location):
    return {"mode": "APPLY", "operations": [{"kind": "create_primitive", "target": None, "parameters": {
        "primitive": "CUBE", "name": name, "size": 1, "location": list(location)}}]}


def committed(result):
    """Committed natively with its Undo step published (``program_finalization.after_native_undo``)."""
    data = structured(result)
    return (not result.get("isError") and data.get("status") == "committed" and data.get("undo_published") is True
            and data.get("undoable") is True)


def saved_output(result):
    blocks = [block["text"] for block in result.get("content", []) if block.get("type") == "text"
              and "Saved as " in block["text"]]
    return Path(blocks[0].split("Saved as ", 1)[1].split(" (", 1)[0].split(". ", 1)[0]) if blocks else None


def binding(result, name):
    """``name``'s handle as an ``angelo_scene_search`` result shows it (the app's short form, which the host expands,
    or the long one) and its object revision if shown (else the host infers the observed one,
    ``identifiers.INFERRED_FIELDS``)."""
    def walk(value):
        if isinstance(value, dict):
            if value.get("name") == name and isinstance(value.get("object") or value.get("handle"), str):
                yield value
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
    for row in walk(structured(result)):
        handle, revision = row.get("object") or row.get("handle"), row.get("object_revision") or row.get("revision")
        if HANDLE.fullmatch(handle) or re.fullmatch(r"o[1-9][0-9]*", handle):
            return handle, revision if isinstance(revision, str) else None
    return None, None


def unmodified(client):
    """``angelo_blend_file``'s ``unsaved_changes``: a load, and the history it starts, never marks the file modified."""
    result, _ = client.call("angelo_blend_file")
    return structured(result).get("unsaved_changes") is False, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--scripts", default=None, help="a checkout's scripts folder to run instead of the app's")
    options = parser.parse_args()
    binary = Path(options.binary).resolve()
    contents = binary.parents[1]
    python = next((contents / "Resources").glob("*/python/bin/python3.*[0-9]"))
    scripts = Path(options.scripts).resolve() if options.scripts else next((contents / "Resources").glob("*/scripts"))
    out = Path(options.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hnc-", dir="/private/tmp"))
    try:
        home, tmp, state, connections, project = (root / "home", root / "tmp", root / "state", root / "connections",
                                                  out / "project")
        shutil.rmtree(project, ignore_errors=True)
        for folder in (home, tmp, state, connections, project):
            folder.mkdir(parents=True, exist_ok=True)
        environ = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(tmp) + "/",
                   "ANGELO_STATE_DIR": str(state), "ANGELO_CONNECTIONS_DIR": str(connections),
                   "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_DEFAULT_CHAT": "1", "LANG": "en_US.UTF-8"}
        checks, timings = {}, {}

        def check(name, value, detail=None):
            checks[name] = bool(value)
            shown = "" if value or detail is None else ": " + str(detail)[:700]
            print(("PASS " if value else "FAIL ") + name + shown)

        source = project / "scene.blend"
        markers = root / "markers"
        markers.mkdir()
        subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", FIXTURE, "--", str(source),
                        str(markers)], env=environ, capture_output=True, timeout=120, check=True)
        before = sha256(source)
        sys.path.insert(0, str(scripts / "addons_core"))
        from angelo_agent import script_files
        from angelo_agent.headless_host import RELOADED
        from angelo_agent.external_clients import AppRegistry, write_access, write_organization
        name = AppRegistry(state).connect("claude_code")
        write_access(state, True)   # What Angelo's window records while "Allow apps on this computer" is on.
        write_organization(state, None)   # And while signed out: no organization (a missing record is the strictest).
        failing = out / "fails.py"
        failing.write_text("import bpy\nbpy.data.objects.new('NeverSaved', None)\nraise RuntimeError('on purpose')\n")
        script_files.attach(state)   # What the window's "Always allow this file while it's unchanged" saves.
        script_files.allow(name, os.path.realpath(source), script_files.read_script(str(failing)))
        script_files.detach()
        client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name,
                         "--state-dir", str(state)], environ)
        output = None
        try:
            client.rpc("initialize", {"protocolVersion": "2025-06-18"})
            opened, timings["open_cold_ms"] = client.call("angelo_open_file", {"file": str(source)})
            check("the file opens headless", not opened["isError"], opened)
            check("the opened file has no unsaved changes", *unmodified(client))

            # The first load: a program and a recipe ----------------------------------------------------------------
            result, timings["program_apply_ms"] = client.call("angelo_scene_program", program("ProgramBox", (3, 0, 0)))
            output = saved_output(result)
            check("an APPLY program commits with its Undo step", committed(result), result)
            check("the committed program is saved to a new file", output is not None, result)
            check("once saved, the edited file reads as saved", *unmodified(client))
            # The program's own box: the fixture's Pedestal carries a Python driver, which no program binds.
            result, _ = client.call("angelo_scene_search", {"query": "ProgramBox"})
            handle, revision = binding(result, "ProgramBox")
            check("the box's handle is observed", handle, result)
            arguments = {"recipe": "fit-dimensions", "target": handle, "mode": "APPLY",
                         "parameters": {"dimensions": [1, 1, 2]}}
            if revision:
                arguments["revision"] = revision
            result, timings["recipe_apply_ms"] = client.call("angelo_apply_recipe", arguments)
            check("an APPLY recipe commits with its Undo step", committed(result), result)
            check("the committed recipe is saved to the same output",
                  output is not None and saved_output(result) == output, result)

            # A reload: a failed script makes the child load the last saved state ---------------------------------
            result, _ = client.call("angelo_run_script_file", {"file": str(failing)})
            reloaded = RELOADED.format(name=output.name if output else "?")
            check("the script ran, failed and the child reloaded the last saved state",
                  result["isError"] and "on purpose" in text(result) and reloaded in text(result), result)
            client.call("angelo_inspect_scene")   # As an app does after "observe the scene again".
            check("after a reload, the file has no unsaved changes", *unmodified(client))
            result, timings["program_after_reload_ms"] = client.call("angelo_scene_program",
                                                                      program("AfterReload", (-3, 0, 0)))
            check("after a reload, an APPLY program commits with its Undo step", committed(result), result)
            check("after a reload, it is saved to the same output",
                  output is not None and saved_output(result) == output, result)

            # A restart: the child died; the launcher starts one on the output (--resume) --------------------------
            child = children_of(client.process.pid)
            check("one child process", len(child) == 1, child)
            if child:
                os.kill(child[0], signal.SIGKILL)
                time.sleep(1.0)
            result, _ = client.call("angelo_inspect_scene")
            check("the session started again on its output", "started its headless session again" in text(result),
                  result)
            check("after a restart, the file has no unsaved changes", *unmodified(client))
            result, timings["program_after_restart_ms"] = client.call("angelo_scene_program",
                                                                       program("AfterRestart", (0, 3, 0)))
            check("after a restart on the output, an APPLY program commits with its Undo step", committed(result),
                  result)
            check("after a restart, it is saved to the same output",
                  output is not None and saved_output(result) == output, result)
            client.call("angelo_close_file")
        finally:
            client.close()
        if output is not None:
            reopened = subprocess.run([str(binary), "--background", "--factory-startup", str(output), "--python-expr",
                                       CHECK], env=environ, capture_output=True, text=True, timeout=120)
            line = next((line for line in reopened.stdout.splitlines() if line.startswith("CHECK")), "CHECK{}")
            data = json.loads(line[5:])
            objects = data.get("objects", [])
            check("the output holds every committed edit and nothing of the failed script",
                  {"ProgramBox", "AfterReload", "AfterRestart", "Pedestal"} <= set(objects)
                  and "NeverSaved" not in objects, data)
            check("the output holds the recipe's dimensions", data.get("box") == [1.0, 1.0, 2.0], data)
        check("no code from the .blend ran", not any(markers.iterdir()), sorted(p.name for p in markers.iterdir()))
        check("the source's bytes never change", sha256(source) == before)
        passed = all(checks.values())   # "success" is the field the suite runner reads (native_suite_manifest.Report).
        report = {"checks": checks, "timings": timings, "binary": str(binary), "scripts": str(scripts),
                  "success": passed, "passed": passed}
        (out / "headless-native-commits.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(timings))
        return 0 if report["passed"] else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
