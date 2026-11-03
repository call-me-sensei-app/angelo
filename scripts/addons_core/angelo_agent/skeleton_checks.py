# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check's structural checks: roots, deforming bones, leaf bones, the armature's transform,
live constraints, B-Bones, the root bone for root motion and bone influences. No Blender dependency.

Every check returns one finding (``finding``) that says what it looked for and what it found, with a fix when it
didn't pass and the ``SOURCES`` key of the rule. Its text is English templates with ``{placeholders}`` marked with
``n_`` (``say``): the agent reads them filled in English, and the person's report card (``skeleton_report_card``)
translates each template before filling it (``render``). Placeholders carry data only: bone and object names,
numbers, engine names. The pose and axis checks are ``skeleton_pose``; the name and hierarchy match and Unreal's
auto-retarget are ``skeleton_match``; ``skeleton_check`` runs them all.
"""

import math

from .skeleton_export import exporter_influences, deform_only
from .ui_text import n_

PASS, INFO, WARN, FAIL = "pass", "info", "warn", "fail"
STATUSES = (FAIL, WARN, INFO, PASS)
MAX_LISTED = 8          # bone names listed in one finding
ROOT_AT_ORIGIN = 0.01   # of the rig's height: a root bone this near the armature's origin is at it
SCALE_EPSILON = 1e-4
ANGLE_EPSILON = 0.01    # degrees
TEXT_FIELDS = ("looked_for", "found", "fix")


def say(_template, **params):
    """One piece of a finding's text: an ``n_`` template and its data."""
    return (_template, params)


def _parts(value):
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _fill(parts, translate=None):
    return "; ".join((translate(t) if translate else t).format(**p) for t, p in parts)


def finding(check, status, looked_for, found, source, fix=None, details=None):
    """``looked_for``, ``found`` and ``fix``: a ``say`` piece or a list of them (shown joined by '; ')."""
    text = {"looked_for": _parts(looked_for), "found": _parts(found), "fix": _parts(fix)}
    out = {"check": check, "status": status, "looked_for": _fill(text["looked_for"]),
           "found": _fill(text["found"]), "source": source, "text": text}
    if text["fix"]:
        out["fix" if status in (WARN, FAIL) else "note"] = _fill(text["fix"])
    if details:
        out["details"] = details
    return out


def render(found, translate):
    """A finding's looked_for, found and fix as the person reads them: each template translated, then filled."""
    return {field: _fill(found["text"][field], translate) for field in TEXT_FIELDS}


def listed(names, limit=MAX_LISTED):
    """Names as data: quoted, comma-separated, '(+N)' for the rest. No words, so nothing to translate."""
    names = list(names)
    shown = ", ".join(repr(n[:64]) for n in names[:limit])
    return shown + (" (+%d)" % (len(names) - limit) if len(names) > limit else "")


def single_root(exported, profile):
    roots = exported.roots()
    fails = profile.target == "UNREAL"
    source = "ue_single_root" if fails else profile.sources["hierarchy"]
    looked = say(n_("one root bone"))
    if len(roots) == 1:
        return finding("single_root", PASS, looked, say(n_("one root bone, {bone}"), bone=listed(roots)), source)
    found = say(n_("root bones ({count}): {bones}"), count=len(roots), bones=listed(roots))
    if fails:
        return finding("single_root", FAIL, looked, found, source,
                       say(n_("Parent every root bone under one root bone at the armature's origin")))
    return finding("single_root", INFO, looked, found, source,
                   say(n_("{engine} accepts several roots; only the bones under Hips are mapped"),
                       engine=profile.display))


def deform_bones(rig, exported, fmt, options):
    extra = [rig.names[i] for i in exported.bones if not rig.deform[i]]
    looked = say(n_("only deforming bones in the exported skeleton"))
    if not extra:
        return finding("deform_only", PASS, looked, say(n_("bones: {count}, all deforming"),
                                                        count=len(exported.bones)), "deform_only")
    if deform_only(fmt, options):   # FBX keeps non-deforming ancestors of deforming bones.
        found = say(n_("non-deforming bones kept because deforming bones are parented under them ({count}): {bones}"),
                    count=len(extra), bones=listed(extra))
        fix = say(n_("Parent the deforming bones to deforming bones (one deform hierarchy), or export as glTF"))
    else:
        found = say(n_("exported bones that deform nothing ({count} of {total}): {bones}"), count=len(extra),
                    total=len(exported.bones), bones=listed(extra))
        fix = (say(n_("Export the FBX with Only Deform Bones on")) if fmt == "FBX" else
               say(n_("Export the glTF with Deform Bones Only on (in Godot: the import setting Export Deforming "
                      "Bones Only)")))
    return finding("deform_only", WARN, looked, found, "deform_only", fix)


def leaf_bones(rig, exported, fmt):
    ends = [rig.names[i] for i in range(len(rig.names)) if rig.is_end_bone(i) and i in exported]
    looked = say(n_("no added leaf bones"))
    if exported.leaf_bones:
        found = [say(n_("'_end' bones the FBX exporter adds, one per chain: {count}"), count=len(exported.leaf_bones))]
        if ends:
            found.append(say(n_("end bones already in the rig that move no vertex: {count}"), count=len(ends)))
        return finding("leaf_bones", WARN, looked, found, "fbx_leaf", say(n_("Export the FBX with Add Leaf Bones off")))
    if ends:
        return finding("leaf_bones", INFO, looked,
                       say(n_("none added; end bones in the rig that move no vertex ({count}): {bones}"),
                           count=len(ends), bones=listed(ends)), "fbx_leaf",
                       say(n_("End bones from an earlier FBX round trip can be deleted")))
    return finding("leaf_bones", PASS, looked, say(n_("none ({format})"), format=fmt), "fbx_leaf")


def transform(facts, profile):
    rotation = [float(a) for a in facts["object"]["rotation"]]
    scale = [float(s) for s in facts["object"]["scale"]]
    unit = float(facts.get("unit_scale", 1.0))
    found = []
    if any(abs(a) > ANGLE_EPSILON for a in rotation):
        found.append(say(n_("rotation in degrees ({values})"), values=", ".join("%.4g" % a for a in rotation)))
    if any(abs(s - 1.0) > SCALE_EPSILON for s in scale):
        found.append(say(n_("negative scale ({values})") if any(s < 0 for s in scale) else n_("scale ({values})"),
                         values=", ".join("%.4g" % s for s in scale)))
    if abs(unit - 1.0) > SCALE_EPSILON:
        found.append(say(n_("scene unit scale {value}"), value="%.4g" % unit))
    looked = say(n_("the armature at rotation (0, 0, 0), scale (1, 1, 1), and unit scale 1"))
    if not found:
        return finding("transform", PASS, looked, say(n_("applied")), "transform")
    return finding("transform", WARN, looked, found, "transform",
                   say(n_("Apply the armature's rotation and scale (Object > Apply > Rotation & Scale) with its "
                          "meshes, and set the unit scale to 1")))


def constraints(rig, exported, facts):
    live = facts.get("constraints", {})
    driven = facts.get("drivers", ())
    bones = sorted({i for i in list(live) + list(driven) if i in exported})
    looked = say(n_("no live constraints or drivers on exported bones"))
    if not bones:
        return finding("constraints", PASS, looked, say(n_("none")), "constraints")
    found = say(n_("exported bones that follow constraints or drivers ({count}): {bones}"), count=len(bones),
                bones=listed(rig.names[i] for i in bones))
    return finding("constraints", WARN, looked, found, "constraints",
                   say(n_("Bake each clip (the exporters sample them when animation is exported); in the engine "
                          "these bones move only by their baked keys")))


def bendy_bones(rig, exported, facts):
    bendy = [rig.names[i] for i, segments in sorted(facts.get("bbones", {}).items())
             if segments > 1 and i in exported and rig.deform[i]]
    looked = say(n_("deforming bones with one segment each"))
    if not bendy:
        return finding("bendy_bones", PASS, looked, say(n_("no B-Bones")), "bendy")
    return finding("bendy_bones", WARN, looked,
                   say(n_("deforming B-Bones ({count}): {bones}"), count=len(bendy), bones=listed(bendy)), "bendy",
                   say(n_("Engines see each B-Bone as one rigid bone: give the curve to chains of plain bones")))


def root_motion(rig, exported, mapping, profile, height):
    hips = mapping.bone("Hips")
    source = profile.sources["root"]
    if profile.root == "none":
        return finding("root_motion", PASS,
                       say(n_("no root bone needed ({engine} derives root motion from the body's centre of mass)"),
                           engine=profile.display), say(n_("not needed")), source)
    looked = say(n_("a root bone above Hips at the armature's origin"))
    missing = FAIL if profile.root == "required" else WARN
    if hips is None or hips not in exported:
        return finding("root_motion", missing, looked, say(n_("no Hips found to look above")), source,
                       say(n_("Add a root bone at the armature's origin and parent the hips bone to it")))
    if exported.object_root is not None:
        return finding("root_motion", PASS, looked, say(n_("the armature object {name} is read as the root bone"),
                                                        name=listed([exported.object_root])), source)
    above = exported.ancestors(hips)
    if not above:
        return finding("root_motion", missing, looked,
                       say(n_("Hips ({bone}) is the top bone: nothing above it carries root motion"),
                           bone=listed([rig.names[hips]])), source,
                       say(n_("Add a root bone at the armature's origin and parent {bone} to it"),
                           bone=listed([rig.names[hips]])))
    root = above[-1]
    offset = float(math.dist(rig.heads[root], rig.origin)) / height if height else 0.0
    found = say(n_("{bone}, {offset} of the rig's height from the origin"), bone=listed([rig.names[root]]),
                offset="%.3g" % offset)
    if offset > ROOT_AT_ORIGIN:
        return finding("root_motion", WARN, looked, found, source,
                       say(n_("Move the root bone's head to the armature's origin, on the floor")))
    return finding("root_motion", PASS, looked, found, source)


def influences(rig, exported, facts, profile, fmt, options):
    engine = profile.influences[fmt]
    writes = exporter_influences(fmt, options)
    limit = engine if writes is None else min(writes, engine)
    looked = [say(n_("at most {limit} bone influences per vertex ({engine} reads {count})"), limit=limit,
                  engine=profile.display, count=engine)]
    if writes is not None:
        looked.append(say(n_("Blender's glTF exporter writes {count}"), count=writes))
    meshes = facts.get("meshes", [])
    if not meshes:
        return finding("influences", INFO, looked, say(n_("no mesh is skinned to this armature")), "influences")
    over, worst, details = 0, 0, []
    for mesh in meshes:
        histogram = {int(k): int(v) for k, v in mesh["histogram"].items()}
        count = sum(v for k, v in histogram.items() if k > limit)
        top = max((k for k, v in histogram.items() if v), default=0)
        worst = max(worst, top)
        over += count
        if count:
            details.append({"mesh": mesh["name"][:64], "over": count, "most": top})
    if not over:
        return finding("influences", PASS, looked, say(n_("at most {count} (meshes: {meshes})"), count=worst,
                                                       meshes=len(meshes)), "influences")
    return finding("influences", WARN, looked,
                   say(n_("vertices over the limit: {count} (up to {most} influences)"), count=over, most=worst), "influences",
                   say(n_("Limit each vertex to {limit} influences and normalize (Weight Paint > Weights > Limit "
                          "Total), so the deformation in the engine is the one you see"), limit=limit), details)
