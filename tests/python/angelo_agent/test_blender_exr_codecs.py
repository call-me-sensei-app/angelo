# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every OpenEXR codec this build writes is read by Angelo's view reader, with files Blender itself wrote.

Run with run_native_suites.py --include test_blender_exr_codecs. In an isolated background app this writes a known
picture with each codec and bit depth the build offers (and a small multilayer render with Depth, Normal and
Cryptomatte passes), reads each back through ``view_exr`` (its own decoder for NONE, RLE, ZIPS and ZIP, the child
OpenImageIO decoder of ``view_exr_codecs`` for the rest) and checks the pixels three ways: against the known picture
for lossless codecs, against Blender's own loader for every codec, and, for the multilayer file, against the same
render saved with ZIP. It then reads the committed fixtures (``exr_fixtures/``, also written by Blender), feeds a
few hundred damaged copies to the reader (it must refuse or read, never raise anything else, hang or take the app
down), checks a path with spaces and non-ASCII letters, and times a 1080p file. No network, model or provider.

To rewrite the committed fixtures with a build (they are plain Blender output, stored as ``.exrbin`` so Git does
not treat them as LFS ``.exr`` files):

    Angelo --background --factory-startup --python tests/python/angelo_agent/test_blender_exr_codecs.py -- --write-fixtures
"""

import collections
import json
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import time
import traceback

import bpy
import numpy

assert bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup process"
ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import view_exr, view_exr_codecs, view_passes  # noqa: E402

WRITE_FIXTURES = "--write-fixtures" in sys.argv
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="exr-codecs-")).resolve()
OUT.mkdir(parents=True, exist_ok=True)
FIXTURES = HERE / "exr_fixtures"
WIDTH, HEIGHT = 37, 21     # Not a multiple of any codec's block (16, 32 or 256 lines; tiles of 16).
CODECS = ("NONE", "RLE", "ZIPS", "ZIP", "PIZ", "PXR24", "B44", "B44A", "DWAA", "DWAB", "HTJ2K")
NATIVE = frozenset({"NONE", "RLE", "ZIPS", "ZIP"})                  # Read by view_exr itself.
EXACT_HALF = frozenset({"NONE", "RLE", "ZIPS", "ZIP", "PIZ", "PXR24", "HTJ2K"})   # Lossless for half-float data.
EXACT_FLOAT = frozenset({"NONE", "RLE", "ZIPS", "ZIP", "PIZ", "HTJ2K"})           # Lossless for 32-bit floats.
LOSSY = {"B44": 0.1, "B44A": 0.1, "DWAA": 0.02, "DWAB": 0.02, "PXR24": 1e-3}   # Largest error / largest value.
FIXTURE_PLAIN = {"16": CODECS, "32": ("NONE", "PIZ", "PXR24", "HTJ2K")}
# Multilayer renders, by bit depth. Each depth is compared with its own ZIP save: a half-float save of the Depth
# pass turns Cycles' no-hit distance (1e10) into infinity, which a 32-bit save does not.
LAYER_CODECS = {"32": ("ZIP", "PIZ", "PXR24", "DWAA", "DWAB", "HTJ2K"), "16": ("ZIP", "B44", "B44A")}
report = {"checks": {}, "codecs": {}, "binary": bpy.app.binary_path, "success": False, "paid_requests": 0}


def check(name, condition, *, defer=False):
    report["checks"][name] = bool(condition)
    if not condition and not defer:
        raise AssertionError(name)


def truth():
    """The known picture, top row first: values k/8 (exact in half floats, below 2048 steps) in R, G and B, A one."""
    ramp = numpy.arange(WIDTH * HEIGHT, dtype=numpy.float32).reshape(HEIGHT, WIDTH) / 8.0
    return {"R": ramp, "G": ramp + 1.0, "B": ramp * 0.5, "A": numpy.ones_like(ramp)}


def settings_for(scene, file_format, depth, codec):
    image_settings = scene.render.image_settings
    if hasattr(image_settings, "media_type"):   # Blender 5: the multilayer format belongs to its own media type.
        image_settings.media_type = "MULTI_LAYER_IMAGE" if file_format == "OPEN_EXR_MULTILAYER" else "IMAGE"
    image_settings.file_format = file_format
    image_settings.color_mode = "RGBA"
    image_settings.color_depth = depth
    image_settings.exr_codec = codec


def offered(scene, depth, codec):
    try:
        settings_for(scene, "OPEN_EXR", depth, codec)
    except (TypeError, ValueError):
        return False
    return scene.render.image_settings.exr_codec == codec and scene.render.image_settings.color_depth == depth


def write_plain(scene, path, depth, codec, size=(WIDTH, HEIGHT)):
    width, height = size
    image = bpy.data.images.new("exr_codecs_plain", width, height, alpha=True, float_buffer=True)
    try:
        if size == (WIDTH, HEIGHT):
            planes = truth()
        else:
            ramp = numpy.random.default_rng(5).random((height, width), dtype=numpy.float32)
            planes = {"R": ramp, "G": ramp * 2, "B": ramp * 0.25, "A": numpy.ones_like(ramp)}
        rgba = numpy.stack([planes[name] for name in "RGBA"], axis=-1)
        image.pixels.foreach_set(numpy.ascontiguousarray(rgba[::-1]).ravel())   # Blender's rows start at the bottom.
        settings_for(scene, "OPEN_EXR", depth, codec)
        image.save_render(str(path), scene=scene)
    finally:
        bpy.data.images.remove(image)
    return planes


def blender_pixels(path):
    """The four channels as Blender's own loader decodes the file, top row first (the independent second opinion)."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        buffer = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(buffer)
        rgba = buffer.reshape(height, width, 4)[::-1]
        return {name: numpy.ascontiguousarray(rgba[..., index]) for index, name in enumerate("RGBA")}
    finally:
        bpy.data.images.remove(image)


def read_all(path):
    """``{(part name, channel): rows}`` of every channel of every part, through the reader under test."""
    with open(path, "rb") as handle:
        parts, _multipart = view_exr.read_parts(handle)
    found = {}
    for part in parts:
        names = [entry[0] for entry in part.channels]
        for start in range(0, len(names), 32):   # A view asks for the few channels of one pass, never all of a big file.
            for name, rows in view_exr.read_channels(path, part.index, names[start:start + 32]).items():
                found[(part.name, name)] = rows
    return found, parts


def worst(first, second):
    """Largest absolute difference of two pictures, ignoring places where both are not finite numbers."""
    both = numpy.isfinite(first) & numpy.isfinite(second)
    if not numpy.array_equal(numpy.isfinite(first), numpy.isfinite(second)):
        return float("inf")
    return float(numpy.abs(first[both] - second[both]).max()) if both.any() else 0.0


def plain_matrix(scene, folder):
    """Each codec and depth the build offers: the known picture, Blender's own decode, and who decoded it."""
    for depth in ("16", "32"):
        for codec in CODECS:
            key = "%s-bit %s" % (depth, codec)
            if not offered(scene, depth, codec):
                report["codecs"][key] = {"offered": False}
                continue
            path = folder / ("plain%s_%s.exr" % (depth, codec))
            expected = write_plain(scene, path, depth, codec)
            started = time.perf_counter()
            found, parts = read_all(path)
            seconds = time.perf_counter() - started
            own = blender_pixels(path)
            entry = {"offered": True, "bytes": path.stat().st_size, "native": parts[0].native,
                     "seconds": round(seconds, 4), "parts": len(parts)}
            check(key + " is read by the expected decoder", parts[0].native == (codec in NATIVE))
            check(key + " has the channels written", sorted(name for _part, name in found) == ["A", "B", "G", "R"])
            channels = {name: rows for (_part, name), rows in found.items()}
            exact = codec in (EXACT_HALF if depth == "16" else EXACT_FLOAT)
            error = max(worst(channels[name], expected[name]) for name in "RGBA")
            entry["error_vs_known_picture"] = error
            if exact:
                check(key + " equals the known picture", error == 0.0)
            else:
                limit = LOSSY.get(codec, 0.0) * float(expected["R"].max())
                check(key + " is within its lossy bound", error <= limit)
            entry["differs_from_blenders_decode"] = max(worst(channels[name], own[name]) for name in "RGBA")
            check(key + " equals Blender's own decode", entry["differs_from_blenders_decode"] == 0.0)
            report["codecs"][key] = entry
    check("the build offers every codec at 16 bits",
          all(report["codecs"].get("16-bit " + codec, {}).get("offered") for codec in CODECS))


def render_layers(scene):
    """A tiny render with Depth, Normal and Cryptomatte passes (Cycles on the CPU when the build has it)."""
    if hasattr(scene, "cycles"):
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
    else:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = WIDTH, HEIGHT, 100
    layer = scene.view_layers[0]
    layer.use_pass_z = layer.use_pass_normal = layer.use_pass_cryptomatte_object = True
    bpy.ops.render.render()
    return next(image for image in bpy.data.images if image.type == "RENDER_RESULT")


def relative_error(found, reference, lossy):
    """Largest difference to the reference over all channels, as a fraction of each channel's largest finite value.

    Cryptomatte channels hold hashes stored as floats, not pictures: a lossy codec may change them, so they are
    compared only for a lossless one.
    """
    worst_error = 0.0
    for name in found:
        if lossy and ".Crypto" in name[1]:
            continue
        top = float(numpy.abs(numpy.where(numpy.isfinite(reference[name]), reference[name], 0.0)).max())
        worst_error = max(worst_error, worst(found[name], reference[name]) / max(top, 1.0))
    return worst_error


def check_against_zip(key, codec, depth, found, reference):
    check(key + " has the same channels as the ZIP save", set(found) == set(reference))
    error = relative_error(found, reference, not (codec in EXACT_FLOAT and depth == "32"))
    if codec in EXACT_FLOAT and depth == "32":
        check(key + " equals the ZIP save", error == 0.0)
    else:
        check(key + " is within its lossy bound of the ZIP save", error <= (1e-3 if codec == "PXR24" else 0.1))
    return error


def multilayer(scene, folder):
    result = render_layers(scene)
    references, saved = {}, {}
    for depth, codecs in LAYER_CODECS.items():
        for codec in codecs:
            key = "multilayer %s-bit %s" % (depth, codec)
            if not offered(scene, depth, codec):
                report["codecs"][key] = {"offered": False}
                check(key + " is offered", codec not in ("PIZ", "DWAA", "HTJ2K", "B44"))
                continue
            settings_for(scene, "OPEN_EXR_MULTILAYER", depth, codec)
            path = folder / ("layers%s_%s.exr" % (depth, codec))
            result.save_render(str(path), scene=scene)
            started = time.perf_counter()
            found, parts = read_all(path)
            entry = {"offered": True, "bytes": path.stat().st_size, "seconds": round(time.perf_counter() - started, 4),
                     "parts": len(parts), "channels": len(found), "native": all(part.native for part in parts)}
            saved[(depth, codec)] = path
            if codec == "ZIP":
                references[depth] = found
                entry["passes"] = sorted({view_passes.split_channel(name)[1] for _part, name in found})[:12]
                check(key + " holds Depth, Normal and Cryptomatte",
                      {"Depth", "Normal"} <= set(entry["passes"])
                      and any(name.startswith("Crypto") for name in entry["passes"]))
            else:
                entry["relative_error_vs_zip"] = check_against_zip(key, codec, depth, found, references[depth])
            catalogue = view_passes.catalogue(str(path))
            check(key + " lists the passes", any(pair[1] == "Depth" for pair in catalogue))
            report["codecs"][key] = entry
    return saved


def fixtures(folder, layers):
    """Write (``--write-fixtures``) or read back the committed files; the same checks run on both."""
    if WRITE_FIXTURES:
        FIXTURES.mkdir(exist_ok=True)
        for depth, codecs in FIXTURE_PLAIN.items():
            for codec in codecs:
                source = folder / ("plain%s_%s.exr" % (depth, codec))
                if source.is_file():
                    shutil.copyfile(source, FIXTURES / ("plain%s_%s.exrbin" % (depth, codec)))
        for (depth, codec), source in layers.items():
            shutil.copyfile(source, FIXTURES / ("layers%s_%s.exrbin" % (depth, codec)))
        write_tiled(FIXTURES / "tiled32_ZIP.exrbin")
    expected, names = truth(), []
    scratch = folder / "fixture-copies"
    scratch.mkdir()
    for fixture in sorted(FIXTURES.glob("*.exrbin")):
        copy = scratch / (fixture.stem + ".exr")
        shutil.copyfile(fixture, copy)
        found, parts = read_all(copy)
        names.append(fixture.name)
        if fixture.name.startswith(("plain", "tiled")):
            channels = {name: rows for (_part, name), rows in found.items()}
            error = max(worst(channels[name], expected[name]) for name in "RGB")
            codec, tiled = fixture.stem.split("_", 1)[1], fixture.name.startswith("tiled")
            if tiled or codec in EXACT_HALF:
                check("fixture %s equals the known picture" % fixture.name, error == 0.0)
            else:
                check("fixture %s is within its bound" % fixture.name, error <= LOSSY[codec] * float(expected["R"].max()))
            check("fixture %s is read by the expected decoder" % fixture.name,
                  parts[0].native == (codec in NATIVE and not tiled))
        else:
            check("fixture %s lists its passes" % fixture.name, len(view_passes.catalogue(str(copy))) >= 3)
    check("the committed fixtures exist", len(names) >= len(CODECS))
    for depth in LAYER_CODECS:
        reference = scratch / ("layers%s_ZIP.exr" % depth)
        check("the %s-bit multilayer ZIP fixture exists" % depth, reference.is_file())
        zipped = read_all(reference)[0]
        for copy in sorted(scratch.glob("layers%s_*.exr" % depth)):
            codec = copy.stem.split("_", 1)[1]
            if codec != "ZIP":
                check_against_zip("fixture " + copy.name, codec, depth, read_all(copy)[0], zipped)
    report["fixtures"] = names
    return scratch


def write_tiled(path):
    """A tiled file, which Blender does not write: OpenImageIO makes it (its own fixture kind)."""
    import OpenImageIO as oiio
    planes = truth()
    spec = oiio.ImageSpec(WIDTH, HEIGHT, 3, "float")
    spec.channelnames = ("R", "G", "B")
    spec.tile_width, spec.tile_height, spec.tile_depth = 16, 16, 1
    spec["compression"] = "zip"
    scratch = Path(tempfile.mkdtemp(dir=OUT)) / "tiled.exr"   # The library picks its writer by the .exr suffix.
    out = oiio.ImageOutput.create(str(scratch))
    assert out.open(str(scratch), spec), out.geterror()
    assert out.write_image(numpy.stack([planes[name] for name in "RGB"], axis=-1)), out.geterror()
    out.close()
    shutil.copyfile(scratch, path)


def mutate(data, rng, round_index):
    data = bytearray(data)
    kind = round_index % 6
    if kind == 0:
        return bytes(data[:rng.randrange(0, len(data))])
    if kind == 1:   # The header.
        for _ in range(rng.randrange(1, 17)):
            data[rng.randrange(0, min(len(data), 512))] = rng.randrange(256)
    elif kind == 2:  # The pixel data.
        for _ in range(rng.randrange(1, 33)):
            data[rng.randrange(len(data) // 2, len(data))] = rng.randrange(256)
    elif kind == 3:  # A block of zeros or ones.
        start = rng.randrange(0, len(data) - 1)
        length = rng.randrange(1, min(512, len(data) - start))
        data[start:start + length] = bytes([rng.choice((0, 255))]) * length
    elif kind == 4:  # The offset table region and just after the header.
        for _ in range(rng.randrange(1, 9)):
            data[rng.randrange(len(data) // 4, len(data) // 2 + 8)] = rng.randrange(256)
    else:            # Extra bytes.
        data += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64)))
    return bytes(data)


def fuzz(folder, scratch):
    """Damaged copies of fixtures that go to the library: each is read or refused, and the app goes on."""
    rng, outcomes, unexpected = random.Random(20261021), collections.Counter(), []
    old = view_exr_codecs.TIMEOUT
    view_exr_codecs.TIMEOUT = 20
    target = folder / "fuzz.exr"
    try:
        for source in sorted(scratch.glob("*.exr")):
            if source.name.startswith(("plain16_NONE", "plain16_ZIP", "plain32_NONE")):
                continue   # Their decoder is the reader's own (fuzzed by test_native_view_exr).
            data = source.read_bytes()
            for index in range(24):
                target.write_bytes(mutate(data, rng, index))
                try:
                    read_all(target)
                except view_exr.ExrError as error:
                    text = str(error)
                    outcomes["timed out" if "took over" in text else
                             "decoder stopped" if "stopped while reading" in text else "refused"] += 1
                except Exception as error:   # Anything but a refusal is a defect, whatever its type.
                    unexpected.append("%s: %s %s" % (source.name, type(error).__name__, str(error)[:80]))
                else:
                    outcomes["read"] += 1
    finally:
        view_exr_codecs.TIMEOUT = old
    report["fuzz"] = {"outcomes": dict(outcomes), "unexpected": unexpected[:10]}
    check("damaged files are read or refused and nothing else escapes the reader", not unexpected)
    check("no damaged file took longer than the decode limit", outcomes.get("timed out", 0) == 0)


def unusual_path(folder, scratch):
    """The child gets the path as one argument whatever it holds."""
    awkward = folder / "folder with spaces ünï-çödé 日本"
    awkward.mkdir()
    source = next(path for path in sorted(scratch.glob("plain16_PIZ*.exr")))
    target = awkward / "a b ü.exr"
    shutil.copyfile(source, target)
    found, parts = read_all(target)
    check("a path with spaces and non-ASCII letters is read by the child", not parts[0].native and len(found) == 4)


def integer_channels_and_mixed_parts(folder):
    """Two decoders must agree on what a channel holds, and a part the library reads must not hide the next one.

    OpenImageIO writes the files (Blender writes neither integer channels nor a different codec per part).
    """
    import OpenImageIO as oiio
    ids = [0, 1, 2, 3, 1000, 70000, 4000000000]
    results = {}
    for codec in ("zip", "piz", "pxr24", "dwaa", "b44"):
        spec = oiio.ImageSpec(7, 1, 3, "float")
        spec.channelnames, spec.channelformats = ("F", "ID", "G"), ("float", "uint", "half")
        spec["compression"] = codec
        buf = oiio.ImageBuf(spec)
        for x in range(7):
            buf.setpixel(x, 0, 0, (x + 0.5, ids[x] / 4294967295.0, x * 2.0))
        path = folder / ("ids_%s.exr" % codec)
        assert buf.write(str(path)), buf.geterror()
        results[codec] = view_exr.read_channels(path, 0, ["ID", "F", "G"])
    for codec, found in results.items():
        check("integer channel values read through %s equal the own decoder's" % codec,
              all(numpy.array_equal(found[name], results["zip"][name]) for name in found))
    check("an integer channel holds its integers, not a 0..1 scale", results["piz"]["ID"][0].tolist() == [float(i) for i in ids])
    specs = []
    for index, codec in enumerate(("piz", "zip", "dwaa", "zip")):
        spec = oiio.ImageSpec(8, 5, 1, "float")
        spec.channelnames, spec["compression"], spec["name"] = ("Z",), codec, "part%d" % index
        specs.append(spec)
    path = folder / "mixed_parts.exr"
    writer = oiio.ImageOutput.create(str(path))
    assert writer.open(str(path), specs), writer.geterror()
    for index, spec in enumerate(specs):
        if index:
            assert writer.open(str(path), spec, "AppendSubimage"), writer.geterror()
        assert writer.write_image(numpy.full((5, 8, 1), index + 1.0, numpy.float32)), writer.geterror()
    writer.close()
    with open(path, "rb") as handle:
        parts, _multipart = view_exr.read_parts(handle)
    check("each part of a mixed-codec file knows who decodes it", [part.native for part in parts] == [False, True, False, True])
    check("every part of a mixed-codec file reads its own pixels",
          all(float(view_exr.read_channels(path, index, ["Z"])["Z"][0, 0]) == index + 1.0 for index in range(4)))


def child_peak_rss():
    """The peak resident size of this process's finished children, where the platform reports it (POSIX getrusage);
    None on Windows, which has no resource module. A receipt figure only: no check reads it."""
    try:
        import resource
    except ImportError:
        return None
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def memory(folder):
    """The application holds the channels once: the traced peak of a read against its output (a review found 4.6x)."""
    import tracemalloc
    import OpenImageIO as oiio
    width, height, channels = 6000, 4000, 3
    spec = oiio.ImageSpec(width, height, channels, "float")
    spec.channelnames, spec["compression"] = ("B", "G", "R"), "piz"
    path = folder / "big_zeros.exr"
    writer = oiio.ImageOutput.create(str(path))
    assert writer.open(str(path), spec), writer.geterror()
    block = numpy.zeros((32, width, channels), numpy.float32)
    for y in range(0, height, 32):
        rows = min(32, height - y)
        assert writer.write_scanlines(y, y + rows, 0, block[:rows]), writer.geterror()
    writer.close()
    output = width * height * channels * 4
    before = child_peak_rss()
    tracemalloc.start()
    try:
        values = view_exr.read_channels(path, 0, ["R", "G", "B"])
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    report["memory"] = {"output_mb": output >> 20, "application_traced_peak_mb": peak >> 20,
                        "child_max_rss_before": before, "child_max_rss_after": child_peak_rss()}
    check("the application's peak is about its output", peak < output * 1.5 and values["R"].shape == (height, width))


def large_file(scene, folder):
    """A 1080p picture: the child's cost against the reader's own, for the receipt (not a pass/fail speed claim)."""
    size = (1920, 1080)
    timings = {}
    for codec in ("ZIP", "PIZ", "DWAA", "HTJ2K"):
        if not offered(scene, "16", codec):
            continue
        path = folder / ("large_%s.exr" % codec)
        write_plain(scene, path, "16", codec, size)
        started = time.perf_counter()
        values = view_exr.read_channels(path, 0, ["R"])
        timings[codec] = {"seconds": round(time.perf_counter() - started, 3), "bytes": path.stat().st_size,
                          "shape": list(values["R"].shape)}
        check("1080p %s reads whole" % codec, values["R"].shape == (size[1], size[0]))
        path.unlink()
    report["timings_1080p_one_channel"] = timings


def scenario():
    scene = bpy.context.scene
    check("the bundled interpreter that runs the child decoder exists", view_exr_codecs.interpreter() is not None)
    import subprocess
    probe = subprocess.run([view_exr_codecs.interpreter(), "-I", "-c", "import numpy, OpenImageIO; print(OpenImageIO.__version__)"],
                           capture_output=True, text=True, timeout=60)
    report["openimageio"] = probe.stdout.strip() or probe.stderr.strip()[-200:]
    check("the bundled interpreter imports NumPy and OpenImageIO", probe.returncode == 0)
    with tempfile.TemporaryDirectory(prefix="exr-codecs-", dir=OUT) as temporary:
        folder = Path(temporary)
        plain_matrix(scene, folder)
        layers = multilayer(scene, folder)
        scratch = fixtures(folder, layers)
        fuzz(folder, scratch)
        unusual_path(folder, scratch)
        integer_channels_and_mixed_parts(folder)
        memory(folder)
        large_file(scene, folder)


try:
    scenario()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
    if not report["success"]:
        raise AssertionError("a check failed: " + ", ".join(name for name, ok in report["checks"].items() if not ok))
except BaseException:
    report["error"] = traceback.format_exc()
    raise
finally:
    (OUT / "exr-codecs.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf8")
    print("exr-codecs: success=%s checks=%d" % (report["success"], len(report["checks"])))
