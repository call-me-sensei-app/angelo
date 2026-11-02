# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shader code no earlier run compiled, for a timing that claims a new (uncompiled) shader. No bpy at import.

A driver keeps compiled shaders across processes (Metal's shader cache on macOS, the GPU driver's on Windows, both
outside a suite's temporary home), so a reference that is "new" only within one run is a cache hit on a machine that
ran the suite before. The value of a node's unlinked input is a uniform, not code, so varying values changes nothing;
and a structure drawn from a small set (a run number modulo a few chain lengths and operation offsets) repeats after
a few dozen runs. test_blender_view_warm's "fraction of a synchronous render of an equally new shader" failed that
way on Mac 2 (1.0.8 cut 00110f294d4): its reference took 49.9 ms where earlier runs took 349-855 ms, because its 102
possible structures were already compiled.

``chain`` links ``count`` Math nodes whose operations come from this process's random ``TOKEN`` (16 bytes from the
operating system) and a ``role``, so every run's code is its own (8^count structures per role: 8^24 is about 5e21)
while two roles of one run get equally long, equally heavy chains. ``operations`` is the pure part
(test_native_fresh_shader).
"""

import random
import secrets

OPERATIONS = ("ADD", "SUBTRACT", "MULTIPLY", "SINE", "COSINE", "POWER", "MINIMUM", "MAXIMUM")
TOKEN = secrets.token_bytes(16)   # This process's: no earlier run, on any machine, had it.


def operations(count, role, token=None):
    """The ``count`` Math operations of ``role``'s chain in the run of ``token`` (default this process's)."""
    pick = random.Random((token if token is not None else TOKEN) + role.encode("utf-8"))
    return [pick.choice(OPERATIONS) for _ in range(count)]


def chain(tree, socket, count, role):
    """Link ``count`` Math nodes after ``socket`` in node ``tree``, operations from ``operations``; the last output."""
    for index, operation in enumerate(operations(count, role)):
        math = tree.nodes.new("ShaderNodeMath")
        math.operation = operation
        math.inputs[1].default_value = 0.37 + 0.01 * (index % 13)   # A uniform: kept off 0 and 1, never the code.
        tree.links.new(socket, math.inputs[0])
        socket = math.outputs[0]
    return socket
