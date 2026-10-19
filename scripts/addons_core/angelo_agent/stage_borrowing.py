# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live materials a staged program cannot write: read in place, never copied into its stage.

Staging copies each bound object and its data, Actions and modifier node
groups. It also copied every material (with its node tree and nested node
groups) so that the stage graph was private; on a character with 30 materials
that copy, and every later walk over it, cost more than the edit itself. A
program whose every operation is ``material_blind`` writes no material, node
tree or material slot: its kernels do not touch them, and commit transfers
none of them (no data swap, no slot or node-tree transfer). Such a program
*borrows* each eligible material instead: the staged copy keeps the live
material in its slot, so checks, evaluation and previews read exactly what
commit leaves live.

A material is eligible when it, its embedded tree and every nested node group
are local, have no animation data (so the stage's active depsgraph has no
Action or driver of theirs to evaluate or write back), and link to nothing
but each other and images, which material clones already shared live.
Nothing the stage copies may be animated either: ``view_layer.update`` makes
the stage's depsgraph active, and an active depsgraph writes evaluated
animation back to the originals. An F-Curve of the staged object, its data or
its shape keys whose path runs through a slot into the material
(``material_slots[0].material.roughness``) would resolve through the staged
slot to the live material and overwrite an artist's unkeyed value before
anything is approved. A program borrows nothing when any ID in its bound
objects' mutable closures (every object data type staging copies, and the
data and its shape keys named again) has an Action, NLA tracks or drivers,
and a PREVIEW (which is retained, and so privatized anyway) never borrows.
Anything else, and every other program, is copied as before: when in doubt,
copy. Kernels cannot write a borrowed block either: it is not in the stage's
owned IDs (``StagedActionContext.require_private``).

The bound objects' exact fingerprints already cover these materials (each is
in its object's mutable closure), so a live edit to one fails verification
as before.

A yield must leave a closed private graph (``_angelo_stage.detach``) and a
bundle must never carry a live material, so ``release`` clears every borrowed
slot before suspension and ``restore`` puts the same live material back after
hydration, by pointer and session UID, or refuses with STALE_DEPENDENCY. While
the stage is resident, ``resident_pointers`` proves each borrowed slot still
holds its own material (stage validation) and ``assert_unanimated`` that no
borrowed block, and no ID the stage owns, gained animation, before every
stage evaluation. A new stage that owns an animated copy anyway unborrows
before its first evaluation (``owns_animated``), when nothing can have been
written yet. Hydration also re-runs each material's eligibility walk, so
a family edited while the job waited is refused, not evaluated live.

The rows in ``stage.extras[BORROWED]`` are identities only; the live wrappers
of a resident stage are in ``Stage.borrowing`` (never persisted).
"""

from .program_jobs import ProgramError

BORROWED = "borrowed_materials"  # stage.extras: ((handle, link, slot, uid, pointer, family), ...).
MAX_FAMILY = 256  # IDs in one material's family; a larger family is copied (and bounded) as before.
MAX_DEPTH = 14  # Nested group depth; a deeper one is copied, and refused, as before (_clone_tree).


def material_blind(operation):
    """Whether an operation's kernel and commit transfer never write a material, node tree or material slot.

    ``transform`` sets object channels. Typed modifier kinds (the settings
    ``MODIFIER_SETTINGS`` lists: numbers, enums and object pointers, never a
    material or a node group) add or configure a modifier; ``modifier_remove``
    and ``modifier_move`` change the stack by name. Commit transfers modifiers
    by name and settings. Generic and Geometry Nodes modifiers are excluded.
    """
    from .action_schemas import MODIFIER_SETTINGS
    kind, params = operation["kind"], operation["parameters"]
    if kind == "transform":
        return True
    if kind in {"modifier_add", "modifier_configure"}:
        return params.get("type") in MODIFIER_SETTINGS and params.get("type") != "NODES"
    return kind in {"modifier_remove", "modifier_move"}


def program_borrows(binding):
    """An APPLY program whose every operation is material-blind and that binds no material identity."""
    from .program_material_obligations import needs_material_binding
    program = binding["program"]
    return (program.get("mode") == "APPLY" and all(material_blind(operation) for operation in program["operations"])
            and not needs_material_binding(program))


def _animated(block):
    """Whether an ID carries animation an active depsgraph would evaluate: an Action, NLA tracks or drivers."""
    animation = getattr(block, "animation_data", None)
    return animation is not None and (animation.action is not None or len(animation.nla_tracks) > 0
                                      or len(animation.drivers) > 0)


class Borrowing:
    """The live wrappers of one resident stage's borrowed materials and their families."""

    def __init__(self):
        self.plan = {}  # Material pointer -> (family rows, family blocks, clone bound); create_stage only.
        self.materials = {}  # Material pointer -> live material.
        self.blocks = {}  # Pointer -> every borrowed family block (material, tree, groups).


def _family(material, links):
    """(rows, blocks, visits) of the material, its tree and nested groups; None when it is not eligible.

    A row is (id_type, session UID, pointer). ``visits`` counts every path to
    every family block, at least the IDs cloning the material makes
    (``ProgramObjectsMixin._clone_material`` clones a group once per node).
    """
    from .program_dependencies import _bpy
    types = _bpy().types
    rows, blocks, stack, visits = [], [], [(material, 0, ())], 0
    while stack:
        block, depth, path = stack.pop()
        pointer = block.as_pointer()
        visits += 1  # A group reached by many paths is visited once per path: bound the work, then copy.
        if pointer in path or depth > MAX_DEPTH or visits > 4 * MAX_FAMILY:
            return None  # A recursive, too deep or too wide group graph: cloning bounds or refuses it, as before.
        if (block.library is not None or getattr(block, "override_library", None) is not None
                or getattr(block, "animation_data", None) is not None):
            return None
        row = (block.id_type, int(block.session_uid), pointer)
        if row not in rows:
            rows.append(row)
            blocks.append(block)
            if len(rows) > MAX_FAMILY:
                return None
        for _, _, dependency in links(block):
            if isinstance(dependency, types.Image):
                continue
            if not isinstance(dependency, types.NodeTree):
                return None  # An object, text, material or other datablock: copy, remap or refuse as before.
            stack.append((dependency, depth + 1, path + (pointer,)))
    return tuple(rows), blocks, visits


def plan(binding):
    """Which of the bound objects' materials a stage of this binding borrows: a Borrowing, or None.

    Pure reads, before the stage exists. Inside the staging job's exact fence
    the links come from the bind that just read the same closures.
    """
    if not program_borrows(binding):
        return None
    from . import exact_fence
    from .program_dependencies import _embedded_links
    with exact_fence.fence():
        return _plan(binding["originals"], _embedded_links)


def _copied(original):
    """The object's closure plus what copy_object copies by name: its data and the data's shape keys.

    The closure walk visits every object data type (``object_data_types``);
    naming the two again keeps one missed type from making a borrowing stage
    own an animated copy.
    """
    from .program_dependencies import _mutable_dependencies
    data = original.data
    return [*_mutable_dependencies(original), data, getattr(data, "shape_keys", None) if data is not None else None]


def _plan(originals, links):
    if any(_animated(block) for original in originals.values() for block in _copied(original)):
        return None  # Its evaluation could write a borrowed material through a slot or a pointer: copy everything.
    borrowing, seen = Borrowing(), set()
    for original in originals.values():
        data = original.data
        materials = list(data.materials) if data is not None and hasattr(data, "materials") else []
        materials += [slot.material for slot in original.material_slots if slot.link == "OBJECT"]
        for material in materials:
            if material is None or material.as_pointer() in seen:
                continue
            seen.add(material.as_pointer())
            family = _family(material, links)
            if family is not None:
                borrowing.plan[material.as_pointer()] = family
    return borrowing


def borrows(stage, material):
    """Whether copy_object keeps this live material in its staged slot."""
    return (material is not None and stage.borrowing is not None
            and material.as_pointer() in stage.borrowing.plan)


def record(stage, handle, link, slot, material):
    """Remember one borrowed slot (``link`` DATA or OBJECT) of a staged object."""
    rows, blocks, _visits = stage.borrowing.plan[material.as_pointer()]
    stage.borrowing.materials[material.as_pointer()] = material
    stage.borrowing.blocks.update((block.as_pointer(), block) for block in blocks)
    entries = list(stage.extras.get(BORROWED, ()))
    entries.append((handle, link, slot, int(material.session_uid), material.as_pointer(), rows))
    stage.extras[BORROWED] = tuple(entries)  # Immutable: suspended descriptors share this value.


def _slot_material(obj, link, slot):
    if link == "DATA":
        return obj.data.materials[slot]
    held = obj.material_slots[slot]
    if held.link != "OBJECT":
        raise ProgramError("A borrowed material slot changed its link while staged", code="INTERNAL_ERROR")
    return held.material


def _assign(obj, link, slot, material):
    if link == "DATA":
        obj.data.materials[slot] = material
    else:
        obj.material_slots[slot].material = material


def _same(block, uid, pointer):
    try:
        return block is not None and block.as_pointer() == pointer and int(block.session_uid) == uid
    except ReferenceError:  # Removed since it was borrowed.
        return False


def resident_pointers(stage):
    """Pointers of the borrowed live materials, after proving each staged slot still holds its own."""
    pointers = set()
    for handle, link, slot, uid, pointer, _rows in getattr(stage, "extras", {}).get(BORROWED, ()):
        if not _same(_slot_material(stage.objects[handle], link, slot), uid, pointer):
            raise ProgramError("A borrowed live material changed in its staged slot", code="INTERNAL_ERROR")
        pointers.add(pointer)
    return pointers


def release(stage):
    """Clear every borrowed slot before a yield: a suspended graph references no live material."""
    resident_pointers(stage)
    for handle, link, slot, *_rest in getattr(stage, "extras", {}).get(BORROWED, ()):
        _assign(stage.objects[handle], link, slot, None)


def clone_bound(stage):
    """At least the IDs privatizing the borrowed slots would add to the stage (see _family).

    ``privatize`` makes one clone per live material of each object, as copy_object does.
    """
    pairs = {(row[0], row[4]) for row in getattr(stage, "extras", {}).get(BORROWED, ())}
    return sum(stage.borrowing.plan[pointer][2] for _handle, pointer in pairs)


def borrows_any(stage):
    """Whether a resident or suspended stage still reads a live material in place."""
    return bool(getattr(stage, "extras", {}).get(BORROWED))


def owns_animated(stage):
    """Whether a borrowing stage owns an ID with an Action, NLA tracks or drivers, which ``_plan`` excludes.

    ``finish_stage`` asks before the stage's first evaluation and then
    unborrows: nothing has evaluated the stage, so nothing can have been
    written through a borrowed slot, and the clones are the ones copy_object
    makes for a program that borrows nothing.
    """
    return borrows_any(stage) and any(_animated(block) for block in stage.owned_ids)


def privatize(stage, clone):
    """Put private clones in the borrowed slots: the stage then holds the copies it would hold without borrowing.

    For a stage that is retained (a job inspection snapshot), which is
    hydrated, inspected and accepted later, so an inspection edit must never
    reach a live material; and for a new stage that owns an animated copy
    (``owns_animated``). One ``clone(material)`` per live material of each
    object, shared by every slot of that object holding it (DATA and OBJECT
    alike), exactly as ``ProgramObjectsMixin.copy_object`` makes them: two
    copies would split the material's faces between two names.
    """
    entries = getattr(stage, "extras", {}).get(BORROWED, ())
    if not entries:
        return
    resident_pointers(stage)
    clones = {}  # (object handle, live material pointer) -> that object's one private copy.
    for handle, link, slot, _uid, pointer, _rows in entries:
        if (handle, pointer) not in clones:
            clones[handle, pointer] = clone(stage.borrowing.materials[pointer])
        _assign(stage.objects[handle], link, slot, clones[handle, pointer])
    del stage.extras[BORROWED]
    stage.borrowing = None
    if stage.context is not None:
        stage.context.borrowing = None


def _stale(handle=None):
    details = {"handles": [handle]} if handle is not None else None
    return ProgramError("A live material this program reads was removed, replaced or animated since staging; "
                        "observe it again", code="STALE_DEPENDENCY", details=details)


def _rebuild(stage):
    """Live wrappers for a stage hydrated from a bundle: one pass over materials and node groups."""
    from .program_dependencies import _bpy
    data = _bpy().data
    borrowing = Borrowing()
    materials = {(int(item.session_uid), item.as_pointer()): item for item in data.materials}
    groups = {(int(item.session_uid), item.as_pointer()): item for item in data.node_groups}
    for handle, _link, _slot, uid, pointer, rows in stage.extras.get(BORROWED, ()):
        material = materials.get((uid, pointer))
        if material is None:
            raise _stale(handle)
        borrowing.materials[pointer] = material
        for id_type, block_uid, block_pointer in rows:
            block = (material if (block_uid, block_pointer) == (uid, pointer) else
                     material.node_tree if _same(material.node_tree, block_uid, block_pointer) else
                     groups.get((block_uid, block_pointer)) if id_type == "NODETREE" else None)
            if block is None:
                raise _stale(handle)
            borrowing.blocks[block_pointer] = block
    return borrowing


def restore(stage):
    """Put each borrowed live material back after hydration, or refuse when it is no longer the same ID."""
    entries = getattr(stage, "extras", {}).get(BORROWED, ())
    if not entries:
        return
    if stage.borrowing is None:
        stage.borrowing = _rebuild(stage)
    _recheck(stage, entries)
    for handle, link, slot, uid, pointer, _rows in entries:
        material = stage.borrowing.materials.get(pointer)
        obj = stage.objects[handle]
        if _slot_material(obj, link, slot) is not None:
            raise ProgramError("A suspended stage kept a live material reference", code="INTERNAL_ERROR")
        _assign(obj, link, slot, material)
    if stage.context is not None:
        stage.context.borrowing = stage.borrowing


def _recheck(stage, entries):
    """Each borrowed material is still the same ID and still eligible with the same family, or STALE.

    The job may have waited between polls: an edit there can add an object
    link, a group or animation to a family, and the next evaluation would run
    on the live material.
    """
    from . import exact_fence
    from .program_dependencies import _embedded_links
    checked = set()
    with exact_fence.fence():
        for handle, _link, _slot, uid, pointer, rows in entries:
            material = stage.borrowing.materials.get(pointer)
            if not _same(material, uid, pointer):
                raise _stale(handle)
            if pointer in checked:
                continue
            checked.add(pointer)
            family = _family(material, _embedded_links)
            if family is None or family[0] != rows:
                raise _stale(handle)


def assert_unanimated(context):
    """Before a stage evaluation: every borrowed block still exists and has no animation data (or drivers).

    ``view_layer.update`` makes the stage's depsgraph active, which would
    evaluate and write back an animated live material. Eligibility excluded
    animation, so this fails only when a live edit added it while staged.
    No ID the stage owns may be animated either (INTERNAL_ERROR: the stage
    may already have evaluated it); before the first evaluation
    ``owns_animated`` has the stage unborrow instead.
    """
    borrowing = getattr(context, "borrowing", None)
    if borrowing is None:
        if getattr(context, "extras", {}).get(BORROWED):
            raise ProgramError("A staged program's borrowed materials were not restored", code="INTERNAL_ERROR")
        return
    for block in borrowing.blocks.values():
        try:
            animated = block.animation_data is not None
        except ReferenceError:
            animated = True
        if animated:
            raise _stale()
    if any(_animated(block) for block in getattr(context, "owned_ids", ())):
        # finish_stage unborrowed any stage that owned an animated copy before its first evaluation (owns_animated).
        # One animated since then may already have been evaluated (an operator a kernel runs evaluates the
        # stage), and so may have written a borrowed material through its slot.
        raise ProgramError("A staged copy of a borrowing program gained animation", code="INTERNAL_ERROR")
