# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The text of an attached PDF, read by the bundled pypdf in a child of the bundled interpreter.

Angelo bundles pypdf (BSD-3-Clause, pure Python; ``vendor/`` holds PyPI's wheel unmodified, renamed ``.pyz``
because the repository keeps ``.whl`` files in Git LFS; ``release/license/pdf-reader`` has its license and
SHA-256), so a PDF's text reaches the agent without any program the person must install. A PDF is untrusted
input: anything a person attaches. pypdf therefore never runs in Angelo itself: ``pdf_text_worker.py`` runs it in
a child process with ``-I -S`` (no site-packages, so neither an installed pypdf nor any other add-on's is ever
used), a short allow-list of environment variables (never the application's provider keys), and these limits:

* before parser import or file opening, Linux requires its fixed hard address-space ceiling and Windows a
  committed-memory Job ceiling; Darwin instead waits for parent admission under a sampled physical-footprint
  termination threshold (NOT a hard allocation ceiling: overshoot and unsampled peaks are possible); accounting
  failure or observed threshold crossing refuses extraction. See pdf_darwin_watchdog for this policy tradeoff;
* stdout is bounded to the JSON character limit plus metadata and stderr is discarded;
* the file is at most ``MAX_BYTES`` (checked again before parsing; attaching already refuses a larger one);
* at most ``MAX_PAGES`` pages and ``MAX_CHARS`` characters are read;
* the worker stops reading pages after ``BUDGET`` seconds and answers with what it has, and the child is killed
  at ``SECONDS`` whatever it does (``subprocess.TimeoutExpired``), so one pathological page can't hang a read;
* pypdf's own limits on decoded streams, images, arrays and parsing work are tightened (``pdf_text_worker.LIMITS``)
  against decompression bombs and huge content streams, deep nesting ends as a parse failure, and no external
  program (``jbig2dec``) is started.

A child is also killable, never holds Blender's interpreter lock, and a crash in it is only a failed read.
Model-facing text is English (``CODES``); the person's reason for each code is ``references``' (its chip).
"""

import json
import os
import sys
from pathlib import Path

from . import pdf_darwin_watchdog, pdf_process_limits, pdf_process_output

VENDOR = Path(__file__).with_name("vendor")
READER = VENDOR / "pypdf-6.21.2-py3-none-any.pyz"
WORKER = Path(__file__).with_name("pdf_text_worker.py")
PYTHON = None              # The interpreter that runs the worker; None finds the bundled one. Tests set it.
MAX_BYTES = 20 * 1024 * 1024   # The limit on any non-image attachment (references.ReferenceStore.import_images).
MAX_PAGES = 64
MAX_CHARS = 1000000        # As for a text attachment (references._file_text).
BUDGET = 20                # Seconds of page reading before the worker answers with the pages it has.
MEMORY_BYTES = 1024 * 1024 * 1024  # Hard AS/Job ceiling on Linux/Windows; sampled footprint trigger on Darwin.
SECONDS = 30               # The child's hard deadline.
ENVIRONMENT = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")

# What the worker says went wrong -> what the model is told (never pypdf's own text, which can hold paths).
CODES = {
    "encrypted": "This PDF is encrypted or password-protected, so its text couldn't be extracted; ask for an "
                 "unprotected copy or page images",
    "empty": "This PDF contains no extractable text; attach page images for visual reference",
    "size": "This PDF is larger than the 20 MiB a reference file may be",
    "limit": "This PDF exceeds the PDF reader's safety limits, so its text couldn't be extracted; attach readable "
             "text or page images",
    "open": "Couldn't extract this PDF's text; attach readable text or page images",
    "resource": "The PDF reader cannot establish its required resource policy on this runtime; attach readable "
                "text or page images instead",
    "read": "Couldn't extract this PDF's text; attach readable text or page images",
}
MISSING_ERROR = "This build of Angelo has no PDF reader; attach text or page images instead"


class Unreadable(ValueError):
    """The file's own failure (cached by ``ReferenceStore``): the model's message, its ``code`` one of ``CODES``."""

    def __init__(self, code):
        self.code = code if code in CODES else "read"
        super().__init__(CODES[self.code])


def interpreter():
    """The bundled interpreter (``sys.prefix``'s, as ``view_exr_codecs`` finds it), or None."""
    if PYTHON is not None:
        return str(PYTHON)
    name = "python.exe" if os.name == "nt" else "python%d.%d" % sys.version_info[:2]
    found = Path(sys.prefix) / "bin" / name
    return str(found) if found.is_file() else None


def available():
    """Whether this install can read PDF text: the interpreter, the worker and the bundled reader are present."""
    return interpreter() is not None and WORKER.is_file() and READER.exists()


def command(path):
    return [interpreter(), "-I", "-S", str(WORKER), str(READER), str(path), str(MAX_BYTES), str(MAX_PAGES),
            str(MAX_CHARS), str(BUDGET), str(MEMORY_BYTES)]


def extract(path):
    """``path``'s text. ``Unreadable`` for the file's own failure; ``OSError`` when the reader can't run (not the
    file's failure); ``subprocess.TimeoutExpired`` past ``SECONDS``. Blocks: never on Blender's main thread."""
    if not available():
        raise OSError(MISSING_ERROR)
    import subprocess
    try:
        output = pdf_process_output.run(
            command(path), memory_bytes=MEMORY_BYTES, output_bytes=6 * MAX_CHARS + 4096, timeout=SECONDS,
            env={name: os.environ[name] for name in ENVIRONMENT if name in os.environ})
    except pdf_darwin_watchdog.FootprintExceeded:
        raise Unreadable("limit") from None
    except (pdf_process_limits.ResourceLimitUnavailable, pdf_darwin_watchdog.AccountingUnavailable):
        raise Unreadable("resource") from None
    except pdf_process_output.OutputLimitExceeded:
        raise Unreadable("limit") from None
    try:
        answer = json.loads(output.stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        answer = None
    if output.returncode or not isinstance(answer, dict):
        raise Unreadable("open")   # A crash or a damaged answer: this file's read failed.
    if answer.get("ok") is not True:
        if answer.get("code") == "import":
            raise OSError(MISSING_ERROR)
        raise Unreadable(str(answer.get("code")))
    text, read, pages = str(answer.get("text", ""))[:MAX_CHARS], int(answer.get("read", 0)), int(answer.get("pages", 0))
    if not text.strip():
        if answer.get("stopped") == "time" and read == 0:
            raise subprocess.TimeoutExpired(command(path), SECONDS)   # No page finished in time.
        raise Unreadable("empty")
    if read < pages:   # Said to the model, so a partial read is never taken for the whole document.
        text += "\n\n[Angelo read pages 1-%d of this PDF's %d; the rest was not extracted.]" % (read, pages)
    return text
