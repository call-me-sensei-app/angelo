# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""angelo_blend_file without Blender: how files are named, the open-file summary and the workspace listing's gates.

The compiled-app suite (test_blender_read_tools.py) proves the same contract with
Blender's own library reader and real .blend files.
"""

import contextlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import blend_file, blend_file_listing, blend_file_paths as paths  # noqa: E402
from angelo_agent import native_programs  # noqa: E402,F401  (patched by name below)
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.read_spec import validate_read  # noqa: E402
from read_tools_fakes import Collection, Struct, prop, rna  # noqa: E402

ID = rna("ID", [prop("name", "STRING")])
TYPES = {name: rna(identifier, base=ID) for name, identifier in (
    ("objects", "Object"), ("meshes", "Mesh"), ("materials", "Material"), ("images", "Image"), ("scenes", "Scene"),
    ("node_groups", "NodeTree"), ("libraries", "Library"), ("worlds", "World"), ("lights", "Light"),
    ("textures", "Texture"), ("linestyles", "FreestyleLineStyle"), ("sounds", "Sound"))}
POSIX = unittest.skipUnless(os.name == "posix", "POSIX path spelling")


def block(kind, name, users=1, **fields):
    values = dict(name=name, users=users, use_fake_user=False, library=None, is_missing=False)
    values.update(fields)
    return Struct(TYPES[kind], **values)


def node(name, idname="ShaderNodeMath", kind="MATH", **fields):
    return SimpleNamespace(name=name, bl_idname=idname, type=kind, **fields)


def open_file(folder="/Users/alice/Client X/island", **collections):
    """A fake ``bpy`` for the open file; ``file_path_map`` returns ``collections['paths']``."""
    stored = collections.pop("paths", {})
    data = SimpleNamespace(filepath=os.path.join(folder, "island.blend") if folder else "", is_dirty=False,
                           file_path_map=lambda: stored)
    data.bl_rna = SimpleNamespace(properties=[prop(name, "COLLECTION", fixed_type=TYPES[name]) for name in TYPES]
                                  + [prop("all_ids", "COLLECTION", fixed_type=ID), prop("filepath", "STRING")])
    for name in TYPES:
        setattr(data, name, Collection(collections.get(name, [])))
    return SimpleNamespace(data=data)


class PathNamingTests(unittest.TestCase):
    @POSIX
    def test_inside_the_workspace_or_the_files_folder_is_relative_otherwise_a_base_name(self):
        folder, workspace = "/Users/alice/Client X/island", "/Users/alice/Client X"
        for raw, expected in (
                ("//textures/rock.png", {"file": "island/textures/rock.png"}),         # Workspace-relative first.
                ("/Users/alice/Client X/island/hdr/sky.exr", {"file": "island/hdr/sky.exr"}),
                ("//../shared/wood.png", {"file": "shared/wood.png"}),                 # Outside the folder, inside the workspace.
                ("//../../Other Client/logo.png", {"file": "logo.png", "outside": True}),
                ("/Volumes/Projects/ACME/tex.png", {"file": "tex.png", "outside": True}),
                ("C:\\Users\\bob\\Desktop\\tex.png", {"file": "tex.png", "outside": True}),
                ("\\\\server\\share\\tex.png", {"file": "tex.png", "outside": True}),
                ("relative/unknown.png", {"file": "unknown.png", "outside": True}),
                ("//textures/", {"file": "island/textures"})):
            with self.subTest(raw=raw):
                self.assertEqual(paths.public_name(raw, folder, workspace), expected)
        # Without a workspace (or outside it), a file in the .blend's own folder is relative to that folder.
        self.assertEqual(paths.public_name("//textures/rock.png", folder), {"file": "textures/rock.png"})
        self.assertEqual(paths.public_name("//textures/rock.png", folder, "/elsewhere"), {"file": "textures/rock.png"})
        # An unsaved file has no folder: nothing is relative to it.
        self.assertEqual(paths.public_name("//textures/rock.png", "", workspace), {"file": "rock.png", "outside": True})

    @POSIX
    def test_no_public_name_is_absolute_or_climbs(self):
        folder, workspace = "/Users/alice/Client X/island", "/Users/alice/Client X"
        for raw in ("//a.png", "//../a.png", "//../../a.png", "//../../../../etc/passwd", "/etc/passwd", "/a.png",
                    "//sub/../../../x.png", "C:/x.png", "~/x.png", "//./a.png", "/Users/alice/Client X", "//"):
            with self.subTest(raw=raw):
                name = paths.public_name(raw, folder, workspace)["file"]
                self.assertFalse(name.startswith(("/", "~", "..")) or ".." in name.split("/") or ":" in name, name)
                self.assertNotIn("alice", name)

    @POSIX
    def test_state_asks_one_question(self):
        asked = []

        def exists(path):
            asked.append(path)
            return path.endswith("here.png") or path.endswith("tile.1001.png")
        folder = "/proj"
        self.assertEqual(paths.state("//here.png", folder, exists=exists), paths.PRESENT)
        self.assertEqual(paths.state("//gone.png", folder, exists=exists), paths.MISSING)
        self.assertEqual(paths.state("//tile.<UDIM>.png", folder, exists=exists), paths.PRESENT)
        self.assertEqual(paths.state("//here.png", "", exists=exists), paths.UNKNOWN)       # Unsaved file.
        self.assertEqual(paths.state("C:\\a\\b.png", folder, exists=exists), paths.UNKNOWN)  # Another platform.
        self.assertEqual(paths.state("//anything.png", folder, packed=True, exists=exists), paths.PACKED)
        self.assertEqual(asked, ["/proj/here.png", "/proj/gone.png", "/proj/tile.1001.png"])

        def failing(_path):
            raise OSError("stale mount")
        self.assertEqual(paths.state("//x.png", folder, exists=failing), paths.UNKNOWN)


class OpenFileTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("angelo_agent.native_programs.runtime_is_staging", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, bpy, workspace="/Users/alice/Client X/island", **arguments):
        result = blend_file.read(bpy, validate_read("angelo_blend_file", arguments), SimpleNamespace(root=workspace))
        json.dumps(result, allow_nan=False)
        return result

    def document(self):
        library = block("libraries", "props.blend", filepath="//libs/props.blend", packed_file=None, parent=None)
        lost = block("libraries", "gone.blend", filepath="/Volumes/Old/gone.blend", packed_file=None, parent=library)
        rock = block("images", "rock.png", packed_file=None, packed_files=[])
        packed = block("images", "decal", packed_file=object(), packed_files=[])
        sound = block("sounds", "wind", packed_file=None)
        generated = block("images", "generated", packed_file=object(), packed_files=[1], filepath="")
        tree = block("node_groups", "Scatter", nodes=[node("Math"), node("Old", "NodeUndefined", "CUSTOM"),
                                                       node("Group", "GeometryNodeGroup", "GROUP", node_tree=None)])
        material = block("materials", "Rock", node_tree=SimpleNamespace(nodes=[
            node("Inner", "ShaderNodeGroup", "GROUP", node_tree=tree)]))
        bpy = open_file(
            objects=[block("objects", "Island"), block("objects", "Tree", library=library, is_missing=True),
                     block("objects", "Bush", library=library)],
            meshes=[block("meshes", "Island"), block("meshes", "Shard 1.001", users=0),
                    block("meshes", "Kept", users=1, use_fake_user=True)],
            materials=[material, block("materials", "Old Rock", users=0, node_tree=None)],
            images=[rock, packed, generated], sounds=[sound], scenes=[block("scenes", "Scene", users=0)],
            node_groups=[tree], libraries=[library, lost],
            paths={rock: {"//textures/rock.png"}, packed: {"//textures/decal.png"},
                   sound: {"/Users/alice/Music/wind.wav", "<builtin>"}})
        return bpy

    def test_summary_counts_types_and_names_what_has_no_user(self):
        with patch.object(os.path, "exists", lambda path: path.endswith("props.blend")):
            result = self.read(self.document())
        self.assertEqual((result["readable"], result["scope"], result["saved"], result["file"]),
                         (True, "OPEN_FILE", True, "island.blend"))
        types = {row["type"]: row for row in result["datablocks"]}
        self.assertEqual(types["meshes"], {"type": "meshes", "count": 3, "unused": 1, "kept_by_fake_user": 1})
        self.assertEqual(types["objects"], {"type": "objects", "count": 3, "linked": 2})
        self.assertEqual(types["scenes"], {"type": "scenes", "count": 1}, "A scene without users is not unused")
        self.assertEqual(result["unused"], {"total": 2, "items": [{"type": "meshes", "name": "Shard 1.001"},
                                                                  {"type": "materials", "name": "Old Rock"}]})
        self.assertIn("kept_by_fake_user", result["unused_rule"])

    def test_libraries_files_and_unresolved_references(self):
        with patch.object(os.path, "exists", lambda path: path.endswith("props.blend")):
            result = self.read(self.document())
        self.assertEqual(result["libraries"]["items"], [
            {"name": "props.blend", "file": "libs/props.blend", "status": "present", "datablocks": 2},
            # A macOS path cannot be located on Windows, so there the library's status is unknown, not missing.
            {"name": "gone.blend", "file": "gone.blend", "outside": True,
             "status": "unknown" if os.name == "nt" else "missing", "datablocks": 0, "indirect": True}])
        files = result["external_files"]
        # The sound's macOS path cannot be located on Windows either: there it is unknown, so one file is missing.
        sound_status = "unknown" if os.name == "nt" else "missing"
        self.assertEqual((files["total"], files["missing"], files["packed"]), (4, 1 if os.name == "nt" else 2, 2))
        # Blender's path walk leaves out packed files; one packed image here has no path at all.
        self.assertEqual(files["items"][-1], {"type": "images", "name": "generated", "status": "packed"})
        self.assertEqual(files["items"][0], {"type": "images", "name": "rock.png", "file": "textures/rock.png",
                                             "status": "missing"})
        self.assertIn({"type": "sounds", "name": "wind", "file": "wind.wav", "outside": True, "status": sound_status},
                      files["items"])
        self.assertEqual(files["items"][-2], {"type": "images", "name": "decal", "file": "textures/decal.png",
                                              "status": "packed"})
        kinds = [(row["kind"], row["name"]) for row in result["unresolved"]["items"]]
        # A library whose macOS path cannot be located on Windows is not known to be missing there.
        library = [] if os.name == "nt" else [("MISSING_LIBRARY", "gone.blend")]
        self.assertEqual(kinds, [("MISSING_LINKED_DATA", "Tree")] + library +
                         [("UNDEFINED_NODE", "Scatter"), ("GROUP_NODE_WITHOUT_GROUP", "Scatter")])

    def test_no_absolute_path_reaches_the_answer(self):
        bpy = self.document()
        with patch.object(os.path, "exists", lambda path: False):
            answers = [self.read(bpy)] + [self.read(bpy, list=part) for part in ("UNUSED", "FILES", "LIBRARIES",
                                                                              "UNRESOLVED", "images", "libraries")]
        text = json.dumps(answers)
        for leak in ("alice", "Client X", "/Users", "/Volumes", "Music"):
            self.assertNotIn(leak, text)

    def test_parts_and_types_are_paged(self):
        bpy = open_file(meshes=[block("meshes", "Mesh %03d" % index, users=0) for index in range(70)])
        summary = self.read(bpy)
        self.assertEqual((summary["unused"]["total"], len(summary["unused"]["items"]), summary["truncated"]),
                         (70, blend_file.SUMMARY_ROWS, True))
        self.assertIn("UNUSED", summary["note"])
        page = self.read(bpy, list="UNUSED", offset=64, limit=64)
        self.assertEqual((len(page["items"]), page["next_offset"], page["truncated"]), (6, None, False))
        meshes = self.read(bpy, list="meshes", limit=2)
        self.assertEqual(meshes["items"], [{"name": "Mesh 000", "users": 0}, {"name": "Mesh 001", "users": 0}])
        self.assertEqual((meshes["total"], meshes["next_offset"], meshes["truncated"]), (70, 2, True))
        unknown = self.read(bpy, list="vertices")
        self.assertEqual((unknown["readable"], unknown["code"]), (False, "UNKNOWN_LIST"))
        self.assertIn("meshes", unknown["types"])

    def test_bounds_are_reported_never_silent(self):
        bpy = open_file(meshes=[block("meshes", "Mesh %d" % index, users=0) for index in range(40)])
        with patch.object(blend_file, "MAX_DATABLOCKS", 10):
            result = self.read(bpy)
        self.assertEqual((result["incomplete"], result["truncated"]), (["datablocks"], True))
        image = block("images", "a", packed_file=None, packed_files=[])
        bpy = open_file(images=[image], paths={image: {"//%d.png" % index for index in range(6)}})
        with patch.object(blend_file, "MAX_FILE_CHECKS", 2), patch.object(os.path, "exists", lambda path: True):
            result = self.read(bpy)
        self.assertEqual((result["external_files"]["present"], result["external_files"]["unknown"]), (2, 4))
        self.assertEqual(result["incomplete"], ["external_files"])

    def test_an_unsaved_file_and_a_resident_stage(self):
        result = self.read(open_file(folder=""))
        self.assertEqual((result["saved"], "file" in result), (False, False))
        with patch("angelo_agent.native_programs.runtime_is_staging", lambda: True):
            staged = self.read(self.document())
        self.assertEqual((staged["readable"], staged["code"]), (False, "STAGING"))


class FakeLibraries:
    """``bpy.data.libraries.load`` standing in for Blender's reader: scripted names, failures and calls."""

    def __init__(self, names=None, error=None, before_exit=None):
        self.names, self.error, self.before_exit, self.calls = names or {}, error, before_exit, []

    @contextlib.contextmanager
    def load(self, path, link=False):
        self.calls.append((path, link))
        if self.error:
            raise self.error
        source = SimpleNamespace(version=(5, 2, 45), libraries=[
            SimpleNamespace(filepath="//libs/props.blend", is_archive=False),
            SimpleNamespace(filepath="/Volumes/Old/gone.blend", is_archive=False)], **self.names)
        yield source, SimpleNamespace()
        if self.before_exit:
            self.before_exit()


class FakeMetadata:
    """The native descriptor contract, with no library loading or hidden pathname open."""

    def __init__(self, libraries):
        self.libraries, self.calls = libraries, []
        self.before_scan = None
        self.status = "OK"

    def metadata_cancel_token(self, _seconds):
        return threading.Event()

    def metadata_cancel(self, token):
        token.set()

    def metadata_scan(self, descriptor, request, limits, token):
        opened = os.fstat(descriptor)
        self.calls.append((descriptor, opened.st_ino, dict(request)))
        if self.before_scan:
            self.before_scan(descriptor, token)
        status = "CANCELLED" if token.is_set() else "INVALID" if self.libraries.error else self.status
        types = {}
        if status == "OK":
            for name, entries in self.libraries.names.items():
                offset = request["offset"] if request["list"] else 0
                count = request["limit"] if request["list"] else request["summary_names"]
                types[name] = {"total": len(entries), "names": entries[offset:offset + count]}
        if self.libraries.before_exit:
            self.libraries.before_exit()
        return {"status": status, "types": types, "version": (5, 2, 45),
                "libraries": ["//libs/props.blend", "/Volumes/Old/gone.blend"],
                "decoded_bytes": opened.st_size, "input_bytes": opened.st_size, "blocks": 4, "passes": 2}


@unittest.skipUnless(os.name == "posix", "anchored POSIX files; the Windows backend has its own suite")
class WorkspaceFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve() / "project"
        (self.root / "stages").mkdir(parents=True)
        self.outside = Path(self.folder.name).resolve() / "outside.blend"
        for path in (self.root / "stages" / "s8.blend", self.outside):
            path.write_bytes(b"BLENDER-v502" + b"\0" * 64)
        self.workspace = FileWorkspace(self.root)
        self.libraries = FakeLibraries({"objects": ["Island"] + ["Pine %d" % index for index in range(20)],
                                        "meshes": ["Island"], "texts": []})
        self.bpy = SimpleNamespace(data=SimpleNamespace(libraries=self.libraries))
        self.metadata = FakeMetadata(self.libraries)
        helper = patch("angelo_agent.blend_file_listing.scanner_api", lambda: self.metadata)
        helper.start()
        self.addCleanup(helper.stop)

    def read(self, file, **arguments):
        result = blend_file_listing.read(self.bpy, validate_read("angelo_blend_file", dict(file=file, **arguments)),
                                         self.workspace)
        text = json.dumps(result, allow_nan=False)
        self.assertNotIn(self.folder.name, text, "No answer carries an absolute path")
        self.assertNotIn(str(self.root), text)
        return result

    def test_names_and_counts_without_linking(self):
        result = self.read("stages/s8.blend")
        self.assertEqual((result["readable"], result["scope"], result["file"], result["blender_version"]),
                         (True, "WORKSPACE_FILE", "stages/s8.blend", "5.2.45"))
        self.assertEqual(result["datablocks"][0], {"type": "meshes", "count": 1, "names": ["Island"]})
        objects = result["datablocks"][1]
        self.assertEqual((objects["count"], len(objects["names"]), objects["more"]), (21, 12, 9))
        self.assertEqual(result["libraries"], [{"file": "stages/libs/props.blend"}, {"file": "gone.blend", "outside": True}])
        self.assertEqual(self.libraries.calls, [], "No library context is entered")
        self.assertEqual(len(self.metadata.calls), 1)
        with self.assertRaises(OSError):
            os.fstat(self.metadata.calls[0][0])
        page = self.read("stages/s8.blend", list="objects", offset=16, limit=64)
        self.assertEqual((page["total"], len(page["items"]), page["next_offset"]), (21, 5, None))
        self.assertEqual(self.read("stages/s8.blend", list="UNUSED")["code"], "UNKNOWN_LIST")

    def test_fifo_is_rejected_before_open_or_native_scan(self):
        os.mkfifo(self.root / "pipe.blend")
        with patch.object(blend_file_listing, "_open_file") as opened:
            result = self.read("pipe.blend")
        self.assertEqual(result["code"], "NOT_A_FILE")
        opened.assert_not_called()
        self.assertEqual(self.metadata.calls, [])

    def test_files_outside_the_workspace_are_answers_not_reads(self):
        (self.root / "link.blend").symlink_to(self.outside)
        (self.root / "folder-link").symlink_to(self.outside.parent, target_is_directory=True)
        (self.root / ".hidden.blend").write_bytes(b"BLENDER")
        hard = self.root / "hard.blend"
        os.link(self.outside, hard)
        for file, code in (("../outside.blend", "OUTSIDE_WORKSPACE"), (str(self.outside), "OUTSIDE_WORKSPACE"),
                           ("link.blend", "OUTSIDE_WORKSPACE"), ("folder-link/outside.blend", "OUTSIDE_WORKSPACE"),
                           (".hidden.blend", "OUTSIDE_WORKSPACE"), ("stages/../../outside.blend", "OUTSIDE_WORKSPACE"),
                           ("hard.blend", "NOT_A_FILE"), ("stages", "NOT_BLEND"), ("stages/missing.blend", "NOT_FOUND"),
                           ("nowhere/s8.blend", "NOT_FOUND"), ("notes.txt", "NOT_BLEND")):
            with self.subTest(file=file):
                result = self.read(file)
                self.assertEqual((result["readable"], result["code"]), (False, code))
        self.assertEqual(self.libraries.calls, [], "Blender never opened any of them")
        self.assertEqual(self.metadata.calls, [])

    def test_files_that_are_not_blends_are_answers(self):
        (self.root / "text.blend").write_bytes(b"this is not a blend file at all")
        (self.root / "empty.blend").write_bytes(b"")
        for file in ("text.blend", "empty.blend"):
            self.assertEqual(self.read(file)["code"], "NOT_BLEND")
        self.assertEqual(self.libraries.calls, [])
        with patch.object(blend_file_listing, "MAX_BLEND_BYTES", 16):
            self.assertEqual(self.read("stages/s8.blend")["code"], "TOO_LARGE")
        for magic in (b"\x28\xb5\x2f\xfdrest", b"\x1f\x8brest"):
            (self.root / "packed.blend").write_bytes(magic)
            self.assertTrue(self.read("packed.blend")["readable"])

    def test_a_damaged_blend_is_an_answer_without_blenders_path(self):
        self.libraries.error = OSError("Error: Failed to read blend file '%s': Missing DNA block"
                                       % (self.root / "stages" / "s8.blend"))
        result = self.read("stages/s8.blend")
        self.assertEqual((result["readable"], result["code"], result["bytes"]), (False, "UNREADABLE", 76))
        self.assertIn("damaged", result["message"])

    def test_a_file_swapped_during_the_read_is_discarded(self):
        target = self.root / "stages" / "s8.blend"

        def swap():
            target.unlink()
            target.write_bytes(b"BLENDER-v502" + b"\1" * 64)
        self.libraries.before_exit = swap
        result = self.read("stages/s8.blend")
        self.assertEqual((result["readable"], result["code"]), (False, "CHANGED"))
        self.assertNotIn("datablocks", result)

    def test_swap_back_cannot_redirect_the_admitted_descriptor(self):
        target = self.root / "stages" / "s8.blend"
        original = target.stat().st_ino
        observed = []
        backup = target.with_suffix(".held")
        def swap_back(descriptor, _token):
            target.rename(backup)
            target.symlink_to(self.outside)
            observed.append(os.fstat(descriptor).st_ino)
            target.unlink()
            backup.rename(target)
        self.metadata.before_scan = swap_back
        result = self.read("stages/s8.blend")
        self.assertEqual(observed, [original])
        self.assertTrue(result["readable"] or result["code"] == "CHANGED")
        self.assertNotIn("outside.blend", json.dumps(result))

    def test_in_place_write_with_restored_mtime_is_rejected_by_ctime(self):
        target = self.root / "stages" / "s8.blend"
        before = target.stat()
        def change(_descriptor, _token):
            target.write_bytes(b"BLENDER-v502" + b"\2" * 64)
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.metadata.before_scan = change
        self.assertEqual(self.read("stages/s8.blend")["code"], "CHANGED")

    def test_budgets_cancellation_and_missing_helper_are_distinct_answers(self):
        for status in ("LIMIT_EXCEEDED", "TIME_LIMIT", "CANCELLED"):
            self.metadata.status = status
            result = self.read("stages/s8.blend")
            self.assertEqual((result["readable"], result["code"]), (False, status))
            self.assertNotIn("damaged", result["message"])
            self.assertNotIn("datablocks", result)
        with patch("angelo_agent.blend_file_listing.scanner_api", lambda: None):
            self.assertEqual(self.read("stages/s8.blend")["code"], "NATIVE_READER_REQUIRED")

    def test_no_workspace(self):
        result = blend_file_listing.read(self.bpy, validate_read("angelo_blend_file", {"file": "a.blend"}), None)
        self.assertEqual((result["readable"], result["code"]), (False, "NO_WORKSPACE"))


if __name__ == "__main__":
    unittest.main()
