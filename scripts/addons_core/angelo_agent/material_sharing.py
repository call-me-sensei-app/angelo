# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One material for several objects: who uses it, and an edit for all of them.

``shared: ALL_USERS`` (``material_principled``, ``shader_nodes`` and
``node_edit`` on a material's own tree) edits a material every object that
uses it shows. The rules keep that edit inside what the program can state:

* The material is one the target itself has, found by its exact name on the
  target's slots; nothing is looked up by name in the file.
* Every user must be an object of the bound scene, directly (an object-linked
  slot) or through its data. A user outside the scene, or any other datablock
  (a Geometry Nodes group, a brush, a line style), refuses the edit
  (``SHARED_OUTSIDE_SCENE``): the program observed none of them. Data no object
  uses, and a fake user, are not users.
* Staging records the users' names; commit reads them again and refuses a
  changed set, so an object that gained or lost the material after approval is
  never edited unseen. The material's own state is part of the target's
  revision, which commit also checks.
* Commit gives every use of the live material the edited copy
  (``ID.user_remap``, journaled, as for a material only the target uses), so
  one Undo step and a failed commit both restore every user.

The result names the users (``shared_edits``) and returns their new revisions
(role ``SHARED``): each one's revision includes the material's graph. Data
that holds the material while no object uses it (an orphan mesh kept by a
fake user) changes too: it is not a user, but the row names it (``holders``).

``property_set`` settings paths of the active material (``active_material.``)
write the live material in place at commit, so they follow the same rules
(otherwise every user changed and only the target was reported):

* Binding records, for every material such a step writes, its users beyond
  the target and the bound scene's objects that show it (``property_facts``).
  The rows join the program's bound obligations, so the approval can name the
  objects an edit for all users changes, and a user set that changes after
  approval or staging refuses staging or commit before any mutation.
* The kernel refuses a material with another user unless ``shared`` is
  ``ALL_USERS`` (``MATERIAL_SHARED``), a user the program cannot name
  (``SHARED_OUTSIDE_SCENE``), and a material the program did not bind for that
  target or an earlier step edited (``SHARING_UNKNOWN``)
  (``check_property_edit``).
* Commit records the ``shared_edits`` row of an edit for all users
  (``record_property_edit``), so the result, the host summary and the
  post-commit revisions (role ``SHARED``) name every object it changed.
"""

import hashlib
import json

from .node_edit_spec import EDITED, MODE, SOURCE, NodeEditError

ALL_USERS = "ALL_USERS"
# Staging marker on an edited copy: the JSON list of the bound scene's objects that use the live material.
USERS = "_angelo_node_edit_users"
# stage.extras: live material uids a material_assign of this program gave to another object.
ASSIGNED = "material_assigned"
# stage.extras: rows the committed result returns ({material, users, count}).
EDITS = "shared_edits"
MAX_NAMED_USERS = 32
# Obligation facts: the live active materials property_set writes in place, and who else uses them.
PROPERTY_FACTS = "property_materials"
# stage.extras: the bound rows the kernel checks, and those it edited for all users (by live uid) for commit.
PROPERTY_SHARING = "property_material_sharing"
PROPERTY_EDITS = "property_material_edits"


def _bpy():
    import bpy
    return bpy


def kept(users):
    """The ``users`` (a user map's) that count as users: datablocks Blender keeps and saves. One with no user and no
    fake user is not saved, and is what a commit leaves of data it replaced (the mesh a material edit swapped out,
    an earlier World) or an artist's deleted object's data: counted, it would make the next edit of a material its
    own object holds a refused shared edit. Scenes, screens and the like always count."""
    always = {"SCENE", "SCREEN", "WINDOWMANAGER", "WORKSPACE", "LIBRARY"}
    return [user for user in users if getattr(user, "id_type", None) in always or getattr(user, "users", 1)
            or getattr(user, "use_fake_user", False)]


def affected(users):
    """Of ``users``, those an edit in place changes: kept ones (``kept``) of this file. A library's own data keeps
    the library's datablock when the edit makes a local copy, as Blender's make-local leaves it, so it is neither
    a user to refuse for nor one to name as changed."""
    return [user for user in kept(users) if getattr(user, "library", None) is None]


def shared_data(data):
    """Whether object ``data`` has another user that counts (``kept``): other objects that show it."""
    if data is None or data.users < 2:
        return False
    return len(kept(_bpy().data.user_map(subset={data}).get(data, ()))) > 1


def scene_users(material, scene, owned):
    """``(objects, outside)``: the bound scene's objects that show ``material``, and the first user it cannot name.

    ``owned``: pointers of the stage's own datablocks, which are not users.
    """
    bpy = _bpy()
    users = [user for user in affected(bpy.data.user_map(subset={material}).get(material, ())) if user.as_pointer() not in owned]
    holders = [user for user in users if not isinstance(user, bpy.types.Object) and hasattr(user, "materials")]
    through = bpy.data.user_map(subset=set(holders)) if holders else {}
    return _scene_users(users, through, scene, owned)


def _scene_users(users, through, scene, owned):
    """``scene_users`` from user maps already read: ``users`` of one material, ``through`` its holders' users."""
    bpy = _bpy()
    local = {obj.as_pointer() for obj in scene.objects}
    objects, outside = {}, None
    for user in users:
        if isinstance(user, bpy.types.Object):
            candidates = [user]
        elif hasattr(user, "materials"):
            candidates = [item for item in affected(through.get(user, ())) if isinstance(item, bpy.types.Object)
                          and item.as_pointer() not in owned]
        else:
            outside = outside or "%s %r" % (user.bl_rna.name, user.name[:48])
            continue
        for obj in candidates:
            if obj.as_pointer() in local:
                objects[obj.as_pointer()] = obj
            else:
                outside = outside or "object %r outside this scene" % obj.name[:48]
    return sorted(objects.values(), key=lambda obj: obj.name), outside


def _unplaced(users, through, owned):
    """Datablocks that hold the material in their slots while no object uses them (orphan data, a fake user)."""
    bpy = _bpy()
    return sorted(user.name[:64] for user in affected(users)
                  if not isinstance(user, bpy.types.Object) and hasattr(user, "materials")
                  and user.as_pointer() not in owned
                  and not any(isinstance(item, bpy.types.Object) for item in affected(through.get(user, ()))))[:MAX_NAMED_USERS]


def unplaced_holders(material, owned):
    """``_unplaced`` for one live material: the data an edit for all its users also changes, with no object."""
    bpy = _bpy()
    users = [user for user in affected(bpy.data.user_map(subset={material}).get(material, ())) if user.as_pointer() not in owned]
    holders = [user for user in users if not isinstance(user, bpy.types.Object) and hasattr(user, "materials")]
    return _unplaced(users, bpy.data.user_map(subset=set(holders)) if holders else {}, owned)


def record(facts, material, scene, owned):
    """Add a staged material's users to its sharing facts (``node_edit_commit.prepare``)."""
    objects, outside = scene_users(material, scene, owned)
    facts.update(users=[obj.name[:64] for obj in objects], outside=outside)


def wanted(operations):
    """Whether any operation of a canonical program edits a material for all its users."""
    return any(operation["parameters"].get("shared") == ALL_USERS for operation in operations)


def mark(block, facts):
    """Mark an edited staged copy as an edit for every user; refuse users the program cannot name."""
    if "users" not in facts:
        raise NodeEditError("SHARING_UNKNOWN", "the material's users were not staged; bind the object in the write set")
    if facts["outside"]:
        raise NodeEditError("SHARED_OUTSIDE_SCENE", "%r is also used by %s, which this program did not observe; pass "
                                                    "shared ISOLATE to give only this target an edited copy, or edit "
                                                    "it with reviewed Python" % (facts["name"], facts["outside"]))
    block[MODE] = "REMAP"
    block[USERS] = json.dumps(facts["users"])


def unify(context, material, uid):
    """Give every staged object that holds a copy of the same live material the edited copy ``material``."""
    if uid is None:
        return  # A new material's actual users already hold this ID; other new IDs are unrelated.
    from .node_edit import source_uid
    for obj in list(context.objects.values()) + list(context.aliases.values()):
        data = getattr(obj.data, "materials", None)
        for index, slot in enumerate(obj.material_slots):
            other = slot.material
            if other is None or other == material or source_uid(other) != uid:
                continue
            if EDITED in other.keys() and SOURCE not in other.keys():
                from .stage_names import public
                raise NodeEditError("SHARED_CONFLICT", "an earlier step of this program already edited %r for one "
                                                       "object; edit a material once per program"
                                    % public({}, material.name)[:64])
            slot.material = material
            if slot.link == "OBJECT" and data is not None and index < len(data) and data[index] == other:
                data[index] = material


def assigned(context, uid):
    """Whether a material_assign of this program gave a copy of the live material ``uid`` to another object."""
    return uid is not None and str(uid) in context.extras.get(ASSIGNED, ())


def note_assignment(context, material):
    from .node_edit import source_uid
    uid = source_uid(material)
    if uid is not None and str(uid) not in context.extras.setdefault(ASSIGNED, []):
        context.extras[ASSIGNED].append(str(uid))


def users_of(block):
    """The user names an edit for all users recorded on its staged copy, or None for any other edit."""
    return json.loads(block[USERS]) if USERS in block.keys() else None


def verify(block, live, scene, owned, extras):
    """At commit: the live material's users are still the staged ones; record the row the result returns."""
    from .program_jobs import ProgramError
    expected = users_of(block)
    objects, outside = scene_users(live, scene, owned)
    names = [obj.name[:64] for obj in objects]
    if outside or names != expected:
        raise ProgramError("node_edit NODE_TREE_SHARED: the users of %r changed before commit (%s); observe again"
                           % (live.name[:64], outside or ", ".join(sorted(set(names) ^ set(expected))[:4])),
                           code="STALE_DEPENDENCY")
    row = {"material": live.name[:64], "users": names[:MAX_NAMED_USERS], "count": len(names),
           "truncated": len(names) > MAX_NAMED_USERS}
    holders = unplaced_holders(live, owned)
    if holders:  # Data no object uses changes too: named, never counted as a user.
        row["holders"] = holders
    extras.setdefault(EDITS, []).append(row)
    return objects


def shared_objects(stage, backend, listed):
    """Objects of the bound scene an edit for all users changed, beyond ``listed`` (written and created ones)."""
    names = {name for row in getattr(stage, "extras", {}).get(EDITS, ()) for name in row["users"]}
    if not names:
        return []
    known = {obj.as_pointer() for obj in listed}
    return [obj for obj in backend.scene.objects if obj.name[:64] in names and obj.as_pointer() not in known]


def property_operations(program):
    """(index, target) of each ``property_set`` that writes its target's live active material.

    Binding follows program order: after a ``material_assign`` with ``copy_as``,
    a ``material_principled`` or a ``shader_nodes`` on the target (the only
    material steps ``program_rules`` admits before such a write), the active
    material is one this program made, which no fact describes and the kernel
    admits as its own (otherwise LINKED_MATERIAL's advised local copy is
    refused because the live library material was bound instead).
    """
    from .action_properties import family
    made, found = set(), []
    for index, operation in enumerate(program["operations"]):
        kind, params = operation["kind"], operation["parameters"]
        if kind in ("material_principled", "shader_nodes") or (kind == "material_assign" and params.get("copy_as")):
            made.update([operation["target"]] + list(params.get("also") or ()))
        elif (kind == "property_set" and operation["target"] not in made
              and any(family(path) == "material" for path in params["values"])):
            found.append((index, operation["target"]))
    return found


def property_facts(binding, scene):
    """Bound rows for the live active materials ``property_set`` steps write in place (read-only).

    One row per (target, material): how many users it has beyond the target
    and the target's own unshared data (other objects or meshes, other
    datablocks, a fake user) and the first few by name, the bound scene's
    objects that show it (at most 32 names, and the count), the first user the
    program cannot name, and ``users_digest``, the exact user identities. Two
    user-map reads whatever the number of steps.
    """
    from .node_edit_commit import _material_rule, other_users
    bpy = _bpy()
    targets = {}
    for _index, target in property_operations(binding["program"]):
        obj = binding["originals"].get(target)
        material = getattr(obj, "active_material", None) if obj is not None else None
        if material is not None:  # A new object or no material: the kernel decides with its own code.
            targets.setdefault((target, int(material.session_uid)), (obj, material))
    if not targets:
        return []
    direct = bpy.data.user_map(subset={material for _obj, material in targets.values()})
    holders = {user for users in direct.values() for user in users
               if not isinstance(user, bpy.types.Object) and hasattr(user, "materials")}
    through = bpy.data.user_map(subset=holders) if holders else {}
    rows = []
    for (target, uid), (obj, material) in targets.items():
        users = affected(direct.get(material, set()))
        allowed, _data = _material_rule(obj)
        others, who = other_users(material, allowed, set(), users)
        objects, outside = _scene_users(users, through, scene, set())
        other_objects = [item.name[:64] for item in objects if item != obj]
        identities = [sorted(int(user.session_uid) for user in users), [int(item.session_uid) for item in objects],
                      bool(material.use_fake_user)]
        row = {"target": target, "uid": uid, "material": material.name[:64], "others": others, "who": who,
               "outside": outside, "users": [item.name[:64] for item in objects][:MAX_NAMED_USERS],
               "count": len(objects), "other_users": other_objects[:MAX_NAMED_USERS],
               "other_count": len(other_objects),
               "users_digest": hashlib.sha256(json.dumps(identities).encode()).hexdigest()}
        holders = _unplaced(users, through, set())
        if holders:
            row["holders"] = holders
        if material.library is not None or getattr(material, "override_library", None) is not None:
            row["linked"] = True  # From a library file: an edit in place is not saved with this one.
            if getattr(material, "override_library", None) is not None:
                row["override"] = True
        rows.append(row)
    return rows


def stage_property_facts(stage):
    """Give the kernel the bound rows of the program it stages (JSON, kept with the stage)."""
    bundle = stage.binding.get("obligations")
    rows = bundle.facts().get(PROPERTY_FACTS, []) if bundle is not None else []
    if rows:
        stage.extras[PROPERTY_SHARING] = rows


def check_property_edit(context, target, material, path, shared=None):
    """Refuse a ``property_set`` write of a live material other users show, unless it is for all of them.

    ``material`` is the staged clone the kernel writes; its marker names the
    live material commit writes in place. A material this program made has no
    other user and becomes live whole. Raises ``PropertyPathError``.
    """
    from .node_edit_spec import EDITED, SOURCE
    from .rna_rules import PropertyPathError
    if getattr(material, "library", None) is not None or getattr(material, "override_library", None) is not None:
        _linked(_live_name(material), path, getattr(material, "override_library", None) is not None)
    keys = material.keys()
    if EDITED in keys and SOURCE not in keys:
        raise PropertyPathError("SHARING_UNKNOWN", path, "an earlier step of this program edited %r; set its settings "
                                                         "in a program of its own" % _live_name(material)[:64])
    if SOURCE not in keys:
        return
    uid = int(material[SOURCE])
    row = next((row for row in context.extras.get(PROPERTY_SHARING, ()) if row["target"] == target and row["uid"] == uid),
               None)
    original = _original(material)
    if row is None and original is not None and (original.library is not None or original.override_library is not None):
        _linked(original.name, path, original.override_library is not None)  # Still the library material it was.
    if row is None:
        raise PropertyPathError("SHARING_UNKNOWN", path, "%r is not a material this program bound for this target (an "
                                "earlier step changed it, or the target is a copy that shares it); set its settings in a "
                                "program of its own, with an object that has it as target" % _live_name(material)[:64])
    if row.get("linked"):
        _linked(row["material"], path, row.get("override", False))
    if not row["others"]:
        return
    if shared != ALL_USERS:
        raise PropertyPathError("MATERIAL_SHARED", path, "%r is also used by %d other user(s)%s, and setting it here "
                                "would change them too; pass shared ALL_USERS to change it for every object that uses "
                                "it, or give this object its own copy first (material_assign with copy_as into its "
                                "active slot, then set the copy, in one program)" % (
                                    row["material"], row["others"], " (" + row["who"] + ")" if row["who"] else ""))
    if row["outside"]:
        raise PropertyPathError("SHARED_OUTSIDE_SCENE", path, "%r is also used by %s, which this program did not "
                                "observe; give this object its own copy first (material_assign with copy_as), or edit "
                                "it with reviewed Python" % (row["material"], row["outside"]))
    context.extras.setdefault(PROPERTY_EDITS, {})[str(uid)] = row


def _original(material):
    """The live material a staged clone stands for (by the session UID it carries), or None."""
    uid = material[SOURCE] if SOURCE in material.keys() else None
    if uid is None:
        return None
    try:
        import bpy
    except ImportError:  # Host tests: stand-in materials.
        return None
    return next((item for item in bpy.data.materials if int(item.session_uid) == int(uid)), None)


def _live_name(material):
    """The name the artist knows: the live material a staged copy stands for, never its private copy's."""
    original = _original(material)
    return original.name if original is not None else material.name


def refuse_overrides(binding):
    """Before approval: refuse material work on an object holding a library override in any slot.

    Native staging cannot copy an override material (its clone capture fails
    after approval and leaves the library material with an extra user), so
    material kinds, active_material settings and copies of it are refused at
    binding (otherwise the advised copy route reached staging).
    """
    from .action_properties import family
    kinds = {"material_assign", "material_principled", "shader_nodes", "node_edit"}
    for operation in binding["program"]["operations"]:
        params = operation["parameters"]
        if operation["kind"] not in kinds and not (operation["kind"] == "property_set" and any(
                family(path) == "material" for path in params["values"])):
            continue
        for target in [operation["target"]] + list(params.get("also") or ()):
            obj = binding["originals"].get(target)
            for slot in getattr(obj, "material_slots", ()):
                if slot.material is not None and getattr(slot.material, "override_library", None) is not None:
                    _linked(slot.material.name, "active_material", True)


def refuse_linked(program, rows):
    """At binding, before approval: refuse writing a library material's settings in place (LINKED_MATERIAL)."""
    from .action_properties import family
    for row in rows:
        if row.get("linked"):
            _linked(row["material"], next(path for operation in program["operations"]
                                          if operation["kind"] == "property_set" and operation["target"] == row["target"]
                                          for path in operation["parameters"]["values"] if family(path) == "material"),
                    row.get("override", False))


def _linked(name, path, override=False):
    from .rna_rules import PropertyPathError
    if override:
        raise PropertyPathError("LINKED_MATERIAL", path, "%r overrides a material of a linked library file, which a "
                                "native program cannot edit or copy; edit it in its library file, or use reviewed "
                                "Python" % name[:64])
    raise PropertyPathError("LINKED_MATERIAL", path, "%r comes from a linked library file, so an edit here would not be "
                            "saved with this file; give this object a local copy first: material_assign with copy_as, "
                            "or material_principled, into its active slot (slot: that slot's index), then set it in "
                            "the same program" % name[:64])


def record_property_edit(stage, live):
    """At commit, before ``live`` is written in place: the ``shared_edits`` row of an edit for all its users.

    The users are the bound ones; commit's opening verification has already
    refused a program whose user set changed since binding.
    """
    row = stage.extras.get(PROPERTY_EDITS, {}).get(str(int(live.session_uid))) if live is not None else None
    if row is None:
        return
    edits = stage.extras.setdefault(EDITS, [])
    if all(edit["material"] != live.name[:64] for edit in edits):  # Two steps writing one material: one row.
        edit = {"material": live.name[:64], "users": row["users"], "count": row["count"],
                "truncated": row["count"] > len(row["users"])}
        if row.get("holders"):
            edit["holders"] = row["holders"]
        edits.append(edit)


def property_changes(previous, current):
    """The material whose bound users changed between two bindings, for the stale message, or None."""
    rows = {(row["target"], row["uid"]): row for row in current}
    for row in previous:
        fresh = rows.get((row["target"], row["uid"]))
        if fresh is None or any(fresh.get(key) != row.get(key) for key in ("users_digest", "others", "users", "outside",
                                                                            "holders")):
            return row["material"]
    return None
