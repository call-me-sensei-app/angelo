# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Child-process decoder for ``view_exr_codecs``: named channels of one OpenEXR part through OpenImageIO.

Run by the bundled interpreter as ``python -I view_exr_worker.py <path> <part> <json names> <max pixels>``; never
imported by Angelo (the library must not load into the application). Only NumPy and OpenImageIO are used.
It writes one JSON line, then, on success, the requested channels as little-endian float32 rows (top row first),
one plane after another in the order asked. Every failure is a code from ``view_exr_codecs.CODES``; the library's own
text (which can hold paths) is never passed on. The size is checked before any pixel is read.

Values are the file's own, as ``view_exr`` reads them: HALF and FLOAT channels as they are, and a UINT channel (object
and material IDs) as its integer converted to float32. The library would otherwise scale an integer channel to 0..1
when asked for floats, so each UINT channel is read as integers. Channels next to each other are read in one call,
so a pass of four channels decodes its blocks once.
"""

import json
import sys


def answer(**fields):
    sys.stdout.buffer.write(json.dumps(fields).encode("ascii") + b"\n")


def _plane(reader, part, begin, end, kind, height, width):
    """The channels ``begin`` to ``end`` as float32 planes (height, width); ValueError for a short read."""
    import numpy
    pixels = reader.read_image(part, 0, begin, end, kind)
    if pixels is None or pixels.size != (end - begin) * height * width:
        raise ValueError("short read")
    pixels = numpy.asarray(pixels).reshape(height, width, end - begin)
    return [numpy.ascontiguousarray(pixels[..., index], dtype="<f4") for index in range(end - begin)]


def decode(path, part, names, max_pixels):
    try:
        import numpy
        import OpenImageIO as oiio
    except ImportError:
        return {"ok": False, "error": "import"}
    oiio.attribute("limits:imagesize_MB", 1024)
    oiio.attribute("limits:channels", 1024)
    oiio.attribute("threads", 4)
    reader = oiio.ImageInput.open(path)
    if reader is None:
        return {"ok": False, "error": "open"}
    try:
        if not reader.seek_subimage(part, 0):
            return {"ok": False, "error": "subimage"}
        spec = reader.spec()
        if spec.deep:
            return {"ok": False, "error": "deep"}
        width, height = spec.width, spec.height
        if width < 1 or height < 1 or width * height > max_pixels:
            return {"ok": False, "error": "size"}
        found = list(spec.channelnames)
        if any(name not in found for name in names):
            return {"ok": False, "error": "channel"}
        formats = list(spec.channelformats) or [spec.format] * len(found)
        indexes = [found.index(name) for name in names]
        decoded = {}
        for index in indexes:   # An integer channel is read as integers; the rest in runs of neighbours below.
            if index not in decoded and str(formats[index]) == "uint":
                decoded[index] = _plane(reader, part, index, index + 1, "uint", height, width)[0]
        others = sorted(index for index in set(indexes) if index not in decoded)
        run_start = 0
        while run_start < len(others):
            run_end = run_start
            while run_end + 1 < len(others) and others[run_end + 1] == others[run_end] + 1 \
                    and str(formats[others[run_end + 1]]) != "uint":
                run_end += 1
            first, last = others[run_start], others[run_end]
            for offset, plane in enumerate(_plane(reader, part, first, last + 1, "float", height, width)):
                decoded[first + offset] = plane
            run_start = run_end + 1
        return {"ok": True, "width": width, "height": height, "planes": [decoded[index] for index in indexes]}
    except Exception:  # A library fault on a damaged file is a refusal, not a traceback.
        return {"ok": False, "error": "read"}
    finally:
        reader.close()


def main(argv):
    try:
        path, part, names, max_pixels = argv[1], int(argv[2]), json.loads(argv[3]), int(argv[4])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError
    except (IndexError, ValueError):
        answer(ok=False, error="read")
        return 0
    result = decode(path, part, names, max_pixels)
    planes = result.pop("planes", ())
    answer(**result)
    for plane in planes:
        sys.stdout.buffer.write(plane.tobytes())
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
