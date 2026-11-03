# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Would Unreal Engine's IK Retargeter recognise this skeleton? Its auto-characterisation, modelled from its source
(``skeleton_profiles.SOURCES["ue_characterizer"]``). No Blender dependency.

The characteriser compares the imported skeleton's bone names with built-in templates. A prefix every bone shares is
removed first. The template with the most bones found by name (case-insensitive) under the same parent name is
chosen, the first on a tie; it is used only if its score, the mean of the share of its bones found and the share
found under the same parent, is above 0.1. Below it, or with no bone under its template parent at all, nothing is
set up.

Angelo models three of Unreal's 23 templates (``UE_TEMPLATES``), so it can say a rig is recognised only when no
template it doesn't model could change the outcome: a template chosen over Angelo's best has at least as many
matching bones, so its score is at least that count over the largest template's size, and when that is above the
threshold some template is used whichever is chosen. Every other rig is ``uncertain``: a template Angelo doesn't
model may be chosen instead (and fall under the threshold), or may match a few generic names when none of Angelo's
does.
"""

import difflib
import os

from .skeleton_profiles import UE_LARGEST_TEMPLATE, UE_TEMPLATE_COUNT, UE_TEMPLATES, UE_THRESHOLD, UE_UNMODELLED

# How a Blender bone name reads in Unreal after import (UE 5.8's Interchange importer, FBX and glTF alike): an FBX
# name loses everything up to its last ':' (Mixamo's "mixamorig:Hips" arrives as "Hips"; glTF keeps it, as
# "mixamorig_Hips"), a space becomes '-' and '+' becomes '_', and every character an Unreal object name may not
# hold becomes '_' (Rigify's "upper_arm.L" is matched as "upper_arm_L").
NOT_IN_NAMES = frozenset("\"' ,/.:|&!~\n\r\t@#(){}[]=;^%$`")   # the characters the editor refuses in an asset name
MAX_MISSES = 12   # template bones listed by name in a result


def engine_name(name, fmt):
    if fmt == "FBX":
        name = name.rpartition(":")[2]   # the FBX namespace, unless the import keeps it (off by default)
    name = name.replace(" ", "-").replace("+", "_")
    return "".join("_" if c in NOT_IN_NAMES else c for c in name)


def shared_prefix(names):
    """The longest prefix every name shares (character by character), as the characteriser removes it."""
    return os.path.commonprefix(list(names)) if len(names) > 1 else ""


def characterise(bones, fmt, deforming=None):
    """``bones``: [(name, parent name or None)] of the skeleton Unreal imports; ``deforming``: the names of its
    deforming bones, when known. Returns the scores per modelled template, the one Unreal would choose among them
    (``best``, None when no bone sits under its template parent), the prediction (``recognised``, ``uncertain`` or
    ``unmatched``) and why a miss misses."""
    names = [engine_name(n, fmt) for n, _ in bones]
    prefix = shared_prefix(names)
    stripped = {}
    for (name, parent), shown in zip(bones, names):
        key = shown[len(prefix):].casefold()
        parent_shown = None if parent is None else engine_name(parent, fmt)
        stripped.setdefault(key, None if parent_shown is None else parent_shown[len(prefix):].casefold())
    scores, best = [], None
    for template in UE_TEMPLATES:
        found = parented = 0
        missing, misparented = [], []
        for name, parent in template.bones:
            key = name.casefold()
            if key not in stripped:
                missing.append(name)
                continue
            found += 1
            if stripped[key] == (None if parent is None else parent.casefold()):
                parented += 1
            else:
                misparented.append((name, parent, stripped[key]))
        count = len(template.bones)
        score = {"template": template.name, "score": round((found / count + parented / count) / 2, 3),
                 "found": found, "parented": parented, "bones": count, "missing": missing,
                 "misparented": misparented, "root": template.root.casefold() in stripped,
                 "pelvis": template.pelvis.casefold() in stripped, "root_name": template.root,
                 "pelvis_name": template.pelvis}
        scores.append(score)
        if parented > (best["parented"] if best else 0):   # chosen by matching bones, not by score
            best = score
    used = best is not None and best["score"] > UE_THRESHOLD
    largest = max([UE_LARGEST_TEMPLATE] + [len(t.bones) for t in UE_TEMPLATES])
    bound = best["parented"] / largest if best else 0.0
    prediction = "recognised" if bound > UE_THRESHOLD else "uncertain" if used else "unmatched"
    return {"prefix": prefix, "scores": scores, "best": best, "prediction": prediction,
            "recognised": prediction == "recognised", "threshold": UE_THRESHOLD, "bound": round(bound, 3),
            "largest": largest, "unmodelled": list(UE_UNMODELLED),
            "unmodelled_count": UE_TEMPLATE_COUNT - len(UE_TEMPLATES), "names": sorted(stripped),
            "prefix_hint": prefix_hint(names, None if deforming is None else
                                       [engine_name(n, fmt) for n in deforming])}


def prefix_hint(names, deforming=None):
    """(token, share) when most bones (or most deforming bones, given ``deforming``) but not every bone start with
    one prefix token (Rigify's ``DEF-``): they keep it, because the characteriser removes only what every bone
    shares. None otherwise."""
    if len(names) < 2:
        return None
    if deforming:
        found = prefix_hint(deforming + ["\0"])   # one bone without it, so a prefix all of them share still counts
        if found and not all(name.startswith(found[0]) for name in names):
            return found[0], round(sum(n.startswith(found[0]) for n in deforming) / len(deforming), 3)
    counts = {}
    for name in names:
        for sep in "-_:.":
            head, found, _ = name.partition(sep)
            if found and head:
                counts[head + sep] = counts.get(head + sep, 0) + 1
                break
    if not counts:
        return None
    token, count = max(counts.items(), key=lambda item: item[1])
    share = count / len(names)
    return (token, round(share, 3)) if 0.5 <= share < 1.0 else None


def closest(name, candidates, cutoff=0.6):
    """The candidate most like ``name`` (case-insensitive), or None."""
    folded = {c.casefold(): c for c in candidates}
    found = difflib.get_close_matches(name.casefold(), list(folded), n=1, cutoff=cutoff)
    return folded[found[0]] if found else None
