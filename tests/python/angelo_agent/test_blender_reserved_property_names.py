# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No property of any registered type has a name RNA reserves, in a real background Blender.

RNA refuses a property named like a Python keyword or like a mapping's own accessors (``keys``, ``values``,
``items``, ``get``; ``RNA_validate_identifier`` in ``rna_define.cc``): such a name cannot be reached as an attribute
on every path, and a build with assertions logs an error for it at every start. Blender's own types are checked when
it is built. Types registered while it runs (an operator's properties defined with ``RNA_def_*`` on ``ot->srna``, an
add-on's operators and property groups) are only checked in a build with assertions, so a release build takes them
silently: three native operators and two add-on operators of Angelo's had a property named ``items`` that way until
1.1.0 (the Optimizer audit of 2026-11-13, Agent-B6).

This suite reads every operator and every other registered type after the add-on is enabled and names each property
whose identifier is reserved. Run through ``run_native_suites.py --include test_blender_reserved_property_names``;
check ``reserved-property-names.json`` for ``success``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import keyword
import traceback
from pathlib import Path

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-reserved-property-names-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
import addon_utils  # noqa: E402
import bpy  # noqa: E402

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False

REPORT = OUT / "reserved-property-names.json"
# As rna_define.cc: every Python keyword except the three constants, and a mapping's accessor names.
RESERVED = (set(keyword.kwlist) - {"False", "None", "True"}) | {"keys", "values", "items", "get"}


def reserved_of(rna):
    return sorted(prop.identifier for prop in rna.properties if prop.identifier in RESERVED)


def main():
    state = {"success": False, "operators": 0, "types": 0, "reserved": []}
    try:
        found = []
        for group in dir(bpy.ops):
            for name in dir(getattr(bpy.ops, group)):
                rna = getattr(getattr(bpy.ops, group), name).get_rna_type()
                state["operators"] += 1
                found += ["%s.%s: %s" % (group, name, identifier) for identifier in reserved_of(rna)]
        for name in dir(bpy.types):
            rna = getattr(getattr(bpy.types, name), "bl_rna", None)
            if rna is None:
                continue
            state["types"] += 1
            found += ["%s: %s" % (name, identifier) for identifier in reserved_of(rna)]
        state["reserved"] = sorted(set(found))
        # A reading that saw nothing proves nothing: Blender has well over a thousand operators and types.
        state["success"] = not state["reserved"] and state["operators"] > 1000 and state["types"] > 1000
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_RESERVED_PROPERTY_NAMES", json.dumps(
        {"success": state["success"], "reserved": state["reserved"][:12]}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
