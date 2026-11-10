# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Child-process reader for ``pdf_text``: the text of an attached PDF through the bundled pypdf.

Run by the bundled interpreter as ``python -I -S pdf_text_worker.py <reader> <pdf> <max bytes> <max pages>
<max chars> <seconds> <memory bytes>``; never imported by Angelo. ``-I -S`` gives it no site-packages, user site, environment
paths or script folder on ``sys.path``: pypdf comes only from ``<reader>``, the archive Angelo bundles, and a
pypdf imported from anywhere else is refused (``import``). The PDF is untrusted input (anything a person
attaches): its size is checked before parsing, pypdf's own resource limits are tightened (``LIMITS``), no
external program is ever run (pypdf would start ``jbig2dec`` for JBIG2 images), and reading stops at the page,
character and time limits it is given. The parent kills it at its own deadline whatever this does.

It writes one JSON line: ``{"ok": true, "text", "pages", "read", "stopped"}`` or ``{"ok": false, "code"}`` with a
code of ``pdf_text.CODES``. pypdf's own messages (which can hold paths) are never passed on.
"""

import json
import os
import sys
import time

# pypdf's per-stream limits (pypdf.Configuration), tightened for reading text: no decoded stream, image or
# array may reach 32 MB (pypdf allows 75 MB), and no external program runs.
MEGABYTES = 1024 * 1024
LIMITS = {"maximum_declared_stream_length": 32 * MEGABYTES,
          "array_based_stream_maximum_output_length": 32 * MEGABYTES,
          "zlib_maximum_output_length": 32 * MEGABYTES,
          "lzw_maximum_output_length": 32 * MEGABYTES,
          "run_length_maximum_output_length": 32 * MEGABYTES,
          "brotli_maximum_output_length": 32 * MEGABYTES,
          "jbig2_maximum_output_length": 32 * MEGABYTES,
          "image_maximum_buffer_size": 32 * MEGABYTES,
          "stream_decoding_work_maximum_length": 100 * MEGABYTES,
          "jbig2dec_binary": None,
          "disable_legacy_handling": True}


def answer(**fields):
    sys.stdout.buffer.write(json.dumps(fields, ensure_ascii=False).encode("utf-8") + b"\n")


def _load(reader):
    """The pypdf package from ``reader`` alone, or None."""
    sys.path.insert(0, reader)
    try:
        import pypdf
    except Exception:   # noqa: BLE001 - a broken archive is the install's problem, said as one code.
        return None
    origin = os.path.normcase(os.path.abspath(getattr(pypdf.__spec__, "origin", "") or ""))
    if not origin.startswith(os.path.normcase(os.path.abspath(reader)) + os.sep):
        return None   # Not the bundled reader: never read with another.
    return pypdf


def read(reader, path, max_bytes, max_pages, max_chars, seconds):
    """The answer for ``path`` as a dict (see the module docstring)."""
    started = time.monotonic()
    try:
        if os.path.getsize(path) > max_bytes:
            return {"ok": False, "code": "size"}
    except OSError:
        return {"ok": False, "code": "open"}
    pypdf = _load(reader)
    if pypdf is None:
        return {"ok": False, "code": "import"}
    import logging
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)   # A damaged file's warnings are not answers.
    from pypdf.errors import DependencyError, FileNotDecryptedError, LimitReachedError
    pypdf.overwrite_configuration(**LIMITS)
    try:
        document = pypdf.PdfReader(path, strict=False)
        if document.is_encrypted and not document.decrypt(""):
            return {"ok": False, "code": "encrypted"}   # Needs a password the person has not given.
        total = len(document.pages)
    except (DependencyError, FileNotDecryptedError, NotImplementedError):
        return {"ok": False, "code": "encrypted"}   # AES needs the optional ``cryptography``, which is not bundled.
    except LimitReachedError:
        return {"ok": False, "code": "limit"}
    except Exception:   # noqa: BLE001 - any parse failure of untrusted input (deep nesting too) is one code.
        return {"ok": False, "code": "open"}
    parts, length, failed, stopped = [], 0, 0, None
    for index in range(total):
        if index >= max_pages:
            stopped = "pages"
            break
        if time.monotonic() - started > seconds:
            stopped = "time"
            break
        try:
            text = document.pages[index].extract_text() or ""
        except (DependencyError, FileNotDecryptedError):
            return {"ok": False, "code": "encrypted"}
        except Exception:   # noqa: BLE001 - one damaged page never loses the others.
            text, failed = "", failed + 1
        parts.append(text)
        length += len(text) + 2
        if length >= max_chars:
            stopped = "chars"
            break
    if parts and failed == len(parts):
        return {"ok": False, "code": "read"}
    return {"ok": True, "text": "\n\n".join(parts)[:max_chars], "pages": total, "read": len(parts),
            "stopped": stopped}


def admit(memory_bytes):
    # -I -S excludes this script directory. Load only the sibling shipped helper, never PYTHONPATH/site.
    import importlib.util
    name = "pdf_darwin_watchdog" if sys.platform == "darwin" else "pdf_process_limits"
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location("_angelo_" + name, helper)
    limits = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(limits)
    if sys.platform == "darwin":
        limits.await_parent(memory_bytes)
    elif os.name == "nt":
        limits.admit_windows(memory_bytes)
    else:
        limits.admit_address_space(memory_bytes)


def main(argv):
    reader, path, max_bytes, max_pages, max_chars, seconds, memory_bytes = argv[1:8]
    try:
        admit(int(memory_bytes))   # Hard OS admission, or Darwin parent watchdog handshake, before ANY PDF access.
    except (OSError, ValueError, ImportError, MemoryError):
        answer(ok=False, code="resource")
        return 0
    answer(**read(reader, path, int(max_bytes), int(max_pages), int(max_chars), float(seconds)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
