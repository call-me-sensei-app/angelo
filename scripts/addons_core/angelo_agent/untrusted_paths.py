# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which path a downloaded file may name for a reader to open: only a shape that can name nothing but a local file.

A model or texture path inside a downloaded file (an FBX texture, an OBJ material library, a USD asset) is untrusted
text. On Windows merely opening a path that names another computer (``\\\\host\\share``, ``\\\\?\\UNC\\...``, the NT
``\\??\\`` prefix) tries SMB sign-in and sends the person's NTLM hash to that host; a device name (``CON``, ``COM1``,
``LPT¹``) opens a device; a stream (``x.png:stream``) or URL is not a plain file. Rather than list those (re-review
r2 found four the first list missed), ``local_form`` allows exactly the shapes of a local file path:

* relative, or absolute on a drive (``C:/...``, also drive-relative ``C:x``) or from the POSIX root (one ``/``,
  written with forward slashes: a drive-less root in Windows form, ``\\Device\\...`` or ``\\GLOBALROOT\\...``,
  is NT-native and refused);
* no control characters and none of Windows' reserved characters ``< > " | ? *`` (they also spell ``\\\\?\\`` and
  ``\\??\\``); the UDIM tile tokens ``<UDIM>`` and ``<UVTILE>`` are the one use of ``<``/``>`` allowed;
* no colon except a drive letter's;
* no part that is a Windows device name, with or without an extension or trailing dots and spaces;
* no surrounding Unicode whitespace, and its NFKC form passing the same checks;
* no slash or colon lookalike that no normalisation maps (a best-fit code page conversion would).

Everything else is refused before anything opens or resolves it. Where the allowed path then leads (an author's own
disk, ``..``) is the reader's concern: nothing outside a download is ever carried into the scene.
Standard library only; also imported as a plain module by the Fab converter child.
"""

import re
import unicodedata

_RESERVED = frozenset('<>"|?*')
# Slash and colon lookalikes no normalisation maps (∕ ∖ ⧵ ∶ ⁄ ⧸ ⧹): a reader converting to an ANSI code page with
# "best fit" would turn them into separators or a colon (re-review r4). No real file name needs them.
_LOOKALIKES = frozenset('\u2215\u2216\u29f5\u2236\u2044\u29f8\u29f9')
_TILE = re.compile(r'<(?:UDIM|UVTILE)>', re.IGNORECASE)
_DEVICES = frozenset({'con', 'prn', 'aux', 'nul', 'conin$', 'conout$', 'clock$',
                      *('%s%s' % (name, digit) for name in ('com', 'lpt') for digit in '0123456789\u00b9\u00b2\u00b3')})


def local_form(raw):
    """Whether ``raw`` has the shape of a local file path (an empty path names nothing and is allowed).

    Its NFKC form must pass too (fullwidth ＼ ／ ： become \\ / :), and it may not begin or end with any Unicode
    whitespace, so no reader's own trimming or best-fit conversion can turn an allowed text into another shape
    (re-review r3 G3)."""
    if not raw:
        return True
    raw = str(raw)
    if raw != raw.strip():
        return False
    normal = unicodedata.normalize('NFKC', raw)
    return _shape(raw) and (normal == raw or (normal == normal.strip() and _shape(normal)))


def _shape(raw):
    if raw.startswith('\\'):
        return False   # Drive-less rooted in Windows form (\Device\..., \GLOBALROOT\...): only POSIX form roots.
    text = _TILE.sub('UDIM', str(raw)).replace('\\', '/')
    if any(ord(c) < 32 or ord(c) == 127 or c in _RESERVED or c in _LOOKALIKES for c in text):
        return False
    rest = text[2:] if re.match(r'[A-Za-z]:', text) else text
    if text.startswith('//') or ':' in rest:
        return False
    for part in rest.split('/'):
        name = part.rstrip(' .').split('.', 1)[0].strip().lower()
        if name in _DEVICES:
            return False
    return True
