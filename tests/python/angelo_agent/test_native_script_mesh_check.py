# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The check a reviewed script's meshes get before anything evaluates them (``script_mesh_check``), with stand-in
meshes: which meshes are validated, and what the result says.

``test_blender_script_invalid_mesh`` runs real scripts in a real Blender and reads the meshes back (competitor
triage row B4), and ``test_blender_mesh_topology_seal`` proves the seal itself on every way a script can write
topology; what is unsound is Blender's own ``Mesh.validate()`` to say. Pinned here, where no Blender is needed:

* a sound mesh is asked and never changed or reported; an unsound one is repaired by its own ``validate`` and
  reported with its counts before and after (``still`` when a second ``validate`` had to correct it again);
* a mesh is validated when its TOPOLOGY SEAL is broken or was never set (``Mesh.is_topology_sealed``: Blender
  breaks it on any access that could write the mesh's edges, face corners or face offsets, and on any change of
  its element counts), whatever its size, linked or in Edit Mode, and is sealed again once ``validate`` finds
  it sound. A mesh whose seal holds was not touched since Blender last found it sound, and is not validated:
  validating every mesh after every script cost 36 to 175 ms a call on scenes of ten thousand small meshes to
  four meshes of a million quads, paid by scripts that touched no mesh (coordinator's ruling, 2026-11-15);
* the audit's case (external audit, round 2, F1: 600,000 vertices and one edge set to (0, 600001), the same
  counts before and after, twenty calls) is repaired by the call that wrote it: the write broke the seal;
* a Blender without the seal (an older build under newer scripts, a stand-in) gets every mesh validated after
  every script, as before: never fewer checks than it can prove are enough;
* a mesh that could NOT be checked (its ``validate`` raised, or the meshes could not be listed) is never silent:
  it is in the outcome and in the tool's result (``meshes_unchecked``, with why), it is not sealed, and the
  others are still checked; a mesh ``validate`` had to correct twice is not sealed either;
* a mesh whose POSITIONS alone were written (``is_topology_sealed`` holds, ``is_geometry_sealed`` does not) is
  not validated: its coordinates are scanned for a value that is not a finite number, which is all ``validate``
  would have done for it, and only a mesh that has one is validated (``validate`` zeroes it) and reported;
* the seals of a whole file are read in one call where the collection offers it, and one by one where it fails;
* Angelo's own read of a sealed mesh (``reading``) leaves it sealed, and never seals an unsealed one;
* the result names what was repaired and what was not checked, at most ``MAX_ROWS`` rows each with the rest
  counted, and nothing when the failed script's file was put back or there is nothing to say;
* the executor calls it between the script and anything that evaluates the scene, on both ways out.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace

from angelo_agent import script_mesh_check

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
COUNTS = {"vertices": 3, "edges": 3, "corners": 3, "faces": 1}
NOTHING = {"repaired": [], "unchecked": [], "validated": 0, "scanned": 0}
NAN = float("nan")


class Vertices:
    """``mesh.vertices`` as far as the check uses it: a length and one ``foreach_get`` of the coordinates."""

    def __init__(self, mesh, count):
        self.mesh, self.count, self.read = mesh, count, 0

    def __len__(self):
        return self.count

    def foreach_get(self, name, values):
        assert name == "co" and len(values) == self.count * 3
        self.read += 1
        self.mesh.touched(topology=False)   # Blender hands a reader the writable array.
        for index in range(len(values)):
            values[index] = 0.0
        if self.mesh.not_finite is not None and len(values):
            values[len(values) - 1] = self.mesh.not_finite


class OldMesh:
    """A triangle unless told otherwise, in a Blender without the topology seal. ``validate`` answers as Blender's
    does: False for a sound mesh, which it leaves alone; True for an unsound one, whose faces and edges it then
    drops (unless ``repairs`` is False)."""

    def __init__(self, name="Mesh", sound=True, size=(3, 3, 3, 1), library=None, editmode=False, repairs=True):
        self.name, self.sound, self.library, self.is_editmode, self.repairs = name, sound, library, editmode, repairs
        self.edges, self.loops, self.polygons = (range(count) for count in size[1:])
        self.vertices, self.not_finite = Vertices(self, size[0]), None
        self.validated = self.updated = 0
        self.cleaned = False

    def validate(self, verbose=False, clean_customdata=True):
        self.validated += 1
        self.cleaned = self.cleaned or clean_customdata   # Whether it was ever asked to strip cached layers.
        self.touched()   # Blender's validate takes the arrays for writing, sound or not.
        if self.not_finite is not None:   # A coordinate that is not a finite number is set to zero.
            self.not_finite = None
            return True
        if self.sound:
            return False
        if self.repairs:
            self.sound, self.edges, self.loops, self.polygons = True, range(0), range(0), range(0)
        return True

    def update(self, calc_edges=False):
        self.updated += 1

    def touched(self, topology=True):
        pass


class Mesh(OldMesh):
    """The same in a Blender with the seal: unsealed until ``topology_seal``, and again after any write. The
    geometry seal is the topology seal and the positions together."""

    def __init__(self, *args, **named):
        super().__init__(*args, **named)
        self.is_topology_sealed = self.is_geometry_sealed = False
        self.seals = 0

    def topology_seal(self, positions=True):
        self.is_topology_sealed, self.is_geometry_sealed = True, positions
        self.seals += 1

    def touched(self, topology=True):
        self.is_geometry_sealed = False
        if topology:
            self.is_topology_sealed = False

    def write(self, sound=True):
        """A script's write to the topology arrays through RNA: in place, the same counts."""
        self.sound = sound
        self.touched()

    def write_positions(self, value=None):
        """A script's write to the coordinates alone; ``value`` is one that is not a finite number, or None."""
        self.not_finite = value
        self.touched(topology=False)


class Meshes(list):
    """``bpy.data.meshes`` as far as the check uses it: iteration, and one ``foreach_get`` for a property."""
    asked = 0
    fails = False

    def foreach_get(self, name, values):
        self.asked += 1
        if self.fails:
            raise RuntimeError("no raw access here")
        for index, mesh in enumerate(self):
            values[index] = getattr(mesh, name)


def data(*meshes):
    return SimpleNamespace(data=SimpleNamespace(meshes=list(meshes)))


def after(*meshes, prior=None):
    return script_mesh_check.after(data(*meshes), prior)


class TheCheck(unittest.TestCase):
    def test_a_sound_mesh_is_asked_and_left_alone_and_an_unsound_one_is_repaired_and_reported(self):
        sound, unsound = Mesh("Sound"), Mesh("Unsound", sound=False)
        outcome = after(sound, unsound)
        self.assertEqual(outcome, {"unchecked": [], "validated": 2, "scanned": 0, "repaired": [{
            "mesh": "Unsound", "found": script_mesh_check.FOUND, "before": COUNTS,
            "after": {"vertices": 3, "edges": 0, "corners": 0, "faces": 0}}]})
        self.assertEqual((sound.validated, sound.updated), (1, 0))
        # Only the geometry is asked about: a sound mesh's cached layers are never stripped on the way.
        self.assertFalse(sound.cleaned or unsound.cleaned)
        self.assertEqual((unsound.validated, unsound.updated), (2, 1))   # The repair, then once more: sound now.
        self.assertTrue(sound.is_geometry_sealed and unsound.is_geometry_sealed)   # Found sound: sealed.

    def test_a_mesh_rewritten_in_place_is_repaired_by_the_call_that_wrote_it_whatever_its_size(self):
        """The audit's case: the same counts before and after, more elements than the first check's whole budget
        covered, twenty calls."""
        for size in ((600_000, 1, 0, 0), (1_000_000, 2_000_000, 4_000_000, 1_000_000), (3, 3, 3, 1)):
            with self.subTest(size=size):
                large, small = Mesh("Large", size=size), Mesh("Small")
                prior = SimpleNamespace()
                self.assertEqual(after(large, small, prior=prior), dict(NOTHING, validated=2))
                large.write(sound=False)   # An index out of range, written in place: only the arrays changed.
                for call in range(20):
                    outcome = after(large, small, prior=prior)
                    self.assertIs(prior.mesh_check, outcome)
                    self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["validated"]),
                                     (["Large"], 1) if call == 0 else ([], 0))
                    self.assertEqual(outcome["unchecked"], [])
                self.assertEqual((large.validated, small.validated), (3, 1))

    def test_only_a_mesh_whose_topology_was_touched_is_validated(self):
        meshes = [Mesh("Mesh %d" % index, size=(1000, 2000, 4000, 1000)) for index in range(5000)]
        self.assertEqual(after(*meshes), dict(NOTHING, validated=5000))   # Never checked yet: every one, once.
        self.assertTrue(all(mesh.seals == 1 for mesh in meshes))
        self.assertEqual(after(*meshes), NOTHING)   # A script that touched no mesh validates none.
        meshes[17].write()
        meshes[4321].write(sound=False)
        outcome = after(*meshes)
        self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["validated"]), (["Mesh 4321"], 2))
        self.assertEqual([mesh.validated for mesh in (meshes[0], meshes[17], meshes[4321])], [1, 2, 3])
        self.assertEqual(after(*meshes), NOTHING)
        new = Mesh("New", sound=False)   # A mesh the script made was never sealed.
        self.assertEqual([row["mesh"] for row in after(*meshes, new)["repaired"]], ["New"])

    def test_a_mesh_whose_positions_alone_were_written_is_scanned_and_validated_only_for_a_value_not_finite(self):
        """``validate`` also sets a coordinate that is not a finite number to zero. A script that moves vertices
        does not touch topology, so the topology seal holds: the check reads the coordinates once instead of
        validating the mesh, and validates it only when one of them is not finite (coordinator's condition on the
        seal, 2026-11-15)."""
        for size in ((3, 3, 3, 1), (1_000, 2_000, 4_000, 1_000)):
            with self.subTest(size=size):
                moved, broken, still = Mesh("Moved", size=size), Mesh("Broken", size=size), Mesh("Still", size=size)
                self.assertEqual(after(moved, broken, still), dict(NOTHING, validated=3))
                moved.write_positions()
                outcome = after(moved, broken, still)
                self.assertEqual(outcome, dict(NOTHING, scanned=1))   # Read once, not validated, sealed again.
                self.assertEqual((moved.validated, moved.vertices.read, moved.is_geometry_sealed), (1, 1, True))
                self.assertEqual(still.vertices.read, 0)   # An untouched mesh's coordinates are never read.
                for value in (NAN, float("inf"), float("-inf")):
                    broken.write_positions(value)
                    outcome = after(moved, broken, still)
                    self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["validated"],
                                      outcome["scanned"]), (["Broken"], 1, 1), value)
                    self.assertEqual(outcome["repaired"][0]["before"], outcome["repaired"][0]["after"])
                    self.assertTrue(broken.is_geometry_sealed and broken.not_finite is None)
                    self.assertEqual(after(moved, broken, still), NOTHING)

    def test_a_scan_that_cannot_be_made_is_a_validation_never_a_pass(self):
        unreadable = Mesh("Unreadable")
        after(unreadable)
        unreadable.write_positions(NAN)
        unreadable.vertices.foreach_get = lambda *_args: (_ for _ in ()).throw(RuntimeError("no raw access"))
        outcome = after(unreadable)
        self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["validated"]), (["Unreadable"], 1))

    def test_a_blender_without_the_seal_validates_every_mesh_after_every_script(self):
        meshes = [OldMesh("Mesh %d" % index) for index in range(50)]
        meshes[43].sound = False
        for call in (1, 2, 3):
            outcome = after(*meshes)
            self.assertTrue(all(mesh.validated >= call for mesh in meshes))
            self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["validated"]),
                             (["Mesh 43"] if call == 1 else [], 50))

    def test_the_seals_of_a_file_are_read_in_one_call_and_one_by_one_where_that_fails(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                meshes = Meshes(Mesh("Mesh %d" % index) for index in range(40))
                meshes.fails = fails
                stand_in = SimpleNamespace(data=SimpleNamespace(meshes=meshes))
                self.assertEqual(script_mesh_check.after(stand_in)["validated"], 40)
                meshes[7].write(sound=False)
                outcome = script_mesh_check.after(stand_in)
                self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["validated"]), (["Mesh 7"], 1))
                self.assertEqual(meshes.asked, 2)   # Once a check, never once a mesh.

    def test_there_is_no_budget_rotation_or_reading_before_the_script(self):
        for gone in ("BUDGET_SECONDS", "PER_MESH_SECONDS", "PER_ELEMENT_SECONDS", "_next", "before"):
            self.assertFalse(hasattr(script_mesh_check, gone), gone)
        import inspect
        self.assertEqual(list(inspect.signature(script_mesh_check.after).parameters), ["bpy", "prior"])

    def test_linked_and_edit_mode_meshes_are_validated_too_and_a_failed_repair_is_said_and_asked_again(self):
        linked = Mesh("Linked", sound=False, library=object())
        editing = Mesh("Editing", sound=False, editmode=True)
        stubborn = Mesh("Stubborn", sound=False, repairs=False)
        outcome = after(linked, editing, stubborn)
        self.assertEqual([(row["mesh"], "still" in row) for row in outcome["repaired"]],
                         [("Linked", False), ("Editing", False), ("Stubborn", True)])
        self.assertEqual(outcome["unchecked"], [])
        self.assertEqual([mesh.is_topology_sealed for mesh in (linked, editing, stubborn)], [True, True, False])
        self.assertEqual([row["mesh"] for row in after(linked, editing, stubborn)["repaired"]], ["Stubborn"])

    def test_a_mesh_that_could_not_be_checked_is_said_each_time_and_the_others_are_still_checked(self):
        broken = Mesh("Broken")
        broken.validate = lambda **_named: (_ for _ in ()).throw(RuntimeError("the mesh is gone: /Users/someone"))
        unsound = Mesh("Unsound", sound=False)
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = after(broken, unsound)
            again = after(broken, unsound)
        self.assertEqual([row["mesh"] for row in outcome["repaired"]], ["Unsound"])
        self.assertEqual(outcome["unchecked"], [{"mesh": "Broken", "why": "Mesh.validate() raised RuntimeError"}])
        self.assertNotIn("someone", str(outcome))
        self.assertFalse(broken.is_topology_sealed)   # Not found sound: asked again, and said again.
        self.assertEqual(again, dict(NOTHING, unchecked=outcome["unchecked"], validated=1))

    def test_a_seal_that_cannot_be_read_or_set_costs_a_check_never_a_miss(self):
        class Unreadable(Mesh):
            def _unreadable(self):
                raise RuntimeError("no runtime")
            is_geometry_sealed = is_topology_sealed = property(_unreadable, lambda self, value: None)
        unreadable, unsealable = Unreadable("Unreadable"), Mesh("Unsealable")
        unsealable.topology_seal = lambda: (_ for _ in ()).throw(RuntimeError("no runtime"))
        for _call in (1, 2):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(after(unreadable, unsealable), dict(NOTHING, validated=2))
        self.assertEqual((unreadable.validated, unsealable.validated), (2, 2))

    def test_angelos_own_read_of_a_sealed_mesh_leaves_it_sealed(self):
        """RNA hands a reader writable arrays, which breaks the seal. Angelo reads the meshes of the objects a
        script changed right after the check (their revision, ``scene_geometry``): without ``reading`` each of
        them was validated a second time by the next script (Linux build of 97e97d74ae2: the mesh repaired by one
        call was validated again by the next, which touched nothing)."""
        sealed, unsealed = Mesh("Sealed"), Mesh("Unsealed")
        sealed.topology_seal()
        with script_mesh_check.reading(sealed):
            sealed.touched()   # As Blender does when the arrays are handed out.
            self.assertFalse(sealed.is_topology_sealed)
        self.assertTrue(sealed.is_geometry_sealed)
        with script_mesh_check.reading(unsealed):   # A read never seals what no check found sound.
            unsealed.touched()
        self.assertFalse(unsealed.is_topology_sealed)
        with self.assertRaises(ValueError), script_mesh_check.reading(sealed):
            sealed.touched()
            raise ValueError("the read gave up")
        self.assertTrue(sealed.is_geometry_sealed)
        sealed.write_positions(NAN)   # Positions a script wrote and no check has read: a read never seals them,
        with script_mesh_check.reading(sealed):   # and it leaves the topology seal as it found it: standing.
            sealed.touched()
        self.assertEqual((sealed.is_topology_sealed, sealed.is_geometry_sealed), (True, False))
        outcome = after(sealed)
        self.assertEqual(([row["mesh"] for row in outcome["repaired"]], outcome["scanned"]), (["Sealed"], 1))
        # Every reader Angelo runs around a script is one: the revision's and the scene signature's (Linux build
        # of fad9a70141d: the signature read every scene mesh's coordinates after each script, so each was
        # scanned by the next script, and a mesh the revision then read was validated a second time).
        for module in ("scene_geometry.py", "scene_signature.py"):
            self.assertIn("with script_mesh_check.reading(", (ADDON / module).read_text(encoding="utf-8"), module)
        with script_mesh_check.reading(OldMesh("No seal here")), script_mesh_check.reading(None):
            pass
        self.assertEqual(after(sealed)["validated"], 0)

    def test_meshes_that_cannot_be_listed_are_said_and_where_there_are_none_nothing_is(self):
        """The executor's own host test runs a script against a stand-in ``bpy`` that has no ``data`` at all
        (``test_native_trusted_scripts``): nothing to check there, and nothing raises."""
        for stand_in in (SimpleNamespace(), SimpleNamespace(data=SimpleNamespace()), SimpleNamespace(data=None),
                         SimpleNamespace(data=SimpleNamespace(meshes=None))):
            self.assertEqual(script_mesh_check.after(stand_in, SimpleNamespace()), NOTHING)

        class Unlistable:
            def __iter__(self):
                raise RuntimeError("the file is being replaced")

            def __bool__(self):
                return True
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = script_mesh_check.after(SimpleNamespace(data=SimpleNamespace(meshes=Unlistable())))
        self.assertEqual(outcome, dict(NOTHING, unchecked=[
            {"mesh": "(every mesh)", "why": "the file's meshes could not be listed (RuntimeError)"}]))


class TheResult(unittest.TestCase):
    ROW = {"mesh": "M", "found": script_mesh_check.FOUND, "before": {}, "after": {}}
    MISSED = {"mesh": "N", "why": "Mesh.validate() raised RuntimeError"}

    def test_a_result_names_what_was_repaired_and_what_was_not_checked_and_says_nothing_otherwise(self):
        result = {"success": True}
        script_mesh_check.attach(result, {"repaired": [dict(self.ROW)], "unchecked": []})
        self.assertEqual(result["meshes_repaired"], [self.ROW])
        self.assertIn("Mesh.validate()", result["mesh_repair"])
        self.assertNotIn("meshes_repaired_omitted", result)
        self.assertNotIn("meshes_unchecked", result)
        missed = {"success": True}
        script_mesh_check.attach(missed, {"repaired": [], "unchecked": [dict(self.MISSED)]})
        self.assertEqual(missed["meshes_unchecked"], [self.MISSED])
        self.assertIn("validate()", missed["mesh_unchecked"])
        self.assertNotIn("meshes_repaired", missed)
        many = {"success": True}
        rows = [dict(self.ROW)] * (script_mesh_check.MAX_ROWS + 5)
        skipped = [dict(self.MISSED)] * (script_mesh_check.MAX_ROWS + 2)
        script_mesh_check.attach(many, {"repaired": rows, "unchecked": skipped})
        self.assertEqual((len(many["meshes_repaired"]), many["meshes_repaired_omitted"]),
                         (script_mesh_check.MAX_ROWS, 5))
        self.assertEqual((len(many["meshes_unchecked"]), many["meshes_unchecked_omitted"]),
                         (script_mesh_check.MAX_ROWS, 2))
        for result, outcome in (({"success": True}, dict(NOTHING, validated=9)), ({"success": True}, None),
                                ({"script_error": True, "main_data_restored": True},
                                 {"repaired": [dict(self.ROW)], "unchecked": [dict(self.MISSED)]})):
            before = dict(result)
            script_mesh_check.attach(result, outcome)
            self.assertEqual(result, before)
        kept = {"script_error": True, "main_data_restored": False}
        script_mesh_check.attach(kept, {"repaired": [dict(self.ROW)], "unchecked": [dict(self.MISSED)]})
        self.assertIn("meshes_repaired", kept)
        self.assertIn("meshes_unchecked", kept)

    def test_the_executor_checks_between_the_script_and_anything_that_evaluates(self):
        text = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        run = "confinement, read = run_confined("   # The script's confined run (script_confinement, the C guard).
        ran = text.index(run)   # The script's branch: from its own test of the name to the next.
        branch = text[text.rindex('if name == "angelo_execute_python":', 0, ran):
                      text.index('if name == "angelo_apply_batch":', ran)]
        ran, checked = branch.index(run), branch.index("script_mesh_check.after(bpy, self._prior)")
        self.assertLess(ran, checked)
        for later in ("kit.finish()", "display.finish(bpy)", "self.window.view_layer.update()"):
            self.assertLess(checked, branch.index(later), later)
        failure = branch[branch.index("except script_errors.SCRIPT_FAILURES"):]
        self.assertLess(failure.index("script_mesh_check.after(bpy, self._prior)"),
                        failure.index("script_errors.failed("))
        after = (ADDON / "script_after.py").read_text(encoding="utf-8")
        self.assertNotIn("script_mesh_check.before", after)   # Nothing is read before the script any more.
        self.assertIn("script_mesh_check.attach(result, self.mesh_check)", after)


if __name__ == "__main__":
    unittest.main()
