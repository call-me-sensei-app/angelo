# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless MCP on a built Angelo: the real launcher, a real background child and a real .blend.

Run with Angelo's bundled Python (no window opens; take ``/tmp/angelo-billed.lock`` on a shared Mac)::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/test_blender_headless_mcp.py \\
        --binary <Angelo.app>/Contents/MacOS/Angelo --out <folder ending in .noindex>

Private HOME, TMPDIR and Angelo state. It saves a fixture scene, connects an app, starts the app's launcher
(``scripts/angelo/angelo_mcp.py``) as an MCP client would and checks, over stdio: no window and no file answers
``no_file``; ``angelo_open_file``; reads, a view (an image), a bounded edit and a second edit saved to ONE new file
next to the source; Python, a job, a preview and a task answer ``needs_gui``; neither a registered text block nor a
Python driver in the file ever runs, and no Blender profile file (recent files, quit.blend, preferences) is written;
the source's bytes never change, no
``.blend1`` appears, the output reopens in a separate Blender with the edits, the child opens no network listener,
and closing ends the child and removes its private state. Writes ``headless-mcp.json`` with timings.
Contract: doc/angelo/external_mcp.rst, "Headless".
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = r'''
import bpy, sys
out, markers = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=False)
bpy.data.objects["Cube"].name = "Pedestal"
# Code a file can carry that must never run headless (break-it review, 2026-10-23): a registered text block
# and a Python driver. Each leaves a marker if it ever runs.
text = bpy.data.texts.new("autorun.py")
text.use_module = True
text.write("import pathlib\npathlib.Path(%r, 'TEXT_BLOCK_RAN').touch()\n" % markers)
driver = bpy.data.objects["Pedestal"].driver_add("location", 2).driver
driver.type = 'SCRIPTED'
driver.expression = "__import__('pathlib').Path(%r, 'DRIVER_RAN').touch() or 0" % markers
bpy.ops.wm.save_as_mainfile(filepath=out)
'''
# Blender's own files in the person's profile; a headless session writes none of them.
PROFILE_FILES = {"recent-files.txt", "quit.blend", "userpref.blend", "startup.blend", "bookmarks.txt"}
CHECK = r'''
import bpy, json, sys
box = bpy.data.objects.get("Box")
print("CHECK" + json.dumps({"objects": sorted(o.name for o in bpy.data.objects),
                            "box": list(box.location) if box else None}))
'''


class Client:
    """A plain MCP stdio client: one JSON-RPC line out, wait for the answer with that id."""

    def __init__(self, command, environ):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=environ, text=True, bufsize=1)
        self._next, self._answers, self._lock = 0, {}, threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            message = json.loads(line)
            with self._lock:
                self._answers[message.get("id")] = message
                self._lock.notify_all()

    def rpc(self, method, params=None, timeout=180):
        self._next += 1
        identity = self._next
        started = time.perf_counter()
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identity, "method": method,
                                             "params": params or {}}) + "\n")
        self.process.stdin.flush()
        with self._lock:
            if not self._lock.wait_for(lambda: identity in self._answers, timeout):
                raise TimeoutError(method)
            answer = self._answers.pop(identity)
        return answer, round((time.perf_counter() - started) * 1000, 1)

    def call(self, tool, arguments=None):
        answer, elapsed = self.rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        return answer["result"], elapsed

    def close(self):
        self.process.stdin.close()
        self.process.wait(30)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def children_of(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(value) for value in out]


def network_listeners(pid):
    out = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-i"], capture_output=True, text=True).stdout
    return [line for line in out.splitlines()[1:] if line.strip()]


def private_directories(home, tmp, environ):
    """Find this fixture's child state, including the allocator's temporary-folder fallback.

    Pass the child's HOME explicitly: the test process still has the person's HOME. Never allocate or clean
    anything here; an empty or ambiguous discovery must fail the ownership assertion before close.
    """
    from angelo_agent import private_roots, storage
    roots = (storage.data_directory(environ, home=home) / private_roots.SCRATCH, Path(tmp))
    found = set()
    for root in roots:
        if root.is_dir():
            found.update(root.glob(private_roots.HEADLESS_PREFIX + "*"))
    return sorted(found)


def one_private_directory(paths):
    return len(paths) == 1 and paths[0].is_dir() and not paths[0].is_symlink()


def private_state_removed(paths):
    # lexists also rejects a dangling link left at the recorded path. An empty discovery proves nothing.
    return len(paths) == 1 and not os.path.lexists(paths[0])


def gui_phase(binary, python, scripts, environ, root, source, state, name, check):
    """Angelo's window has the file open with access on: opening it routes the session to the window."""
    sys.path.insert(0, str(HERE))
    import gui_launch
    ready, stop = root / "gui-ready", root / "gui-stop"
    environ = dict(environ, ANGELO_CLI_RUNTIME_DIR=str(root / "run"))
    log = (root / "gui.log").open("w")
    window = subprocess.Popen([str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags("behind"),
                               "--window-geometry", "80", "80", "1100", "700", str(source), "--python",
                               str(HERE / "bench_external_mcp_latency.py"), "--", "--native-phase", "gui",
                               "--ready", str(ready), "--stop", str(stop)], env=environ, stdout=log,
                              stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 180
        while not ready.is_file() and window.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        check("the window listens", ready.is_file())
        client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name,
                         "--state-dir", str(state)], environ)
        try:
            client.rpc("initialize", {"protocolVersion": "2025-06-18"})
            opened, _ = client.call("angelo_open_file", {"file": str(source)})
            check("a file open in the window routes the session to it",
                  (opened.get("structuredContent") or {}).get("in_angelo_window") is True, opened)
            check("no child is started for it", not children_of(client.process.pid))
            result, _ = client.call("angelo_inspect_scene")
            check("calls then reach the window's live document", not result["isError"] and "Pedestal" in json.dumps(
                result), result)
        finally:
            client.close()
    finally:
        stop.write_text("1")
        try:
            window.wait(60)
        except subprocess.TimeoutExpired:
            window.kill()
        log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--gui", action="store_true", help="also check routing to Angelo's window (opens one, behind)")
    options = parser.parse_args()
    binary = Path(options.binary).resolve()
    contents = binary.parents[1]
    python = next((contents / "Resources").glob("*/python/bin/python3.*[0-9]"))
    scripts = next((contents / "Resources").glob("*/scripts"))
    out = Path(options.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hl-", dir="/private/tmp"))
    home, tmp, state, project = root / "home", root / "tmp", root / "state", out / "project"
    for folder in (home, tmp, state, project):
        folder.mkdir(parents=True, exist_ok=True)
    environ = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(tmp) + "/",
               "ANGELO_STATE_DIR": str(state), "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_DEFAULT_CHAT": "1",
               "LANG": "en_US.UTF-8"}
    checks, timings = {}, {}

    def check(name, value, detail=None):
        checks[name] = bool(value)
        print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": " + str(detail)[:400]))

    source = project / "scene.blend"
    markers = root / "markers"
    markers.mkdir()
    subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", FIXTURE, "--", str(source),
                    str(markers)], env=environ, capture_output=True, timeout=120, check=True)
    home_before = {path for path in home.rglob("*")}
    before = sha256(source)
    sys.path.insert(0, str(scripts / "addons_core"))
    from angelo_agent.external_clients import AppRegistry, write_access, write_organization
    name = AppRegistry(state).connect("claude_code")
    write_access(state, True)   # What Angelo's window records while "Allow apps on this computer" is on.
    write_organization(state, None)   # And while signed out: no organization (a missing record is the strictest).
    client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name,
                     "--state-dir", str(state)], environ)
    try:
        init, timings["initialize_ms"] = client.rpc("initialize", {"protocolVersion": "2025-06-18"})
        check("initialize names the open-file tool", "angelo_open_file" in init["result"]["instructions"])
        tools = {tool["name"] for tool in client.rpc("tools/list")[0]["result"]["tools"]}
        check("tools/list offers the file tools", {"angelo_open_file", "angelo_file_status", "angelo_close_file"} <= tools)
        result, _ = client.call("angelo_inspect_scene")
        check("no window and no file answers no_file", (result.get("structuredContent") or {}).get("code") == "no_file",
              result)
        opened, timings["open_cold_ms"] = client.call("angelo_open_file", {"file": str(source)})
        check("angelo_open_file opens the file", not opened["isError"] and "Pedestal" in json.dumps(opened), opened)
        child = children_of(client.process.pid)
        check("one child process", len(child) == 1, child)
        check("the child opens no network listener", child and not network_listeners(child[0]),
              child and network_listeners(child[0]))
        reads = []
        for _ in range(10):
            result, elapsed = client.call("angelo_inspect_scene")
            reads.append(elapsed)
        timings["inspect_scene_ms"] = reads
        check("reads work", not result["isError"] and "Pedestal" in json.dumps(result), result)
        result, timings["scene_search_ms"] = client.call("angelo_scene_search", {"query": "Pedestal"})
        check("search works", not result["isError"], result)
        result, timings["blend_file_ms"] = client.call("angelo_blend_file")
        check("the file summary works", not result["isError"], result)
        result, timings["view_ms"] = client.call("angelo_view", {"views": ["ISO"]})
        check("a view returns an image", not result["isError"] and any(
            block.get("type") == "image" for block in result.get("content", [])), json.dumps(result)[:600])
        result, timings["create_cube_ms"] = client.call("angelo_create_cube",
                                                        {"name": "Box", "size": 1, "location": [2, 0, 0]})
        saved = [block["text"] for block in result.get("content", []) if block.get("type") == "text"
                 and block["text"].startswith("Saved as ")]
        check("an edit is saved to a new file and says so", not result["isError"] and saved, result)
        output = Path(saved[0].split("Saved as ", 1)[1].split(" (", 1)[0]) if saved else None
        result, timings["set_transform_ms"] = client.call("angelo_set_transform", {"object": "Box", "location": [3, 1, 0]})
        check("a second edit saves the same output", output is not None and ("Saved as %s" % output) in json.dumps(result),
              result)
        # Headless Python (maintainer decision 2026-10-23): only a script file the person always allows while it is
        # unchanged, as the window saved it, runs; any other file is refused with how to approve it (break-it review
        # H1: the in-process tests could not show that a real child had no operator to apply it).
        approved, unapproved = project.parent / "build.py", project.parent / "other.py"
        approved.write_text("import bpy\nbpy.data.objects.new('FromScript', None)\n"
                            "bpy.context.scene.collection.objects.link(bpy.data.objects['FromScript'])\n"
                            "result = {'made': 'FromScript'}\n")
        unapproved.write_text("result = {'made': None}\n")
        from angelo_agent import script_files
        script_files.attach(state)
        script_files.allow(name, os.path.realpath(source), script_files.read_script(str(approved)))
        script_files.detach()
        result, timings["script_file_ms"] = client.call("angelo_run_script_file", {"file": str(approved)})
        check("an approved script file runs headless and is saved to the output",
              not result["isError"] and output is not None and ("Saved as %s" % output) in json.dumps(result), result)
        result, _ = client.call("angelo_run_script_file", {"file": str(unapproved)})
        check("an unapproved script file answers needs_gui with how to approve it",
              result["isError"] and "needs_gui" in json.dumps(result) and "Always allow this file" in json.dumps(result),
              result)
        for tool, arguments in (("angelo_execute_python", {"description": "x", "code": "result = 1"}),
                                ("angelo_job_start", {"kind": "RENDER"}),
                                ("angelo_scene_program", {"mode": "PREVIEW", "operations": []}),
                                ("angelo_task", {"task": "make a bridge"})):
            result, _ = client.call(tool, arguments)
            check(tool + " answers needs_gui", result["isError"] and "needs_gui" in json.dumps(result), result)
        status, _ = client.call("angelo_file_status")
        check("status names the output", output is not None and status["structuredContent"]["output"] == str(output),
              status)
        private = private_directories(home, tmp, environ)
        owned_private = one_private_directory(private)
        check("exactly one child's private state directory is observed before close", owned_private, private)
        closed, _ = client.call("angelo_close_file")
        time.sleep(1.0)
        check("closing ends the child", child and subprocess.run(["kill", "-0", str(child[0])],
                                                                 capture_output=True).returncode != 0)
        check("the child's private state is removed", owned_private and private_state_removed(private), private)
    finally:
        client.close()
    check("no code from the file ran (registered text block, Python driver)", not any(markers.iterdir()),
          sorted(path.name for path in markers.iterdir()))
    written = sorted(str(path.relative_to(home)) for path in home.rglob("*")
                     if path not in home_before and path.name in PROFILE_FILES)
    check("nothing written to Blender's files in the person's profile", not written, written)
    check("the source's bytes never change", sha256(source) == before)
    names = sorted(path.name for path in project.iterdir())
    check("only the source and one output in the folder", output is not None and names == sorted(
        ["scene.blend", output.name]), names)
    if output is not None:
        reopened = subprocess.run([str(binary), "--background", "--factory-startup", str(output), "--python-expr",
                                   CHECK], env=environ, capture_output=True, text=True, timeout=120)
        line = next((line for line in reopened.stdout.splitlines() if line.startswith("CHECK")), "CHECK{}")
        data = json.loads(line[5:])
        check("the output reopens with both edits", data.get("box") == [3.0, 1.0, 0.0] and "Pedestal" in data.get(
            "objects", []), data)
        check("the output holds what the approved script made", "FromScript" in data.get("objects", []), data)
    if options.gui:
        gui_phase(binary, python, scripts, environ, root, source, state, name, check)
        check("the window never changed the source", sha256(source) == before)
    passed = all(checks.values())   # "success" is the field the suite runner reads (native_suite_manifest.Report).
    report = {"checks": checks, "timings": timings, "binary": str(binary), "success": passed, "passed": passed}
    (out / "headless-mcp.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(timings))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
