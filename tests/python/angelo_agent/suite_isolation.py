# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The per-file entry: importing it first gives this process no real provider login unless it opts in.

Every Blender suite, host test and billed in-app driver starts with it (``provider_isolation.preamble_files``,
pinned by ``test_native_suite_isolation``), so a suite started by hand is isolated as ``run_native_suites.py`` isolates
it. What it does and the opt-ins are ``provider_isolation``'s docstring. In a host test (plain Python) it also installs
``thread_failures``: an uncaught exception in a thread the test starts fails that test, and it appends this checkout's
``scripts/addons_core`` to sys.path, so ``angelo_agent`` imports the same alone as in the full suite (a test's own
earlier entries still win; ``test_native_addon_path``). In every process it also stops git from looking for a
repository above this tree (``own_checkout.confine``).
"""

import sys
from pathlib import Path

import own_checkout
import provider_isolation

STATE = provider_isolation.ensure()
# Git, for this process and what it starts, never finds a repository above this tree: a snapshot unpacked inside
# another checkout is not answered for by that checkout (own_checkout).
own_checkout.confine(Path(__file__).resolve().parents[3])

if not provider_isolation.in_blender():   # A host test: a thread's uncaught exception fails it (thread_failures).
    import thread_failures
    thread_failures.install()
    import angelo_guard_twin  # noqa: F401  The grant authority's Python twin (no _bpy here; grant_authority).
    _ADDONS = str(Path(__file__).resolve().parents[3] / "scripts/addons_core")
    if _ADDONS not in sys.path:
        sys.path.append(_ADDONS)
