# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``Image.angelo_source_revision`` in Angelo's own RNA: it changes when what an image's pixels are loaded FROM
changes (reload, path, packing, a file changed on disk and read again after its buffers were freed) and never on
reading, re-reading an unchanged source, freeing buffers, an undo push or a pixel edit (BKE_angelo_image_source.hh).

Run in Angelo, in background (``--factory-startup``), through ``run_native_suites.py``::

    python3 tests/python/angelo_agent/run_native_suites.py --binary <Angelo> --include test_blender_image_source_revision

Readers tell a build that has the revision from one that has not by the property itself (it is new: no older build
has it); this suite pins that it exists, is read-only and is not animatable. Writes ``image-source-revision.json``
in ``ANGELO_QA_DIR``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-image-source-revision-qa"))
OUT.mkdir(parents=True, exist_ok=True)
FOLDER = Path(tempfile.mkdtemp(prefix="angelo-image-source-"))

checks, observed = {}, {}
report = {"success": False, "checks": checks, "observed": observed}


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


def write_png(path, size, grey):
    """A PNG written by Blender itself (a scratch generated image saved, then removed)."""
    scratch = bpy.data.images.new("scratch", size, size, alpha=True)
    scratch.pixels.foreach_set([grey, grey, grey, 1.0] * (size * size))
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)


def read(image):
    """Read a pixel: loads the image when nothing is cached."""
    return round(image.pixels[0], 2)


def rev(image):
    return image.angelo_source_revision


def run():
    prop = bpy.types.Image.bl_rna.properties.get("angelo_source_revision")
    check("capability: Image.angelo_source_revision exists", prop is not None)
    check("capability: read-only and not animatable", prop is not None and prop.is_readonly and not prop.is_animatable)

    support = bpy.types.Image.bl_rna.properties.get("angelo_source_loads_supported")
    check("capability: load support is read-only and not animatable",
          support is not None and support.is_readonly and not support.is_animatable)

    a, b = FOLDER / "a.png", FOLDER / "b.png"
    write_png(a, 4, 0.2)
    write_png(b, 4, 0.8)
    image = bpy.data.images.load(str(a))
    other = bpy.data.images.load(str(b))
    read(other)
    check("descriptor-backed load supported", other.angelo_source_loads_supported)
    opaque = bpy.data.images.new("opaque-decoder", 4, 4)
    opaque.source = "MOVIE"
    check("opaque movie source unsupported", not opaque.angelo_source_loads_supported)
    bpy.data.images.remove(opaque)
    other_start = rev(other)

    first = read(image)
    start = rev(image)
    for _ in range(3):
        read(image)
        image.update()  # Marks the pixels changed for the GPU: a pixel revision, not a source one.
    check("reading and GPU updates leave it", rev(image) == start, (start, rev(image)))

    for _ in range(3):
        image.buffers_free()
        read(image)
    check("re-reading an unchanged file after freeing its buffers leaves it", rev(image) == start)

    try:
        bpy.ops.ed.undo_push(message="image source revision")
        bpy.ops.ed.undo_push(message="image source revision 2")
        observed["undo_push"] = "ok"
    except RuntimeError as error:
        observed["undo_push"] = str(error)
    check("an undo push runs", observed["undo_push"] == "ok", observed["undo_push"])
    check("an undo push leaves it", rev(image) == start)
    name = image.name
    bpy.context.scene.render.resolution_x += 1
    bpy.ops.ed.undo_push(message="image source revision: an unrelated edit")
    bpy.ops.ed.undo()
    image = bpy.data.images[name]  # Re-fetched: an undo step may re-read the datablock.
    check("undoing an unrelated edit leaves it", rev(image) == start, (start, rev(image)))
    read(image)
    check("re-reading after the undo leaves it", rev(image) == start)

    pixel_start = image.angelo_pixel_revision
    pixels = list(image.pixels)
    pixels[0] = 1.0 - pixels[0]
    image.pixels.foreach_set(pixels)
    check("a pixel write is the pixel revision's, not the source's",
          image.angelo_pixel_revision != pixel_start and rev(image) == start)
    image.reload()
    after_reload = rev(image)
    check("reload changes it", after_reload != start)
    check("the reload's own load reads the same file: unchanged", read(image) == first and rev(image) == after_reload)

    # A file touched or rewritten on disk: nothing changes until something reads it again.
    write_png(a, 8, 0.6)
    check("a changed file that nothing read leaves it", rev(image) == after_reload)
    image.buffers_free()
    check("freeing buffers leaves it", rev(image) == after_reload)
    value = read(image)
    changed = rev(image)
    check("the changed file read after freeing changes it", changed != after_reload and value == 0.6, (value, changed))
    image.buffers_free()
    read(image)
    check("reading the same changed file again leaves it", rev(image) == changed)

    image.filepath = str(b)
    check("a new path changes it", rev(image) != changed and read(image) == 0.8)
    image.filepath = str(a)
    read(image)

    # Packed: packing changes it; re-reading the packed bytes does not; unpacking changes it.
    before_pack = rev(image)
    image.pack()
    packed = rev(image)
    check("packing changes it", packed != before_pack)
    for _ in range(2):
        image.buffers_free()
        read(image)
    check("re-reading packed bytes leaves it", rev(image) == packed, (packed, rev(image)))
    check("packed bytes supported", image.angelo_source_loads_supported)
    image.unpack(method="USE_ORIGINAL")
    check("unpacking changes it", rev(image) != packed)
    check("unpacking removed the packed source", not image.packed_files)
    check("unpacking invalidates packed load evidence", not image.angelo_source_loads_supported)
    unpacked = rev(image)
    image.buffers_free()
    read(image)
    check("reading the unpacked file settles its source", rev(image) == unpacked)
    check("the unpacked file has descriptor evidence", image.angelo_source_loads_supported)

    # Generated: its pixels come from its settings, never a file; re-generating them leaves it.
    generated = bpy.data.images.new("generated", 8, 8)
    read(generated)
    generated_start = rev(generated)
    for _ in range(2):
        generated.buffers_free()
        read(generated)
    check("a generated image re-generated leaves it", rev(generated) == generated_start)

    check("another image's changes leave this one", rev(other) == other_start)

    seen = {rev(image), rev(other), rev(generated)}
    repeated = []
    for _ in range(4):
        bpy.data.images.remove(image)
        image = bpy.data.images.load(str(a))
        for step in ("new", "reload"):
            if step == "reload":
                image.reload()
            repeated += [rev(image)] if rev(image) in seen else []
            seen.add(rev(image))
    check("values never repeat across removed and new images", not repeated, repeated)
    observed["values_seen"] = len(seen)


try:
    run()
    report["success"] = bool(checks) and all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    print(report["error"], flush=True)
finally:
    (OUT / "image-source-revision.json").write_text(json.dumps(report, indent=2))
    print("RESULT", "PASS" if report["success"] else "FAIL", flush=True)
