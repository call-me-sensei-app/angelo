# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The angelo_library_* tools: contract, placement, compact results and the host binding's refusals.

* Validation is exact (unknown fields, bounds, handles, finite locations).
* The family is deferred on Claude and behind Codex's deferLoading namespace, never in voice threads or
  collaborators; connected MCP apps have scoped access. Import always shows a card, search needs none.
* Search results are compact (ids and facts, no link or path) and wait for a loading catalog within a bound.
* Refusals before the scene can change carry closed codes and ``scene_changed: false``.

Dependency-free: ``library_host.library()`` is replaced with a small in-memory catalog (``library_catalog.Catalog``);
nothing here reaches the network or Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import concurrent.futures
import json
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import (library_agent, library_catalog, library_fetch, library_host,  # noqa: E402
                          plan_schema, private_roots)
from angelo_agent.approvals import approval_message  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.external_clients import (EDIT, READ, EDIT_TOOLS, HEADLESS_TOOLS, NEVER_FOR_APPS,  # noqa: E402
                                          READ_TOOLS, WINDOW_ONLY_EDITS, tools_for)
from angelo_agent.external_requests import PHASE1_READ_TOOLS  # noqa: E402
from angelo_agent.library_spec import (IMPORT, LIBRARY_EFFECTS, LIBRARY_GUIDANCE, LIBRARY_NAMES,  # noqa: E402
                                       LIBRARY_TOOLS, SEARCH, compact)
from angelo_agent.team_library_files import ImportFailed, NotImported  # noqa: E402
from angelo_agent.tool_specs import (BOUNDED_MUTATIONS, COLLABORATOR_TOOLS, TOOL_MAP, codex_tools,  # noqa: E402
                                     dynamic_tools, native_plan_schema, validate_call)

SHA = "a" * 64
HANDLE = "scn:%s:7" % ("0" * 32)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _file(role, name, size=2048):
    return {"role": role, "name": name, "url": "https://assets.toonlab.io/x/" + name, "size": size, "sha256": SHA}


def _item(identity, kind, title, files, **extra):
    raw = {"id": identity, "kind": kind, "title": title, "tags": ["stock"], "categories": ["props"],
           "source": "ToonLab", "authors": ["A. Maker"], "license": "CC0-1.0", "attribution": "",
           "attribution_required": False, "popularity": 5, "files": files,
           "thumb": "https://cdn.toonlab.io/thumbs/%s.png" % identity, **extra}
    item = library_catalog.clean_item(raw)
    assert item is not None, identity
    return item


ITEMS = [
    _item("tl-chair-01", "model", "Wooden chair", [_file("model", "chair.glb", 3 * 1048576)], popularity=50),
    _item("ph-oak-floor", "texture", "Oak floor planks", [_file("base_color", "oak_diff_1k.png"),
                                                          _file("normal_gl", "oak_nor_1k.png")],
          resolution="1k", resolutions={"4k": [_file("base_color", "oak_diff_4k.png", 9000),
                                               _file("normal_gl", "oak_nor_4k.png", 9000)]},
          attribution_required=True, attribution="Oak floor by A. Maker"),
    _item("ph-studio-sky", "hdri", "Studio sky", [_file("hdri", "sky_1k.hdr")], resolution="1k"),
    _item("tl-spark", "vfx", "Spark burst", [_file("image", "spark.png")]),
]


class FakeLibrary:
    """``library_host.Library``'s surface the tools use, over a real in-memory ``Catalog``."""

    def __init__(self, root, catalog=None, status="ready"):
        self.root, self.catalog, self.status, self.error = Path(root), catalog, status, ""
        self.downloaded, self.fetches, self.future, self.ensured = set(), [], None, 0

    def ensure_catalog(self, refresh=False):
        self.ensured += 1

    def search(self, query, kind=None, limit=60, offset=0):
        return self.catalog.search(query, kind, limit, offset) if self.catalog is not None else ([], 0)

    def get(self, identity):
        return self.catalog.get(identity) if self.catalog is not None else None

    def cached(self, item, resolution=None):
        return item["id"] in self.downloaded

    def fetch(self, item, resolution=None, done=None, progress=None, cancel=None):
        self.fetches.append((item["id"], resolution))
        self.future.lease = unittest.mock.Mock()   # library_host.Library.fetch's contract: each future holds a lease.
        return self.future


class FakeReferences:
    def __init__(self):
        self.calls = []

    def import_images(self, paths):
        self.calls.append(list(paths))
        return [{"id": "view-%d-%d" % (len(self.calls), i), "kind": "image"} for i, _path in enumerate(paths)]


def fake_window(objects=(), viewport=True):
    layer = object()
    scene = types.SimpleNamespace(objects=list(objects), session_uid=1)
    screen = types.SimpleNamespace(areas=[types.SimpleNamespace(type="VIEW_3D" if viewport else "OUTLINER")])
    return types.SimpleNamespace(screen=screen, scene=scene, view_layer=layer)


def encoded(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def wait_for(pending, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = pending.poll()
        if result is not None:
            return result
        time.sleep(0.01)
    raise AssertionError("the pending call never finished")


class LibraryCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.lib = FakeLibrary(self.folder, library_catalog.Catalog("t1", list(ITEMS)))
        patcher = unittest.mock.patch.object(library_host, "library", return_value=self.lib)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.references = FakeReferences()
        self.controller = types.SimpleNamespace(references=self.references)

    def service(self, window=None):
        return library_agent.LibraryService(self.controller, window or fake_window())


class ContractTests(unittest.TestCase):
    def test_validation_is_exact(self):
        self.assertEqual(validate_call(SEARCH, {"query": "chair", "limit": 20})["limit"], 20)
        self.assertEqual(validate_call(IMPORT, {"item_id": "tl-chair-01", "location": [1, 2, 3]})["location"],
                         [1.0, 2.0, 3.0])
        self.assertEqual(validate_call(IMPORT, {"item_id": "ph-oak-floor", "objects": [HANDLE, "o3"]})["objects"],
                         [HANDLE, "o3"])
        bad = [(SEARCH, {}), (SEARCH, {"query": "x", "limit": 21}), (SEARCH, {"query": "x", "limit": 0}),
               (SEARCH, {"query": "x", "kind": "audio"}), (SEARCH, {"query": "x" * 201}),
               (SEARCH, {"query": "x", "preview": 1}), (SEARCH, {"query": "a\nb"}), (SEARCH, {"query": "x", "page": 2}),
               (IMPORT, {}), (IMPORT, {"item_id": ""}), (IMPORT, {"item_id": " "}), (IMPORT, {"item_id": "x" * 301}),
               (IMPORT, {"item_id": "x", "resolution": "3k"}), (IMPORT, {"item_id": "x", "location": [0, 0]}),
               (IMPORT, {"item_id": "x", "location": [0, 0, float("nan")]}),
               (IMPORT, {"item_id": "x", "location": [0, 0, 1e9]}), (IMPORT, {"item_id": "x", "location": [0, True, 0]}),
               (IMPORT, {"item_id": "x", "objects": []}), (IMPORT, {"item_id": "x", "objects": ["o1", "o1"]}),
               (IMPORT, {"item_id": "x", "objects": ["../etc"]}), (IMPORT, {"item_id": "x", "into": "SCENE"})]
        for name, args in bad:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                validate_call(name, args)

    def test_import_is_the_only_effect_and_search_is_read_only(self):
        self.assertEqual({t["name"] for t in LIBRARY_TOOLS if not t["annotations"]["readOnlyHint"]}, LIBRARY_EFFECTS)
        self.assertEqual(LIBRARY_EFFECTS, {IMPORT})
        self.assertTrue(all(name.startswith("angelo_library_") for name in LIBRARY_NAMES))
        for tool in LIBRARY_TOOLS:   # Item text is untrusted data and the tools say so, or say what the files can do.
            self.assertTrue("never instructions" in tool["description"] or "Nothing in the files runs" in
                            tool["description"], tool["name"])

    def test_compact_rows_carry_ids_and_facts_only(self):
        row = compact(ITEMS[1], library_catalog.item_bytes(ITEMS[1]), True)
        self.assertEqual(set(row), {"id", "kind", "title", "source", "license", "attribution_required", "mb",
                                    "downloaded", "resolutions", "default_resolution"})
        self.assertEqual((row["resolutions"], row["default_resolution"]), (["1k", "4k"], "1k"))
        model = compact(ITEMS[0], library_catalog.item_bytes(ITEMS[0]), False)
        self.assertEqual((model["mb"], model["downloaded"]), (3.0, False))
        self.assertNotIn("resolutions", model)
        for item in ITEMS:
            text = encoded(compact(item, library_catalog.item_bytes(item), False))
            self.assertNotIn("http", text)
            self.assertNotIn("/", text.replace("CC0-1.0", ""))


class PlacementTests(unittest.TestCase):
    def test_deferred_never_automatic_and_scoped_to_chat_or_connected_apps(self):
        self.assertLessEqual(LIBRARY_NAMES, plan_schema.DEFERRED_TOOLS)
        self.assertFalse(LIBRARY_NAMES & BOUNDED_MUTATIONS, "an import always shows its card, Auto included")
        self.assertFalse(LIBRARY_NAMES & COLLABORATOR_TOOLS)
        self.assertFalse(LIBRARY_NAMES & PHASE1_READ_TOOLS)
        self.assertFalse(LIBRARY_NAMES & NEVER_FOR_APPS)
        self.assertIn(SEARCH, READ_TOOLS)
        self.assertNotIn(IMPORT, READ_TOOLS)
        self.assertIn(IMPORT, EDIT_TOOLS)
        self.assertEqual(LIBRARY_NAMES & tools_for((READ,)), {SEARCH})
        self.assertEqual(LIBRARY_NAMES & tools_for((READ, EDIT)), LIBRARY_NAMES)
        self.assertIn(IMPORT, WINDOW_ONLY_EDITS)
        self.assertNotIn(IMPORT, HEADLESS_TOOLS)
        self.assertEqual(LIBRARY_NAMES & tools_for((READ, EDIT), headless=True), {SEARCH})
        self.assertFalse({t["name"] for t in dynamic_tools()} & LIBRARY_NAMES, "voice threads never declare it")
        self.assertFalse({t["name"] for t in dynamic_tools(frozenset(TOOL_MAP))} & LIBRARY_NAMES)

    def test_claude_names_it_and_codex_loads_it_on_demand(self):
        schema = encoded(native_plan_schema())
        for name in LIBRARY_NAMES:
            self.assertEqual(schema.count('"%s"' % name), 1, name)
        self.assertNotIn("default_resolution", schema)
        (namespace,) = [t for t in codex_tools(None, short_identifiers=True, defer=True) if t["type"] == "namespace"]
        self.assertTrue(all(t.get("deferLoading") for t in namespace["tools"] if t["name"] in LIBRARY_NAMES))
        self.assertEqual(LIBRARY_NAMES, {t["name"] for t in namespace["tools"]} & LIBRARY_NAMES)
        self.assertIn("the Angelo Library", namespace["description"])
        main_thread = {t["name"] for t in codex_tools(None, short_identifiers=True, defer=True) if t["type"] != "namespace"}
        self.assertFalse(main_thread & LIBRARY_NAMES)
        from angelo_agent.providers import codex_instructions
        self.assertIn("the Angelo Library", codex_instructions(short_identifiers=True, deferred=True))

    def test_lookup_returns_the_schemas_with_the_family_guidance(self):
        found = plan_schema.lookup("TOOLS", [SEARCH, IMPORT])
        self.assertEqual([tool["name"] for tool in found["tools"]], [SEARCH, IMPORT])
        self.assertEqual(found["guidance"], {"LIBRARY": LIBRARY_GUIDANCE})

    def test_approval_and_checkpoint_classes_follow_the_studio_model_import(self):
        studio = "angelo_studio_import_3d"
        for check in (Controller.requires_mutation_checkpoint, Controller.is_native_action):
            self.assertEqual(check(None, IMPORT), check(None, studio), check.__name__)
        self.assertEqual(IMPORT in BOUNDED_MUTATIONS, studio in BOUNDED_MUTATIONS)
        self.assertFalse(Controller.requires_mutation_checkpoint(None, SEARCH))
        self.assertFalse(Controller.is_native_action(None, SEARCH), "a catalog read is no scene action")

    def test_the_guidance_is_generic(self):
        # Fix the class, never the case: no item, scene or object names, no numbers tuned to a test.
        for word in ("chair", "oak", "sky", "spark", "ToonLab"):
            self.assertNotIn(word.lower(), LIBRARY_GUIDANCE.lower())
        self.assertFalse(any(character.isdigit() for character in LIBRARY_GUIDANCE))


class SearchTests(LibraryCase):
    def test_a_loaded_catalog_answers_at_once_with_compact_rows(self):
        self.lib.downloaded.add("tl-chair-01")
        result = self.service().execute(SEARCH, {"query": "chairs"})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], "tl-chair-01")
        self.assertTrue(result["items"][0]["downloaded"])
        self.assertIs(result["scene_changed"], False)
        self.assertNotIn("http", encoded(result))
        kind = self.service().execute(SEARCH, {"query": "", "kind": "texture", "limit": 1})
        self.assertEqual(([row["kind"] for row in kind["items"]], kind["kind"]), (["texture"], "texture"))
        self.assertIn("note", self.service().execute(SEARCH, {"query": "zeppelin"}))

    def test_a_loading_catalog_is_waited_for_within_a_bound(self):
        self.lib.catalog, self.lib.status = None, "loading"
        pending = self.service().execute(SEARCH, {"query": "oak"})
        self.assertIsInstance(pending, library_agent.LibraryPending)
        self.assertIsNone(pending.poll())
        self.lib.catalog, self.lib.status = library_catalog.Catalog("t1", list(ITEMS)), "ready"
        self.assertEqual(pending.poll()["items"][0]["id"], "ph-oak-floor")
        self.lib.catalog, self.lib.status = None, "loading"
        with unittest.mock.patch.object(library_agent, "CATALOG_WAIT", 0.0):
            late = self.service().execute(SEARCH, {"query": "oak"}).poll()
        self.assertEqual((late["error_code"], late["scene_changed"]), ("EXPIRED", False))

    def test_an_unreachable_library_refuses_with_a_closed_code(self):
        self.lib.catalog, self.lib.status, self.lib.error = None, "offline", "Couldn't reach the Angelo Library"
        result = self.service().execute(SEARCH, {"query": "oak"}).poll()
        self.assertEqual((result["error_code"], result["scene_changed"]), ("EXPIRED", False))
        self.assertIn("unavailable", result["error"])

    def test_preview_adds_the_first_thumbnails_as_images_in_result_order(self):
        thumbs = self.folder / "thumbs"
        thumbs.mkdir()
        (thumbs / "t.img").write_bytes(PNG)
        previews = self.folder / "previews"
        previews.mkdir()
        with unittest.mock.patch.object(library_fetch, "thumbnail", return_value=thumbs / "t.img"), \
                unittest.mock.patch.object(private_roots, "allocate", return_value=previews):
            result = wait_for(self.service().execute(SEARCH, {"query": "", "limit": 4, "preview": True}))
        self.assertEqual(result["previews"], [row["id"] for row in result["items"]])
        self.assertEqual(len(result["views"]), 4)
        self.assertEqual([len(call) for call in self.references.calls], [4])
        self.assertFalse(previews.exists(), "the copied thumbnails are removed when the call ends")
        with unittest.mock.patch.object(library_fetch, "thumbnail", return_value=None), \
                unittest.mock.patch.object(private_roots, "allocate", return_value=self.folder / "p2"):
            (self.folder / "p2").mkdir()
            missing = wait_for(self.service().execute(SEARCH, {"query": "chair", "preview": True}))
        self.assertNotIn("views", missing)
        self.assertIn("preview_note", missing)


class ImportTests(LibraryCase):
    def refusal(self, args, window=None):
        with self.assertRaises(library_agent.RefusalError) as caught:
            self.service(window).precondition(IMPORT, args)
        return caught.exception

    def test_preconditions_refuse_before_any_card_with_closed_codes(self):
        cases = [({"item_id": "nope"}, "VALIDATION", "No Angelo Library item"),
                 ({"item_id": "tl-chair-01", "resolution": "4k"}, "VALIDATION", "texture sets and HDRIs only"),
                 ({"item_id": "ph-oak-floor", "resolution": "2k", "objects": [HANDLE]}, "VALIDATION", "1k, 4k"),
                 ({"item_id": "ph-studio-sky", "location": [0, 0, 0]}, "VALIDATION", "models and VFX sprites only"),
                 ({"item_id": "tl-chair-01", "objects": [HANDLE]}, "VALIDATION", "texture set's meshes only"),
                 ({"item_id": "ph-oak-floor"}, "VALIDATION", "needs meshes")]
        for args, code, words in cases:
            with self.subTest(args=args):
                error = self.refusal(args)
                self.assertEqual(error.code, code)
                self.assertIn(words, str(error))
        self.assertEqual(self.refusal({"item_id": "tl-chair-01"}, fake_window(viewport=False)).code, "UNSUPPORTED")
        self.lib.catalog, self.lib.status = None, "loading"
        self.assertEqual(self.refusal({"item_id": "tl-chair-01"}).code, "EXPIRED")

    def test_a_valid_precondition_pins_the_files_and_placement(self):
        signature = self.service().precondition(IMPORT, {"item_id": "tl-chair-01", "location": [1, 0, 0]})
        self.assertEqual(signature, ("tl-chair-01", None, (("chair.glb", 3 * 1048576),), (), (1.0, 0.0, 0.0)))
        hdri = self.service().precondition(IMPORT, {"item_id": "ph-studio-sky", "resolution": "1k"})
        self.assertEqual(hdri[1], "1k")

    def test_a_failed_download_reports_its_closed_code_and_an_unchanged_scene(self):
        future = concurrent.futures.Future()
        self.lib.future = future
        pending = self.service().execute(IMPORT, {"item_id": "ph-studio-sky"})
        self.assertEqual(self.lib.fetches, [("ph-studio-sky", None)])
        self.assertIsNone(pending.poll())
        future.set_exception(library_fetch.FetchError("A library file doesn't match its checksum; it wasn't used.",
                                                      "UNSUPPORTED"))
        self.assertEqual(pending.poll(), {"error": "A library file doesn't match its checksum; it wasn't used.",
                                          "scene_changed": False, "error_code": "UNSUPPORTED"})
        self.assertEqual(future.lease.release.call_count, 1, "the cache may trim the item once the call has ended")
        stopped = concurrent.futures.Future()
        stopped.cancel()
        self.lib.future = stopped
        self.assertEqual(self.service().execute(IMPORT, {"item_id": "tl-spark"}).poll()["error_code"], "CANCELLED")
        self.assertEqual(stopped.lease.release.call_count, 1)
        running = concurrent.futures.Future()
        self.lib.future = running
        self.service().execute(IMPORT, {"item_id": "tl-spark"}).cancel()   # A stop (or the call's timeout).
        self.assertEqual(running.lease.release.call_count, 1)

    def test_main_thread_failures_claim_an_unchanged_scene_only_when_it_is(self):
        self.assertEqual(library_agent._finish_failure(NotImported("Leave the active editing mode."))["error_code"],
                         "UNSUPPORTED")
        rolled_back = library_agent._finish_failure(ImportFailed("Rolled back.", scene_unchanged=True))
        self.assertEqual(rolled_back, {"error": "Rolled back.", "scene_changed": False})
        partial = library_agent._finish_failure(ImportFailed("Part remains.", scene_unchanged=False))
        self.assertEqual(partial, {"error": "Part remains.", "partial_changes_possible": True})
        unknown = library_agent._finish_failure(RuntimeError("boom"))
        self.assertTrue(unknown["partial_changes_possible"])
        self.assertNotIn("scene_changed", unknown)

    def test_the_card_names_the_item_its_size_and_effect(self):
        card = approval_message(IMPORT, {"item_id": "ph-oak-floor", "resolution": "4k", "objects": [HANDLE]})
        self.assertEqual(card["title"], "Add from the Angelo Library")
        self.assertIn("“Oak floor planks” · 4k · CC0-1.0 · 17 KB download", card["body"])
        self.assertIn("on the 1 object named", card["body"])
        self.assertIn("Credit required: Oak floor by A. Maker", card["body"])
        self.assertIn("Nothing in them runs", card["warning"])
        self.assertIn("replacing the current one", approval_message(IMPORT, {"item_id": "ph-studio-sky"})["body"])
        self.assertIn("isn't in the library's catalog", approval_message(IMPORT, {"item_id": "gone"})["warning"])


if __name__ == "__main__":
    unittest.main()
