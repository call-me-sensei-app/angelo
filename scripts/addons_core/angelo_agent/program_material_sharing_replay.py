# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bound obligations' replay of who holds a material's staged copy, and the kernel's refusals that follow.

A mixin of ``program_material_obligations._Replay`` (its ``owners``, ``bound``, ``resources`` and ``assigned``):
an in-place edit of a bound material that came to its object in the program, that the program created and
shared, or whose edited copy another object holds now, and a join's slots as its kernel appends them. Each
refusal is raised before approval with the kernel's own words (``material_rules.shared_in_program``).
"""

from . import material_rules


class SharingReplay:
    def shared_in_program(self, target, token, params, error):
        """The kernel's refusals of an in-place edit of a bound material: animated (node_edit._local_animation), or
        shared within the program (material_rules)."""
        resource, everyone = self.resources[token], params.get("shared") == "ALL_USERS"
        if resource.get("animated"):  # Keyed or driven (natively driven ones bind now): refused before approval.
            raise error("ANIMATED_TREE", "animated or driven node settings are edited with reviewed Python")
        others = [owner for owner, state in self.owners.items() if owner != target and token in state["slots"].values()]
        refusal = material_rules.shared_in_program(
            repr(resource["name"][:64]), arrived=resource.get("uid") is not None and token not in self.bound.get(target, ()),
            created=resource.get("uid") is None and others and params.get("shared") == "REFUSE",
            assigned=resource.get("uid") is not None and others and token in self.assigned and not everyone
            and not resource.get("edited"))
        if refusal is None and resource.get("uid") is not None and not everyone:
            refusal = self.edited_shared(target, token, params)
        if refusal is not None:
            raise error(*refusal)

    def data_group(self, reference):
        """The object whose data ``reference`` shares through linked copies made in this program (itself if none)."""
        seen = set()
        while reference in self.linked and reference not in seen:
            seen.add(reference)
            reference = self.linked[reference]
        return reference

    def holders(self, token, besides=None):
        return sorted(owner for owner, state in self.owners.items() if owner != besides and token in state["slots"].values())

    def note_edit(self, token, params):
        """Who holds a bound material's staged copy right after an edit of it in place, and the edit's policy."""
        self.resources[token]["last_edit"] = {"policy": params.get("shared"), "holders": self.holders(token)}

    def edited_shared(self, target, token, params):
        """The kernel's outcome for a bound material another object of the stage holds (node_edit._material): an
        edited copy is not copied for one object silently, and ISOLATE keeps no copy apart from one the program
        gave away or edited for all users. Each is refused here, before approval, not after it; an object that
        held it when it was edited holds the copy the kernel kept for it, so that edit stays allowed."""
        resource, last = self.resources[token], self.resources[token].get("last_edit")
        group = self.data_group(target)  # A linked copy shares the target's data: its slots are the target's.
        others = [owner for owner in self.holders(token, besides=target) if self.data_group(owner) != group]
        name = repr(resource["name"][:64])
        if params.get("shared") == "ISOLATE":
            given = [owner for owner in others if token not in self.bound.get(owner, ())]
            if given or last is not None and last["policy"] == "ALL_USERS" and others:
                return material_rules.shared_in_program(name, isolated=True)
            return None
        if last is not None:
            later = others if last["policy"] == "ALL_USERS" else [owner for owner in others
                                                                   if owner not in last["holders"]]
            if later:
                return material_rules.shared_in_program(name, edited=True)
        return None

    def join(self, target, sources):
        """A join's slots, as its kernel appends them (action_join_separate.join): each source slot's material the
        target lacks, in program order. Their materials arrive in the program (shared_in_program)."""
        for source in sources:
            state = self.owners.get(source)
            for slot in range(state["count"]) if state is not None else ():
                token = state["slots"].get(slot)
                if token is None:
                    self.owner(target)["count"] += 1  # An empty slot is appended as one.
                elif not self.held(target, token):
                    self.place(target, token)
