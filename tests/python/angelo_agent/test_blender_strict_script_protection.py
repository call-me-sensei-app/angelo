# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict script protection in the real app with the real guard (``strict_protection``, Ruling 17; the review of
93ef5b119e2).

* Off by default (a factory start), and C's flag says so; turned on (narrowing, open to Angelo's own code), C's flag
  reads on.
* A confined script can't change it by any route: an attribute write, ``property_unset`` (M1), ID property
  access, its definition (redefined, removed, its class unregistered or replaced: M2), the C setter or sync, or
  Angelo's own sync while the saved value differs from the flag (S7). A route that got past is put back by the run's
  end (the sweep), which also re-syncs C.
* Off is the person's: Angelo's own code without a press can't turn it off (S2); in the harness scope (a child with
  --enable-event-simulate, standing in for the person's press) it turns off (S7's positive case).
* A real preference read (``wm.read_factory_userpref``, unconfined) brings the flag to the read value (S7).
* A worker started by an Angelo that has it on has it on (``ANGELO_STRICT_SCRIPT_PROTECTION``, ``headless_main``).

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import subprocess
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-strict-protection-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
NAME = "strict_script_protection"
READER_INSTALLS_WRAPPERS = True   # The reparse branch's stat-family and ownership wrappers install with the flag.
ROUTES = {   # A confined script's every way to the preference (M1, as a class).
    "attribute": "PREFS.strict_script_protection = False\n",
    "property_unset": "PREFS.property_unset('strict_script_protection')\n",
    "id_properties_clear": "PREFS.id_properties_clear()\n",
    "id_properties_ensure": "PREFS.id_properties_ensure()\n",
    "subscript": "PREFS['strict_script_protection'] = 0\n",
    "pop": "PREFS.pop('strict_script_protection', None)\n",
    "system_properties": "PREFS.bl_system_properties_get(do_create=True)['strict_script_protection'] = 0\n",
    "c_setter": "import _bpy\n_bpy._angelo_guard.set_strict_script_protection(False)\n",
    "c_sync": "import _bpy\n_bpy._angelo_guard.sync_strict_script_protection()\n",
    # Its definition (the review of a121c9a, M2): a redefinition without its update, a type swap a read then frees the
    # stored value for, a removal, and the class taken away or replaced.
    "redefine": "type(PREFS).strict_script_protection = bpy.props.BoolProperty(name='Strict script protection')\n",
    "redefine_type": ("type(PREFS).strict_script_protection = bpy.props.StringProperty()\n"
                      "PREFS.strict_script_protection\n"),
    "delete_definition": "del type(PREFS).strict_script_protection\n",
    "remove_property": "bpy.props.RemoveProperty(type(PREFS), attr='strict_script_protection')\n",
    "unregister_class": "bpy.utils.unregister_class(type(PREFS))\n",
    "register_class": ("class Other(bpy.types.AddonPreferences):\n    bl_idname = 'angelo_agent'\n"
                       "bpy.utils.register_class(Other)\n"),
}


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    REPORT.setdefault("details", {})[name] = str(detail)[:600]


def run(source, **names):
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
    admit("strict", source)
    try:
        _, result = script_confinement.run_confined(
            source + "result = 'done'\n", namespace, None, "strict",
            finish=lambda ns: script_errors.read_back(ns.get("result")))
        return {"refused": None, "result": result}
    except script_confinement.ScriptConfined as error:
        return {"refused": script_confinement.reason_of(error)}
    except Exception as error:   # noqa: BLE001 - a TypeError (no such ID properties) also changes nothing.
        return {"refused": None, "error": repr(error)[:300]}


def child(expression, env=None, simulate=False):
    """A fresh Angelo (factory, background) running ``expression``; its stdout. Its own state and connections folders,
    new each time: Strict script protection's saved choice (strict-script-protection.json, On from either wins at
    start) is never this process's (build3 on 1f95324: a child that shared the parent's started On)."""
    folders = Path(tempfile.mkdtemp(prefix="strict-child-", dir=OUT))
    own = {"ANGELO_STATE_DIR": str(folders / "state"), "ANGELO_CONNECTIONS_DIR": str(folders / "connections")}
    simulated = ["--enable-event-simulate"] if simulate else []
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                           "--offline-mode", *simulated, "--python-exit-code", "1", "--python-expr", expression],
                          capture_output=True, text=True, timeout=180, env=dict(os.environ, **own, **(env or {})))
    return done.returncode, done.stdout[-2000:] + done.stderr[-2000:]


PERSON_OFF = """
import addon_utils, bpy, os, sys
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import grant_guard, script_confinement
native = sys.modules["nt" if os.name == "nt" else "posix"]
prefs = bpy.context.preferences.addons["angelo_agent"].preferences
print("STRICT_START", script_confinement._guard.strict_script_protection())   # Its own state: off by default.
print("STRICT_STAT_START", getattr(os.stat, "__self__", None) is not native)
prefs.strict_script_protection = True
guard = script_confinement._guard
print("STRICT_ON", guard.strict_script_protection())
print("STRICT_STAT_ON", getattr(os.stat, "__self__", None) is not native)
prefs.strict_script_protection = False          # No press: refused.
print("STRICT_NO_PRESS", prefs.strict_script_protection, guard.strict_script_protection())
grant_guard.harness(setattr, prefs, "strict_script_protection", False)   # The person's stand-in.
print("STRICT_PERSON", prefs.strict_script_protection, guard.strict_script_protection())
print("STRICT_STAT_OFF", getattr(os.stat, "__self__", None) is not native)
"""
WORKER = """
import bpy
from angelo_agent import headless_main, ui
headless_main._register(bpy, ui)
import _bpy
print("STRICT_WORKER", _bpy._angelo_guard.strict_script_protection(),
      bpy.context.preferences.addons["angelo_agent"].preferences.strict_script_protection)
"""



class _CapturingGuard:
    """The native guard as install_sweeps sees it, with set_sweep captured and its other registrations left alone
    (they were made at the add-on's registration; nothing is registered twice)."""

    def __init__(self, guard, captured):
        self._guard, self._captured = guard, captured

    def set_sweep(self, begin, end):
        self._captured.append((begin, end))

    def watch_modules(self, names):
        pass

    def angelo_package(self, name):
        pass

    def __getattr__(self, name):
        return getattr(self._guard, name)


def registered_blender_sweep():
    """(begin, end) of the Blender sweep, from install_sweeps itself (script_confinement_blender)."""
    from angelo_agent import script_confinement, script_confinement_blender
    captured, real = [], script_confinement._guard
    script_confinement._guard = _CapturingGuard(real, captured)
    try:
        script_confinement_blender.install_sweeps(bpy)
    finally:
        script_confinement._guard = real
    built = [pair for pair in captured if pair[0].__qualname__.startswith("blender_sweep.")]
    check("the_registered_blender_sweep_is_found", len(built) == 1, [pair[0].__qualname__ for pair in captured])
    return built[0]


def main():
    from angelo_agent import script_confinement, strict_protection
    guard = script_confinement._guard
    prefs = bpy.context.preferences.addons["angelo_agent"].preferences
    check("guard_has_the_flag", guard is not None and hasattr(guard, "sync_strict_script_protection"))
    check("off_by_default", prefs.strict_script_protection is False and guard.strict_script_protection() is False,
          (prefs.strict_script_protection, guard.strict_script_protection()))

    prefs.strict_script_protection = True   # Narrowing: Angelo's own code may.
    check("flag_follows_the_preference_on", guard.strict_script_protection() is True and strict_protection.enabled())

    definition = prefs.bl_rna.properties[NAME].as_pointer()
    for route, source in ROUTES.items():
        outcome = run(source, PREFS=prefs)
        prefs = bpy.context.preferences.addons["angelo_agent"].preferences
        check("confined_" + route + "_changes_nothing",
              prefs.strict_script_protection is True and guard.strict_script_protection() is True
              and prefs.bl_rna.properties[NAME].as_pointer() == definition
              and (outcome.get("refused") is not None or "error" in outcome), outcome)
        prefs.strict_script_protection = True   # Whatever a failed check left, the next route starts on.

    # S7: a confined call of Angelo's own sync while the saved value differs from the flag. Unconfined, Angelo's own
    # code resets the saved value past the update (the flag stays on), then the script asks for a sync.
    prefs.property_unset(NAME)
    differs = prefs.strict_script_protection is False and guard.strict_script_protection() is True
    outcome = run("SYNC()\n", SYNC=strict_protection.sync)
    check("confined_angelo_sync_refused", differs and guard.strict_script_protection() is True, outcome)
    prefs.strict_script_protection = True

    # The run-end sweep on the real preferences (every confined route is refused, so this drives the sweep itself,
    # as C runs it around a script): a reset past the update during a "run" is put back and C re-synced.
    from angelo_agent import grant_guard, strict_flag
    # The sweep the app registers, never one built here (the re-read of 2328f07, NIT-4): C holds the registered pair
    # privately (confine_run.cc, by design: no accessor), so install_sweeps runs its own code once more with its
    # set_sweep captured, and the Blender sweep it hands over is the one driven. Whether Strict protects comes from
    # C's flag, which must say what the preference says wherever a run can start (the coordinator's (Q)).
    begin, end = registered_blender_sweep()
    check("c_flag_is_the_preference_at_a_run_start", strict_flag.enabled() is prefs.strict_script_protection is True,
          (strict_flag.enabled(), prefs.strict_script_protection))
    snapshot = begin()
    prefs.property_unset(NAME)   # Unconfined, past the update: stored Off, the flag still On.
    findings = end(snapshot)
    check("the_sweep_puts_it_back_and_resyncs", "setting " + NAME in findings
          and prefs.strict_script_protection is True and guard.strict_script_protection() is True, findings)

    prefs.strict_script_protection = False   # Angelo's own code, no press (background): put back.
    check("off_needs_the_person", prefs.strict_script_protection is True and guard.strict_script_protection() is True,
          (prefs.strict_script_protection, guard.strict_script_protection()))

    code, output = child(PERSON_OFF, simulate=True)
    check("the_person_can_turn_it_off", code == 0 and "STRICT_START False" in output and "STRICT_ON True" in output
          and "STRICT_NO_PRESS True True" in output and "STRICT_PERSON False False" in output, output)
    # A reader that installs with the flag (the reparse branch's os.stat wrapper; iso-core, S12): native while off,
    # wrapped while on through a sync-driven change. READER_INSTALLS_WRAPPERS says whether this build has one.
    check("os_stat_native_when_off", "STRICT_STAT_START False" in output and "STRICT_STAT_OFF False" in output, output)
    if READER_INSTALLS_WRAPPERS:
        check("os_stat_wrapped_when_on", "STRICT_STAT_ON True" in output, output)

    # A preference read Python starts (an add-on, code a script left, no press) never turns it off: C keeps On and
    # the preference gets On back (the re-read of 9b3f450, M3).
    bpy.ops.wm.read_factory_userpref()
    prefs = bpy.context.preferences.addons["angelo_agent"].preferences
    state = Path(os.environ["ANGELO_STATE_DIR"]) / strict_protection.FILE
    saved = json.loads(state.read_text()).get("on") if state.is_file() else None
    check("a_read_python_starts_keeps_it_on", prefs.strict_script_protection is True
          and guard.strict_script_protection() is True and saved is True,
          (prefs.strict_script_protection, guard.strict_script_protection(), saved))
    # The person's own read (the harness stands in for their press on Load Factory Preferences) brings it along.
    grant_guard.harness(bpy.ops.wm.read_factory_userpref)
    prefs = bpy.context.preferences.addons["angelo_agent"].preferences
    check("a_preference_read_brings_the_flag_along", prefs.strict_script_protection is False
          and guard.strict_script_protection() is False, (prefs.strict_script_protection,
                                                          guard.strict_script_protection()))

    code, output = child(WORKER, env={"ANGELO_STRICT_SCRIPT_PROTECTION": "1"})
    check("a_worker_started_with_it_on_has_it_on", code == 0 and "STRICT_WORKER True True" in output, output)
    code, output = child(WORKER, env={"ANGELO_STRICT_SCRIPT_PROTECTION": "0"})
    check("a_worker_started_with_it_off_has_it_off", code == 0 and "STRICT_WORKER False False" in output, output)

    # S13: a confined script changes the definition in place (no RNA change); Angelo registers again (disabled and
    # enabled), and the person's On still reaches C (the definitions are written anew from import).
    outcome = run("for k in type(PREFS).__mro__:\n"
                  "    a = k.__dict__.get('__annotations__') or {}\n"
                  "    if 'strict_script_protection' in a:\n"
                  "        a['strict_script_protection'].keywords.pop('update', None)\n",
                  PREFS=bpy.context.preferences.addons["angelo_agent"].preferences)
    addon_utils.disable("angelo_agent", default_set=False)
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    prefs = bpy.context.preferences.addons["angelo_agent"].preferences
    prefs.strict_script_protection = True
    check("a_definition_changed_in_place_never_reaches_a_registration",
          script_confinement._guard.strict_script_protection() is True, outcome)

    stored = bpy.context.preferences.addons["angelo_agent"].preferences
    check("stored_with_the_preferences", "strict_script_protection" in stored.bl_rna.properties
          and not stored.bl_rna.properties["strict_script_protection"].is_skip_save)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "strict-protection.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps(REPORT, default=str)[:6000])
if REPORT["success"]:
    print("ANGELO_STRICT_PROTECTION_OK")
sys.exit(0 if REPORT["success"] else 1)
