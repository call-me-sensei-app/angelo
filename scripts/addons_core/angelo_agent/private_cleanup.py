# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which folders an owner's own cleanup (discard, cancel, sweep) may remove.

Native programs, round 11: the preview bundle moved to the scratch root and its discard stopped removing it, because the
discard still asked where the system temporary folder is. Cleanup asks ``owns`` instead.
"""

from pathlib import Path
import tempfile

from . import private_roots


def owns(folder, prefix):
    """Whether ``folder`` is one of Angelo's own folders of family ``prefix``: made by this process, or named ``prefix*``
    directly in the scratch root or the temporary folder (an earlier process's leftover). No other path qualifies."""
    folder = Path(folder)
    if prefix not in {name for name, _ in private_roots.FAMILIES} or not folder.name.startswith(prefix):
        return False
    if folder in private_roots.registered():
        return True
    return folder.parent in (private_roots.scratch_root(), Path(tempfile.gettempdir()))
