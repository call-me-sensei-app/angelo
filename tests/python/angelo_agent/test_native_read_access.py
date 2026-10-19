# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read adapter invariants: native copies, missing storage, and computed callbacks never invoked."""

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_native_read_tools import ReaderFixture, ID, GROUP
from read_tools_fakes import Collection, Struct, prop, rna


class SafeAccessTests(ReaderFixture):
    def add_scene_property(self, definition, stored=None, *, present=True):
        scene = self.window.scene
        scene.bl_rna = rna("Scene", [definition], base=scene.bl_rna)
        if present:
            scene.set(definition.identifier, stored)
        return scene

    def test_missing_cycles_group_reads_declared_defaults_without_creating_storage(self):
        scene = self.window.scene
        del scene._values["cycles"]
        definition = scene.bl_rna.properties.get("cycles").fixed_type
        with patch.object(definition.properties.get("samples"), "default", 128):
            result = self.read("scene.cycles.samples")
        self.assertEqual(result["property"]["value"], 128)
        self.assertNotIn("cycles", scene._values)
        self.assertNotIn(("Scene", "cycles"), Struct.reads)

    def test_malformed_cycles_group_is_reported_without_repair(self):
        self.window.scene.set("cycles", 13)
        result = self.read("scene.cycles.samples")
        self.assertEqual(result["code"], "INVALID_STORAGE")
        self.assertEqual(self.window.scene._values["cycles"], 13)

    def test_computed_values_are_explicitly_stored_or_default_and_never_fetched(self):
        definition = prop("computed", "FLOAT", is_runtime=True, has_python_getter=True, default=3.25)
        scene = self.add_scene_property(definition, 1.5)
        stored = self.read("scene.computed")["property"]
        self.assertEqual((stored["value"], stored["value_source"], stored["computed_getter"]), (1.5, "stored", "not_run"))
        del scene._values["computed"]
        default = self.read("scene.computed")["property"]
        self.assertEqual((default["value"], default["value_source"], default["computed_getter"]), (3.25, "default", "not_run"))
        self.assertNotIn(("Scene", "computed"), Struct.reads)

    def test_dynamic_enum_items_are_not_called_or_guessed(self):
        definition = prop("computed_enum", "ENUM", is_runtime=True, has_python_enum_items=True,
                          enum_default=7, enum_items_static=[])
        self.add_scene_property(definition, 7)
        result = self.read("scene.computed_enum")["property"]
        self.assertEqual((result["withheld"], result["value_source"], result["computed_getter"]),
                         ("COMPUTED_VALUE", "stored", "not_run"))
        definition.enum_items_static = [SimpleNamespace(value=7, identifier="SEVEN", name="Seven")]
        self.assertEqual(self.read("scene.computed_enum")["property"]["value"], "SEVEN")
        del self.window.scene._values["computed_enum"]
        result = self.read("scene.computed_enum")["property"]
        self.assertEqual((result["value"], result["value_source"]), ("SEVEN", "default"))
        self.assertNotIn(("Scene", "computed_enum"), Struct.reads)

    def test_secret_definition_stops_before_native_storage_or_getter(self):
        self.add_scene_property(prop("api_token", "STRING", is_runtime=True, has_python_getter=True), "secret")
        result = self.read("scene.api_token")["property"]
        self.assertEqual(result["withheld"], "SECRET")
        self.assertFalse(any(call[0] == "stored" and call[2] == ("api_token",) for call in self.native_read.calls))
        self.assertNotIn(("Scene", "api_token"), Struct.reads)

    def test_native_runtime_property_without_python_getter_keeps_its_native_value(self):
        self.add_scene_property(prop("native_generated", "INT", is_runtime=True), 42)
        result = self.read("scene.native_generated")["property"]
        self.assertEqual(result, {"name": "native_generated", "type": "INT", "value": 42, "readonly": False})
        self.assertIn(("Scene", "native_generated"), Struct.reads)

    def test_stored_collections_remain_paged_and_named_without_materialization(self):
        item_rna = rna("StoredItem", [prop("value", "INT", is_runtime=True, uses_idprop_storage=True)], base=GROUP)
        scene = self.add_scene_property(prop("items", "COLLECTION", is_runtime=True, uses_idprop_storage=True,
                                             fixed_type=item_rna), present=False)
        scene.set("items", Collection([Struct(item_rna, scene, name="First", value=1),
                                       Struct(item_rna, scene, name="Second", value=2)]))
        page = self.read("scene.items", offset=1, limit=1, depth=2)
        self.assertEqual((page["length"], page["items"][0]["name"], page["items"][0]["index"]), (2, "Second", 1))
        self.assertEqual(self.read('scene.items["Second"].value')["property"]["value"], 2)
        self.assertNotIn(("Scene", "items"), Struct.reads)

    def test_matrix_storage_is_reshaped_like_native_rna(self):
        self.add_scene_property(prop("matrix", "FLOAT", is_runtime=True, uses_idprop_storage=True,
                                      array_length=4, array_dimensions=(2, 2, 0)), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(self.read("scene.matrix")["property"]["value"], [[1.0, 2.0], [3.0, 4.0]])

    def test_stored_id_pointers_preserve_null_traversal_and_admission(self):
        cube = self.bpy.data.objects[0]
        definition = prop("target", "POINTER", is_runtime=True, uses_idprop_storage=True,
                          fixed_type=cube.bl_rna)
        group_rna = rna("TargetSettings", [definition], base=GROUP)
        scene = self.add_scene_property(prop("settings", "POINTER", is_runtime=True, uses_idprop_storage=True,
                                             fixed_type=group_rna), present=False)
        group = Struct(group_rna, scene, name="", target=cube)
        scene.set("settings", group)
        self.assertEqual(self.read("scene.settings.target.name")["property"]["value"], "Cube")
        self.assertEqual(self.read("scene.settings")["properties"][1]["value"]["read"], 'data.objects["Cube"]')
        group.set("target", None)
        self.assertIsNone(self.read("scene.settings.target")["property"]["value"])
        group.set("target", self.bpy.data.images[0])
        self.assertEqual(self.read("scene.settings.target")["code"], "INVALID_STORAGE")
        definition.fixed_type = scene.bl_rna
        group.set("target", self.bpy.data.scenes[1])
        self.assertEqual(self.read("scene.settings.target")["code"], "OTHER_SCENE")

    def test_older_binary_refuses_all_live_reads_before_access(self):
        with patch("angelo_agent.data_read_access.native_api", lambda: None):
            from angelo_agent import data_read
            from angelo_agent.read_spec import validate_read
            before = list(Struct.reads)
            for path in ("scene", "scene.cycles", "scene.frame_current", "data.objects[0].name"):
                args = validate_read("angelo_read_data", {"path": path})
                result = data_read._Reader(None, self.window, args, epoch_owner=lambda: "test").read()
                self.assertEqual(result["code"], "NATIVE_READER_REQUIRED")
            self.assertEqual(Struct.reads, before)


class SharedWorkTests(ReaderFixture):
    """Declarations are copied once per declarations key; row values are copied in batches."""

    def described(self):
        copies = []
        describe = self.native_read.describe
        def recording(*args, **keywords):
            result = describe(*args, **keywords)
            if result.get("status") == "OK" and result["kind"] in {"STRUCT", "COLLECTION"}:
                copies.append(result["payload"]["properties"] is not None)
            return result
        return copies, patch.object(self.native_read, "describe", recording)

    def test_held_declarations_are_not_copied_again(self):
        copies, recording = self.described()
        with recording:
            first = self.read("scene", depth=2)
            copied_first = list(copies)
            copies.clear()
            second = self.read("scene", depth=2)
        self.assertTrue(any(copied_first))
        self.assertFalse(any(copies), "a held declaration list was copied again")
        self.assertEqual(first["properties"], second["properties"])

    def test_omitted_declarations_the_reader_does_not_hold_are_refused(self):
        from angelo_agent import data_read_access
        describe = self.native_read.describe
        def omitting(context, address, allowance, member, detail=False, copy=True):
            result = describe(context, address, allowance, member, detail, copy)
            if result.get("status") == "OK" and result["kind"] == "STRUCT":
                result["payload"].update(properties=None, bookkeeping=None, data_collections=None)
            return result
        data_read_access._DECLARATIONS.clear()
        access = data_read_access.Access(self.window, 10 ** 9, lambda: "epoch", backend=self.native_read)
        try:
            with patch.object(self.native_read, "describe", omitting):
                with self.assertRaisesRegex(ValueError, "does not hold"):
                    access.root("scene")
        finally:
            access.close()

    def test_struct_rows_copy_values_in_batches_and_never_batch_withheld_rows(self):
        self.native_read.calls.clear()
        result = self.read("scene.render", limit=64)
        batches = [call for call in self.native_read.calls if call[0] == "fetch_many"]
        self.assertTrue(batches)
        self.assertLess(len(batches), len(result["properties"]))
        batched = {identifier for call in batches for identifier in call[2]}
        # Withheld by its declaration (a file path): never copied. Content screening happens after a copy.
        self.assertEqual(next(row for row in result["properties"] if row["name"] == "filepath")["withheld"], "PATH")
        self.assertNotIn("filepath", batched)
        self.assertFalse([call for call in self.native_read.calls if call[0] == "fetch" and call[1][2][-1:] == (("attribute", "filepath"),)])

    def test_batched_pages_answer_exactly_as_single_operations(self):
        from angelo_agent.data_read_access import Access
        paths = ("scene", "data.objects", "data.objects[0].modifiers", "scene.render")
        batched = [self.read(path, depth=2) for path in paths]
        calls = [call[0] for call in self.native_read.calls]
        self.assertIn("reference_many", calls)
        self.assertIn("fetch_many", calls)
        with patch.object(Access, "prefetch_groups", lambda *args: None), \
                patch.object(Access, "prefetch_references", lambda *args: None):
            single = [self.read(path, depth=2) for path in paths]
        self.assertTrue(any(call[0] == "fetch_many" and call[1] == ("data", (("attribute", "objects"),), ())
                            for call in self.native_read.calls), "item rows' values were not batched")
        for path, one, other in zip(paths, batched, single):
            self.assertEqual(one, other, path)

    def test_float_matrix_rows_match_python_rows(self):
        definition = prop("matrix_probe", "FLOAT", subtype="MATRIX", array_length=16, array_dimensions=(4, 4, 0))
        scene = self.window.scene
        scene.bl_rna = rna("Scene", [definition], base=scene.bl_rna)
        scene.set("matrix_probe", [float(index) for index in range(16)])  # RNA order: column by column.
        value = self.read("scene.matrix_probe")["property"]["value"]
        self.assertEqual(value, [[0., 4., 8., 12.], [1., 5., 9., 13.], [2., 6., 10., 14.], [3., 7., 11., 15.]])


class MeshAndTileTests(ReaderFixture):
    def mesh(self, weights):
        group_rna = rna("VertexGroupElement", [prop("group", "INT"), prop("weight", "FLOAT")], name_property=None)
        vertex_rna = rna("MeshVertex", [prop("index", "INT"), prop("groups", "COLLECTION", fixed_type=group_rna)],
                         name_property=None)
        mesh_rna = rna("Mesh", [prop("vertices", "COLLECTION", fixed_type=vertex_rna)], base=ID)
        mesh = Struct(mesh_rna, name="Mesh", id_type="MESH", stored_weights=weights)
        vertices = [Struct(vertex_rna, mesh, index=index, groups=AssertionError("allocating getter was called"))
                    for index in range(2)]
        mesh.set("vertices", Collection(vertices, named=False))
        self.bpy.data.bl_rna = rna("BlendData", [prop("meshes", "COLLECTION", fixed_type=mesh_rna)],
                                    base=self.bpy.data.bl_rna, name_property=None)
        self.bpy.data.set("meshes", Collection([mesh]))
        return mesh

    def test_groups_copy_only_requested_vertex_page_and_absent_storage_stays_absent(self):
        mesh = self.mesh({1: [(3, .25), (8, .5), (15, .75)]})
        before = copy.deepcopy(mesh._values["stored_weights"])
        empty = self.read("data.meshes[0].vertices[0].groups")
        self.assertEqual((empty["length"], empty["items"]), (0, []))
        page = self.read("data.meshes[0].vertices[1].groups", offset=1, limit=1, depth=2)
        self.assertEqual((page["length"], page["next_offset"]), (3, 2))
        self.assertEqual(page["items"][0]["properties"], [{"name": "group", "type": "INT", "value": 8},
                                                         {"name": "weight", "type": "FLOAT", "value": .5}])
        self.assertEqual(self.read("data.meshes[0].vertices[1].groups[-1].weight")["property"]["value"], .75)
        self.assertEqual(mesh._values["stored_weights"], before)
        self.assertNotIn(("MeshVertex", "groups"), Struct.reads)

    def test_tile_metadata_requires_the_exact_cached_tile_and_preserves_traversal(self):
        image = self.bpy.data.images[0]
        tile_rna = rna("UDIMTile", [prop("number", "INT"), prop("label", "STRING"),
                                   prop("size", "INT", array_length=2, array_dimensions=(2, 0, 0)),
                                   prop("channels", "INT")], name_property="label")
        image.bl_rna = rna("Image", [prop("tiles", "COLLECTION", fixed_type=tile_rna)], base=image.bl_rna)
        tiles = [Struct(tile_rna, image, number=number, label=str(number),
                        size=AssertionError("loading getter"), channels=AssertionError("loading getter"))
                 for number in (1001, 1002)]
        collection = Collection(tiles, named=False, definition=rna("ImageTiles", [
            prop("active", "POINTER", fixed_type=tile_rna)], name_property=None))
        collection.active = tiles[0]
        image.set("tiles", collection)
        image.set("has_data", True)
        image.set("cached_tiles", {(1002, 0, 0): {"size": [32, 64], "channels": 4}})
        page = self.read("data.images[0].tiles", depth=2)
        self.assertEqual(page["length"], 2)
        self.assertEqual(self.read("data.images[0].tiles[0].size")["property"]["withheld"], "LOADS_FILE")
        self.assertEqual(self.read("data.images[0].tiles.active.channels")["property"]["withheld"], "LOADS_FILE")
        self.assertEqual(self.read("data.images[0].tiles[1].size")["property"]["value"], [32, 64])
        self.assertEqual(self.read("data.images[0].tiles[1].channels")["property"]["value"], 4)
        self.assertNotIn(("UDIMTile", "size"), Struct.reads)
        self.assertNotIn(("UDIMTile", "channels"), Struct.reads)

    def test_image_metadata_never_uses_any_tile_cache_as_permission_to_load_default(self):
        image = self.bpy.data.images[0]
        image.set("has_data", True)
        image.set("cached_tiles", {(1002, 0, 0): {"size": [32, 64], "channels": 4}})
        image.set("size", AssertionError("image acquire getter was called"))
        self.assertEqual(self.read("data.images[0].size")["property"]["withheld"], "LOADS_FILE")
        image.set("cached_default", {"size": [8, 16]})
        self.assertEqual(self.read("data.images[0].size")["property"]["value"], [8, 16])
        self.assertNotIn(("Image", "size"), Struct.reads)

    def test_known_movie_duration_does_not_require_a_cached_default_frame(self):
        image = self.bpy.data.images[0]
        image.bl_rna = rna("Image", [prop("frame_duration", "INT")], base=image.bl_rna)
        image.set("cached_default", {"frame_duration": 27})
        image.set("frame_duration", AssertionError("duration acquire getter was called"))
        self.assertEqual(self.read("data.images[0].frame_duration")["property"]["value"], 27)
        self.assertEqual(self.read("data.images[0].size")["property"]["withheld"], "LOADS_FILE")


class DetachedBoundaryTests(ReaderFixture):
    """Consumer negatives against explicitly injected detached contract results."""

    def reader(self, path, *, epoch=None, api=None, **arguments):
        from angelo_agent import data_read
        from angelo_agent.read_spec import validate_read
        args = validate_read("angelo_read_data", dict(path=path, **arguments))
        return data_read._Reader(None, self.window, args, backend=api or self.native_read,
                                 epoch_owner=epoch or (lambda: "a" * 32)).read()

    def test_old_binary_never_touches_window_scene_or_value_constructors(self):
        from angelo_agent import data_read
        from angelo_agent.read_spec import validate_read
        class Window:
            @property
            def scene(self):
                raise AssertionError("Python Window.scene getter was entered")
        with patch("angelo_agent.data_read_access.native_api", lambda: SimpleNamespace(ADDRESS_READ_VERSION=0)):
            result = data_read._Reader(None, Window(), validate_read("angelo_read_data", {"path": "scene"}),
                                      epoch_owner=lambda: "a"*32).read()
        self.assertEqual(result["code"], "NATIVE_READER_REQUIRED")
        self.assertFalse(self.native_read.contexts)

    def test_context_is_closed_after_success_missing_and_refusal(self):
        for path in ("scene.frame_current", "scene.no_such_property", "scene.background_set"):
            self.reader(path)
            self.assertTrue(self.native_read.contexts[-1].closed)

    def test_epoch_change_discards_a_copied_answer(self):
        epoch = ["before"]
        fetch = self.native_read.fetch
        def changed(*args):
            result = fetch(*args)
            epoch[0] = "after"
            return result
        with patch.object(self.native_read, "fetch", changed):
            result = self.reader("scene.frame_current", epoch=lambda: epoch[0])
        self.assertEqual(result["code"], "EXPIRED")
        self.assertNotIn("property", result)
        self.assertTrue(self.native_read.contexts[-1].closed)

    def test_final_lifecycle_check_discards_a_missing_answer(self):
        check = self.native_read.check
        count = [0]
        def closed(context):
            count[0] += 1
            # Root describe, member describe, final publication check.
            return dict(status="EXPIRED", reason="DOCUMENT") if count[0] == 3 else check(context)
        with patch.object(self.native_read, "check", closed):
            result = self.reader("scene.no_such_property")
        self.assertEqual(result["code"], "EXPIRED")

    def test_budget_check_keeps_a_completed_partial_page(self):
        check = self.native_read.check
        def exhausted(context):
            result = check(context)
            if result["status"] == "OK":
                result["budget_exhausted"] = True
            return result
        with patch.object(self.native_read, "check", exhausted):
            page = self.reader("scene.cycles", limit=1)
        self.assertTrue(page["found"])
        self.assertEqual((page["returned"], page["next_offset"]), (1, 1))

    def test_native_budget_before_first_value_is_unavailable(self):
        with patch.object(self.native_read, "fetch", lambda *args: {"status": "BUDGET"}):
            result = self.reader("scene.frame_current")
        self.assertEqual(result["code"], "BUDGET")
        self.assertNotIn("property", result)

    def test_native_budget_after_one_row_keeps_only_completed_rows(self):
        fetch = self.native_read.fetch
        count = [0]
        def bounded(*args):
            count[0] += 1
            return {"status": "BUDGET"} if count[0] > 2 else fetch(*args)
        with patch.object(self.native_read, "fetch", bounded):
            result = self.reader("scene.cycles", limit=3)
        self.assertEqual((result["returned"], result["next_offset"], result["truncated"]), (1, 1, True))

    def test_earlier_missing_native_prefix_overrides_python_walk_position(self):
        fetch = self.native_read.fetch
        def missing(context, address, allowance, expected):
            if (address[1] + address[2])[-1] == ("attribute", "levels"):
                return dict(status="MISSING", kind="NULL", edge=2, length=None, member=None)
            return fetch(context, address, allowance, expected)
        with patch.object(self.native_read, "fetch", missing):
            result = self.reader('data.objects[0].modifiers[0].levels')
        self.assertEqual((result["code"], result["resolved"]), ("NULL", "data.objects[0]"))

    def test_derived_failures_remain_field_local(self):
        fetch = self.native_read.fetch
        def missing(context, address, allowance, expected):
            if address[2] and address[2][-1] == ("attribute", "levels"):
                return dict(status="MISSING", kind="NULL", edge=2, length=None, member=None)
            return fetch(context, address, allowance, expected)
        with patch.object(self.native_read, "fetch", missing):
            result = self.reader('data.objects[0].modifiers', depth=2)
        self.assertTrue(result["found"])
        self.assertEqual(result["items"][0]["properties"][-1]["withheld"], "NULL")

    def test_changed_terminal_projection_retries_once_with_fresh_facts(self):
        definition = self.window.scene.bl_rna.properties.get("frame_current")
        fetch = self.native_read.fetch
        count = [0]
        def changed(*args):
            if not count[0]:
                count[0] += 1
                definition.has_python_getter = True
            return fetch(*args)
        with patch.object(self.native_read, "fetch", changed):
            result = self.reader("scene.frame_current")
        self.assertEqual(result["property"]["value"], 12)
        self.assertEqual(result["property"]["computed_getter"], "not_run")
        del definition.has_python_getter

    def test_repeated_changed_projection_refuses(self):
        fetch = self.native_read.fetch
        def changing(context, address, allowance, expected):
            return dict(status="STALE_ADMISSION", address=address, edge=len(address[1]+address[2])-1)
        with patch.object(self.native_read, "fetch", changing):
            result = self.reader("scene.frame_current")
        self.assertEqual(result["code"], "STALE_ADMISSION")

    def test_changed_text_admission_stops_before_new_fetch(self):
        definition = prop("caption", "STRING")
        self.window.scene.bl_rna = rna("Scene", [definition], base=self.window.scene.bl_rna)
        self.window.scene.set("caption", "hello")
        count = [0]
        def changed(context, address, allowance, expected):
            count[0] += 1
            definition.subtype = "PASSWORD"
            return dict(status="STALE_ADMISSION", address=address, edge=len(address[1]+address[2])-1)
        with patch.object(self.native_read, "fetch", changed):
            result = self.reader("scene.caption")
        self.assertEqual(result["property"]["withheld"], "SECRET")
        self.assertEqual(count[0], 1)

    def test_detached_metadata_does_not_retain_mutable_transport(self):
        from angelo_agent.data_read_access import _property
        context = self.native_read.begin(self.window, 2.0)
        definition = self.window.scene.bl_rna.properties.get("frame_current")
        transport = self.native_read.property(context, self.window.scene, definition)
        copied = _property(transport)
        transport["projection"][1]["name"] = "changed"
        self.assertNotEqual(copied.name, "changed")
        self.assertNotEqual(dict(copied.projection[1])["name"], "changed")
        self.assertEqual(copied.transport(), transport["fingerprint"])
        with self.assertRaises((AttributeError, TypeError)):
            copied.name = "changed"

    def test_protocol_subclasses_and_live_objects_are_rejected(self):
        from angelo_agent.data_read_access import _copy, _address
        class Mapping(dict):
            pass
        for value in (Mapping(status="OK"), SimpleNamespace(), [1, 2]):
            with self.assertRaises(ValueError):
                _copy(value)
        with self.assertRaises(ValueError):
            _address(("scene", (("index", True),), ()))

    def test_full_public_path_has_two_independent_formatter_steps(self):
        leaf = rna("Leaf", [prop("値", "INT")], name_property=None)
        node = Struct(leaf, self.window.scene, **{"値": 23})
        definition = leaf
        for index in range(16, 0, -1):
            definition = rna("Level%d" % index, [prop("child", "POINTER", fixed_type=definition)], name_property=None)
            node = Struct(definition, self.window.scene, child=node)
        self.window.scene.bl_rna = rna("Scene", [prop("child", "POINTER", fixed_type=definition)], base=self.window.scene.bl_rna)
        self.window.scene.set("child", node)
        result = self.reader("scene" + ".child" * 16, depth=2)
        self.assertTrue(result["found"])
        self.assertEqual(result["properties"][0]["properties"][0]["value"], 23)
        self.assertTrue(any(len(call[1][1]) == 16 and len(call[1][2]) == 2
                            for call in self.native_read.calls if call[0] == "fetch"))

    def test_collection_owned_scalar_and_null_slots_keep_existing_answers(self):
        collection = self.bpy.data.objects[0]._values["modifiers"]
        collection.bl_rna = rna("Modifiers", [prop("active_index", "INT")], name_property=None)
        collection.active_index = 1
        scalar = self.reader("data.objects[0].modifiers.active_index")
        self.assertEqual(scalar["property"]["value"], 1)
        collection.items.insert(0, None)
        empty = self.reader("data.objects[0].modifiers[0]")
        self.assertIsNone(empty["property"]["value"])
        stopped = self.reader("data.objects[0].modifiers[0].name")
        self.assertEqual((stopped["code"], stopped["resolved"]), ("NULL", "data.objects[0].modifiers[0]"))
        page = self.reader("data.objects[0].modifiers", limit=1)
        self.assertEqual(page["items"], [{"index": 0, "value": None}])

    def test_key_replay_keeps_original_selector_after_reorder(self):
        fetch = self.native_read.fetch
        collection = self.bpy.data.objects[0]._values["modifiers"]
        changed = [False]
        def reordered(context, address, allowance, expected):
            if not changed[0] and (address[1]+address[2])[-1] == ("attribute", "levels"):
                changed[0] = True
                collection.items.reverse()
            return fetch(context, address, allowance, expected)
        with patch.object(self.native_read, "fetch", reordered):
            result = self.reader('data.objects[0].modifiers["Bevel"].levels')
        self.assertEqual(result["property"]["value"], 2)
        self.assertTrue(any(("key", "Bevel") in call[1][1] for call in self.native_read.calls if call[0] == "fetch"))

    def test_enum_flags_and_filtered_choices_preserve_output(self):
        choices = [SimpleNamespace(identifier="/private/%d" % index, name="Path", value=index) for index in range(80)]
        choices += [SimpleNamespace(identifier="A", name="Alpha", value=1), SimpleNamespace(identifier="B", name="Beta", value=2)]
        definition = prop("flags", "ENUM", is_enum_flag=True, enum_items=choices)
        self.window.scene.bl_rna = rna("Scene", [definition], base=self.window.scene.bl_rna)
        self.window.scene.set("flags", {"B", "A"})
        result = self.reader("scene.flags")["property"]
        self.assertEqual(result["value"], ["A", "B"])
        self.assertEqual(result["choices"], [{"id": "A", "label": "Alpha"}, {"id": "B", "label": "Beta"}])

    def test_dynamic_array_uses_actual_length_and_nonfinite_number_policy(self):
        definition = prop("dynamic", "FLOAT", has_dynamic_length=True)
        self.window.scene.bl_rna = rna("Scene", [definition], base=self.window.scene.bl_rna)
        self.window.scene.set("dynamic", [float("nan")] + [1.0]*99)
        row = self.reader("scene.dynamic")["property"]
        self.assertEqual((row["length"], len(row["value"]), row["value"][0], row["truncated"]), (100, 64, None, True))


class ReviewedP2Tests(ReaderFixture):
    """Independent controls for the four reproduced consumer review defects."""

    def access(self):
        import time
        from angelo_agent.data_read_access import Access
        access = Access(self.window, time.monotonic()+2, lambda: "a"*32, backend=self.native_read)
        self.addCleanup(access.close)
        return access

    def enum(self, access):
        scene = access.root("scene")
        cycles = access.value(scene, access.member(scene, "cycles")["property"], derived=False)
        return cycles, access.member(cycles, "device")["property"]

    def test_changed_enum_flag_is_not_converted_by_original_declaration(self):
        definition = self.window.scene.bl_rna.properties.get("cycles").fixed_type.properties.get("device")
        fetch = self.native_read.fetch
        changed = [False]
        def switch(context, address, allowance, expected):
            if not changed[0] and (address[1] + address[2])[-1] == ("attribute", "device"):
                changed[0] = True
                definition.is_enum_flag = True
            return fetch(context, address, allowance, expected)
        with patch.object(self.native_read, "fetch", switch):
            result = self.read("scene.cycles.device")
        self.assertEqual(result["code"], "STALE_ADMISSION")
        self.assertNotIn("property", result)
        try:
            fresh = self.read("scene.cycles.device")
            self.assertEqual(fresh["property"]["value"], ["GPU"])
        finally:
            del definition.is_enum_flag
        self.assertEqual(self.read("scene.cycles.device")["property"]["value"], "GPU")

    def test_changed_owner_type_cannot_reuse_old_row_policy_names(self):
        cycles = self.window.scene._values["cycles"]
        original = cycles.bl_rna
        fetch = self.native_read.fetch
        def refine(context, address, allowance, expected):
            if (address[1] + address[2])[-1] == ("attribute", "samples"):
                cycles.bl_rna = rna("RefinedSettings", base=original)
            return fetch(context, address, allowance, expected)
        with patch.object(self.native_read, "fetch", refine):
            result = self.read("scene.cycles.samples")
        self.assertEqual(result["code"], "STALE_ADMISSION")
        self.assertEqual(self.read("scene.cycles.samples")["property"]["value"], 256)

    def test_changed_array_dimensions_refuse_then_fresh_read_uses_new_shape(self):
        definition = prop("matrix", "FLOAT", is_runtime=True, uses_idprop_storage=True,
                          array_length=4, array_dimensions=(4, 0, 0))
        self.window.scene.bl_rna = rna("Scene", [definition], base=self.window.scene.bl_rna)
        self.window.scene.set("matrix", [1., 2., 3., 4.])
        fetch = self.native_read.fetch
        def reshape(context, address, allowance, expected):
            definition.array_dimensions = (2, 2, 0)
            return fetch(context, address, allowance, expected)
        with patch.object(self.native_read, "fetch", reshape):
            result = self.read("scene.matrix")
        self.assertEqual(result["code"], "STALE_ADMISSION")
        self.assertEqual(self.read("scene.matrix")["property"]["value"], [[1., 2.], [3., 4.]])

    def test_successful_choice_page_with_more_and_elapsed_deadline_is_truncated(self):
        clock = [100.]
        def page(context, address, allowance, expected, offset=0, limit=64):
            clock[0] = 103.
            return self.native_read.ok("CHOICES", dict(items=(("GPU", "GPU", 1),), length=100,
                                                      offset=offset, more=True))
        with patch("angelo_agent.data_read_access.time.monotonic", lambda: clock[0]), patch.object(self.native_read, "choices", page):
            row = self.read("scene.cycles.device")["property"]
        self.assertEqual(row["value"], "GPU")
        self.assertEqual(row["choices"], [{"id": "GPU", "label": "GPU"}])
        self.assertTrue(row["choices_truncated"])
        self.assertNotIn("choices_partial", row)

    def test_choice_end_of_source_control_is_complete(self):
        access = self.access()
        owner, prop = self.enum(access)
        with patch.object(self.native_read, "choices", lambda *args: self.native_read.ok("CHOICES", dict(
                items=(("GPU", "GPU", 1),), length=1, offset=0, more=False))):
            source, more = access.choices(owner, prop)
        self.assertEqual(source, (("GPU", "GPU", 1),))
        self.assertFalse(more)

    def test_empty_more_page_and_budget_flag_cannot_claim_complete_choices(self):
        for items, exhausted in (((), False), ((("GPU", "GPU", 1),), True)):
            with self.subTest(items=items, exhausted=exhausted):
                access = self.access()
                owner, prop = self.enum(access)
                access.budget_exhausted = exhausted
                with patch.object(self.native_read, "choices", lambda *args: self.native_read.ok("CHOICES", dict(
                        items=items, length=100, offset=0, more=True))):
                    source, more = access.choices(owner, prop)
                self.assertEqual(source, items)
                self.assertTrue(more)

    def test_derived_item_removed_before_reference_is_a_local_row(self):
        reference = self.native_read.reference
        def vanished(context, address, allowance, relation, fields):
            if address[2] and address[2][-1] == ("index", 0):
                return dict(status="MISSING", kind="INDEX_RANGE", edge=len(address[1]), length=0, member=None)
            return reference(context, address, allowance, relation, fields)
        with patch.object(self.native_read, "reference", vanished):
            result = self.read("data.objects[0].modifiers", depth=2)
        self.assertTrue(result["found"])
        self.assertEqual(result["items"][0], {"index": 0, "withheld": "INDEX_RANGE"})
        self.assertEqual(result["items"][1]["name"], "Subdivision")
        self.assertIn("properties", result["items"][1])

    def vanish_after_reference(self):
        """The first item resolves for its reference, then is gone for its nested values."""
        fetch, fetch_many = self.native_read.fetch, self.native_read.fetch_many
        def gone(address):
            steps = address[2]
            return len(steps) >= 1 and steps[0] == ("index", 0)
        def missing(address):
            return dict(status="MISSING", kind="INDEX_RANGE", edge=len(address[1]), length=0, member=None)
        def single(context, address, allowance, expected):
            return missing(address) if gone(address) else fetch(context, address, allowance, expected)
        def batch(context, address, allowance, members):
            return missing(address) if gone(address) else fetch_many(context, address, allowance, members)
        return patch.multiple(self.native_read, fetch=single, fetch_many=batch)

    def test_nested_item_description_disappearance_is_local_and_restores_row_state(self):
        with self.vanish_after_reference():
            result = self.read("data.objects[0].modifiers", depth=2)
        self.assertTrue(result["found"])
        nested = result["items"][0].get("properties", [])
        self.assertTrue(all(row.get("withheld") == "INDEX_RANGE" for row in nested), nested)
        self.assertFalse(any("value" in row for row in nested), nested)
        self.assertEqual(result["items"][1]["name"], "Subdivision")
        self.assertIn("properties", result["items"][1])
        self.assertTrue(any("value" in row for row in result["items"][1]["properties"]))

    def test_missing_nested_item_restores_socket_text_policy_for_following_row(self):
        collection = self.bpy.data.objects[0]._values["modifiers"]
        first, second = collection.items
        first.bl_rna = rna("GeometryNodesInterfaceInputs", [prop("value", "STRING")], name_property=None)
        second.bl_rna = rna("OrdinaryValue", [prop("value", "STRING")], name_property=None)
        first.set("value", "socket text")
        second.set("value", "plain label")
        with self.vanish_after_reference():
            result = self.read("data.objects[0].modifiers", depth=2)
        first_rows = result["items"][0].get("properties", [])
        self.assertTrue(all(row.get("withheld") in {"INDEX_RANGE", "SOCKET_TEXT"} for row in first_rows), first_rows)
        row = next(row for row in result["items"][1]["properties"] if row["name"] == "value")
        self.assertEqual(row["value"], "plain label")
        self.assertNotIn("withheld", row)

    def test_derived_reference_expiry_and_stale_still_discard_whole_answer(self):
        for status in ("EXPIRED", "STALE_ADMISSION"):
            with self.subTest(status=status):
                reference = self.native_read.reference
                def refuse(context, address, allowance, relation, fields):
                    if address[2]:
                        return (dict(status="EXPIRED", reason="SCENE") if status == "EXPIRED" else
                                dict(status="STALE_ADMISSION", address=address, edge=len(address[1]+address[2])))
                    return reference(context, address, allowance, relation, fields)
                with patch.object(self.native_read, "reference", refuse):
                    result = self.read("data.objects[0].modifiers", depth=2)
                self.assertEqual(result["code"], status)
                self.assertNotIn("items", result)

    def test_choices_reject_wrong_exact_primitives_independently(self):
        bad = (("length", "100"), ("length", True), ("length", -1), ("offset", False), ("offset", "0"),
               ("more", 1), ("items", (("GPU", "GPU", True),)), ("items", ((1, "GPU", 1),)),
               ("items", (("GPU", False, 1),)), ("items", (("GPU", "GPU"),)), ("items", ("GPU",)))
        for field, value in bad:
            with self.subTest(field=field, value=value):
                access = self.access()
                owner, prop = self.enum(access)
                payload = dict(items=(("GPU", "GPU", 1),), length=1, offset=0, more=False)
                payload[field] = value
                with patch.object(self.native_read, "choices", lambda *args: self.native_read.ok("CHOICES", payload)):
                    with self.assertRaises(ValueError):
                        access.choices(owner, prop)

    def test_wrong_choices_types_do_not_publish_valid_choice_rows(self):
        payload = dict(items=(("GPU", "GPU", True),), length="100", offset=False, more=1)
        with patch.object(self.native_read, "choices", lambda *args: self.native_read.ok("CHOICES", payload)):
            row = self.read("scene.cycles.device")["property"]
        self.assertEqual(row["unreadable"], "ValueError")
        self.assertNotIn("choices", row)

    def test_sibling_page_member_raw_and_reference_schema_negatives(self):
        from angelo_agent.data_read_access import _raw
        valid = dict(state="ABSENT", type="NONE", subtype=None, length=None, value=None,
                     default=0, default_array=None, enum_default=None, enum_items_static=None)
        for field, value in (("length", True), ("enum_default", True), ("default_array", ("1",)),
                             ("enum_items_static", (("A", "A", True),)), ("value", {"live": 1})):
            with self.subTest(raw=field):
                malformed = dict(valid, **{field: value})
                with self.assertRaises(ValueError):
                    _raw(malformed)
        access = self.access()
        scene = access.root("scene")
        collection = access.value(scene, access.member(scene, "objects")["property"], derived=False)
        for field, value in (("offset", False), ("length", True), ("more", 1)):
            with self.subTest(items=field):
                payload = dict(items=(), length=0, offset=0, more=False)
                payload[field] = value
                with patch.object(self.native_read, "items", lambda *args: self.native_read.ok("ITEMS", payload)):
                    with self.assertRaises(ValueError):
                        access.items(collection, 0, 1)
        describe = self.native_read.describe
        def bad_limits(*args):
            result = describe(*args)
            if result.get("kind") == "MEMBER":
                result["payload"]["numeric_limits"] = dict(hard_min=True, hard_max=100)
            return result
        with patch.object(self.native_read, "describe", bad_limits):
            with self.assertRaises(ValueError):
                access.member(scene, "frame_current", detail=True)
        reference = self.native_read.reference
        def bad_field(*args):
            result = reference(*args)
            if result.get("kind") == "REFERENCE":
                result["payload"]["fields"][0]["value"] = 42
            return result
        with patch.object(self.native_read, "reference", bad_field):
            with self.assertRaises(ValueError):
                access.reference(scene)

    def test_collection_description_and_struct_metadata_types_are_exact(self):
        access = self.access()
        scene = access.root("scene")
        collection = access.value(scene, access.member(scene, "objects")["property"], derived=False)
        describe = self.native_read.describe
        for field, value in (("properties", None), ("collection_type", dict(identifier=True, bases=(), name_property=None))):
            with self.subTest(collection=field):
                def malformed(*args):
                    result = describe(*args)
                    if result.get("kind") == "COLLECTION":
                        result["payload"][field] = value
                    return result
                with patch.object(self.native_read, "describe", malformed):
                    with self.assertRaises(ValueError):
                        access.describe(collection.address)
        for field, value in (("bookkeeping", (True,)), ("data_collections", (("Object", False),))):
            with self.subTest(struct=field):
                def malformed(*args):
                    result = describe(*args)
                    if result.get("kind") == "STRUCT":
                        result["payload"][field] = value
                    return result
                with patch.object(self.native_read, "describe", malformed):
                    with self.assertRaises(ValueError):
                        access.describe(("scene", (), ()))  # root() keeps its first answer per request.


class SharedIdentityTests(unittest.TestCase):
    """Read facts share ordinary identity ordering, replacement and epochs."""

    def setUp(self):
        import test_native_scene_queries
        self.queries = test_native_scene_queries.queries
        self.queries.invalidate()

    def test_integer_bookkeeping_keeps_lru_capacity_and_type_keys(self):
        queries = self.queries
        with patch.object(queries, "MAX_OBJECTS", 1):
            for uid in range(8):
                queries._observe_identity("CachedCustomObject", uid, uid+100)
            queries._observe_identity("CachedCustomObject", 0, 100)
            epoch = queries.document_epoch()
            queries._observe_identity("Object", 0, 900)
        self.assertEqual(queries.document_epoch(), epoch)
        self.assertEqual(len(queries._identities), 8)
        self.assertNotIn(("CachedCustomObject", 1), queries._identities)
        self.assertEqual(list(queries._identities)[-2:], [("CachedCustomObject", 0), ("Object", 0)])

    def test_fresh_read_identity_preserves_class_facts_and_expires_on_replacement(self):
        from angelo_agent.data_read_access import ReadUnavailable, _object_handle_for_read as handle_for_read
        queries = self.queries
        class FreshAccess:
            def __init__(self):
                self.epoch = queries.document_epoch()
                self.pointer = 101
                self.calls = []
            def object_identity(self, node, relation):
                self.calls.append((node, relation))
                return dict(type_name="CachedCustomObject", session_uid=7, pointer=self.pointer, scene_uid=1)
            def assert_epoch(self):
                if self.epoch != queries.document_epoch():
                    raise ReadUnavailable("EXPIRED", "Identity changed")
        access, node = FreshAccess(), object()
        handle = handle_for_read(access, node, "owner")
        self.assertEqual(handle, "scn:%s:7" % access.epoch)
        self.assertEqual(access.calls, [(node, "owner")])
        self.assertIn(("CachedCustomObject", 7), queries._identities)
        self.assertNotIn(("Object", 7), queries._identities)
        self.assertEqual(handle_for_read(access, node), handle)
        access.pointer = 202
        with self.assertRaises(ReadUnavailable):
            handle_for_read(access, node)
        self.assertNotEqual(queries.document_epoch(), access.epoch)
        self.assertEqual(queries._identities[("CachedCustomObject", 7)], 202)

    def test_read_identity_without_current_membership_does_not_mint_handle(self):
        from angelo_agent.data_read_access import _object_handle_for_read as handle_for_read
        queries = self.queries
        class Access:
            def object_identity(self, node, relation):
                return None
            def assert_epoch(self):
                raise AssertionError("No identity was returned")
        self.assertIsNone(handle_for_read(Access(), object()))
        self.assertEqual(list(queries._identities), [])


if __name__ == "__main__":
    unittest.main()
