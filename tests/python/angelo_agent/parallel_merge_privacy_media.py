# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parts for ``test_blender_parallel_merge_privacy`` (BREAKIT-C7A-5): what a worker's files carry besides what
Blender reads, what a text transport makes of a private path, and a long text.

- ``write_media``: images whose bytes hold the part's path in text chunks (tEXt, and zTXt compressed), as a render's
  stamp does: one larger than the check's raw-array limit, one small, one the worker packed; a 16-bit image (a float
  buffer, held in scene linear) and a data one; a file that is not audio loaded as a sound; a font with bytes after
  its tables (as Blender's own fonts are WOFF2, which refuses them: a copy), and one packed under Blender's own font's
  name; an image packed, then made generated with its private
  path (BREAKIT-C7A-6), which the person edits and saves. Each image must merge as its own pixels and nothing else;
  the sound's file and the fonts never.
- ``write_encoded``: an ID property spelling a state-folder path form-encoded (a space as "+"), or as UTF-16 bytes:
  nothing can cut it in place, so the merge must refuse with nothing added.
- ``write_long``: a text of many lines that name Angelo (as every text in the data folder's path does) with one
  private path: cut, in bounded time.
"""

import hashlib
import os
import struct
import time
import zlib
from pathlib import Path
from urllib.parse import urlencode

import bpy
import numpy

LONG_LINES = 100000
LONG_SECONDS = 30.0   # Generous: the part merged in about 3 s on Mac 2 and took 74 s before (BREAKIT-C7A-5 L1).


def png_with_text(path, size, text, stored=False):
    """An 8-bit RGBA PNG of ``size`` squared whose text chunks hold ``text``; ``stored``: its pixels not compressed,
    so the file is larger than the check's raw-array limit (1 MiB) at 720 pixels."""
    y, x = numpy.mgrid[0:size, 0:size]
    pixels = numpy.stack([(x * 7) % 256, (y * 5) % 256, (x + y) % 256, numpy.full_like(x, 255)], -1).astype(numpy.uint8)
    rows = b"".join(b"\0" + row.tobytes() for row in pixels.reshape(size, size * 4))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    written = text.encode("utf-8")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                     + chunk(b"tEXt", b"File\0" + written) + chunk(b"zTXt", b"Note\0\0" + zlib.compress(written))
                     + chunk(b"IDAT", zlib.compress(rows, 0 if stored else 6)) + chunk(b"IEND", b""))
    return path


def deep_png(path, data):
    """A 16-bit PNG (Blender reads it as a float buffer, in scene linear), tagged as data when ``data``."""
    image = bpy.data.images.new(path.stem, 4, 4, alpha=True, float_buffer=True)
    image.pixels.foreach_set([0.25, 0.5, 0.75, 1.0, 0.1, 0.9, 0.3, 1.0] * 8)
    settings = bpy.context.scene.render.image_settings
    settings.file_format, settings.color_depth = "PNG", "16"
    image.save_render(str(path))
    bpy.data.images.remove(image)
    loaded = bpy.data.images.load(str(path), check_existing=False)
    if data:
        loaded.colorspace_settings.name = "Non-Color"
    return loaded


def digest(image):
    pixels = numpy.empty(image.size[0] * image.size[1] * 4, dtype=numpy.float32)
    image.pixels.foreach_get(pixels)
    return hashlib.sha1(pixels.tobytes()).hexdigest()


IMAGES = ("render.png", "stamp.png", "packed.png", "deep.png", "data.png")
FONT_BYTES = []


def write_media(part, folder, quad, textured, secret):
    """The fifth part; returns each image's pixel digest as the worker had it."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    stamp = str(part)   # What a render's stamp writes: the open file's path, in a private folder.
    images = [bpy.data.images.load(str(png_with_text(folder / "render.png", 720, stamp, stored=True))),
              bpy.data.images.load(str(png_with_text(folder / "stamp.png", 16, stamp))),
              bpy.data.images.load(str(png_with_text(folder / "packed.png", 8, stamp))),
              deep_png(folder / "deep.png", False), deep_png(folder / "data.png", True)]
    images[2].pack()   # The worker's own bytes, packed by the worker.
    for image in images[3:]:
        image.name = Path(image.filepath).name
    media = bpy.data.objects.new("Media", quad("MediaMesh", [textured("Media " + image.name, image)
                                                             for image in images]))
    bpy.context.scene.collection.objects.link(media)
    notes = folder / "notes.json"
    notes.write_text('{"api_key": "%s"}' % secret)
    speaker = bpy.data.objects.new("Media Speaker", bpy.data.speakers.new("MediaSpeaker"))
    speaker.data.sound = bpy.data.sounds.load(str(notes))   # Never read as audio: a sound only by its loader.
    bpy.context.scene.collection.objects.link(speaker)
    fonts = Path(bpy.utils.system_resource("DATAFILES", path="fonts"))
    source = next(p for p in sorted(fonts.iterdir()) if p.suffix.lower() in (".ttf", ".otf", ".woff2"))
    tailed = folder / ("tailed" + source.suffix)
    data = source.read_bytes()
    tailed.write_bytes(data)
    try:   # Bytes after its tables, read by no one, where its format takes them.
        tailed.write_bytes(data + secret.encode())
        bpy.data.fonts.remove(bpy.data.fonts.load(str(tailed), check_existing=False))
    except RuntimeError:
        tailed.write_bytes(data)
    FONT_BYTES[:] = [data[len(data) // 2:len(data) // 2 + 64]]   # Never in the person's file.
    builtin = bpy.data.fonts.load(str(tailed), check_existing=False)
    builtin.pack()
    builtin.filepath = "<builtin>"   # Blender's own font's name over a worker's packed bytes.
    for name, font in (("Media Tailed", bpy.data.fonts.load(str(tailed), check_existing=False)),
                       ("Media Builtin", builtin)):
        label = bpy.data.objects.new(name, bpy.data.curves.new(name, "FONT"))
        label.data.font = font
        bpy.context.scene.collection.objects.link(label)
    made = bpy.data.images.load(str(png_with_text(folder / "made.png", 8, stamp)))
    made.pack()
    made.source = "GENERATED"
    made.filepath_raw = str(folder / "made.png")   # Its private path, kept by a generated image.
    media.data.materials.append(textured("Media made", made))
    expected = {image.name: digest(image) for image in images}
    bpy.ops.wm.save_mainfile(compress=False)
    return expected


def media_checks(check, answer, saved, leaks, secret, expected, marker):
    check("media_merged", answer.get("success") is True, answer)
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    data = Path(saved).read_bytes()
    check("media_saved_file_holds_nothing_private", not leaks(saved) and secret.encode() not in data, leaks(saved))
    found = {name: bpy.data.images.get(name) for name in IMAGES}
    check("media_images_packed_as_their_own_pixels", all(
        image is not None and len(image.packed_files) == 1 and digest(image) == expected[name]
        and marker not in image.filepath_raw for name, image in found.items()),
        {name: (bool(image and image.packed_file), image and digest(image) == expected[name],
                getattr(image, "filepath_raw", None)) for name, image in found.items()})
    check("media_float_buffers_stay_linear_or_data",
          found["deep.png"] is not None and not found["deep.png"].colorspace_settings.is_data
          and found["data.png"] is not None and found["data.png"].colorspace_settings.is_data,
          {name: getattr(found[name], "colorspace_settings", None) and found[name].colorspace_settings.name
           for name in ("deep.png", "data.png")})
    fonts = [bpy.data.objects[name].data.font for name in ("Media Tailed", "Media Builtin") if name in bpy.data.objects]
    check("media_worker_fonts_never_packed", len(fonts) == 2 and not any(font.packed_file for font in fonts)
          and all(marker not in font.filepath.lower() for font in fonts) and FONT_BYTES[0] not in data,
          [(font.name, font.filepath, bool(font.packed_file)) for font in fonts])
    made = bpy.data.images.get("made.png")
    if made is not None:   # The person edits it and saves modified images: repacked, with its own path only.
        pixels = numpy.empty(made.size[0] * made.size[1] * 4, dtype=numpy.float32)
        made.pixels.foreach_get(pixels)
        pixels[0] = 1.0 - pixels[0]
        made.pixels.foreach_set(pixels)
        made.update()
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            bpy.ops.image.save_all_modified()
        bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    check("media_edited_image_repacked_with_its_own_path", made is not None and made.packed_file is not None
          and not leaks(saved) and all(marker not in (file.filepath or "").lower() for file in made.packed_files),
          (getattr(made, "filepath_raw", None), [file.filepath for file in made.packed_files] if made else None,
           leaks(saved)))
    sound = bpy.data.sounds.get("notes.json")
    check("media_non_audio_file_never_packed", sound is not None and not sound.packed_file
          and marker not in sound.filepath.lower(), getattr(sound, "filepath", None))
    also = answer.get("also_added") or {}
    check("media_answer_names_packed_and_detached", set(IMAGES) <= set(also.get("packed", []))
          and "notes.json" in also.get("detached", []), also)


def reopened_media_checks(check, expected):
    """After the saved file is opened again: each image reads its own pixels from what was packed."""
    found = {name: bpy.data.images.get(name) for name in IMAGES}
    check("reopened_media_images_read_their_pixels", all(
        image is not None and digest(image) == expected[name] for name, image in found.items()),
        {name: image is not None and digest(image) == expected[name] for name, image in found.items()})


def write_encoded(part, quad, path, how):
    """A part spelling ``path`` (in the state folder) as a text transport does (``how``: "plus", form-encoded in a
    URL's query, or "wide", UTF-16 bytes): nothing can cut it in place."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    encoded = bpy.data.objects.new("Encoded " + how, quad("Encoded%sMesh" % how))
    bpy.context.scene.collection.objects.link(encoded)
    if how == "plus":
        encoded["query"] = "https://example.invalid/view?" + urlencode({"file": str(path)})
    else:
        encoded["wide"] = str(path).encode("utf-16-le")
    bpy.ops.wm.save_mainfile(compress=False)


def write_long(part, quad, folder):
    """A part whose Text has ``LONG_LINES`` lines naming Angelo, one with a private path."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    log = bpy.data.texts.new("Long Log")
    lines = ["frame %d: angelo state of the scene, workspace ok" % index for index in range(LONG_LINES)]
    lines[LONG_LINES // 2] = "cache at %s" % os.path.join(str(folder), "long.json")
    log.from_string("\n".join(lines))
    holder = bpy.data.objects.new("Long", quad("LongMesh"))
    holder["log"] = log
    bpy.context.scene.collection.objects.link(holder)
    bpy.ops.wm.save_mainfile(compress=False)


def timed(merge):
    started = time.perf_counter()
    answer = merge()
    return answer, time.perf_counter() - started


def long_checks(check, answer, seconds, saved, leaks):
    check("long_merged", answer.get("success") is True, answer)
    log = bpy.data.texts.get("Long Log")
    lines = log.as_string().split("\n") if log else []
    check("long_text_keeps_the_file_name", len(lines) == LONG_LINES and lines[LONG_LINES // 2] == "cache at long.json",
          lines[LONG_LINES // 2] if len(lines) > LONG_LINES // 2 else len(lines))
    check("long_text_merges_in_bounded_time", seconds < LONG_SECONDS, round(seconds, 2))
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    check("long_saved_file_holds_nothing_private", not leaks(saved), leaks(saved))
