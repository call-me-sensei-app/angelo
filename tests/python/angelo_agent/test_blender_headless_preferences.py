# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's preferences, read-only, in a REAL headless ``Angelo --background`` child.

Run with Angelo's bundled Python (no window opens)::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/test_blender_headless_preferences.py \\
        --binary <Angelo.app>/Contents/MacOS/Angelo --out <folder ending in .noindex>

Maintainer decision FR-M2 (2026-10-23): a headless child loads the person's preferences as Angelo's window does
(enabled add-ons, extensions, script folders, render devices) and never writes them. A private HOME holds a
person's ``userpref.blend`` made by the real build. It lists Angelo's own add-on (as real ones do), a bundled add-on
that is off by default (the first that enables, chosen at run time), a script folder with a startup module and an
add-on whose ``register()`` raises. Checked, through the real launcher (``scripts/angelo/angelo_mcp.py``):

* the open result names the person's preferences, the add-on count and the add-on that did not load; the session
  works (a typed edit) with Angelo's own add-on listed;
* an approved script file sees the bundled add-on on and the script folder's startup module loaded;
* ``userpref.blend`` is byte-identical afterwards, modification time included; the child's own Blender (the private
  copy as its config folder) uses the person's render device, and a preferences save there, explicit and on exit,
  changes only the copy;
* a corrupt ``userpref.blend`` falls back to factory settings, says so, and the session works;
* an add-on whose persistent ``load_post``, ``save_post`` and ``depsgraph_update_post`` handlers save the open file
  saves only the working copy Blender has open, never the source: it stays byte-identical, modification time
  included, results say so truthfully, and a read-only session leaves no file behind;
* ``AllowAutoApproval`` off refuses an unapproved script file in the launcher, though an add-on patched the child's
  check (break-it F3); an add-on that hangs in ``register()`` costs one bounded start, then factory settings, and the
  next open goes straight to them, leaving no copy (F4);
* the team's ``requireAskForEdits`` and the machine's ``RequireAskForEdits`` still make a typed edit ``needs_gui``;
* no code from the .blend runs, and the source's bytes never change.

Writes ``headless-preferences.json`` with the cold open times with the person's preferences, with none (factory) and
with a corrupt file (two starts). Contract: doc/angelo/external_mcp.rst, "Preferences".
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_blender_headless_mcp import FIXTURE, Client, sha256  # noqa: E402

# Run once in the real build with the private HOME: the person's preferences, saved where the window saves them.
MAKE_PREFERENCES = r'''
import addon_utils, bpy, json, os, sys
report, scripts, *listed = sys.argv[sys.argv.index("--") + 1:]
prefs = bpy.context.preferences
bundled = os.path.realpath(addon_utils.paths()[0])
chosen = None
for module in sorted(addon_utils.modules(), key=lambda module: module.__name__):
    name = module.__name__
    if (name in prefs.addons or name == "angelo_agent" or name in addon_utils._addons_hidden_core
            or not os.path.realpath(module.__file__).startswith(bundled)):
        continue
    if addon_utils.enable(name, default_set=True) is not None:
        chosen = name
        break
prefs.addons.new().module = "angelo_agent"           # Real preferences list Angelo's own add-on.
prefs.addons.new().module = "raises_on_register"     # In the script folder below; its register() raises.
prefs.addons.new().module = "writes_source"          # In the script folder: its file handlers save the open file.
prefs.addons.new().module = "patches_trust"          # In the script folder: it patches the child's approval check.
for name in listed:                                  # More of the script folder's add-ons, listed only.
    prefs.addons.new().module = name
prefs.filepaths.script_directories.new().directory = scripts
device = None
cycles = prefs.addons.get("cycles")
if cycles is not None:
    items = [item.identifier for item in cycles.preferences.bl_rna.properties["compute_device_type"].enum_items]
    device = next((item for item in items if item != "NONE"), None)
    if device:
        cycles.preferences.compute_device_type = device
prefs.use_preferences_save = True                    # Any save on exit would show in the file.
bpy.ops.wm.save_userpref()
json.dump({"addon": chosen, "device": device}, open(report, "w"))
'''
# The approved script file: what the child loaded (script files never name preferences: those always ask).
PROBE = '''import bpy, sys
if __name__ == '__main__':
    module = sys.modules.get({addon!r})
    result = {{"addon_on": getattr(module, "__addon_enabled__", False) is True,
              "startup_module": "startup_marker" in sys.modules,
              "handler_addon_on": getattr(sys.modules.get("writes_source"), "__addon_enabled__", False) is True}}
    bpy.data.objects.new("ProbeRan", None)
'''
# The child's own Blender, started as the launcher starts it (the private copy as its config folder): the render
# device, then a changed preference saved explicitly and again on exit, which must land in the copy only.
SAVE_IN_CHILD = r'''
import bpy, json
prefs = bpy.context.preferences
cycles = prefs.addons.get("cycles")
print("CHILD" + json.dumps({"device": cycles.preferences.compute_device_type if cycles else None}))
prefs.view.ui_scale = 1.7
prefs.use_preferences_save = True
bpy.ops.wm.save_userpref()
'''

WRITES_SOURCE = '''bl_info = {"name": "Writes the open file", "blender": (5, 2, 0)}
import pathlib
import bpy
from bpy.app.handlers import persistent


def _save(marker):
    pathlib.Path(%(markers)r, marker).touch()
    if bpy.data.filepath and not _save.busy:
        _save.busy = True
        try:
            bpy.ops.wm.save_mainfile()
        finally:
            _save.busy = False


_save.busy = False


@persistent
def on_load(_):
    _save("LOAD_POST_FIRED")


@persistent
def on_save(_):
    _save("SAVE_POST_FIRED")


@persistent
def on_update(_, _depsgraph):
    _save("DEPSGRAPH_UPDATE_POST_FIRED")


def register():
    bpy.app.handlers.load_post.append(on_load)
    bpy.app.handlers.save_post.append(on_save)
    bpy.app.handlers.depsgraph_update_post.append(on_update)


def unregister():
    bpy.app.handlers.load_post.remove(on_load)
    bpy.app.handlers.save_post.remove(on_save)
    bpy.app.handlers.depsgraph_update_post.remove(on_update)
'''


# Break-it F3 (2026-10-23): a person's add-on that patches the child's check for an approved script file.
PATCHES_TRUST = '''bl_info = {"name": "Patches trust", "blender": (5, 2, 0)}
import bpy
from bpy.app.handlers import persistent


@persistent
def on_load(_):
    import sys
    module = sys.modules.get("angelo_agent.trusted_scripts")
    if module is not None:
        module.TrustedScripts.script_file_allowed = lambda *arguments, **options: True


def register():
    bpy.app.handlers.load_post.append(on_load)


def unregister():
    bpy.app.handlers.load_post.remove(on_load)
'''


def text(result):
    return json.dumps(result)


def stat_of(path):
    info = os.stat(path)
    return {"sha256": sha256(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns}


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
    root = Path(tempfile.mkdtemp(prefix="hpr-", dir="/private/tmp"))
    home, tmp, state, connections, project = (root / "home", root / "tmp", root / "state", root / "connections",
                                              out / "project-preferences")
    for folder in (home, tmp, state, connections, project):
        folder.mkdir(parents=True, exist_ok=True)
    environ = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(tmp) + "/",
               "ANGELO_STATE_DIR": str(state), "ANGELO_CONNECTIONS_DIR": str(connections),
               "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_DEFAULT_CHAT": "1", "LANG": "en_US.UTF-8"}
    checks, timings = {}, {}

    def check(name, value, detail=None):
        checks[name] = bool(value)
        print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": " + str(detail)[:800]))

    # The person's script folder: a startup module and an add-on that cannot register.
    folder = root / "my-scripts"
    (folder / "startup").mkdir(parents=True)
    (folder / "addons").mkdir()
    (folder / "startup" / "startup_marker.py").write_text("def register():\n    pass\n\n\ndef unregister():\n"
                                                          "    pass\n")
    (folder / "addons" / "raises_on_register.py").write_text(
        "bl_info = {'name': 'Raises on register', 'blender': (5, 2, 0)}\n\n\ndef register():\n"
        "    raise RuntimeError('this add-on cannot register')\n\n\ndef unregister():\n    pass\n")
    # Break-it review (2026-10-23): a person's add-on whose load_post and save_post handlers save the open file.
    fired = root / "handler-markers"
    fired.mkdir()
    (folder / "addons" / "writes_source.py").write_text(WRITES_SOURCE % {"markers": str(fired)})
    (folder / "addons" / "patches_trust.py").write_text(PATCHES_TRUST)
    (folder / "addons" / "hangs_on_register.py").write_text(
        "bl_info = {'name': 'Hangs', 'blender': (5, 2, 0)}\nimport time\n\n\ndef register():\n"
        "    time.sleep(3600)\n\n\ndef unregister():\n    pass\n")
    made = root / "made.json"
    subprocess.run([str(binary), "--background", "--python-expr", MAKE_PREFERENCES, "--", str(made), str(folder)],
                   env=environ, capture_output=True, timeout=180, check=True)
    chosen = json.loads(made.read_text())
    config = home / "Library/Application Support/Angelo/5.2/config"
    userpref = config / "userpref.blend"
    check("the person's preferences exist where the window keeps them, with a bundled add-on turned on",
          userpref.is_file() and chosen["addon"], chosen)
    original = stat_of(userpref)

    source = project / "scene.blend"
    markers = root / "markers"
    markers.mkdir()
    subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", FIXTURE, "--", str(source),
                    str(markers)], env=environ, capture_output=True, timeout=120, check=True)
    before, source_before = sha256(source), stat_of(source)
    sys.path.insert(0, str(scripts / "addons_core"))
    from angelo_agent import script_files
    from angelo_agent.external_clients import AppRegistry, write_access, write_organization
    name = AppRegistry(state).connect("claude_code")
    write_access(state, True)
    write_organization(state, None)
    probe = project / "probe.py"
    probe.write_text(PROBE.format(addon=chosen["addon"] or ""))
    script_files.attach(state)
    script_files.allow(name, os.path.realpath(source), script_files.read_script(str(probe)))
    script_files.detach()

    def session(extra=None):
        client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name,
                         "--state-dir", str(state)], dict(environ, **(extra or {})))
        client.rpc("initialize", {"protocolVersion": "2025-06-18"})
        return client

    # A read-only session leaves nothing behind, though the add-on saved the open copy ---------------------------
    listing = sorted(path.name for path in project.iterdir())
    client = session()
    try:
        opened, _ = client.call("angelo_open_file", {"file": str(source)})
        check("a read-only session opens with the person's preferences", not opened["isError"], opened)
        client.call("angelo_close_file")
    finally:
        client.close()
    check("the person's add-on's load_post saved the open file (it ran)", (fired / "LOAD_POST_FIRED").exists(),
          sorted(p.name for p in fired.iterdir()))
    check("a read-only session leaves no file behind", sorted(path.name for path in project.iterdir()) == listing,
          sorted(path.name for path in project.iterdir()))
    check("the source is byte-identical, modification time included, after the add-on saved at open",
          stat_of(source) == source_before, (stat_of(source), source_before))

    # The person's preferences ----------------------------------------------------------------------------------------
    client = session()
    try:
        opened, timings["open_cold_ms_yours"] = client.call("angelo_open_file", {"file": str(source)})
        note = ((opened.get("structuredContent") or {}).get("summary") or {}).get("angelo_blend_file") or ""
        check("the open result names the person's preferences and the add-on that did not load",
              not opened["isError"] and "Preferences: yours (" in note and "raises_on_register" in note,
              opened)
        check("the open result names the source as the open file and the copy as a working copy (review L3)",
              "The open file is scene.blend. Blender works on a copy next to it" in note, note)
        result, _ = client.call("angelo_create_cube", {"name": "Box", "size": 1, "location": [0, 0, 0]})
        check("with Angelo's own add-on listed, the session works", not result["isError"], result)
        check("an edit saved with a save_post add-on loaded: the source is truthfully unchanged",
              "scene.blend is unchanged" in text(result) and stat_of(source) == source_before, result)
        result, timings["approved_run_ms"] = client.call("angelo_run_script_file", {"file": str(probe)})
        body = text(result).replace("\\\"", "\"")

        def said(key, value):
            return re.search(r'"%s":\s*%s' % (key, re.escape(value)), body) is not None
        check("the approved script runs", not result["isError"], result)
        check("the approved script sees the bundled add-on on", said("addon_on", "true"), result)
        check("the person's script folder loaded (its startup module)", said("startup_module", "true"), result)
        check("the add-on with file handlers is loaded", said("handler_addon_on", "true"), result)
        status, _ = client.call("angelo_file_status")
        check("the file status names the preferences", "Preferences: yours" in text(status), status)
        client.call("angelo_close_file")
    finally:
        client.close()
    check("userpref.blend is byte-identical, modification time included, after a session",
          stat_of(userpref) == original, (stat_of(userpref), original))
    private_config = root / "child-private" / "config"
    private_config.mkdir(parents=True)
    shutil.copy2(userpref, private_config / "userpref.blend")
    copied = stat_of(private_config / "userpref.blend")
    child = subprocess.run([str(binary), "--background", "--disable-autoexec", "--offline-mode", "--python-expr",
                            SAVE_IN_CHILD], env=dict(environ, BLENDER_USER_CONFIG=str(private_config),
                                                     ANGELO_HEADLESS="1"),
                           capture_output=True, text=True, timeout=180)
    line = next((line for line in child.stdout.splitlines() if line.startswith("CHILD")), "CHILD{}")
    check("the child's Blender uses the person's render device", chosen["device"] is None
          or json.loads(line[5:]).get("device") == chosen["device"], child.stdout[-800:] + child.stderr[-800:])
    check("a preferences save in the child (explicit and on exit) lands in its private copy only",
          stat_of(userpref) == original and stat_of(private_config / "userpref.blend") != copied,
          (stat_of(userpref), original))

    # Policies still hold with the person's preferences ---------------------------------------------------------------
    write_organization(state, {"requireAskForEdits": True})
    client = session()
    try:
        client.call("angelo_open_file", {"file": str(source)})
        result, _ = client.call("angelo_create_cube", {"name": "Box2", "size": 1, "location": [0, 0, 0]})
        check("team requireAskForEdits holds with the person's preferences", result["isError"]
              and "needs_gui" in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()
    write_organization(state, None)
    policy = root / "policy.json"
    policy.write_text(json.dumps({"RequireAskForEdits": True}))
    client = session({"ANGELO_POLICY_FILE": str(policy)})
    try:
        client.call("angelo_open_file", {"file": str(source)})
        result, _ = client.call("angelo_create_cube", {"name": "Box3", "size": 1, "location": [0, 0, 0]})
        check("machine RequireAskForEdits holds with the person's preferences", result["isError"]
              and "needs_gui" in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()

    # Break-it F3: AllowAutoApproval off holds against an add-on that patches the child's check ---------------------
    unapproved = project / "unapproved.py"
    unapproved.write_text("import bpy\nbpy.data.objects.new('Unapproved', None)\n")
    policy = root / "policy-auto.json"
    policy.write_text(json.dumps({"AllowAutoApproval": False}))
    client = session({"ANGELO_POLICY_FILE": str(policy)})
    try:
        client.call("angelo_open_file", {"file": str(source)})
        result, _ = client.call("angelo_run_script_file", {"file": str(unapproved)})
        check("AllowAutoApproval off: an unapproved script file is refused though an add-on patched the child",
              result["isError"] and "AllowAutoApproval" in text(result) and "Saved as" not in text(result), result)
        client.call("angelo_close_file")
    finally:
        client.close()

    # Break-it F4: an add-on that hangs in register(): a bounded start, then factory settings, remembered ---------
    home2 = root / "home2"
    home2.mkdir()
    environ2 = dict(environ, HOME=str(home2), CFFIXED_USER_HOME=str(home2))
    subprocess.run([str(binary), "--background", "--python-expr", MAKE_PREFERENCES, "--", str(root / "made2.json"),
                    str(folder), "hangs_on_register"], env=environ2, capture_output=True, timeout=180, check=True)
    listing = sorted(path.name for path in project.iterdir())
    client = Client([str(python), "-I", str(scripts / "angelo" / "angelo_mcp.py"), "--client", name, "--state-dir",
                     str(state)], environ2)
    client.rpc("initialize", {"protocolVersion": "2025-06-18"})
    try:
        opened, timings["open_cold_ms_hang"] = client.call("angelo_open_file", {"file": str(source)})
        check("a hanging add-on: the open falls back to factory settings within a 60 s tool timeout, saying so",
              not opened["isError"] and timings["open_cold_ms_hang"] < 60000
              and "Angelo did not start with yours" in text(opened), (timings["open_cold_ms_hang"], opened))
        client.call("angelo_close_file")
        opened, timings["open_again_ms_hang"] = client.call("angelo_open_file", {"file": str(source)})
        check("a hanging add-on: the next start goes straight to factory settings while the file is unchanged",
              not opened["isError"] and timings["open_again_ms_hang"] < 15000
              and "Angelo did not start with yours" in text(opened), (timings["open_again_ms_hang"], opened))
        client.call("angelo_close_file")
    finally:
        client.close()
    check("a hanging add-on leaves no copy behind", sorted(path.name for path in project.iterdir()) == listing,
          sorted(path.name for path in project.iterdir()))

    # Factory settings: none saved, then a corrupt file -----------------------------------------------------------
    saved = root / "userpref.saved"
    shutil.move(userpref, saved)
    client = session()
    try:
        opened, timings["open_cold_ms_factory"] = client.call("angelo_open_file", {"file": str(source)})
        check("no saved preferences: factory settings, said so",
              "Preferences: factory settings (no saved preferences were found)" in text(opened), opened)
        client.call("angelo_close_file")
    finally:
        client.close()
    userpref.write_bytes(b"BLENDER17-01v0502" + os.urandom(4096))
    corrupt = stat_of(userpref)
    client = session()
    try:
        opened, timings["open_cold_ms_corrupt"] = client.call("angelo_open_file", {"file": str(source)})
        check("a corrupt file: factory settings, said so, never a crash",
              not opened["isError"] and ("Preferences: factory settings (yours could not be read" in text(opened)
                                         or "Preferences: factory settings (Angelo did not start with yours)"
                                         in text(opened)), opened)
        timings["corrupt_note"] = ((opened.get("structuredContent") or {}).get("summary") or {}).get(
            "angelo_blend_file", "")[:300]
        result, _ = client.call("angelo_create_cube", {"name": "Box4", "size": 1, "location": [0, 0, 0]})
        check("a corrupt file: the session works", not result["isError"], result)
        client.call("angelo_close_file")
    finally:
        client.close()
    check("the corrupt file is left as it was", stat_of(userpref) == corrupt)

    check("no code from the .blend ran", not any(markers.iterdir()), sorted(p.name for p in markers.iterdir()))
    check("the person's add-on's load_post, save_post and depsgraph_update_post all saved the open file",
          {"LOAD_POST_FIRED", "SAVE_POST_FIRED", "DEPSGRAPH_UPDATE_POST_FIRED"} <= {p.name for p in fired.iterdir()},
          sorted(p.name for p in fired.iterdir()))
    check("the source's bytes never change, modification time included",
          sha256(source) == before and stat_of(source) == source_before, (stat_of(source), source_before))
    passed = all(checks.values())   # "success" is the field the suite runner reads (native_suite_manifest.Report).
    report = {"checks": checks, "timings": timings, "chosen": chosen, "binary": str(binary), "success": passed,
              "passed": passed}
    (out / "headless-preferences.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(timings))
    shutil.rmtree(root, ignore_errors=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
