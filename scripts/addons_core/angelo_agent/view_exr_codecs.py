# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""OpenEXR parts ``view_exr`` does not decode itself, read by OpenImageIO in a child process.

Production OpenEXR files use PIZ, PXR24, B44, DWAA/DWAB and HTJ2K compression and are often tiled. Decoding those
needs the libraries Blender already ships (OpenImageIO over OpenEXR and OpenJPH), which Angelo's bundle carries:
nothing new is added to the installer. A file is untrusted input and a decoder bug in native code would stop
Angelo itself, so the library never runs in the application: ``view_exr_worker.py`` runs in a child of the bundled
interpreter (``sys.prefix``), with a time limit, and answers with the requested channels only. A crash, a hang,
a refusal or a damaged answer is an ``ExrError`` and the application goes on. No library is imported here.

Model-facing text is English and names no path: the worker answers with a code, and the message comes from here.
The child gets a short allow-list of environment variables, never the application's (which can hold provider keys),
and its answer is read straight into one buffer, so the application holds the channels once, not four times.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .view_exr import MAX_PIXELS, ExrError

WORKER = Path(__file__).with_name("view_exr_worker.py")
PYTHON = None          # The interpreter that runs the worker; None finds the bundled one. Tests set it.
TIMEOUT = 40           # Seconds a decode may take; the child is killed past it.
MAX_NAMES = 64         # Channels one call asks for.
MAX_OUTPUT = 1 << 30   # Bytes of channel data one call accepts (the worker never writes more than it was told).
MAX_CHUNK = 256 << 20  # Bytes the library holds to decode one block of scanlines (width x lines x every channel).
# Scanlines in a block, by OpenEXR's compression code (view_exr.COMPRESSION_NAMES); a tiled part is not bounded this way.
LINES = {4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256, 10: 256, 11: 32}
SAMPLE_BYTES = {0: 4, 1: 2, 2: 4}   # UINT, HALF, FLOAT.
ENVIRONMENT = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
CODES = {              # What the worker says went wrong -> what a model is told (never the library's own text).
    "open": "the library could not open the file",
    "subimage": "the library found no such part in the file",
    "deep": "the file holds deep data, which a view does not show",
    "size": "the picture is larger than a view reads",
    "channel": "the part lacks a requested channel",
    "read": "the library could not read the pixels",
    "import": "the OpenImageIO library is not available",
}


def interpreter():
    """The bundled interpreter that has OpenImageIO, or None (a build without it)."""
    if PYTHON is not None:
        return str(PYTHON)
    name = "python.exe" if os.name == "nt" else "python%d.%d" % sys.version_info[:2]
    found = Path(sys.prefix) / "bin" / name
    return str(found) if found.is_file() else None


def _what(part, compression):
    return "tiled" if part.kind == "tiledimage" else "compressed with %s" % compression


def _environment():
    return {name: os.environ[name] for name in ENVIRONMENT if name in os.environ}


def _chunk_bytes(part):
    """What the library holds to decode one block of this part's scanlines, or 0 for a tiled part."""
    lines = LINES.get(part.compression, 0) if part.kind != "tiledimage" else 0
    return part.size[0] * lines * sum(SAMPLE_BYTES.get(kind, 4) for _name, kind, *_rest in part.channels)


def decode(path, part, names, compression):
    """``{name: float32 rows (height, width), top row first}`` of ``names`` in ``part``, read by the child decoder.

    ``part`` is a ``view_exr.Part`` the caller has already checked (size, channel types and names). The planes are
    views of one buffer.
    """
    import numpy
    from .child_process import popen
    width, height = part.size
    what = _what(part, compression)
    python = interpreter()
    if python is None or not WORKER.is_file():
        raise ExrError("the file is %s, which a view decodes with OpenImageIO, and this build has no decoder for it" % what)
    if not 0 < len(names) <= MAX_NAMES:
        raise ExrError("a view reads at most %d channels of a %s part at once; %d were asked for" % (MAX_NAMES, what, len(names)))
    wanted = width * height * 4 * len(names)
    if wanted > MAX_OUTPUT:
        raise ExrError("the channels asked for are %d MB: more than a view reads" % (wanted >> 20))
    if _chunk_bytes(part) > MAX_CHUNK:
        raise ExrError("the file is %s and one block of its scanlines is %d MB wide: more than a view decodes"
                       % (what, _chunk_bytes(part) >> 20))
    command = [python, "-I", str(WORKER), str(path), str(part.index), json.dumps(list(names)), str(MAX_PIXELS)]
    try:
        process = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        env=_environment())
    except OSError:
        raise ExrError("the file is %s, and its decoder could not be started" % what) from None
    expired = threading.Event()

    def stop():
        expired.set()
        process.kill()
    timer = threading.Timer(TIMEOUT, stop)
    timer.start()
    buffer, filled, answer = bytearray(wanted), 0, None
    try:
        head = process.stdout.readline(4097)
        try:
            answer = json.loads(head) if head.endswith(b"\n") else None
        except ValueError:
            answer = None
        if isinstance(answer, dict) and answer.get("ok") is True:
            view = memoryview(buffer)
            while filled < wanted:
                got = process.stdout.readinto(view[filled:])
                if not got:
                    break
                filled += got
            view.release()
            extra = process.stdout.read(1) if filled == wanted else b""
        else:
            extra = b""
    finally:
        timer.cancel()
        if process.poll() is None:
            try:
                process.wait(timeout=2)   # A decoder that answered has only to exit.
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        process.stdout.close()
    if expired.is_set():
        raise ExrError("the file is %s and decoding it took over %d s, so it was stopped" % (what, TIMEOUT))
    if not isinstance(answer, dict):
        if process.returncode:
            raise ExrError("the file is %s and its decoder stopped while reading it; the file may be damaged" % what)
        raise ExrError("the file is %s and its decoder gave no usable answer; the file may be damaged" % what)
    if answer.get("ok") is not True:
        raise ExrError("the file is %s and could not be read: %s"
                       % (what, CODES.get(answer.get("error"), "the decoder refused it")))
    if process.returncode:
        raise ExrError("the file is %s and its decoder stopped while reading it; the file may be damaged" % what)
    if answer.get("width") != width or answer.get("height") != height or filled != wanted or extra:
        raise ExrError("the file is %s and its decoder returned a picture of the wrong size" % what)
    planes = numpy.frombuffer(buffer, dtype="<f4").reshape(len(names), height, width)
    return {name: planes[index] for index, name in enumerate(names)}
