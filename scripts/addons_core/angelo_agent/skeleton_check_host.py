# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check on the host (``skeleton_check_spec``): the agent's and MCP apps' route, and the
person's report card (``skeleton_report_card``). Main thread; reads only.

Which armature: the one the call names, else the scene's only armature, else the active object when it is one; with
several and none chosen, the refusal lists their handles. An armature in Edit Mode is refused: its bones' rest
data is only current outside it.
"""

from . import skeleton_check, skeleton_facts
from .refusal_codes import RefusalError


def armature_for(window, handle=None):
    from . import scene_queries
    if handle is not None:
        armature = scene_queries.resolve_object(window, handle)
        if armature.type != "ARMATURE":
            raise RefusalError("armature must be an armature object", code="VALIDATION")
        return armature
    found = skeleton_facts.armatures(window.scene)
    if len(found) == 1:
        return found[0]
    active = window.view_layer.objects.active
    if active is not None and active.type == "ARMATURE" and active in found:
        return active
    if not found:
        raise RefusalError("The scene has no armature", code="VALIDATION")
    handles = ", ".join("%s (%s)" % (scene_queries.object_handle(obj), obj.name[:64]) for obj in found[:8])
    raise RefusalError("The scene has %d armatures; name one (armature): %s" % (len(found), handles),
                       code="VALIDATION")


def report(scene, armature, target, fmt="AUTO"):
    """The full report for ``armature`` (an armature object in ``scene``) against ``target``."""
    if armature.mode == "EDIT" or getattr(armature.data, "is_editmode", False):
        raise RefusalError("The armature is in Edit Mode; leave it so its bones are current", code="VALIDATION")
    try:
        facts = skeleton_facts.gather(scene, armature)
    except skeleton_facts.FactsError as error:
        raise RefusalError(str(error), code="VALIDATION") from None
    result = skeleton_check.check(facts, target, fmt)
    if facts.get("note"):
        result["notes"].append(facts["note"])
    return result


def report_for(scene, name, target, fmt="AUTO"):
    """The person's card: the report for the armature named ``name`` in ``scene``, looked up again each time."""
    armature = scene.objects.get(name)
    if armature is None or armature.type != "ARMATURE":
        raise LookupError("The armature %r is gone" % name[:64])
    return report(scene, armature, target, fmt)


def execute(window, name, request):
    armature = armature_for(window, request.get("armature"))
    try:
        result = report(window.scene, armature, request["target"], request["format"])
    except ValueError as error:
        raise RefusalError(str(error), code="VALIDATION") from None
    from . import scene_queries
    out = skeleton_check.for_agent(result)
    out["armature"] = {"handle": scene_queries.object_handle(armature), "name": armature.name}
    return out
