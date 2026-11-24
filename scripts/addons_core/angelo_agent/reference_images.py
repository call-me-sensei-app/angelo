# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading a conversation's picture by its reference id, checked on the descriptor read (2.1.0). No Blender here.

``ReferenceStore.attached_image`` finds the file by its id: a canonical UUID, this folder's own regular PNG, JPEG or
WebP file under the image limit, never a link. The file can change between that check and an open, so every check is
made again on the descriptor that is read (``opened``): opened through ``shared_replace`` (sharing delete on Windows,
so a reader never refuses the writer's replace), never following a link at the name and never waiting on a FIFO
(``O_NOFOLLOW | O_NONBLOCK``, POSIX), a regular file by its ``fstat``, at most the image limit, and read to at most one
byte past it. The live session page (``session_page_pending``), an app's picture (``external_look``) and an app's views
(``external_visibility``) all read through here (break-it review of e0fc461e471, NIT-2 and NIT-3).

Errors: ``Refused`` (a ValueError) for what is never this conversation's picture (``references.attached_image``'s
refusal, a file that is no regular file, one over the limit); ``reference_errors.ImageMissing`` (a ValueError) while
there is no such file; ``OSError`` for a look at the disk that failed (a sharing violation, a share's reconnect, a link
swapped in). Any other exception is a defect (break-it review of b4dbd6b3c78, NIT-4).

Not every by-path use of a picture goes through here: ``view_compare.reference`` reads a header and ``worker_share``
links or copies the file, both by ``attached_image``'s path (NIT-5). On Windows a link swapped in at the name is
followed; the ``fstat`` checks still apply.
"""

import contextlib
import os
import stat


class Refused(ValueError):
    """Never this conversation's picture while it stays as it is: dropped by its readers, never waited on."""


FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


@contextlib.contextmanager
def opened(store, reference_id):
    """``(path, status, handle)`` for ``reference_id``'s picture in ``store`` (a ``ReferenceStore``): ``status`` the
    open descriptor's ``fstat``, ``handle`` a binary file to ``read`` from. Closed on leaving."""
    from . import shared_replace
    from .reference_errors import ImageMissing
    try:
        path = store.attached_image(reference_id)
    except ImageMissing:
        raise
    except ValueError as refused:   # Not a UUID, not this folder's own regular picture, over the limit.
        raise Refused(str(refused)) from refused
    with shared_replace.open_file(path, FLAGS) as handle:
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise Refused("That picture is no regular file")
        if status.st_size > store.image_limit:
            raise Refused("That picture is larger than an attachment may be")
        yield path, status, handle


def read(store, handle):
    """The picture's bytes from ``opened``'s ``handle``: at most the image limit (a file grown past it is refused)."""
    data = handle.read(store.image_limit + 1)
    if len(data) > store.image_limit:
        raise Refused("That picture is larger than an attachment may be")
    return data


def restat(handle):
    """The open descriptor's ``fstat`` again, after a read: unchanged size and time mean the bytes are one file's."""
    return os.fstat(handle.fileno())


def read_image(store, reference_id):
    """``(bytes, path)`` of ``reference_id``'s picture, checked and read on one descriptor."""
    with opened(store, reference_id) as (path, _status, handle):
        return read(store, handle), path
