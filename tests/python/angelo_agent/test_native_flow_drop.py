# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Images dropped into a node editor land where they are dropped, in one undo step (1.0.5 drop targets).

``NODE_OT_angelo_drop_assets`` (C++, ``space_node/node_add.cc``) takes a drop of one image, a selection (an Asset
Browser or Asset Shelf selection, the Outliner's selected images) or image files. In a Generation Flow it hands
``angelo.flow_drop_items`` (``flow_drop``) one line per item and that places one Image step per image at the drop
point; a 3D model is refused with a reason. In any other node tree it adds one image node per image. Here the real
``flow_drop`` runs with stand-ins for ``bpy``, the library and the tree, and the C++ side is checked at the source
for the contract the two share; ``test_blender_node_drop_targets`` drags in the running app.
"""

import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
NODE_ADD = ROOT / "source/blender/editors/space_node/node_add.cc"
SPACE_NODE = ROOT / "source/blender/editors/space_node/space_node.cc"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def _bpy_modules():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    identity = lambda text, context=None: text  # noqa: E731
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=identity, pgettext_tip=identity,
                                                           pgettext_rpt=identity, pgettext=identity, locale="en_US"),
                              handlers=handlers, background=True)
    bpy.path = SimpleNamespace(abspath=lambda path, library=None: path)
    bpy.utils = types.ModuleType("bpy.utils")
    bpy.utils.register_class = bpy.utils.unregister_class = lambda cls: None
    previews = types.ModuleType("bpy.utils.previews")
    previews.new = lambda: {}
    bpy.utils.previews = previews
    bpy.data = SimpleNamespace(node_groups=[], images=[])
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers, "bpy.utils": bpy.utils, "bpy.utils.previews": previews}


MODULES = _bpy_modules()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import flow_drop, flow_nodes  # noqa: E402


class Nodes(list):
    def __init__(self):
        super().__init__()
        self.active = None

    def new(self, idname):
        node = SimpleNamespace(bl_idname=idname, location=(0, 0), asset_id="", select=False)
        self.append(node)
        return node


class Image(SimpleNamespace):
    def save(self, filepath, save_copy):
        assert save_copy, "a drop never repoints the person's image at a temporary file"
        Path(filepath).write_bytes(b"pixels")
        self.saved = filepath


def image(path="", **state):
    fields = dict(filepath=path, library=None, packed_file=None, is_dirty=False, source='FILE', file_format='PNG')
    return Image(**(fields | state))


def items_of(identifier):
    """Stands in for media_place.from_identifiers: ``images/<id>.blend/Material/<name>`` names the item ``<id>``."""
    folder, _, rest = identifier.partition("/")
    identity, separator, _ = rest.partition(".blend/")
    return [SimpleNamespace(id=identity, kind="IMAGE" if folder == "images" else "3D")] if separator else []


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.file = self.folder / "photo.png"
        self.file.write_bytes(b"png")
        self.imported = []

    def import_image(self, path):
        self.imported.append(Path(path).read_bytes())
        if Path(path).name == "broken.png":
            raise ValueError("Select a regular image file")
        return {"id": "asset-%d" % len(self.imported)}

    def test_every_image_in_drop_order_and_models_are_named_not_dropped(self):
        images = {7: image(str(self.file)), 9: image("", packed_file=object())}
        entries = flow_drop.parse("image-id:7\nmodel:Chair\nimage-file:%s\nimage-id:9\n" % self.file)
        with mock.patch.dict(sys.modules, MODULES):
            ids, models, failures = flow_drop.library_images(entries, images.get, self.import_image, {}, items_of)
        self.assertEqual(ids, ["asset-1", "asset-2", "asset-3"])
        self.assertEqual(models, ["Chair"])
        self.assertEqual(failures, [])
        self.assertEqual(self.imported, [b"png", b"png", b"pixels"], "a packed image is written out, then imported")

    def test_an_image_on_disk_is_used_as_is_unless_edited(self):
        with mock.patch.dict(sys.modules, MODULES):
            self.assertEqual(flow_drop.image_file(image(str(self.file)), str(self.folder / "t")), str(self.file))
            edited = image(str(self.file), is_dirty=True)
            self.assertNotEqual(flow_drop.image_file(edited, str(self.folder / "t")), str(self.file))
            self.assertTrue(edited.saved.endswith(".png"))
            generated = image("", source='GENERATED', file_format='JPEG')
            self.assertTrue(flow_drop.image_file(generated, str(self.folder / "u")).endswith(".jpg"))

    def test_an_image_that_fails_is_reported_and_the_rest_still_land(self):
        broken = self.folder / "broken.png"
        broken.write_bytes(b"x")
        entries = flow_drop.parse("image-file:%s\nimage-file:%s" % (broken, self.file))
        with mock.patch.dict(sys.modules, MODULES):
            ids, _, failures = flow_drop.library_images(entries, {}.get, self.import_image, {}, items_of)
        self.assertEqual(ids, ["asset-2"])
        self.assertEqual(failures, ["Select a regular image file"])

    def test_a_media_studio_image_is_used_as_it_is(self):
        one, model = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
        library = {one: {"kind": "IMAGE"}, model: {"kind": "3D"}}
        entries = flow_drop.parse("studio-item:images/%s.blend/Material/A poster\n"
                                  "studio-item:images/%s.blend/Material/A poster 2\n"   # Its second picture.
                                  "studio-item:models/%s.blend/Object/A column\n"
                                  "studio-item:images/33333333-3333-3333-3333-333333333333.blend/Material/Gone"
                                  % (one, one, model))
        with mock.patch.dict(sys.modules, MODULES):
            ids, _, failures = flow_drop.library_images(entries, {}.get, self.import_image, library, items_of)
        self.assertEqual(ids, [one], "one step per item, its pictures together")
        self.assertEqual(self.imported, [], "never imported a second time")
        self.assertEqual(len(failures), 2, "a model's identifier or a removed image is reported")

    def test_a_path_with_a_colon_survives_parsing(self):
        self.assertEqual(flow_drop.parse("image-file:C:\\refs\\a.png"), [("image-file", "C:\\refs\\a.png")])


class PlacementTests(unittest.TestCase):
    def test_one_image_step_each_in_a_column_from_the_drop_point_all_selected(self):
        tree = SimpleNamespace(nodes=Nodes())
        old = tree.nodes.new("ANGELO_FlowGenerateImageNode")
        old.select = True
        placed = flow_drop.place(tree, (120.0, 40.0), ["a", "b", "c"])
        self.assertEqual([n.bl_idname for n in placed], [flow_nodes.ANGELO_FlowImageNode.bl_idname] * 3)
        self.assertEqual([n.asset_id for n in placed], ["a", "b", "c"])
        x, y = 120.0 - 30.0, 40.0 + 10.0   # Blender's own offset from the pointer (position_node_based_on_mouse).
        self.assertEqual([n.location for n in placed],
                         [(x, y), (x, y - flow_drop.STEP_SPACING), (x, y - 2 * flow_drop.STEP_SPACING)])
        self.assertEqual([n.select for n in tree.nodes], [False, True, True, True], "only the dropped steps")
        self.assertIs(tree.nodes.active, placed[0])


def body(source, start):
    """The text of the C++ function starting at ``start`` up to its closing brace at column 0."""
    text = source.split(start, 1)[1]
    return text.split("\n}\n", 1)[0]


class ContractTests(unittest.TestCase):
    """The C++ drop and the add-on's operator agree; the stock drops step aside where Angelo's takes over."""

    @classmethod
    def setUpClass(cls):
        cls.node_add, cls.space_node = NODE_ADD.read_text(), SPACE_NODE.read_text()
        cls.flow_drop = (ADDON / "flow_drop.py").read_text()

    def test_every_item_kind_the_drop_writes_is_one_the_flow_reads(self):
        copy = body(self.node_add, "void angelo_drop_assets_copy(")
        written = set(re.findall(r'"([a-z-]+):\{\}', copy))
        self.assertEqual(written, {"image-id", "image-file", "studio-item", "model"})
        for kind in written:
            self.assertIn('"%s"' % kind, self.flow_drop)

    def test_the_flow_operator_is_called_with_its_own_properties(self):
        execute = body(self.node_add, "static wmOperatorStatus angelo_drop_assets_exec(")
        self.assertIn('WM_operatortype_find("ANGELO_OT_flow_drop_items"', execute)
        self.assertIn('bl_idname = "angelo.flow_drop_items"', self.flow_drop)
        for prop in re.findall(r'RNA_\w+_set(?:_array)?\(&ptr, "(\w+)"', execute):
            self.assertRegex(self.flow_drop, r"\n    %s: \w+Property\(" % prop)

    def test_a_step_lands_where_blender_puts_a_node_at_the_pointer(self):
        offset = body(self.node_add, "static void position_node_based_on_mouse(")
        self.assertIn("location.x - NODE_DY * 1.5f", offset)
        self.assertIn("location.y + NODE_DY * 0.5f", offset)
        self.assertEqual(flow_drop.POINTER_OFFSET, (-20 * 1.5, 20 * 0.5))

    def test_one_drop_is_one_undo_step(self):
        operator = body(self.node_add, "void NODE_OT_angelo_drop_assets(")
        self.assertIn("OPTYPE_UNDO", operator)
        self.assertNotIn("OPTYPE_REGISTER", operator, "never repeated by Repeat Last (it imports into the library)")
        self.assertIn("bl_options = {'INTERNAL'}", self.flow_drop, "the flow's operator pushes no second step")

    def test_angelo_drop_comes_first_and_the_stock_drops_step_aside(self):
        boxes = body(self.space_node, "static void node_dropboxes()")
        self.assertLess(boxes.index('"NODE_OT_angelo_drop_assets"'), boxes.index('"NODE_OT_add_object"'))
        for poll in ("node_object_drop_poll", "node_collection_drop_poll", "node_id_im_drop_poll",
                     "node_material_drop_poll"):
            self.assertIn("node_stock_drop(C, *drag)", body(self.space_node, "static bool %s(" % poll), poll)
        self.assertIn("!node_angelo_takes_drop(C)", body(self.space_node, "static bool node_stock_drop("))
        takes = body(self.space_node, "static bool node_angelo_takes_drop(")
        self.assertIn("node_is_flow(C)", takes)
        self.assertIn("node_image_selection_drag(C)", takes)
        self.assertIn("studio_images == 0", body(self.space_node, "static bool node_material_drop_poll("),
                      "a Media Studio image (a material asset) is never Blender's material drop")

    def test_a_model_only_drop_says_why(self):
        refuse = body(self.space_node, "static bool node_angelo_refuse_poll(")
        self.assertIn("counts.models == 0 || counts.images > 0", refuse)
        self.assertIn("TIP_(", refuse)
        self.assertIn("return counts.images > 0;", body(self.space_node, "static bool node_angelo_drop_poll("),
                      "a flow's own drop takes only drags with images; models alone get the refusal")

    def test_media_studio_drags_use_the_shared_contract(self):
        # ED_asset_angelo_media.hh (part E): which drags are Media Studio's, the absorbed single half of a
        # multi-asset drag, the shared refusal; the add-on maps the identifiers (media_place.from_identifiers).
        items = body(self.node_add, "static void angelo_drag_items(")
        self.assertIn("angelo_media::drag_items(drag)", items)
        self.assertIn("relative_asset_identifier", items)
        self.assertNotIn("blender-assets", self.node_add, "Studio items are known by E's catalogues, not a path")
        poll = body(self.space_node, "static bool node_angelo_drop_poll(")
        self.assertIn("angelo_media::is_absorbed(*C, *drag)", poll)
        self.assertIn("angelo_media::is_absorbed(*C, drag)", body(self.space_node, "static bool node_stock_drop("))
        refuse = body(self.space_node, "static bool node_angelo_refuse_poll(")
        self.assertIn("angelo_media::refuse(", refuse)
        self.assertIn('"ASSET_OT_angelo_media_cannot_drop"', body(self.space_node, "static void node_dropboxes()"))
        self.assertIn("from .media_place import from_identifiers", self.flow_drop)
        # The stand-in above has the real item's field names: media_place.Item keeps the Studio id as ``id``.
        item = (ADDON / "media_place.py").read_text().split("class Item:", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("self.kind, self.id,", item)

    def test_every_selection_source_is_read_whole(self):
        items = body(self.node_add, "static void angelo_drag_items(")
        for kind in ("WM_DRAG_ASSET_LIST", "WM_DRAG_ASSET", "WM_DRAG_ID", "WM_DRAG_PATH"):
            self.assertIn("case %s:" % kind, items)
        self.assertIn("drag.ids", items, "every ID of an Outliner drag, not the first")

    def test_the_native_strings_are_extracted(self):
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        from i18n import sources
        listed = {path.relative_to(ROOT).as_posix() for path in sources.CPP_FILES}
        self.assertTrue({"source/blender/editors/space_node/node_add.cc",
                         "source/blender/editors/space_node/space_node.cc"} <= listed)
        self.assertIn("flow_drop", sources.UI_MODULES)


if __name__ == "__main__":
    unittest.main()
