# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Voxel remesh guard (Blender #77868), read from the sources.

Every path into OpenVDB's voxel remesh checks the shared estimate first: both kernel overloads
(modifier and reports), the operator's exec (Python and the Sculpt Mode shortcut reach it) and its
invoke, which asks or offers a size. A new Remesh modifier's voxel size follows the object's size.
test_blender_voxel_remesh_guard(_gui).py prove the behavior in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KERNEL = ROOT / "source/blender/blenkernel/intern/mesh_remesh_voxel.cc"
ESTIMATE = ROOT / "source/blender/blenkernel/intern/mesh_remesh_voxel_estimate.cc"
OPERATOR = ROOT / "source/blender/editors/object/object_remesh.cc"


def body(text, signature):
    start = text.index(signature)
    index, depth = text.index("{", start), 0
    while True:
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        index += 1
        if depth == 0:
            return text[start:index]


def untranslated(msgids):
    """Each (context, msgid) missing from Angelo's template or untranslated in one of its 20 catalogues."""
    sys.path.insert(0, str(ROOT / "scripts/angelo"))
    from i18n import languages, po, sources
    template = {entry.key for entry in po.read(str(sources.POT))[1] if not entry.obsolete}
    problems = [("template", key) for key in msgids if key not in template]
    for language in languages.LANGUAGES:
        entries = {entry.key: entry for entry in po.read(str(sources.LOCALE_DIR / "po" / f"{language.po}.po"))[1]
                   if not entry.obsolete}
        problems += [(language.po, key) for key in msgids if not (key in entries and entries[key].str)]
    return problems


class VoxelRemeshGuardSources(unittest.TestCase):
    def test_both_kernel_overloads_refuse_before_openvdb(self):
        text = KERNEL.read_text()
        overloads = [match.start() for match in re.finditer(r"^Mesh \*BKE_mesh_remesh_voxel\(", text, re.M)]
        self.assertEqual(len(overloads), 2)
        for start in overloads:
            function = body(text, text[start:start + 40])
            # A reservation held until the function returns, taken before OpenVDB runs.
            guard = function.index("const bke::VoxelRemeshReservation reservation = bke::voxel_remesh_reserve(")
            self.assertLess(guard, function.index("remesh_voxel_level_set_create"))
            self.assertIn("if (!reservation.granted()) {", function)

    def test_operator_exec_refuses_and_invoke_asks_or_offers(self):
        text = OPERATOR.read_text()
        execute = body(text, "static wmOperatorStatus voxel_remesh_exec(")
        self.assertLess(execute.index("VoxelRemeshSize::TooLarge"), execute.index("BKE_mesh_remesh_voxel("))
        self.assertIn("voxel_remesh_extras(*mesh)", execute)
        # The accepted size is written only after the remesh succeeded and the copy carried old settings.
        self.assertEqual(execute.count("mesh->remesh_voxel_size ="), 1)
        self.assertLess(execute.index("BKE_mesh_nomain_to_mesh(new_mesh, mesh, ob);"),
                        execute.index("mesh->remesh_voxel_size = voxel_size;"))
        size = body(text, "static float voxel_remesh_size(")
        self.assertIn("!(op.flag & OP_IS_REPEAT_LAST)", size)
        invoke = body(text, "static wmOperatorStatus voxel_remesh_invoke(")
        for case in ("Fits", "Large", "TooLarge"):
            self.assertIn("case bke::VoxelRemeshSize::" + case, invoke)
        self.assertIn('RNA_float_set(op->ptr, "voxel_size", estimate.suggested_voxel_size)', invoke)
        self.assertLess(invoke.index("if (estimate.suggested_voxel_size <= 0.0f) {"),
                        invoke.index('RNA_float_set(op->ptr, "voxel_size"'))
        self.assertEqual(invoke.count("WM_operator_confirm_ex("), 2)
        # The refusal dialog's default button is Cancel; the "large" one's is Remesh.
        self.assertRegex(invoke, r"AlertIcon::Error,\s*true\)")
        self.assertRegex(invoke, r"AlertIcon::Warning,\s*false\)")
        registration = body(text, "void OBJECT_OT_voxel_remesh(")
        self.assertIn("ot->invoke = voxel_remesh_invoke;", registration)
        self.assertIn("PROP_HIDDEN | PROP_SKIP_SAVE", registration)

    def test_extras_follow_the_mesh_settings(self):
        extras = body(OPERATOR.read_text(), "static bke::VoxelRemeshExtras voxel_remesh_extras(")
        self.assertIn("ME_REMESH_REPROJECT_VOLUME | ME_REMESH_REPROJECT_ATTRIBUTES", extras)
        self.assertIn("(mesh.flag & ME_REMESH_FIX_POLES) && mesh.remesh_voxel_adaptivity <= 0.0f", extras)

    def test_estimate_bounds_and_messages(self):
        text = ESTIMATE.read_text()
        constants = dict(re.findall(r"static constexpr double (\w+) = ([^;]+);", text))
        self.assertEqual(constants["LARGE_FREE_SHARE"], "0.5")
        self.assertEqual(constants["LARGE_VERTICES"], "10'000'000.0")
        self.assertIn("INT32_MAX", constants["MAX_VERTICES"])
        estimate = body(text, "VoxelRemeshEstimate voxel_remesh_estimate(")
        # Refused when it does not fit in the free memory (NaN compares false, so it is refused too).
        self.assertIn("!(estimate.peak_bytes <= estimate.free_bytes)", estimate)
        self.assertIn("fitting >= MIN_USEFUL_VERTICES", estimate)
        # An open or non-manifold surface remeshes on both sides (measured up to 2x): counted twice.
        self.assertIn("projected_area(mesh) * (surface_is_open(mesh) ? 2.0 : 1.0)", text)
        self.assertIn("estimate.projected_area = remeshed_area(mesh);", estimate)
        # The remesher's own triangles, each counted positive: a face's vector area cancels across a
        # non-planar or crossed face (a saddle quad read 21x low, a crossed quad zero).
        area = body(text, "static double projected_area(")
        self.assertIn("mesh.corner_tris()", area)
        self.assertNotIn("faces[", area)
        self.assertIn("sum += 0.5 * (std::abs(area.x) + std::abs(area.y) + std::abs(area.z));", area)
        self.assertIn("return count == 1 || count > 2;", body(text, "static bool surface_is_open("))
        reserve = body(text, "VoxelRemeshReservation voxel_remesh_reserve(")
        self.assertIn("compare_exchange_weak", reserve)
        # Messages are formatted to the translation's length, never cut by a fixed buffer.
        self.assertIn("BLI_sprintfN(", body(text, "static std::string format_message("))
        # One pass over the faces, no per-face allocation, and no sampling that could miss a large face.
        self.assertIn("threading::parallel_reduce(", text)
        self.assertNotIn("RandomNumberGenerator", text)
        for function in ("voxel_remesh_too_large_message", "voxel_remesh_large_message"):
            self.assertIn('RPT_("Voxel size %s', body(text, "std::string " + function + "("))
        self.assertIn("source/blender/blenkernel/intern/mesh_remesh_voxel_estimate.cc",
                      (ROOT / "scripts/angelo/i18n/sources.py").read_text())
        cmake = (ROOT / "source/blender/blenkernel/CMakeLists.txt").read_text()
        self.assertIn("intern/mesh_remesh_voxel_estimate.cc", cmake)
        self.assertIn("BKE_mesh_remesh_voxel_estimate.hh", cmake)

    def test_free_memory_is_the_bound_not_the_address_space(self):
        text = ESTIMATE.read_text()
        self.assertIn("BLI_system_memory_available_bytes()", text)
        self.assertNotIn("BLI_system_memory_max_in_megabytes", text)
        system = (ROOT / "source/blender/blenlib/intern/system.cc").read_text()
        physical = body(system, "size_t BLI_system_memory_physical_bytes()")
        for platform_source in ("GlobalMemoryStatusEx", 'sysctlbyname("hw.memsize"', "_SC_PHYS_PAGES"):
            self.assertIn(platform_source, physical)
        available = body(system, "size_t BLI_system_memory_available_bytes()")
        for platform_source in ("ullAvailPhys", "compressor_page_count", "MemAvailable", "memory.max"):
            self.assertIn(platform_source, available)

    def test_new_remesh_modifier_size_follows_the_object(self):
        text = (ROOT / "source/blender/editors/object/object_modifier.cc").read_text()
        add = body(text, "ModifierData *modifier_add(")
        self.assertIn("else if (type == eModifierType_Remesh) {\n      remesh_voxel_size_init(", add)
        self.assertIn("bke::voxel_remesh_default_size(", body(text, "static void remesh_voxel_size_init("))
        default = body(ESTIMATE.read_text(), "float voxel_remesh_default_size(")
        self.assertIn("std::clamp(blender_vertices, DEFAULT_MIN_VERTICES, DEFAULT_MAX_VERTICES)", default)

    def test_interface_text_is_in_every_catalogue(self):
        # The C++ templates (IFACE_/RPT_ in the estimate and the operator) and the hidden property's description.
        estimate, operator = ESTIMATE.read_text(), OPERATOR.read_text()
        templates = re.findall(r'RPT_\(((?:"(?:[^"\\]|\\.)*"\s*)+)\)', estimate)
        msgids = [("", "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', template))) for template in templates]
        msgids += [("", "Remesh at %s"), ("", "Voxel size to use, written to the mesh first (0: the mesh's own)")]
        self.assertEqual(len(templates), 4)
        self.assertIn('IFACE_("Remesh at %s")', operator)
        self.assertEqual(untranslated(msgids), [])


if __name__ == "__main__":
    unittest.main()
