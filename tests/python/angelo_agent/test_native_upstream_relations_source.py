# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source facts the upstream query (``ViewLayer.angelo_upstream``, ``DEG_angelo_upstream.hh``) relies on.

The behaviour is proven by the GTest ``depsgraph_angelo_upstream_test.cc`` on graphs Blender builds. These pins catch
an upstream or local change that would silently change what the records mean:

- the query only looks graphs up: it never calls what creates, builds, rebuilds, tags, flushes, evaluates or
  activates one, and never reaches an ID through a graph node;
- every driver variable RNA path the builder resolves is recorded, and the walk resolves each one it reaches again
  (a write that re-points data the path passes through without tagging the relations must not read as current);
- the one cross-ID visibility relation Blender builds is a custom bone shape's (the GTest checks on every graph it
  builds that a relation leaving a visibility component enters another one);
- the node-type values records carry are the order of ``enum class NodeType``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPSGRAPH = ROOT / "source/blender/depsgraph"

# The values records carry in their third field. A change here is a change of the record format.
NODE_TYPES = (
    "UNDEFINED", "OPERATION", "TIMESOURCE", "ID_REF", "PARAMETERS", "ANIMATION", "TRANSFORM", "GEOMETRY",
    "SEQUENCER", "COMPOSITOR", "LAYER_COLLECTIONS", "COPY_ON_EVAL", "OBJECT_FROM_LAYER", "HIERARCHY", "AUDIO",
    "ARMATURE", "GENERIC_DATABLOCK", "SCENE", "VISIBILITY", "EVAL_POSE", "BONE", "PARTICLE_SYSTEM",
    "PARTICLE_SETTINGS", "SHADING", "POINT_CACHE", "IMAGE_ANIMATION", "CACHE", "BATCH_CACHE", "INSTANCING",
    "SYNCHRONIZATION", "NTREE_OUTPUT", "NTREE_GEOMETRY_PREPROCESS", "NUM_TYPES",
)


# Calls that create, build, rebuild, tag, flush, evaluate or activate a graph, and member reads that reach an ID.
FORBIDDEN = re.compile(
    r"\b(?:BKE_scene_ensure_depsgraph|BKE_scene_graph_\w+|DEG_graph_build\w*|DEG_graph_relations_update"
    r"|DEG_\w*tag\w*|DEG_relations_tag_update|DEG_evaluate\w*|DEG_make_active|deg_graph_flush\w*"
    r"|deg_evaluate\w*|graph_id_tag_update\w*|add_new_relation|add_relation|clear_all_nodes)\s*\("
    r"|(?:->|\.)(?:id_orig|id_cow)\b")


def _function_body(text, signature):
    start = text.index(signature)
    body = text.index("{", start)
    depth = 0
    for index in range(body, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[body:index + 1]
    raise AssertionError("unterminated function: " + signature)


def _code(text):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


class UpstreamQuerySourceTests(unittest.TestCase):
    def test_the_query_only_looks_graphs_up(self):
        source = (DEPSGRAPH / "intern/depsgraph_query_foreach.cc").read_text(encoding="utf-8")
        for signature in ("DEGUpstreamStatus DEG_angelo_foreach_upstream(",
                          "DEGUpstreamStatus deg_foreach_ancestor_component(",
                          "static bool angelo_path_reads_current("):
            body = _code(_function_body(source, signature))
            self.assertEqual(FORBIDDEN.findall(body), [], signature)
        entry = _code(_function_body(source, "DEGUpstreamStatus DEG_angelo_foreach_upstream("))
        self.assertIn("BKE_scene_get_depsgraph", entry)
        self.assertIn("need_update_relations", entry)
        self.assertIn("is_evaluating", entry)
        self.assertIn("BLI_thread_is_main", entry)
        # Bakes evaluate this graph on job threads: the scene-owned job registry and the interface lock.
        self.assertIn("WM_jobs_test(&wm, scene, WM_JOB_TYPE_ANY)", entry)
        self.assertIn("is_interface_locked", entry)

    def test_every_resolved_driver_path_is_recorded_and_checked_again(self):
        builder = _code((DEPSGRAPH / "intern/builder/deg_builder_relations.cc").read_text(encoding="utf-8"))
        body = _function_body(builder, "void DepsgraphRelationBuilder::build_driver_rna_path_variable(")
        record = body.index("record_path_read(driver_key, target_prop, rna_path, variable_exit_key.ptr)")
        # Recorded as soon as the path resolves: before any early return that adds no relation.
        self.assertLess(body.index("RNA_pointer_is_null"), record)
        self.assertLess(record, body.index("is_same_bone_dependency"))
        walk = _code((DEPSGRAPH / "intern/depsgraph_query_foreach.cc").read_text(encoding="utf-8"))
        loop = _function_body(walk, "DEGUpstreamStatus deg_foreach_ancestor_component(")
        self.assertIn("angelo_path_reads_current(graph, *op_node)", loop)
        check = _function_body(walk, "static bool angelo_path_reads_current(")
        for fact in ("RNA_path_resolve_full", "resolved.owner_id != read.owner", "resolved.type != read.type",
                     "find_id_node(read.root.owner_id)"):
            self.assertIn(fact, check)

    def test_the_only_cross_id_visibility_relation_is_a_custom_shape(self):
        calls = []
        for path in sorted((DEPSGRAPH / "intern/builder").glob("*.cc")):
            for line in _code(path.read_text(encoding="utf-8")).splitlines():
                if re.search(r"\badd_visibility_relation\s*\(", line) and "::add_visibility_relation" not in line:
                    calls.append((path.name, line.strip()))
        self.assertEqual([name for name, _ in calls], ["deg_builder_relations_rig.cc"], calls)
        self.assertIn("custom", calls[0][1])

    def test_record_node_types_are_the_enum_order(self):
        header = (DEPSGRAPH / "intern/node/deg_node.hh").read_text(encoding="utf-8")
        body = _code(header[header.index("enum class NodeType"):])
        body = body[body.index("{") + 1:body.index("};")]
        names = tuple(re.findall(r"^\s*([A-Z_]+)(?:\s*=\s*\d+)?\s*,", body, flags=re.M))
        self.assertEqual(names, NODE_TYPES)


if __name__ == "__main__":
    unittest.main()
