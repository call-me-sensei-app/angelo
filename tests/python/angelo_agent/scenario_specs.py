# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scenario task sets of ``bench_mcp_vs_angelo.py``: multi-turn builds graded against a ground truth.

Dependency-free (no bpy): imported by the host launcher, both in-app drivers
and host tests. A scenario lives in ``scenario_<id>.py`` and defines ``TURNS``
(prompt, attached reference views and closed checks per turn), ``CAMERAS``
(the fixed views the references are rendered from and the end-of-session
captures are scored in), ``SUBJECT`` (the objects the subject silhouette is
made of), ``FRAME`` and ``build(bpy, upto)``, the deterministic ground truth.

Task sets are named ``scenario-<id>``. Each turn is one edit task of one
continuing chat; its checks are the closed ``scenario_checks`` kinds, graded by
the driver after the turn, never by the model. References are JPEG renders of
the ground truth, made once per ground-truth version (``scenario_references``)
and kept in ``~/.angelo-bench/scenarios/references/<id>/<digest>``; both sides
receive the same files, verified by SHA-256 before each send.

Three scenarios exist: the bridge (built to reference images, exact sizes),
the island (an art-directed Cycles still, loose checks, a render job at the
end) and the heavy scene (five everyday edits in a dense castle scene of about
2,000 objects and 2.7 million vertices, each needing geometry to be read).
The planned ones (an interior room, a product turntable, a rigged walk cycle
and a scattered landscape with a sun time-lapse) are added by writing their
module and listing them here.

A scenario may also declare, all optional:

* per turn ``outputs``: files the session must have produced by the end of
  the turn (``scenario_checks.evaluate_outputs``), graded by the driver in the
  side's own output folders and counted with the turn's checks;
* per turn ``job``: the background job a perfect agent starts after building
  the turn (the dry run's scripted models do);
* ``APPROVABLE``: tools its tester approves besides the scenario rule's (the
  island: ``angelo_job_start``);
* ``JOB_WAIT_S``: how long a driver waits for a background job the turn
  started before it grades the turn;
* ``START`` with ``make_start`` and ``setup``: a start file made before the
  references (``scenario_start_job``) and kept with them (SHA-256 in the
  manifest); the reference job begins from it, and both sides of a trial
  append it into their scene before the first message instead of starting
  from the factory scene (``start_file``).

One-shot tests (``doc/angelo/oneshot_tests.rst``) are scenarios with exactly
one turn, asked for in one message and never followed up. Their task sets are
``oneshot-<id>``, their modules ``scenario_oneshot_<id>.py`` (scenario name
``oneshot_<id>``), and they declare ``LIMITS`` (the turn's time cap, the wait
for a render job, the cost cap), ``FINAL_STILL`` and ``FINAL_VIEW``. They are
listed apart: ``SCENARIO_IDS`` and ``TASK_SETS`` stay the multi-turn scenarios.
The ``mini-`` tests are the smallest of them (``oneshot_minis``): one short
request on the factory scene, no reference image and no still.
``ONESHOT_GROUPS`` names sets of them that one command runs in sequence.
"""

import ast
import hashlib
import re
import importlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIO_IDS = ("bridge", "island", "heavy", "shop", "workshop")
TASK_SET_PREFIX = "scenario-"
TASK_SETS = tuple(TASK_SET_PREFIX + name for name in SCENARIO_IDS)
REFERENCE_ROOT = Path.home() / ".angelo-bench" / "scenarios" / "references"
REFERENCES_ENV = "ANGELO_SCENARIO_REFERENCES"
MANIFEST = "manifest.json"
# Every file whose change alters a ground truth or its renders; part of the reference digest.
SHARED_SOURCES = ("scenario_build_kit.py", "scenario_job.py", "scenario_capture.py")
REFERENCE_RESOLUTION = (1280, 720)
MAX_IMAGES_PER_TURN = 4   # Angelo's attachment limit per message (references.MAX_IMAGES).
# Claude Code (2.1) re-encodes any attached image larger than 512,000 bytes as a JPEG
# before sending it, Codex and Angelo send the file as it is. References are JPEG
# files under this budget, so all three model inputs carry the same bytes (the dry
# run proves it per side).
REFERENCE_MAX_BYTES = 500_000
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
# --python-approvals: whether the Angelo side's tester approves reviewed Python in a scenario.
PYTHON_APPROVAL_MODES = ("approve", "decline")
ONESHOT_MINIS = ("mini-footbridge", "mini-footbridge-arched", "mini-cube", "mini-camera", "mini-lighting")
# Appended, never inserted: the order is pinned by the registry and scene tests.
ONESHOT_FACE_HAIR = ("head-stylised", "head-portrait", "hair-short", "hair-long", "head-anime")
# The parallel-agents benchmark (chess) and its held-out cases: jobs a person splits by unique design.
ONESHOT_PARALLEL = ("chess", "parlour", "roadster", "outfit")
# Long one-shot candidates (LONG-TEST-SELECTION): large multi-part scenes meant to take one chat 20+ minutes. Apart from
# the registered parallel set and from every group: each is run on its own, as --task-set oneshot-<id>.
ONESHOT_LONG = ("apartment", "high-street", "town-centre", "grand-apartment", "tempest")
ONESHOT_IDS = (("bridge", "footbridge", "tree", "cottage", "castle") + ONESHOT_MINIS
               + ("footbridge-conflict", "bridge-conflict") + ONESHOT_FACE_HAIR + ONESHOT_PARALLEL + ONESHOT_LONG)
ONESHOT_PREFIX = "oneshot-"
ONESHOT_TASK_SETS = tuple(ONESHOT_PREFIX + name for name in ONESHOT_IDS)
ONESHOT_NAMES = tuple("oneshot_" + name.replace("-", "_") for name in ONESHOT_IDS)
# Sets of one-shot tests that one command runs in sequence, each as its own one-message run (oneshot_group).
ONESHOT_GROUPS = {"oneshot-minis": tuple(ONESHOT_PREFIX + name for name in ONESHOT_MINIS),
                  "oneshot-face-hair": tuple(ONESHOT_PREFIX + name for name in ONESHOT_FACE_HAIR),
                  "oneshot-parallel": tuple(ONESHOT_PREFIX + name for name in ONESHOT_PARALLEL)}
# What also decides a one-shot test's references: its own job and image code, and the files its ground truth reads.
ONESHOT_SHARED = ("oneshot_job.py", "oneshot_images.py")
# The face and hair tests' ground truths: the heads' designs and mesher, the hairstyles, the shared scene.
FACE_HAIR_SOURCES = ("face_tests.py", "head_kit.py", "head_designs.py", "head_sdf.py", "hair_kit.py")
ONESHOT_SOURCES = {**{"oneshot_" + name.replace("-", "_"): FACE_HAIR_SOURCES for name in ONESHOT_FACE_HAIR},
                   "oneshot_bridge": ("scenario_bridge.py",),
                   "oneshot_tree": ("oneshot_kit.py",), "oneshot_cottage": ("oneshot_kit.py",),
                   "oneshot_castle": ("castle_kit.py",),
                   **{"oneshot_" + name: ("parallel_kits.py",) for name in ONESHOT_PARALLEL},
                   "oneshot_apartment": ("long_kits.py", "long_kit_apartment.py", "long_probe_margins.py"),
                   "oneshot_high_street": ("long_kits.py", "long_kit_street.py", "long_probe_margins.py"),
                   "oneshot_town_centre": ("long_kits.py", "long_kit_street.py", "long_kit_town.py",
                                           "long_probe_margins.py", "long_probe_check.py"),
                   "oneshot_grand_apartment": ("long_kits.py", "long_kit_apartment.py", "long_kit_flat.py",
                                               "long_kit_flat_north.py", "long_kit_flat_south.py",
                                               "long_probe_margins.py", "long_probe_check.py"),
                   "oneshot_tempest": ("long_kits.py", "long_kit_tempest.py", "long_kit_tempest_ship.py", "long_kit_tempest_values.py",
                                       "long_kit_tempest_brief.py", "long_probe_margins.py", "long_probe_scaled.py"),
                   **{"oneshot_" + name.replace("-", "_"): ("oneshot_minis.py",) for name in ONESHOT_MINIS}}
# What else decides a scenario's references: the files its start scene and ground truth are made from.
SCENARIO_SOURCES = {"heavy": ("heavy_scene.py", "heavy_plan.py", "heavy_scene_blend.py", "castle_kit.py"),
                    "shop": ("many_object_scenes.py",), "workshop": ("many_object_scenes.py",)}
# A test that is another's scene with another brief reads that test's reference folder and digest, so both are given
# byte-identical pictures (rendered references are not bit-reproducible).
SHARED_REFERENCES = {"oneshot_footbridge_conflict": "oneshot_footbridge", "oneshot_bridge_conflict": "oneshot_bridge"}
# Neither the brief nor the checks decide a picture. A test whose brief or checks alone changed after billed runs keeps
# the folder those runs were given: (that folder's digest, the digest of the same sources without the brief). Any
# other change to the sources no longer matches the second and falls back to the full digest, so the references are
# made again. The folders below were kept through changes that render nothing: checks, the stand-ins' removal of the
# default cube's unused mesh and material (oneshot_kit.clear_default_scene), and the copyright holder's change in
# the files' notice lines (which the digest leaves out, NOTICE_LINE).
KEPT_DIGESTS = {
                "bridge": ("52718cacffb88478", "85ab25eace402e80"),
                "heavy": ("177fc564efbf4f05", "f30be220b0638ab1"),
                "island": ("d9f1cb15cc84191d", "9c4c819b8e9295ff"),
                "oneshot_bridge": ("f0d313c1cba67ef2", "e30566079d906eb3"),
                "oneshot_castle": ("42d6f53c523251eb", "3f36b47072ec08de"),
                "oneshot_cottage": ("7855a5d1df889098", "4c7f9317528ac1cf"),
                "oneshot_footbridge": ("f676d5e14eeded19", "a001df37b20294c2"),
                "oneshot_hair_long": ("7bd961b6e2055a7e", "f71de0e44f0c9dd5"),
                "oneshot_hair_short": ("e90e7b37451e3e78", "ad80bd9d2d23f514"),
                "oneshot_head_anime": ("569e12e69bf7d929", "470583ae5369efdf"),
                "oneshot_head_portrait": ("c2983428cdb9ea95", "f59e669fed356e69"),
                "oneshot_head_stylised": ("7cd50ea02cb4d168", "c999c386f438cadc"),
                "oneshot_mini_camera": ("22ea44460463babd", "f33638bdcc8b739a"),
                "oneshot_mini_cube": ("6d7339ecb341e22d", "1793e14db93140b6"),
                "oneshot_mini_footbridge": ("5c74c15702e78109", "806579388d345403"),
                "oneshot_mini_footbridge_arched": ("94480f7b2cff3187", "5c411364b5fa6e27"),
                "oneshot_mini_lighting": ("5fc793f964a5138f", "bad93a73bc66ba9a"),
                "oneshot_tree": ("dc6c5e8d1bb54e09", "f0739775dab2e39d")}
# The longest turn a one-shot test may be given (--task-timeout); every other task set keeps 1,800 s.
ONESHOT_MAX_WALL_S = 6 * 3600


def add_live_arguments(parser):
    """The live eval's scenario-only options (eval_live_agent.parse)."""
    parser.add_argument("--python-approvals", choices=PYTHON_APPROVAL_MODES, default="approve",
                        help="scenario task sets only: approve (default, as an artist would) or decline reviewed "
                             "Python; every approval is counted. Other task sets always decline it")
    parser.add_argument("--demo-record", action="store_true",
                        help="scenario task sets only: record the trial window for demo videos (scenario_demo); "
                             "it costs main-thread time, so the run's timings are not comparable")
    parser.add_argument("--stage-copies", action="store_true",
                        help="scenario task sets only: keep a copy of the scene after every turn "
                             "(qa/stages/NN-<turn>.blend), for stage renders")
    parser.add_argument("--render-device", choices=("", "cpu", "gpu"), default="",
                        help="scenario task sets only: gpu enables the machine's GPU for Cycles in the trial's "
                             "preferences, as a person with a GPU has it (the scene still chooses its device); "
                             "default: the factory preference, CPU")


def is_scenario(task_set):
    return str(task_set).startswith((TASK_SET_PREFIX, ONESHOT_PREFIX))


def is_oneshot(value):
    """Whether a task set (``oneshot-<id>``) or a scenario name (``oneshot_<id>``) is a one-shot test."""
    return str(value).startswith((ONESHOT_PREFIX, "oneshot_"))


def scenario_id(task_set):
    text = str(task_set)
    if text in ONESHOT_TASK_SETS:
        return ONESHOT_NAMES[ONESHOT_TASK_SETS.index(text)]
    name = text[len(TASK_SET_PREFIX):]
    if not text.startswith(TASK_SET_PREFIX) or name not in SCENARIO_IDS:
        raise ValueError("Choose a scenario task set: " + ", ".join(TASK_SETS + ONESHOT_TASK_SETS))
    return name


def module(name):
    if name not in SCENARIO_IDS + ONESHOT_NAMES:
        raise ValueError("Unknown scenario %r" % name)
    return importlib.import_module("scenario_" + name)


def limits(name):
    """A one-shot test's ``{"wall_s", "job_wait_s", "cost_usd"}``; empty for a scenario."""
    return dict(getattr(module(name), "LIMITS", {})) if name in ONESHOT_NAMES else {}


def final_still(name):
    """The still a one-shot brief asks the agent to save (``{"name", "size"}``), or None when it asks for none."""
    still = getattr(module(name), "FINAL_STILL", None) if name in ONESHOT_NAMES else None
    return dict(still) if still and still.get("name") else None


def wall_cap(task_set):
    """Seconds a one-shot test's single turn may take by default (0 for every other task set)."""
    return limits(scenario_id(task_set)).get("wall_s", 0) if str(task_set) in ONESHOT_TASK_SETS else 0


# A file's copyright and licence notice renders nothing: a change of copyright holder keeps every reference folder.
NOTICE_LINE = re.compile(rb"(?m)^[ \t]*(?:#|//|/?\*|\.\.|<!--)?[ \t]*SPDX-(?:FileCopyrightText|License-Identifier):[^\n]*\n?")


def digest(name):
    """SHA-256 over the scenario module and the shared builder/render sources (first 16 hex digits).

    A test listed in ``SHARED_REFERENCES`` has the digest of the test it shares with; a kept digest
    (``KEPT_DIGESTS``) stands while the sources without the brief are those its folder was rendered from.
    """
    name = SHARED_REFERENCES.get(name, name)
    kept = KEPT_DIGESTS.get(name)
    if kept and sources_digest(name, brief=False) == kept[1]:
        return kept[0]
    return sources_digest(name)


def sources_digest(name, brief=True):
    """The digest of the files that decide a scenario's references; ``brief=False`` leaves its ``PROMPT`` out."""
    sha = hashlib.sha256()
    module_file = "scenario_%s.py" % name
    extra = ONESHOT_SHARED + ONESHOT_SOURCES.get(name, ()) if name in ONESHOT_NAMES else SCENARIO_SOURCES.get(name, ())
    for file_name in (module_file,) + SHARED_SOURCES + extra:
        data = NOTICE_LINE.sub(b"", (HERE / file_name).read_bytes().replace(b"\r\n", b"\n"))
        if not brief and file_name == module_file:
            data = without_brief(data)
        sha.update(file_name.encode() + b"\0" + data + b"\0")
    return sha.hexdigest()[:16]


def without_brief(source):
    """A module's source (bytes) without its top-level ``PROMPT = ...`` statement; unchanged when it has none."""
    lines = source.split(b"\n")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "PROMPT" for target in node.targets):
            return b"\n".join(lines[:node.lineno - 1] + lines[node.end_lineno:])
    return source


def reference_root():
    """The references root: ``ANGELO_SCENARIO_REFERENCES``, else ``~/.angelo-bench/scenarios/references``.

    Launchers pass it to trial processes explicitly, so an isolated HOME still finds the same files.
    """
    return Path(os.environ.get(REFERENCES_ENV) or REFERENCE_ROOT)


def references_dir(name):
    """Where this ground-truth version's references live (the shared test's folder for ``SHARED_REFERENCES``)."""
    return reference_root() / SHARED_REFERENCES.get(name, name) / digest(name)


def manifest(name):
    """The reference manifest of the current ground truth, or None when it was not rendered yet."""
    path = references_dir(name) / MANIFEST
    return json.loads(path.read_text()) if path.is_file() else None


def media_type(path):
    """The MIME type of a reference file, from its suffix."""
    return MEDIA_TYPES[Path(path).suffix.lower()]


def reference_files(name, views):
    """``[(view, path, sha256)]`` for a turn's attached views; raises when a file is missing or changed."""
    data = manifest(name)
    if data is None:
        raise FileNotFoundError("No references for scenario %s (%s); run scenario_references.py first"
                                % (name, references_dir(name)))
    folder, files = references_dir(name), []
    for view in views:
        entry = data["images"][view]
        path = folder / entry["file"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError("Reference %s changed on disk (%s != %s)" % (path, actual, entry["sha256"]))
        files.append((view, str(path), actual))
    return files


def start_file(name):
    """``(path, sha256)`` of the start file both sides append before the first message, or None (factory scene).

    Raises when the scenario has one and its references (which hold it) were not made yet.
    """
    if getattr(module(name), "START", None) is None:
        return None
    data = manifest(name)
    if data is None or not data.get("start"):
        raise FileNotFoundError("No start file for scenario %s (%s); run scenario_references.py first"
                                % (name, references_dir(name)))
    return references_dir(name) / data["start"]["file"], data["start"]["sha256"]


def tasks(name):
    """The harness task list of one scenario: one edit task per turn, in order."""
    scenario = module(name)
    if name in ONESHOT_NAMES and len(scenario.TURNS) != 1:
        raise ValueError("A one-shot test is one message: %s has %d turns" % (name, len(scenario.TURNS)))
    output = []
    for number, turn in enumerate(scenario.TURNS, 1):
        if len(turn["references"]) > MAX_IMAGES_PER_TURN:
            raise ValueError("%s/%s attaches more than %d images" % (name, turn["id"], MAX_IMAGES_PER_TURN))
        task = {"id": turn["id"], "kind": "edit", "requires_object": False, "prompt": turn["prompt"],
                "checks": [], "scenario": name, "scenario_checks": turn["checks"],
                "references": list(turn["references"]), "title": turn["title"],
                "source": "scenario %s turn %d: %s" % (name, number, turn["title"])}
        # Only a turn that names files or a job carries these keys: other scenarios' tasks are unchanged.
        if turn.get("outputs"):
            task["scenario_outputs"] = [dict(entry) for entry in turn["outputs"]]
        if turn.get("job"):
            task["scenario_job"] = dict(turn["job"])
        output.append(task)
    return output


def approvable(name):
    """Tools this scenario's tester approves besides the scenario rule's (``scenario_session``)."""
    return frozenset(getattr(module(name), "APPROVABLE", ()))


def job_wait(name):
    """Seconds a driver waits for a background job a turn started before grading it (0: the scenario has none)."""
    return float(getattr(module(name), "JOB_WAIT_S", 0.0))


def output_count(name):
    return sum(len(turn.get("outputs") or ()) for turn in module(name).TURNS)


def views(name, role=None):
    """Camera names of a scenario, optionally only ``reference`` or ``closeup`` views, in spec order."""
    cameras = module(name).CAMERAS
    return [view for view, spec in cameras.items() if role is None or spec["role"] == role]


def all_checks(name):
    """Every turn's checks, labelled by turn: the end-of-session compliance pass."""
    return [dict(check, label="%s: %s" % (turn["id"], check.get("label", check["kind"])))
            for turn in module(name).TURNS for check in turn["checks"]]


def plan(name):
    scenario = module(name)
    return {"scenario": name, "title": scenario.TITLE, "turns": [turn["id"] for turn in scenario.TURNS],
            "checks": sum(len(turn["checks"]) for turn in scenario.TURNS),
            **({"outputs": output_count(name), "job_wait_s": job_wait(name)} if output_count(name) else {}),
            **({"oneshot": {"messages_per_side": 1, "limits": limits(name), "final_still": final_still(name),
                            "ground_truth": scenario.GROUND_TRUTH}} if name in ONESHOT_NAMES else {}),
            "references_per_turn": {turn["id"]: turn["references"] for turn in scenario.TURNS if turn["references"]},
            "reference_digest": digest(name), "references": str(references_dir(name)),
            "scored_views": views(name), "frame": scenario.FRAME}
