# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a material a program appends lands, checked before the operation runs. No Blender import.

The program plans a material's slot from the slots it knows (``program_material_obligations.place``); the native
operation appends at the count it finds. An earlier operation that changed the object's slots makes them differ.
"""

from .program_jobs import ProgramError

SLOT_CHANGING = ("modifier_apply", "object_convert", "mesh_separate")   # The planner models joins itself.


def _lands_held(stage, operation, effect, target, slots, planned):
    """Whether the native operation puts the material in the slot that already holds it, at ``planned``: only a
    material_assign of an existing material (no copy) on its target, or any ``also`` object, and only when that slot
    holds that very material and no earlier slot does (``action_materials.assign`` and ``also`` take the first)."""
    reuse = target != operation["target"] or operation["kind"] == "material_assign" and \
        operation["parameters"].get("copy_as") is None
    if not reuse or planned >= len(slots) or slots[planned].material is None:
        return False
    from .program_material_provenance import matches_resource
    held = [index for index, slot in enumerate(slots)
            if matches_resource(stage.context, slot.material, effect["resource"])]
    return held[:1] == [planned]


def appended_where_planned(stage, operation, effect, index):
    """A material the program appends (slot -1, not held) lands at the slot count its object has when the operation
    runs (``action_materials.place``, ``also``); the program planned that slot from the slots it knew. When an
    earlier operation changed the object's slots (applying a modifier: a Boolean adds a slot for its operand, a
    Geometry Nodes Set Material its material), the material goes elsewhere and the operation fails for certain:
    refused here, before it runs, with what to change, never left to fail as unexplained after it."""
    operations = stage.binding["program"]["operations"]
    for target, planned in effect["append"].items():
        slots = stage.context.resolve(target).material_slots
        if len(slots) == planned or _lands_held(stage, operation, effect, target, slots, planned):
            continue
        earlier = [(number, item["kind"]) for number, item in enumerate(operations[:index])
                   if item.get("target") == target and item["kind"] in SLOT_CHANGING]
        step = ("operation %d (%s)" % earlier[-1]) if earlier else "an earlier operation of this program"
        raise ProgramError(
            "%s (operation %d) on %s: the program planned this material for slot %d of %s, from the %d material "
            "slot(s) it knew, but %s has %d at this point because %s changed its material slots (applying a "
            "modifier can: a Boolean adds a slot for its operand). Nothing was changed. Give the material before "
            "that operation, or run the operations up to it as a program of their own and give the material in a "
            "later program, which then sees the object's slots." % (
                operation["kind"], index, operation["target"], planned, target, planned, target, len(slots), step),
            code="VALIDATION", details={"operation": index, "object": target, "planned_slot": planned,
                                        "slots": len(slots)})
