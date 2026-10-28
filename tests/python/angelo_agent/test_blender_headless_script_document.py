# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An approved script file that replaces the document, in the REAL headless MCP child: refused, never a crash.

Run with Angelo's bundled Python (no window opens; take ``/tmp/angelo-billed.lock`` on a shared Mac)::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/test_blender_headless_script_document.py \\
        --binary <Angelo.app>/Contents/MacOS/Angelo --out <folder ending in .noindex>

Evidence: the headless child runs script files the person always allows through the same executor as the window,
and in a background Blender a script that loads a file freed the window that executor read the scene back through:
the child crashed (exit 139; the parallel-agents spike found it with no worker). ``script_document`` ends such a
script as ``DOCUMENT_REPLACED``. Through the real launcher (``scripts/angelo/angelo_mcp.py``) and its child, with
private HOME, TMPDIR, state and connections folders. Each script file is approved as the window's "Always allow
this file while it's unchanged" saves it, then run on the open file:

* ``read_homefile``, ``open_mainfile`` of another file, a computed name and ``read_factory_settings``: refused as
  ``DOCUMENT_REPLACED``, and the session's own file is loaded again with nothing of the run saved (``FOREIGN_FILE``);
* after each, the same child runs a plain approved file and saves it: it is alive and on the session's file;
* the source's bytes never change, and the saved copy holds the plain runs and nothing a load brought in.
Writes ``headless-script-document.json``.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_blender_headless_mcp import FIXTURE, Client, sha256  # noqa: E402

CHECK = r'''
import bpy, json
print("CHECK" + json.dumps(sorted(o.name for o in bpy.data.objects)))
'''
LOADS = {"read_homefile": "bpy.ops.wm.read_homefile(use_empty=True)\n",
         "open_other_file": "bpy.ops.wm.open_mainfile(filepath=%r, load_ui=False)\n",
         "computed_name": "getattr(bpy.ops.wm, 'read_' + 'homefile')(use_empty=True)\n",
         # Last: it also resets the preferences, and the next approved file must still run and save.
         "factory_settings": "bpy.ops.wm.read_factory_settings(use_empty=True)\n"}


def text(result):
    return "\n".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    options = parser.parse_args()
    binary = Path(options.binary).resolve()
    contents = binary.parents[1]
    python = next((contents / "Resources").glob("*/python/bin/python3.*[0-9]"))
    scripts = next((contents / "Resources").glob("*/scripts"))
    out = Path(options.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hsd-", dir="/private/tmp"))
    home, tmp, state, connections, project = (root / "home", root / "tmp", root / "state", root / "connections",
                                              out / "project")
    for folder in (home, tmp, state, connections, project):
        folder.mkdir(parents=True, exist_ok=True)
    environ = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(tmp) + "/",
               "ANGELO_STATE_DIR": str(state), "ANGELO_CONNECTIONS_DIR": str(connections),
               "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_DEFAULT_CHAT": "1", "LANG": "en_US.UTF-8"}
    checks, results = {}, {}

    def check(name, value, detail=None):
        checks[name] = bool(value)
        print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": " + str(detail)[:500]))

    source, other = project / "scene.blend", project / "other.blend"
    markers = root / "markers"
    markers.mkdir()
    for path in (source, other):
        subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", FIXTURE, "--", str(path),
                        str(markers)], env=environ, capture_output=True, timeout=120, check=True)
    before = sha256(source)
    sys.path.insert(0, str(scripts / "addons_core"))
    from angelo_agent import script_files
    from angelo_agent.external_clients import AppRegistry, write_access, write_organization
    name = AppRegistry(state).connect("claude_code")
    write_access(state, True)   # What Angelo's window records while "Allow apps on this computer" is on.
    write_organization(state, None)

    def approved(label, body):
        """A script file the person always allows while it is unchanged (what the window's button saves)."""
        path = out / (label + ".py")
        path.write_text("import bpy\n" + body)
        script_files.attach(state)
        script_files.allow(name, os.path.realpath(source), script_files.read_script(str(path)))
        script_files.detach()
        return path

    saved = None
    client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name,
                     "--state-dir", str(state)], environ)
    try:
        client.rpc("initialize", {"protocolVersion": "2025-06-18"})
        opened, _ = client.call("angelo_open_file", {"file": str(source)})
        check("the file opens headless", not opened["isError"], opened)
        for label, body in LOADS.items():
            load = approved("load_" + label, body % str(other) if "%r" in body else body)
            result, _ = client.call("angelo_run_script_file", {"file": str(load)})
            results[label] = text(result)[:2000]
            check(label + ": refused as DOCUMENT_REPLACED", result["isError"] and "DOCUMENT_REPLACED" in text(result),
                  result)
            check(label + ": nothing of it is saved and the session's own file is loaded again",
                  "nothing of the run was saved" in text(result) and "Angelo loaded" in text(result), result)
            plain = approved("plain_" + label, "bpy.context.scene.collection.objects.link(bpy.data.objects.new(%r, None))\n"
                             % ("After_" + label))
            result, _ = client.call("angelo_run_script_file", {"file": str(plain)})
            check(label + ": the same child runs the next approved file and saves it",
                  not result["isError"] and "Saved as " in text(result), result)
            if "Saved as " in text(result):
                saved = Path(text(result).split("Saved as ", 1)[1].split(" (", 1)[0].split(". ", 1)[0])
        client.call("angelo_close_file")
    finally:
        client.close()
    check("the source's bytes never change", sha256(source) == before)
    objects = []
    if saved is not None:
        reopened = subprocess.run([str(binary), "--background", "--factory-startup", str(saved), "--python-expr",
                                   CHECK], env=environ, capture_output=True, text=True, timeout=120)
        line = next((line for line in reopened.stdout.splitlines() if line.startswith("CHECK")), "CHECK[]")
        objects = json.loads(line[5:])
    check("the saved copy holds every plain run and the session's own objects",
          all("After_" + label in objects for label in LOADS) and "Pedestal" in objects, objects)
    report = {"checks": checks, "results": results, "saved_objects": objects,
              "success": bool(checks) and all(checks.values())}
    (out / "headless-script-document.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_HEADLESS_SCRIPT_DOCUMENT", json.dumps({"success": report["success"]}), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
