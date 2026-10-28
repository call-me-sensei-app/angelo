# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One merge's material swaps (``worker_materials.Swaps``): made and refused before any is applied, applied per
placement, and checked once every part is placed.

A placement that swaps: each copy the viewport evaluates must show the swap's material exactly where it showed the
part's as built, its instances included (``worker_material_check.judge``); what the check cannot read is refused by
name, never passed: instancing deeper than Blender's limit, an instanced object not evaluated, and a copy Blender
renders though the viewport does not evaluate it (P2 M2). A copy shown nowhere in the view layer is
listed as not checked. Before anything is swapped, a swap is refused by name unless every object of its part is of
a known-safe kind (``worker_material_reach.unsafe``: an allow-list, after six break-it rounds found one more way each
time for a swap to reach another placement or for renders to differ from the viewport check). Every refusal removes
what the merge added.
"""

SHOWN_UNCHECKED = 12


class Swaps:
    """``before``: every datablock before the merge appended (``ids``), so a refusal removes what the merge added;
    ``unrecord`` then puts the scene's record of merged parts back."""

    def __init__(self, appended, view_layer, before, unrecord=None):
        from .refusal_codes import RefusalError
        from .worker_materials import mapping, refusal
        self.view_layer, self.before, self.memo, self.state = view_layer, before, {}, {}
        self.unrecord, self.refs, self.known = unrecord, {}, {}
        self.swaps, self.made, self.unchecked, self.labels, self.unseen = {}, {}, {}, {}, {}
        self.owner = {}   # Copy pointer -> (part, placement).
        variants = {}
        for number, item in enumerate(appended):
            self.made[number] = []
            given = {new: old for old, new in item["renamed"].items()}   # The worker's names, as the model knows them.
            for index, (placement, instance) in enumerate(zip(item["part"]["placements"], item["instances"])):
                self.labels.update({copy.as_pointer(): given.get(obj.name, obj.name) for obj, copy in instance.items()})
                self.owner.update({copy.as_pointer(): (number, index) for copy in instance.values()})
                if placement["materials"]:
                    swap = mapping(placement["materials"], item["materials"], variants, self.made[number])
                    self.labels.update({material.as_pointer(): name for name, material in item["materials"].items()})
                    self.labels.update({swap[item["materials"][source]].as_pointer(): value if isinstance(value, str)
                                        else value["name"] for source, value in placement["materials"].items()})
                    self.swaps[(number, index)] = (item, swap, instance)
        self._allowed(appended)
        for (number, index), (item, swap, instance) in sorted(self.swaps.items(), key=lambda pair: pair[0]):
            why = refusal(instance.values(), set(instance.values()), swap, self.memo, self.label, self.swap_of)
            if why is not None:
                self.refuse(RefusalError("Worker %d's part: placement %d cannot swap %s for that placement alone: %s; "
                                         "nothing was merged" % (item["part"]["worker"], index + 1,
                                                                 self.listed(swap), why), code="UNSUPPORTED"))
        self.names = dict(self.labels)   # Pointer -> name for the evaluated check's materials, seeded with these.

    def _allowed(self, appended):
        """Refuse, before anything is swapped, each swap whose part holds an object not of a known-safe kind
        (``worker_material_reach.unsafe``), that swaps to or from a material not of one (``destination``), or while
        anything in the file can read what it changes (``reader``): only then does the swap stay its placement's
        alone in every mode. Every placement holds a copy of every object, so the swapping placement's copies are
        read."""
        from .refusal_codes import RefusalError
        from .worker_material_check import ids
        from .worker_material_reach import destination, incoming, reader, render_unsafe, scoped, unsafe
        memo = {}

        def refuse(item, index, swap, who, why):
            self.refuse(RefusalError(
                "Worker %d's part: placement %d's swap of %s can't be checked: %s has %s, and a swap is accepted only "
                "where everything in the part and every material it swaps to is of a kind known to keep it that "
                "placement's alone, and nothing else in the file can read what it changes; nothing was merged" % (
                    item["part"]["worker"], index + 1, self.listed(swap), who, why), code="UNSUPPORTED"))
        for (number, index), (item, swap, instance) in sorted(self.swaps.items(), key=lambda pair: pair[0]):
            placement = set(instance.values())
            for copy in instance.values():
                why = unsafe(copy, placement, memo)
                if why is not None:
                    refuse(item, index, swap, self.label(copy), why)
            for material in sorted({*swap, *swap.values()}, key=lambda found: found.name):   # The swap as made.
                why = destination(material)
                if why is not None:
                    refuse(item, index, swap, "the swap", why)
        if self.swaps:   # Once per merge: nothing in the file may read the incoming part (break-it P2R7, P2R8).
            added = ids() - self.before
            blocks = {block for _item, swap, instance in self.swaps.values() for copy in instance.values()
                      for block in (copy, copy.data, *(slot.material for slot in copy.material_slots)) if block}
            scene = self.view_layer.id_data   # The scene that shows the part (others using it refuse in ``incoming``).
            reads = render_unsafe(scene)
            found = reader(added) or scoped() or ((scene, "renders that use " + reads) if reads else None)
            used = incoming(blocks, added) if found is None else None
            if used is not None:
                found = used[0], "a use of %s %r, which is or holds what this merge adds" % (
                    type(used[1]).__name__.lower(), used[1].name)
            if found is not None:
                key = min(self.swaps)
                item, swap, _instance = self.swaps[key]
                refuse(item, key[1], swap, "%s %r" % (type(found[0]).__name__, found[0].name), found[1])

    def label(self, item):
        return self.labels.get(item.as_pointer(), item.name)

    def listed(self, swap):
        return ", ".join(sorted(map(self.label, swap)))

    def swap_of(self, obj):
        found = self.swaps.get(self.owner.get(obj.as_pointer()))
        return found[1] if found is not None else {}

    def refuse(self, error):
        """Raise ``error`` once every datablock the merge added is removed: nothing of it stays."""
        from .worker_material_check import remove_added
        remove_added(self.before)
        if self.unrecord is not None:
            self.unrecord()
        error.partial_changes_possible = False
        raise error

    def copies(self):
        """Every object the check reads: each swapping placement's copies."""
        return [copy for _item, _swap, instance in self.swaps.values() for copy in instance.values()]

    def _view(self, depsgraph):
        from .worker_material_check import View
        from .worker_placement import evaluated
        view = View(depsgraph if depsgraph is not None else evaluated(self.view_layer), self.names, self.known)
        for obj in self.copies():   # Read now: the scene changes after.
            view.reach(view.read(obj))
        return view

    def as_built(self, depsgraph):
        """What each copy a swap concerns shows as built (``depsgraph``: evaluated as built, or None)."""
        from .worker_materials import evaluated_slots
        self.built = self._view(depsgraph)
        self.slots = evaluated_slots([copy for _item, _swap, instance in self.swaps.values()
                                      for copy in instance.values()], self.built.depsgraph)

    def apply_all(self):
        """Every placement's swaps, once every copy is placed."""
        for number, index in sorted(self.swaps):
            self.apply(number, index)

    def apply(self, number, index):
        """One placement's swaps."""
        from .worker_materials import apply
        if (number, index) in self.swaps:
            _item, swap, instance = self.swaps[(number, index)]
            apply(instance.values(), swap, self.slots, self.memo, self.state)

    def check(self, depsgraph):
        """Each copy shows each swap where it showed the part's material, and nothing else changed, all of it read;
        else refused."""
        from .refusal_codes import RefusalError
        from .worker_material_check import judge, materials, unread, where
        from .worker_material_reach import rendered
        now, cache, rendering = self._view(depsgraph), {}, None
        for (number, index), (item, swap, instance) in sorted(self.swaps.items(), key=lambda pair: pair[0]):
            involved, owned = set(swap) | set(swap.values()), set(instance.values())
            what = "Worker %d's part: placement %d's swap of %s" % (item["part"]["worker"], index + 1, self.listed(swap))
            for copy in instance.values():
                start = self.known[copy]   # Read by both views (``_view``).
                if self.built.own[start] is None or now.own[start] is None:
                    if copy.instance_type != "NONE" or len(copy.modifiers) or len(copy.particle_systems) or any(
                            slot.material in involved for slot in copy.material_slots):   # Could show the swap.
                        rendering = rendered(self.view_layer) if rendering is None else rendering
                        pointer = start
                        if pointer in rendering and not self._parts_checked(copy, owned, now):
                            self.refuse(RefusalError(
                                "%s cannot be checked on %s: Blender renders it, but the viewport does not evaluate it "
                                "(its viewport display is off), and the check reads the viewport; nothing was merged" % (
                                    what, self.label(copy)), code="UNSUPPORTED"))
                        if pointer not in rendering:   # Shown nowhere here; else checked through its parts.
                            self.unchecked.setdefault(number, []).append(copy.name)   # Its name in this scene.
                    continue
                deep = unread(self.built, copy, start) or unread(now, copy, start)
                if deep is not None:
                    self.refuse(RefusalError("%s cannot be checked on everything %s shows: %s, so it is not checked; "
                                             "nothing was merged" % (what, self.label(copy), deep), code="UNSUPPORTED"))
                why = judge(self.built, now, copy, swap, self.names, cache, start)
                if why is not None:
                    self.refuse(RefusalError("%s did not reach everything %s shows: %s (%s); nothing was merged" % (
                        what, self.label(copy), why, where(copy, owned)), code="CHECK_FAILED"))
            if all(self.built.own[self.known[copy]] is not None and now.own[self.known[copy]] is not None
                   for copy in instance.values()):
                pointers = set().union(*(materials(self.built, copy) for copy in instance.values()))
                shown = {source for source in swap if source.as_pointer() in pointers}
                for source in sorted(set(swap) - shown, key=self.label):   # Known only when all were checked.
                    self.unseen.setdefault(number, []).append({"material": self.label(source),
                                                               "placement": index + 1})

    def _parts_checked(self, copy, owned, now):
        """Whether ``copy`` shows nothing of its own and instances only copies of its placement, each evaluated."""
        from .worker_material_check import GEOMETRY, instanced
        found = instanced(copy)
        return bool(found) and copy.type not in GEOMETRY and not len(copy.modifiers) and all(
            other in owned and self.built.evaluated(other) and now.evaluated(other)
            for _how, others in found for other in others)

    def report(self, number):
        """What the merge's entry for part ``number`` says of its swaps."""
        found = {"variants": self.made[number]} if self.made.get(number) else {}
        if self.unseen.get(number):   # A swap that changed nothing any copy shows: a fact, never an error.
            found["swaps_not_shown"] = self.unseen[number]
        if self.unchecked.get(number):
            found["materials_unchecked"] = {"objects": self.unchecked[number][:SHOWN_UNCHECKED],
                                            "why": "Shown nowhere in this view layer (excluded, or disabled in "
                                                   "viewports and renders), so not checked"}
        return found
