# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game rig's skeleton as plain data: names, parents and filler bones (doc/angelo/rigging.rst, "Game rig").
No Blender dependency, so the host suite pins it.

``plan(bones, parents, deform, roles, profile, template)`` decides, before any bone is made:

* **Kept bones**: the control rig's deforming bones, each hanging from its nearest deforming ancestor (the hierarchy a
  deform-only export writes), where a non-deforming ancestor that a deforming bone follows (``follows``: the bone a
  Copy Transforms or Child Of points at) counts as that deforming bone. A control rig such as a generated Rigify rig
  starts every deforming chain under control bones, so this alone is not enough: a bone that plays a role hangs
  from the bone of its nearest present ancestor role whenever its own ancestry doesn't already lead there (twist
  bones and a second neck bone in between stay), and a bone left with no parent hangs from the top bone (the root,
  else the hips): one root. A bone that plays a role takes the profile's name for it. Any other bone keeps its own
  name, unless the engine's skeleton uses that name (a prop bone called ``pelvis`` would put two bones under one
  name): it gets a free ``_extra`` name and the plan says so.
* **Fillers**: for an engine that finds bones by exact name and parent (Unreal, ``template`` = its skeleton), a
  template bone the rig lacks is inserted between its template parent and the kept bones below it, so every name sits
  under the parent the engine expects: a three-bone spine gets ``spine_03``..``spine_05`` between its chest and its
  neck and clavicles, a one-bone neck gets ``neck_02``, a rig without clavicles or metacarpals gets them, and a rig
  without a root gets ``root`` above the pelvis. A filler deforms nothing (no vertex group), so skinning is unchanged.
  It is inserted only when a kept bone below it already hangs from its template parent or above it; nothing is
  re-parented across the body.
* **Engines that match by role** (Godot, Unity: no ``template``) get the same fillers from the role tree, for the
  bones they require and the rig lacks (Godot's shoulders on a rig without clavicles) and for the root an engine
  wants (Godot's optional ``Root``, above the hips). Nothing they don't require is added.
* **An engine with no root** (Unity: a Humanoid avatar ignores bones above the hips, so travel there is lost)
  leaves out the rig's role-less deforming bones above the hips; the hips carry their motion, and their vertex
  groups map to the hips.

Each filler records where it sits: ``run`` = (k, m, top, below): the k-th of m fillers in a row between the bone
``top`` and the kept bone ``below``; the Blender side spaces a run evenly from ``top``'s head to ``below``'s head, and
puts a filler with no ``top`` at the armature's origin.
"""


def _up(bone, parents, wanted, follows):
    """``bone``'s nearest ancestor in ``wanted``, where an ancestor that a ``wanted`` bone follows counts as that bone
    (never ``bone`` itself or a bone below it)."""
    current = parents.get(bone)
    while current is not None:
        if current in wanted:
            return current
        carrier = follows.get(current)
        if carrier in wanted and carrier != bone and not _below(carrier, bone, parents):
            return carrier
        current = parents.get(current)
    return None


def _below(bone, top, parents):
    current = parents.get(bone)
    while current is not None:
        if current == top:
            return True
        current = parents.get(current)
    return False


def _by_roles(kept, kept_parent, role_of):
    """Role bones re-hung from their nearest present ancestor role's bone where their ancestry misses it, then every
    other parentless bone hung from the top bone, in place."""
    from .skeleton_profiles import ROLE_PARENT
    bone_of = {role: bone for bone, role in role_of.items()}

    def ancestry(bone):
        out, current, seen = [], kept_parent.get(bone), set()
        while current is not None and current not in seen:
            out.append(current)
            seen.add(current)
            current = kept_parent.get(current)
        return out
    moves = {}
    for bone in kept:
        role = role_of.get(bone)
        if not role or role == "Root":
            continue
        up = ROLE_PARENT.get(role)
        while up is not None and up not in bone_of:
            up = ROLE_PARENT.get(up)
        if up is not None and bone_of[up] != bone and bone_of[up] not in ancestry(bone):
            moves[bone] = bone_of[up]
    kept_parent.update(moves)
    top = bone_of.get("Root") or bone_of.get("Hips") or (kept[0] if kept else None)
    for bone in kept:
        if kept_parent.get(bone) is None and bone != top:
            kept_parent[bone] = top
    for bone in kept:   # A cycle can only come from a move: such a bone hangs from the top instead.
        if bone in ancestry(bone):
            kept_parent[bone] = top if bone != top else None


def _free(name, taken):
    out, k = name, 1
    while out in taken:
        out = "%s_extra%s" % (name, "" if k == 1 else k)
        k += 1
    return out


def _name_paths(template, names, used, kept_parent, role_of, engine_names, notes):
    """Role-less kept bones on the path between two present template bones take the template's role-less names on
    that path, in order (a five-bone spine becomes spine_01..spine_05). With more bones than names, the ones nearest
    the lower bone take them; with fewer, the first names are taken and fillers supply the rest."""
    t_parent = {name: p for name, p, _ in template or ()}
    t_role = {name: r for name, _, r in template or ()}
    for lower_name, _, _ in template or ():
        by_new = {new: bone for bone, new in names.items()}
        if lower_name not in by_new:
            continue
        run, up = [], t_parent.get(lower_name)
        while up is not None and t_role.get(up) is None and up not in by_new:
            run.insert(0, up)
            up = t_parent.get(up)
        if not run or up is None or up not in by_new:
            continue
        upper_bone, path, current = by_new[up], [], kept_parent.get(by_new[lower_name])
        while current is not None and current != upper_bone:
            path.insert(0, current)
            current = kept_parent.get(current)
        if current != upper_bone or not path or any(role_of.get(b) or names[b] in engine_names for b in path):
            continue
        pairs = list(zip(path[-len(run):], run)) if len(path) >= len(run) else list(zip(path, run))
        for bone, name in pairs:
            used.discard(names[bone])
            notes.append("%s takes the name %s (on the path from %s to %s)" % (bone, name, up, lower_name))
            names[bone] = name
            used.add(name)


def _role_template(profile, present, root):
    """[(name, parent name, role)] parents first over the roles the rig has, the ones the engine requires, and the
    root when it wants one: each hangs from its nearest such ancestor role, so the fillers below add exactly the
    required bones (and the root) the rig lacks, between bones it has."""
    from .skeleton_profiles import ROLE_PARENT, ROLES
    wanted = (set(present) | set(profile.required) | ({"Root"} if root else set())) & set(profile.names)
    out = []
    for role, _ in ROLES:
        if role not in wanted:
            continue
        up = ROLE_PARENT.get(role)
        while up is not None and up not in wanted:
            up = ROLE_PARENT.get(up)
        out.append((profile.names[role], profile.names[up] if up else None, role))
    return out


def plan(bones, parents, deform, roles, profile, template=None, follows=None):
    """``bones``: every bone name, parents before children; ``parents``: {bone: parent or None} over all bones;
    ``deform``: the set of deforming bones; ``roles``: {role: bone}; ``profile``: a ``skeleton_profiles.Profile``;
    ``template``: [(name, parent name, role or None)] of an exact-name engine's skeleton, parents first, or None;
    ``follows``: {control bone: the deforming bone that follows it}.

    Returns {"bones": [{"name", "parent", "source" (control-rig bone or None), "role", "run" (fillers only)}] parents
    first, "names": {control bone: engine name}, "notes": [str]}."""
    follows = follows or {}
    kept = [b for b in bones if b in deform]
    role_of = {bone: role for role, bone in roles.items() if bone in deform}
    dropped = []   # An engine with no root (Unity): role-less deforming bones above the hips are left out.
    if profile.root == "none" and "Hips" in role_of.values():
        up = _up(roles["Hips"], parents, deform, follows)
        while up is not None:
            if up not in role_of:
                dropped.append(up)
            up = _up(up, parents, deform, follows)
        kept = [b for b in kept if b not in dropped]
    if "Hips" in role_of.values() and "Root" not in role_of.values() and "Root" in profile.names:
        top = _up(roles["Hips"], parents, deform, follows)
        while top is not None and _up(top, parents, deform, follows) is not None:
            top = _up(top, parents, deform, follows)
        if top is not None and top not in role_of:
            role_of[top] = "Root"   # A deforming bone above the hips with no role is the rig's root.
    template = list(template or ())
    engine_names = set(profile.names.values()) | {name for name, _, _ in template}
    names, used, notes = {}, set(), []
    for bone in kept:
        name = profile.names.get(role_of.get(bone))
        if name and name not in used:
            names[bone] = name
            used.add(name)
    for bone in kept:
        if bone not in names:
            names[bone] = _free(bone, engine_names | used)
            used.add(names[bone])
            if names[bone] != bone:
                notes.append("renamed %s to %s: the engine's skeleton uses that name" % (bone, names[bone]))
    for bone in dropped:   # Its motion is baked into the hips; its skin (if any) goes to them too.
        names[bone] = names[roles["Hips"]]
        notes.append("left out %s above the hips: %s ignores bones above them, so the hips carry its motion"
                     % (bone, profile.display))
    kept_set = set(kept)
    kept_parent = {b: _up(b, parents, kept_set, follows) for b in kept}
    _by_roles(kept, kept_parent, role_of)
    _name_paths(template, names, used, kept_parent, role_of, engine_names, notes)
    parent = {names[b]: (names[p] if p else None) for b, p in kept_parent.items()}
    source = {names[b]: b for b in kept}
    role = {names[b]: role_of.get(b) for b in kept}
    order = [names[b] for b in kept]
    fillers = {}

    def ancestors(name):
        out, current = [], parent.get(name)
        while current is not None:
            out.append(current)
            current = parent.get(current)
        return out

    t_parent = {name: p for name, p, _ in template}
    for name in order:   # A role bone below twist or helper bones hangs from its template parent directly.
        want = t_parent.get(name)
        if want and parent.get(name) != want and want in ancestors(name):
            notes.append("%s hangs from %s directly (it was below %s)" % (name, want, parent[name]))
            parent[name] = want
    if not template:   # An engine that matches by role (Godot, Unity): the role tree over what it needs.
        template = _role_template(profile, {r for r in role.values() if r},
                                  profile.root in ("required", "optional") and "Hips" in role.values())
    t_children = {}
    for name, p, _ in template:
        t_children.setdefault(p, []).append(name)

    def frontier(name):
        """The present template bones below ``name`` with no present template bone between."""
        out, stack = [], list(t_children.get(name, ()))
        while stack:
            child = stack.pop(0)
            if child in parent:
                out.append(child)
            else:
                stack.extend(t_children.get(child, ()))
        return out

    for name, t_par, t_role in template:
        if name in parent:
            continue
        if t_par is not None and t_par not in parent:
            continue
        if t_par is None:
            below = [b for b in frontier(name) if b in parent and parent[b] is None]
        else:
            above = [t_par] + ancestors(t_par)
            below = [b for b in frontier(name) if b in parent and b not in fillers and parent[b] in above]
        if not below:
            continue
        fillers[name] = {"role": t_role}
        for b in below:
            parent[b] = name
        parent[name] = t_par
        notes.append("added %s (deforms nothing) so %s hang%s from it as in the engine's skeleton"
                     % (name, ", ".join(below), "s" if len(below) == 1 else ""))
    children = {}
    for name, p in parent.items():
        children.setdefault(p, []).append(name)
    t_order = {name: k for k, (name, _, _) in enumerate(template)}
    for name, info in fillers.items():
        first = name
        while parent[first] in fillers and len(children[parent[first]]) == 1:
            first = parent[first]
        last = name
        while len(children.get(last, ())) == 1 and children[last][0] in fillers:
            last = children[last][0]
        run, cur = [first], first
        while cur != last:
            cur = children[cur][0]
            run.append(cur)
        below = min(children.get(last, [None]), key=lambda c: t_order.get(c, len(t_order)))
        info["run"] = (run.index(name) + 1, len(run), parent[first], below)
    entries, placed = [], set()
    pending = list(fillers) + order
    while pending:
        progressed = False
        for name in list(pending):
            p = parent.get(name)
            if p is None or p in placed:
                entry = {"name": name, "parent": p, "source": source.get(name),
                         "role": fillers[name]["role"] if name in fillers else role.get(name)}
                if name in fillers:
                    entry["run"] = fillers[name]["run"]
                entries.append(entry)
                placed.add(name)
                pending.remove(name)
                progressed = True
        if not progressed:
            raise ValueError("the game rig's hierarchy has a cycle")
    return {"bones": entries, "names": names, "notes": notes}
