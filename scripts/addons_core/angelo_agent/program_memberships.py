# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collection membership at commit: created collections, new objects and staged collection edits.

A new object commits into exactly the collections it holds on the stage: the memberships staging recorded for it in
program order (``action_object_management._membership``, which membership checks read too). A duplicate, batch copy,
conversion or separated part takes its source's collections at that point of the program, and its own later
``collection_link`` edits change them; never the source's collections before the program (otherwise a
duplicate made after its source moved collection was linked where the source had been, so its passing check failed
live and a valid edit was refused; without a check the copy silently landed in the wrong collection).

Commit order, each step journaled for rollback: the collections the program creates, in program order (a new object
may stage in one); every new object, linked once into its staged collections (``link_new_objects``); then each
declared object's edits, replayed in program order from its live memberships. Every edited object is then compared
with its staged memberships; a collection gone from the bound scene meanwhile is a stale dependency.
"""

from .program_jobs import ProgramError
from .program_names import NAMED  # noqa: F401  (re-exported: tests and readers find the key here)


def staged_membership(stage, obj, alias):
    """The collection names a new object holds on the stage after every staged edit (program order); None when
    staging recorded none, which commit treats as a fault rather than guessing the Scene Collection."""
    from .action_object_management import _membership
    if alias is not None:
        return _membership(stage.context, alias)
    # No alias: a separated part (its target's collections when it was separated) or an unaliased create, by the
    # name commit gives it, followed across suspensions (program_names._unaliased, stage_new_objects.follow).
    members = stage.extras.get(NAMED, {}).get(obj.name)
    return list(members) if members is not None else None


def verify_new_memberships(backend, stage, new_objects):
    """After every link and collection edit: each new object is in exactly its staged collections, else the whole
    commit rolls back (an internal fault: staging promised these collections)."""
    from .action_object_management import MASTER_COLLECTION
    from .program_objects import _scene_collections
    aliases = {obj.as_pointer(): "$" + alias.lstrip("$") for alias, obj in stage.context.aliases.items()}
    pointers = {obj.as_pointer() for obj in new_objects}
    actual = {pointer: [] for pointer in pointers}
    for collection in [backend.scene.collection, *_scene_collections(backend.scene)]:
        name = MASTER_COLLECTION if collection == backend.scene.collection else collection.name
        for item in collection.objects:
            if item.as_pointer() in pointers:
                actual[item.as_pointer()].append(name)
    for obj in new_objects:
        wanted = staged_membership(stage, obj, aliases.get(obj.as_pointer()))
        if wanted is None or sorted(actual[obj.as_pointer()]) != sorted(wanted):
            raise ProgramError("COMMIT_FIDELITY: the new object %r is in %s, not its staged collections %s"
                               % (obj.name[:64], sorted(actual[obj.as_pointer()])[:8], (wanted or [])[:8]),
                               code="INTERNAL_ERROR")


def link_new_objects(backend, stage, new_objects, memberships, scope):
    """Link the program's new objects into the live scene, each journaled in ``memberships``.

    Every linked duplicate and linked batch copy shares the data of its link
    root: following ``linked_duplicates`` through duplicates of duplicates and
    copies of a duplicate to the first source that is a declared object (its
    live data, which a data swap already gave it) or a new unlinked object (its
    own staged data). ``program_rules`` refuses any later edit of linked data,
    so a staged copy always equals its root's data; a relation that would not
    hold live fails the commit (otherwise copies of a linked duplicate
    shared a separate mesh, and a chain was silently unlinked). Each object
    joins exactly its staged collections (``staged_membership``), which must
    exist live: the program's own were created first (``_create_collections``).
    """
    linked = stage.extras.get("linked_duplicates", {})
    aliases = {obj.as_pointer(): "$" + alias.lstrip("$") for alias, obj in stage.context.aliases.items()}
    created = {alias: obj for obj in new_objects for alias in [aliases.get(obj.as_pointer())] if alias}

    def root_data(reference, seen=()):
        if reference in stage.originals:
            return stage.originals[reference].data
        if reference in linked and reference not in seen:
            return root_data(linked[reference], seen + (reference,))
        return created[reference].data if reference in created else None

    index = None
    for obj in new_objects:
        backend.mutation_started = True
        alias = aliases.get(obj.as_pointer())
        if alias in linked:
            data = root_data(alias)
            if data is None:
                raise ProgramError("COMMIT_FIDELITY: the linked duplicate %r has no data to share; nothing was changed"
                                   % obj.name[:64], code="UNSUPPORTED")
            if obj.data != data:
                staged_data = obj.data
                obj.data = data  # A linked duplicate shares its root's live data.
                if scope is not None:
                    scope.record_transfer(staged_data, obj.data, "linked_duplicate_data", alias)
        if index is None:  # One walk of the scene's collections for every new object (a batch makes hundreds).
            index = backend._collection_index()
        wanted = staged_membership(stage, obj, alias)
        if wanted is None:
            raise ProgramError("COMMIT_FIDELITY: staging recorded no collections for the new object %r"
                               % obj.name[:64], code="INTERNAL_ERROR")
        for name in wanted:
            if name not in index:
                raise ProgramError("COLLECTION_NOT_FOUND: %r, which the new object %r joins, is no longer in the bound "
                                   "scene" % (name[:64], obj.name[:64]), code="STALE_DEPENDENCY")
            index[name].objects.link(obj)
            memberships.append(("link", index[name], obj))


class ProgramMembershipMixin:
    __slots__ = ()

    def _collection_index(self):
        """Every local collection of the bound scene by its exact name (the master is "Scene Collection"). A library
        collection cannot hold a local object and never stands for a local one of its name."""
        from .action_object_management import MASTER_COLLECTION
        from .program_objects import _scene_collections
        found = {collection.name: collection for collection in _scene_collections(self.scene)
                 if collection.library is None}
        found[MASTER_COLLECTION] = self.scene.collection
        return found

    def _collections_named(self, names):
        """Live collections of the bound scene with these exact names."""
        found = self._collection_index()
        return [found[name] for name in names if name in found]

    def _ensure_collection(self, name, journal):
        """The bound scene's collection ``name``, created and linked under its master when missing (journaled)."""
        from .program_commit_scope import for_backend
        from .program_dependencies import _bpy
        named = self._collections_named([name])
        if named:
            return named[0]
        scope = for_backend(self)
        collection = scope.new_collection(name) if scope is not None else _bpy().data.collections.new(name)
        if collection.name != name:
            if scope is None:
                _bpy().data.collections.remove(collection)
            else:
                scope.delete_created_collections((collection,))
            raise ProgramError("collection_link NAME_TAKEN: the collection name was taken before commit",
                               code='STALE_DEPENDENCY')
        journal.append(("created", collection, None))
        self.scene.collection.children.link(collection)
        return collection

    def _create_collections(self, stage, journal):
        """The collections this program creates, in program order, before any new object joins one.

        Staging refused a name any collection held; one the bound scene gained before commit is never adopted as
        the program's own (its objects would join the artist's collection): a stale dependency."""
        for name in stage.extras.get("created_collections", ()):
            if self._collections_named([name]):
                raise ProgramError("collection_link NAME_TAKEN: a collection named %r was added before commit"
                                   % name[:64], code='STALE_DEPENDENCY')
            self.mutation_started = True
            self._ensure_collection(name, journal)

    def _apply_collection_intents(self, stage, journal):
        """Replay each declared object's staged collection edits live, journaling each link change; then compare
        every edited object, new ones included (linked into their staged collections already), with its staging."""
        from .action_object_management import MASTER_COLLECTION
        from .program_objects import _scene_collections
        aliases = {"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()}
        for reference, edits in stage.extras.get("collections", {}).items():
            declared = reference in stage.originals
            obj = stage.originals[reference] if declared else aliases[reference]
            for edit in edits if declared else ():
                if edit["create"]:
                    target = self._ensure_collection(edit["collection"], journal)
                else:
                    named = self._collections_named([edit["collection"]])
                    if not named:
                        raise ProgramError("collection_link COLLECTION_NOT_FOUND: " + edit["collection"][:64],
                                           code='STALE_DEPENDENCY')
                    target = named[0]
                current = [self.scene.collection] + list(_scene_collections(self.scene))
                if edit["mode"] in {"LINK", "MOVE"} and not any(item == obj for item in target.objects):
                    target.objects.link(obj)
                    journal.append(("link", target, obj))
                for collection in current:
                    drop = (edit["mode"] == "UNLINK" and collection == target) or (edit["mode"] == "MOVE" and collection != target)
                    if drop and any(item == obj for item in collection.objects):
                        collection.objects.unlink(obj)
                        journal.append(("unlink", collection, obj))
            wanted = sorted(stage.extras.get("memberships", {}).get(reference, ()))
            actual = sorted([MASTER_COLLECTION] * any(item == obj for item in self.scene.collection.objects)
                            + [collection.name for collection in _scene_collections(self.scene)
                               if any(item == obj for item in collection.objects)])
            if actual != wanted:
                raise ProgramError("Committed collection membership does not match its staged intent", code='INTERNAL_ERROR')

    def _unlink_deleted(self, original, journal):
        """Unlink a deleted object from the bound scene; return it when nothing else uses it."""
        from .program_dependencies import _bpy
        from .program_objects import _scene_collections
        bpy = _bpy()
        if any(scene.camera == original for scene in bpy.data.scenes):
            raise ProgramError("object_delete OBJECT_IN_USE: it is a scene's active camera", code='UNSUPPORTED')
        from .material_sharing import kept  # Data nothing keeps (what an earlier commit replaced) uses nothing.
        users = kept(bpy.data.user_map(subset={original}).get(original, set()))
        if any(not isinstance(user, (bpy.types.Collection, bpy.types.Scene)) for user in users):
            raise ProgramError("object_delete OBJECT_IN_USE: another object or datablock still depends on it", code='UNSUPPORTED')
        for collection in [self.scene.collection] + list(_scene_collections(self.scene)):
            if any(item == original for item in collection.objects):
                collection.objects.unlink(original)
                journal.append(("unlink", collection, original))
        return [] if original.users_collection else [original]
