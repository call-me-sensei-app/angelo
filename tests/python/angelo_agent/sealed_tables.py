# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replace a sealed table for a test or a benchmark. The add-on's validators read read-only copies of their tables
(``read_only.seal``: mapping proxies, tuples, frozensets), so ``patch.dict`` or an item assignment raises. The seam is
the module attribute: ``override`` rebinds the name, as a read-only copy of ``value``, in its own module and in every
loaded add-on module that imported the same object by name (``from .action_operator_spec import WORK_BOUNDS``), so
each reader sees the replacement. It returns ``mock`` patchers: ``start()`` them, ``stop()`` them to put it back."""

import sys
from unittest import mock


def override(module, name, value):
    from angelo_agent import read_only
    original, replacement = getattr(module, name), read_only.frozen(value)
    holders = [held for held in list(sys.modules.values())
               if held is not None and (held is module or getattr(held, "__name__", "").startswith("angelo_agent"))]
    return [mock.patch.object(held, attr, replacement) for held in holders
            for attr, current in list(vars(held).items()) if current is original]


def start(module, name, value):
    """``override`` started at once, for a run that keeps it to the end (a benchmark's main)."""
    patchers = override(module, name, value)
    for patcher in patchers:
        patcher.start()
    return patchers
