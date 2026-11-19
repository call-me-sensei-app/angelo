# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One request's calls run one at a time, in the order they arrived (``claude_direct.DirectRequest``).

A call waits for its turn and ends it once it is over. Where the call ends decides what the next call can see:
Angelo's own direct-tools turn ends its turn when the call returns.
"""

import threading


class CallOrder:
    def __init__(self):
        self._next, self._turn = 0, threading.Condition()

    def wait(self, ticket):
        """Until every call that arrived before ``ticket`` (``DirectRequest.arrived``) has ended its turn. None: at
        once (a call outside the order)."""
        with self._turn:
            self._turn.wait_for(lambda: ticket is None or self._next == ticket)

    def end(self, ticket):
        """``ticket``'s call is over, so the next may run; only the call whose turn it is ends it (None: none)."""
        with self._turn:
            if ticket is not None and self._next == ticket:
                self._next += 1
                self._turn.notify_all()
