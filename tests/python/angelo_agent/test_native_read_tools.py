# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""angelo_read_data on RNA stand-ins: resolution, bounds, what is withheld and the answers a wrong path gets.

The compiled-app suite (test_blender_read_tools.py) proves the same contract on
Blender's own RNA; these run without Blender, so CI covers the reader's logic.
"""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import data_read, data_read_policy as policy, data_read_values as values  # noqa: E402
from angelo_agent import native_programs  # noqa: E402,F401  (patched by name below)
from angelo_agent.read_spec import READ_NAMES, READ_TOOLS, validate_read  # noqa: E402
from read_tools_fakes import Collection, Struct, ReadAPI, prop, rna  # noqa: E402

ID = rna("ID", [prop("name", "STRING"), prop("users", "INT"), prop("session_uid", "INT"),
                prop("id_type", "ENUM"), prop("library", "POINTER", fixed_type=rna("Library", base=None))])
GROUP = rna("PropertyGroup", [prop("name", "STRING")])
CYCLES = rna("CyclesRenderSettings", [
    prop("samples", "INT", is_runtime=True, hard_min=1, hard_max=2 ** 24),
    prop("device", "ENUM", is_runtime=True, enum_items_static=[
        SimpleNamespace(identifier="CPU", name="CPU", value=0), SimpleNamespace(identifier="GPU", name="GPU Compute", value=1)]),
    prop("adaptive_threshold", "FLOAT", is_runtime=True, name="Noise Threshold"),
    *[prop("filler_%02d" % index, "INT", is_runtime=True) for index in range(60)]], base=GROUP)
RENDER = rna("RenderSettings", [prop("filepath", "STRING", subtype="FILE_PATH"), prop("resolution_x", "INT"),
                                prop("engine", "ENUM", enum_items=[SimpleNamespace(identifier="CYCLES", name="Cycles")]),
                                prop("stamp_note_text", "STRING"), prop("api_token", "STRING")],
             name_property=None)
MODIFIER = rna("Modifier", [prop("name", "STRING"), prop("type", "ENUM"), prop("levels", "INT")])
LEGACY = prop("use_nodes", "BOOLEAN", is_deprecated=True)
SOCKET = rna("NodeSocketString", [prop("name", "STRING"), prop("identifier", "STRING"), prop("type", "ENUM"),
                                  prop("enabled", "BOOLEAN"), prop("default_value", "STRING")],
             base=rna("NodeSocket"))
DEPSGRAPH = rna("Depsgraph", name_property=None)
TEXT = rna("Text", [prop("filepath", "STRING")], base=ID)
# A packed image file is "named" by its file path: its RNA name property is filepath.
PACKED = rna("ImagePackedFile", [prop("filepath", "STRING", subtype="FILE_PATH"), prop("view", "INT")],
             name_property="filepath")
IMAGE = rna("Image", [prop("size", "INT", array_length=2, array_dimensions=(2, 0, 0)), prop("has_data", "BOOLEAN"),
                      prop("source", "ENUM"), prop("pixels", "FLOAT"), prop("packed_files", "COLLECTION", fixed_type=PACKED),
                      prop("preview_choice", "ENUM", is_runtime=True)], base=ID)
OBJECT = rna("Object", [prop("type", "ENUM"), prop("location", "FLOAT", array_length=3, array_dimensions=(3, 0, 0)),
                        prop("matrix_world", "FLOAT", array_length=16, array_dimensions=(4, 4, 0)),
                        prop("modifiers", "COLLECTION", fixed_type=MODIFIER), prop("script", "POINTER", fixed_type=TEXT),
                        prop("texture", "POINTER", fixed_type=IMAGE), prop("driver_expression", "STRING"),
                        prop("sockets", "COLLECTION", fixed_type=SOCKET), prop("broken", "FLOAT")],
             base=ID, functions=("evaluated_get",))
SCENE = rna("Scene", [prop("cycles", "POINTER", fixed_type=CYCLES, is_runtime=True),
                      prop("render", "POINTER", fixed_type=RENDER), prop("objects", "COLLECTION", fixed_type=OBJECT),
                      prop("frame_current", "INT"), prop("camera", "POINTER", fixed_type=OBJECT),
                      prop("background_set", "POINTER", fixed_type=None), prop("depsgraph", "POINTER", fixed_type=DEPSGRAPH),
                      prop("vertices", "COLLECTION", fixed_type=MODIFIER)],
            base=ID, functions=("statistics",))
SCENE.properties.get("background_set").fixed_type = SCENE
SCENE.properties.get("cycles").uses_idprop_storage = True
for _property in CYCLES.properties:
    if _property.is_runtime:
        _property.uses_idprop_storage = True
DATA = rna("BlendData", [prop("filepath", "STRING", subtype="FILE_PATH"),
                         prop("objects", "COLLECTION", fixed_type=OBJECT), prop("scenes", "COLLECTION", fixed_type=SCENE),
                         prop("texts", "COLLECTION", fixed_type=TEXT), prop("images", "COLLECTION", fixed_type=IMAGE)],
           name_property=None)
HANDLE = "scn:" + "a" * 32 + ":%d"


def document():
    """A small fake document: a bound scene with Cycles settings, one object, and a second scene."""
    scene = Struct(SCENE, name="Scene", users=1, session_uid=1, id_type="SCENE", library=None, frame_current=12)
    other = Struct(SCENE, name="Other", users=0, session_uid=2, id_type="SCENE", library=None, frame_current=1)
    text = Struct(TEXT, name="notes.py", users=1, session_uid=3, id_type="TEXT", library=None, filepath="/Users/a/n.py")
    image = Struct(IMAGE, name="rock.png", users=1, session_uid=4, id_type="IMAGE", library=None, has_data=False,
                   size=(4, 4), source="FILE", pixels=[0.0] * 64, preview_choice="/Users/alice/Client X/previews/a.png")
    packed = Struct(PACKED, image, filepath="/Users/alice/Client X/textures/rock.png", view=0)
    object.__setattr__(packed, "name", packed.filepath)  # What Blender's collection uses as the item's key.
    image.set("packed_files", Collection([packed]))
    cube = Struct(OBJECT, name="Cube", users=1, session_uid=5, id_type="OBJECT", library=None, type="MESH",
                  location=(1.0, 2.0, 3.5), matrix_world=tuple(tuple(float(r == c) for c in range(4)) for r in range(4)),
                  script=text, texture=image, driver_expression="var * 2 + open('/etc/passwd')",
                  broken=RuntimeError("getter failed"))
    cube.set("modifiers", Collection([Struct(MODIFIER, cube, name="Bevel", type="BEVEL", levels=2),
                                      Struct(MODIFIER, cube, name="Subdivision", type="SUBSURF", levels=3)]))
    cube.set("sockets", Collection([Struct(SOCKET, cube, name="Label", identifier="Socket_3", type="STRING",
                                           enabled=True, default_value="/Users/alice/Client X/notes.txt")]))
    filler = {"filler_%02d" % index: index for index in range(60)}
    cycles = Struct(CYCLES, scene, name="", samples=256, device="GPU", adaptive_threshold=0.01, **filler)
    render = Struct(RENDER, scene, filepath="/Users/alice/Client X/renders/", resolution_x=3840, engine="CYCLES",
                    stamp_note_text="C:\\Users\\alice\\out", api_token="sk-ant-0123456789abcdef")
    scene.set("cycles", cycles)
    scene.set("render", render)
    scene.set("objects", Collection([cube]))
    scene.set("camera", None)
    scene.set("background_set", other)
    scene.set("depsgraph", Struct(DEPSGRAPH, scene))
    scene.set("vertices", Collection([Struct(MODIFIER, scene, name="v", type="X", levels=0)], named=False))
    data = Struct(DATA, filepath="/Users/alice/Client X/island.blend", objects=Collection([cube]),
                  scenes=Collection([scene, other]), texts=Collection([text]), images=Collection([image]))
    object.__setattr__(data, "id_data", None)
    bpy = SimpleNamespace(data=data, types=SimpleNamespace(ID=SimpleNamespace(bl_rna=ID)))
    return bpy, SimpleNamespace(scene=scene)


class ReaderFixture(unittest.TestCase):
    def setUp(self):
        self.bpy, self.window = document()
        Struct.reads = []
        patcher = patch("angelo_agent.native_programs.runtime_is_staging", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The real handle helper (data_read_access) runs against this stand-in identity table.
        scene_queries = SimpleNamespace(document_epoch=lambda: "a" * 32, _epoch="a" * 32,
                                        _main_thread=lambda: None, _observe_identity=lambda *facts: None)
        scene_patch = patch.dict(sys.modules, {"angelo_agent.scene_queries": scene_queries})
        scene_patch.start()
        self.addCleanup(scene_patch.stop)
        import angelo_agent
        scene_attribute = patch.object(angelo_agent, "scene_queries", scene_queries, create=True)
        scene_attribute.start()
        self.addCleanup(scene_attribute.stop)
        self.native_read = ReadAPI(self.bpy)
        # Parsed declarations are shared between reads; each test starts from an empty cache.
        from angelo_agent import data_read_access
        from angelo_agent import data_read_rows
        for cache in (data_read_access._DECLARATIONS, data_read_access._REFERENCE_FIELDS, data_read_rows._LISTINGS):
            cache.clear()
            self.addCleanup(cache.clear)
        helper = patch("angelo_agent.data_read_access.native_api", lambda: self.native_read)
        helper.start()
        self.addCleanup(helper.stop)

    def read(self, path, **arguments):
        args = validate_read("angelo_read_data", dict(path=path, **arguments))
        result = data_read._Reader(None, self.window, args, backend=self.native_read, epoch_owner=lambda: "a" * 32).read()
        json.dumps(result, allow_nan=False)  # Every answer is finite JSON.
        return result

    def detached(self, value):
        from angelo_agent.data_read_access import _node
        context = self.native_read.begin(self.window, 2.0)
        return _node(self.native_read.node(context, value, ("scene", (), ())))

    def row(self, result, name):
        return next(row for row in result["properties"] if row["name"] == name)


class ResolutionTests(ReaderFixture):
    def test_a_struct_lists_names_types_and_current_values(self):
        result = self.read("scene.cycles")
        self.assertEqual((result["found"], result["kind"], result["struct"]), (True, "STRUCT", "CyclesRenderSettings"))
        self.assertEqual(self.row(result, "samples"), {"name": "samples", "type": "INT", "value": 256})
        self.assertEqual(self.row(result, "device")["value"], "GPU")
        # An add-on property's label is shown when it says more than its identifier.
        self.assertEqual(self.row(result, "adaptive_threshold")["label"], "Noise Threshold")
        self.assertNotIn("label", self.row(result, "samples"))
        self.assertEqual(result["owner"]["name"], "Scene")

    def test_one_property_adds_choices_range_and_label(self):
        device = self.read("scene.cycles.device")["property"]
        self.assertEqual(device["choices"], [{"id": "CPU", "label": "CPU"}, {"id": "GPU", "label": "GPU Compute"}])
        samples = self.read("bpy.context.scene.cycles.samples")["property"]
        self.assertEqual((samples["value"], samples["min"], samples["max"], samples["readonly"]), (256, 1, 2 ** 24, False))

    def test_collections_page_by_index_and_name(self):
        result = self.read('scene.objects["Cube"].modifiers')
        self.assertEqual((result["kind"], result["length"], result["struct"]), ("COLLECTION", 2, "Modifier"))
        self.assertEqual(result["items"], [{"index": 0, "struct": "Modifier", "name": "Bevel", "type": "BEVEL"},
                                           {"index": 1, "struct": "Modifier", "name": "Subdivision", "type": "SUBSURF"}])
        self.assertEqual(self.read('data.objects[0].modifiers[-1].levels')["property"]["value"], 3)
        page = self.read('scene.objects["Cube"].modifiers', offset=1, limit=1)
        self.assertEqual(([row["name"] for row in page["items"]], page["next_offset"], page["truncated"]),
                         (["Subdivision"], None, False))

    def test_depth_two_lists_each_rows_own_values(self):
        result = self.read('scene.objects["Cube"].modifiers', depth=2)
        self.assertEqual([row["name"] for row in result["items"][0]["properties"]], ["name", "type", "levels"])
        self.assertEqual(result["items"][1]["properties"][2], {"name": "levels", "type": "INT", "value": 3})

    def test_arrays_and_their_elements(self):
        location = self.read('scene.objects["Cube"].location')["property"]
        self.assertEqual((location["value"], location["length"]), ([1.0, 2.0, 3.5], 3))
        self.assertEqual(self.read('scene.objects["Cube"].location[2]')["property"],
                         {"name": "location", "type": "FLOAT", "index": 2, "value": 3.5})
        matrix = self.read('scene.objects["Cube"].matrix_world')["property"]["value"]
        self.assertEqual((len(matrix), matrix[1]), (4, [0.0, 1.0, 0.0, 0.0]))
        self.assertEqual(self.read('scene.objects["Cube"].location[7]')["code"], "INDEX_RANGE")

    def test_object_references_carry_a_path_and_a_handle(self):
        row = self.read("data.objects")["items"][0]
        self.assertEqual(row, {"index": 0, "struct": "Object", "name": "Cube", "type": "MESH", "handle": HANDLE % 5})
        self.assertEqual(self.read('scene.objects["Cube"].modifiers[0]')["owner"]["read"], 'data.objects["Cube"]')

    def test_a_datablock_lists_its_own_settings_before_bookkeeping(self):
        names = [row["name"] for row in self.read('scene.objects["Cube"]')["properties"]]
        self.assertEqual(names[:3], ["name", "type", "location"])
        self.assertEqual(names[-4:], ["users", "session_uid", "id_type", "library"])

    def test_a_null_pointer_is_a_value_and_a_stop(self):
        self.assertEqual(self.read("scene.camera")["property"]["value"], None)
        stopped = self.read("scene.camera.location")
        self.assertEqual((stopped["found"], stopped["code"], stopped["resolved"]), (False, "NULL", "scene.camera"))

    def test_a_deprecated_property_says_so(self):
        self.window.scene.bl_rna = rna("Scene", [LEGACY], base=self.window.scene.bl_rna)
        self.window.scene.set("use_nodes", True)
        row = self.row(self.read("scene"), "use_nodes")
        self.assertEqual(row, {"name": "use_nodes", "type": "BOOLEAN", "deprecated": True, "value": True})

    def test_a_failing_getter_costs_one_row(self):
        result = self.read('scene.objects["Cube"]')
        self.assertEqual(self.row(result, "broken"), {"name": "broken", "type": "FLOAT", "unreadable": "RuntimeError"})
        self.assertEqual(self.row(result, "type")["value"], "MESH")


class WrongPathTests(ReaderFixture):
    def test_an_unknown_property_names_the_valid_ones(self):
        result = self.read("scene.cycles.feature_set")
        self.assertEqual((result["found"], result["code"], result["resolved"]), (False, "UNKNOWN_MEMBER", "scene.cycles"))
        self.assertIn("samples", result["members"])
        self.assertEqual(result["members_total"], 64)
        self.assertEqual(self.read("scene.cycles.sample")["similar"], ["samples"])

    def test_names_indexes_and_shapes(self):
        missing = self.read('scene.objects["Sphere"]')
        self.assertEqual((missing["code"], missing["names"], missing["length"]), ("UNKNOWN_NAME", ["Cube"], 1))
        self.assertEqual(self.read('scene.objects["Cub"]')["similar"], ["Cube"])
        self.assertEqual(self.read("scene.objects[4]")["code"], "INDEX_RANGE")
        self.assertEqual(self.read('scene.vertices["v"]')["code"], "NO_NAMES")
        self.assertEqual(self.read("scene[0]")["code"], "NOT_A_COLLECTION")
        self.assertEqual(self.read("scene.frame_current.real")["code"], "NOT_A_STRUCT")
        self.assertEqual(self.read("scene.objects.cycles")["code"], "UNKNOWN_MEMBER")

    def test_functions_are_named_never_called(self):
        result = self.read("scene.statistics")
        self.assertEqual((result["found"], result["code"]), (False, "FUNCTION"))
        self.assertEqual(self.read('scene.objects["Cube"].evaluated_get')["code"], "FUNCTION")

    def test_a_path_outside_the_grammar_is_an_answer_with_its_position(self):
        for path, code in (("scene.objects.get('Cube')", "CALL"), ("__import__('os')", "ROOT"),
                           ("scene.objects[0:2]", "SYNTAX"), ("scene.__class__", "PRIVATE_NAME")):
            with self.subTest(path=path):
                result = self.read(path)
                self.assertEqual((result["found"], result["code"]), (False, code))
                self.assertIn("position", result)
        self.assertEqual(Struct.reads, [], "Nothing is read for a path that does not parse")

    def test_while_a_stage_is_resident_nothing_is_read(self):
        with patch("angelo_agent.native_programs.runtime_is_staging", lambda: True):
            result = self.read("scene.cycles")
        self.assertEqual((result["found"], result["code"]), (False, "STAGING"))
        self.assertEqual(Struct.reads, [])


class WithheldTests(ReaderFixture):
    def test_paths_secrets_scripts_and_socket_text_never_leave(self):
        render = self.read("scene.render")
        self.assertEqual(self.row(render, "filepath"), {"name": "filepath", "type": "STRING", "withheld": "PATH"})
        self.assertEqual(self.row(render, "stamp_note_text")["withheld"], "PATH")  # Looks like a path.
        self.assertEqual(self.row(render, "api_token")["withheld"], "SECRET")
        cube = self.read('scene.objects["Cube"]')
        self.assertEqual(self.row(cube, "driver_expression")["withheld"], "SCRIPT")
        socket = self.read('scene.objects["Cube"].sockets[0]')
        self.assertEqual(self.row(socket, "default_value")["withheld"], "SOCKET_TEXT")
        self.assertEqual(self.row(socket, "identifier")["value"], "Socket_3")
        self.assertEqual(self.read("scene.render.filepath")["property"]["withheld"], "PATH")
        everything = json.dumps([render, cube, socket, self.read("data"), self.read('scene.objects["Cube"].sockets', depth=2)])
        for secret in ("alice", "Client X", "/Users", "sk-ant", "passwd", "C:\\\\"):
            self.assertNotIn(secret, everything)

    def test_a_withheld_value_is_never_fetched(self):
        self.read("scene.render")
        self.read('scene.objects["Cube"].sockets', depth=2)
        self.read("data")
        fetched = {name for _struct, name in Struct.reads}
        self.assertFalse(fetched & {"filepath", "api_token", "default_value", "pixels"})
        self.assertIn("stamp_note_text", fetched, "Ordinary text is read, then screened")

    def test_interface_script_library_and_evaluated_data_are_out_of_reach(self):
        for path, code in (("data.scenes", "DENIED"), ("data.texts", "DENIED"), ("scene.background_set", "OTHER_SCENE"),
                           ("scene.background_set.frame_current", "OTHER_SCENE"), ("scene.depsgraph", "DENIED"),
                           ('scene.objects["Cube"].script', "DENIED"), ("scene.rna_type", "DENIED")):
            with self.subTest(path=path):
                result = self.read(path)
                self.assertEqual((result["found"], result["code"]), (False, code))
        self.assertNotIn(("Scene", "depsgraph"), Struct.reads, "A dependency graph pointer is not even fetched")
        scene = self.read("scene")
        self.assertEqual(self.row(scene, "background_set")["withheld"], "OTHER_SCENE")
        self.assertEqual(self.row(scene, "depsgraph")["withheld"], "DENIED")
        listing = self.read("data")
        self.assertEqual(self.row(listing, "scenes"), {"name": "scenes", "type": "COLLECTION", "withheld": "DENIED"})

    def test_a_label_that_is_a_path_and_a_choice_that_is_a_path_are_withheld(self):
        """Found by the compiled-app sweep: a packed file's RNA name property is its file path."""
        rows = self.read("data.images[0].packed_files")
        self.assertEqual(rows["items"], [{"index": 0, "struct": "ImagePackedFile"}])
        missing = self.read('data.images[0].packed_files["nope"]')
        self.assertEqual((missing["code"], missing["names"], missing["similar"]), ("UNKNOWN_NAME", [], []))
        entry = self.read("data.images[0].packed_files[0]")
        self.assertEqual(self.row(entry, "filepath")["withheld"], "PATH")
        # An add-on's preview enum whose identifiers are file paths.
        self.assertEqual(self.row(self.read("data.images[0]"), "preview_choice")["withheld"], "PATH")
        everything = json.dumps([rows, missing, entry, self.read("data.images[0]"), self.read("data.images", depth=2)])
        for secret in ("alice", "Client X", "/Users"):
            self.assertNotIn(secret, everything)

    def test_an_unloaded_image_is_not_loaded_by_a_read(self):
        image = self.read("data.images[0]")
        self.assertEqual(self.row(image, "size")["withheld"], "LOADS_FILE")
        self.assertEqual(self.row(image, "pixels")["withheld"], "LOADS_FILE")
        self.assertEqual(self.row(image, "source")["value"], "FILE")
        self.assertNotIn(("Image", "size"), Struct.reads)
        self.bpy.data.images[0].set("has_data", True)
        self.bpy.data.images[0].set("cached_default", {"size": [4, 4]})
        self.assertEqual(self.row(self.read("data.images[0]"), "size")["value"], [4, 4])
        self.assertEqual(self.row(self.read("data.images[0]"), "pixels")["withheld"], "LOADS_FILE")


class BoundsTests(ReaderFixture):
    def test_rows_are_paged_and_say_where_to_continue(self):
        first = self.read("scene.cycles")
        self.assertEqual((first["total"], first["returned"], first["next_offset"], first["truncated"]), (64, 32, 32, True))
        self.assertIn("offset 32", first["note"])
        last = self.read("scene.cycles", offset=32, limit=64)
        self.assertEqual((last["returned"], last["next_offset"], last["truncated"]), (32, None, False))
        self.assertNotIn("note", last)
        self.assertEqual([row["name"] for row in first["properties"]] + [row["name"] for row in last["properties"]],
                         [definition.identifier for definition in CYCLES.properties])

    def test_the_character_budget_ends_a_page_early(self):
        with patch.object(data_read, "MAX_CHARS", 400):
            page = self.read("scene.cycles", limit=64)
        self.assertLess(page["returned"], 16)
        self.assertEqual((page["truncated"], page["next_offset"]), (True, page["returned"]))
        self.assertLessEqual(len(json.dumps(page["properties"], separators=(",", ":"))), 400 + 64)

    def test_the_deadline_ends_a_page_early_but_returns_a_row(self):
        with patch.object(data_read, "DEADLINE_SECONDS", -1.0):
            page = self.read("scene.cycles")
        self.assertEqual((page["returned"], page["truncated"], page["next_offset"]), (1, True, 1))

    def test_long_text_and_long_arrays_are_cut(self):
        self.window.scene.cycles.set("name", "x" * 1000)
        self.assertEqual(len(self.row(self.read("scene.cycles"), "name")["value"]), values.MAX_TEXT)
        long_array = prop("weights", "FLOAT", array_length=500, array_dimensions=(500, 0, 0))
        self.assertEqual(values.array([0.5] * 500, long_array), {"value": [0.5] * values.MAX_ELEMENTS, "length": 500,
                                                                  "truncated": True})
        self.assertIsNone(values.number(float("nan")))


class PolicyTests(ReaderFixture):
    def test_structs_without_a_document_owner_are_refused(self):
        unowned = Struct(RENDER)
        object.__setattr__(unowned, "id_data", None)
        self.assertEqual(policy.refusal(self.detached(unowned), None)[0], "OUT_OF_DOCUMENT")
        self.assertEqual(policy.refusal(SimpleNamespace(), None)[0], "NOT_DATA")

    def test_evaluated_interface_and_file_backed_data(self):
        scene = self.window.scene
        evaluated = Struct(OBJECT, id_type="OBJECT", is_evaluated=True)
        self.assertEqual(policy.refusal(self.detached(evaluated), scene)[0], "DENIED")
        for kind in policy.DENIED_ID_TYPES:
            self.assertEqual(policy.refusal(self.detached(Struct(ID, id_type=kind)), scene)[0], "DENIED", kind)
        clip = Struct(ID, id_type="MOVIECLIP")
        self.assertIsNone(policy.refusal(self.detached(clip), scene))
        self.assertTrue(policy.identity_only(self.detached(clip)))
        self.assertEqual(policy.refusal(self.detached(Struct(MODIFIER, clip)), scene)[0], "DENIED")
        self.assertIsNone(policy.refusal(self.detached(scene), scene))

    def test_text_rules_use_the_definition_before_the_value(self):
        names = ["Anything"]
        for identifier, subtype, expected in (
                ("filepath", "NONE", values.PATH), ("cache_directory", "NONE", values.PATH), ("anything", "DIR_PATH", values.PATH),
                ("file_name", "NONE", values.PATH), ("object_path", "NONE", values.PATH), ("remote_url", "NONE", values.PATH),
                ("data_path", "NONE", None), ("rna_path", "NONE", None), ("file_extension", "NONE", None),
                ("password", "NONE", values.SECRET), ("anything", "PASSWORD", values.SECRET), ("api_key", "NONE", values.SECRET),
                ("expression", "NONE", values.SCRIPT), ("bytecode", "NONE", values.SCRIPT), ("buffer", "BYTE_STRING", values.BYTES),
                ("body", "NONE", None), ("uv_map", "NONE", None)):
            with self.subTest(identifier=identifier, subtype=subtype):
                self.assertEqual(values.text_reason(names, prop(identifier, "STRING", subtype=subtype)), expected)
        self.assertEqual(values.text_reason(["NodeTreeInterfaceSocketString", "NodeTreeInterfaceSocket"],
                                            prop("default_value", "STRING")), values.SOCKET_TEXT)
        self.assertEqual(values.text_reason(["Socket_3", "PropertyGroup"], prop("value", "STRING"), socket_inputs=True),
                         values.SOCKET_TEXT)
        self.assertIsNone(values.text_reason(["Socket_3", "PropertyGroup"], prop("value", "STRING")))
        self.assertEqual(values.text_reason(["FunctionNodeInputString", "FunctionNode", "Node"], prop("string", "STRING")),
                         values.SOCKET_TEXT)
        self.assertIsNone(values.text_reason(["ShaderNodeAttribute", "Node"], prop("string", "STRING")))

    def test_text_that_looks_like_a_path_or_a_secret_is_withheld(self):
        for text in ("/Users/alice/tex.png", "//textures/wood.png", "textures/wood.png", "C:\\work\\a.png", "c:/work",
                     "~/Desktop", "\\\\server\\share", "https://example.com/a", "..", "Bearer abcdef123456",
                     "password=hunter2", "sk-ant-0123456789abcdef"):
            with self.subTest(text=text):
                self.assertIsNone(values.screened_text(text)[0])
        for text in ("Cube.001", "UVMap", "location", 'pose.bones["Arm"].rotation_euler', "wood_diffuse.png", "Col", ""):
            with self.subTest(text=text):
                self.assertEqual(values.screened_text(text), (text, None))
        # Names are labels, returned as every other scene read returns them.
        self.assertEqual(values.screened_text("Wood/Oak", is_name=True), ("Wood/Oak", None))


class SpecTests(unittest.TestCase):
    def test_both_tools_are_read_only_closed_and_bounded(self):
        self.assertEqual(READ_NAMES, {"angelo_read_data", "angelo_blend_file"})
        for tool in READ_TOOLS:
            self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertLessEqual(len(tool["description"]), 640, "Keep descriptions short")
            self.assertIn("64 rows", tool["description"], "Code-mode declarations show no bounds")

    def test_defaults_and_bounds(self):
        self.assertEqual(validate_read("angelo_read_data", {"path": "scene"}),
                         {"path": "scene", "offset": 0, "limit": 32, "depth": 1})
        self.assertEqual(validate_read("angelo_blend_file", {}), {"file": "", "offset": 0, "limit": 32})
        self.assertEqual(validate_read("angelo_blend_file", {"file": " a.blend ", "list": "meshes", "limit": 64})["file"],
                         "a.blend")
        for name, arguments in (("angelo_read_data", {}), ("angelo_read_data", {"path": ""}),
                                ("angelo_read_data", {"path": "scene", "limit": 65}),
                                ("angelo_read_data", {"path": "scene", "limit": 0}),
                                ("angelo_read_data", {"path": "scene", "offset": -1}),
                                ("angelo_read_data", {"path": "scene", "depth": 3}),
                                ("angelo_read_data", {"path": "scene", "depth": True}),
                                ("angelo_read_data", {"path": "scene", "eval": "1"}),
                                ("angelo_read_data", {"path": "x" * 513}), ("angelo_read_data", {"path": "a\nb"}),
                                ("angelo_read_data", {"path": ["scene"]}),
                                ("angelo_blend_file", {"file": 3}), ("angelo_blend_file", {"file": "a\x00.blend"}),
                                ("angelo_blend_file", {"list": ""}), ("angelo_blend_file", {"list": "x" * 33}),
                                ("angelo_blend_file", {"limit": 1.5}), ("angelo_other", {})):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValueError):
                validate_read(name, arguments)
        # The grammar answers when the path is read, so a wrong path costs a read and not a rejected plan.
        self.assertEqual(validate_read("angelo_read_data", {"path": "scene.objects.get('x')"})["path"],
                         "scene.objects.get('x')")


class RowSizeTests(ReaderFixture):
    """A scalar row counts its JSON characters as it is built; the count is what ``encoded`` says."""

    def test_counted_sizes_match_the_encoded_rows(self):
        from angelo_agent import data_read_rows
        original, counted = data_read_rows.Rows._scalar_row, []

        def checked(rows, owner, prop):
            result = original(rows, owner, prop)
            if result is not None:
                counted.append(result)
            return result

        with patch.object(data_read_rows.Rows, "_scalar_row", checked):
            for path in ("scene", "scene.cycles", 'scene.objects["Cube"]', 'scene.objects["Cube"].modifiers'):
                self.read(path, depth=2)
        self.assertTrue(counted)
        for row, size in counted:
            self.assertEqual(size, data_read_rows.encoded(row), row)

    def test_text_and_number_sizes(self):
        from angelo_agent.data_read_rows import _number_size, _text_size, encoded
        for text in ("Cube", "", 'a"b', "back\\slash", "line\nbreak", "tab\t", "\x1f", "é ü", "\u2028"):
            self.assertEqual(_text_size(text), encoded(text), repr(text))
        for number in (None, True, False, 0, -7, 2 ** 40, 0.1, 1.5, -0.0, 1e-07, 1e22, 3.000001):
            self.assertEqual(_number_size(number), encoded(number), repr(number))


class TrustedModule:
    """The same answers when the backend is the native module the add-on imported: its records
    are then used as built (no detaching copy, no shape checks on OK results)."""

    def setUp(self):
        super().setUp()
        from angelo_agent import data_read_access
        calls = {name: getattr(self.native_read, name) for name in data_read_access._METHODS}
        for name, value in (("_NATIVE", self.native_read), ("_NATIVE_USABLE", True), ("_NATIVE_CALLS", calls)):
            patcher = patch.object(data_read_access, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read(self, path, **arguments):
        result = super().read(path, **arguments)
        self.assertTrue(data_read_access_trusted(self.native_read))
        return result

    def test_a_later_reassignment_never_feeds_the_trusted_path(self):
        # A reassigned module function would hand back a live object; the trusted path calls only
        # the functions captured at import, so the read answers as before.
        expected = self.read("scene.cycles")
        live = object()
        for name in ("describe", "fetch", "fetch_many", "reference", "reference_many"):
            patcher = patch.object(self.native_read, name, lambda *args, live=live: live)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(self.read("scene.cycles"), expected)


def data_read_access_trusted(backend):
    from angelo_agent import data_read_access
    return data_read_access._NATIVE is backend


class TrustedResolutionTests(TrustedModule, ResolutionTests):
    pass


class TrustedWrongPathTests(TrustedModule, WrongPathTests):
    pass


class TrustedWithheldTests(TrustedModule, WithheldTests):
    pass


class TrustedBoundsTests(TrustedModule, BoundsTests):
    pass


class TrustedPolicyTests(TrustedModule, PolicyTests):
    pass


if __name__ == "__main__":
    unittest.main()
