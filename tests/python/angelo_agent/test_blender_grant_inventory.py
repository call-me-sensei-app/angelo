# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The grant inventory against Blender's live registry (grant_inventory; doc/angelo/grant_operators.rst, "Audit").

The host test reads the source; an operator made at run time (``type()``), registered by C, or in a file no scanner
reads is invisible there (break-it of the first inventory, M3). Here, in the app, with Angelo's add-on on:

* every registered operator that is Angelo's (its Python class's module is ``angelo_agent``, or its idname names
  Angelo, which C's own are) is guarded (``grant_guard.REGISTRY``), a guarded sibling (``GROUPED``) or in
  ``NOT_A_GRANT``; and every listed one is registered;
* the source scan sees every live Python operator, the run-time ones through ``DYNAMIC``, and every C operator it
  finds in the sources is live; an operator registered only under a condition (``REGISTERED_WHEN``) is live exactly
  when its condition says so;
* C's denied-operator list holds every guarded operator, a guarded sibling included, so the confinement's operator
  gate and the interface's refusal to draw a grant button for a script know each one.

Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import importlib
import json
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import grant_inventory as inventory  # noqa: E402
from angelo_agent import grant_authority, grant_guard  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-grant-inventory-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, notes = {}, {}


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            notes[name + "_error"] = traceback.format_exc()[-2000:]
            print(traceback.format_exc())
        return function
    return wrap


def live_operators():
    """{bpy idname: the Python class's module, or "C"} of every registered operator that is Angelo's."""
    found = {}
    for module_name in dir(bpy.ops):
        module = getattr(bpy.ops, module_name)
        for name in dir(module):
            idname = "%s.%s" % (module_name, name)
            try:
                cls = getattr(bpy.types, getattr(module, name).idname(), None)
            except (AttributeError, KeyError, RuntimeError):
                cls = None
            origin = getattr(cls, "__module__", "") if cls is not None else ""
            if origin.split(".")[0] == "angelo_agent" or "angelo" in idname:
                found[idname] = origin or "C"
    return found


LIVE = live_operators()


def registered_now(idname):
    """Whether the condition ``REGISTERED_WHEN`` names for ``idname`` says it is registered now (True without one)."""
    if idname not in inventory.REGISTERED_WHEN:
        return True
    module, function, _why = inventory.REGISTERED_WHEN[idname]
    return bool(getattr(importlib.import_module("angelo_agent." + module), function)())   # Imported, never registered.


EXPECTED_OFF = {idname for idname in inventory.REGISTERED_WHEN if not registered_now(idname)}
GUARDED = dict(grant_guard.REGISTRY)
LISTED = set(GUARDED) | set(inventory.GROUPED) | set(inventory.NOT_A_GRANT)
notes["live_count"], notes["guarded_count"] = len(LIVE), len(GUARDED)


@case("every_live_angelo_operator_is_guarded_or_has_a_reason")
def _():
    notes["unlisted"] = sorted(set(LIVE) - LISTED)
    return bool(LIVE) and not notes["unlisted"]


@case("every_listed_operator_is_registered")
def _():
    notes["not_registered"] = sorted(LISTED - set(LIVE) - EXPECTED_OFF)
    return not notes["not_registered"]


@case("the_source_scan_sees_every_live_python_operator")
def _():
    declared, _makers = inventory.source_operators()
    dynamic = {idname for made in inventory.DYNAMIC.values() for idname in made}
    python = {idname for idname, origin in LIVE.items() if origin != "C"}
    notes["live_but_not_in_the_source"] = sorted(python - set(declared) - dynamic)
    notes["in_the_source_but_not_live"] = sorted((set(declared) | dynamic) - set(LIVE) - EXPECTED_OFF)
    return not notes["live_but_not_in_the_source"] and not notes["in_the_source_but_not_live"]


@case("a_conditional_operator_is_live_exactly_when_its_condition_says")
def _():
    notes["conditional"] = {idname: registered_now(idname) for idname in inventory.REGISTERED_WHEN}
    notes["conditional_wrong"] = sorted(idname for idname, on in notes["conditional"].items() if on != (idname in LIVE))
    return not notes["conditional_wrong"]


@case("every_c_operator_in_the_sources_is_live")
def _():
    c_side = inventory.c_operators()
    if c_side is None:   # An installed app has no C sources: the live registry above is the check.
        notes["c_sources"] = "absent"
        return True
    notes["c_not_live"] = sorted(set(c_side) - set(LIVE))
    return not notes["c_not_live"]


@case("c_denies_every_guarded_operator")
def _():
    guard = grant_authority.GUARD
    reader, asker = getattr(guard, "denied_operators", None), getattr(guard, "is_grant_operator", None)
    if reader is None and asker is None:
        notes["denied"] = "the C guard can say neither its list (denied_operators) nor one entry (is_grant_operator)"
        return False
    denied = set(reader()) if reader is not None else None
    expected = set(GUARDED) | set(inventory.GROUPED) | set(grant_guard.ACCOUNT_OPERATORS)   # Refused to scripts too.

    def c_name(idname):   # "angelo.x" -> "ANGELO_OT_x", as C may hold either form.
        module, name = idname.split(".", 1)
        return getattr(getattr(bpy.ops, module), name).idname()

    def held(idname):
        if denied is not None:
            return idname in denied or c_name(idname) in denied
        return bool(asker(idname)) or bool(asker(c_name(idname)))
    notes["guarded_not_denied"] = sorted(idname for idname in expected if not held(idname))
    return not notes["guarded_not_denied"]


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "notes": notes}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_GRANT_INVENTORY", json.dumps(report))
