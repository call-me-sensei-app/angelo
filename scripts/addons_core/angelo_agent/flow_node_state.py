# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-use state of a flow node that stands for several steps (a Subflow's recipe steps, a per-part node's parts):
kept on the one node, by inner name, as small JSON fields. No drawing; used by ``flow_nodes`` and
``flow_character_nodes``."""

import json


class _PerUseState:
    """Per-step state kept on one node, by inner name: a Subflow's recipe steps, or a per-part step's parts."""

    def inner(self, field):
        """{name: value} of one per-use field (chosen, last_key, ...)."""
        try:
            value = json.loads(getattr(self, field + "_json") or "{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def set_inner(self, field, step, value):
        values = self.inner(field)
        if value:
            values[step] = value
        else:
            values.pop(step, None)
        text = json.dumps(values, sort_keys=True) if values else ""
        if getattr(self, field + "_json") != text:
            setattr(self, field + "_json", text)
