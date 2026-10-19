# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pin the memfile Undo swap contract; the native suite and the stress prove the behaviour.

``read_libblock_undo_restore_at_old_address`` swaps a newly read ID into its previous address
before lib-linking. Its self-remap may only touch the pointers ``lib_link_cb`` skips (resolved
while reading data); every other pointer still holds a written address that lib-linking
resolves, and remapping it lost ``scene.world`` on Redo (doc/angelo/agent_execution.rst).
``test_blender_undo_unlinked_pointers.py`` calls the swap with the flags pinned here.
"""

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
READFILE = ROOT / "source/blender/blenloader/intern/readfile.cc"
REMAP_HEADER = ROOT / "source/blender/blenkernel/BKE_lib_remap.hh"
REMAP = ROOT / "source/blender/blenkernel/intern/lib_remap.cc"
LIB_ID_HEADER = ROOT / "source/blender/blenkernel/BKE_lib_id.hh"
NATIVE_TEST = HERE / "test_blender_undo_unlinked_pointers.py"


def function(path, signature):
    source = path.read_text()
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 2]


def literal(node):
    """A literal, or a left shift of literals (the flag values)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.LShift):
        return literal(node.left) << literal(node.right)
    return ast.literal_eval(node)


def native_constant(name):
    for node in ast.parse(NATIVE_TEST.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == name for target in node.targets):
            if isinstance(node.value, ast.Dict):
                return {literal(key): literal(value) for key, value in zip(node.value.keys, node.value.values)}
            return literal(node.value)
    raise KeyError(name)


def condition_flags(body, condition_start):
    """IDWALK_CB_* names in the ``if`` condition starting with ``condition_start``."""
    start = body.index(condition_start)
    return set(re.findall(r"IDWALK_CB_[A-Z_]+", body[start:body.index("{", start)]))


class UndoUnlinkedPointerContractTests(unittest.TestCase):
    def test_undo_restore_swap_skips_the_pointers_lib_linking_resolves(self):
        body = function(READFILE, "static void read_libblock_undo_restore_at_old_address(")
        call = body[body.index("BKE_lib_id_swap_full("):body.index(");", body.index("BKE_lib_id_swap_full("))]
        self.assertIn("ID_REMAP_SKIP_LIB_LINKED_POINTERS", call)
        self.assertIn("true", call)  # The self-remap the embedded IDs need stays on.
        self.assertEqual(set(re.findall(r"ID_REMAP_[A-Z_]+", call)), set(native_constant("READFILE_SWAP_FLAGS")))

    def test_native_suite_uses_the_declared_flag_values(self):
        header = REMAP_HEADER.read_text()
        declared = {name: 1 << int(shift) for name, shift in re.findall(r"(ID_REMAP_[A-Z_]+) = 1 << (\d+),", header)}
        for name, value in native_constant("READFILE_SWAP_FLAGS").items():
            with self.subTest(flag=name):
                self.assertEqual(declared[name], value)
        self.assertEqual(len(set(declared.values())), len(declared), "remap flags must not share a bit")

    def test_remap_filter_mirrors_the_lib_link_skip_set(self):
        remap = function(REMAP, "static int foreach_libblock_remap_callback(")
        self.assertEqual(condition_flags(remap, "if ((id_remap_data->flag & ID_REMAP_SKIP_LIB_LINKED_POINTERS) != 0 &&"),
                         {"IDWALK_CB_EMBEDDED_NOT_OWNING", "IDWALK_CB_READFILE_IGNORE"})
        # IDWALK_CB_EMBEDDED never reaches the filter: the callback returns before it.
        self.assertLess(remap.index("if (cb_flag & IDWALK_CB_EMBEDDED) {"),
                        remap.index("ID_REMAP_SKIP_LIB_LINKED_POINTERS"))
        link = function(READFILE, "static int lib_link_cb(")
        self.assertIn("if (cb_data->cb_flag & (IDWALK_CB_EMBEDDED | IDWALK_CB_EMBEDDED_NOT_OWNING)) {", link)
        self.assertIn("if (cb_data->cb_flag & IDWALK_CB_READFILE_IGNORE) {", link)

    def test_native_suite_calls_the_declared_swap_signature(self):
        self.assertIn("void BKE_lib_id_swap_full(\n    Main *bmain, ID *id_a, ID *id_b, const bool do_self_remap, "
                      "const int self_remap_flags);", LIB_ID_HEADER.read_text())
        self.assertEqual(native_constant("SWAP_SYMBOL"), "_ZN7blender20BKE_lib_id_swap_fullEPNS_4MainEPNS_2IDES3_bi")


if __name__ == "__main__":
    unittest.main()
