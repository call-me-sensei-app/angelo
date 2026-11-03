# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's engine link tools (doc/angelo/engine_link.rst, "The agent"), on a stand-in ``bpy`` with Godot's
adapter and fake editors.

Covered: the two tools are deferred, the status tool is read-only and the edit tool is approved on a card (never
Auto, never voice), apps reach status at Read and the edit tool at Edit with a window only, while turning the link
on stays out of their reach; every action is refused with its reason when the link is off or nothing is linked and
none turns the link on; STATUS is compact and carries no path; acknowledgements are kept only from editors of the
linked asset's engine and project, about that asset; PUSH and SAVE wait only for editors that acknowledge, report
the rest as unconfirmed, and end on a bounded wait; SAVE refuses when the open file isn't the asset's; a numbered
acknowledgement answers only its own save, an unnumbered one the newest written save; CLOSE ends the link and keeps
the file; the card names the asset and the engine.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import machine_clock  # noqa: E402


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    handlers = SimpleNamespace(persistent=lambda function: function, depsgraph_update_post=[], load_post=[],
                               save_post=[], exit_pre=[])
    timers = SimpleNamespace(registered=set())
    timers.register = lambda function, **kwargs: timers.registered.add(function)
    timers.unregister = timers.registered.discard
    timers.is_registered = lambda function: function in timers.registered
    bpy.app = SimpleNamespace(handlers=handlers, timers=timers, background=True, binary_path="/Apps/Angelo",
                              translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text,
                                                           locale="en_US"))
    bpy.data = SimpleNamespace(filepath="", is_dirty=False)
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]))
    bpy.ops = SimpleNamespace(wm=SimpleNamespace())
    return bpy


BPY = fake_bpy()
SUGGEST = types.ModuleType("angelo_agent.engine_suggest_ui")
SUGGEST.facts = lambda: {"running": ["Godot"], "installed": [], "live_link_on": True}
with mock.patch.dict(sys.modules, {"bpy": BPY, "angelo_agent.engine_suggest_ui": SUGGEST}):
    from angelo_agent import (engine_link, engine_link_agent, engine_link_assets as assets,  # noqa: E402
                              engine_link_facts as facts,
                              engine_link_tool_approvals as approvals, engine_link_tool_host as host,
                              godot_link_assets)
    from angelo_agent.engine_link_tool_spec import (EDIT, ENGINE_LINK_NAMES, ENGINE_LINK_TOOLS, STATUS,  # noqa
                                                    validate_engine_link)
    from angelo_agent.refusal_codes import RefusalError  # noqa: E402
    PACKAGE = sys.modules["angelo_agent"]
sys.modules.setdefault("angelo_agent.engine_link_assets", assets)
sys.modules.setdefault("angelo_agent.godot_link_assets", godot_link_assets)
for _name in ("engine_link", "engine_link_agent", "engine_link_import", "scene_changes", "engine_link_tool_host",
              "engine_link_tool_approvals", "engine_suggest_ui"):
    _package = sys.modules.get("angelo_agent")
    if _package is not None and getattr(getattr(_package, _name, None), "bpy", None) is BPY:
        delattr(_package, _name)


class FakeServer:
    def __init__(self, project):
        self.sent = []
        self.info = {}
        self.project = project

    def editor(self, client, engine="godot", project=None, capabilities=("durable", "live", "acks")):
        self.info[client] = {"engine": engine, "project": str(project or self.project), "project_name": "Game",
                             "version": "4.7.2"}
        if capabilities is not None:
            self.info[client]["capabilities"] = list(capabilities)

    def send(self, client, header, payload=b""):
        self.sent.append((client, header))
        return True

    def broadcast(self, header, payload=b"", only=None):
        targets = [client for client, info in self.info.items() if only is None or only(info)]
        self.sent.extend((client, header) for client in targets)
        return len(targets)

    def clients(self):
        return {client: dict(info) for client, info in self.info.items()}

    def info_of(self, client):
        return dict(self.info.get(client, {}))

    def drain(self):
        return []


class FakeWriteBack:
    def __init__(self):
        self.requests, self.done = [], []

    def request(self, *args, baked=(), stamp=None):
        self.requests.append(args)

    def busy(self):
        return False

    def finished(self):
        items, self.done = self.done, []
        return items


class ToolTests(unittest.TestCase):
    def setUp(self):
        # Modules the host imports when called (scene_changes, studio_agent, ui_text, engine_suggest_ui) see the
        # stand-in bpy only during this test, and nothing they bind on the package outlives it.
        package = sys.modules.get("angelo_agent", PACKAGE)
        bound = dict(vars(package))
        self.addCleanup(lambda: ([delattr(package, key) for key in set(vars(package)) - set(bound)],
                                 vars(package).update(bound)))
        # The modules imported above, whatever other suites imported under these names: code that imports one when
        # called (the card's text reads engine_link) must find the same module these tests drive.
        pinned = {"engine_link": engine_link, "engine_link_agent": engine_link_agent, "engine_link_assets": assets,
                  "engine_link_facts": facts, "godot_link_assets": godot_link_assets}
        self.enterContext(mock.patch.dict(sys.modules, {"bpy": BPY, "angelo_agent": package,
                                                        "angelo_agent.engine_suggest_ui": SUGGEST,
                                                        **{"angelo_agent." + name: module
                                                           for name, module in pinned.items()}}))
        for name, module in pinned.items():
            setattr(package, name, module)
        self.project = Path(tempfile.mkdtemp()).resolve()
        (self.project / "project.godot").write_text("config_version=5\n")
        for name in ("crate.glb", "crate.blend"):
            (self.project / name).write_bytes(b"x")
        self.server = FakeServer(self.project)
        saved = dict(engine_link._state)
        self.addCleanup(lambda: (engine_link._state.clear(), engine_link._state.update(saved)))
        self.writeback = FakeWriteBack()
        engine_link._state.update(server=self.server, pending=None, session=None, opening=None, dirty_at=None,
                                 writeback=self.writeback, facts=facts.new(), error=None, agent_policy=facts.new_policy(),
                                 folder=tempfile.mkdtemp(), version=0, exporting=False)
        BPY.data.filepath, BPY.data.is_dirty = "", False
        BPY.app.background = True

    def link(self, ident="res://crate.glb"):
        asset = godot_link_assets.resolve({"project": str(self.project), "asset": ident})
        engine_link._state["links"] = engine_link._state.get("links", 0) + 1   # As engine_link._begin numbers it.
        engine_link._state["session"] = {"asset": asset, "params": {}, "number": engine_link._state["links"]}
        return asset

    def ack(self, client, **header):
        engine_link._handle(client, dict({"type": "status", "asset": engine_link.session().ident}, **header))

    def run_pending(self, result, ticks=(lambda: None,)):
        """Poll a deferred result as the controller does, calling each of ``ticks`` between polls."""
        if isinstance(result, dict):
            return result
        for tick in ticks:
            tick()
            found = result.poll()
            if found is not None:
                return found
        return None

    # The contract ---------------------------------------------------------------------------------------------

    def test_status_is_read_only_and_the_edit_tool_is_approved_deferred_and_never_voice(self):
        from angelo_agent.external_clients import (EDIT_TOOLS, NEVER_FOR_APPS, READ_TOOLS, WINDOW_ONLY_EDITS,
                                                   tools_for)
        from angelo_agent.plan_kinds import DEFERRED_TOOLS
        from angelo_agent.tool_specs import BOUNDED_MUTATIONS, TOOL_MAP, dynamic_tools
        self.assertEqual(ENGINE_LINK_NAMES, {STATUS, EDIT})
        self.assertLessEqual(ENGINE_LINK_NAMES, DEFERRED_TOOLS)   # A turn without an engine sends only the names.
        self.assertTrue(TOOL_MAP[STATUS]["annotations"]["readOnlyHint"])
        self.assertFalse(TOOL_MAP[EDIT]["annotations"]["readOnlyHint"])
        self.assertNotIn(EDIT, BOUNDED_MUTATIONS)   # Auto never runs it without the card.
        self.assertFalse(ENGINE_LINK_NAMES & {tool["name"] for tool in dynamic_tools()})
        self.assertIn(STATUS, READ_TOOLS)
        self.assertIn(EDIT, EDIT_TOOLS)
        self.assertIn(EDIT, WINDOW_ONLY_EDITS)
        self.assertNotIn(EDIT, tools_for(("read", "edit"), headless=True))
        self.assertIn(STATUS, tools_for(("read",), headless=True))
        self.assertIn("angelo_app_feature", NEVER_FOR_APPS)   # Turning the link on stays the person's.
        self.assertNotIn("angelo_app_feature", tools_for(("read", "edit", "python", "tasks")))

    def test_a_withheld_arm_declares_nothing_of_the_tools(self):
        """The measured script-route arm (ANGELO_WITHHOLD_TOOLS, read at launch): no declaration, guidance, lookup,
        example or app level names the tools, so the arm's model sees what 1.0.8's did."""
        import subprocess
        probe = (
            "import json\n"
            "from angelo_agent import tool_specs, feature_guide, external_clients, codex_text, providers\n"
            "texts = [json.dumps(tool_specs.native_plan_schema()), json.dumps(tool_specs.codex_tools()),\n"
            "         json.dumps(tool_specs.dynamic_tools()), json.dumps(feature_guide.lookup(['engine_link'])[0]),\n"
            "         providers.codex_instructions(short_identifiers=True, deferred=True, plan=True, team=True),\n"
            "         providers.CLAUDE_INSTRUCTIONS, ' '.join(external_clients.tools_for(('read', 'edit', 'python')))]\n"
            "print(json.dumps([name for name in tool_specs.TOOL_MAP if 'engine_link' in name] +\n"
            "                 [index for index, text in enumerate(texts) if 'angelo_engine_link' in text\n"
            "                  or 'the engine live link,' in text]))\n")   # Codex's family sentence names it so.
        for withheld, expected in (("", True), ("angelo_engine_link_status,angelo_engine_link", False)):
            found = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT / "scripts/addons_core"),
                                   env=dict(os.environ, ANGELO_WITHHOLD_TOOLS=withheld), capture_output=True,
                                   text=True, check=True)
            self.assertEqual(bool(json.loads(found.stdout.strip().splitlines()[-1])), expected, withheld)

    def test_the_schemas_are_small(self):
        """Engine-MCP users complain of 13k-token responses; each deferred declaration stays about 1 KB."""
        for tool in ENGINE_LINK_TOOLS:
            self.assertLess(len(json.dumps(tool)), 1100, tool["name"])

    def test_validation(self):
        self.assertEqual(validate_engine_link(STATUS, {}), {})
        self.assertEqual(validate_engine_link(EDIT, {"action": "SAVE"}), {"action": "SAVE"})
        self.assertEqual(validate_engine_link(EDIT, {"action": "OPEN", "asset": " res://crate.glb ", "engine": "GODOT"}),
                         {"action": "OPEN", "asset": "res://crate.glb", "engine": "GODOT"})
        for name, args in ((STATUS, {"action": "PUSH"}), (EDIT, {}), (EDIT, {"action": "ON"}),
                           (EDIT, {"action": "OPEN", "path": "/tmp/x"}), (EDIT, {"action": "SAVE", "engine": "GODOT"}),
                           (EDIT, {"action": "OPEN"}), (EDIT, {"action": "OPEN", "asset": "a\nb"}),
                           (EDIT, {"action": "OPEN", "asset": "x" * 1025}), (EDIT, {"action": "SAVE", "asset": "x"}),
                           (EDIT, {"action": "OPEN", "asset": "x", "engine": "O3DE"}),
                           (EDIT, []), ("angelo_engine", {"action": "SAVE"})):
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                validate_engine_link(name, args)

    # Refusals: never turns the link on ------------------------------------------------------------------------

    def test_every_action_is_refused_while_the_link_is_off_and_none_turns_it_on(self):
        engine_link._state["server"] = None
        with mock.patch.object(engine_link, "start", side_effect=AssertionError("never turns it on")):
            off = host.execute(None, STATUS, {})
            self.assertEqual(off["link"], "off")
            self.assertIn("Only the person turns it on", off["next"])
            for action in ("PUSH", "SAVE", "CLOSE"):
                with self.subTest(action=action), self.assertRaises(RefusalError) as refused:
                    host.execute(None, EDIT, {"action": action})
                self.assertEqual(refused.exception.code, "UNSUPPORTED")
                self.assertIn("this tool never does", str(refused.exception))

    def test_every_action_is_refused_when_nothing_is_linked(self):
        self.server.editor(1)
        for action in ("PUSH", "SAVE", "CLOSE"):
            with self.subTest(action=action), self.assertRaises(RefusalError) as refused:
                host.execute(None, EDIT, {"action": action})
            self.assertIn("No engine asset is linked", str(refused.exception))
        self.assertEqual(self.server.sent, [])

    def test_an_approval_binds_the_linked_session_and_refuses_before_a_card_without_one(self):
        """The card names the asset; the controller re-reads this before running and refuses a change as stale, so
        an approval never acts on an asset linked after the card was shown (the same asset linked again counts too)."""
        with self.assertRaises(RefusalError):
            host.precondition(EDIT, {"action": "SAVE"})
        self.link()
        first = host.precondition(EDIT, {"action": "SAVE"})
        self.assertEqual(first, host.precondition(EDIT, {"action": "SAVE"}))
        self.assertNotIn(str(self.project), repr(first))
        self.link("res://crate.blend")
        self.assertNotEqual(first, host.precondition(EDIT, {"action": "SAVE"}))
        self.link()
        self.assertNotEqual(first, host.precondition(EDIT, {"action": "SAVE"}))

    # STATUS ---------------------------------------------------------------------------------------------------

    def test_status_is_compact_and_names_no_path(self):
        for client in range(1, 9):   # As many editors as the server admits.
            self.server.editor(client, capabilities=("durable", "live", "acks") if client % 2 else None)
        asset = self.link()
        BPY.data.filepath = asset.editable
        facts.pushed(engine_link_agent.link_facts(), 3, 4096, 12.5, 8)
        self.ack(1, code="previewed", version=3, instances=2)
        number = facts.save_started(engine_link_agent.link_facts(), 41, 3, True)
        facts.save_finished(engine_link_agent.link_facts(), number)
        self.ack(1, code="reimported", save=number)
        status = host.execute(None, STATUS, {})
        text = json.dumps(status)
        self.assertLess(len(text), 2500, "a status read stays small with every editor connected")
        self.assertNotIn(str(self.project), text)
        self.assertNotIn(self.project.name, text)
        linked = status["linked"]
        self.assertEqual((linked["asset"], linked["engine"], linked["open_here"]), ("res://crate.glb", "Godot", True))
        self.assertEqual(linked["pushed"]["version"], 3)
        self.assertEqual(linked["saved"]["state"], "written")
        first = status["editors"][0]   # Each editor's acknowledgements are in its own row.
        self.assertEqual((first["previewed"]["instances"], first["reimported"]["save"]), (2, number))
        self.assertEqual([editor["acks"] for editor in status["editors"]], [True, False] * 4)
        self.assertEqual(status["engines_running"], ["Godot"])

    def test_status_carries_the_preflight_hook_when_set(self):
        self.server.editor(1)
        self.link()
        facts.save_started(engine_link._state["facts"], 1, 0, False)   # The report names this save.
        report = {"status": "done", "scene_version": 0, "save_key": facts.save_key(engine_link._state["facts"]),
                  "target": "GODOT", "counts": {"error": 1, "warning": 7},
                  "findings": [{"code": "c%d" % index, "severity": "warning", "subject": "Crate",
                                "message": "at /Users/someone/game/crate.glb", "fix": "x" * 500, "facts": {}}
                               for index in range(8)]}
        with mock.patch.object(host, "PREFLIGHT", lambda asset: report):
            compact = host.status()["linked"]["preflight"]
        self.assertEqual((compact["findings_total"], len(compact["findings"])), (8, host.PREFLIGHT_FINDINGS))
        self.assertEqual(compact["findings"][0], {"code": "c0", "severity": "warning", "subject": "Crate",
                                                  "message": "(text that named a file; Angelo's log has it)"})
        with mock.patch.object(host, "PREFLIGHT", mock.Mock(side_effect=OSError("boom /private/x/y.json"))):
            self.assertEqual(host.status()["linked"]["preflight"]["error"], "OSError")   # Its text named a file.
        with mock.patch.object(host, "PREFLIGHT", False):   # No preflight in this build.
            self.assertNotIn("preflight", host.status()["linked"])

    def test_a_pending_request_is_reported_without_being_answered(self):
        self.server.editor(1)
        engine_link._handle(1, {"type": "open", "id": "7", "project": str(self.project), "asset": "res://crate.glb",
                                "project_name": "Game"})
        status = host.status()
        self.assertEqual(status["request"]["asset"], "res://crate.glb")
        self.assertIsNotNone(engine_link.pending(), "reading never answers the person's card")

    # Acknowledgements -----------------------------------------------------------------------------------------

    def test_acknowledgements_count_only_from_the_assets_editors_about_the_asset(self):
        other = Path(tempfile.mkdtemp()).resolve()
        (other / "project.godot").write_text("config_version=5\n")
        self.server.editor(1)
        self.server.editor(2, project=other)              # Another project.
        self.server.editor(3, engine="unity")              # Another engine.
        self.link()
        for client in (1, 2, 3):
            self.ack(client, code="previewed", version=1)
        engine_link._handle(1, {"type": "status", "asset": "res://other.glb", "code": "previewed", "version": 9})
        engine_link._handle(1, {"type": "status", "asset": "res://crate.glb", "code": "unknown", "version": 9})
        self.assertEqual(set(engine_link_agent.link_facts()["editors"]), {1})
        self.assertEqual(engine_link_agent.link_facts()["editors"][1]["preview"]["version"], 1)
        engine_link._handle(1, {"type": "disconnected"})
        self.assertEqual(engine_link_agent.link_facts()["editors"], {}, "an editor's acks leave with it")

    def test_an_ack_counts_only_for_its_own_save_once_written(self):
        """Break-it F3: an acknowledgement before the asset was written, or echoing another save, answers nothing."""
        known = facts.new()
        first = facts.save_started(known, 7, 1, True)
        for early in ({"code": "reimported"}, {"code": "reimported", "save": first}):
            facts.acknowledge(known, 1, {}, early)   # Nothing was written yet: nothing could be re-imported.
            self.assertIsNone(facts.save_ack(known, 1, first), early)
        facts.save_finished(known, first)
        time.sleep(0.001)
        facts.acknowledge(known, 1, {}, {"code": "reimported"})
        self.assertIsNotNone(facts.save_ack(known, 1, first), "an unnumbered one after the write counts")
        second = facts.save_started(known, 8, 2, False)
        time.sleep(0.001)
        facts.acknowledge(known, 1, {}, {"code": "reimported", "save": first})
        self.assertIsNone(facts.save_ack(known, 1, second), "an echo of the earlier save never answers the newer")
        facts.acknowledge(known, 1, {}, {"code": "reimported", "save": second})
        self.assertIsNotNone(facts.save_ack(known, 1, second))
        for bad in (True, -1, 2 ** 60, "2", 1.5):
            facts.acknowledge(known, 1, {}, {"code": "reimported", "save": bad})
            self.assertIsNone(known["editors"][1]["save"]["save"], bad)

    def test_acknowledgement_fields_are_bounded(self):
        known = facts.new()
        for client in range(20):
            facts.acknowledge(known, client, {"engine": "e" * 500, "project_name": "p" * 500},
                              {"code": "preview_failed", "version": 1, "message": "m" * 5000})
        self.assertEqual(len(known["editors"]), facts.MAX_EDITORS)
        entry = known["editors"][0]
        self.assertEqual((len(entry["engine"]), len(entry["project_name"]), len(entry["preview"]["message"])),
                         (40, 80, facts.MAX_MESSAGE))

    # PUSH -----------------------------------------------------------------------------------------------------

    def push(self, live=True):
        BPY.context.scene = BPY.context.view_layer = None   # A background session follows the context's scene.
        with mock.patch.object(assets, "export", lambda window, path, options: Path(path).write_bytes(b"glb")), \
                mock.patch.object(engine_link.scene_changes, "scene_key", lambda scene, view_layer: None):
            return host.execute(None, EDIT, {"action": "PUSH"})

    def test_push_needs_an_editor_that_shows_live_scenes(self):
        self.server.editor(1, capabilities=("durable", "acks"))
        self.link()
        with self.assertRaises(RefusalError) as refused:
            self.push()
        self.assertIn("SAVE updates the asset", str(refused.exception))

    def test_push_waits_for_the_acknowledging_editors_and_names_the_others(self):
        self.server.editor(1)
        self.server.editor(2, capabilities=None)   # An older plugin: live by its adapter, but no acks.
        self.link()
        pending = self.push()
        self.assertIsNone(pending.poll(), "waits for editor 1")
        self.assertEqual(self.server.sent[-1][1]["type"], "scene")
        self.ack(1, code="previewed", version=1, instances=3)
        result = pending.poll()
        self.assertEqual(result["pushed"]["version"], 1)
        self.assertEqual(result["acknowledged"], [{"engine": "Godot", "project": "Game", "code": "previewed",
                                                   "instances": 3}])
        self.assertEqual(result["unconfirmed"], [{"engine": "Godot", "project": "Game"}])
        self.assertNotIn("no_answer", result)

    def test_push_returns_at_once_when_no_editor_acknowledges(self):
        self.server.editor(1, capabilities=None)
        self.link()
        result = self.push()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["acknowledged"], [])

    def test_a_push_wait_that_runs_out_is_a_result(self):
        self.server.editor(1)
        self.link()
        pending = self.push()
        with mock.patch.object(host, "PUSH_WAIT_S", 0.0):
            result = pending.poll()
        self.assertEqual(result["no_answer"], [{"engine": "Godot", "project": "Game"}])
        self.assertEqual(result["acknowledged"], [])

    def test_a_failed_preview_is_reported(self):
        self.server.editor(1)
        self.link()
        pending = self.push()
        self.ack(1, code="preview_failed", version=1, message="glTFast is missing")
        self.assertEqual(pending.poll()["acknowledged"][0]["message"], "glTFast is missing")

    # SAVE -----------------------------------------------------------------------------------------------------

    def saving(self, asset):
        """``bpy.ops.wm.save_mainfile`` as Blender's: the save handlers run."""
        def save_mainfile(filepath):
            engine_link._save_post(filepath)
            return {"FINISHED"}
        BPY.ops.wm.save_mainfile = save_mainfile
        BPY.data.filepath = asset.editable

    def test_save_refuses_when_the_open_file_isnt_the_assets(self):
        self.server.editor(1)
        self.link()
        BPY.data.filepath = str(self.project / "unrelated.blend")
        BPY.ops.wm.save_mainfile = mock.Mock(side_effect=AssertionError("never saves another file"))
        with self.assertRaises(RefusalError) as refused:
            host.execute(None, EDIT, {"action": "SAVE"})
        self.assertEqual(refused.exception.code, "STALE_CONTEXT")

    def test_save_of_a_glb_waits_for_the_write_back_and_the_reimport(self):
        self.server.editor(1)
        self.server.editor(2, capabilities=None)
        asset = self.link()
        self.saving(asset)
        pending = host.execute(None, EDIT, {"action": "SAVE"})
        self.assertEqual(len(self.writeback.requests), 1)
        self.assertIsNone(pending.poll(), "the write-back is running")
        self.writeback.done.append((self.writeback.requests[-1][0], None))
        with mock.patch.object(assets, "write_stamp"):
            engine_link._written()
        saved = [header for _client, header in self.server.sent if header["type"] == "saved"]
        self.assertEqual(saved[-1]["save"], 1)
        self.assertIsNone(pending.poll(), "written; waiting for the re-import")
        self.ack(1, code="reimported", save=1)
        result = pending.poll()
        self.assertEqual((result["state"], result["save"]), ("written", 1))
        self.assertEqual(result["acknowledged"][0]["code"], "reimported")
        self.assertEqual(result["unconfirmed"], [{"engine": "Godot", "project": "Game"}])

    def test_a_failed_write_back_is_reported(self):
        self.server.editor(1)
        asset = self.link()
        self.saving(asset)
        pending = host.execute(None, EDIT, {"action": "SAVE"})
        self.writeback.done.append((self.writeback.requests[-1][0], "Blender's gltf exporter didn't write the file"))
        engine_link._written()
        result = pending.poll()
        self.assertEqual(result["state"], "failed")
        self.assertIn("exporter", result["error"])

    def test_save_of_a_blend_is_written_at_once(self):
        self.server.editor(1)
        asset = self.link("res://crate.blend")
        self.saving(asset)
        pending = host.execute(None, EDIT, {"action": "SAVE"})
        self.assertEqual(self.writeback.requests, [])
        self.ack(1, code="reimported", save=1)
        self.assertEqual(pending.poll()["acknowledged"][0]["code"], "reimported")

    def test_a_save_wait_ends_when_the_link_does(self):
        self.server.editor(1)
        asset = self.link()
        self.saving(asset)
        pending = host.execute(None, EDIT, {"action": "SAVE"})
        engine_link.end()
        self.assertEqual(pending.poll()["state"], "unknown")

    # Break-it review: paths, size, attribution, waits, window --------------------------------------------------

    def test_no_result_or_refusal_carries_a_path(self):
        """F1: OS, Blender and write-back errors name files; the agent reads only their last names."""
        self.server.editor(1)
        asset = self.link()
        self.saving(asset)
        secret = str(self.project / ".angelo" / "crate.glb.blend@")
        BPY.ops.wm.save_mainfile = mock.Mock(side_effect=RuntimeError("Error: Cannot open file %s for writing" % secret))
        with self.assertRaises(RefusalError) as refused:
            host.execute(None, EDIT, {"action": "SAVE"})
        self.assertNotIn(str(self.project), str(refused.exception))
        self.saving(asset)
        pending = host.execute(None, EDIT, {"action": "SAVE"})
        self.writeback.done.append((self.writeback.requests[-1][0], "PermissionError: [Errno 13] Permission denied: "
                                    "'%s'" % (self.project / "models" / ".angelo-link-x.glb")))
        engine_link._written()
        engine_link._state["error"] = "FileNotFoundError: '%s'" % (self.project / "tmp" / "live.glb")
        facts.push_failed(engine_link_agent.link_facts(), engine_link._state["error"])
        for result in (pending.poll(), host.status()):
            self.assertNotIn(str(self.project), json.dumps(result))
            self.assertNotIn(self.project.name, json.dumps(result))

    def test_status_stays_within_its_budget_whatever_eight_editors_send(self):
        """F2: every field at its maximum, in text that escapes to six bytes a character."""
        for client in range(1, 9):
            self.server.editor(client)
            self.server.info[client].update(project_name="プ" * 400, version="版" * 80)
        self.link()
        for client in range(1, 9):
            self.ack(client, code="preview_failed", version=1, message="失" * 500)
            self.ack(client, code="reimport_failed", save=1, message="敗" * 500)
        status = host.status()
        self.assertLessEqual(len(json.dumps(status)), host.STATUS_BUDGET)
        self.assertEqual(status["editors_summary"]["connected"], 8)
        self.assertEqual(status["editors_summary"]["failed"], 8)
        self.assertEqual(status["linked"]["asset"], "res://crate.glb")
        quiet = [dict(self.server.info[1], project_name="Game", version="4.7.2")] * 8
        self.server.info = dict(enumerate(quiet, 1))
        self.assertIn("editors", host.status(), "an ordinary link lists its editors")

    def test_a_write_back_ends_only_its_own_save_of_its_own_link(self):
        """F3: a write-back of an earlier link of the same asset, or an earlier save, never ends a later one."""
        self.server.editor(1)
        asset = self.link()
        self.saving(asset)
        first = host.execute(None, EDIT, {"action": "SAVE"})
        old_key = self.writeback.requests[-1][0]
        engine_link.end()
        asset = self.link()   # The same asset, linked again.
        self.saving(asset)
        second = host.execute(None, EDIT, {"action": "SAVE"})
        self.assertEqual(first.poll()["state"], "unknown", "the first link ended")
        self.writeback.done.append((old_key, None))
        self.server.sent.clear()
        engine_link._written()
        self.assertEqual(engine_link_agent.link_facts()["save"]["state"], "writing")
        self.assertFalse(any(header["type"] == "saved" for _client, header in self.server.sent))
        self.ack(1, code="reimported", save=self.writeback.requests[-1][0][1])   # Early, before its write.
        self.assertIsNone(second.poll())
        self.writeback.done.append((self.writeback.requests[-1][0], None))
        with mock.patch.object(assets, "write_stamp"):
            engine_link._written()
        self.assertIsNone(second.poll(), "the early ack counts for nothing")
        time.sleep(0.001)
        self.ack(1, code="reimported", save=self.writeback.requests[-1][0][1])
        self.assertEqual(second.poll()["state"], "written")

    def test_a_push_wait_follows_its_link_and_names_editors_that_left(self):
        """F4: another asset's preview never answers this push; an editor that leaves is named, not dropped."""
        self.server.editor(1)
        self.server.editor(2)
        self.link()
        pending = self.push()
        self.link("res://crate.blend")
        self.ack(1, code="previewed", version=2, instances=4)
        self.assertEqual(pending.poll()["state"], "unknown")
        self.link()
        pending = self.push()
        del self.server.info[2]
        engine_link._handle(2, {"type": "disconnected"})
        self.ack(1, code="previewed", version=engine_link._state["version"], instances=1)
        result = pending.poll()
        self.assertEqual([item["code"] for item in result["acknowledged"]], ["previewed"])
        self.assertEqual(result["left"], [{"engine": "Godot", "project": "Game"}])

    def test_push_exports_the_scene_the_link_follows(self):
        """F5: never the chat's own window when another shows the linked scene."""
        self.server.editor(1)
        self.link()
        followed, chats = object(), object()
        with mock.patch.object(engine_link_agent, "followed_window", lambda: followed), \
                mock.patch.object(host.engine_link, "publish", mock.Mock(return_value=0)) as publish, \
                mock.patch("angelo_agent.scene_changes.busy", lambda window: False):
            with self.assertRaises(RefusalError):
                host.execute(chats, EDIT, {"action": "PUSH"})
        publish.assert_called_once_with(followed)

    def test_an_expired_wait_delivers_what_stands_not_a_bare_expiry(self):
        """F6: a wait outlived by a main-thread stall still says the save stands (pending_delivery)."""
        from angelo_agent import pending_delivery
        finished = []
        job = SimpleNamespace(poll=lambda: None, cancel=lambda: None, stopped_result={"save": 3, "stopped": "x"})
        controller = SimpleNamespace(media_jobs={"c": job}, finish_tool=lambda call, result: finished.append(result))
        self.assertTrue(pending_delivery.expire_media_job(controller, SimpleNamespace(id="c", revoked=None)))
        self.assertEqual(finished, [{"save": 3, "stopped": "x", "expired": True}])
        ready = SimpleNamespace(poll=lambda: {"state": "written"}, cancel=lambda: None, stopped_result=None)
        controller.media_jobs["c"] = ready
        pending_delivery.expire_media_job(controller, SimpleNamespace(id="c", revoked=None))
        self.assertEqual(finished[-1], {"state": "written"})
        bare = SimpleNamespace(poll=lambda: None, cancel=lambda: None)
        controller.media_jobs["c"] = bare
        self.assertFalse(pending_delivery.expire_media_job(controller, SimpleNamespace(id="c", revoked=None)))
        controller.media_jobs["c"] = job   # Revoked: the generic expiry says the revocation.
        self.assertFalse(pending_delivery.expire_media_job(controller, SimpleNamespace(id="c", revoked="Stop")))

    def test_paths_with_spaces_or_after_a_colon_never_reach_the_agent(self):
        """Break-it 2-3 and 3-1: whatever surrounds a path, no folder or user name survives; text with no path stays."""
        secrets = ("Jane", "jack", "Secret", "Acme", "Application Support", "Users", "neil", "nas", "Projects", "Network")
        for text in ("Cannot open file '/Users/Jane Doe/Documents/My Game/models/crate.glb'",
                     "write failed:/Users/jack/Secret Project/crate.glb", "copy failed,/Users/jack/Projects/crate.glb",
                     "Permission denied: '/Users/o'neil/Projects/crate.glb'", "~jack/Projects/crate.glb is missing",
                     "smb://nas/share/jack/x.glb", "//nas/share/jack/x.glb", "\u201c/Users/jack/Projects/x\u201d",
                     "'C:\\Users\\Jane Doe\\OneDrive - Acme Corp\\crate.glb'", "Is a directory: '/Users/jack'",
                     "RuntimeError: /Users/jack/NetworkError/x.glb", "see file:///Users/jane/Secret Project/x.glb now"):
            with self.subTest(text=text):
                for secret in secrets:
                    self.assertNotIn(secret, facts.plain(text, 300))
        self.assertEqual(facts.plain("OSError: [Errno 13] Permission denied: '/Users/jack/'", 300),
                         "OSError; Permission denied")
        self.assertEqual(facts.plain(PermissionError(13, "Permission denied", "/Users/jack/x")),
                         "PermissionError: Permission denied")
        self.assertEqual(facts.plain("Blender's gltf exporter didn't write the file"),
                         "Blender's gltf exporter didn't write the file", "text without a path stays")
        stored = "OSError: " + str(OSError(28, "No space left on device", "/Users/" + "x" * 160 + "/jack"))[:200]
        self.assertEqual(facts.plain(stored), "OSError; No space left on device", "a cut path leaks nothing")
        for text in ("Godot couldn't import res://models/crate.glb: missing texture",   # Break-it 4-5: kept.
                     "RuntimeError: Object 'Rig/Body' has 3/4 faces without UVs",
                     "glTF/GLB export failed: unsupported node type", "user://saves/a.res failed"):
            self.assertEqual(facts.plain(text, 300), text)
        for text in (" \u2215Users\u2215jack\u2215Secret\u2215x", "\uff0fUsers\uff0fjack", "smb://nas/share/jack/x",   # 4-6
                     "Macintosh HD:Users:jack:Projects:crate.glb", "file:%2F%2F%2FUsers%2Fjack%2Fx",
                     "failed: 'Jane Doe Secret Project", "http://host/Users/jack/x"):
            with self.subTest(text=text):
                for secret in ("Jane", "jack", "Secret", "nas", "Users"):
                    self.assertNotIn(secret, facts.plain(text, 300))
        for odd in (OSError("abc", "def"), OSError(2 ** 70, "x"), OSError(-1, "x")):   # 4-7: never raises.
            self.assertTrue(facts.plain(odd))

    def test_shortening_always_meets_the_budget(self):
        """Break-it 3-3: whatever strings a result holds, the answer fits (a fixed minimal one at worst)."""
        for result in ({"link": "on", "items": ["x" * 10] * 300},
                       {"link": "on", "k": {str(index): "y" * 8 for index in range(600)}},
                       {"link": "on", "linked": {"asset": "res://" + "\u6a21" * 300}, "rows": ["\u8a9e" * 300] * 50}):
            fitted = host._shorten(json.loads(json.dumps(result)))
            self.assertLessEqual(len(json.dumps(fitted)), host.STATUS_BUDGET)

    def test_a_hard_link_to_a_working_file_is_not_an_asset(self):
        """Break-it 3-5: the same file under another name is still Angelo's working file."""
        self.server.editor(1)
        (self.project / ".angelo").mkdir()
        working = self.project / ".angelo" / "crate.glb.blend"
        working.write_bytes(b"x")
        os.link(working, self.project / "copy.blend")
        with self.assertRaises(RefusalError):
            self.asks(self.chat(1), "res://copy.blend")

    def test_a_working_folder_it_cannot_list_is_never_assumed_clear(self):
        """Break-it 5-H1: a subfolder the scan can't read may hold the file's other name: counted as holding it."""
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        working = self.project / ".angelo" / "sub"
        working.mkdir(parents=True)
        (working / "crate.glb.blend").write_bytes(b"x")
        os.link(working / "crate.glb.blend", self.project / "copy.blend")
        os.chmod(working, 0)
        self.addCleanup(os.chmod, working, 0o700)
        self.assertTrue(assets._holds_inode(self.project / ".angelo", (self.project / "copy.blend").stat()))

    def test_plain_fails_closed_on_every_path_shape(self):
        """Break-it 5-1: a word is kept only when it is no path; every other shape (scripts beside it, a rooted
        Windows path, escaped or encoded slashes, a ``res:///`` path, ``..``, a variable) is rebuilt without it."""
        for text in ("\u30d5\u30a1\u30a4\u30eb/Users/jack/Secret/crate.glb\u3092\u958b\u3051\u307e\u305b\u3093",
                     "\u6587\u4ef6C:\\Users\\jack\\Secret\\crate.fbx\u4e0d\u5b58\u5728",
                     "Fichier\u00e9/Users/jack/Secret/x introuvable", "Cannot open \\Users\\jack\\Secret\\crate.fbx",
                     'reimport failed: {"path":"\\/Users\\/jack\\/Secret\\/crate.glb"}',
                     "Godot couldn't import res:///Users/jack/Secret/crate.glb",
                     "open failed: file:%252F%252FUsers%252Fjack%252FSecret", "failed: &#47;Users&#47;jack&#47;Secret",
                     "import failed: ../../jack/Secret/crate.glb", "write failed: $HOME/jack/Secret/crate.glb",
                     "OSError: [Errno 2] No such file or directory: 'Jack Doe Secret Project"):
            with self.subTest(text=text):
                kept = facts.plain(text, 300)
                self.assertNotIn("jack", kept.lower())
                self.assertNotIn("Secret", kept)
        for text in ("Reimport of /Game/Props/SM_Crate failed: missing material", "Import took ~3 s and failed",
                     "Body / Head have 3 faces without UVs", "Import failed at 12:30:45: missing texture",
                     "LOD 1 / 2 failed: too many bones", "Can't open \u201ccrate.glb\u201d: Doe's 'x' is missing"):
            with self.subTest(text=text):
                self.assertEqual(facts.plain(text, 300), text)

    def test_a_path_glued_to_a_word_or_relative_to_the_home_folder_never_reaches_the_agent(self):
        """Break-it 6-1: a slash inside a word is kept only when none of its parts is an entry at the filesystem root,
        a part of the home folder or the user name; built from this machine's own, so it holds on every platform."""
        import getpass
        home, user = Path.home(), getpass.getuser()
        relative = str(home.relative_to(home.anchor)).replace("\\", "/")
        for text in ("failed at%s/x" % home.as_posix(), "-o%s/out" % home.as_posix(), "_%s" % home.as_posix(),
                     "Cannot open %s/Documents/secret.blend" % relative, "Cannot open %s/secret.blend" % user,
                     "x//nas/share/x", "Macintosh HD:%s" % user, "\u27cbUsers\u27cb%s" % user,
                     "%2525252FUsers%2525252F" + user):
            with self.subTest(text=text):
                self.assertNotIn(user, facts.plain(text, 300))
        root = sorted(name for name in os.listdir(os.path.abspath(os.sep)) if name.isalnum())[0]
        self.assertNotIn("secret", facts.plain("open failed: %s/secret.glb" % root, 300))
        self.assertEqual(facts.plain("JackSmithError: '/Users/jack/a'"), "(text that named a file; Angelo's log has it)",
                         "a name read from a string is never trusted as an exception's (break-it 6-4)")
        self.assertEqual(facts.plain(RuntimeError("bad %s/x" % home.as_posix())), "RuntimeError")
        for text in ("std::runtime_error thrown", "UE::Interchange failed", "/Script/Engine.StaticMesh missing",
                     "Assets/Models/R\u00edg.fbx failed", "fe80::1 refused"):   # Kept (break-it 6, over-blanking).
            with self.subTest(text=text):
                self.assertEqual(facts.plain(text, 300), text)

    def test_a_place_named_with_spaces_another_normal_form_or_a_home_entry_is_still_a_place(self):
        """Break-it 7-2, 7-3: place names are folded as the text is; a name holding a space or quote counts beside a
        separator; the home folder's own entries are places; a fold under percent-encoding is undone."""
        import getpass
        import unicodedata
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        (home / "Documents").mkdir()
        user = unicodedata.normalize("NFD", "Jos\u00e9 O'Doe")
        facts._places_read[:] = []
        self.addCleanup(facts._places_read.clear)
        with mock.patch.object(Path, "home", lambda: home), mock.patch.object(getpass, "getuser", lambda: user):
            for text in ("import failed: Jos\u00e9 O'Doe/Secret/crate.glb", "Documents/AcmeCorp/crate.glb failed",
                         "Users%26%2347%3Bjose", "x Users%E2%88%95jose"):
                with self.subTest(text=text):
                    kept = facts.plain(text, 300)
                    for secret in ("Doe", "Acme", "jose"):
                        self.assertNotIn(secret, kept)
            self.assertEqual(facts.plain("Rig/Body has 3/4 faces", 300), "Rig/Body has 3/4 faces")

    def test_hard_links_are_found_by_a_real_stat_and_a_linked_working_folder_is_never_walked(self):
        """Break-it 6-2: a listing's device is 0 on Windows, so an entry whose number matches is confirmed with a stat
        of its own; and a working folder that is itself a link is refused before any walk."""
        working = self.project / ".angelo"
        working.mkdir()
        (working / "crate.glb.blend").write_bytes(b"x")
        os.link(working / "crate.glb.blend", self.project / "copy.blend")
        info = (self.project / "copy.blend").stat()
        real = os.scandir

        def windows_scandir(path):   # As Windows lists: inode() true, the entry's own stat with no device.
            for entry in list(real(path)):
                yield SimpleNamespace(path=entry.path, inode=entry.inode, is_dir=entry.is_dir,
                                      stat=lambda follow_symlinks=True, entry=entry: SimpleNamespace(
                                          **{name: getattr(entry.stat(follow_symlinks=follow_symlinks), name)
                                             for name in ("st_ino", "st_mode")}, st_dev=0, st_nlink=0))
        with mock.patch.object(assets.os, "scandir", windows_scandir):
            self.assertTrue(assets._holds_inode(working, info))

        def foreign_numbers(path):   # Break-it 7-4: a listing whose numbers aren't the files' own (overlayfs, FUSE).
            for entry in list(real(path)):
                yield SimpleNamespace(path=entry.path, inode=lambda: 0xFFFFFFFF, is_dir=entry.is_dir, stat=entry.stat)
        with mock.patch.object(assets.os, "scandir", foreign_numbers):   # Not held, but the listing can't clear it.
            self.assertTrue(assets._holds_inode(working, (self.project / "crate.blend").stat()))
        self.assertFalse(assets._holds_inode(working, (self.project / "crate.blend").stat()), "with real numbers")
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, True)
        (working / "crate.glb.blend").rename(elsewhere / "crate.glb.blend")
        working.rmdir()
        working.symlink_to(elsewhere, target_is_directory=True)
        with mock.patch.object(assets, "_holds_inode", side_effect=AssertionError("walked")):
            self.assertTrue(assets._in_working_folder(self.project, (self.project / "copy.blend").resolve()))

    def test_plain_is_linear(self):
        """Break-it 5-2: an editor's message may be ~64 KB and plain runs on the main thread; each character once."""
        size = 60000
        for text in ("a" * size, "a:" * (size // 2), ": '" + "x" * size, " '" * (size // 2), "ab/" * (size // 3),
                     "%2" * (size // 2), "\u30d5" * size, ("abc:def " * size)[:size], "'" * size):
            window = machine_clock.start(self)
            facts.plain(text)
            facts.plain(text, 10 ** 6)
            window.assert_within(0.5, "slow on %r..." % text[:6])

    def test_status_is_within_budget_with_one_editor_and_long_fields(self):
        """Break-it 2-4: every string the answer holds is bounded, whatever field it is in."""
        (self.project / ("模" * 60 + ".glb")).write_bytes(b"x")
        self.server.editor(1)
        self.server.info[1].update(project_name="プロジェクト" * 40)
        self.link("res://" + "模" * 60 + ".glb")
        engine_link._state["error"] = "エラー" * 200
        facts.push_failed(engine_link_agent.link_facts(), "失敗" * 200)
        number = facts.save_started(engine_link_agent.link_facts(), 5, 1, True)
        facts.save_finished(engine_link_agent.link_facts(), number, "書き込み" * 200)
        engine_link._state["pending"] = {"asset": engine_link.session(), "client": 1, "id": "", "at": time.monotonic(),
                                         "project_name": "名" * 200, "node": "", "live": True}
        status = host.status()
        self.assertLessEqual(len(json.dumps(status)), host.STATUS_BUDGET)
        self.assertTrue(status["linked"]["asset"].startswith("res://"))

    # OPEN -----------------------------------------------------------------------------------------------------

    def asked(self):
        events = []
        engine_link.hooks["request"] = lambda request: events.append(request["asset"].ident)
        self.addCleanup(engine_link.hooks.__setitem__, "request", None)
        return events

    def test_open_raises_the_persons_card_and_opens_nothing(self):
        from angelo_agent.engine_link_tool_spec import asks_only
        self.server.editor(1)
        events = self.asked()
        result = host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.glb"})
        self.assertEqual((result["asked"], result["opened"]), ("res://crate.glb", False))
        self.assertIn("Nothing is open yet", result["note"])
        self.assertIn("its own chat", result["note"])
        self.assertEqual(events, ["res://crate.glb"])
        request = engine_link.pending()
        self.assertTrue(request["by_agent"])
        self.assertIsNone(request["client"])
        self.assertIsNone(engine_link.session(), "nothing opens inside the tool")
        self.assertEqual(self.server.sent, [], "no editor hears a request it didn't send")
        self.assertTrue(asks_only(EDIT, {"action": "OPEN", "asset": "x"}))
        for action in ("PUSH", "SAVE", "CLOSE"):
            self.assertFalse(asks_only(EDIT, {"action": action}), action)
        self.assertNotIn(str(self.project), json.dumps(result))

    def chat(self, number):
        """A conversation as caller_of reads it (``Controller.person_spoke_at``: when its person last wrote)."""
        return SimpleNamespace(state={"id": "chat-%d" % number}, person_spoke_at=None)

    @staticmethod
    def says(chat):
        """The person sends their own words in ``chat`` now (``Controller.send`` without a host request)."""
        before = time.monotonic()
        while time.monotonic() <= before:
            pass
        chat.person_spoke_at = time.monotonic()

    def asks(self, caller, asset="res://crate.glb"):
        return host.execute(None, EDIT, {"action": "OPEN", "asset": asset}, caller)

    def test_open_cannot_raise_the_card_again_and_again(self):
        """Break-it 1, 2-2, 3-2: one card while one waits; after one the person didn't open, its conversation asks
        again only once the person writes there; after two in a row nobody does until the person acts, however long
        anyone waits or retries; an app never gets a fresh start by waiting."""
        self.server.editor(1)
        events = self.asked()
        first, app = self.chat(1), SimpleNamespace(external_client="claude-code", _request_generation=1)
        self.asks(first)
        for asset in ["res://crate.glb", "res://crate.blend"] * 25:
            with self.assertRaises(RefusalError) as refused:
                self.asks(first, asset)
            self.assertEqual(refused.exception.code, "WRITER_BUSY")
        engine_link.decline()   # Not now.
        with self.assertRaises(RefusalError) as refused:   # Its person silent: never.
            self.asks(first, "res://crate.blend")
        self.assertIn("once they reply there", str(refused.exception))
        self.asks(app, "res://crate.blend")
        engine_link._state["pending"]["at"] -= engine_link.REQUEST_SECONDS + 1   # Ignored: it expires.
        with mock.patch.object(engine_link._state["server"], "drain", lambda: []):
            engine_link._tick()
        self.assertEqual(len(events), 2)
        callers = [self.chat(2), self.chat(3), app, SimpleNamespace(external_client="other-app")]
        for _round in range(48):   # Two not opened in a row: nobody, until the person acts.
            for caller in callers:
                with self.assertRaises(RefusalError):
                    self.asks(caller)
        self.assertEqual(len(events), 2)
        self.says(first)   # The person writes in the first conversation: that one may ask.
        self.asks(first)
        engine_link._withdraw("opened")   # They open it: everyone may ask again.
        self.asks(app, "res://crate.blend")
        self.assertEqual(len(events), 4)

    def test_only_the_persons_own_words_release_a_conversation(self):
        """Break-it 4-1, 5-P: only the person's own send marks it (never Angelo's host requests, escalations, worker
        events or an app's task), and only words after the decline count, not words written while the card waited."""
        source = (ROOT / "scripts/addons_core/angelo_agent/controller.py").read_text(encoding="utf-8")
        start = source.index("    def send(self, text")
        body = source[start:source.index("    def _start_turn(", start)]
        self.assertIn('if host_request is None and not getattr(self, "_external_task", False):', body)
        self.assertIn("self.person_spoke_at = time.monotonic()", body)
        self.assertEqual(source.count("person_spoke_at ="), 1, "set nowhere else (no escalation, no event)")
        self.server.editor(1)
        events, lead = self.asked(), self.chat(1)
        self.asks(lead)
        self.says(lead)   # Written while the card waited, before Not now.
        engine_link.decline()
        with self.assertRaises(RefusalError):
            self.asks(lead, "res://crate.blend")
        self.says(lead)
        self.asks(lead, "res://crate.blend")
        self.assertEqual(len(events), 2)

    def test_one_message_after_the_stop_releases_the_conversation_it_was_written_in(self):
        """Break-it 5-P2: after two declines, the person writing once in a fresh conversation lets that one ask."""
        self.server.editor(1)
        events = self.asked()
        self.asks(self.chat(1))
        engine_link.decline()
        self.asks(self.chat(2), "res://crate.blend")
        engine_link.decline()
        third = self.chat(3)
        self.says(third)
        self.asks(third, "res://crate.blend")
        self.assertEqual(len(events), 3)

    def test_the_engines_own_requests_launder_nothing(self):
        """Break-it 4-2, 4-3: engine requests the person didn't open, however many, never lift a hold."""
        self.server.editor(1)
        events = self.asked()
        app = SimpleNamespace(external_client="claude-code")
        self.asks(self.chat(1))
        engine_link.decline()
        self.asks(self.chat(2), "res://crate.blend")
        engine_link.decline()
        for index in range(40):   # The editor asks and leaves, again and again; nobody opens anything.
            engine_link._handle(1, {"type": "open", "id": str(index), "project": str(self.project),
                                    "asset": "res://crate.blend", "project_name": "Game"})
            engine_link._handle(1, {"type": "disconnected"})
            for caller in (app, self.chat(3 + index)):
                with self.assertRaises(RefusalError):
                    self.asks(caller)
        self.assertEqual(len([event for event in events if event == "res://crate.glb"]), 1)

    def test_turning_the_link_on_again_releases_an_app(self):
        """Break-it 4-4: an app has no chat the person writes in here: opening a request, or turning the link off and
        on (a new policy, engine_link.start), releases it; its refusal says so."""
        self.server.editor(1)
        app = SimpleNamespace(external_client="claude-code")
        self.asks(app)
        engine_link.decline()
        with self.assertRaises(RefusalError) as refused:
            self.asks(app, "res://crate.blend")
        self.assertIn("turns the link off and on", str(refused.exception))
        engine_link._state["agent_policy"] = facts.new_policy()   # As the person's own switch (start) does.
        self.asks(app, "res://crate.blend")

    def test_only_the_persons_own_switch_lifts_held_requests(self):
        """Break-it 6-F3, 7-1: start() keeps every hold while any conversation's agent call executes (a script calling
        the + menu's operator, angelo_app_feature, an app's call), whatever route reached it; no caller says who."""
        source = (ROOT / "scripts/addons_core/angelo_agent/engine_link.py").read_text(encoding="utf-8")
        self.assertIn("def start():", source)
        self.assertIn('held = _state.get("agent_policy") if agent_acting() else None', source)
        self.assertIn("agent_policy=held or facts.new_policy(),", source)
        running = SimpleNamespace(executing=True)
        chats = {"a": SimpleNamespace(pending={"1": SimpleNamespace(executing=False)}), "b": SimpleNamespace(pending={})}
        package = sys.modules["angelo_agent"]
        for controllers, acting in ((chats, False), (dict(chats, c=SimpleNamespace(pending={"2": running})), True)):
            stub = SimpleNamespace(_controllers=controllers)
            with mock.patch.dict(sys.modules, {"angelo_agent.ui": stub}), mock.patch.object(package, "ui", stub,
                                                                                            create=True):
                self.assertIs(engine_link_agent.agent_acting(), acting)
        broken = SimpleNamespace(_controllers=None)
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": broken}), mock.patch.object(package, "ui", broken,
                                                                                          create=True):
            self.assertTrue(engine_link_agent.agent_acting(), "unknown keeps every hold")

    def test_a_declined_asset_waits_and_a_failed_open_is_not_held_against_the_agent(self):
        self.server.editor(1)
        self.asks(self.chat(1))
        engine_link.decline()
        with self.assertRaises(RefusalError) as refused:
            self.asks(self.chat(2))
        self.assertIn("they can open it from the engine", str(refused.exception))
        self.asks(self.chat(3), "res://crate.blend")
        engine_link.decline("failed", "Angelo couldn't use its working folder in the project")   # Open, then failed.
        self.asks(self.chat(4), "res://crate.blend")
        self.assertTrue(engine_link.pending()["by_agent"])


    def test_open_never_preempts_an_editors_request_but_an_editor_preempts_it(self):
        self.server.editor(1)
        engine_link._handle(1, {"type": "open", "id": "7", "project": str(self.project), "asset": "res://crate.glb",
                                "project_name": "Game"})
        with self.assertRaises(RefusalError):
            host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.blend"})
        self.assertEqual(engine_link.pending()["client"], 1)
        engine_link.decline()
        host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.blend"})
        engine_link._handle(1, {"type": "open", "id": "8", "project": str(self.project), "asset": "res://crate.glb",
                                "project_name": "Game"})
        self.assertEqual((engine_link.pending()["client"], engine_link.pending()["asset"].ident), (1, "res://crate.glb"))

    def test_the_persons_open_links_every_editor_of_the_project(self):
        self.server.editor(1)
        self.server.editor(2)
        host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.blend"})
        asset = engine_link.pending()["asset"]
        BPY.ops.wm.open_mainfile = lambda filepath: (setattr(BPY.data, "filepath", filepath), {"FINISHED"})[1]
        BPY.context.window_manager.windows = []
        with mock.patch.object(engine_link.live_windows, "first", lambda *args, **kwargs: None):
            engine_link.accept(None)
        self.assertEqual(engine_link.session(), asset)
        self.assertEqual(sorted(client for client, header in self.server.sent if header["type"] == "opened"), [1, 2])

    def test_open_refuses_what_no_connected_project_holds_and_ambiguity(self):
        other = Path(tempfile.mkdtemp()).resolve()
        (other / "project.godot").write_text("config_version=5\n")
        (other / "crate.glb").write_bytes(b"x")
        self.server.editor(1)
        for asset in ("res://missing.glb", "res://../escape.glb", "res://.angelo/crate.glb.blend", "/etc/passwd"):
            with self.subTest(asset=asset), self.assertRaises(RefusalError) as refused:
                host.execute(None, EDIT, {"action": "OPEN", "asset": asset})
            self.assertEqual(refused.exception.code, "VALIDATION")
            self.assertNotIn(str(self.project), str(refused.exception))
        self.server.editor(2, project=other)
        self.server.info[2]["project_name"] = "Other"
        with self.assertRaises(RefusalError) as refused:
            host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.glb"})
        self.assertIn("name engine and project", str(refused.exception))
        result = host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.glb", "project": "Other"})
        self.assertEqual(result["project"], "Other")
        self.assertEqual(engine_link.pending()["asset"].project, str(other))

    def test_angelos_working_files_are_never_assets_whatever_leads_to_them(self):
        """Break-it 2-6: a link named otherwise, to the working folder spelled in another case, is still refused."""
        self.server.editor(1)
        working = self.project / ".angelo"
        working.mkdir()
        (working / "crate.glb.blend").write_bytes(b"x")
        upper = self.project / ".ANGELO"
        target = upper if upper.exists() else working   # A case-insensitive volume: .ANGELO is .angelo.
        (self.project / "props").symlink_to(target if target is working else upper, target_is_directory=True)
        (self.project / "spelled").symlink_to(self.project / ".ANGELO", target_is_directory=True)
        for asset in ("res://props/crate.glb.blend", "res://spelled/crate.glb.blend", "res://.ANGELO/crate.glb.blend"):
            with self.subTest(asset=asset), self.assertRaises(RefusalError):
                host.execute(None, EDIT, {"action": "OPEN", "asset": asset}, SimpleNamespace())
        self.assertIsNone(engine_link.pending())

    def test_open_by_name_is_refused_where_only_the_engine_can_name_the_source(self):
        unreal = Path(tempfile.mkdtemp()).resolve()
        (unreal / "Game.uproject").write_text("{}")
        self.server.editor(1, engine="unreal", project=unreal)
        with self.assertRaises(RefusalError) as refused:
            host.execute(None, EDIT, {"action": "OPEN", "asset": "/Game/Props/SM_Crate"})
        self.assertIn("Edit in Angelo in Unreal Engine", str(refused.exception))
        self.assertIsNone(engine_link.pending())

    def test_open_of_the_linked_asset_asks_nothing(self):
        self.server.editor(1)
        self.link()
        events = self.asked()
        self.assertEqual(host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.glb"})["already_open"],
                         "res://crate.glb")
        self.assertEqual(events, [])

    def test_only_open_skips_the_tool_card_and_never_in_a_read_only_chat(self):
        """OPEN's request card is the person's approval; PUSH, SAVE and CLOSE always show the tool's own card."""
        from angelo_agent.controller import Controller
        auto = lambda arguments, mode: Controller._auto_bounded(SimpleNamespace(state={}),  # noqa: E731
                                                                SimpleNamespace(name=EDIT, arguments=arguments), mode)
        for mode in ("ASK", "AUTO_BOUNDED"):
            self.assertTrue(auto({"action": "OPEN", "asset": "res://crate.glb"}, mode), mode)
            for action in ("PUSH", "SAVE", "CLOSE"):
                self.assertFalse(auto({"action": action}, mode), (mode, action))
        self.assertFalse(auto({"action": "OPEN", "asset": "res://crate.glb"}, "READ_ONLY"))

    def test_open_is_refused_while_the_link_is_off(self):
        engine_link._state["server"] = None
        for call in (lambda: host.precondition(EDIT, {"action": "OPEN", "asset": "x"}),
                     lambda: host.execute(None, EDIT, {"action": "OPEN", "asset": "x"})):
            with self.assertRaises(RefusalError) as refused:
                call()
            self.assertEqual(refused.exception.code, "UNSUPPORTED")

    # CLOSE and the card ---------------------------------------------------------------------------------------

    def test_close_ends_the_link_and_keeps_the_file(self):
        self.server.editor(1)
        asset = self.link()
        BPY.data.filepath = asset.editable
        result = host.execute(None, EDIT, {"action": "CLOSE"})
        self.assertIsNone(engine_link.session())
        self.assertEqual(BPY.data.filepath, asset.editable)
        self.assertEqual(result["closed"], "res://crate.glb")
        self.assertEqual(self.server.sent[-1][1], {"type": "ended", "asset": "res://crate.glb", "reason": "angelo"})

    def test_the_card_names_the_asset_and_engine(self):
        self.link()
        for action, words in (("PUSH", "live update"), ("SAVE", "re-imports"), ("CLOSE", "Stop editing")):
            card = approvals.message(EDIT, {"action": action})
            text = " ".join(card.values())
            self.assertIn("crate.glb", text, action)
            self.assertIn("Godot", text, action)
            self.assertIn(words, text, action)
            self.assertNotIn(str(self.project), text)


if __name__ == "__main__":
    unittest.main()
