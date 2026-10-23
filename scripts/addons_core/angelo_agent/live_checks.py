# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every check of a program, re-run on the live objects after commit's writes, inside its rollback scope.

Checks run on the stage first (``program_jobs`` VALIDATE, and again immediately
before commit). Commit then reads every declared and derived check again from
the live scene, after all writes, renames, collection and visibility intents
and a view-layer update, so the certificate's "Host checks passed on the live
result" is literal: what each check reads (geometry, transforms, settings,
materials, sharing, memberships) is read from the objects the artist now has.
A check that passed on the stage and fails live raises ``COMMIT_FIDELITY``
(``CHECK_FAILED``) before the commit returns, and the journal rolls the whole
commit back (without it, copies of a linked duplicate, certified as
linked, shared a separate mesh; a material write lost to a later slot removal
was listed as a change).

The live context needs no stage overlay: targets are the live originals, the
new objects (by alias) and the bound scene; deleted objects resolve to nothing.
A world-space or evaluated measure of an object the live view layer does not
evaluate (disabled in viewports, excluded) is computed from base data and its
parent chain, or, when base data cannot reproduce it, is unobservable: the run
is partial and nothing about it is certified (``validation_reader``).
A material check guarded by a staged identity proof (``__host_material``)
reads the material of the same slot of the live object, the proof itself
having held on the stage; a node-link guard reads the live tree.
"""

import copy

from .program_jobs import ProgramError


def live_context(backend, stage):
    from .scene_validation import VerificationContext
    deleted = set(stage.extras.get("deleted", ()))
    targets = {handle: obj for handle, obj in stage.originals.items()
               if handle not in deleted and backend.scene.objects.get(obj.name) == obj}
    targets.update({"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()})
    targets.update({handle: backend.scene for handle in stage.context.scene_handles})
    return VerificationContext(targets=targets, scene_objects=list(backend.scene.objects), bindings={},
                               view_layer=backend.view_layer, depsgraph=backend.view_layer.depsgraph)


def _report(context, check):
    from .scene_validation import verify
    check = copy.deepcopy(check)
    guard = check.pop("__host_material", None)
    link_guard = check.pop("__host_node_links", None)
    owner = context.targets.get(check.get("target"))
    if guard is not None:
        slots = owner.material_slots if owner is not None and hasattr(owner, "material_slots") else ()
        material = slots[guard["slot"]].material if 0 <= guard["slot"] < len(slots) else None
        if material is None:
            return False, "the bound material slot is empty or gone"
        check["material"] = material.name
        if check["kind"] in {"material_assignment", "material_values"} and not check.get("all_faces"):
            from .program_material_facts import selected_material_report
            report = selected_material_report(material, check, owner if guard.get("rendered") else None,
                                              guard["slot"])
        else:
            report = verify(context, [check])
    else:
        report = verify(context, [check])
    if report.get("status") == UNOBSERVABLE:
        return False, UNOBSERVABLE
    if not report["success"]:
        row = (report.get("checks") or [{}])[0]
        return False, row.get("reason") or str(row.get("observed"))[:200]
    if link_guard is not None:
        from .program_node_link_checks import verify_absent_links
        links = verify_absent_links(owner, check, link_guard)
        if not links["passed"]:
            return False, links.get("reason", "a removed link is present")
    return True, ""


UNOBSERVABLE = "unobservable"


def verify_live(backend, stage, checks, unobservable):
    """Raise unless every check passes, or is unobservable, on the live objects; return how many were read live.

    ``unobservable``: indexes of checks the stage could not measure; a check the live scene cannot measure (an
    object its view layer does not evaluate, measured from data it cannot reproduce) is added to it."""
    if not checks:
        return 0
    context = live_context(backend, stage)
    read = 0
    for index, check in enumerate(checks):
        if index in unobservable:
            continue  # Already certifies nothing (the stage could not measure it).
        read += 1
        passed, reason = _report(context, check)
        if reason == UNOBSERVABLE:
            unobservable.add(index)
            continue
        if not passed:
            raise ProgramError("COMMIT_FIDELITY: check %d (%s) passed on the staged copy but not on the committed "
                               "scene (%s); nothing was changed" % (index + 1, check.get("kind"), reason[:200]),
                               code="CHECK_FAILED")
    return read
