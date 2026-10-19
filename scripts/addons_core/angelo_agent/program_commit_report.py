# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded post-commit reporting from the existing backend and revision owner.

The original module retains shared state and patchable dependencies.
Methods resolve those named dependencies at call time; this base owns no resources.
"""


# What a committed result says about data the edit left without users. Blender keeps such data in the session
# and never writes it to the file; models spent reviewed scripts deleting it (recorded one-shots). Data
# that only those blocks use (a deleted cube's material) still has a user, so the next save writes it and it reopens
# unused (measured in Blender 5.2, scenario_hygiene.saved_orphans): the note said "needs no cleanup" of it too, and a
# brief asking for no unused data then failed. ``held_only_by_them`` names it; typed programs have no purge, so the
# note names the one call that removes both.
LEFTOVER_NOTE = ("Data this edit replaced or released (an earlier World or mesh, a deleted object's data) now has no "
                 "users. Blender keeps it in this session only and does not write it when the file is saved.")
HELD_NOTE = (" held_only_by_them is data only those blocks use: the next save still writes it, and it reopens unused. "
             "Typed programs have no purge; to remove both now, a reviewed script can call "
             "bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True).")
MAX_LEFTOVER_NAMES = 8
# A program may set the scene's frame_current (rna_rules.SCENE_ROOTS), and every object's revision includes the frame
# (scene_fingerprints): the revisions observed before such a commit no longer bind anything this result does not list.
FRAME_NOTE = ("This edit moved the current frame, which every object's revision includes: bind a later program to "
              "revisions observed after it (look objects up again), never to earlier ones.")


def held_only_by(orphans):
    """Sorted names of the local materials whose every user is a slot of these unused blocks.

    A mesh's (or curve's) material slots are each one user of the material, so a
    material with as many users as those slots, and no fake user, is used by
    nothing a save keeps. One level: the materials' own images and groups are
    not followed.
    """
    references = {}
    for block in orphans:
        for material in getattr(block, "materials", ()) or ():
            if material is not None:
                pointer = material.as_pointer()
                count, _ = references.get(pointer, (0, material))
                references[pointer] = (count + 1, material)
    return sorted(material.name[:64] for count, material in references.values()
                  if material.users == count and not material.use_fake_user and material.library is None)


class ProgramCommitReportMixin:
    __slots__ = ()

    def _world_before(self):
        """The World's ``world_surface.fingerprint`` before this commit writes (its graph, Sun lights by world matrix,
        so a Sun moved through a parent or constraint counts, and the view transform), or None when unreadable."""
        from . import world_surface
        try:
            return world_surface.fingerprint(self.scene)
        except Exception:  # noqa: BLE001  Optional evidence: a commit never fails for it.
            return None

    def _motion_after(self, stage, new_objects):
        """``{"motion": ...}`` for the written and created objects that move in time (``motion_facts.after_edit``),
        read after the live writes; optional evidence that never fails the commit."""
        from types import SimpleNamespace
        from .motion_facts import after_edit
        try:
            objects = [stage.originals[handle] for handle in stage.binding["writes"] if handle in stage.originals]
            window = SimpleNamespace(scene=self.scene, view_layer=self.view_layer, screen=getattr(self, "screen", None))
            found = after_edit(window, objects=objects + list(new_objects))
        except Exception:  # noqa: BLE001  Read after the live writes: it never fails the commit.
            found = None
        return {"motion": found} if found else {}

    def _world_after(self, before):
        """``{"world": ...}``: what the World shows now, its Sun lights against the sky's sun (``from_sky_deg``) and
        its brightness, when this commit changed any of it: the comparison a script's result carries
        (``script_after.notes``). Recorded: without it, typed world and Sun-light edits returned no
        comparison, and in all three bridge trials the sky's sun stood about 158 degrees from the Sun light."""
        from . import world_surface
        try:
            shown = world_surface.changed(self.scene, before)
        except Exception:  # noqa: BLE001  Optional evidence, read after the live writes: it never fails the commit.
            shown = None
        return {"world": shown} if shown is not None else {}

    def _commit_notes(self, stage, replaced, released):
        """``orphan_data``, and the private ``stage_names.FIXES`` when a name the results gave changed at commit:
        a reserved name taken meanwhile, the World's when its users changed after staging, or a new object's that
        the artist took while its stage was suspended (stage_new_objects)."""
        from . import stage_names
        from .action_world import COMMITTED_NAME
        notes = self._leftovers(replaced, released)
        frame = getattr(self, "frame", None)
        if frame is not None and (self.scene.frame_current, self.scene.frame_subframe) != tuple(frame):
            notes["frame_moved"] = FRAME_NOTE
        fixes = {"names": dict(stage.extras[stage_names.CHANGED])} if stage.extras.get(stage_names.CHANGED) else {}
        reported, world = stage.extras.get(COMMITTED_NAME), self.scene.world
        scene = stage.binding.get("scene")
        if (reported and world is not None and scene is not None and "world" in stage.changes.get(scene["handle"], ())
                and world.name != reported):
            fixes["world"] = [reported, world.name[:64]]
        from .stage_new_objects import DRIFTED, NOTE, note
        if stage.extras.get(DRIFTED):  # Created IDs that came back under other names while suspended.
            fixes["moved"] = {kind: dict(names) for kind, names in stage.extras[DRIFTED].items()}
            notes[NOTE] = note(stage.extras)
        if fixes:
            notes[stage_names.FIXES] = fixes
        return notes

    @staticmethod
    def _leftovers(replaced, released):
        """``{"orphan_data": ...}`` for live data the commit replaced or released that no longer has a user."""
        names = {}
        for block in [pair[0] for pair in replaced] + list(released):
            if (block is not None and not block.is_embedded_data and block.users == 0 and not block.use_fake_user
                    and block.library is None):
                names[block.as_pointer()] = block.name[:64]
        if not names:
            return {}
        listed = sorted(names.values())
        report = {"count": len(listed), "names": listed[:MAX_LEFTOVER_NAMES], "note": LEFTOVER_NOTE}
        # Once each: a block both replaced and released (a deleted object's swapped data) is one set of users.
        orphans = {block.as_pointer(): block for block in [pair[0] for pair in replaced] + list(released)
                   if block is not None and block.as_pointer() in names}
        held = held_only_by(list(orphans.values()))
        if held:
            report["held_only_by_them"] = {"count": len(held), "names": held[:MAX_LEFTOVER_NAMES]}
            report["note"] += HELD_NOTE
        return {"orphan_data": report}

    def _committed_rows(self, stage):
        from . import program_commit as commit
        rows = []
        deleted = set(stage.extras.get("deleted", ()))
        for handle, resources in stage.changes.items():
            if handle in stage.objects and handle not in deleted:
                row = commit.modified_row(handle, stage.extras.get("renames", {}).get(handle, stage.logical_names[handle]), resources)
                rows.append({key: value for key, value in row.items() if key != "handle"})
        scene = stage.binding.get("scene")
        if scene is not None and scene["handle"] in stage.changes:
            row = commit.modified_row(scene["handle"], commit.label(self.scene.name), stage.changes[scene["handle"]])
            rows.append({key: value for key, value in row.items() if key != "handle"})
        return rows

    def post_commit_revisions(self, stage, new_objects, clock=None):
        """Document handles and exact NEW revisions of every written or created object.

        Each revision comes from the same ``object_revision`` path that binding
        and commit re-run, read inside the commit operator after the live writes
        and stage cleanup, so the next program can bind these objects without a
        lookup. They are evidence, not authority: any later change, including a
        selection change (or a frame change, for an object that can change with
        time: scene_fingerprints.context_field), makes them stale and that
        program fails closed. Revisions are attempted while a cooperative budget of this
        thread's own work lasts (``clock``, a new ``WorkClock`` by default;
        checked between objects; the first is always attempted, and a small one may use an
        allowance past it: ``revision_budget``); an omission states its reason, as in the turn context.
        """
        from . import program_commit as commit
        from .revision_budget import left_out
        clock = commit.WorkClock() if clock is None else clock
        identities = self.store
        if not (hasattr(identities, "object_handle") and hasattr(identities, "document_epoch")):
            from . import scene_queries as identities  # The production store; also when none was resolved.
        query_error = getattr(identities, "SceneQueryError", commit.SceneQueryError)
        aliases = {obj.as_pointer(): "$" + str(alias).lstrip("$")
                   for alias, obj in getattr(stage.context, "aliases", {}).items()}
        deleted = set(getattr(stage, "extras", {}).get("deleted", ()))
        targets = [(stage.originals[handle], "WRITTEN") for handle in stage.binding["writes"] if handle not in deleted]
        targets += [(obj, "CREATED") for obj in new_objects]
        # Objects a material edit for all users changed: their revisions include the material's graph.
        targets += [(obj, "SHARED") for obj in commit.shared_edits.shared_objects(stage, self, [t[0] for t in targets])]
        started, entries = clock(), []
        for obj, role in targets[:commit.MAX_LIVE_REVISIONS]:
            handle = identities.object_handle(obj)
            entry = {"handle": handle, "name": commit.label(obj.name), "type": obj.type, "role": role}
            if obj.as_pointer() in aliases:
                entry["alias"] = aliases[obj.as_pointer()]
            if entries and left_out(clock, started, commit.POST_COMMIT_REVISION_SECONDS, obj):
                entry["revision_omitted"] = {"reason": "BUDGET", "detail": "cooperative_post_commit_budget"}
            else:
                try:
                    entry["object_revision"] = self._revision(obj, handle)
                except query_error as error:
                    entry["revision_omitted"] = commit.revision_omission(error, closed_codes=True)
                except (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError) as error:
                    entry["revision_omitted"] = commit.revision_omission(error, closed_codes=False)
            entries.append(entry)
        scene = stage.binding.get("scene")
        if scene is not None and scene["write"]:
            # The scene's new settings revision binds a follow-up settings edit.
            from .scene_settings import scene_handle, settings_revision
            entries.append({"handle": scene_handle(self.scene), "name": commit.label(self.scene.name), "type": "SCENE",
                            "role": "WRITTEN", "object_revision": settings_revision(self.scene)})
        return {"status": "COMMITTED", "document_epoch": identities.document_epoch(), "objects": entries,
                "truncated": len(targets) > commit.MAX_LIVE_REVISIONS, "note": commit.LIVE_REVISIONS_NOTE}
