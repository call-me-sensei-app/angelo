# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of the engine link comparison (``engine_link_compare``): a scenario session for
``eval_live_agent_session``. BILLED through that driver only.

``begin`` builds the Godot project, turns the engine link on as the person's + menu does and starts a headless Godot
editor with the shipped plugin and the recorder. ``ready`` runs each brief's untimed setup before its message:
the crate restored and linked through Godot's own Edit in Angelo, then shaped as the brief's state says.
``jobs_pending`` answers the barrel's request card after brief 4's turn, as a person would. ``grade`` applies the
module's closed checks. Nothing here differs between the arms but the withheld tools the launcher sets.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import engine_link_compare as compare
import scenario_session

SETUP_S = 150.0       # One brief's setup at most; a setup that can't finish makes that task invalid, never failed.
CLICK_WAIT_S = 90.0   # After the card's click: until Godot links the barrel.


class EngineLinkSession(scenario_session.ScenarioSession):
    def __init__(self, task_set, out, python_approvals="approve", demo=None, stage_copies=False):
        self.name, self.out = compare.TASK_SET, Path(out)
        self.python_approvals, self.demo, self.extra = "approve", None, frozenset()
        self.job_wait_s, self.stage_copies, self.stages = CLICK_WAIT_S, False, []
        withheld = bool(os.environ.get("ANGELO_WITHHOLD_TOOLS"))
        self.report = {"design_sha256": compare.DESIGN_SHA256, "arm": "script" if withheld else "tools",
                       "setups": [], "python_approvals": "approve"}
        self.turn_start = (None, 0)
        self.bpy = self.window = self.godot = self.project = None
        self.attached, self.prepared, self.setup = 0, -1, None
        self.loads, self.clicked_at, self.invalid = [], None, {}
        self.shape = {}   # Heights and widths at the start, and brief 3's update (its version and when it showed).

    # Start and end -------------------------------------------------------------------------------------------------

    def begin(self, bpy, window):
        self.bpy, self.window = bpy, window
        folder = self.out / "compare"
        folder.mkdir(parents=True, exist_ok=True)
        godot = os.environ.get("ANGELO_QA_GODOT") or "/Applications/Godot.app/Contents/MacOS/Godot"
        record = folder / "link-record.json"
        os.environ["ANGELO_LINK_RECORD"] = str(record)   # Angelo's and Godot's record: private to the trial.
        home = folder / "godot-home"
        home.mkdir(exist_ok=True)
        env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME") if key in os.environ}
        env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_LINK_RECORD=str(record),
                   ANGELO_QA_DIR=str(folder / "godot"), ANGELO_DISABLE_KEYCHAIN="1", ANGELO_AGENT_RUNTIME="off")
        (folder / "godot").mkdir(exist_ok=True)
        started = time.perf_counter()
        self.project = compare.build_project(folder, bpy.app.binary_path, godot, env)
        from angelo_agent import engine_link_ui
        failed = engine_link_ui.turn_on()   # As the person's + menu row does, before any brief.
        if failed:
            raise RuntimeError("The engine link didn't turn on: %s" % failed)

        @bpy.app.handlers.persistent
        def loaded(*_args):
            self.loads.append(time.time())
        bpy.app.handlers.load_post.append(loaded)
        self.godot = subprocess.Popen([godot, "--headless", "--editor", "--path", str(self.project)], env=env,
                                      stdout=(folder / "godot.log").open("w"), stderr=subprocess.STDOUT)
        self.report["setup"] = {"seconds": round(time.perf_counter() - started, 1),
                                "crate_file_height": compare.glb_height(folder / "originals/crate.glb")}
        self.report["engine_facts"] = facts = self.seed_engine_facts()
        if "Godot" not in facts["running"]:
            raise RuntimeError("Angelo's engine scan didn't find the running Godot: %s" % facts)
        return self.report

    def seed_engine_facts(self):
        """What Angelo's engine watcher finds on a person's computer with Godot open: Godot running and installed, for
        the FEATURES lookup, STATUS and each turn's ``engines`` note. The watcher never runs in an automation run
        (``engine_suggest_ui._off``), so the first design run of 2026-11-03 told every brief in both arms that no
        engine ran (superseded). Here one real scan, after Godot started and before any brief, in both arms alike."""
        from angelo_agent import engine_detect, engine_suggest_ui
        engine_suggest_ui._running = engine_detect.running()
        engine_suggest_ui._installed = engine_detect.installed()
        return engine_suggest_ui.facts()

    def finish(self, bpy, window, probe=True):
        if self.godot is not None and self.godot.poll() is None:
            self.godot.terminate()
            try:
                self.godot.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.godot.kill()
        from angelo_agent import engine_link
        engine_link.stop()
        self.report["invalid_tasks"] = self.invalid
        return self.report

    def undo_probe(self, bpy, window):
        return self.report

    # Each brief's setup (untimed) ------------------------------------------------------------------------------

    def godot_report(self):
        try:
            return json.loads((self.out / "compare/godot/godot.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def ready(self, finishing=False):
        """Whether the next brief may be sent: its setup is done (or failed: that task is then invalid)."""
        if finishing or self.prepared == self.attached:
            return True
        task = compare.tasks()[self.attached]
        if self.setup is None or self.setup["task"] != task["id"]:
            self.setup = {"task": task["id"], "state": task["setup"], "phase": "restore", "at": time.time()}
        try:
            done = self.step(self.setup)
        except Exception as error:   # A harness failure, never the agent's: the task is invalid.
            done = "%s: %s" % (type(error).__name__, str(error)[:200])
        if done is not True and time.time() - self.setup["at"] > SETUP_S:
            done = "setup timed out in %s" % self.setup["phase"]
        if done is True or isinstance(done, str):
            if isinstance(done, str):
                self.invalid[task["id"]] = done
            self.report["setups"].append({"task": task["id"], "seconds": round(time.time() - self.setup["at"], 1),
                                          "problem": done if isinstance(done, str) else None})
            self.prepared, self.setup = self.attached, None
            return True
        return False

    def step(self, setup):
        bpy = self.bpy
        from angelo_agent import engine_link
        godot = self.godot_report()
        if setup["phase"] == "restore":
            if not godot.get("ready"):
                return False
            engine_link.end()
            if engine_link.pending() is not None:
                engine_link.decline()
            shutil.rmtree(self.project / ".angelo", ignore_errors=True)
            for name in ("crate.glb", "barrel.glb"):
                shutil.copy2(self.out / "compare/originals" / name, self.project / name)
            setup["command"] = int(godot.get("handled", 0)) + 1
            (self.out / "compare/godot/command.json").write_text(json.dumps({"n": setup["command"],
                                                                             "edit": compare.CRATE}))
            setup.update(phase="request", since=time.time())
            return False
        if setup["phase"] == "request":
            request = engine_link.pending()
            if request is None or request["asset"].ident != compare.CRATE or request["client"] is None:
                return False
            window = self.main_window()
            with bpy.context.temp_override(window=window):
                engine_link.accept(window, request)   # The person's Open, as the card's (the setup is untimed).
            setup["phase"] = "linked"
            return False
        if setup["phase"] == "linked":
            shown = [item for item in godot.get("previews", ()) if item["asset"] == compare.CRATE
                     and item["at"] >= setup["since"]]
            linked = engine_link.session()
            if not shown or linked is None or linked.ident != compare.CRATE or godot.get("linked") != compare.CRATE:
                return False
            self.shape.setdefault("height", shown[0]["height"])
            self.shape.setdefault("width", shown[0]["width"])
            crate = bpy.data.objects.get("Crate")
            if setup["state"] == "taller":
                crate.scale.z *= compare.TALLER
                setup.update(phase="shaped", key="height", target=compare.TALLER * self.shape["height"])
            elif setup["state"] == "wider":
                crate.scale.x *= compare.WIDER
                setup.update(phase="shaped", key="width", target=compare.WIDER * self.shape["width"])
            else:
                return True
            window = self.main_window()   # Evaluated now, as the event loop does after a person's edit.
            (window.view_layer if window is not None else bpy.context.view_layer).update()
            setup["since"] = time.time()
            return False
        shown = [item for item in godot.get("previews", ()) if item["asset"] == compare.CRATE
                 and item["at"] >= setup["since"] and abs(item[setup["key"]] - setup["target"]) <= compare.TOLERANCE_M]
        if not shown:
            return False
        if setup["state"] == "wider":   # Brief 3's truth: the update Godot last showed, and when.
            self.shape.update(status_version=shown[0]["version"], status_at=shown[0]["at"])
        return True

    def main_window(self):
        from angelo_agent import ui
        return ui.main_window() or self.window

    # The turn ------------------------------------------------------------------------------------------------------

    def attach(self, controller, task, record):
        self.attached += 1
        self.record = record
        if task["id"] == "open":
            self.clicked_at = None
        super().attach(controller, task, record)

    def turn_end(self, record):
        """When the turn ended (the harness saw it idle), on the system clock Godot's recorder stamps with; now for a
        turn that never went idle."""
        if "send_to_idle_ms" in record and "sent_at_unix" in record:
            return record["sent_at_unix"] + record["send_to_idle_ms"] / 1000
        return time.time()

    def recorded(self):
        """Whether Godot's recorder has written past the turn's end. It writes every 0.25 s: graded at once, a push
        acknowledged in the turn's last 0.25 s read as never shown (Opus, design run of 2026-11-03, trials 2 and 3)."""
        record = getattr(self, "record", None)
        return record is None or self.godot_report().get("at", 0) >= self.turn_end(record)

    def decide(self, tool_name):
        return "approve"   # Every card, tool or script, in both arms (the design).

    def jobs_pending(self):
        """After every turn: wait until Godot's recorder covers the turn's end (``recorded``). After brief 4's: then
        answer the barrel's request card with its primary button and wait for Godot to link it. At most
        ``CLICK_WAIT_S``."""
        if not self.recorded():
            return True
        if self.attached != len(compare.BRIEFS) or self.prepared != self.attached - 1:
            return False
        from angelo_agent import engine_link, engine_link_ui
        if self.clicked_at is None:
            request = engine_link.pending()
            if request is None or request["asset"].ident != compare.BARREL:
                return False
            bpy = self.bpy
            action = ("open" if not bpy.data.is_dirty else "save_open" if bpy.data.filepath else "discard_open")
            self.clicked_at = time.time()
            window = self.main_window()
            with bpy.context.temp_override(window=window):
                engine_link_ui._open(action, request)
            return True
        linked = engine_link.session()
        return not (linked is not None and linked.ident == compare.BARREL
                    and self.godot_report().get("linked") == compare.BARREL)

    def grade(self, bpy, scene, task, record, workspace=None, window=None):
        godot, end = self.godot_report(), self.turn_end(record)
        sent, reply = record.get("sent_at_unix", end), scenario_session.final_reply(self.turn_rows())
        record["arm"] = self.report["arm"]
        record["tool_result_bytes"] = sum(len(json.dumps(row.get("result"), default=str)) for row in self.turn_rows()
                                          if isinstance(row, dict) and row.get("role") == "activity"
                                          and row.get("result") is not None)
        if task["id"] not in self.invalid and not self.recorded():   # A harness failure, never the agent's.
            self.invalid[task["id"]] = "Godot's recorder didn't write past the turn's end"
        if task["id"] in self.invalid:
            result = {"passed": False, "invalid": self.invalid[task["id"]]}
        elif task["id"] == "push":
            result = compare.check_push(godot, sent, self.shape["height"], end)
        elif task["id"] == "save":
            result = compare.check_save(self.project / "crate.glb", self.report["setup"]["crate_file_height"],
                                        godot, sent, end)
        elif task["id"] == "status":
            result = compare.check_status(reply, self.shape.get("status_version"),
                                          end - self.shape.get("status_at", end))
        else:
            result = compare.check_open(bpy.data.filepath, self.project / ".angelo/barrel.glb.blend", godot,
                                        [at for at in self.loads if at >= sent], self.clicked_at)
        record["host_check"] = dict(result, success=result["passed"])
        return result["passed"]

    def stopped(self, bpy, scene, task, record, workspace=None, window=None):
        """A turn that ended without grading (stopped, timed out) is still graded, never passed. Called after every
        turn (``eval_live_agent_session.complete``): a graded turn keeps its grade (harness check 3 read every pass as
        a failure, 2026-11-03)."""
        if "host_check" not in record:
            self.grade(bpy, scene, task, record, workspace, window)
            record["host_check_passed"] = False
