# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless Python on a built Angelo: script files the person always allows, in a REAL ``Angelo --background`` child.

Run with Angelo's bundled Python (no window opens; take ``/tmp/angelo-billed.lock`` on a shared Mac)::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/test_blender_headless_script_files.py \\
        --binary <Angelo.app>/Contents/MacOS/Angelo --out <folder ending in .noindex>

Maintainer decisions (2026-10-23): an external app's script file the person chose to "Always allow while it's
unchanged" runs without Angelo's window under that approval; anything else is refused with how to approve it; and
every policy the window applies holds there. The break-it review found the in-process harness hid a missing operator
in the real child (H1), so everything here drives the real launcher (``scripts/angelo/angelo_mcp.py``) and the real
child it starts. Private HOME, TMPDIR, Angelo state and connections folder. Checked, in order:

* approve (what the window's button saves) -> run: the script runs and its object is in the saved copy;
* change the file -> refused, naming the file and how to approve it, and nothing runs;
* approve again -> run;
* the team's policy (``<state>/mcp/organization.json``, as the window records it while signed in):
  ``requireAskForEdits`` makes a typed edit ``needs_gui`` and the approved script refused as Ask;
  ``allowLocalCliAccess`` false refuses ``angelo_open_file``;
* the machine policy (``ANGELO_POLICY_FILE``, the lowest machine source; MDM sources apply the same way):
  ``AllowLocalCLIAccess`` false refuses, ``RequireAskForEdits`` makes a typed edit ``needs_gui``,
  ``AllowAutoApproval`` false refuses the approved script with that reason;
* the source's bytes never change. Writes ``headless-script-files.json``.
Contract: doc/angelo/external_mcp.rst, "Script files", "Headless" and "Organization policy".
"""

import argparse
import json
import os
import shutil
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
CUBE = {"name": "Box", "size": 1, "location": [0, 0, 0]}


def text(result):
    return json.dumps(result)


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
    root = Path(tempfile.mkdtemp(prefix="hsf-", dir="/private/tmp"))
    home, tmp, state, connections, project = (root / "home", root / "tmp", root / "state", root / "connections",
                                              out / "project")
    for folder in (home, tmp, state, connections, project):
        folder.mkdir(parents=True, exist_ok=True)
    environ = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(tmp) + "/",
               "ANGELO_STATE_DIR": str(state), "ANGELO_CONNECTIONS_DIR": str(connections),
               "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_DEFAULT_CHAT": "1", "LANG": "en_US.UTF-8"}
    checks, timings = {}, {}

    def check(name, value, detail=None):
        checks[name] = bool(value)
        print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": " + str(detail)[:500]))

    source = project / "scene.blend"
    markers = root / "markers"
    markers.mkdir()
    subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", FIXTURE, "--", str(source),
                    str(markers)], env=environ, capture_output=True, timeout=120, check=True)
    before = sha256(source)
    sys.path.insert(0, str(scripts / "addons_core"))
    from angelo_agent import script_files
    from angelo_agent.external_clients import AppRegistry, write_access, write_organization
    name = AppRegistry(state).connect("claude_code")
    write_access(state, True)   # What Angelo's window records while "Allow apps on this computer" is on.
    write_organization(state, None)   # And while signed out: no organization (a missing record is the strictest).
    build = out / "build.py"

    def write_build(label):
        """A script file as people write them: a main guard and its own ``__file__`` (break-it review FR-M1)."""
        build.write_text("import bpy\nif __name__ == '__main__':\n    object = bpy.data.objects.new(%r, None)\n"
                         "    bpy.context.scene.collection.objects.link(object)\n"
                         "result = {'made': %r, 'file': __file__}\n" % (label, label))

    def approve():
        """What the window's "Always allow this file while it's unchanged" saves."""
        script_files.attach(state)
        script_files.allow(name, os.path.realpath(source), script_files.read_script(str(build)))
        script_files.detach()

    def session(extra=None):
        client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name,
                         "--state-dir", str(state)], dict(environ, **(extra or {})))
        client.rpc("initialize", {"protocolVersion": "2025-06-18"})
        return client

    def saved_output(result):
        blocks = [block["text"] for block in result.get("content", []) if block.get("type") == "text"
                  and "Saved as " in block["text"]]
        return Path(blocks[0].split("Saved as ", 1)[1].split(" (", 1)[0].split(". ", 1)[0]) if blocks else None

    # Approve -> run -> change -> refused -> approve again -> run ------------------------------------------------
    write_build("FirstRun")
    approve()
    client = session()
    output = None
    try:
        opened, timings["open_cold_ms"] = client.call("angelo_open_file", {"file": str(source)})
        check("the file opens headless", not opened["isError"], opened)
        result, timings["approved_run_ms"] = client.call("angelo_run_script_file", {"file": str(build)})
        output = saved_output(result)
        check("an approved file runs in the real child and is saved to a new file", not result["isError"]
              and output is not None, result)
        check("it runs as that file: __name__ is __main__ and __file__ its path",
              json.dumps(os.path.realpath(build)) in text(result).replace("\\\"", "\""), result)
        write_build("Changed")
        result, _ = client.call("angelo_run_script_file", {"file": str(build)})
        check("a changed file is refused with how to approve it", result["isError"] and "needs_gui" in text(result)
              and str(os.path.realpath(build)) in text(result) and "Always allow this file" in text(result), result)
        approve()
        result, timings["reapproved_run_ms"] = client.call("angelo_run_script_file", {"file": str(build)})
        check("approved again, the changed file runs", not result["isError"] and saved_output(result) == output,
              result)
        result, _ = client.call("angelo_execute_python", {"description": "inline", "code": "result = 1"})
        check("inline code stays refused headless", result["isError"] and "needs_gui" in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()
    if output is not None:
        reopened = subprocess.run([str(binary), "--background", "--factory-startup", str(output), "--python-expr",
                                   CHECK], env=environ, capture_output=True, text=True, timeout=120)
        line = next((line for line in reopened.stdout.splitlines() if line.startswith("CHECK")), "CHECK[]")
        objects = json.loads(line[5:])
        check("the saved copy holds both approved runs and not the refused one",
              "FirstRun" in objects and "Changed" in objects and objects.count("Changed") == 1, objects)

    # The team's policy, as the window records it while signed in --------------------------------------------------
    def team_policy(policy):
        write_organization(state, policy)

    team_policy({"requireAskForEdits": True})
    client = session()
    try:
        client.call("angelo_open_file", {"file": str(source)})
        result, _ = client.call("angelo_create_cube", CUBE)
        check("team requireAskForEdits: a typed edit needs the window", result["isError"]
              and "needs_gui" in text(result), result)
        result, _ = client.call("angelo_run_script_file", {"file": str(build)})
        check("team requireAskForEdits: the approved script is refused as Ask", result["isError"]
              and "Ask or Read only" in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()
    team_policy({"allowLocalCliAccess": False})
    client = session()
    try:
        result, _ = client.call("angelo_open_file", {"file": str(source)})
        check("team allowLocalCliAccess false: opening is refused", result["isError"]
              and "organization turned off access" in text(result), result)
    finally:
        client.close()
    write_organization(state, None)   # Signed out again.

    # The machine policy (ANGELO_POLICY_FILE; MDM sources apply the same way) --------------------------------------
    def machine(policy):
        path = root / ("policy-%d.json" % len(list(root.glob("policy-*.json"))))
        path.write_text(json.dumps(policy))
        return {"ANGELO_POLICY_FILE": str(path)}

    client = session(machine({"AllowLocalCLIAccess": False}))
    try:
        result, _ = client.call("angelo_open_file", {"file": str(source)})
        check("machine AllowLocalCLIAccess false: opening is refused", result["isError"], result)
    finally:
        client.close()
    client = session(machine({"RequireAskForEdits": True}))
    try:
        client.call("angelo_open_file", {"file": str(source)})
        result, _ = client.call("angelo_create_cube", CUBE)
        check("machine RequireAskForEdits: a typed edit needs the window", result["isError"]
              and "needs_gui" in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()
    client = session(machine({"AllowAutoApproval": False}))
    try:
        client.call("angelo_open_file", {"file": str(source)})
        result, _ = client.call("angelo_run_script_file", {"file": str(build)})
        check("machine AllowAutoApproval false: the approved script is refused with that reason", result["isError"]
              and "AllowAutoApproval" in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()

    check("no code from the .blend ran", not any(markers.iterdir()), sorted(p.name for p in markers.iterdir()))
    check("the source's bytes never change", sha256(source) == before)
    passed = all(checks.values())   # "success" is the field the suite runner reads (native_suite_manifest.Report).
    report = {"checks": checks, "timings": timings, "binary": str(binary), "success": passed, "passed": passed}
    (out / "headless-script-files.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(timings))
    shutil.rmtree(root, ignore_errors=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
