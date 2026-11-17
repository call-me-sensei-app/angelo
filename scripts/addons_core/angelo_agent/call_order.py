# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One request's calls run one at a time, in the order they arrived (``claude_direct.DirectRequest``).

A call waits for its turn and ends it once it is over. Where the call ends decides what the next call can see:
Angelo's own direct-tools turn and an app's call in the window end their turn when the call returns; without the
window an app's call ends it only when its answer goes out, after the save or reload the host does for it
(``ExternalWork.count_out``), so the next call never runs before that reload (Agent-B2, doc/angelo/external_mcp.rst).
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
