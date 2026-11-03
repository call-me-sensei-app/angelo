# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check: one armature against one engine's humanoid profile, as plain data. No Blender
dependency (``skeleton_facts`` reads the scene).

``check(facts, target, fmt)`` maps the rig's roles (``skeleton_roles``), models the skeleton the engine receives
(``skeleton_export``) and runs every check in ``ORDER``. The report lists each finding (what it looked for, what it
found, the fix, the rule's source and engine versions), the verdict (the worst status), the role each mapped bone
plays, and what the mapping couldn't settle. Read-only: nothing here changes the scene.
doc/angelo/engine_skeleton.rst is the contract.
"""

import time

from . import skeleton_checks as C, skeleton_match as M, skeleton_pose as P
from .skeleton_export import Exported, deform_only, exporter_influences, exporter_options
from .skeleton_profiles import FORMATS, PROFILES, SOURCES
from .skeleton_roles import map_roles

ORDER = ("bone_match", "auto_retarget", "single_root", "root_motion", "deform_only", "leaf_bones", "rest_pose",
         "facing", "bone_axes", "transform", "constraints", "bendy_bones", "influences")


def resolve_format(target, fmt="AUTO"):
    profile = PROFILES[target]
    fmt = profile.default_format if fmt in (None, "AUTO") else fmt
    if fmt not in FORMATS:
        raise ValueError("format must be AUTO, GLB or FBX")
    return fmt


def check(facts, target, fmt="AUTO"):
    started = time.perf_counter()
    profile = PROFILES[target]
    fmt = resolve_format(target, fmt)
    options = exporter_options(target, fmt)
    rig = facts["rig"]
    exported = Exported(rig, target, fmt, options, facts["armature"])
    mapping = map_roles(rig)
    points = list(rig.heads) + list(rig.tails)
    height = float(max(p[2] for p in points) - min(p[2] for p in points)) if points else 0.0
    found = {
        "bone_match": M.bone_match(rig, exported, mapping, profile, fmt),
        "single_root": C.single_root(exported, profile),
        "root_motion": C.root_motion(rig, exported, mapping, profile, height),
        "deform_only": C.deform_bones(rig, exported, fmt, options),
        "leaf_bones": C.leaf_bones(rig, exported, fmt),
        "rest_pose": P.rest_pose(rig, mapping, profile),
        "facing": P.facing(mapping, profile),
        "bone_axes": P.bone_axes(rig, mapping, profile),
        "transform": C.transform(facts, profile),
        "constraints": C.constraints(rig, exported, facts),
        "bendy_bones": C.bendy_bones(rig, exported, facts),
        "influences": C.influences(rig, exported, facts, profile, fmt, options),
    }
    if target == "UNREAL":
        found["auto_retarget"] = M.auto_retarget(rig, exported, mapping, fmt)
    findings = [found[name] for name in ORDER if name in found]
    counts = {status: sum(1 for f in findings if f["status"] == status) for status in C.STATUSES}
    verdict = next(status for status in C.STATUSES if counts[status] or status == C.PASS)
    used = sorted({f["source"] for f in findings})
    return {
        "armature": facts["armature"], "target": target, "engine": profile.display, "format": fmt,
        "exporter": {"deform_only": deform_only(fmt, options), "leaf_bones": bool(exported.leaf_bones),
                     "influences": exporter_influences(fmt, options) or "all"},
        "verdict": verdict, "counts": counts, "findings": findings,
        "bones": {"in_rig": len(rig.names), "exported": exported.count()},
        "roles": {role: rig.names[bone] for role, bone in mapping.roles.items()},
        "notes": list(mapping.notes),
        "sources": {key: {"source": SOURCES[key][0], "versions": SOURCES[key][1]} for key in used},
        "ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


def for_agent(report):
    """The report as the agent gets it: passed checks by name only, so a clean rig costs few tokens."""
    out = {key: report[key] for key in ("armature", "target", "format", "verdict", "counts", "exporter", "bones",
                                         "roles", "notes", "sources")}
    out["passed"] = [f["check"] for f in report["findings"] if f["status"] == C.PASS]
    out["findings"] = [{key: value for key, value in f.items() if key != "text"}   # the card's templates
                       for f in report["findings"] if f["status"] != C.PASS]
    out["sources"] = {key: value for key, value in report["sources"].items()
                      if any(f["source"] == key for f in out["findings"])}
    return out
