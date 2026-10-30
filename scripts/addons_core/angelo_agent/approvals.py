# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Readable approval summaries and complete, schema-bounded sculpt details. Card text is translated at display time
through ``ui_text.iface_`` with whole sentences and named placeholders; exact Details keep the submitted values."""

from collections.abc import Mapping

from .ui_text import iface_, plural_
from .recovery_review import (_recovery_text, _changed_values, _review_json_default,
                              _evidence_value, _with_recovery)


def _number(value):
    # repr is the shortest round-trippable float; :g's six significant digits
    # made materially different fine-detail strokes look identical in review.
    return repr(value) if isinstance(value, float) else str(value)


def position(values):
    return ", ".join(axis + " " + _number(value) for axis, value in zip(("X", "Y", "Z"), values))


def _label(value):
    return value.replace('_', ' ').title()


def _range(values):
    low, high = min(values), max(values)
    return _number(low) if low == high else iface_("{low} to {high}", low=_number(low), high=_number(high))


def _coordinates(step):
    region = step.get('region')
    return iface_("World offsets from region “{region}”", region=region) if region else iface_("Absolute world coordinates")


def _protection(step):
    masks = ', '.join(_label(mask) for mask in step.get('automasking', [])) or iface_("None")
    if step.get('region'):
        masks = iface_("{masks}; region face-set constraint also applies when configured", masks=masks)
    symmetry = iface_("None") if step['symmetry'] == 'NONE' else iface_("local {axes}", axes=step['symmetry'])
    return (iface_("Symmetry: {symmetry} · Front faces only: {front}", symmetry=symmetry,
                   front=iface_("Yes") if step['front_faces_only'] else iface_("No")) + '\n' +
            iface_("Falloff: {falloff} · Automatic masking: {masking}", falloff=_label(step.get('falloff', 'SMOOTH')), masking=masks))


def _pass_summary(step, index=None):
    points = [point for path in step['paths'] for point in path]
    coordinates = ', '.join(axis + ' ' + _range([p['position'][i] for p in points])
                            for i, axis in enumerate(('X', 'Y', 'Z')))
    head = iface_("{brush} · {direction} · radius {radius} · strength {strength}", brush=_label(step['brush']),
                  direction=_label(step['direction']), radius=_number(step['radius']), strength=_number(step['strength']))
    if index is not None:
        head = iface_("{number}. {summary}", number=index, summary=head)
    counts = iface_("{paths}, {points} · pressure {pressure} · radius scale {scale} · tilt X {tilt_x}, Y {tilt_y}",
                    paths=plural_(len(step['paths']), "{count} path", "{count} paths"),
                    points=plural_(len(points), "{count} control point", "{count} control points"),
                    pressure=_range([p['pressure'] for p in points]), scale=_range([p.get('radius_scale', 1) for p in points]),
                    tilt_x=_range([p.get('tilt', [0, 0])[0] for p in points]), tilt_y=_range([p.get('tilt', [0, 0])[1] for p in points]))
    return '\n'.join((head, iface_("{coordinates}: {values}", coordinates=_coordinates(step), values=coordinates), counts,
                      _protection(step), iface_("Outward world normal: {normal} · spacing {spacing} × radius",
                                                normal=position(step['normal']), spacing=_number(step['spacing']))))


def _pass_details(step, index=None):
    brush = _label(step['brush'])
    lines = [iface_("Pass {number}: {brush}", number=index, brush=brush) if index is not None else brush,
             iface_("Direction: {label} ({identifier})", label=_label(step['direction']), identifier=step['direction']),
             iface_("Base radius: {radius} world units", radius=_number(step['radius'])),
             iface_("Strength: {strength}", strength=_number(step['strength'])),
             _protection(step),
             iface_("Outward viewing normal (world): {normal}", normal=position(step['normal'])),
             iface_("Spacing: {spacing} × radius", spacing=_number(step['spacing'])),
             iface_("Existing masks and hidden faces remain protected."),
             iface_("Coordinate mode: {mode}", mode=_coordinates(step))]
    if step.get('region'):
        lines.append(iface_("Each point is an offset from the region’s current surface anchor. "
                            "The region radius bounds stroke centers; brush falloff can extend beyond that radius."))
    lines.append(iface_("Every requested control point is listed below in execution order; "
                        "pressure, radius scale and pen tilt are interpolated along the path."))
    for number, path in enumerate(step['paths'], 1):
        lines.append('\n' + plural_(len(path), "Path {number} ({count} control point):", "Path {number} ({count} control points):",
                                    number=number))
        for point_index, point in enumerate(path, 1):
            tilt = point.get('tilt', [0, 0])
            fields = dict(index=point_index, position=position(point['position']), pressure=_number(point['pressure']),
                          scale=_number(point.get('radius_scale', 1)), tilt_x=_number(tilt[0]), tilt_y=_number(tilt[1]))
            lines.append('  ' + (iface_("{index}. Offset {position}; pressure {pressure}; radius scale {scale}; tilt X {tilt_x}, Y {tilt_y}", **fields)
                                 if step.get('region') else
                                 iface_("{index}. Position {position}; pressure {pressure}; radius scale {scale}; tilt X {tilt_x}, Y {tilt_y}", **fields)))
    return '\n'.join(lines)


def _target_name(operation, target_labels=None):
    """A card names objects as the artist knows them; exact handles stay in Details."""
    target = operation.get("target")
    if not target:
        name = (operation.get("parameters") or {}).get("name") if isinstance(operation.get("parameters"), Mapping) else None
        return iface_("new “{name}”", name=str(name).replace("\n", " ")) if name else iface_("new object")
    label = (target_labels or {}).get(target) if isinstance(target, str) else None
    if label:
        return iface_("“{name}”", name=str(label).replace("\n", " "))
    return iface_("new object") if str(target).startswith("$") else iface_("an object")


def _review_obligations(call, binding=None):
    """Exact admission evidence; the returned private bundle is never UI authority."""
    from .program_checks import FrozenObligations, resolve_obligations
    from .program_context import program_binding
    program = getattr(call, "program_arguments", None)
    program = program if program is not None else call.arguments
    bound = program_binding(binding if binding is not None else getattr(call, "precondition", None))
    obligations = bound.obligations if bound is not None else None
    if obligations is None:
        obligations = resolve_obligations(program)  # Unresolved bound programs fail closed.
    if not isinstance(obligations, FrozenObligations):
        raise ValueError("The program's private verification obligations are unavailable; submit a fresh request")
    obligations.assert_integrity(program)
    if bound is not None and bound.checks_digest and obligations.checks_digest != bound.checks_digest:
        raise ValueError("The program's required verification changed before review; submit a fresh request")
    return obligations


def _material_review(checks, effects, facts, operations):
    """Project every private identity conjunction into artist-readable controls.

    This edits only detached decoded checks. Resource tokens stay in the frozen
    bundle; shared references name actual selected target slots in the review.
    """
    from .program_material_obligations import GUARD
    origins, selected, groups = {}, {}, {}
    for owner in facts.get("owners", ()):
        for slot in owner["slots"]:
            token = slot["resource"]
            source = {"target": owner["target"], "slot": slot["slot"]}
            origins.setdefault(token, {"original_material": source})
            for name, tree in facts.get("resources", {}).get(token, {}).get("groups", {}).items():
                origins.setdefault(tree, {"original_node_group": dict(source, group=name)})
    for effect in effects:
        source = {"step": effect["operation"] + 1, "target": effect["target"], "slot": effect["slot"]}
        if effect["mode"] in {"create", "copy", "isolate"}:
            label = {"create": "created_by", "copy": "copied_by", "isolate": "isolated_by"}[effect["mode"]]
            origins.setdefault(effect["resource"], {label: source})
        if effect.get("tree_resource") != effect.get("tree_source"):
            group = operations[effect["operation"]]["parameters"]["group"]
            origins.setdefault(effect["tree_resource"], {"isolated_node_group_by": dict(source, group=group)})
    for check in checks:
        guard = check.get(GUARD)
        if guard is not None:
            selected.setdefault(guard["resource"], {"target": check["target"], "slot": guard["slot"]})
            if "tree_resource" in guard:
                groups.setdefault(guard["tree_resource"], dict(selected[guard["resource"]], group=check.get("group")))

    def origin(token):
        if token not in origins:
            raise ValueError("The program's material identity cannot be shown completely; submit a fresh request")
        return origins[token]

    for check in checks:
        guard = check.pop(GUARD, None)
        if guard is None:
            continue
        identity = dict(origin(guard["resource"]), selected_slot=guard["slot"])
        peer = selected[guard["resource"]]
        if peer["target"] != check["target"] or peer["slot"] != guard["slot"]:
            identity["same_material_as"] = peer
        if "tree_resource" in guard:
            identity["node_group_identity"] = dict(origin(guard["tree_resource"]))
            peer = groups[guard["tree_resource"]]
            if peer["target"] != check["target"]:
                identity["node_group_identity"]["same_node_group_as"] = peer
        check["material_identity"] = identity
    shown_effects = []
    for effect in effects:
        shown = {"step": effect["operation"] + 1, "target": effect["target"], "slot": effect["slot"],
                 "action": effect["mode"], "material_identity": origin(effect["resource"])}
        if effect.get("source") is not None:
            shown["source_material"] = origin(effect["source"])
        if effect.get("tree_resource") is not None:
            shown["node_group_identity"] = origin(effect["tree_resource"])
            shown["source_node_group"] = origin(effect["tree_source"])
        shown_effects.append(shown)
    return shown_effects


def _shared_review(facts, operations):
    """Each step that edits a material for every object using it, with the other objects it changes.

    From the bound facts the approval was prepared with (``material_sharing``,
    ``program_material_facts``): the other objects of the bound scene (at most
    32 names) and their count. A step whose material has no other user is an
    ordinary edit and is not listed; one whose users the facts do not name (a
    material an earlier step made or assigned) keeps ``other_users`` null.
    """
    from .material_sharing import ALL_USERS, PROPERTY_FACTS
    owners = {owner["target"]: owner for owner in facts.get("owners", ())}
    rows = []
    for index, operation in enumerate(operations):
        params = operation["parameters"]
        if params.get("shared") != ALL_USERS:
            continue
        target = operation["target"]
        if operation["kind"] == "property_set":  # The target's live active material, bound by material_sharing.
            fact = next((row for row in facts.get(PROPERTY_FACTS, ()) if row["target"] == target), None)
            if fact is None:
                continue  # A new object's material: nothing else uses it, and the kernel refuses any other.
            name = fact["material"]
        else:
            name = params.get("material", params.get("name"))
            token = next((slot["resource"] for slot in owners.get(target, {}).get("slots", ()) if slot["name"] == name),
                         None)
            fact = facts.get("resources", {}).get(token, {}).get("sharing", {}).get(target) if token else None
        if fact is not None and not fact["others"]:
            continue
        known = fact is not None and "other_users" in fact
        row = {"step": index + 1, "material": name, "other_users": fact["other_users"] if known else None,
               "other_count": fact["other_count"] if known else None}
        if known and fact.get("holders"):
            row["holders"] = fact["holders"]  # Data no object uses that holds the material: it changes too.
        rows.append(row)
    return rows


def _shared_lines(rows):
    """Warning sentences naming who an edit for all users changes (at most four steps, eight names each)."""
    lines = []
    for row in rows[:4]:
        names, count = row["other_users"], row["other_count"]
        if names is None:
            lines.append(iface_("Step {step} changes material “{material}” for every object that uses it.",
                                step=row["step"], material=row["material"] or "?"))
            continue
        shown = ", ".join(iface_("“{name}”", name=name) for name in names[:8])
        if count > len(names[:8]):
            shown = iface_("{names} and {count} more", names=shown, count=count - len(names[:8]))
        if count:
            lines.append(plural_(count, "Step {step} also changes material “{material}” on {count} other object: {names}.",
                                 "Step {step} also changes material “{material}” on {count} other objects: {names}.",
                                 step=row["step"], material=row["material"], names=shown))
        if row.get("holders"):
            lines.append(iface_("Step {step} also changes it in data no object uses: {names}.", step=row["step"],
                                names=", ".join(iface_("“{name}”", name=name) for name in row["holders"][:8])))
    if len(rows) > 4:
        lines.append(plural_(len(rows) - 4, "And {count} more shared material edit; see Shared edits in Details.",
                             "And {count} more shared material edits; see Shared edits in Details."))
    return lines


def _review_arguments(call, obligations=None):
    """What an approval shows: a recipe call shows its host-expanded program,
    and a flow run its planned card with the user's current choices.

    Recovery evidence is a detached host snapshot, never a submitted argument.
    The display copy does not change the program or its execution authority.
    """
    if call.name == "angelo_flow_run":
        return dict(call.arguments, _review=call.precondition, _choices=getattr(call, "flow_selection", None))
    if call.name == "angelo_job_start" and isinstance(call.precondition, tuple) and len(call.precondition) == 3:
        import json  # The host's admission facts for this exact request, and what the card also shows (job_host).
        return dict(call.arguments, _facts=dict(json.loads(call.precondition[2]),
                                                **(getattr(call.precondition, "shown", None) or {})))
    program = getattr(call, "program_arguments", None)
    if call.name == "angelo_scene_program" or call.name == "angelo_apply_recipe" and program is not None:
        program = program if program is not None else call.arguments
        obligations = obligations if obligations is not None else _review_obligations(call)
        checks, effects = obligations.checks(), obligations.effects()
        effects = _material_review(checks, effects, obligations.facts(), program["operations"])
        shown = dict(program, expected_checks=checks)
        if effects:
            shown["material_effects"] = effects
        shared = _shared_review(obligations.facts(), program["operations"])
        if shared:
            shown["shared_edits"] = shared
        if obligations.unknowns():
            shown["unverified_effects"] = [{"step": row["operation"] + 1, "root": row["root"],
                "reason": row["reason"].replace("_", " "), "affected": row["affected"]} for row in obligations.unknowns()]
        arguments = dict(call.arguments, program=shown) if call.name == "angelo_apply_recipe" else shown
    else:
        arguments = call.arguments
    if call.name in {"angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"}:
        arguments = {key: value for key, value in arguments.items() if key != "_recovery"}
        evidence = getattr(call, "recovery_evidence", None)
        if getattr(call, "recovery_review", False) and evidence:
            arguments["_recovery"] = evidence
    return arguments


def review_arguments(call):
    """Detached review controls; native canvas draws use approval_review.review_rows."""
    from .approval_review import review_arguments as detached_arguments
    return detached_arguments(call)


def approval_message(name, args, usage_label="", target_labels=None, chat_id=""):
    if name == "angelo_apply_recipe":
        if "program" not in args:
            # Expansion precedes every approval; never present an unexpanded recipe.
            return {"title": iface_("Expanding {recipe} recipe", recipe=args["recipe"]),
                    "body": iface_("Waiting for the host's exact expansion."),
                    "warning": iface_("Nothing runs before the expanded program is reviewed.")}
        message = approval_message("angelo_scene_program", args["program"], target_labels=target_labels)
        return _with_recovery(dict(message, title=iface_("{recipe} recipe · {summary}", recipe=args["recipe"], summary=message["title"]),
                                  body=iface_("Planned from the object as it is right now.") + "\n" + message["body"]), args)
    if name == "angelo_expand_recipe":
        return {"title": iface_("Plan the {recipe} recipe (read-only)", recipe=args["recipe"]),
                "body": iface_("Work out the exact steps for {target}.", target=args["target"]),
                "warning": iface_("Planning changes nothing. You will review every step before anything is applied.")}
    if name == "angelo_memory_update":
        from .agent_workspace_ui import readable_controls
        return {"title": iface_("Update Project Brief"),
                "body": readable_controls(args["patch"], iface_("Replace these fields in the Project Brief")),
                "warning": iface_("Save notes only. Memory does NOT authorize any future scene edit, paid use, file/network access "
                                  "or replay. Reference and asset pointers need existing host-checked evidence.")}
    if name == "angelo_scene_program":
        preview = args["mode"] == "PREVIEW"
        operations = args["operations"]
        from .agent_workspace_ui import control_lines
        # The step kind is the exact action name (Details use the same words), not translated prose.
        from .approval_steps import grouped_steps
        lines = grouped_steps(operations, lambda kind: _label(kind).capitalize(),
                              lambda op: _target_name(op, target_labels))
        lines += ["", iface_("Open Details to see every exact setting and check.")]
        count = len(operations)
        if preview:
            title = plural_(count, "Preview {count} change", "Preview {count} changes")
            warning = iface_("Angelo builds a preview on a copy. Your scene doesn't change until you accept it. Stop cancels between steps.")
        else:
            title = plural_(count, "Apply {count} change", "Apply {count} changes")
            warning = iface_("Angelo works on a copy, checks that your scene hasn't changed, then applies everything "
                             "as one Undo step. Stop cancels between steps.")
        if args.get("unverified_effects"):
            warning += " " + iface_("Verification is partial. Some effects cannot be checked automatically; see Unverified effects in Details.")
        if args.get("shared_edits"):
            warning += "\n" + "\n".join(_shared_lines(args["shared_edits"]))
        return _with_recovery({"title": title, "body": "\n".join(lines), "warning": warning}, args)
    if name in {"angelo_accept_preview", "angelo_discard_preview"}:
        accept = name == "angelo_accept_preview"
        message = {"title": iface_("Apply the preview") if accept else iface_("Discard the preview"),
                   "body": iface_("Preview {preview}", preview=args["preview_id"]),
                   "warning": (iface_("Check the preview first. Angelo re-checks your scene before applying and never overwrites changes you made since.")
                               if accept else iface_("Throws away this preview only. Changes already applied stay."))}
        return _with_recovery(message, args) if accept else message
    if name == "angelo_set_workspace_mode":
        return {"title": iface_("Enter Director Mode") if args["mode"] == "DIRECTOR" else iface_("Show manual controls"),
                "body": iface_("Switch workspace presentation; preserve the original manual editors and chat."),
                "warning": iface_("No scene or account changes.")}
    if name.startswith("angelo_studio_"):
        from .studio_approvals import message
        return message(name, args, usage_label)
    if name.startswith("angelo_flow_"):
        from .flow_approvals import message
        return message(name, args, usage_label)
    if name.startswith("angelo_team_"):
        from .team_library_approvals import message
        return message(name, args)
    if name.startswith("angelo_job_"):
        from .job_approvals import message
        return message(name, args, usage_label)
    if name == 'angelo_sculpt_program':
        return {'title': iface_("Sculpt {object}", object=(target_labels or {}).get(args['object'], args['object'])),
                'body': '\n\n'.join(_pass_summary(step, i + 1) for i, step in enumerate(args['steps'])) +
                '\n\n' + iface_("Open Details for every ordered stroke point and exact control value."),
                'warning': iface_("One bounded native program and one Undo. Stop takes effect between programs. "
                                  "Partial failures retain recovery details.")}
    if name == 'angelo_sculpt_region':
        constraint = (iface_("Limit deformation to existing face set {face_set}.", face_set=args['face_set']) if args['face_set']
                      else iface_("No additional face-set constraint."))
        return {'title': iface_("Define {region} on {object}", region=args['name'], object=args['object']),
                'body': iface_("Save a surface landmark near {position} (world coordinates).", position=position(args['position'])) +
                '\n' + iface_("Region radius: {radius} world units. {constraint}", radius=_number(args['radius']), constraint=constraint),
                'warning': iface_("Follows the surface as it changes. Redefine after changing topology or working level.")}
    if name == 'angelo_sculpt_resolution':
        selection = (iface_("Automatic brush-footprint selection") if args['mode'] == 'AUTO'
                     else iface_("Level {level}", level=args['level']))
        return {'title': iface_("Set sculpt working resolution"),
                'body': iface_("{object} · {stage} · {selection}", object=(target_labels or {}).get(args['object'], args['object']), stage=args['stage'].title(), selection=selection) +
                '\n' + iface_("Brush radius: {radius} world units.", radius=_number(args['radius'])),
                'warning': iface_("Selects an existing Multires level; preserves higher detail and base topology.")}
    if name == 'angelo_prepare_sculpt':
        levels = args['add_levels']
        return {"title": iface_("Prepare {object} for sculpting", object=(target_labels or {}).get(args['object'], args['object'])),
                "body": (plural_(levels, "Add {count} non-destructive Multires level and enter Sculpt Mode.",
                                 "Add {count} non-destructive Multires levels and enter Sculpt Mode.") if levels else
                         iface_("Select this mesh and enter Sculpt Mode without changing its topology.")),
                "warning": iface_("Preserves base mesh, UVs and masks. Working resolution is bounded; "
                                  "no automatic remeshing or modifier application.")}
    if name == 'angelo_sculpt_batch':
        return {"title": iface_("{brush} sculpt pass on {object}", brush=args['brush'].title(), object=(target_labels or {}).get(args['object'], args['object'])),
                "body": _pass_summary(args) + '\n\n' + iface_("Open Details for every ordered stroke point and exact control value."),
                "warning": iface_("Uses native Blender brushes. Masks and hidden faces are protected. "  # app-name: stock Blender's own sculpt brushes, used unchanged
                                  "One Undo reverts the pass; Stop takes effect between passes.")}
    if name == "angelo_delegate":
        task = args["task"]
        return {"title": (iface_("Consult {model} via {account}", model=args["model"], account=usage_label) if usage_label
                          else iface_("Consult {model}", model=args["model"])),
                "body": iface_("{task}… Open Details for the full question.", task=task[:800]) if len(task) > 800 else task,
                "warning": iface_("Read-only collaborator. Uses the selected account's subscription quota or API billing; "
                                  "Fast is off. It cannot edit, run scripts or delegate again.")}
    if name == "angelo_set_transform":
        return {"title": iface_("Move {object}", object=args['object']),
                "body": iface_("Set its position to {position} (Blender units).", position=position(args["location"])), "warning": ""}
    if name == "angelo_create_cube":
        return {"title": iface_("Add a cube named {name}", name=args['name']),
                "body": iface_("Size {size}; position {position}.", size=format(args['size'], 'g'), position=position(args["location"])),
                "warning": ""}
    if name == "angelo_apply_batch":
        operations = args["operations"]
        lines = ["• " + approval_message(op["tool"], op["arguments"])["title"] for op in operations[:4]]
        if len(operations) > 4:
            lines.append(plural_(len(operations) - 4, "And {count} more edit. Open Details to review all of them.",
                                 "And {count} more edits. Open Details to review all of them."))
        return {"title": plural_(len(operations), "Apply {count} scene edit", "Apply {count} scene edits"),
                "body": "\n".join(lines), "warning": ""}
    if name == "angelo_execute_python":
        from .fallback_categories import uses_preferences
        warning = iface_("A script can do anything Angelo can, including reading files or using the network. "
                         "Effects outside the scene can't be undone, and Stop can't interrupt a running script.")
        if uses_preferences(args["code"]):
            warning += "\n" + iface_("This script uses Angelo's preferences. A change there applies to every project, "
                                     "and neither Undo nor a snapshot restores it.")
        return {"title": args["description"], "body": iface_("The agent will run this Blender Python code. Review it before approving."),  # Never claims a change: it may only read.
                "warning": warning}
    if name == "angelo_write_file":
        return {"title": iface_("Write {path}", path=args["path"]),
                "body": plural_(len(args['content'].encode('utf-8')), "Save {count:,} byte of text in the selected file workspace.",
                                "Save {count:,} bytes of text in the selected file workspace."),
                "warning": iface_("Existing contents will be replaced. Restoring a snapshot reverts this file; Undo doesn't.")}
    if name == "angelo_delete_file":
        return {"title": iface_("Delete {path}", path=args["path"]), "body": iface_("Remove this file from the selected workspace."),
                "warning": iface_("A copy is kept, so restoring a snapshot brings the file back. Undo doesn't.")}
    if name in {"angelo_workers_start", "angelo_workers_merge"}:
        from . import worker_approval
        return (worker_approval.message(args) if name.endswith("start")
                else worker_approval.merge_message(args, worker_approval.merge_facts(chat_id)))
    if name.startswith("angelo_web_"):
        from .web_export_ui import approval_message as web_message
        return web_message(name, args)
    return {"title": iface_("Approve this step"), "body": name.replace("angelo_", "").replace("_", " "), "warning": ""}


def approval_details(name, args, target_labels=None):
    if name in {"angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"} and "_recovery" in args:
        explanation = _recovery_text(args, complete=True)
        submitted = {key: value for key, value in args.items() if key != "_recovery"}
        details = approval_details(name, submitted, target_labels=target_labels)
        return explanation + "\n\n" + details if explanation else details
    if name == "angelo_apply_recipe" and "program" in args:
        from .agent_workspace_ui import readable_controls
        message = approval_message(name, args, target_labels=target_labels)
        # Every canonical control of the program that will be staged, then the
        # recipe inputs (defaults included) that produced it.
        return (readable_controls(args["program"], message["title"], target_labels=target_labels) + "\n\n" +
                readable_controls({"recipe": args["recipe"], "parameters": args["parameters"]},
                                  iface_("Recipe inputs, defaults included"), target_labels=target_labels)
                + "\n\n" + message["warning"])
    if name == "angelo_expand_recipe":
        from .agent_workspace_ui import readable_controls
        message = approval_message(name, args)
        return readable_controls(args, message["title"], target_labels=target_labels) + "\n\n" + message["warning"]
    if name == "angelo_memory_update":
        message = approval_message(name, args)
        return message["title"] + "\n" + message["body"] + "\n\n" + message["warning"]
    if name == "angelo_scene_program":
        from .agent_workspace_ui import readable_controls
        message = approval_message(name, args)
        return readable_controls(args, message["title"], target_labels=target_labels) + "\n\n" + message["warning"]
    if name in {"angelo_accept_preview", "angelo_discard_preview"}:
        message = approval_message(name, args)
        return message["title"] + "\n" + message["body"] + "\n\n" + message["warning"]
    if name.startswith(("angelo_flow_", "angelo_job_")):
        import json
        summary = approval_message(name, args)
        shown = {k: v for k, v in args.items() if not k.startswith("_")}
        return summary["title"] + "\n" + summary["body"] + "\n\n" + json.dumps(shown, indent=2, ensure_ascii=False) + "\n\n" + summary["warning"]
    if name.startswith("angelo_studio_"):
        import json
        summary = approval_message(name, args)
        return summary["title"] + "\n" + json.dumps(args, indent=2, ensure_ascii=False) + "\n\n" + summary["warning"]
    if name in {'angelo_sculpt_batch', 'angelo_sculpt_program'}:
        message = approval_message(name, args)
        steps = args['steps'] if name == 'angelo_sculpt_program' else [args]
        # validate_call bounds this to eight passes, 256 controls per pass and
        # one shared 512-dab budget. Never slice a purportedly complete review.
        return (message['title'] + '\n' + iface_("Target mesh revision: {revision}", revision=args['mesh_revision']) + '\n\n' +
                '\n\n'.join(_pass_details(step, i+1) for i, step in enumerate(steps)) + '\n\n' + message['warning'])
    if name in {'angelo_sculpt_region', 'angelo_sculpt_resolution', 'angelo_prepare_sculpt'}:
        message = approval_message(name, args)
        return (message['title'] + '\n' + message['body'] + '\n' + iface_("Target mesh revision: {revision}", revision=args['mesh_revision']) +
                '\n\n' + message['warning'])
    if name == "angelo_delegate":
        return (iface_("Account: {account}", account=args["connection_id"]) + "\n" + iface_("Model: {model}", model=args["model"]) +
                "\n\n" + args["task"])
    if name == "angelo_execute_python":
        return args["code"]
    if name == "angelo_apply_batch":
        return "\n\n".join(approval_message(op["tool"], op["arguments"])["title"] + "\n" + approval_message(op["tool"], op["arguments"])["body"] for op in args["operations"])
    if name == "angelo_write_file":
        return args["path"] + "\n\n" + args["content"]
    message = approval_message(name, args)
    return message["title"] + "\n" + message["body"]
