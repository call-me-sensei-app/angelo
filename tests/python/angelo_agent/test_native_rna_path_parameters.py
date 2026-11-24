# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every RNA string that names a file, a folder or an address Angelo's process later reads, scans or fetches is
judged when a confined script sets it (doc/angelo/script_confinement.rst, "Strings that name a place"; Ruling 11).

The confinement judges a value whose subtype is a file or folder path (``PROP_FILEPATH``/``PROP_DIRPATH``). A place
declared as a plain string escapes that judge, unless the confinement lists it (``PLACE_STRINGS`` in
``bpy_angelo_guard_confine_ops.cc``: an address is the network, a byte-string folder is judged as a path).
``sequence_editor.strips.new_movie`` and ``new_sound`` opened a link into the automounter unjudged this way, and
``ProjectAssetLibraryCollection.new(directory=, remote_url=)`` handed the asset system a folder and an address.

* Every RNA string property and function parameter named like a place (path, file, dir, folder, URL, host, ...) is
  a path subtype, a listed place, or classified here as no place, with why. A new one fails until it is one of those.
* Every listed place is in the confinement's table, and every table entry still exists.
* The strings this change made path-typed stay so; the strips ask the file gate before they open.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAKESRNA = ROOT / "source/blender/makesrna/intern"
CONFINE_OPS = ROOT / "source/blender/python/intern/bpy_angelo_guard_confine_ops.cc"
NAME = re.compile(r"(path|file|dir|directory|folder|url|uri|server|host|endpoint)", re.IGNORECASE)
CALL = re.compile(r'RNA_def_(property|string(?:_file_path|_dir_path|_file_name)?)\s*\(\s*(\w+)\s*,\s*"(\w+)"\s*,'
                  r'([^;]*?)\)\s*;', re.S)
PATH_SUBTYPES = {"PROP_FILEPATH", "PROP_DIRPATH"}

# Places the confinement judges by name (PLACE_STRINGS): (RNA struct, function or None, name) -> what Angelo does
# with the value later.
PLACES = {
    ("UserAssetLibrary", None, "remote_url"): "an asset library's address, fetched later",
    ("UserExtensionRepo", None, "remote_url"): "an extension repository's address, fetched later",
    ("ProjectAssetLibraryCollection", "new", "remote_url"): "a project asset library's address, fetched later",
    ("AssetLibraryCollection", "new", "remote_url"): "an asset library's address, fetched later",
    ("UserExtensionRepoCollection", "new", "remote_url"): "an extension repository's address, fetched later",
    ("FileSelectParams", None, "directory"): "the file browser's folder (a byte string), listed later",
}
# (file, name) -> where the scan finds it in source, so a listed place is matched to its declaration.
PLACE_SOURCES = {
    ("rna_userdef.cc", "remote_url"): {("UserAssetLibrary", None, "remote_url"), ("UserExtensionRepo", None, "remote_url"),
                                       ("AssetLibraryCollection", "new", "remote_url"),
                                       ("UserExtensionRepoCollection", "new", "remote_url")},
    ("rna_blender_project.cc", "remote_url"): {("ProjectAssetLibraryCollection", "new", "remote_url")},
    ("rna_space.cc", "directory"): {("FileSelectParams", None, "directory")},
}
# (file, name): why a plain string is right: it names no file, folder or address Angelo reads, scans or fetches.
NOT_A_PLACE = {
    ("rna_ID.cc", "rna_path"): "an RNA data path inside the document",
    ("rna_action.cc", "data_path"): "an RNA data path inside the document",
    ("rna_animation.cc", "data_path"): "an RNA data path inside the document",
    ("rna_fcurve.cc", "data_path"): "an RNA data path inside the document",
    ("rna_nla.cc", "data_path"): "an RNA data path inside the document",
    ("rna_node_socket.cc", "data_path"): "an RNA data path inside the document",
    ("rna_node_tree_interface.cc", "data_path"): "an RNA data path inside the document",
    ("rna_constraint.cc", "object_path"): "an object's path inside an Alembic/USD archive, not a file",
    ("rna_modifier.cc", "object_path"): "an object's path inside an Alembic/USD archive, not a file",
    ("rna_cachefile.cc", "path"): "CacheObjectPath: an object's path inside the archive, not a file",
    ("rna_nodetree.cc", "filepath"): "the texture Output node's output name (stored as `name`), not a file",
    ("rna_render.cc", "temporary_directory"): "read-only: Angelo's own temporary folder, for the engine",
    ("rna_scene.cc", "file_suffix"): "a suffix added to file names, not a place",
    ("rna_scene.cc", "file_extension"): "read-only: the extension of the output format",
    ("rna_asset.cc", "url"): "a function's return value (the online library's address), not a script's input",
    ("rna_asset.cc", "path"): "a function's return value (the online library's cache), not a script's input",
    ("rna_asset.cc", "full_library_path"): "read-only, PROP_FILENAME: where an asset is, as Blender reports it",
    ("rna_asset.cc", "full_path"): "read-only, PROP_FILENAME: where an asset is, as Blender reports it",
    ("rna_sequencer.cc", "filename"): "PROP_FILENAME: a name inside the strip's `directory` (a PROP_DIRPATH)",
    ("rna_sequencer_api.cc", "filename"): "StripElements.append: a name inside the strip's `directory`, a "
                                          "PROP_DIRPATH property judged when it is set",
    ("rna_space.cc", "filename"): "PROP_FILENAME: a name inside the browser's `directory`, judged as a place",
    ("rna_ui.cc", "path"): "an asset catalog path (\"Models/Rocks\")",
    ("rna_ui.cc", "active_catalog_path"): "an asset catalog path",
    ("rna_ui.cc", "bl_file_extensions"): "a file handler's extensions, not a place",
    ("rna_ui_api.cc", "catalog_path"): "an asset catalog path",
    ("rna_ui_api.cc", "url"): "UILayout.link: a button the person clicks; opening it is the person's act",
    ("rna_wm_api.cc", "library_url"): "remote-asset callbacks' parameters: values Blender hands TO an add-on's "
                                      "Python, never a script's setting",
    ("rna_wm_api.cc", "absolute_file_url"): "the same: handed to an add-on's callback",
    ("rna_wm_api.cc", "local_file_abspath"): "the same: handed to an add-on's callback",
    ("rna_wm_api.cc", "preview_full_path"): "the same: handed to an add-on's callback",
    ("rna_xr.cc", "path"): "an OpenXR action path (/user/hand/left), not a file",
    ("rna_xr.cc", "user_path"): "an OpenXR user path, not a file",
    ("rna_xr.cc", "user_path_other"): "an OpenXR user path, not a file",
    ("rna_xr.cc", "profile"): "an OpenXR interaction profile path, not a file",
}


def scan():
    """{(file, name): set of subtypes} for every RNA string named like a place, path subtypes excluded."""
    found = {}
    for path in sorted(MAKESRNA.glob("*.cc")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for kind, _owner, name, rest in CALL.findall(text):
            if not NAME.search(name):
                continue
            if kind == "property":
                if "PROP_STRING" not in rest:
                    continue
                subtype = re.search(r"PROP_STRING\s*,\s*(PROP_\w+)", rest)
                subtype = subtype.group(1) if subtype else "?"
            else:
                subtype = {"string": "PROP_NONE", "string_file_path": "PROP_FILEPATH",
                           "string_dir_path": "PROP_DIRPATH", "string_file_name": "PROP_FILENAME"}[kind]
            if subtype in PATH_SUBTYPES:
                continue
            found.setdefault((path.name, name), set()).add(subtype)
    return found


class PlaceStrings(unittest.TestCase):
    def test_every_place_named_string_is_judged_or_classified(self):
        unclassified = sorted(key for key in scan() if key not in NOT_A_PLACE and key not in PLACE_SOURCES)
        self.assertEqual(unclassified, [], "make it RNA_def_string_file_path/_dir_path (judged like every loader), "
                                           "list it in PLACE_STRINGS (an address or a byte-string folder), or "
                                           "classify it in NOT_A_PLACE with why")

    def test_every_classification_is_still_used(self):
        found = scan()
        self.assertEqual(sorted(set(NOT_A_PLACE) - set(found)), [], "gone: remove it from NOT_A_PLACE")
        self.assertEqual(sorted(set(PLACE_SOURCES) - set(found)), [], "gone: remove it from PLACE_SOURCES")

    def test_the_confinement_lists_every_place(self):
        table = CONFINE_OPS.read_text(encoding="utf-8")
        listed = set()
        for type_name, function, name, _url in re.findall(r'\{"(\w+)", (nullptr|"\w+"), "(\w+)", (true|false)\}',
                                                          table):
            listed.add((type_name, None if function == "nullptr" else function.strip('"'), name))
        self.assertEqual(listed, set(PLACES), "PLACE_STRINGS and PLACES must name the same places")
        self.assertEqual(set().union(*PLACE_SOURCES.values()), set(PLACES))
        for type_name, _function, _name in PLACES:
            self.assertTrue(any('"%s"' % type_name in path.read_text(encoding="utf-8", errors="replace")
                                for path in MAKESRNA.glob("*.cc")), type_name)

    def test_folders_and_files_a_script_hands_over_are_path_typed(self):
        expected = {
            "rna_blender_project.cc": ['RNA_def_string_dir_path(func, "directory"'],
            "rna_userdef.cc": ['RNA_def_string_dir_path(func, "directory"', 'RNA_def_string_dir_path(func,\n'
                               '                          "custom_directory"',
                               'RNA_def_string_file_path(\n      func, "path"'],
            "rna_cachefile.cc": ['RNA_def_string_file_path(\n      func, "filepath"'],
            "rna_render.cc": ['RNA_def_string_file_path(\n      func,\n      "filepath"'],
        }
        for name, needles in expected.items():
            text = (MAKESRNA / name).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, name)
        render = (MAKESRNA / "rna_render.cc").read_text(encoding="utf-8")
        self.assertEqual(render.count('RNA_def_string_file_path(\n      func,\n      "filepath"'), 2,
                         "RenderResult.load_from_file and RenderLayer.load_from_file read the file named")

    def test_the_strip_loaders_take_file_paths(self):
        source = (MAKESRNA / "rna_sequencer_api.cc").read_text(encoding="utf-8")
        for function in ("new_image", "new_movie", "new_sound"):
            body = source[source.index('"%s", new_%s_func_name' % (function, function[4:])):]
            body = body[:body.index("RNA_def_function_return")]
            self.assertIn('RNA_def_string_file_path(func, "filepath"', body, function)
            self.assertIn("PROP_PATH_SUPPORTS_BLEND_RELATIVE", body, function + ": the strip resolves // itself")

    def test_movie_and_sound_strips_ask_the_gate_before_opening(self):
        source = (ROOT / "source/blender/sequencer/intern/strip_add.cc").read_text(encoding="utf-8")
        sound = source[source.index("Strip *add_sound_strip(Main *bmain"):]
        self.assertLess(sound.index("strip_file_refused(filepath_abs)"), sound.index("BKE_sound_new_file_exists"))
        movie = source[source.index("Strip *add_movie_strip("):]
        movie = movie[:movie.index("Strip *strip = strip_alloc(")]
        self.assertLess(movie.index("strip_file_refused(filepath)"), movie.index("openanim("))

    def test_byte_strings_are_judged_too(self):
        source = (ROOT / "source/blender/python/intern/bpy_rna.cc").read_text(encoding="utf-8")
        self.assertEqual(source.count("BPY_angelo_guard_path_property_refusal(prop, param)"), 2,
                         "the byte-string and text branches both ask")


if __name__ == "__main__":
    unittest.main()
