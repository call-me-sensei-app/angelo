# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An asset file changed within the same second is a new version: linking from it packed takes the new content.

Blender knows packed linked data by a deep hash built on its source file's hash, which it cached by the file's path
and modification time in whole seconds (blenkernel/intern/id_hash.cc). An asset saved again within one second (a
script, an agent: link, edit, save, link again) kept its old hash: linking it packed returned the datablock packed
from the old version (reading deduplicates packed data by deep hash), and Undo did the same. Found while fixing the
Redo crash on packed linked data (doc/angelo/agent_execution.rst).

Each case, in a new session: an asset library holding a node group whose frame says "AAAA" is linked and packed;
the file then changes to a version that says "BBBB", within the same second; the node group is linked and packed
again. The second must say "BBBB" and be another datablock than the first, which still says "AAAA". The cases
cover the three ways the change can look: copied over with another size and a time later by half a second; copied
over with the same size and the very same time to the nanosecond (only the hash's age tells: the file changed
within two seconds of being hashed); and written by Blender itself with the same size and time (a new file at the
path: Blender writes a temporary file and renames it).

And a library reached through a symbolic link (``latest.blend -> v1.blend``, as asset versioning often does) packs
the right content, and the file behind it is read once, then not again while it is unchanged: a child Blender
counts the "Hashing library file" lines of ``--log "lib.id_hash"``. Skipped where symbolic links cannot be made.
(That an ID read from an older version never takes a newer version's hash is the gtest IDHashSourceFileTest.)

The add-on is disabled first. Run with ``--background --factory-startup --python-exit-code 1``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-packed-link-same-second-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "packed-link-same-second.json"
report = {"success": False, "blender": bpy.app.version_string, "cases": {}, "running": None}
CASES = ("copied, other size, half a second later", "copied, same size and time",
         "written by Blender (a new file at the path)", "through a symbolic link")


def save_report():
    REPORT.write_text(json.dumps(report, indent=2))


def write_version(path, label, extra_node):
    """A library file whose node group "Kit Asset" has a frame with `label`."""
    group = bpy.data.node_groups.new("Kit Asset", "ShaderNodeTree")
    group.nodes.new("NodeFrame").label = label
    if extra_node:
        group.nodes.new("NodeReroute")
    bpy.data.libraries.write(str(path), {group}, fake_user=True)
    bpy.data.node_groups.remove(group)


def link_packed(path):
    before = set(bpy.data.node_groups)
    with bpy.data.libraries.load(str(path), link=True, pack=True) as (_source, target):
        target.node_groups = ["Kit Asset"]
    group = target.node_groups[0]
    return group, group not in before


def label(group):
    return next(node.label for node in group.nodes if node.bl_idname == "NodeFrame")


def case(name):
    report["running"] = name
    facts = report["cases"][name] = {}
    save_report()
    folder = OUT / name.replace(", ", "-").replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    asset, first, second = folder / "asset-library.blend", folder / "first.blend", folder / "second.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    write_version(first, "AAAA", False)
    write_version(second, "BBBB", name.startswith("copied, other size"))
    shutil.copyfile(first, asset)
    # A whole second, so that half a second later stays in it. Ten seconds old (a hash of it is trusted) but for
    # the case only the hash's age can tell: a file that changed within two seconds of being hashed.
    stamp = (time.time_ns() if name == "copied, same size and time" else   # Exact: the whole two-second window.
             time.time_ns() // 1_000_000_000 * 1_000_000_000 - 10_000_000_000)
    os.utime(asset, ns=(stamp, stamp))

    old, _new = link_packed(asset)
    facts["first"] = label(old)
    if name.startswith("written by Blender"):
        write_version(asset, "BBBB", False)
    else:
        shutil.copyfile(second, asset)
    later = stamp + (500_000_000 if name.startswith("copied, other size") else 0)
    os.utime(asset, ns=(later, later))
    facts["same_size"] = first.stat().st_size == asset.stat().st_size
    facts["same_second"] = later // 1_000_000_000 == stamp // 1_000_000_000

    group, is_new = link_packed(asset)
    facts.update(second=label(group), another_datablock=is_new and group != old, first_after=label(old),
                 packed=group.is_linked_packed)
    save_report()
    assert facts["same_second"], "the test did not keep both versions in one second"
    if name == "copied, same size and time":
        assert facts["same_size"], "the two versions differ in size: the case would not test the time rule"
    assert facts["second"] == "BBBB" and facts["another_datablock"], (
        "linking the changed file packed returned the old version: %s" % json.dumps(facts))
    assert facts["first_after"] == "AAAA" and facts["packed"], "the first version changed: %s" % json.dumps(facts)
    facts["passed"] = True


SYMLINK_GROUPS = ["Kit %d" % index for index in range(5)]


def symlink_child(folder):
    """In the child: pack five node groups through the link, twice; prints what it got."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    latest = folder / "latest.blend"
    for call in (1, 2):
        print("ANGELO_HASH_CALL", call, flush=True)
        with bpy.data.libraries.load(str(latest), link=True, pack=True) as (_source, target):
            target.node_groups = list(SYMLINK_GROUPS)
        print("ANGELO_SYMLINK_GOT", json.dumps(sorted((group.name, label(group), group.is_linked_packed)
                                                      for group in target.node_groups)), flush=True)
        for group in list(target.node_groups):
            bpy.data.node_groups.remove(group)


def through_symlink(name):
    import subprocess
    report["running"] = name
    facts = report["cases"][name] = {}
    save_report()
    folder = OUT / "symlink"
    folder.mkdir(parents=True, exist_ok=True)
    version, latest = folder / "v1.blend", folder / "latest.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    groups = set()
    for group_name in SYMLINK_GROUPS:
        group = bpy.data.node_groups.new(group_name, "ShaderNodeTree")
        group.nodes.new("NodeFrame").label = "AAAA"
        groups.add(group)
    bpy.data.libraries.write(str(version), groups, fake_user=True)
    old = time.time_ns() - 10_000_000_000   # Unchanged for a while: a hash of it can be trusted.
    os.utime(version, ns=(old, old))
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    try:
        os.symlink(version.name, latest)
    except (OSError, NotImplementedError) as error:
        facts.update(skipped="no symbolic links here: %s" % error, passed=True)
        return
    time.sleep(2.5)   # Its status change time (set with the times above) is two seconds old: a hash can be trusted.
    log = folder / "child.txt"
    with open(log, "wb") as out:
        code = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "1",
                               "--log", "lib.id_hash", "--log-level", "debug", "--python",
                               os.path.abspath(__file__), "--", "--symlink-child", str(folder)],
                              stdout=out, stderr=subprocess.STDOUT, timeout=600,
                              env=dict(os.environ, ANGELO_QA_DIR=str(folder / "child-qa"))).returncode
    text = log.read_text(errors="replace")
    calls = text.split("ANGELO_HASH_CALL")[1:]
    facts.update(exit=code, hashes_per_call=[call.count("Hashing library file") for call in calls],
                 got=[line.split(" ", 1)[1] for line in text.splitlines() if line.startswith("ANGELO_SYMLINK_GOT")])
    save_report()
    expected = json.dumps(sorted((group_name, "AAAA", True) for group_name in SYMLINK_GROUPS))
    assert code == 0 and facts["got"] == [expected, expected], "packing through the link: %s" % json.dumps(facts)
    assert facts["hashes_per_call"] == [1, 0], (
        "the file behind the link was hashed %s times (once per call, then not while unchanged)" % (
            facts["hashes_per_call"]))
    facts["passed"] = True


def run():
    addon_utils.disable("angelo_agent", default_set=False)   # Blender only.
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--symlink-child" in arguments:
        symlink_child(Path(arguments[arguments.index("--symlink-child") + 1]))
        report["success"] = True
        return
    for name in CASES[:3]:
        case(name)
    through_symlink(CASES[3])
    report["running"] = None
    report["success"] = all(facts.get("passed") for facts in report["cases"].values()) and len(
        report["cases"]) == len(CASES)


try:
    run()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    save_report()
    print("ANGELO_PACKED_LINK_SAME_SECOND", json.dumps({"success": report["success"], "running": report["running"],
                                                       "error": (report.get("error") or "")[-600:]}), flush=True)
    if not report["success"]:
        sys.exit(1)
