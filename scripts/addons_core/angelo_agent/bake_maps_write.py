# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turning one target's baked arrays into its texture set's files, on a worker thread. numpy and zlib only.

Contract: doc/angelo/long_jobs.rst, "Texture sets". ``write_set`` runs off the
main thread (``job_bake_maps`` submits it), so Angelo stays responsive while
the maps are finished, packed and compressed: the UDIM tiles are stacked so a
range (height, emission) is shared by every tile, the sums become channels
(``bake_maps_pack.finish``, ``prepare``), and each file of the packing
(``bake_maps_spec.files``) is composed band by band and streamed to each tile's
file (``encode_one``), so a file holds a band of pixels, never a whole copy. The
job runs these as units on one shared pool under a memory budget
(``job_bake_maps``); ``write_set`` runs them in turn on one thread. It
never touches Blender data; it writes only into the job's staging folder.
"""

from pathlib import Path

import numpy

from . import bake_maps_encode as encode
from . import bake_maps_pack as pack
from .bake_maps_spec import FORMATS, HIGH_POSITION, LOW_NORMAL, LOW_POSITION, depth_of, file_name, files

WORKERS = 4   # The one encode pool's threads (job_bake_maps): zlib and numpy release the interpreter lock.
# Peak bytes of the units, measured with tracemalloc over every format, depth, channel count, colour, tile count
# and size the tests check (test_native_bake_maps_memory), with about a quarter of headroom. A file: per
# byte of its float band (measured at most 4.9 for PNG, 7.6 for OpenEXR: zlib's output beside the band and its
# planar copy), plus zlib's own state (1.1 MiB at most for a small band).
BAND_FACTOR = {"PNG": 6.0, "OPEN_EXR": 9.5}
ENCODER_BYTES = 3 * 2 ** 19
# A target's preparation's temporaries per texel of the stacked image: measured at most 22 (a high-to-low height).
PREPARE_TEXEL_BYTES = 28


def _stack(value):
    """A sum as one array with the tiles stacked bottom to top, or its constant."""
    if isinstance(value, tuple):
        return value
    return numpy.concatenate([array for _tile, array in value], axis=0) if len(value) > 1 else value[0][1]


def geometric_height(high, low, normal):
    """High to low: the distance along the target's normal from its surface to the sources', in scene units, where
    a source was hit (else 0). Each input keeps its coverage as a fourth component."""
    hit = (high[..., 3] > 0.5) & (low[..., 3] > 0.5)
    normal = normal[..., :3]
    length = numpy.sqrt(numpy.einsum("...i,...i->...", normal, normal))[..., None]
    offset = numpy.subtract(high[..., :3], low[..., :3], dtype=numpy.float32)
    offset *= normal
    height = offset.sum(axis=-1, keepdims=True, dtype=numpy.float32)
    del offset
    height /= numpy.where(length > 1e-8, length, numpy.float32(1.0))   # Along the unit normal.
    height[~hit] = 0.0
    return height


def array_bytes(values):
    """Bytes the arrays in ``values`` (a dict of sums or channels, lists of tiles) hold; constants count nothing.
    Views of one array (a packed bake's components) count that array once, all of it."""
    owners = {}
    for value in values.values():
        for array in [value] if isinstance(value, numpy.ndarray) else [a for _t, a in value] if isinstance(
                value, list) else []:
            while isinstance(array.base, numpy.ndarray):
                array = array.base
            owners[id(array)] = array.nbytes
    return sum(owners.values())


def prepare(work, cancelled=lambda: False):
    """Stack a target's tiles and finish its sums into channels (``bake_maps_pack.finish``); None when stopped.

    ``work``: ``target`` and ``stem`` (its name and file stem), ``tiles`` ([] or UDIM numbers), ``size`` ((w, h)
    of a tile), ``sums`` ({key: RGB tuple or [(tile, array)]}), ``channels``, ``request`` and ``folder``. The sums
    are taken out of ``work`` and let go one by one as they are used, so the arrays a target handed over are freed
    while its channels are made, not after.
    """
    if cancelled():
        return None
    handed, sums = work.pop("sums"), {}
    for key in list(handed):
        sums[key] = _stack(handed.pop(key))
    width, height = work["size"]
    tiles = work["tiles"] or [None]
    shape = (height * len(tiles), width)
    if HIGH_POSITION in sums:
        sums["HEIGHT"] = geometric_height(sums.pop(HIGH_POSITION), sums.pop(LOW_POSITION), sums.pop(LOW_NORMAL))
    request = work["request"]
    image_format = request.get("format", "PNG")
    channels, facts = pack.finish(sums, shape, image_format)
    jobs = [(name, slots, colour, depth_of(name, request))
            for name, slots, colour in files(request["packing"], work["channels"])]
    return {"channels": channels, "facts": facts, "shape": shape, "tiles": tiles, "height": height, "jobs": jobs,
            "stem": work["stem"], "folder": Path(work["folder"]), "format": image_format,
            "extension": FORMATS[image_format]}


def prepare_bytes(work):
    """Bytes ``prepare`` adds at its peak beyond the arrays handed over: its channels where they outgrow the sums
    they are made from (a constant colour over a varying weight becomes an array), and its temporaries, at most
    ``PREPARE_TEXEL_BYTES`` a texel of the stacked image (a UDIM sum stacked, a weight divided, a high-to-low
    height's offsets)."""
    from .bake_maps_spec import WIDTH
    width, height = work["size"]
    texels = width * height * max(1, len(work["tiles"]))
    channels = texels * 4 * sum(WIDTH[channel] for channel in work["channels"])
    return max(0, channels - array_bytes(work["sums"])) + texels * PREPARE_TEXEL_BYTES


def file_bytes(prepared, job):
    """Bytes one file holds while it is composed and encoded (``encode_one``): a band of its pixels and the
    encoder's temporaries (measured with tracemalloc, with headroom), whatever the file's size."""
    _rows, width = prepared["shape"]
    return band_bytes(width, prepared["height"], len(job[1]), prepared["format"])


def widest_file_bytes(work):
    """``file_bytes`` of a target's widest file, known before it is prepared (its size and channels)."""
    width, height = work["size"]
    widest = max(len(slots) for _name, slots, _colour in files(work["request"]["packing"], work["channels"]))
    return band_bytes(width, height, widest, work["request"].get("format", "PNG"))


def band_bytes(width, height, channels, image_format):
    """``file_bytes`` of a file ``width`` wide with ``channels`` components: its band's peak and zlib's state."""
    lines = encode.EXR_ZIP_LINES if image_format == "OPEN_EXR" else 1
    band = min(height, encode.band_rows(width, channels, lines)) * width * channels * 4
    return int(band * BAND_FACTOR[image_format]) + ENCODER_BYTES


class _Stopped(Exception):
    pass


def encode_one(prepared, job, cancelled=lambda: False):
    """Compose one file of a prepared target band by band and stream each tile to its file; its entries. A file
    stopped part way is removed."""
    name, slots, colour, depth = job
    channels, shape, height = prepared["channels"], prepared["shape"], prepared["height"]
    written = []
    for index, tile in enumerate(prepared["tiles"]):
        if cancelled():
            return written
        bottom = index * height   # The tile's rows in the stacked channels, row 0 at the bottom.

        def band(top, count, bottom=bottom):
            if cancelled():
                raise _Stopped
            lo = bottom + height - top - count
            return pack.compose(channels, slots, shape, (lo, lo + count))[::-1]

        filename = file_name(prepared["stem"], name, prepared["extension"], tile)
        path = prepared["folder"] / filename
        try:
            with open(path, "wb") as out:
                encode.write(out, band, (height, shape[1], len(slots)), prepared["format"], depth, colour)
        except _Stopped:
            path.unlink(missing_ok=True)
            return written
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        entry = {"file": filename, "map": name, "depth": depth}
        if tile is not None:
            entry["tile"] = tile
        written.append(entry)
    return written


def write_set(work, cancelled=lambda: False):
    """Write one target's files in this thread (``prepare``, then each file); returns ``(entries, facts)``."""
    prepared = prepare(work, cancelled)
    if prepared is None:
        return [], {}
    entries = [entry for job in prepared["jobs"] for entry in encode_one(prepared, job, cancelled)]
    return entries, prepared["facts"]
