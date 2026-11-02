# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's index checks and local search (library_catalog.py), and the index builder's merge rules
(scripts/angelo/library_index.py), on small hand-made catalogs. Host tests only."""

import json
import sys
import unittest
import unittest.mock

import team_library_fixtures as f   # Puts the add-on folder on sys.path.
from angelo_agent import library_catalog as catalog

sys.path.insert(0, str(f.ROOT / "scripts/angelo"))
import library_categories  # noqa: E402
import library_index as builder  # noqa: E402

SHA = "a" * 64
MD5 = "b" * 32
R2 = "https://assets.toonlab.io/"


def item(identity="polyhaven:chair", kind="model", title="Green Chair 01", tags=("chair", "seat"), files=None, **extra):
    files = files or [{"role": "model", "name": "model.glb", "url": R2 + "a/model.glb", "size": 10, "sha256": SHA}]
    return dict({"id": identity, "kind": kind, "title": title, "tags": list(tags), "categories": [], "source": "polyhaven",
                 "authors": ["Someone"], "license": "CC0-1.0", "attribution": "x", "attribution_required": False,
                 "popularity": 5, "thumb": R2 + "t.png", "files": files}, **extra)


def texture(identity="ambientcg:Bricks01", title="Bricks 01", tags=("brick", "wall"), **extra):
    files = [{"role": "base_color", "name": "base_color.jpg", "url": R2 + "x/b.jpg", "size": 5, "sha256": SHA},
             {"role": "normal_gl", "name": "normal_gl.jpg", "url": R2 + "x/n.jpg", "size": 5, "sha256": SHA}]
    return item(identity, "texture", title, tags, files, **extra)


def hdri(identity="polyhaven:studio_small_01", title="Studio Small 01", tags=("studio", "indoor"), popularity=9):
    files = [{"role": "hdri", "name": "environment_1k.hdr", "url": R2 + "h/1k.hdr", "size": 9, "sha256": SHA}]
    return item(identity, "hdri", title, tags, files, popularity=popularity, resolution="1k",
                resolutions={"4k": [dict(files[0], name="environment_4k.hdr", sha256=None, md5=MD5)]})


class CleanItemTests(unittest.TestCase):
    def test_a_good_item_survives_with_only_known_fields(self):
        cleaned = catalog.clean_item(dict(item(), unknown="drop me"))
        self.assertEqual(cleaned["id"], "polyhaven:chair")
        self.assertNotIn("unknown", cleaned)

    def test_links_must_be_https_on_a_toonlab_host(self):
        for url in ("http://assets.toonlab.io/a.glb", "https://evil.example/a.glb", "https://assets.toonlab.io:8443/a",
                    "https://user:pw@assets.toonlab.io/a.glb", "file:///etc/passwd"):
            with self.subTest(url=url):
                files = [{"role": "model", "name": "model.glb", "url": url, "size": 10, "sha256": SHA}]
                self.assertIsNone(catalog.clean_item(item(files=files)))

    def test_file_names_never_climb_or_start_oddly(self):
        for name in ("../model.glb", "a/../../b.glb", "/abs.glb", ".hidden.glb", "a\\b.glb", "a/b/c/d/e.glb", ""):
            with self.subTest(name=name):
                files = [{"role": "model", "name": name, "url": R2 + "m.glb", "size": 10, "sha256": SHA}]
                self.assertIsNone(catalog.clean_item(item(files=files)))

    def test_default_files_need_sha256_and_variants_at_least_md5(self):
        files = [{"role": "model", "name": "model.glb", "url": R2 + "m.glb", "size": 10, "md5": MD5}]
        self.assertIsNone(catalog.clean_item(item(files=files)))
        cleaned = catalog.clean_item(hdri())
        self.assertEqual(list(cleaned["resolutions"]), ["4k"])
        broken = hdri()
        broken["resolutions"]["4k"][0].pop("md5")
        self.assertEqual(catalog.clean_item(broken)["resolutions"], {})   # The variant goes, the item stays.

    def test_shapes_per_kind(self):
        two = [{"role": "model", "name": n, "url": R2 + n, "size": 1, "sha256": SHA} for n in ("a.glb", "b.glb")]
        self.assertIsNone(catalog.clean_item(item(files=two)))
        no_colour = [{"role": "normal_gl", "name": "n.jpg", "url": R2 + "n.jpg", "size": 1, "sha256": SHA}]
        self.assertIsNone(catalog.clean_item(item(kind="texture", files=no_colour)))
        self.assertIsNone(catalog.clean_item(item(kind="tree-recipe")))
        oversize = [{"role": "model", "name": "m.glb", "url": R2 + "m.glb", "size": catalog.MAX_FILE_BYTES + 1, "sha256": SHA}]
        self.assertIsNone(catalog.clean_item(item(files=oversize)))

    def test_text_is_bounded_and_links_to_pages_are_https_only(self):
        cleaned = catalog.clean_item(item(title="x" * 500, source_url="javascript:alert(1)", tags=["t"] * 100))
        self.assertEqual(len(cleaned["title"]), 120)
        self.assertIsNone(cleaned["source_url"])
        self.assertLessEqual(len(cleaned["tags"]), 40)

    def test_malformed_links_names_ids_and_text_are_refused_or_cleaned(self):
        for url in ("https://assets.toonlab.io:99999/a.glb", "https://assets.toonlab.io:abc/a", "https://[x/a.glb",
                    "https://assets.toonlab.io/a\\b.glb", "https://assets.toonlab.io/a b.glb"):
            self.assertFalse(catalog.allowed_url(url), url)   # Refused, never an exception that stops the catalog.
        for name in ("tex.png\n", "a\n/b.bin", "x.png.", "a./b.png"):
            self.assertFalse(catalog.safe_name(name), repr(name))
        self.assertIsNone(catalog.clean_item(item(identity="../../x")))
        self.assertIsNone(catalog.clean_item(item(identity="a" * 300)))
        cleaned = catalog.clean_item(item(title="Chair\u202eexe.gnp", tags=["a\u200bb"]))
        self.assertEqual((cleaned["title"], cleaned["tags"]), ("Chairexe.gnp", ["ab"]))

    def test_parse_refuses_another_schema_and_drops_duplicates(self):
        with self.assertRaises(catalog.IndexRefused):
            catalog.parse({"schema": "other/1", "version": "1", "items": []})
        version, items, dropped = catalog.parse({"schema": catalog.SCHEMA, "version": "2026-10-30.1",
                                                 "items": [item(), item(), {"id": 3}]})
        self.assertEqual((version, len(items), dropped), ("2026-10-30.1", 1, 2))


class SearchTests(unittest.TestCase):
    def setUp(self):
        items = [catalog.clean_item(raw) for raw in (
            item(), item("q:oak-table", title="Oak Table", tags=("table", "wood")), texture(),
            texture("ambientcg:Oak01", title="Oak Floor", tags=("wood", "floor")), hdri(),
            hdri("polyhaven:sunset", "Venice Sunset", ("outdoor", "sunset"), popularity=50))]
        self.catalog = catalog.Catalog("v", items)

    def titles(self, query, kind=None):
        return [i["title"] for i in self.catalog.search(query, kind)[0]]

    def test_kind_words_narrow_the_kind(self):
        self.assertEqual(self.titles("studio HDRI"), ["Studio Small 01"])
        self.assertEqual(self.titles("hdri"), ["Venice Sunset", "Studio Small 01"])   # By popularity.
        self.assertEqual(self.titles("oak material"), ["Oak Floor"])

    def test_prefix_plural_and_accents(self):
        self.assertEqual(self.titles("chai"), ["Green Chair 01"])
        self.assertEqual(self.titles("chairs"), ["Green Chair 01"])
        self.assertEqual(self.titles("brïcks"), ["Bricks 01"])

    def test_every_word_first_else_the_most_words(self):
        self.assertEqual(self.titles("oak table"), ["Oak Table"])
        self.assertEqual(self.titles("green oak chair"), ["Green Chair 01"])   # Two of three words beat one.
        self.assertEqual(self.titles("zebra"), [])

    def test_kind_argument_and_paging(self):
        self.assertEqual(self.titles("oak", kind="texture"), ["Oak Floor"])
        found, total = self.catalog.search("", limit=2, offset=1)
        self.assertEqual((len(found), total), (2, 6))

    def test_files_for_a_resolution(self):
        studio = self.catalog.get("polyhaven:studio_small_01")
        self.assertEqual(catalog.files_for(studio, "4k")[1], "4k")
        self.assertEqual(catalog.files_for(studio, "8k")[1], "1k")   # Not mirrored: the default.


def pro_row(identity, kind="model", **metadata):
    row = {"id": identity, "source": identity.split(":")[0], "source_id": identity, "kind": kind, "name": identity,
           "categories": [], "tags": ["t"], "license": "CC0", "attribution": "a", "thumb_path": None,
           "styled_thumb_path": None, "file_path": None, "download_url": None, "source_url": "https://x.org",
           "popularity": 1, "metadata": dict({"format": "glb", "fileSize": 100}, **metadata)}
    return row


class BuilderTests(unittest.TestCase):
    def test_unavailable_off_host_and_unknown_formats_are_left_out_and_counted(self):
        rows = [pro_row("smithsonian:gone", availability="source_unavailable"),
                dict(pro_row("met:1"), download_url="https://api.vntana.com/a.glb"),
                pro_row("cgtrader:fbx", format="fbx"),
                dict(pro_row("quaternius:car"), file_path="cc0/quaternius/car.glb")]
        items, report = builder.build(rows, [])
        self.assertEqual([i["id"] for i in items], ["quaternius:car"])
        self.assertEqual(items[0]["files"][0]["url"], "https://cdn.toonlab.io/assets/cc0/quaternius/car.glb")
        self.assertEqual(sum(report["skipped"].values()), 3)

    def test_the_seed_release_copy_with_sha256_is_preferred(self):
        oss = [{"id": "quaternius:car", "source": "quaternius", "files": [
            {"path": "asset.glb", "kind": "primary", "url": R2 + "official/car/asset.glb", "sha256": SHA, "size": 100},
            {"path": "thumbnail.png", "kind": "thumbnail", "url": R2 + "official/car/thumbnail.png", "sha256": SHA,
             "size": 7}]}]
        items, _ = builder.build([dict(pro_row("quaternius:car"), file_path="cc0/q/car.glb")], oss)
        self.assertEqual(items[0]["files"][0], {"role": "model", "name": "model.glb", "size": 100, "sha256": SHA,
                                                "url": R2 + "official/car/asset.glb"})
        self.assertEqual(items[0]["thumb"], R2 + "official/car/thumbnail.png")
        self.assertEqual(builder.needing_digest(items), [])

    def test_gltf_resources_keep_their_relative_names(self):
        resources = {"m.bin": {"url": R2 + "m/m.bin", "sizeBytes": 40, "md5": MD5},
                     "textures/m_diff_1k.jpg": {"url": R2 + "m/textures/m_diff_1k.jpg", "sizeBytes": 30, "md5": MD5}}
        row = dict(pro_row("polyhaven:m", format="gltf", modelResources=resources, fileSize=100,
                           thumbnailUrl="https://cdn.polyhaven.com/m.png"), download_url=R2 + "m/m_1k.gltf")
        (built,), _ = builder.build([row], [])
        self.assertEqual([f["name"] for f in built["files"]], ["m_1k.gltf", "m.bin", "textures/m_diff_1k.jpg"])
        self.assertEqual(built["files"][0]["size"], 30)   # The catalog's size is the whole model's.
        self.assertIsNone(built["thumb"])   # A preview on a source's CDN is never listed.
        self.assertEqual(len(builder.needing_digest([built])), 3)

    def test_thumbnails_come_only_from_toonlab_hosts(self):
        mirror = R2 + "mirrors/thumbnails/v1/polyhaven/m/0123456789abcdef.webp"
        for url, expected in ((mirror, mirror), ("https://cdn.polyhaven.com/m.png", None),
                              ("http://assets.toonlab.io/m.png", None), ("https://assets.toonlab.io:8443/m.png", None)):
            row = dict(pro_row("polyhaven:m", fileSize=100, thumbnailUrl=url), download_url=R2 + "m/m.glb")
            (built,), _ = builder.build([row], [])
            self.assertEqual(built["thumb"], expected, url)
        self.assertIsNone(catalog.clean_item(dict(item(), thumb="https://cdn.polyhaven.com/m.png"))["thumb"])

    def test_texture_roles_and_resolutions(self):
        maps = {"diffuse": {"url": R2 + "t/d.jpg", "sizeBytes": 5, "md5": MD5},
                "normal": {"path": "cc0/t/n.png", "sizeBytes": 5}, "unknownmap": {"url": R2 + "t/u.jpg"}}
        row = pro_row("ambientcg:T", kind="texture", format="image", textureFiles=maps,
                      resolutions={"2k": {"textureFiles": {"diffuse": {"url": R2 + "t/d2.jpg", "sizeBytes": 9, "md5": MD5}}}})
        (built,), _ = builder.build([row], [])
        self.assertEqual(sorted(f["role"] for f in built["files"]), ["base_color", "normal_gl"])
        self.assertEqual(list(built["resolutions"]), ["2k"])

    def test_the_built_document_passes_the_clients_checks(self):
        rows = [dict(pro_row("quaternius:car"), file_path="cc0/q/car.glb"),
                pro_row("polyhaven:sky", kind="hdri", format="hdr", fileSize=50)]
        rows[1]["download_url"] = R2 + "h/sky_1k.hdr"
        items, _ = builder.build(rows, [])
        for entry in builder.needing_digest(items):
            entry["sha256"] = SHA
        document = json.loads(builder.encode(builder.document(items, "t", "now", R2)))
        _version, parsed, dropped = catalog.parse(document)
        self.assertEqual((len(parsed), dropped), (2, 0))


class CategoryTests(unittest.TestCase):
    """Every item has one kind and one category of that kind, by where it is used; each surface shows its kind."""

    def test_the_builder_and_the_client_agree_on_the_categories(self):
        for kind in catalog.KINDS:
            self.assertEqual(library_categories.ids(kind), list(catalog.CATEGORIES[kind]), kind)

    def test_every_item_lands_in_exactly_one_kind_and_category(self):
        rows = [pro_row("quaternius:car", tags=["car", "vehicle"]), pro_row("polyhaven:chair", tags=["furniture"]),
                pro_row("smithsonian:x", tags=["smithsonian open access"]), pro_row("q:thing", tags=["thing"]),
                pro_row("polyhaven:t", kind="texture", format="image",
                        textureFiles={"diffuse": {"url": R2 + "t.jpg", "sizeBytes": 5}}),
                pro_row("polyhaven:sky", kind="hdri", format="hdr"), pro_row("kenney:s", kind="vfx")]
        rows[0]["file_path"] = rows[1]["file_path"] = rows[2]["file_path"] = rows[3]["file_path"] = "cc0/m.glb"
        rows[5]["download_url"] = R2 + "h/sky_1k.hdr"
        rows[6]["file_path"] = "cc0/s.png"
        items, _report = builder.build(rows, [])
        self.assertEqual(len(items), len(rows))
        for item in items:
            self.assertIn(item["kind"], catalog.KINDS)
            self.assertIn(item["category"], catalog.CATEGORIES[item["kind"]], item["id"])
        by_id = {i["id"]: i["category"] for i in items}
        self.assertEqual((by_id["quaternius:car"], by_id["polyhaven:chair"], by_id["smithsonian:x"], by_id["q:thing"]),
                         ("vehicles", "furniture", "history", "props"))

    def test_categories_come_from_words_and_never_from_one_items_name(self):
        rule = lambda **item: library_categories.categorize(dict({"kind": "model", "title": "", "tags": []}, **item))  # noqa
        self.assertEqual(rule(title="Oak Dining Table"), "furniture")
        self.assertEqual(rule(title="Gorilla gorilla: Cuneiform2 Left", source="smithsonian"), "science")
        self.assertEqual(rule(title="Ewer", source="smithsonian"), "history")
        self.assertEqual(rule(title="Fragment", source="toonlab-rock"), "nature")
        self.assertEqual(library_categories.categorize({"kind": "texture", "title": "Red Bricks 02", "tags": []}), "brick")
        self.assertEqual(library_categories.categorize({"kind": "hdri", "title": "Photo Studio", "tags": []}), "studio")

    def test_an_index_without_categories_uses_each_kinds_fallback(self):
        cleaned = catalog.clean_item(dict(texture(), category="not-a-category"))
        self.assertEqual(cleaned["category"], catalog.CATEGORIES["texture"][-1])

    def test_category_search_and_counts(self):
        items = [catalog.clean_item(dict(texture(identity="a:%d" % n, title="Wall %d" % n), category=c))
                 for n, c in enumerate(("brick", "brick", "wood"))]
        found = catalog.Catalog("v", items)
        self.assertEqual([i["id"] for i in found.search("", "texture", category="brick")[0]], ["a:0", "a:1"])
        self.assertEqual(found.categories("texture"), [("wood", 1), ("brick", 2)])

    def test_each_surface_shows_only_its_kind(self):
        from angelo_agent import library_overlay_draw as look
        self.assertEqual([k for k, _ in look.kind_chips(None)], [None] + list(catalog.KINDS))
        for lock in catalog.KINDS:
            self.assertEqual([k for k, _ in look.kind_chips(lock)], [lock])
            self.assertEqual(look.active_kind({"lock": lock, "kind": "model"}), lock)
        source = (f.ROOT / "scripts/addons_core/angelo_agent/library_ui.py").read_text(encoding="utf-8")
        self.assertIn('bl_context = "material"\n    bl_order = 100\n    KIND, TEXT, ICON = \'TEXTURE\'', source)
        self.assertIn('bl_context = "world"\n    bl_order = 100\n    KIND, TEXT, ICON = \'HDRI\'', source)
        for kind in catalog.KINDS:
            self.assertEqual(set(look.CATEGORY_LABELS[kind]), set(catalog.CATEGORIES[kind]))


class Recorder:
    """A Painter stand-in that records shapes and text in region pixels."""
    s = 1.0

    def __init__(self):
        self.shapes, self.texts = [], []

    def shape(self, rect, color, radius=0.0):
        self.shapes.append((tuple(rect), color))

    def measure(self, value, size):
        return len(value) * size * 0.55

    def text(self, value, x, y, size, color=None, width=None):
        self.texts.append((value, x, y))


class StatusPlacementTests(unittest.TestCase):
    """A download or notice never covers the kind chips, category chips or grid, in any mode (it covered the chips
    in the docked browser)."""

    def test_the_status_sits_in_the_footer_beside_credits(self):
        from angelo_agent import card_kit as kit, library_overlay_draw as look

        class Library:
            catalog, status = None, "ready"   # A status word: the panel once unpacked it in place of the download's.

            def get(self, _id):
                return None

        def inside(inner, outer):
            return (outer[0] <= inner[0] and inner[0] + inner[2] <= outer[0] + outer[2]
                    and outer[1] <= inner[1] and inner[1] + inner[3] <= outer[1] + outer[3])

        def overlaps(a, b):
            return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]

        for mode in ("search", "browse"):
            for width, height in ((900, 700), (1600, 1200), (2600, 1500)):
                rects = look.layout(width, height, height - 30, 40, width - 40, mode, 1.0)
                paint = Recorder()
                with unittest.mock.patch.dict(sys.modules, {"gpu": unittest.mock.MagicMock(),
                                                            "gpu_extras": unittest.mock.MagicMock(),
                                                            "gpu_extras.presets": unittest.mock.MagicMock()}):
                    look.draw_panel(paint, {"hits": [], "rects": rects, "mode": mode, "query": ""}, rects, Library(),
                                    [], 0, (0.5, "Getting a model"))
                bars = [r for r, color in paint.shapes if color == kit.SCRIM]
                self.assertEqual(len(bars), 1)
                self.assertTrue(inside(bars[0], rects["footer"]), (mode, width))
                for other in ("chips", "grid"):
                    self.assertFalse(overlaps(bars[0], rects[other]), (mode, width, other))
                self.assertIn("Getting a model", [value for value, _x, _y in paint.texts])
        pill = look.layout(1600, 1200, 1170, 40, 1560, None, 1.0)["pill"]
        under = look.progress_rect(pill, 1.0)
        self.assertLessEqual(under[1] + under[3], pill[1])


if __name__ == "__main__":
    unittest.main()
