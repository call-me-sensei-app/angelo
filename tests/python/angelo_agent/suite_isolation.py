# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The per-file entry: importing it first gives this process no real provider login unless it opts in.

Every Blender suite, host test and billed in-app driver starts with it (``provider_isolation.preamble_files``,
pinned by ``test_native_suite_isolation``), so a suite started by hand is isolated as ``run_native_suites.py`` isolates
it. What it does and the opt-ins are ``provider_isolation``'s docstring.
"""

import provider_isolation

STATE = provider_isolation.ensure()
