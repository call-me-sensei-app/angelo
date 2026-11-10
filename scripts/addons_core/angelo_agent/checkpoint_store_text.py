# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the person reads when the checkpoint store's volume can't keep a Sculpt snapshot, and what to do about it.

The store lives in Angelo's data folder (``ui.state_directory``: Blender's user configuration folder, inside the
home folder, or the state override), never beside the project. A Sculpt pair's capture and its Restore check stats
taken on Angelo's own open writers; where closing a file may restamp it (Windows' FAT and exFAT, measured; where
nothing says, as on a share, a time kept in 2 s steps), those stats can't vouch for anything
(``angelo_change_clock.ClosingRestamps``), so the snapshot is refused before anything is published, in words the
person can act on: a ``display_text.Text`` the chat and Blender's reports translate where they show it. A time ahead
of the clock (this computer's or a share's server's set wrong) is refused so too, wherever a capture or a Restore
copies (``awaited``).
"""

import contextlib

from . import change_clock
from .display_text import Text
from .ui_text import n_

RESTAMPS_AT_CLOSE = n_("Sculpt snapshots can't be kept where Angelo's data is stored: that disk or network folder may "
                       "change a file's time when the file is closed, so a snapshot couldn't be checked. Your scene "
                       "is unchanged. To use Sculpt snapshots, keep your home folder on a local disk formatted APFS "
                       "(Mac) or NTFS (Windows).")


CLOCK_AHEAD = n_("A snapshot couldn't be checked: a file it uses shows a time ahead of this computer's clock, so this "
                 "computer or the server that stores the file has the wrong time. Your scene is unchanged. Set both "
                 "clocks to update automatically, then try again.")


@contextlib.contextmanager
def awaited():
    """``change_clock.awaited()`` for a step the person waits for (a snapshot capture, a Restore's copies), with a
    clock set wrong (``change_clock.ClockAhead``: this computer's, or a share's server's) refused in their words."""
    try:
        with change_clock.awaited():
            yield
    except change_clock.ClockAhead as error:
        raise ValueError(Text(CLOCK_AHEAD)) from error


def refused():
    """The person's refusal of a ``ClosingRestamps`` (raised ``from`` it)."""
    return ValueError(Text(RESTAMPS_AT_CLOSE))


def settle_writers(change_clock, witnesses):
    """``change_clock.settle_all`` of stats taken on Angelo's own open writers (``writing=True``), returning them as a
    tuple; a volume where closing may restamp is refused in the person's words (``refused``)."""
    witnesses = tuple(witnesses)
    try:
        change_clock.settle_all(witnesses, writing=True)
    except change_clock.ClosingRestamps as error:
        raise refused() from error
    return witnesses
