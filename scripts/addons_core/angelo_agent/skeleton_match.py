# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check's name and hierarchy match against a profile, and Unreal's auto-retarget. No Blender
dependency.

* **Unity and Godot** map bones to their humanoid slots by layout and hierarchy, not by name (Unity's automap, a
  Godot BoneMap), so a miss is a role no bone plays (``skeleton_roles``) or a bone that isn't below the bone mapped to
  its parent slot. Each miss says which bone the layout found, or why none.
* **Unreal** finds bones by exact name, so a miss is a profile name no exported bone has, or has under another
  parent; each says which bone plays that role by layout (rename it) or which name is closest.
* **Auto-retarget** (Unreal): the IK Retargeter's characterisation score per modelled template (``skeleton_unreal``).
"""

from .skeleton_checks import FAIL, INFO, PASS, WARN, finding, listed, say
from .skeleton_profiles import ROLE_PARENT, UE_SKELETON
from . import skeleton_unreal
from .ui_text import n_

MAX_MISSES = 12


def _profile_parent(profile, mapping, role):
    """The nearest role above ``role`` in the profile that a bone plays (Unity and Godot allow bones between)."""
    parent = profile.parents.get(role)
    while parent is not None and (parent not in profile.names or mapping.bone(parent) is None):
        parent = ROLE_PARENT.get(parent)
    return parent


def _why_missing(mapping, role):
    """The mapping's note about the side or part this role is on (model-facing English), or a plain reason."""
    side = "left" if role.startswith("Left") else "right" if role.startswith("Right") else ""
    related = [note for note in mapping.notes if side and side in note] or mapping.notes
    return related[0] if related else "no bone has the layout of this role"


MATCH_NOTE = {"GODOT": n_("Godot maps the bones with a BoneMap (Advanced Import Settings > Skeleton > Retarget > "
                          "SkeletonProfileHumanoid); check its guesses against the roles found here"),
              "UNITY": n_("Unity's automap guesses the slots by hierarchy; check them in Rig > Configure")}


def bone_match(rig, exported, mapping, profile, fmt):
    if profile.exact_names:
        return _exact(rig, exported, mapping, profile, fmt)
    misses, missing, outside, named = [], [], [], 0
    for role in profile.roles():
        bone = mapping.bone(role)
        expected = profile.names[role]
        if bone is None or bone not in exported:
            if role in profile.required:
                missing.append(expected)
                misses.append({"role": expected, "required": True, "found": None, "why": _why_missing(mapping, role)})
            continue
        if rig.names[bone] == expected:
            named += 1
        parent_role = _profile_parent(profile, mapping, role)
        if parent_role is None:
            continue
        parent = mapping.bone(parent_role)
        if parent not in exported.ancestors(bone):
            outside.append(expected)
            misses.append({"role": expected, "found": rig.names[bone], "why": "not below %r, which plays %s"
                           % (rig.names[parent][:64], profile.names.get(parent_role, parent_role))})
    mapped = sum(1 for role in profile.roles() if mapping.bone(role) is not None)
    looked = say(n_("the bones {engine} requires ({count}) found by layout, each below the bone of its parent slot"),
                 count=len(profile.required), engine=profile.display)
    found = [say(n_("slots mapped: {mapped} of {slots}"), mapped=mapped, slots=len(profile.roles()))]
    if profile.target == "GODOT":
        found.append(say(n_("named as the profile: {count}"), count=named))
    if missing:
        found.append(say(n_("no bone for {roles}"), roles=listed(missing)))
    if outside:
        found.append(say(n_("outside their parent slot's bone: {roles}"), roles=listed(outside)))
    if missing or outside:
        fix = (say(n_("Reparent those bones so each sits below the bone of its parent slot")) if outside else
               say(n_("Add the missing bones: {engine} needs every required slot"), engine=profile.display))
        return finding("bone_match", FAIL, looked, found, profile.sources["names"], fix, misses[:MAX_MISSES])
    return finding("bone_match", PASS if profile.target == "UNITY" else INFO, looked, found,
                   profile.sources["names"], say(MATCH_NOTE[profile.target]))


def _exact(rig, exported, mapping, profile, fmt):
    named = exported.named()
    engine = [skeleton_unreal.engine_name(n, fmt) for n, _ in named]
    prefix = skeleton_unreal.shared_prefix(engine)
    by_name = {}
    for (name, parent), shown in zip(named, engine):
        parent_shown = None if parent is None else skeleton_unreal.engine_name(parent, fmt)[len(prefix):]
        by_name.setdefault(shown[len(prefix):].casefold(), (shown[len(prefix):], parent_shown))
    expected_parent = {name: parent for name, parent, _ in UE_SKELETON}
    misses, renames, ok = [], [], 0
    for role in profile.roles():
        expected = profile.names[role]
        have = by_name.get(expected.casefold())
        bone = mapping.bone(role)
        if have is None:
            if bone is not None and bone in exported:
                renames.append({"from": rig.names[bone], "to": expected})
                why = "%r plays this role by layout" % rig.names[bone][:64]
            else:
                near = skeleton_unreal.closest(expected, [n for n, _ in by_name.values()])
                why = "no bone plays this role by layout" + ("; closest name %r" % near[:64] if near else "")
            misses.append({"bone": expected, "required": role in profile.required, "why": why})
            continue
        want = expected_parent.get(expected)
        if (have[1] or "").casefold() != (want or "").casefold():
            misses.append({"bone": expected, "why": "its parent is %r, not %r" % ((have[1] or "none")[:64], want)})
        else:
            ok += 1
    looked = say(n_("the UE5 Mannequin's bone names (case-insensitive, after a prefix every bone shares), each under "
                    "its parent"))
    found = [say(n_("named and parented as the Mannequin: {count} of {total}"), count=ok, total=len(profile.roles()))]
    if prefix:
        found.append(say(n_("prefix {prefix} removed"), prefix=listed([prefix])))
    if not misses:
        return finding("bone_match", PASS, looked, found, profile.sources["names"])
    found.append(say(n_("missing or under another parent: {count}"), count=len(misses)))
    details = misses[:MAX_MISSES] + ([{"renames": renames[:40]}] if renames else [])
    return finding("bone_match", WARN, looked, found, profile.sources["names"],
                   say(n_("Rename the bones to the Mannequin's names (the renames are listed), or set up the IK "
                          "Rig's retarget chains by hand")), details)


def auto_retarget(rig, exported, mapping, fmt):
    deforming = [rig.names[i] for i in exported.bones if rig.deform[i]]
    result = skeleton_unreal.characterise(exported.named(), fmt, deforming)
    best, prediction = result["best"], result["prediction"]
    looked = say(n_("the template the IK Retargeter's auto-characterisation chooses (the most bones with the same "
                    "name and parent), used above a score of {threshold}, with its retarget root and pelvis"),
                 threshold="%.1f" % result["threshold"])
    if best:
        found = [say(n_("chosen {template} {score} (bones with the same name and parent: {parented} of {bones}; by "
                        "name: {found})"), template=best["template"], score="%.2f" % best["score"],
                     parented=best["parented"], bones=best["bones"], found=best["found"])]
    else:
        found = [say(n_("no bone has the same name and parent as in a template Angelo models"))]
    found += [say(n_("{template} {score}"), template=s["template"], score="%.2f" % s["score"])
              for s in result["scores"] if s is not best]
    shown = best or max(result["scores"], key=lambda s: s["score"])
    details = []
    hint = result["prefix_hint"]
    if hint:
        details.append({"prefix": hint[0], "share": hint[1],
                        "why": "most bones start with %r but not all, so Unreal keeps it and their names don't "
                               "match a template" % hint[0]})
    if shown["missing"]:
        details.append({"template": shown["template"], "missing": shown["missing"][:MAX_MISSES],
                        "missing_count": len(shown["missing"])})
    if prediction == "recognised" and best["root"] and best["pelvis"]:
        out = finding("auto_retarget", PASS, looked, found, "ue_characterizer")
    elif prediction == "recognised":
        need = list(dict.fromkeys(name for name, have in ((best["root_name"], best["root"]),
                                                          (best["pelvis_name"], best["pelvis"])) if not have))
        found.append(say(n_("no {bones} bone"), bones=listed(need)))
        out = finding("auto_retarget", WARN, looked, found, "ue_characterizer",
                      say(n_("Name the root and pelvis bones {bones}"), bones=listed(need)), details)
    else:
        unmodelled = result["unmodelled_count"]
        if prediction == "uncertain":
            found.append(say(n_("uncertain: one of the {count} templates Angelo doesn't model, with more bones "
                                "matching, could be chosen instead and then fall under the threshold"),
                             count=unmodelled))
        else:
            found.append(say(n_("uncertain: no template Angelo models would be used, but one of the {count} it "
                                "doesn't model may still match a few names"), count=unmodelled))
        fix = [say(n_("Rename the bones to the UE5 Mannequin's (the bone match lists the renames), or set up the "
                      "retarget chains by hand"))]
        if hint:
            fix.append(say(n_("export deforming bones only, so every bone shares the prefix {prefix}"),
                           prefix=listed([hint[0]])))
        fix.append(say(n_("Unreal also has templates Angelo doesn't model: {templates}"),
                       templates=", ".join(result["unmodelled"])))
        out = finding("auto_retarget", WARN, looked, found, "ue_characterizer", fix, details)
    out["prediction"] = prediction
    return out
