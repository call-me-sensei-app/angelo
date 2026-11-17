# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The default reasoning effort is decided in one place (``chat_settings.default_effort``).

The rule (maintainer's ruling, 2026-11-12): a chat whose person chose no effort
runs at "high" wherever its model offers "high", else at the highest level the
model offers; a model named in ``DEFAULT_EFFORT_EXCEPTIONS`` takes its own
level. Only the default moved: an effort the person picked and saved is sent
as it is. Pinned here: the helper, every coding model of the registry, the
effort each runtime's request sends, a model change, a saved choice, the
launch keys that must not depend on effort, and (by reading the add-on's
source) that no call site resolves an effort its own way or hard-codes a level,
the ruled exceptions named with their reason.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import agent_runtime, chat_settings, provider_registry, runtime_launch, runtime_prewarm  # noqa: E402
from angelo_agent.chat_popovers import EFFORT_NAMES, effective_effort  # noqa: E402
from angelo_agent.chat_settings import (CLAUDE_EFFORTS, EFFORTS, carried_effort, chat_effort, clean_options,  # noqa: E402
                                        codex_turn_options, default_effort, normalize_models, request_effort,
                                        stored_options)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

ADDON = ROOT / "scripts/addons_core/angelo_agent"


def model(identity, *efforts):
    return {"id": identity, "name": identity, "default": True, "efforts": list(efforts), "fast_tier": None, "images": True,
            "runtime_default_alias": False}


def chat(models, **options):
    """A request's context: the chat's options, its connection's settings and its catalog."""
    return SimpleNamespace(state={"options": stored_options(options)}, settings={}, models=list(models))


def coding_models():
    for mode in sorted(provider_registry.CODING_MODES):
        for entry in provider_registry.SPECS[mode].get("models", ()):
            yield mode, entry


class TheRule(unittest.TestCase):
    def test_high_wherever_the_model_offers_it(self):
        for efforts in (("low", "medium", "high"), ("high",), ("none", "high"), ("low", "high", "max"),
                        ("low", "medium", "high", "xhigh"), ("max", "high", "low"), EFFORTS, CLAUDE_EFFORTS):
            with self.subTest(efforts=efforts):
                self.assertEqual(default_effort(efforts), "high")

    def test_without_high_it_is_the_highest_level_offered_never_the_first_listed(self):
        for efforts, expected in ((("low", "medium"), "medium"), (("medium", "low"), "medium"), (("low",), "low"),
                                  (("none", "minimal", "low"), "low"), (("minimal", "none"), "minimal"),
                                  (("xhigh", "max"), "max"), (("low", "invented", "medium"), "medium")):
            with self.subTest(efforts=efforts):
                self.assertEqual(default_effort(efforts), expected)

    def test_a_model_that_advertises_no_level_takes_the_default_itself(self):
        for efforts in ((), [], None, ("invented",)):
            with self.subTest(efforts=efforts):
                self.assertEqual(default_effort(efforts), "high")

    def test_an_excepted_model_takes_its_own_level_only_where_it_offers_it(self):
        with patch.dict(chat_settings.DEFAULT_EFFORT_EXCEPTIONS, {"capped": "medium"}):
            self.assertEqual(default_effort(("low", "medium", "high"), "capped"), "medium")
            self.assertEqual(default_effort((), "capped"), "medium", "no advertised level: the exception still holds")
            self.assertEqual(default_effort(("low", "high"), "capped"), "high", "a level it does not offer is never sent")
            self.assertEqual(default_effort(("low", "medium", "high"), "another"), "high")
            self.assertEqual(chat_effort(stored_options({}), model("capped", "low", "medium", "high")), "medium")
            self.assertEqual(chat_effort(stored_options({"effort": "high"}), model("capped", "low", "medium", "high")),
                             "high", "the person's choice outranks the exception")

    def test_a_catalog_s_own_default_is_never_consulted(self):
        listed = normalize_models([{"model": "m", "defaultReasoningEffort": "low", "isDefault": True,
                                    "supportedReasoningEfforts": [{"reasoningEffort": level} for level in ("low", "medium", "high")]}])
        self.assertNotIn("default_effort", listed[0], "a stored default could be read in place of the rule")
        self.assertEqual(chat_effort(stored_options({}), listed[0]), "high")
        self.assertEqual(effective_effort(dict(listed[0], default_effort="low"), ""), "high")


class EveryCodingModel(unittest.TestCase):
    def test_the_registry_lists_coding_models(self):
        self.assertGreaterEqual(len(list(coding_models())), 5)

    def test_every_model_s_default_is_high_where_it_offers_high(self):
        """High, except a model the maintainer named (chat_settings.DEFAULT_EFFORT_EXCEPTIONS: Carbon at medium)."""
        exceptions = chat_settings.DEFAULT_EFFORT_EXCEPTIONS
        for mode, entry in coding_models():
            with self.subTest(mode=mode, model=entry["id"]):
                offered = [level for level in EFFORTS if level in entry["efforts"]]
                expected = "high" if "high" in offered or not offered else offered[-1]
                if exceptions.get(entry["id"]) in offered:
                    expected = exceptions[entry["id"]]
                self.assertNotIn("default_effort", entry)
                self.assertEqual(default_effort(entry["efforts"], entry["id"]), expected)
                self.assertEqual(chat_effort(stored_options({}), entry), expected)
                self.assertEqual(codex_turn_options(chat([entry]))["effort"], expected)
                self.assertEqual(request_effort(chat([entry]), stored_options({}), "codex"), expected)
                self.assertEqual(effective_effort(entry, ""), expected, "the chip shows the effort the request sends")

    def test_each_exception_names_a_coding_model_that_offers_its_level(self):
        """An exception for a model no connection offers, or at a level it lacks, would silently do nothing."""
        models = {entry["id"]: entry for _mode, entry in coding_models()}
        self.assertEqual(chat_settings.DEFAULT_EFFORT_EXCEPTIONS, {"gemini-4-carbon": "medium"})
        for identity, level in chat_settings.DEFAULT_EFFORT_EXCEPTIONS.items():
            with self.subTest(model=identity):
                self.assertIn(identity, models)
                self.assertIn(level, models[identity]["efforts"])
                self.assertEqual(default_effort(models[identity]["efforts"], identity), level)

    def test_a_custom_provider_s_launch_starts_at_its_model_s_default(self):
        self.assertTrue(provider_registry.EXTERNAL_CODEX_MODES)
        for mode in sorted(provider_registry.EXTERNAL_CODEX_MODES):
            wire = provider_registry.runtime_wire(mode)
            # A mode on a wire only Angelo's runtime has launches as that runtime, with the wire declared.
            runtime = (patch.object(agent_runtime, "kind", return_value="bundled") if wire else patch.dict({}))
            wires = list(agent_runtime.PIN.get("wire_apis", ())) + ([wire] if wire else [])
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory, runtime, \
                    patch.dict(agent_runtime.PIN, {"wire_apis": wires}):
                first = provider_registry.SPECS[mode]["models"][0]
                command, _env = runtime_launch.codex_command("/official/codex", mode, directory, True, api_key="test-key")
                self.assertIn('model_reasoning_effort="%s"' % default_effort(first["efforts"], first["id"]), command)
                self.assertEqual(sum(item.startswith("model_reasoning_effort=") for item in command), 1)

    def test_a_codex_launch_without_a_pinned_model_starts_at_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            command, _env = runtime_launch.codex_command("/official/codex", "codex_subscription", directory)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertEqual(sum(item.startswith("model_reasoning_effort=") for item in command), 1)


class WhatARequestSends(unittest.TestCase):
    SOL = model("gpt-6.1-sol", "low", "medium", "high")

    def test_a_new_chat_holds_no_choice_and_sends_the_default(self):
        self.assertEqual(stored_options({})["effort"], "")
        self.assertEqual(clean_options(None)["effort"], "")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(SessionStore(directory).new()["options"]["effort"], "")
            controller = Controller(directory)
            try:
                controller.models = [self.SOL]
                self.assertEqual(codex_turn_options(controller)["effort"], "high")
            finally:
                controller.close()

    def test_codex_sends_the_default_of_the_model_it_runs(self):
        for efforts, expected in ((("low", "medium", "high"), "high"), (("low", "medium"), "medium"), ((), "high"),
                                  (("none", "high"), "high"), (("low", "high", "max"), "high")):
            with self.subTest(efforts=efforts):
                self.assertEqual(codex_turn_options(chat([model("m", *efforts)]))["effort"], expected)
        self.assertEqual(codex_turn_options(chat([]))["effort"], "high", "before its catalog is read")

    def test_claude_sends_the_default_among_what_claude_code_accepts(self):
        opus = model("opus", "low", "medium", "high", "xhigh", "max")
        self.assertEqual(request_effort(chat([opus]), stored_options({}), "claude"), "high")
        self.assertEqual(request_effort(chat([model("small", "low", "medium")]), stored_options({}), "claude"), "medium")
        self.assertEqual(request_effort(chat([]), stored_options({}), "claude"), "high", "before its catalog is read")
        self.assertEqual(request_effort(SimpleNamespace(state={}), stored_options({}), "claude"), "high", "no catalog at all")
        self.assertEqual(request_effort(chat([model("odd", "none", "minimal")]), stored_options({}), "claude"), "high",
                         "none of the model's levels is one Claude Code accepts")
        self.assertEqual(request_effort(chat([opus]), stored_options({"effort": "minimal"}), "claude"), "high",
                         "a level Claude Code does not accept is never sent")

    def test_no_request_ever_sends_the_unset_marker(self):
        catalogs = [[], [model("m")], [model("m", "low")], [model("m", "low", "medium", "high")], [model("m", "invented")]]
        for models in catalogs:
            for effort in ("", None, "invented", 7, *EFFORTS):
                for runtime in ("codex", "claude"):
                    with self.subTest(models=models, effort=effort, runtime=runtime):
                        sent = request_effort(chat(models, effort=effort), stored_options({"effort": effort}), runtime)
                        self.assertIn(sent, CLAUDE_EFFORTS if runtime == "claude" else EFFORTS)
                self.assertIn(codex_turn_options(chat(models, effort=effort))["effort"], EFFORTS)


class ASavedChoiceStays(unittest.TestCase):
    def test_every_level_the_person_can_pick_is_sent_as_picked(self):
        every = model("m", *EFFORTS)
        for level in EFFORTS:
            with self.subTest(level=level):
                self.assertEqual(stored_options({"effort": level})["effort"], level)
                self.assertEqual(codex_turn_options(chat([every], effort=level))["effort"], level)
                self.assertEqual(effective_effort(every, level), level)
        claude = model("opus", *CLAUDE_EFFORTS)
        for level in CLAUDE_EFFORTS:
            with self.subTest(level=level, runtime="claude"):
                self.assertEqual(request_effort(chat([claude], effort=level), stored_options({"effort": level}), "claude"), level)

    def test_a_chat_saved_at_low_is_still_low_after_a_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            state = store.new()
            state["options"] = {"model": "gpt-6.1-sol", "effort": "low", "fast": False, "approval": "AUTO_BOUNDED"}
            store.save(state)
            loaded = SessionStore(directory).load()   # A new store on the same file, as the next launch reads it.
            self.assertEqual(loaded["id"], state["id"])
            self.assertEqual(loaded["options"]["effort"], "low")
            context = SimpleNamespace(state=loaded, settings={}, models=[model("gpt-6.1-sol", "low", "medium", "high")])
            self.assertEqual(codex_turn_options(context)["effort"], "low")
            self.assertEqual(request_effort(context, loaded["options"], "claude"), "low")

    def test_a_choice_set_in_the_chat_is_saved_and_reset_returns_to_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            try:
                controller.models = [model("gpt-6.1-sol", "low", "medium", "high")]
                controller.set_options(model="gpt-6.1-sol", effort="low")
                self.assertEqual(codex_turn_options(controller)["effort"], "low")
                self.assertEqual(controller.store.load()["options"]["effort"], "low")
                controller.set_options(effort="")   # Reset: the model's default again, whatever it is by then.
                self.assertEqual(controller.store.load()["options"]["effort"], "")
                self.assertEqual(codex_turn_options(controller)["effort"], "high")
            finally:
                controller.close()

    def test_a_choice_no_runtime_knows_reads_as_no_choice(self):
        for value in (None, "", "invented", 3, ["high"]):
            with self.subTest(value=value):
                self.assertEqual(stored_options({"effort": value})["effort"], "")


class AModelChange(unittest.TestCase):
    WIDE, NARROW, HIGH_ONLY = model("wide", "low", "medium", "high"), model("narrow", "low", "medium"), model("only", "high")

    def test_a_choice_the_new_model_offers_is_kept(self):
        self.assertEqual(carried_effort({"effort": "low"}, self.NARROW), "low")
        self.assertEqual(carried_effort({"effort": "medium"}, self.WIDE), "medium")
        self.assertEqual(carried_effort({"effort": "max"}, model("silent")), "max", "a model that advertises no level")
        self.assertEqual(carried_effort({"effort": "max"}, None), "max")

    def test_a_choice_the_new_model_lacks_becomes_no_choice_so_the_default_applies(self):
        self.assertEqual(carried_effort({"effort": "high"}, self.NARROW), "")
        self.assertEqual(carried_effort({"effort": "low"}, self.HIGH_ONLY), "")
        self.assertEqual(carried_effort({}, self.WIDE), "", "no choice stays no choice")
        self.assertEqual(chat_effort({"effort": carried_effort({"effort": "high"}, self.NARROW)}, self.NARROW), "medium")

    def test_the_chat_s_own_model_change_follows_it(self):
        models = [dict(self.WIDE, default=True), dict(self.NARROW, default=False), dict(self.HIGH_ONLY, default=False)]
        with tempfile.TemporaryDirectory() as directory:
            controller, connection = Controller(directory), str(uuid.uuid4())
            try:
                controller.set_document(("scratch", 1))

                def choose(identity):
                    controller.choose_connection("codex_subscription", {"model": ""}, "", connection, models, identity)
                    return controller.state["options"]["effort"], codex_turn_options(controller)["effort"]
                self.assertEqual(choose("wide"), ("", "high"))
                self.assertEqual(choose("narrow"), ("", "medium"))
                controller.set_options(effort="low")
                self.assertEqual(choose("wide"), ("low", "low"), "the person's choice, which this model offers")
                self.assertEqual(choose("only"), ("", "high"), "a choice this model lacks is dropped, not rewritten")
                self.assertEqual(choose("wide"), ("", "high"), "and does not come back")
            finally:
                controller.close()


class AutoRoutingReadsEachModelAtItsOwnEffort(unittest.TestCase):
    CATALOG = normalize_models([{"value": "default", "isDefault": True},
                                {"value": "claude-opus-5-5", "supportedEffortLevels": ["low", "medium", "high"]},
                                {"value": "claude-haiku-5-5", "supportedEffortLevels": ["low", "medium"]}], "claude")

    def efforts(self, **options):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            try:
                controller.set_document(("scratch", 1))
                controller.choose_connection("claude_subscription", {"model": ""}, "", str(uuid.uuid4()), self.CATALOG, "")
                controller.set_options(**options)
                profile = controller.capability_profile()
                return {entry["id"]: entry["effort"] for entry in profile["models"]}
            finally:
                controller.close()

    def test_no_choice_is_each_model_s_default_and_a_choice_holds_where_the_model_offers_it(self):
        self.assertEqual(self.efforts(), {"claude-opus-5-5": "high", "claude-haiku-5-5": "medium"})
        self.assertEqual(self.efforts(effort="low"), {"claude-opus-5-5": "low", "claude-haiku-5-5": "low"})
        self.assertEqual(self.efforts(effort="high"), {"claude-opus-5-5": "high", "claude-haiku-5-5": "medium"})


class EffortStaysOutOfLaunchKeys(unittest.TestCase):
    def test_a_prewarmed_launch_is_the_same_for_every_effort_and_for_none(self):
        launches = {tuple(sorted(runtime_prewarm.launch_options(stored_options({"model": "m", "effort": effort})).items()))
                    for effort in ("", *EFFORTS)}
        self.assertEqual(len(launches), 1)
        self.assertNotIn("effort", dict(next(iter(launches))))


class NoNewInterfaceText(unittest.TestCase):
    def test_every_level_a_default_can_be_already_has_its_translated_name(self):
        self.assertEqual(set(EFFORT_NAMES), set(EFFORTS))
        self.assertEqual([EFFORT_NAMES[level] for level in ("low", "medium", "high")], ["Low", "Medium", "High"])


def _chat_options(node):
    """Whether ``node`` is a chat's options: a name ending in ``options``, or what ``clean_options``/``stored_options`` return."""
    if isinstance(node, ast.Call):
        node = node.func
        return isinstance(node, (ast.Name, ast.Attribute)) and (node.id if isinstance(node, ast.Name) else node.attr) in (
            "clean_options", "stored_options")
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
    return name.endswith("options")


def hard_coded_levels(tree):
    """``(line, level)`` of every effort level written into ``tree`` as a value: in a dict under "effort", as an
    ``effort`` argument or parameter default, or as the fallback of an expression about an effort."""
    def level(node):
        return isinstance(node, ast.Constant) and type(node.value) is str and node.value in EFFORTS

    def about_effort(node):
        return "effort" in ast.unparse(node).lower()
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            found += [(node.lineno, value.value) for key, value in zip(node.keys, node.values)
                      if isinstance(key, ast.Constant) and key.value == "effort" and level(value)]
        elif isinstance(node, ast.Call):
            found += [(node.lineno, keyword.value.value) for keyword in node.keywords
                      if keyword.arg and "effort" in keyword.arg and level(keyword.value)]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = node.args
            positional = arguments.posonlyargs + arguments.args
            pairs = list(zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults))
            pairs += list(zip(arguments.kwonlyargs, arguments.kw_defaults))
            found += [(node.lineno, default.value) for argument, default in pairs
                      if default is not None and "effort" in argument.arg and level(default)]
        elif isinstance(node, ast.BoolOp) and level(node.values[-1]) and about_effort(node):
            found.append((node.lineno, node.values[-1].value))
        elif isinstance(node, ast.IfExp) and about_effort(node):
            found += [(node.lineno, branch.value) for branch in (node.body, node.orelse) if level(branch)]
    return sorted(found)


class NoModuleHardCodesAnEffortLevel(unittest.TestCase):
    """A level written into a module is a second default waiting to drift: ``default_effort`` is the only one.

    ``RULED`` names the exceptions a maintainer ruled on: the module, the level and the reason. Each must still be
    there (a stale exception fails) and must say "ruling" in the lines above it, where the next reader meets it.
    """

    RULED = {"studio_enhance.py": ("low", "Studio's helper requests (the prompt editor and the parts checks) stay at "
                                          "low: maintainer's ruling of 2026-11-12, \"Keep it at low\".")}

    def modules(self):
        return [path for path in sorted(ADDON.glob("*.py")) if path.name != "chat_settings.py"]

    def test_only_the_ruled_exceptions_write_a_level(self):
        seen = {}
        for path in self.modules():
            found = hard_coded_levels(ast.parse(path.read_text(encoding="utf-8")))
            if found:
                seen[path.name] = found
        self.assertEqual({name: [level for _line, level in found] for name, found in seen.items()},
                         {name: [level] for name, (level, _reason) in self.RULED.items()},
                         "ask chat_settings (default_effort, chat_effort), or name a maintainer's ruling in RULED: %s" % seen)

    def test_each_ruled_exception_names_its_ruling_where_it_is_written(self):
        for name, (level, reason) in self.RULED.items():
            with self.subTest(module=name):
                self.assertIn("ruling", reason)
                lines = (ADDON / name).read_text(encoding="utf-8").splitlines()
                (line, found), = hard_coded_levels(ast.parse("\n".join(lines)))
                self.assertEqual(found, level)
                self.assertIn("ruling", " ".join(lines[max(0, line - 6):line]).lower())

    def test_the_scan_sees_each_way_of_writing_a_level(self):
        for source in ('options = {"model": m, "effort": "low"}', 'start(effort="medium")', 'profile(c, turn_effort="high")',
                       'def profile(catalog, *, effort="low"): pass', 'def profile(catalog, effort="max"): pass',
                       'effort = entry.get("effort") or "low"', 'effort = chosen if chosen in efforts else "low"',
                       'pick = lambda effort="minimal": effort'):
            with self.subTest(source=source):
                self.assertEqual(len(hard_coded_levels(ast.parse(source))), 1)
        for source in ('label = {"low": "Low"}', 'tier = entry.get("tier") or "low"', 'quality = "high"',
                       'values = {"effort": request_effort(context, options, "claude")}', 'effort = options.get("effort") or ""',
                       'def profile(catalog, *, effort): pass'):
            with self.subTest(source=source):
                self.assertEqual(hard_coded_levels(ast.parse(source)), [])


class NoCallSiteResolvesAnEffortItself(unittest.TestCase):
    """Every module but ``chat_settings`` asks it: a second rule somewhere else is how the defaults drifted apart."""

    # Modules whose ``turn_options`` are ``codex_turn_options``' result (a turn's parameters, the effort already
    # resolved), not a chat's options.
    TURN_PARAMETERS = frozenset({"codex_bases.py"})

    def modules(self):
        return [path for path in sorted(ADDON.glob("*.py")) if path.name != "chat_settings.py"]

    def test_the_add_on_has_modules_to_read(self):
        self.assertGreater(len(self.modules()), 100)

    def test_no_module_reads_a_model_s_own_default_or_its_first_listed_level(self):
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            for banned in ('default_effort"', "defaultReasoningEffort", '["efforts"][0]', "efforts[0]", "choices[0]"):
                with self.subTest(module=path.name, banned=banned):
                    if banned == "choices[0]" and path.name != "chat_popovers.py":
                        continue
                    self.assertNotIn(banned, text)

    def test_a_chat_s_effort_is_read_only_through_the_resolvers(self):
        resolvers = {"effective_effort", "chat_effort", "carried_effort", "request_effort"}
        for path in self.modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            inside = set()
            for call in ast.walk(tree):
                func = getattr(call, "func", None)
                if isinstance(call, ast.Call) and (getattr(func, "id", None) or getattr(func, "attr", None)) in resolvers:
                    inside.update(id(node) for node in ast.walk(call))
            for node in ast.walk(tree):
                raw = (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "effort"
                       and isinstance(node.ctx, ast.Load) and _chat_options(node.value))
                got = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                       and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "effort"
                       and _chat_options(node.func.value))
                if (raw or got) and id(node) not in inside and path.name not in self.TURN_PARAMETERS:
                    with self.subTest(module=path.name, line=node.lineno):
                        self.fail("%s:%d reads a chat's effort without chat_settings' resolvers" % (path.name, node.lineno))

    def test_the_scan_sees_a_raw_read(self):
        for source in ('effort = options["effort"]', 'x = controller.turn_options["effort"]',
                       'y = clean_options(state.get("options"))["effort"]', 'z = turn_options.get("effort")'):
            with self.subTest(source=source):
                node = ast.parse(source).body[0].value
                self.assertTrue(_chat_options(node.value if isinstance(node, ast.Subscript) else node.func.value))


if __name__ == "__main__":
    unittest.main()
