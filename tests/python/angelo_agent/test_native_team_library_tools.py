# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The angelo_team_* tools: contract, prompt gating, discovery, approvals, results and the host path.

* Signed out, every declaration a model receives (Claude plan, Codex tools,
  Codex plan schema, voice tools) is byte-identical to a build without the
  family, and discovery never lists or describes it.
* Signed in, the family is deferred (names only until looked up), never in
  Auto, never offered to collaborators, voice or the external CLI.
* Results never carry links and name people only as "you" / their name.
* Through the real Controller with the fake console: reads run on a worker and
  return without approval; import and save always need approval (Auto
  included), read-only chats refuse them, and failures clean up.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock

import team_library_fixtures as f
from fake_team_console import FakeConsole
from angelo_agent import codex_plan, plan_schema, team_library_access, team_library_results as results, tool_specs
from angelo_agent.approvals import approval_message
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.external_requests import PHASE1_READ_TOOLS
from angelo_agent.routing import RuntimeContext
from angelo_agent.team_library_client import TeamLibraryClient
from angelo_agent.team_library_host import TeamLibraryService, TeamPending
from angelo_agent.team_library_spec import TEAM_EFFECTS, TEAM_NAMES, TEAM_TOOLS, validate_team
from angelo_agent.tool_specs import (BOUNDED_MUTATIONS, COLLABORATOR_TOOLS, TOOL_MAP, TOOLS, codex_tools,
                                     dynamic_tools, native_plan_schema, validate_call)

ITEM = "6f1d8d0c-3a51-4bb2-9a6f-9b1c1d2e3f40"
SIGNED_OUT = frozenset(TOOL_MAP) - TEAM_NAMES


def encoded(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class FakeAccount:
    def __init__(self, signed_in=True, scopes=("api", "generate", "offline_access")):
        self._view = types.SimpleNamespace(signed_in=signed_in, scopes=frozenset(scopes), org_id="org-1",
                                           org_name="Northwind Studio", user_id="user-1")

    def view(self):
        return self._view


class ContractTests(unittest.TestCase):
    def test_the_family_is_appended_deferred_and_never_automatic(self):
        self.assertEqual([t["name"] for t in TOOLS[-len(TEAM_TOOLS):]], [t["name"] for t in TEAM_TOOLS])
        self.assertLessEqual(TEAM_NAMES, plan_schema.DEFERRED_TOOLS)
        self.assertFalse(TEAM_NAMES & BOUNDED_MUTATIONS, "import and save always need a click")
        self.assertFalse(TEAM_NAMES & COLLABORATOR_TOOLS)
        self.assertFalse(TEAM_NAMES & PHASE1_READ_TOOLS)
        self.assertEqual({t["name"] for t in TEAM_TOOLS if not t["annotations"]["readOnlyHint"]}, TEAM_EFFECTS)

    def test_validation_is_exact(self):
        self.assertEqual(validate_call("angelo_team_library_search", {"query": "ship", "limit": 25})["limit"], 25)
        bad = [("angelo_team_library_search", {"limit": 26}), ("angelo_team_library_search", {"space": "org"}),
               ("angelo_team_library_get", {"item_id": "not-a-uuid"}), ("angelo_team_library_get", {}),
               ("angelo_team_library_import", {"item_id": ITEM, "into": "DISK"}),
               ("angelo_team_ips_resolve", {"entities": []}),
               ("angelo_team_ips_resolve", {"entities": [{"entity_id": ITEM}] * 21}),
               ("angelo_team_library_save", {"source": "OBJECTS", "space": "team", "title": "T"}),
               ("angelo_team_library_save", {"source": "VIEW", "view_id": ITEM, "asset_id": ITEM, "space": "team",
                                             "title": "T"}),
               ("angelo_team_library_save", {"source": "VIEW", "view_id": ITEM, "space": "team", "title": " "})]
        for name, args in bad:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                validate_call(name, args)
        saved = validate_team("angelo_team_library_save", {"source": "VIEW", "view_id": ITEM.upper(), "space": "team",
                                                           "title": "Hero", "tags": ["Ship", "ship ", "Hero"]})
        self.assertEqual((saved["view_id"], saved["tags"]), (ITEM, ["hero", "ship"]))


class GatingTests(unittest.TestCase):
    def without_family(self, compute):
        """What ``compute`` declares in a build that never had the family."""
        earlier = [t for t in TOOLS if t["name"] not in TEAM_NAMES]
        with unittest.mock.patch.object(tool_specs, "TOOLS", earlier), unittest.mock.patch.object(codex_plan, "TOOLS", earlier):
            codex_plan._documents.cache_clear()
            try:
                return encoded(compute(None))
            finally:
                codex_plan._documents.cache_clear()

    def test_signed_out_declarations_are_byte_identical_to_a_build_without_the_family(self):
        cases = {"claude plan": native_plan_schema,
                 "codex tools": lambda allowed: codex_tools(allowed, short_identifiers=True, defer=True),
                 "codex function tools": lambda allowed: codex_tools(allowed, short_identifiers=True, defer=False),
                 "voice tools": dynamic_tools, "codex plan schema": codex_plan.output_schema}
        for name, compute in cases.items():
            with self.subTest(name=name):
                self.assertEqual(encoded(compute(SIGNED_OUT)), self.without_family(compute))

    def test_signed_in_the_family_is_named_only(self):
        schema = encoded(native_plan_schema())
        for name in TEAM_NAMES:
            self.assertEqual(schema.count('"%s"' % name), 1, name)
        self.assertNotIn("ip_entity_id", schema)
        (namespace,) = [t for t in codex_tools(None, short_identifiers=True, defer=True) if t["type"] == "namespace"]
        self.assertIn("the team library and IPs", namespace["description"])
        self.assertTrue(all(t["deferLoading"] for t in namespace["tools"] if t["name"] in TEAM_NAMES))
        self.assertFalse({t["name"] for t in dynamic_tools()} & TEAM_NAMES, "voice threads never declare it")

    def test_codex_instructions_name_the_family_only_when_the_thread_declares_it(self):
        from angelo_agent.providers import codex_instructions
        for plan in (False, True):
            plain = codex_instructions(short_identifiers=True, deferred=True, plan=plan)
            team = codex_instructions(short_identifiers=True, deferred=True, plan=plan, team=True)
            self.assertNotIn("angelo_team_", plain)
            self.assertEqual(len(team.encode()) - len(plain.encode()), 153)
            for name in ("angelo_team_library_search", "angelo_team_ips_list", "angelo_team_ips_resolve",
                         "angelo_team_ip_get"):
                self.assertIn(name, team)
        self.assertEqual(codex_instructions(short_identifiers=False, deferred=False, team=True),
                         codex_instructions(short_identifiers=False, deferred=False), "voice keeps INSTRUCTIONS")

    def test_each_request_takes_the_family_only_while_signed_in(self):
        host = types.SimpleNamespace(state={"options": {}}, _request_generation=0, store=None, workspace=None,
                                     references=None, models=[])
        context = RuntimeContext(host, "claude_subscription", {}, "", None, 0)
        for account, expected in ((None, SIGNED_OUT), (FakeAccount(signed_in=False), SIGNED_OUT),
                                  (FakeAccount(scopes=("generate",)), SIGNED_OUT), (FakeAccount(), None)):
            with self.subTest(account=account), unittest.mock.patch.object(team_library_access, "account",
                                                                           return_value=account):
                context.begin({}, [], [])
                self.assertEqual(context.allowed_tools, expected)

    def test_discovery_hides_the_family_while_signed_out(self):
        listed = {t["name"] for t in plan_schema.deferred_index(TEAM_NAMES)["deferred_tools"]}
        self.assertFalse(listed & TEAM_NAMES)
        hidden = plan_schema.lookup("TOOLS", ["angelo_team_library_search"], TEAM_NAMES)
        self.assertEqual((hidden["tools"], hidden["unknown_names"]), ([], ["angelo_team_library_search"]))
        shown = plan_schema.lookup("TOOLS", ["angelo_team_library_search"])
        self.assertEqual(shown["tools"][0]["name"], "angelo_team_library_search")
        self.assertIn("TEAM", shown["guidance"])
        with unittest.mock.patch.object(team_library_access, "account", return_value=None):
            self.assertEqual(team_library_access.hidden_tools(), TEAM_NAMES)
            self.assertIn("Sign in to your Angelo team", team_library_access.refusal())
        with unittest.mock.patch.object(team_library_access, "account", return_value=FakeAccount(scopes=())):
            self.assertIn("lacks the console permission", team_library_access.refusal())


class ResultTests(unittest.TestCase):
    ITEM_DTO = {"id": ITEM, "space": "team", "kind": "model", "title": "Hero ship", "description": "x" * 2000,
                "tags": ["ship"], "mimeType": "model/gltf-binary", "sizeBytes": 1024, "width": None, "height": None,
                "durationMs": None, "thumbnailUrl": "https://r2.example/thumb?sig=secret",
                "createdBy": {"userId": "user-2", "label": "mika@example.test"}, "createdAt": "2026-10-08T10:00:00Z",
                "provenance": {"source": "generated", "prompt": "A ship"}, "usedBy": [], "hiddenUseCount": 2,
                "canEdit": False}

    def test_results_carry_no_links_and_no_email_addresses(self):
        for value in (results.item(self.ITEM_DTO, "user-1"), results.item(self.ITEM_DTO, "user-1", full=True)):
            text = encoded(value)
            self.assertNotIn("http", text)
            self.assertNotIn("@", text)
            self.assertEqual(value["by"], "a teammate")
        self.assertEqual(results.person({"userId": "user-1", "label": "Mika"}, "user-1"), "you")
        self.assertEqual(len(results.item(self.ITEM_DTO, "u")["description"]), 300)
        resolved = results.resolved({"fingerprint": "f", "prompt": {"positive": "p", "negative": "n"}, "entities": [],
                                     "references": [{"slot": 1, "libraryItemId": ITEM, "url": "https://r2/x?sig=1",
                                                     "expiresAt": "t", "kind": "close_up"}]})
        self.assertNotIn("r2", encoded(resolved))

    def test_names_and_file_names_from_either_console(self):
        current = dict(self.ITEM_DTO, fileName="hero_ship.glb", createdBy={"userId": "u2", "name": "Kai", "label": "Kai"})
        self.assertEqual((results.item(current, "u1")["by"], results.item(current, "u1")["file"]), ("Kai", "hero_ship.glb"))
        self.assertEqual(results.person({"userId": None, "name": None, "label": "Former member"}, "u1"), "Former member")
        self.assertEqual(results.person({"userId": "u2", "label": "kai@example.test"}, "u1"), "a teammate")
        self.assertNotIn("file", results.item(self.ITEM_DTO, "u1"), "an earlier console sends no file name")

    def test_importability_is_stated_up_front(self):
        self.assertEqual(results.importable(self.ITEM_DTO), (["SCENE"], None))
        self.assertEqual(results.importable({"kind": "image", "mimeType": "image/png"})[0], ["SCENE", "STUDIO"])
        self.assertIn(".blend", results.importable({"kind": "model", "mimeType": "application/x-blender"})[1])
        self.assertIsNone(results.importable({"kind": "video", "mimeType": "video/mp4"})[0])
        stl = results.importable({"kind": "model", "mimeType": "application/octet-stream", "fileName": "part.stl"})
        self.assertEqual(stl, (None, "Only GLB, binary FBX, OBJ and USD models are imported (this is a .stl file)."))
        self.assertEqual(results.importable({"kind": "model", "mimeType": "application/octet-stream",
                                             "fileName": "hero.FBX"})[0], ["SCENE"])

    def test_readiness_speaks_the_consoles_language(self):
        value = results.readiness({"ready": False, "missing": ["character_sheet", "close_up"], "recommended": [],
                                   "sheet": {"via": None, "missingViews": ["side", "back"]}})
        self.assertEqual(value["missing"][1], "Add at least one close-up of the face.")
        self.assertTrue(value["generation"].startswith("blocked"))


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(team_library_access, "account", return_value=FakeAccount())
        patcher.start()
        self.addCleanup(patcher.stop)
        results.forget_all()

    def test_import_cards_name_the_item_and_the_effect(self):
        results.remember("org-1", ResultTests.ITEM_DTO)
        card = approval_message("angelo_team_library_import", {"item_id": ITEM})
        self.assertIn("“Hero ship” · model/gltf-binary · 1 KB · team space of Northwind Studio", card["body"])
        self.assertIn("Nothing in it runs", card["warning"])
        self.assertIn("Media Studio", approval_message("angelo_team_library_import",
                                                       {"item_id": ITEM, "into": "STUDIO"})["title"])

    def test_save_cards_say_who_will_see_it(self):
        args = {"source": "OBJECTS", "objects": ["scn:%s:1" % ("0" * 32)], "space": "team", "title": "Hero",
                "tags": ["ship"], "ip_entities": [{"entity_id": ITEM, "version": 3}]}
        card = approval_message("angelo_team_library_save", args)
        self.assertIn("everyone in Northwind Studio will see it", card["body"])
        self.assertIn("Records 1 IP entity version", card["body"])
        self.assertIn("only you", approval_message("angelo_team_library_save", dict(args, space="personal"))["body"])


class FakeWindow:
    def __init__(self, viewport=True):
        self.screen = types.SimpleNamespace(areas=[types.SimpleNamespace(type="VIEW_3D" if viewport else "OUTLINER")])
        self.scene = object()


class TeamExecutor:
    """BlenderExecutor's routing of the family, without Blender (no bpy work is reached by these cases)."""

    def __init__(self, window):
        self.window, self.studio_context, self.calls = window, None, []

    def service(self):
        return TeamLibraryService(self.studio_context, self.window)

    def precondition(self, name, arguments):
        return self.service().precondition(name, arguments)

    def execute(self, name, arguments):
        self.calls.append(name)
        return self.service().execute(name, arguments)


class HostCase(unittest.TestCase):
    def setUp(self):
        results.forget_all()
        self.console = FakeConsole()
        self.addCleanup(self.console.close)
        self.library = self.console.library
        self.account = f.signed_in(self, self.console)
        patcher = unittest.mock.patch.object(team_library_access, "account", return_value=self.account)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.set_document(("instance", "scratch.blend", "Scene"))
        self.controller.busy = True
        self.window = FakeWindow()
        self.executor = TeamExecutor(self.window)

    def request(self, name, arguments, key="call"):
        answers = []
        self.controller.request_tool(key, name, arguments, answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        return answers

    def settle(self, answers, timeout=10):
        deadline = time.monotonic() + timeout
        while not answers and time.monotonic() < deadline:
            self.controller.pump_events()
            time.sleep(0.01)
        self.assertTrue(answers, "the call never finished")
        return answers[0]


class HostReadTests(HostCase):
    def test_search_runs_on_a_worker_without_approval(self):
        self.library.add_item("Hero ship", f.glb(), "model/gltf-binary", tags=["ship"])
        answers = self.request("angelo_team_library_search", {"query": "ship"})
        self.assertEqual(answers, [], "the answer arrives from the worker, not inline")
        self.assertEqual(self.controller.status, "Searching the team library")
        result = self.settle(answers)
        self.assertEqual([i["title"] for i in result["items"]], ["Hero ship"])
        self.assertEqual(result["items"][0]["import_into"], ["SCENE"])
        self.assertEqual(results.known(self.account.view().org_id, result["items"][0]["id"])["title"], "Hero ship")

    def test_get_with_preview_adds_the_thumbnail_as_a_chat_image(self):
        identity = self.library.add_item("Hero", f.glb(), "model/gltf-binary", thumbnail=f.webp_header(512, 256))
        result = self.settle(self.request("angelo_team_library_get", {"item_id": identity, "preview": True}))
        (view,) = result["views"]
        self.assertEqual(view["mime"], "image/webp")
        self.assertTrue(self.controller.references.checked_path(view).is_file())
        bomb = self.library.add_item("Bomb", f.glb(), "model/gltf-binary", thumbnail=f.png_header(20000, 20000))
        result = self.settle(self.request("angelo_team_library_get", {"item_id": bomb, "preview": True}, key="bomb"))
        self.assertNotIn("views", result)
        self.assertIn("20000 x 20000", result["preview_note"])

    def test_resolve_refuses_an_unready_character_with_what_is_missing(self):
        ip = self.library.add_ip("Sky Kingdom")
        bram = self.library.add_entity(ip, "character", "Bram")
        result = self.settle(self.request("angelo_team_ips_resolve", {"entities": [{"entity_id": bram}]}))
        self.assertEqual(result["code"], "CHARACTER_NOT_READY")
        self.assertFalse(result["retry"])
        self.assertEqual(result["not_ready"], [{"entity_id": bram, "missing": [
            "Add a character sheet, or turnarounds viewed from the front, side and back.",
            "Add at least one close-up of the face."]}])
        self.assertIn("won't generate this character", result["fix"])

    def test_the_ip_list_tool_pages_on_both_consoles(self):
        for n in range(3):
            self.library.ips[self.library.add_ip("World %d" % n)]["updatedAt"] = "2026-10-08T12:0%d:00.000Z" % n
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.library.legacy = legacy
                first = self.settle(self.request("angelo_team_ips_list", {"limit": 2}, key="a%s" % legacy))
                rest = self.settle(self.request("angelo_team_ips_list", {"limit": 2, "cursor": first["next_cursor"]},
                                                key="b%s" % legacy))
                self.assertEqual([ip["name"] for ip in first["ips"] + rest["ips"]], ["World 2", "World 1", "World 0"])
                self.assertIsNone(rest["next_cursor"])

    def test_signed_out_calls_are_refused_with_how_to_sign_in(self):
        self.account.sign_out()
        result = self.request("angelo_team_library_search", {})
        self.assertIn("Sign in to your Angelo team", result[0]["error"])


class HostEffectTests(HostCase):
    def setUp(self):
        super().setUp()
        self.ship = self.library.add_item("Hero ship", f.glb(images=[f.png()]), "model/gltf-binary")
        self.settle(self.request("angelo_team_library_search", {}, key="search"))

    def pending_call(self):
        return next(call for call in self.controller.pending.values() if call.name.startswith("angelo_team_library_"))

    def test_import_needs_approval_even_in_auto_and_is_refused_in_read_only_chats(self):
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        answers = self.request("angelo_team_library_import", {"item_id": self.ship})
        self.assertEqual(answers, [])
        self.assertTrue(self.pending_call().approval)
        self.assertNotIn("angelo_team_library_import", self.executor.calls)
        self.controller.state["options"]["approval"] = "READ_ONLY"
        refused = self.request("angelo_team_library_import", {"item_id": self.ship}, key="ro")
        self.assertIn("read-only", refused[0]["error"])

    def test_import_preconditions_need_a_looked_up_importable_item(self):
        unknown = self.request("angelo_team_library_import", {"item_id": ITEM}, key="unknown")
        self.assertIn("Look the item up first", unknown[0]["error"])
        self.assertEqual(unknown[0]["error_code"], "VALIDATION")
        self.assertIsNone(self.controller.recovery_barrier.pending)
        into_studio = self.request("angelo_team_library_import", {"item_id": self.ship, "into": "STUDIO"}, key="studio")
        self.assertIn("Only images can be imported into STUDIO", into_studio[0]["error"])
        self.assertEqual(into_studio[0]["error_code"], "VALIDATION")
        blend = self.library.add_item("Old scene", b"BLENDER-v502", "application/x-blender")
        self.settle(self.request("angelo_team_library_get", {"item_id": blend}, key="get"))
        refused = self.request("angelo_team_library_import", {"item_id": blend}, key="blend")
        self.assertIn(".blend files are never imported", refused[0]["error"])
        self.assertEqual(refused[0]["error_code"], "UNSUPPORTED")
        self.assertIsNone(self.controller.recovery_barrier.pending)

    def test_unexpected_import_precondition_failure_remains_closed(self):
        with unittest.mock.patch.object(self.executor, "precondition", side_effect=RuntimeError("Look the item up first")):
            refused = self.request("angelo_team_library_import", {"item_id": self.ship}, key="unexpected")
        self.assertEqual(refused[0]["error_code"], "UNKNOWN")
        self.assertTrue(refused[0]["partial_changes_possible"])
        self.assertNotIn("scene_changed", refused[0])
        self.assertIsNotNone(self.controller.recovery_barrier.pending)
        retry = self.request("angelo_team_library_import", {"item_id": self.ship}, key="retry")
        self.assertEqual(retry[0]["error_code"], "RECOVERY_REQUIRED")
        self.assertNotIn("angelo_team_library_import", self.executor.calls)

    def test_the_import_worker_downloads_checks_and_cleans_up(self):
        service = TeamLibraryService(self.controller, self.window)
        client = TeamLibraryClient(self.account)
        pending = service._library_import(client, {"item_id": self.ship})
        self.assertIsInstance(pending, TeamPending)
        f.wait_for(pending.job.done.is_set)
        self.assertIsNone(pending.job.error)
        self.assertEqual(pending.job.value["facts"]["format"], "glb")
        folder = pending.job.folder
        self.assertTrue(pending.job.value["path"].is_file())
        pending.cancel()
        self.assertFalse(folder.exists(), "the private download folder is removed")

    def test_a_download_that_doesnt_match_the_verified_checksum_is_refused(self):
        tampered = self.library.add_item("Tampered", f.glb(), "model/gltf-binary", sha256="0" * 64)
        pending = TeamLibraryService(self.controller, self.window)._library_import(TeamLibraryClient(self.account),
                                                                                  {"item_id": tampered})
        f.wait_for(pending.job.done.is_set)
        self.assertIn("doesn't match the checksum the library verified", pending.poll()["error"])

    def test_hostile_downloads_are_refused_before_blender(self):
        evil = self.library.add_item("Evil", f.glb({"asset": {"version": "2.0"}, "images": [{"uri": "../../.ssh/id_rsa"}]}),
                                     "model/gltf-binary")
        disguised = self.library.add_item("Disguised", b"BLENDER-v502" + bytes(64), "model/gltf-binary")
        service = TeamLibraryService(self.controller, self.window)
        client = TeamLibraryClient(self.account)
        for identity, text in ((evil, "refers to other files"), (disguised, ".blend files are never imported")):
            with self.subTest(text=text):
                pending = service._library_import(client, {"item_id": identity})
                f.wait_for(pending.job.done.is_set)
                result = pending.poll()
                self.assertIn(text, result["error"])
                self.assertEqual(result["error_code"], "UNSUPPORTED", "a refused file is a closed refusal")
                self.assertFalse(pending.job.folder.exists())

    def test_a_refused_file_keeps_later_scene_imports_open(self):
        evil = self.library.add_item("Evil", f.glb({"asset": {"version": "2.0"}, "images": [{"uri": "../x.png"}]}),
                                     "model/gltf-binary")
        self.settle(self.request("angelo_team_library_get", {"item_id": evil}, key="get"))
        answers = self.request("angelo_team_library_import", {"item_id": evil}, key="evil")
        self.controller.execute_tool(self.pending_call(), self.executor, self.controller.document)
        refused = self.settle(answers)
        self.assertIn("refers to other files", refused["error"])
        self.assertEqual(refused["error_code"], "UNSUPPORTED")
        self.assertIsNone(self.controller.recovery_barrier.pending)
        later = self.request("angelo_team_library_import", {"item_id": self.ship}, key="ship")
        self.assertEqual(later, [], "the next import waits for its approval; the barrier did not refuse it")
        self.assertTrue(self.pending_call().approval)

    def finished(self, error, changes_scene):
        from angelo_agent.team_library_host import Job
        job = Job(lambda cancel: {})
        f.wait_for(job.done.is_set)

        def finish(value):
            raise error
        return TeamPending(job, finish, status_text="", activity_note="", timeout=1, changes_scene=changes_scene).poll()

    def test_a_scene_import_claims_an_unchanged_scene_only_when_refused_early_or_verified(self):
        from angelo_agent.team_library_files import ImportFailed, NotImported, Refused
        unchanged = ((NotImported("Leave the active editing or sculpt mode before importing."), "UNSUPPORTED"),
                     (Refused("This build of Angelo can't import USD files; ask for the model as GLB or FBX."), "UNSUPPORTED"),
                     (ImportFailed("Blender didn't finish importing this GLB. Nothing from it was kept.",
                                   scene_unchanged=True), None))
        for error, code in unchanged:
            with self.subTest(error=type(error).__name__):
                result = self.finished(error, True)
                self.assertIs(result["scene_changed"], False)
                self.assertEqual(result.get("error_code"), code)
                self.assertNotIn("partial_changes_possible", result)
        unverified = (ImportFailed("Blender didn't finish importing this GLB. Removing what it added could not be "
                                   "confirmed; check the scene before continuing.", scene_unchanged=False),
                      KeyError("tagging failed after the undo step"))
        for error in unverified:
            with self.subTest(error=type(error).__name__):
                result = self.finished(error, True)
                self.assertNotIn("scene_changed", result)
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("error_code", result, "an unclassified failure fails closed")
                self.assertIn("check the scene", result["error"].lower())
                self.assertNotIn("nothing was changed", result["error"])
        studio = self.finished(KeyError("studio library"), False)
        self.assertIs(studio["scene_changed"], False, "a call that never changes the scene may say so")

    def test_an_unverified_scene_import_failure_raises_the_barrier_without_an_unchanged_claim(self):
        from angelo_agent import team_library_host
        from angelo_agent.team_library_files import ImportFailed
        failure = ImportFailed("Blender didn't finish importing this GLB. Removing what it added could not be confirmed; "
                               "check the scene before continuing.", scene_unchanged=False)
        fake = types.SimpleNamespace(import_into_scene=unittest.mock.Mock(side_effect=failure))
        with unittest.mock.patch.dict(sys.modules, {"angelo_agent.team_library_import": fake}), \
                unittest.mock.patch.object(team_library_host, "_window_for", return_value=self.window):
            answers = self.request("angelo_team_library_import", {"item_id": self.ship}, key="unverified")
            self.controller.execute_tool(self.pending_call(), self.executor, self.controller.document)
            result = self.settle(answers)
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("scene_changed", result)
        self.assertIsNotNone(self.controller.recovery_barrier.pending)

    def test_transfer_failures_and_the_concurrency_limit_are_closed_refusals(self):
        from angelo_agent import team_library_host
        from angelo_agent.team_library_transfer import Cancelled, TransferError
        self.assertEqual(TransferError("Team storage refused the download (HTTP 403).", "EXPIRED").refusal_code, "EXPIRED")
        self.assertEqual(Cancelled("The download was stopped.").refusal_code, "CANCELLED")
        tampered = self.library.add_item("Tampered", f.glb(), "model/gltf-binary", sha256="0" * 64)
        self.settle(self.request("angelo_team_library_get", {"item_id": tampered}, key="get"))
        answers = self.request("angelo_team_library_import", {"item_id": tampered}, key="tampered")
        self.controller.execute_tool(self.pending_call(), self.executor, self.controller.document)
        refused = self.settle(answers)
        self.assertIn("doesn't match the checksum", refused["error"])
        self.assertEqual(refused["error_code"], "UNSUPPORTED")
        self.assertIs(refused["scene_changed"], False)
        self.assertIsNone(self.controller.recovery_barrier.pending)
        with unittest.mock.patch.object(team_library_host, "MAX_ACTIVE", 0):
            answers = self.request("angelo_team_library_import", {"item_id": self.ship}, key="busy")
            self.controller.execute_tool(self.pending_call(), self.executor, self.controller.document)
        busy = self.settle(answers)
        self.assertIn("four run at a time", busy["error"])
        self.assertEqual(busy["error_code"], "LIMIT_EXCEEDED")
        self.assertNotIn("partial_changes_possible", busy)
        self.assertIsNone(self.controller.recovery_barrier.pending)

    def test_only_a_refused_file_is_classified_when_the_import_finishes(self):
        from angelo_agent.team_library_files import Refused
        from angelo_agent.team_library_host import Job
        cases = ((Refused("This USD file composes other USD files (x.usda); only self-contained USD files are imported."),
                  "UNSUPPORTED"), (RuntimeError("Blender didn't finish importing this USDA."), None))
        for error, code in cases:
            with self.subTest(error=type(error).__name__):
                job = Job(lambda cancel: {})
                f.wait_for(job.done.is_set)

                def finish(value, error=error):
                    raise error
                result = TeamPending(job, finish, status_text="", activity_note="", timeout=1).poll()
                self.assertEqual(result["error"], str(error))
                self.assertEqual(result.get("error_code"), code, "anything else stays unclassified and fails closed")


class SaveTests(HostCase):
    """The upload half of angelo_team_library_save; Blender's preparation is a fake module here."""

    def setUp(self):
        super().setUp()
        self.file = os.path.join(self.directory.name, "render.png")
        with open(self.file, "wb") as handle:
            handle.write(f.png(16, 9))
        prepared = {"path": __import__("pathlib").Path(self.file), "mime": "image/png", "kind": "image",
                    "extension": ".png", "width": 16, "height": 9, "size": os.path.getsize(self.file),
                    "provenance": {"source": "upload", "scene": "Scene", "app": {"name": "Angelo", "version": "5.2"}}}
        fake = types.ModuleType("angelo_agent.team_library_save")
        fake.prepare = lambda controller, window, args, folder: dict(prepared)
        fake.check_source = lambda controller, window, args: ("signature",)
        patcher = unittest.mock.patch.dict(sys.modules, {"angelo_agent.team_library_save": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.args = {"source": "VIEW", "view_id": ITEM, "space": "team", "title": "Castle at dusk",
                     "description": "Establishing shot", "tags": ["castle"],
                     "ip_entities": [{"entity_id": ITEM, "version": 2}]}

    def save(self):
        service = TeamLibraryService(self.controller, self.window)
        pending = service.execute("angelo_team_library_save", self.args)
        f.wait_for(pending.job.done.is_set)
        return pending.poll()

    def test_a_save_uploads_and_completes_with_metadata(self):
        self.library.complete_busy = 2  # The console's image slots are full twice: 429 + Retry-After.
        result = self.save()
        self.assertEqual(result["saved"]["title"], "Castle at dusk")
        self.assertEqual(result["visible_to"], "everyone in Northwind Studio")
        item = self.library.items[result["item_id"]]
        self.assertEqual((item["_status"], item["tags"], item["width"]), ("ready", ["castle"], 16))
        self.assertEqual(item["provenance"]["source"], "upload")
        self.assertFalse(result["blender_undoable"])

    def test_the_upload_sends_the_signed_checksum_header(self):
        result = self.save()
        with open(self.file, "rb") as handle:
            self.assertEqual(self.library.items[result["item_id"]]["sha256"], hashlib.sha256(handle.read()).hexdigest())
        (put,) = [r for r in self.console.requests if r[0] == "PUT"]
        self.assertIn("x-amz-checksum-sha256", {name.lower() for name in put[3]})

    def test_content_the_console_refuses_is_reported_and_nothing_kept(self):
        with open(self.file, "wb") as handle:  # A JPEG where the upload declares PNG.
            handle.write(f.jpeg_header(16, 9))
        sys.modules["angelo_agent.team_library_save"].prepare = lambda *args: {
            "path": __import__("pathlib").Path(self.file), "mime": "image/png", "kind": "image", "extension": ".png",
            "size": os.path.getsize(self.file), "provenance": {"source": "upload"}}
        result = self.save()
        self.assertTrue(result["error"].startswith("The team library refused the file: This isn't a PNG file"))
        self.assertEqual([i for i in self.library.items.values() if i["_status"] == "pending"], [])

    def test_a_failed_upload_deletes_its_reservation(self):
        self.library.fail["/complete"] = (400, "UPLOAD_INVALID", "The uploaded file doesn't match what was declared.")
        result = self.save()
        self.assertIn("doesn't match what was declared", result["error"])
        self.assertEqual([i for i in self.library.items.values() if i["_status"] == "pending"], [])
        self.assertIn("DELETE", [r[0] for r in self.console.requests])

    def test_save_needs_approval(self):
        answers = self.request("angelo_team_library_save", self.args)
        self.assertEqual(answers, [])
        self.assertTrue(self.controller.pending[next(iter(self.controller.pending))].approval)


if __name__ == "__main__":
    unittest.main()
