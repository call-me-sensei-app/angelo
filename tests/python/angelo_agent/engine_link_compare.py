# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link tools against the script route (doc/angelo/engine_link.rst, "The agent"): the briefs, the Godot
project, and the closed checks. Standard library only; the in-app half is ``engine_link_compare_session``.

The design was approved as written by the maintainer on 2026-11-02 and frozen before any timed trial (its file's
SHA-256 is ``DESIGN_SHA256``); the readings of its checks below were sent for confirmation before any run. Both
arms run the same build, briefs, harness and grading; the script-route arm withholds the two tools
(``ANGELO_WITHHOLD_TOOLS``, ``--withhold-tools``), so its model sees what 1.0.8's did. Run through
``eval_live_agent.py --task-set engine-link-compare --new-chat-per-task``.

* Setup before each brief (untimed, both arms): the crate restored from its pristine file and linked at its starting
  size, as the person's Edit in Angelo in Godot does; before brief 2 the crate is also made 1.2x taller (what brief 1
  leaves), before brief 3 10% wider, waiting until Godot shows it.
* Brief 4: once the turn ends, the harness answers the first request card for the barrel with its primary button
  (Open; Save and Open when the file is unsaved), as a person would, in both arms.
* Checks (``check_*``): closed facts from Godot's recorder (``godot_link_e2e/eval_driver``), the file on disk, the
  link's own record and the turn's final reply.
"""

import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

TASK_SET = "engine-link-compare"
DESIGN_SHA256 = "deeaec3caa2ad70a0d71b68955ab40da685ee22ffa8c7c3e117c96181393768b"
WITHHELD = "angelo_engine_link_status,angelo_engine_link"   # The script-route arm.
TALLER, WIDER, TOLERANCE_M = 1.2, 1.1, 0.001
ELAPSED_RANGE = (0.5, 2.0)
CRATE, BARREL = "res://crate.glb", "res://barrel.glb"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PLUGIN = ROOT / "scripts/addons_core/angelo_agent/godot_addon/angelo_link"
RECORDER = HERE / "godot_link_e2e/eval_driver/angelo_link_eval"

# (id, prompt, the state setup leaves): the briefs exactly as approved.
BRIEFS = (("push", "Make the crate 20% taller and send it to Godot now.", "start"),
          ("save", "Save the crate so Godot gets it, and tell me once Godot has re-imported it.", "taller"),
          ("status", "What is linked to my game engine right now, and when did Godot last get an update?", "wider"),
          ("open", "Open the barrel from my Godot project here.", "start"))

MAKE_ASSETS = """
import bpy, sys
folder = sys.argv[-1]
for name, add in (("Crate", lambda: bpy.ops.mesh.primitive_cube_add(size=2)),
                  ("Barrel", lambda: bpy.ops.mesh.primitive_cylinder_add(radius=0.6, depth=1.5))):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    add()
    bpy.context.object.name = name
    bpy.ops.export_scene.gltf(filepath="%s/%s.glb" % (folder, name.lower()), export_format="GLB")
"""
PROJECT = """config_version=5

[application]

config/name="Angelo Link Compare"

[editor_plugins]

enabled=PackedStringArray("res://addons/angelo_link/plugin.cfg", "res://addons/angelo_link_eval/plugin.cfg")
"""
SCENE = """[gd_scene load_steps=3 format=3]

[ext_resource type="PackedScene" path="res://crate.glb" id="1_crate"]
[ext_resource type="PackedScene" path="res://barrel.glb" id="2_barrel"]

[node name="Main" type="Node3D"]

[node name="Crate" parent="." instance=ExtResource("1_crate")]

[node name="Barrel" parent="." instance=ExtResource("2_barrel")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 4, 0, 0)
"""


def tasks():
    """The harness task list: one edit task per brief, graded by this module's closed check of that brief, which
    ``scenario_checks`` names (the session's ``grade`` applies it; nothing else evaluates this set)."""
    return [{"id": brief, "kind": "edit", "requires_object": False, "prompt": prompt, "checks": [],
             "scenario": TASK_SET, "scenario_checks": [{"check": "engine_link_compare.check_" + brief}],
             "references": [], "title": brief, "setup": state,
             "source": "engine link compare brief %d: %s" % (number, brief)}
            for number, (brief, prompt, state) in enumerate(BRIEFS, 1)]


def environment(godot, withheld):
    """What the trial's process needs: Godot, and the arm (the tools withheld, or none)."""
    return {"ANGELO_QA_GODOT": str(godot), "ANGELO_WITHHOLD_TOOLS": WITHHELD if withheld else ""}


def build_project(folder, angelo, godot, env):
    """A throwaway Godot project with the shipped plugin, the recorder, crate.glb and barrel.glb, imported once; the
    pristine assets are kept in ``originals`` for each brief's setup. Returns the project folder."""
    project = folder / "project"
    (project / "addons").mkdir(parents=True)
    shutil.copytree(PLUGIN, project / "addons/angelo_link")
    shutil.copytree(RECORDER, project / "addons/angelo_link_eval")
    (project / "project.godot").write_text(PROJECT, encoding="utf-8")
    subprocess.run([angelo, "--background", "--factory-startup", "--python-expr", MAKE_ASSETS, "--", str(project)],
                   check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    (project / "main.tscn").write_text(SCENE, encoding="utf-8")
    originals = folder / "originals"
    originals.mkdir()
    for name in ("crate.glb", "barrel.glb"):
        shutil.copy2(project / name, originals / name)
    subprocess.run([godot, "--headless", "--path", str(project), "--import"], env=env, timeout=300,
                   stdout=(folder / "godot-import.log").open("w"), stderr=subprocess.STDOUT)
    return project


# The .glb on disk --------------------------------------------------------------------------------------------------

def _matrix(node):
    if "matrix" in node:
        m = node["matrix"]
        return [[m[column * 4 + row] for column in range(4)] for row in range(4)]
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    rotation = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    scaled = [[rotation[row][column] * (sx, sy, sz)[column] for column in range(3)] for row in range(3)]
    return [scaled[0] + [tx], scaled[1] + [ty], scaled[2] + [tz], [0.0, 0.0, 0.0, 1.0]]


def _multiply(a, b):
    return [[sum(a[row][k] * b[k][column] for k in range(4)) for column in range(4)] for row in range(4)]


def glb_bounds(path):
    """(min, max) world-space corners of every mesh in a .glb (glTF's Y up), from its accessors' bounds."""
    data = Path(path).read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + length])
    nodes, meshes, accessors = document.get("nodes", []), document.get("meshes", []), document.get("accessors", [])
    low, high = [float("inf")] * 3, [float("-inf")] * 3

    def visit(index, parent):
        node = nodes[index]
        world = _multiply(parent, _matrix(node))
        if "mesh" in node:
            for primitive in meshes[node["mesh"]]["primitives"]:
                accessor = accessors[primitive["attributes"]["POSITION"]]
                for corner in range(8):
                    point = [accessor["max"][axis] if corner >> axis & 1 else accessor["min"][axis] for axis in range(3)]
                    moved = [sum(world[row][k] * (point + [1.0])[k] for k in range(4)) for row in range(3)]
                    for axis in range(3):
                        low[axis], high[axis] = min(low[axis], moved[axis]), max(high[axis], moved[axis])
        for child in node.get("children", ()):
            visit(child, world)

    identity = [[float(row == column) for column in range(4)] for row in range(4)]
    for root in document["scenes"][document.get("scene", 0)]["nodes"]:
        visit(root, identity)
    return low, high


def glb_height(path):
    low, high = glb_bounds(path)
    return high[1] - low[1]


# The checks (the readings sent for confirmation before any run) ----------------------------------------------------

def check_push(godot, sent_at, start_height, turn_end):
    """1: Godot showed a live preview of the crate, after the message, 1.2x the starting height (within 1 mm). Only
    the turn's own previews count, by Godot's time, as check_save's re-import does: the next brief's setup pushes."""
    shown = [item for item in godot.get("previews", ())
             if item["asset"] == CRATE and sent_at <= item["at"] <= turn_end]
    passed = any(abs(item["height"] - TALLER * start_height) <= TOLERANCE_M for item in shown)
    return {"passed": passed, "previews_after_send": len(shown),
            "heights": [round(item["height"], 4) for item in shown][-5:], "expected": round(TALLER * start_height, 4)}


def check_save(path, start_height, godot, sent_at, turn_end):
    """2: the .glb on disk is 1.2x tall (within 1 mm); Godot re-imported it after the message; the turn's reply ended
    after that re-import."""
    height = glb_height(path)
    reimports = [item["at"] for item in godot.get("reimports", ()) if item["asset"] == CRATE and item["at"] >= sent_at]
    file_ok = abs(height - TALLER * start_height) <= TOLERANCE_M
    after = bool(reimports) and min(reimports) <= turn_end
    return {"passed": file_ok and after, "file_height": round(height, 4), "expected": round(TALLER * start_height, 4),
            "reimported": bool(reimports), "reply_after_reimport": after}


# Seconds, minutes, hours; never a bare "m", which a reply uses for metres ("2.4 m tall").
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|hours?|hrs?|h)\b", re.IGNORECASE)
_VERSION = re.compile(r"(?:\bversion\b|\bv|#)\s*#?\s*(\d+)\b", re.IGNORECASE)
_UNIT = {"s": 1, "m": 60, "h": 3600}


def check_status(reply, version, elapsed_s):
    """3: the reply names the crate and Godot, and either states the time since the last update within 0.5-2x of the
    true seconds, or names that live scene's version."""
    text = reply or ""
    names = "crate" in text.lower() and "godot" in text.lower()
    stated = [float(value) * _UNIT[unit[0].lower()] for value, unit in _DURATION.findall(text)]
    timed = any(ELAPSED_RANGE[0] * elapsed_s <= seconds <= ELAPSED_RANGE[1] * elapsed_s for seconds in stated)
    versioned = any(int(value) == version for value in _VERSION.findall(text))
    return {"passed": names and (timed or versioned), "names": names, "stated_seconds": stated[:5],
            "true_seconds": round(elapsed_s, 1), "version": version, "names_version": versioned}


def check_open(open_file, barrel_working, godot, loads, clicked_at):
    """4: the barrel's working .blend is the open file, Godot reports the barrel linked, and no file loaded before the
    harness clicked a card."""
    early = [at for at in loads if clicked_at is None or at < clicked_at]
    is_open = bool(open_file) and Path(open_file).resolve() == Path(barrel_working).resolve()
    linked = godot.get("linked") == BARREL
    return {"passed": is_open and linked and not early and clicked_at is not None, "open": is_open,
            "godot_linked": linked, "card_clicked": clicked_at is not None, "loads_before_click": len(early)}
