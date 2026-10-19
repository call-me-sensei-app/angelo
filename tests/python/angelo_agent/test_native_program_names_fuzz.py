# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Differential fuzz of program_names against a model of plain Blender running the same edits.

Seeded programs of renames (of existing and new objects), deletes (sometimes
of an object another scene still links), camera and empty creation and named
and unnamed duplicates run on the real kernels, on test_native_program_names'
fake Main that names like Blender, and on ``Blender``: unique names, the lowest
free ``.NNN`` of the base, and a delete that frees a name unless another scene
keeps the object. The contract is the module's: names follow program order,
as Blender's own sequence of the same edits would, except that a request by
name takes at once a name every declared holder of which the program releases
later (``plan_oracle`` states that rule independently of ``plan_names``, and a
plan it finds ambiguous must be refused before staging).

* Every staged name equals Blender's; an exact-name kind (``object_rename``,
  ``create_object``, a named ``object_duplicate``) refuses exactly when Blender
  would suffix the name it asked for.
* Commit gives every promised name, leaves no private name on an object that
  stays, and without an artist's edit is never refused and equals Blender.
* An artist taking a wanted name while the stage waits fails the commit
  closed, with an exact rollback.

Seeds are fixed. ANGELO_NAMES_FUZZ_RUNS sets the programs per seed; the
default keeps the host suite fast; 2000 take about 85 s.
"""

import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_program_names as base  # noqa: E402
from test_native_program_names import FakeCollection, FakeObject, World, camera, empty, op, roll_back  # noqa: E402
from angelo_agent import program_names as names  # noqa: E402
from angelo_agent.action_catalog import deleted_references  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402

CAMERA, CUBE, LAMP, EXTRA = base.CAMERA, base.CUBE, base.LAMP, base.EXTRA
POOL = ("Camera", "Cube", "Light", "Camera.001", "Shot", "Cube.001", "Prop", "Shot.002")
RUNS = int(os.environ.get("ANGELO_NAMES_FUZZ_RUNS", "5000"))


class Blender:
    """Plain Blender running the same sequence: reference -> name; an unlinked kept object stays as '#reference'."""

    def __init__(self, names_by_ref):
        self.names = dict(names_by_ref)

    def taken(self, name, me):
        return any(other != me and held == name for other, held in self.names.items())

    def give(self, ref, wanted):
        name = wanted
        if self.taken(wanted, ref):
            stem = names._NUMBERED.sub("", wanted)
            name = next(candidate for candidate in ("%s.%03d" % (stem, number) for number in range(1, 10000))
                        if not self.taken(candidate, ref))
        self.names[ref] = name
        return name

    def delete(self, ref, kept):
        name = self.names.pop(ref)
        if kept:
            self.names["#" + ref] = name  # Another scene still links it: it keeps its name.


def generate(rng, length):
    live = [CAMERA, CUBE, LAMP, EXTRA]
    deleted, aliases, program = set(), [], []
    for index in range(length):
        choice = rng.random()
        kept = [handle for handle in live if handle not in deleted]
        if choice < .3 and (kept or aliases):
            program.append(op("object_rename", rng.choice(kept + aliases), name=rng.choice(POOL)))
        elif choice < .5 and kept:
            target = rng.choice(kept)
            deleted.add(target)
            program.append(op("object_delete", target))
        elif choice < .75:
            aliases.append("$n%d" % index)
            program.append(camera(rng.choice(POOL), alias="n%d" % index))
        elif choice < .85:
            aliases.append("$e%d" % index)
            program.append(empty(rng.choice(POOL), "e%d" % index))
        elif kept or aliases:
            source = rng.choice(kept + aliases)
            aliases.append("$d%d" % index)
            program.append(op("object_duplicate", source=source, alias="d%d" % index,
                              name=rng.choice(POOL + (None, None)), linked=False, location=None))
    return program


def _request(operation):
    if operation["kind"] == "object_rename":
        return operation["target"], operation["parameters"]["name"]
    if operation["kind"] in {"camera_create", "create_object", "object_duplicate"} and operation["parameters"]["name"]:
        return "$" + operation["parameters"]["alias"], operation["parameters"]["name"]
    return None


def plan_oracle(program, live, shared):
    """The rule as stated: ({index: name} taken at once, ambiguous).

    A request by name for a name declared objects hold at that point takes it
    at once when one of them is renamed or deleted later, every one of them
    ends without it (renamed to another name, or deleted and linked by no
    other scene) and no later operation asks for it; when one of them is
    renamed or deleted later but that fails, the plan is ambiguous.
    """
    declared, decisions = dict(live), {}
    for index, operation in enumerate(program):
        asked, rest = _request(operation), program[index + 1:]
        if asked is not None:
            ref, name = asked
            fates = {}
            for holder in [handle for handle, held in declared.items() if held == name and handle != ref]:
                renames = [later["parameters"]["name"] for later in rest
                           if later["kind"] == "object_rename" and later["target"] == holder]
                deleted = any(later["kind"] == "object_delete" and later["target"] == holder for later in rest)
                fates[holder] = (bool(renames) or deleted, (renames or [name])[-1] != name or deleted and holder != shared)
            if any(touched for touched, _ in fates.values()):
                others = [_request(later) for later in rest]
                if not all(released for _, released in fates.values()) or any(
                        other is not None and other[1] == name and other[0] != ref for other in others):
                    return None, True
                decisions[index] = name
        if operation["kind"] == "object_rename" and operation["target"] in declared:
            declared[operation["target"]] = operation["parameters"]["name"]
        if operation["kind"] == "object_delete" and operation["target"] != shared:
            declared[operation["target"]] = None
    return decisions, False


def _reference(operation):
    if operation["kind"] in {"camera_create", "create_object", "object_duplicate"}:
        return "$" + operation["parameters"]["alias"]
    return operation["target"]


class DifferentialFuzzTests(unittest.TestCase):
    def stage(self, program, rng):
        """Stage ``program``; return (world, model, shared) or None when Angelo refused it, as Blender implies."""
        world = World(self)
        world.add_live(EXTRA, "Camera.001")
        shared = rng.choice(list(world.live)) if rng.random() < .2 else None
        if shared is not None:
            world.live[shared].users_collection.append(FakeCollection())  # Another scene links it too.
        names.record_kept_elsewhere(world.context.extras, world.live,
                                    [ref for operation in program for ref in deleted_references(operation)],
                                    [world.scene.collection, world.collection])
        live = {handle: obj.name for handle, obj in world.live.items()}
        decisions, ambiguous = plan_oracle(program, live, shared)
        try:
            world.plan(program)
        except ProgramError as error:
            self.assertTrue(ambiguous and "NAME_AMBIGUOUS" in str(error), ("refused a clear plan", program, str(error)))
            return None
        self.assertFalse(ambiguous, ("an ambiguous plan was staged", program))
        self.assertEqual(sorted(map(tuple, world.context.extras.get(names.FORWARD, []))),
                         sorted((_request(program[index])[0], name) for index, name in decisions.items()),
                         ("plan_names decided otherwise", program))
        model = Blender(live)
        for index, operation in enumerate(program):
            kind, params, ref = operation["kind"], operation["parameters"], _reference(operation)
            if kind == "object_delete":
                world.run(operation)
                model.delete(ref, ref == shared)
                continue
            asked = params["name"] or model.names[params["source"]]
            exact = kind in {"object_rename", "create_object"} or kind == "object_duplicate" and params["name"]
            if index in decisions:
                model.names[ref] = blender = asked  # Taken at once: its holders release it later.
            else:
                blender = model.give(ref, asked)
            try:
                result = world.run(operation)
            except RefusalError as error:
                self.assertTrue(exact and blender != asked, ("refused a name Blender gives", program, str(error)))
                return None
            angelo = result["name"] if kind == "object_rename" else result["object"]
            self.assertEqual(angelo, blender, ("a staged name differs from Blender's", program, operation))
            self.assertFalse(exact and blender != asked, ("an exact-name kind accepted a suffix", program, operation))
        return world, model, shared

    def commit(self, world, model, shared, program, artist=None):
        extras = world.context.extras
        promised = {ref: names.committed_name(extras, obj) for ref, obj in world.context.aliases.items()}
        before = world.snapshot()
        if artist is not None:
            artist(world)
        try:
            journal = world.commit()
        except ProgramError as error:
            self.assertEqual(world.snapshot(), before, ("rollback not exact", program))
            self.assertIsNotNone(artist, ("a plan Blender runs cleanly was refused at commit", program, str(error)))
            return "refused:" + error.code
        deleted = set(extras.get("deleted", ()))
        stays = {handle: obj.name for handle, obj in world.live.items() if handle not in deleted}
        stays.update({"#" + handle: world.live[handle].name for handle in deleted if handle == shared})
        new = {ref: obj.name for ref, obj in world.context.aliases.items()}
        for name in list(stays.values()) + list(new.values()):
            self.assertFalse(names.private_name(name), ("a private name stays", program))
        self.assertEqual(new, promised, ("commit gave a name the stage did not promise", program))
        if artist is None:
            self.assertEqual({**stays, **new}, model.names, ("committed names differ from Blender", program))
        roll_back(journal)
        self.assertEqual(world.snapshot(), before, ("rollback not exact", program))
        return "committed"

    def fuzz(self, seed, artist=None):
        rng, tally = random.Random(seed), {}
        for _ in range(RUNS):
            program = generate(rng, rng.randint(1, 8))
            staged = self.stage(program, rng)
            outcome = "refused_on_stage" if staged is None else self.commit(*staged, program, artist)
            tally[outcome] = tally.get(outcome, 0) + 1
        return tally

    def test_names_match_blender_and_every_clean_plan_commits(self):
        tally = self.fuzz(20261014)
        self.assertGreater(tally.get("committed", 0), RUNS // 5, tally)  # The small name pool collides often.

    def test_an_artist_taking_a_wanted_name_fails_closed_with_exact_rollback(self):
        def artist(world):
            extras = world.context.extras
            wanted = sorted(set(extras.get(names.CLAIMS, {}).values()) | set(extras.get("renames", {}).values()))
            if wanted and world.main.objects.get(wanted[0]) is None:
                FakeObject(world.main, wanted[0])
        tally = self.fuzz(7, artist)
        self.assertTrue(any(key.startswith("refused:") for key in tally), tally)


if __name__ == "__main__":
    unittest.main()
