# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Approve a script once by its exact bytes, per user and project (``reviewed_scripts``, ``TrustedScripts``).

The consent goes through the real ``TrustedScripts`` mixin on a stand-in chat; a policy is patched where the test
names it. Never a path, a name or a downloaded file: only the person's click on a script they opened, for the bytes
they opened, in the saved project they are in.
"""
import suite_isolation  # noqa: F401

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import reviewed_scripts as rs  # noqa: E402
from angelo_agent.trusted_scripts import TrustedScripts  # noqa: E402

import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())   # Its tests remember scripts as the person does (grant_authority).

CODE = "import bpy\nfor i in range(10):\n    bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))\n"
ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


class StoreTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.store = rs.ReviewedScripts(self.root)
        self.project = rs.project_key("doc-1", str(self.root / "castle.blend"))

    def test_agent_code_never_remembers_a_script(self):
        """The share-link break-it class (2026-11-15): the function behind the card's button refuses agent code, so a
        script that calls it, or the store, itself approves nothing. Forgetting one narrows and is never refused."""
        from angelo_agent import grant_guard
        self.store.add(self.project, CODE + "# kept", "Kept")
        with grant_guard.agent_script():
            with self.assertRaises(PermissionError):
                self.store.add(self.project, CODE, "Build ten cubes")
            with self.assertRaises(PermissionError):
                TrustedScripts.remember_script(SimpleNamespace(), SimpleNamespace(arguments={"code": CODE}))
            self.store.revoke(self.project, rs.digest(CODE + "# kept"))
        self.assertFalse(self.store.allowed(self.project, CODE))
        self.assertFalse(self.store.allowed(self.project, CODE + "# kept"))

    def test_a_write_that_fails_remembers_nothing(self):
        """r2 S-c: the file is written before memory, so a refused or failed write approves nothing for the session."""
        with patch.object(rs.ReviewedScripts, "_save", side_effect=OSError("refused")), self.assertRaises(OSError):
            self.store.add(self.project, CODE, "Build ten cubes")
        self.assertFalse(self.store.allowed(self.project, CODE))

    def test_forgetting_holds_in_memory_even_when_its_write_fails(self):
        """r3 S-4: a forgotten script asks again at once; a failed write is reported, never undone."""
        self.store.add(self.project, CODE, "Build ten cubes")
        with patch.object(rs.ReviewedScripts, "_save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.store.revoke(self.project, rs.digest(CODE))
        self.assertFalse(self.store.allowed(self.project, CODE))

    def test_the_exact_bytes_in_one_project(self):
        self.store.add(self.project, CODE, "Build ten cubes")
        again = rs.ReviewedScripts(self.root)
        self.assertTrue(again.allowed(self.project, CODE))
        self.assertFalse(again.allowed(self.project, CODE + " "), "One changed byte asks again")
        self.assertFalse(again.allowed(rs.project_key("doc-1", str(self.root / "copy.blend")), CODE), "Another path")
        self.assertFalse(again.allowed(rs.project_key("doc-2", str(self.root / "castle.blend")), CODE), "Another id")
        self.assertFalse(again.allowed("", CODE))

    def test_the_file_holds_digests_never_code_or_paths_owner_only(self):
        self.store.add(self.project, CODE, "Build ten cubes")
        raw = (self.root / rs.NAME).read_text(encoding="utf-8")
        self.assertNotIn("primitive_cube_add", raw)
        self.assertNotIn(str(self.root), raw)
        self.assertIn(hashlib.sha256(CODE.encode()).hexdigest(), raw)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((self.root / rs.NAME).stat().st_mode), 0o600)

    def test_list_newest_first_and_remove(self):
        self.store.add(self.project, CODE, "first")
        self.store.add(self.project, CODE + "#2", "  second\nline ")
        listed = self.store.entries(self.project)
        self.assertEqual([entry[1] for entry in listed], ["second line", "first"])
        self.assertTrue(self.store.revoke(self.project, listed[0][0]))
        self.assertFalse(self.store.allowed(self.project, CODE + "#2"))
        self.assertFalse(self.store.revoke(self.project, listed[0][0]))

    def test_bounded_and_unsaved_refused(self):
        for index in range(rs.MAX_PER_PROJECT + 3):
            self.store.add(self.project, "x = %d" % index, "")
        self.assertEqual(len(self.store.entries(self.project)), rs.MAX_PER_PROJECT)
        self.assertFalse(self.store.allowed(self.project, "x = 0"))
        with self.assertRaises(ValueError):
            self.store.add("", CODE, "")
        self.assertEqual(rs.project_key("doc", ""), "")

    def test_a_damaged_or_linked_file_reads_as_empty(self):
        (self.root / rs.NAME).write_text("{bad", encoding="utf-8")
        self.assertEqual(rs.ReviewedScripts(self.root).entries(self.project), [])
        if os.name == "posix":
            (self.root / rs.NAME).unlink()
            target = self.root / "planted.json"
            target.write_text(json.dumps({"version": 1, "projects": {self.project: {
                hashlib.sha256(CODE.encode()).hexdigest(): {"label": "", "at": 1, "bytes": 1}}}}), encoding="utf-8")
            (self.root / rs.NAME).symlink_to(target)
            self.assertFalse(rs.ReviewedScripts(self.root).allowed(self.project, CODE))


class LedgerTests(unittest.TestCase):
    """2.0.0's rule for every standing consent (doc/angelo/grant_operators.rst, "Authority in C"): it is held in C's
    ledger, written only in the person's grant scope, and Python's copy alone never lets a script run unasked."""

    def setUp(self):
        from angelo_agent import grant_authority
        self.ga = grant_authority
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        angelo_guard_twin.strict_for(self)   # The guard's own rule: C exactly, as it runs alone.
        angelo_guard_twin.reset()
        self.addCleanup(angelo_guard_twin.reset)
        self.store = rs.ReviewedScripts(self.root)
        self.project = rs.project_key("doc-1", str(self.root / "castle.blend"))
        self.key = rs.ledger_key(self.project, rs.digest(CODE))

    def held(self):
        return self.ga.holds(self.ga.REVIEWED_SCRIPTS, self.key)

    def test_the_namespace_is_one_of_cs_standard_ones(self):
        """Defined by C as it starts (``define_standard``), before any add-on or script could define it otherwise."""
        self.assertIn(self.ga.REVIEWED_SCRIPTS, angelo_guard_twin._STANDARD)
        self.assertIs(angelo_guard_twin._STANDARD[self.ga.REVIEWED_SCRIPTS], False)
        core = (ADDON.parents[2] / "source/blender/python/intern/bpy_angelo_guard_authority_core.cc").read_text()
        self.assertIn("define(REVIEWED_SCRIPTS, Value(false));", core)

    def test_remembering_is_held_in_c_and_forgetting_ends_it(self):
        self.store.add(self.project, CODE, "Build ten cubes")
        self.assertTrue(self.held())
        self.store.revoke(self.project, rs.digest(CODE))
        self.assertFalse(self.held())
        self.assertFalse(self.store.allowed(self.project, CODE))

    def test_outside_the_persons_own_scope_nothing_is_remembered(self):
        import threading
        errors = []

        def elsewhere():   # Another thread holds no grant scope (the twin's harness scope is this thread's).
            try:
                self.store.add(self.project, CODE, "Build ten cubes")
            except PermissionError as error:
                errors.append(error)
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertFalse(self.held())
        self.assertFalse(self.store.allowed(self.project, CODE))
        self.assertFalse((self.root / rs.NAME).exists(), "Refused in C first: nothing is written")

    def test_python_state_alone_never_lets_a_script_run(self):
        """What an agent script could change in memory or on disk is not the consent: C is."""
        self.store._projects = {self.project: {rs.digest(CODE): {"label": "", "at": 1.0, "bytes": 1}}}
        self.assertFalse(self.store.allowed(self.project, CODE))
        self.store.add(self.project, CODE, "Build ten cubes")
        angelo_guard_twin.remove(self.ga.REVIEWED_SCRIPTS, self.key)
        self.assertFalse(self.store.allowed(self.project, CODE))

    def test_a_saved_list_is_held_in_c_again_when_angelo_reads_it(self):
        self.store.add(self.project, CODE, "Build ten cubes")
        angelo_guard_twin.reset()   # A new Angelo: C holds nothing yet.
        self.assertTrue(rs.ReviewedScripts(self.root).allowed(self.project, CODE), "Angelo's own file, mirrored")
        angelo_guard_twin.reset()
        with angelo_guard_twin.confine():   # Read while an agent script runs: nothing is mirrored, so it asks.
            again = rs.ReviewedScripts(self.root)
        self.assertFalse(again.allowed(self.project, CODE))

    def test_a_write_that_fails_holds_nothing_in_c_either(self):
        with patch.object(rs.ReviewedScripts, "_save", side_effect=OSError("refused")), self.assertRaises(OSError):
            self.store.add(self.project, CODE, "Build ten cubes")
        self.assertFalse(self.held())


SHIPPED = rs.OFFERED   # As the add-on ships, read before setUpModule turns the mechanism on for its own tests.


_twin_set_up = setUpModule   # angelo_guard_twin's hook (the person's harness scope): chained, never replaced (M-2).


def setUpModule():
    """The mechanism as built is tested with Always run on; FailClosedTests checks how it ships (off)."""
    _twin_set_up()
    patcher = patch.object(rs, "OFFERED", True)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class Chat(TrustedScripts):
    """A stand-in chat with the real consent mixin."""

    def __init__(self, directory, project="p" * 64, approval="ASK"):
        self.pending, self.reviewed_calls, self.version = {}, set(), 0
        self.state = {"id": "chat-1", "options": {"approval": approval}}   # 2.0.0 approves in C per chat id.
        self.document, self.document_invalid, self._closing = ("doc", 1), False, False
        self.external_client, self.remote_executor = "", None
        self.store = SimpleNamespace(directory=directory)
        self.project = project

    def script_review_project(self):
        return self.project

    def chat_consents(self):
        return ()

    @property
    def always_allow_scripts(self):
        return False

    def expire_tool(self, call):
        return False


def script_call(code=CODE, identity="call-1"):
    arguments = {"description": "Build ten cubes", "code": code}
    digest = hashlib.sha256(json.dumps({"tool": "angelo_execute_python", "arguments": arguments}, sort_keys=True,
                                       ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return SimpleNamespace(id=identity, name="angelo_execute_python", arguments=arguments, approval=True,
                           executing=False, recovery_review=False, reviewed_script=None, invocation_digest=digest,
                           script_consent=None, script_file=None, automatic=False)


class ConsentTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        rs._stores.clear()
        self.addCleanup(rs._stores.clear)
        self.chat = Chat(self.directory)

    def waiting(self, chat=None, code=CODE, identity="call-1"):
        chat = chat or self.chat
        call = script_call(code, identity)
        chat.pending[call.id] = call
        return call

    def test_offered_only_once_the_person_opened_the_exact_text(self):
        call = self.waiting()
        self.assertFalse(self.chat.offers_remember_script(call))
        self.chat.review_script(call)
        self.assertTrue(self.chat.offers_remember_script(call))

    def test_remembered_bytes_run_unasked_and_a_changed_byte_asks(self):
        call = self.waiting()
        self.chat.review_script(call)
        self.assertTrue(self.chat.remember_script(call))
        later = self.waiting(identity="call-2")
        self.assertEqual(self.chat.script_consent_kind(later), "reviewed_script")
        self.assertTrue(self.chat._automatic_script(later), "Ask-first steps aside only for these exact bytes")
        changed = self.waiting(code=CODE.replace("10", "11"), identity="call-3")
        self.assertIsNone(self.chat.script_consent_kind(changed))

    def test_removal_reaches_a_script_already_waiting(self):
        call = self.waiting()
        self.chat.review_script(call)
        self.chat.remember_script(call)
        later = self.waiting(identity="call-2")
        later.automatic = True
        self.assertTrue(self.chat.script_authorized(later))
        rs.store(self.directory).revoke(self.chat.project, rs.digest(CODE))
        self.assertFalse(self.chat.script_authorized(later), "Checked again at dispatch")

    def test_never_where_a_remembered_script_must_not_run(self):
        store = rs.store(self.directory)
        store.add("p" * 64, CODE, "")
        for name, change in (("read only", lambda c: c.state.update(options={"approval": "READ_ONLY"})),
                             ("external app", lambda c: setattr(c, "external_client", "Claude Desktop")),
                             ("worker chat", lambda c: setattr(c, "remote_executor", object())),
                             ("unsaved", lambda c: setattr(c, "project", "")),
                             ("another project", lambda c: setattr(c, "project", "q" * 64)),
                             ("changed document", lambda c: setattr(c, "document_invalid", True))):
            chat = Chat(self.directory)
            change(chat)
            with self.subTest(name):
                self.assertIsNone(chat.script_consent_kind(self.waiting(chat)))
        with patch("angelo_agent.managed_policy.current", return_value=SimpleNamespace(allow_auto_approval=False)):
            self.assertIsNone(self.chat.script_consent_kind(self.waiting()))

    def test_a_script_touching_preferences_or_angelo_always_asks(self):
        code = "import angelo_agent\n"
        rs.store(self.directory).add("p" * 64, code, "")
        call = self.waiting(code=code)
        self.chat.review_script(call)
        self.assertIsNone(self.chat.script_consent_kind(call))
        self.assertFalse(self.chat.offers_remember_script(call))

    def test_a_card_that_changed_since_its_review_offers_nothing(self):
        call = self.waiting()
        self.chat.review_script(call)
        call.invocation_digest = "0" * 64   # The script was replaced after the person opened it.
        self.assertFalse(self.chat.remember_script(call))
        self.assertEqual(rs.store(self.directory).entries("p" * 64), [])

    def test_code_swapped_between_the_review_and_the_press_is_never_remembered(self):
        """The 2.1.0 break-it review, A-1: the press remembers only the text C approved at the person's review (its
        call's APPROVED_SCRIPTS digest), never what the call holds by then (agent code still running can swap it and
        leave the card's digests as they were)."""
        call = self.waiting()
        self.chat.review_script(call)
        call.arguments["code"] = CODE.replace("10", "11")
        self.assertFalse(self.chat.remember_script(call))
        self.assertEqual(rs.store(self.directory).entries("p" * 64), [])

    def test_a_script_that_runs_code_it_loads_is_never_offered(self):
        """A-2: these bytes can stay the same while the code they load changes, so Always run never covers them."""
        loads = ('exec(bpy.data.texts["build"].as_string())\n', "result = eval(source)\n",
                 "code = compile(text, 'x', 'exec')\n", "module = __import__(name)\n", "import importlib\n",
                 "from importlib import import_module\n", "import runpy\n", "bpy.ops.text.run_script()\n",
                 "bpy.ops.script.python_file_run(filepath=path)\n", "module = bpy.data.texts['t'].as_module()\n",
                 "exec (source)\n")
        for index, code in enumerate(loads):
            with self.subTest(code=code):
                call = self.waiting(code=code, identity="load-%d" % index)
                self.chat.review_script(call)
                self.assertTrue(rs.runs_other_code(code))
                self.assertFalse(self.chat.offers_remember_script(call))
                self.assertFalse(self.chat.remember_script(call))
        for code in (CODE, "obj.name = 'evaluation executed'\n", "executor = 1\nretro_compile = 2\n"):
            with self.subTest(code=code):
                self.assertFalse(rs.runs_other_code(code))
        self.assertEqual(rs.store(self.directory).entries("p" * 64), [])

    def test_any_way_to_reach_code_from_outside_the_script_withholds_the_offer(self):
        """N-3 (round 2): the class, not a list of call names. Code reached through another name (an attribute, an
        alias, a name looked up at run time), an import of anything not in the app or Python's standard library, a
        loader module, or a process that runs another program all withhold Always run this exact script."""
        loads = ("import builtins\nbuiltins.exec(source)\n", "run = exec\nrun(source)\n",
                 "import sys\nsys.path.insert(0, folder)\nimport helper\n", "from helper import build\n",
                 "bpy.utils.execfile(path)\n", "import pickle\nstate = pickle.loads(blob)\n",
                 "import subprocess\nsubprocess.run(['python', path])\n", "import os\nos.system(command)\n",
                 "getattr(builtins, 'ex' + 'ec')(source)\n", "getattr(__builtins__, 'eval')(source)\n",
                 "globals()['__builtins__']['exec'](source)\n", "().__class__.__base__.__subclasses__()\n",
                 "from . import sibling\n", "import marshal\n", "import ctypes\n", "import code\n",
                 "bpy.ops.script.execute_preset(filepath=path, menu_idname='M')\n",
                 "bpy.ops.preferences.addon_enable(module='x')\n", "bpy.data.texts['t'].use_module = True\n",
                 "bpy.utils.load_scripts()\n", "import addon_utils\naddon_utils.enable('x')\n",
                 "loader.exec_module(module)\n", "import multiprocessing\n", "def broken(:\n",
                 "from os import system\nsystem(command)\n", "import os as o\nrun = o.system\nrun(command)\n",
                 "import sys\nsys.modules['os'].system(command)\n")
        for index, code in enumerate(loads):
            with self.subTest(code=code):
                self.assertTrue(rs.runs_other_code(code))
                call = self.waiting(code=code, identity="reach-%d" % index)
                self.chat.review_script(call)
                self.assertFalse(self.chat.offers_remember_script(call))
        ordinary = (CODE, "import bpy, bmesh, math, random\nfrom mathutils import Vector\nimport json, os.path\n"
                          "data = json.loads('{}')\nname = getattr(bpy.context.object, 'name', '')\n"
                          "kind = bpy.context.object.__class__.__name__\nroot = os.path.dirname(bpy.data.filepath)\n",
                    "from bpy_extras import object_utils\nimport collections, itertools, functools\n"
                    "class Grow:\n    def __init__(self):\n        self.size = 2\n",
                    # Blender's own data shares names with ways to run programs and reload code (the suites' scripts).
                    "import bpy\nbpy.context.scene.unit_settings.system = 'METRIC'\n"
                    "cache = bpy.context.preferences.system.memory_cache_limit\n"
                    "image = bpy.data.images['Grid']\nimage.reload()\n")
        for code in ordinary:
            with self.subTest(code=code):
                self.assertFalse(rs.runs_other_code(code))

    def test_a_name_is_judged_the_same_however_the_script_reaches_it(self):
        """Round 3, S-1: one judgement per name, whether the script writes it as a name, an attribute, a constant
        argument to getattr or setattr, or an import; only the app's and an allow-list of Python's own modules are
        imported; the import system's state (sys.modules, sys.path) and star imports withhold it too, as do Python's
        string runners, code objects, and Blender's driver expressions and namespace."""
        loads = ("import os\ngetattr(os, 'system')(command)\n", "getattr(text, 'as_module')()\n",
                 "import shutil\nshutil.os.system(command)\n", "bpy.utils.os.system(command)\n",
                 "import os\ndef run(module):\n    module.system(command)\nrun(os)\n",
                 "import os\nmodule = os\nmodule.system(command)\n", "import os\nos.__dict__['system'](command)\n",
                 "import sys\nsys.modules['subprocess'].run(['python', path])\n",
                 "import sys\nsys.modules['pickle'].loads(blob)\n", "from os import *\nsystem(command)\n",
                 "from math import *\n", "import sys\nsys.path.insert(0, folder)\n",
                 "import pdb\npdb.run(source)\n", "import cProfile\ncProfile.run(source)\n",
                 "import timeit\ntimeit.timeit(source)\n", "import trace\n", "import bdb\n",
                 "import types\nrun = types.FunctionType(code, {})\n", "setattr(function, '__code__', code)\n",
                 "import asyncio\nasyncio.create_subprocess_exec('python', path)\n", "breakpoint()\n",
                 "driver.expression = open(path).read()\n", "bpy.app.driver_namespace['f'] = loaded\n",
                 "bpy.ops.wm.open_mainfile(filepath=path, use_scripts=True)\n", "import xmlrpc\n", "import socketserver\n")
        for code in loads:
            with self.subTest(code=code):
                self.assertTrue(rs.runs_other_code(code))
        ordinary = ("import re\npattern = re.compile(r'\\d+')\n",
                    "import bpy\nclass Grow(bpy.types.Operator):\n    bl_idname = 'object.grow'\n    bl_label = 'Grow'\n"
                    "    def invoke(self, context, event):\n        return self.execute(context)\n",
                    "import platform\nkind = platform.system()\n", "state = dict(obj.__dict__)\n", "keys = {'vars': 1}\n",
                    "from concurrent.futures import ThreadPoolExecutor\n", "driver.expression = 'var * 2'\n",
                    "import os\nname = os.path.basename(bpy.data.filepath)\n",
                    "import math, json, random, itertools, collections, statistics, colorsys, datetime\n",
                    "from types import SimpleNamespace\nimport typing, dataclasses, enum, functools, operator\n")
        for code in ordinary:
            with self.subTest(code=code):
                self.assertFalse(rs.runs_other_code(code))

    def test_a_script_is_judged_once_however_often_its_card_draws(self):
        """Round 3, S-3: a card draws its offer several times a frame; the judgement is kept by the script's digest."""
        import ast
        import uuid
        code = CODE + "# %s\n" % uuid.uuid4().hex
        with patch("ast.parse", wraps=ast.parse) as parse:
            for _ in range(4):
                rs.runs_other_code(code)
        self.assertEqual(parse.call_count, 1)

    def test_the_documentation_promises_no_more_than_the_check(self):
        doc = (Path(__file__).resolve().parents[3] / "doc/angelo/agent_execution.rst").read_text(encoding="utf-8")
        section = doc.split("A build script the agent runs again and again", 1)[1].split("Tests:", 1)[0]
        self.assertNotIn("never offered", section)
        self.assertIn("can't recognise every way", section)

    def test_the_card_never_promises_a_check_text_cannot_make(self):
        """N-3: a check over a script's text can't recognise every way to load code, so the card doesn't say never."""
        from angelo_agent import chat_cards
        self.assertNotIn("never", chat_cards.REMEMBER_LIMIT)
        self.assertIn("can't recognise every way", chat_cards.REMEMBER_LIMIT)

    def test_the_card_says_what_the_consent_does_not_cover(self):
        from angelo_agent import chat_cards
        controller = SimpleNamespace(offers_remember_script=lambda call: True)
        rows = chat_cards.always_allow_rows(controller, None, lambda text, *rest: [text], 100, 14, 0)
        self.assertTrue(any("isn't covered" in row and "code it loads" in row for row in rows), rows)


class WiringTests(unittest.TestCase):
    def test_card_button_comes_first_and_hosted_workers_never_offer_it(self):
        from angelo_agent import chat_cards   # 2.0.1's buttons are a tuple: Always run this exact script comes first.
        offers = SimpleNamespace(offers_remember_script=lambda call: True, offers_session_allow=lambda call: True,
                                 offers_always_allow=lambda call: True)
        with patch.object(chat_cards, "iface_", lambda text, **fields: text):
            self.assertEqual(chat_cards.always_allow_button(offers, None)[0][0], "script_remember")
        cards = (ADDON / "chat_cards.py").read_text(encoding="utf-8")
        self.assertIn('"script_remember"} and shielded(value)', cards)
        from angelo_agent.worker_consent import LeasedConsent
        self.assertFalse(LeasedConsent.offers_remember_script(None, None))
        self.assertFalse(LeasedConsent.reviewed_script_allowed(None, None))
        ui = (ADDON / "trusted_scripts_ui.py").read_text(encoding="utf-8")
        self.assertIn("or agent_script_running():", ui.split("def remember_script_from_card", 1)[1])


class ScriptsTabTests(unittest.TestCase):
    def test_the_rules_page_lists_this_projects_scripts_and_removes_one(self):
        from angelo_agent import rules_sheet
        with tempfile.TemporaryDirectory() as root:
            rs._stores.clear()
            self.addCleanup(rs._stores.clear)
            chat = Chat(Path(root))
            chat.state["id"] = "chat-1"
            rs.store(Path(root)).add(chat.project, CODE, "Build ten cubes")
            ui = SimpleNamespace(bpy=None, state_directory=lambda: root)
            state = {"hits": []}
            rules_sheet.open_sheet(state, chat, scope="scripts", ui=ui)
            sentence, rows = rules_sheet._script_rows(chat)
            self.assertEqual(len(rows), 1)
            self.assertIn("Build ten cubes", rows[0][1])
            self.assertFalse(rules_sheet.dispatch(state, chat, "send", None, ui=ui), "Nothing to save here")
            self.assertTrue(rules_sheet.dispatch(state, chat, rules_sheet.PREFIX + "revoke", rows[0][0], ui=ui))
            self.assertFalse(rs.store(Path(root)).allowed(chat.project, CODE))
            chat.project = ""
            self.assertEqual(rules_sheet._script_rows(chat), ("Save the file first: scripts are remembered per saved "
                                                              "project.", []))


if __name__ == "__main__":
    unittest.main()
