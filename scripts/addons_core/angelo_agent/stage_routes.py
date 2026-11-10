# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which way every link of a stage reaches the person's data, decided before the stage is evaluated
(doc/angelo/agent_execution.rst, "Links between staged copies").

An evaluated stage's depsgraph is active: Blender writes evaluated animation and object matrices back to the
original of every ID it built. Whatever a staged ID links to is built into that graph (properties and Python
``bpy.props`` pointers, drivers, nodes, constraints, modifiers, particles, light linking), so before each
evaluation every target ``_angelo_stage.relink`` finds outside the stage takes one route (``ROUTES``):

* ``owned``: a staged copy or stand-in, or what a kernel created;
* ``unit_copy`` / ``private_copy``: the bound object's own copy, or a reference copy (``stage_links.close``);
* ``live``: a type evaluation never writes and that holds no animation (``LIVE_TYPES``: images, fonts, texts,
  libraries), shared as it is;
* ``borrowed``: a material ``stage_borrowing`` reads in place, or a collection or object whose whole link closure is
  still (no Action, NLA track or driver, no rigid body or cache-writing modifier, no simulation or bake node, no
  object this program binds, no scene) and bounded;
* ``refused``: anything else (a scene, a moving collection, an undeclared object, UI data), a plain coded refusal
  before anything is evaluated.

After each evaluation ``tripwire`` checks that every original the depsgraph built is owned or on a route it
allowed; a violation refuses (``INTERNAL_ERROR``).
"""


from .program_jobs import ProgramError
from .program_dependencies import _bpy
from .scene_read_values import label

MAX_MADE_ROUNDS = 8   # IDs a stage's code made since its last check, and what those link to, a few deep.
ROUTES = ("owned", "unit_copy", "private_copy", "stand_in", "live", "borrowed", "refused")
MAX_BORROWED_IDS = 20000  # A borrowed collection's closure, walked before every evaluation.
# Object modifiers whose evaluation writes the person's caches (point caches, simulation and dynamic paint state).
CACHE_MODIFIERS = frozenset({"CLOTH", "SOFT_BODY", "DYNAMIC_PAINT", "FLUID", "PARTICLE_SYSTEM"})
# Modifiers whose evaluation in an active depsgraph writes their original's bind data (Blender's MOD_*.cc that call
# BKE_modifier_get_original under DEG_is_active): only while not yet bound. A Decimate's face count is a display stat
# the person's own graph writes with the same value; Geometry Nodes logs are runtime only.
BINDING_MODIFIERS = {"SURFACE_DEFORM": "is_bound", "MESH_DEFORM": "is_bound", "CORRECTIVE_SMOOTH": "is_bind"}
SIMULATION_NODES = frozenset({"GeometryNodeSimulationInput", "GeometryNodeSimulationOutput", "GeometryNodeBake"})
SCENE_REFUSAL = ("This edit's data links to the scene \"{name}\" (a driver or a custom property names it), which a "
                 "staged edit cannot keep separate from the person's file. Use reviewed Python for this edit.")
COLLECTION_REFUSAL = ("This edit's data links to the collection \"{name}\" (an instance, light linking, a Geometry "
                      "Nodes input or a custom property names it), and {reason}, so a staged edit cannot read it "
                      "without changing the person's file. Use reviewed Python for this edit.")
OBJECT_REFUSAL = ("This edit's data links to the object \"{name}\", which the program does not declare. Add it to "
                  "the program's read_set, or use reviewed Python for this edit.")
OTHER_REFUSAL = ("This edit's data links to {kind} \"{name}\", which a staged edit cannot keep separate from the "
                 "person's file. Use reviewed Python for this edit.")
LINKED_REFUSAL = ("This edit's data links to the {kind} \"{name}\", and {reason}, so a staged edit cannot read it "
                  "without changing the person's file. Use reviewed Python for this edit.")
OPEN = ("This edit's staged data now links to the person's {kind} \"{name}\", which a staged edit cannot keep separate "
        "from the person's file. Use reviewed Python for this edit.")
TRIPWIRE = ("Staging evaluated the person's {kind} \"{name}\" outside the private stage. Nothing was committed; "
            "restore the pre-request checkpoint if that data changed")


def live_types():
    types = _bpy().types
    return (types.Image, types.VectorFont, types.Text, types.Library)


def _never_types():
    types = _bpy().types
    return (types.Scene, types.WindowManager, types.Screen, types.WorkSpace)


def _kind(block):
    return block.bl_rna.name.lower() if hasattr(block, "bl_rna") else type(block).__name__.lower()


def _reach(roots, owned, limit=None):
    """(every ID the live ``roots`` reach by any link, embedded IDs included, or None past ``limit``; the animated
    ones), by ``_angelo_stage.reach`` (one native walk)."""
    from _bpy import _angelo_stage
    return _angelo_stage.reach(list(roots), list(owned.values()), MAX_BORROWED_IDS if limit is None else limit)


def _moving(block):
    """Why evaluating ``block`` in place could write the person's data (beyond animation, ``reach``), or None."""
    bpy = _bpy()
    if isinstance(block, bpy.types.Object):
        if block.rigid_body is not None or block.rigid_body_constraint is not None:
            return "\"%s\" in it is simulated" % label(block.name)
        if any(_writes_cache(modifier) for modifier in block.modifiers if modifier.type in CACHE_MODIFIERS):
            return "\"%s\" in it is simulated" % label(block.name)
        for modifier in block.modifiers:
            bound = BINDING_MODIFIERS.get(modifier.type)
            if bound is not None and not getattr(modifier, bound, True) and (
                    modifier.type != "CORRECTIVE_SMOOTH" or getattr(modifier, "rest_source", "") == "BIND"):
                return "\"%s\" in it has a modifier that binds when evaluated" % label(block.name)
    elif isinstance(block, bpy.types.NodeTree) and any(node.bl_idname in SIMULATION_NODES for node in block.nodes):
        return "\"%s\" in it simulates or bakes" % label(block.name)
    return None


def _writes_cache(modifier):
    """Whether evaluating a cache modifier writes its point cache: not when every cache it has is baked (read
    only), nor for static hair or a dynamic paint brush or a fluid flow or effector (they keep no cache); a fluid
    domain always may (its caches live beside the file)."""
    kind = modifier.type
    if kind == "PARTICLE_SYSTEM":
        system = modifier.particle_system
        if system.settings.type == "HAIR" and not system.use_hair_dynamics:
            return False
        caches = [system.point_cache]
    elif kind == "DYNAMIC_PAINT":
        canvas = modifier.canvas_settings
        caches = [surface.point_cache for surface in canvas.canvas_surfaces] if canvas is not None else []
    elif kind == "FLUID":
        return modifier.fluid_type == "DOMAIN"
    else:
        caches = [getattr(modifier, "point_cache", None)]
    return any(cache is None or not cache.is_baked for cache in caches)


class Router:
    """Routes for one stage at one moment (one evaluation): computed once, read by the check and the tripwire.

    ``stage``: a Stage or a kernel's StagedActionContext (``owned_ids``, ``created_ids``, ``extras``, ``objects``;
    ``originals`` when the backend set them on it).
    """

    def __init__(self, stage):
        from .stage_borrowing import borrows_any, resident_pointers
        from . import stage_links
        self.stage = stage
        self.blocks = stage_links._blocks(stage)
        self.owned = {block.as_pointer(): block for block in self.blocks}
        self.borrowed = set(resident_pointers(stage)) if borrows_any(stage) else set()
        self.bound = {obj.as_pointer() for obj in (getattr(stage, "originals", None) or {}).values()}
        self.allowed = set(self.owned)   # Originals the depsgraph may build (the tripwire).
        self.made, self.uid_mark = set(), None   # (pointer, session UID) the stage's code made (``_routes``).
        self._collections = {}

    def adopt(self, block):
        """``block`` is the stage's own (made by its code since the last check)."""
        if block.as_pointer() not in self.owned:
            self.blocks.append(block)
            self.owned[block.as_pointer()] = block
            self.allowed.add(block.as_pointer())

    def route(self, target, walk=True):
        """(route, refusal text or None) of a live ``target`` a staged ID links to (``walk``: a collection's
        closure may be walked natively)."""
        bpy = _bpy()
        pointer = target.as_pointer()
        if pointer in self.owned:
            return "owned", None
        if pointer in self.borrowed:
            return "borrowed", None
        if target.is_embedded_data:
            # Another ID's own data (a scene's master collection, a material's tree) named apart from its owner.
            return "refused", OTHER_REFUSAL.format(kind=_kind(target), name=label(target.name))
        if target.library is not None and not isinstance(target, _never_types()):
            # Linked data links only to linked data, evaluates as in the person's own graph (the same values at the
            # same frame; nothing of it is saved in the person's file), and is written as a link; but a simulation
            # in it writes its caches (beside the library too), whatever graph evaluates it.
            reason = self._simulated(target) if walk else None
            if reason is None:
                return "live", None
            return "refused", LINKED_REFUSAL.format(kind=_kind(target), name=label(target.name), reason=reason)
        if isinstance(target, live_types()):
            # Never written by evaluation itself, but whatever it links to (a Python pointer of either store) is
            # built with it: live only when that whole closure is still, as for a borrowed collection.
            reason = self._collection(target) if walk else None
            if reason is None:
                return "live", None
            return "refused", LINKED_REFUSAL.format(kind=_kind(target), name=label(target.name), reason=reason)
        if isinstance(target, bpy.types.Object):
            # A still object the program does not bind (a bone's custom shape widget, an add-on's pointer) is read in
            # place like a still collection; anything else must be declared.
            if walk and self._collection(target) is None:
                return "borrowed", None
            return "refused", OBJECT_REFUSAL.format(name=label(target.name))
        if isinstance(target, bpy.types.Scene):
            return "refused", SCENE_REFUSAL.format(name=label(target.name))
        if isinstance(target, _never_types()):
            return "refused", OTHER_REFUSAL.format(kind=_kind(target), name=label(target.name))
        if isinstance(target, bpy.types.Collection):
            reason = self._collection(target) if walk else "its contents cannot be checked"
            if reason is None:
                return "borrowed", None
            return "refused", COLLECTION_REFUSAL.format(name=label(target.name), reason=reason)
        return "private_copy", None

    def _collection(self, collection):
        """None when a collection's or object's whole closure can be read in place, else why not."""
        key = collection.as_pointer()
        if key not in self._collections:
            self._collections[key] = self._judge(collection)
        return self._collections[key]

    def _judge(self, collection):
        reached, animated = _reach([collection], self.owned)
        if reached is None:
            return "it reaches more than %d datablocks" % MAX_BORROWED_IDS
        if animated:
            return "\"%s\" in it is animated or driven" % label(animated[0].name)
        for block in reached:
            if block.as_pointer() in self.bound:
                return "it reaches \"%s\", which this program edits" % label(block.name)
            if isinstance(block, _never_types()):
                return "it reaches a scene"
            reason = _moving(block)
            if reason is not None:
                return reason
        self.allowed.update(block.as_pointer() for block in reached)
        return None

    def _simulated(self, target):
        key = ("simulated", target.as_pointer())
        if key not in self._collections:
            reached, _animated = _reach([target], self.owned)
            if reached is None:
                self._collections[key] = "it reaches more than %d datablocks" % MAX_BORROWED_IDS
            else:
                self._collections[key] = next((reason for reason in map(_moving, reached) if reason), None)
        return self._collections[key]

    def reaches_borrowed(self, target):
        """Whether a copy of ``target`` would link (directly or through others) to a borrowed material."""
        reached, _animated = _reach([target], self.owned)
        return reached is None or any(block.as_pointer() in self.borrowed for block in reached)

    def allow_live(self, targets):
        """Live and borrowed targets' own closures may be built too (an image, a borrowed material's tree)."""
        reached, _animated = _reach(targets, self.owned)   # Unanimated by route (or linked: evaluated as live).
        if reached is None:
            raise ProgramError(OTHER_REFUSAL.format(kind="data", name=label(targets[0].name)), code='UNSUPPORTED')
        self.allowed.update(block.as_pointer() for block in reached)


def check(stage):
    """Before an evaluation: ``audit``, then every outside target is live or borrowed (no copy is made here) and
    every path is safe, else refused. Returns the Router, whose ``allowed`` the tripwire reads after. It releases
    nothing: only ``guarded`` evaluates."""
    from . import stage_guard
    marker = stage_guard._marker(stage)
    if marker is None:
        raise ProgramError("A staged evaluation has no private stage scene", code='INTERNAL_ERROR')
    stage_guard.audit(stage)
    router = _routes(stage, stage_guard._LAST.get(marker))
    stage_guard._LAST[marker] = router   # Only a complete check's allowance is what later evaluations are held to.
    stage_guard._watch()
    return router


def _routes(stage, last=None):
    """The routes of one check. IDs the stage's code made since ``last`` (a greater session UID, on the main
    thread between two checks of one tick; ``forget`` at every yield and after commit's last check) whose every user
    is a stage ID are its own before its kernels track them (an operator's first shape keys); their links are routed
    like the rest. Carried by (pointer, session UID) from check to check until tracked."""
    from . import stage_links, stage_paths
    router = Router(stage)
    native = stage_links.available()
    carried = set(getattr(last, "made", ()) or ())
    since = getattr(last, "uid_mark", None)
    found = None
    if native:
        for _ in range(MAX_MADE_ROUNDS if since is not None or carried else 0):
            found = stage_links.outside(stage_links._holders(router.blocks))
            made = [target for target in found if target.as_pointer() not in router.owned
                    and not target.is_embedded_data and _made_here(target, since, carried)]
            made = _used_only_by(router, made)
            if not made:
                break
            for block in made:
                router.made.add((block.as_pointer(), int(block.session_uid)))
                router.adopt(block)
            found = None
    router.uid_mark = stage_links.session_uid_last()
    # Paths are not links: whatever a driver's or an F-Curve's path crosses is checked too (stage_paths, H5).
    shared = stage_paths.check(stage, router, router.blocks, walk=native)
    if not native:
        if shared:
            router.allowed.update(block.as_pointer() for block in shared)   # Images, fonts, texts: no links out.
        return router
    if found is None:
        found = stage_links.outside(stage_links._holders(router.blocks))
    for target in found:
        route, refusal = router.route(target)
        if route == "refused":
            raise ProgramError(refusal, code='UNSUPPORTED')
        if route == "private_copy":
            # A link a kernel made after staging, to data a later kernel could write: refused, coded, before evaluation.
            raise ProgramError(OPEN.format(kind=_kind(target), name=label(target.name)), code='UNSUPPORTED')
        if target.as_pointer() not in router.allowed:
            shared.append(target)
    if shared:
        router.allow_live(shared)   # One walk for every shared target.
    return router


def _made_here(target, since, carried):
    uid = int(target.session_uid)
    return (target.as_pointer(), uid) in carried or (since is not None and uid > since)


def _used_only_by(router, candidates):
    """The candidates every user of which is a stage ID (or a fake user; no scene or collection of the person's):
    counted from the stage's own user links (``_angelo_stage.links``, LINK_USER)."""
    if not candidates:
        return []
    from . import stage_links
    module = stage_links._module()
    wanted = {block.as_pointer(): block for block in candidates}
    counts = dict.fromkeys(wanted, 0)
    for _owner, _holder, target, flags in module.links(stage_links._holders(router.blocks)):
        if target is not None and target.as_pointer() in counts and flags & module.LINK_USER:
            counts[target.as_pointer()] += 1
    # A fake user is one more allowed user: Blender gives some new IDs one (palettes, brushes, masks).
    return [block for pointer, block in wanted.items()
            if block.users <= counts[pointer] + (1 if getattr(block, "use_fake_user", False) else 0)]


def tripwire(stage, router):
    """After an evaluation: every original the stage's depsgraph built is owned or allowed, else refused.

    Embedded IDs too (a driver path can build one alone): allowed when a borrowed or live closure listed it
    (``reach`` lists embedded IDs), or when it is an owned ID's own (``_angelo_stage.links``, LINK_EMBEDDED)."""
    depsgraph = stage.view_layer.depsgraph
    if depsgraph is not None:   # None: never evaluated, nothing built.
        _tripwire(depsgraph, router)


def _tripwire(depsgraph, router):
    embedded = None
    for item in depsgraph.ids:
        original = item.original
        if original is None or original.as_pointer() in router.allowed:
            continue
        if original.is_embedded_data:
            if embedded is None:
                embedded = _owned_embedded(router)
            if original.as_pointer() in embedded:
                continue
        raise ProgramError(TRIPWIRE.format(kind=_kind(original), name=label(original.name)), code='INTERNAL_ERROR')


def _owned_embedded(router):
    """Pointers of the owned IDs' own embedded IDs: exactly what foreach_ID_link reports as embedded."""
    from . import stage_links
    module = stage_links._module()
    if module is None:   # An older binary: the embedded IDs Blender has (node trees, a scene's master collection).
        found = (getattr(block, name, None) for block in router.blocks for name in ("node_tree", "collection"))
        return {item.as_pointer() for item in found if getattr(item, "is_embedded_data", False)}
    holders = stage_links._holders(router.blocks)
    return {target.as_pointer() for _owner, _holder, target, flags in module.links(holders)
            if flags & module.LINK_EMBEDDED}


def route(stage, target):
    """The route of a live ``target`` for ``stage`` (``ROUTES``): the one fact callers read about how staging reads
    it. ``unit_copy``: a bound object or its data or keys (they have staged copies); ``private_copy`` /
    ``stand_in``: a reference copy or reference stand-in already stands for it; ``owned``: a staged ID itself."""
    from . import stage_links
    router = Router(stage)
    if target.as_pointer() in router.owned:
        return "owned"
    for handle, original in stage.originals.items():
        data = getattr(original, "data", None)
        if handle in stage.objects and target in (original, data, getattr(data, "shape_keys", None)):
            return "unit_copy"
    uid = int(target.session_uid)
    for marker, name in ((stage_links.REFERENCE, "private_copy"), (stage_links.REFERENCE_STAND_IN, "stand_in")):
        if any(int(block[marker]) == uid for block in stage_links.references(stage, marker)):
            return name
    return router.route(target)[0]


# The hold and every evaluation's lifecycle (stage_guard); kept importable from here for callers and tests.
from .stage_guard import (HOLD, STOPPED, audit, disarm, forget, guarded, hold, _before, _observed,  # noqa: E402,F401
                          _FRESH, _LAST, _VIOLATIONS)
