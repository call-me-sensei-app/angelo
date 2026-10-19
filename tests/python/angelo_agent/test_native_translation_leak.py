# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No translated byte reaches a model: the pseudo-locale proof.

The pseudo-locale (``scripts/angelo/i18n/pseudo.py``) translates every message
into marked, accented, longer text (``⟦Ȧççèṗţ~~⟧``), so any translated byte is
recognisable. With it active for every lookup kind (interface, tooltip, report):

* the model-facing payloads (tool declarations, plan schemas, guidance, every
  constant of the model-facing modules) are byte-identical to an English run,
  compared by digest across two fresh interpreters, one of which installs the
  pseudo-locale before importing anything of the add-on;
* chat notices, stored errors and template errors keep English text, while what
  a person sees from the same rows is translated (so the pseudo-locale is
  really active), and the handoff and context-refresh seed a model reads
  contain no marked byte.
"""

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from i18n import pseudo, sources  # noqa: E402
from angelo_agent import context_rebase, display_text, session_validation, studio_state, ui_text  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402

DIGEST = textwrap.dedent('''
    import hashlib, importlib, json, sys
    sys.path[:0] = [{addon!r}, {tools!r}]
    if {pseudo!r}:
        from i18n import pseudo
        from angelo_agent import ui_text
        for kind in ("iface", "tip", "rpt"):
            ui_text._pgettext[kind] = lambda message, context=None: pseudo.pseudo(message)
    from i18n import sources
    payload = {{}}
    names = sorted(sources.MODEL_FACING) + ["codex_plan", "providers", "routing", "claude_outcomes"]
    for name in names:
        try:
            module = importlib.import_module("angelo_agent." + name)
        except ModuleNotFoundError as error:
            if error.name in ("bpy", "angelo_agent." + name):
                continue  # Needs Blender (blender_tools) or does not exist in this checkout.
            raise
        for key, value in sorted(vars(module).items()):
            if not key.startswith("_") and isinstance(value, (str, int, float, bool, list, tuple, dict, set, frozenset)):
                payload[name + "." + key] = value
    from angelo_agent import codex_plan, plan_schema, tool_specs
    payload["codex_plan.plan_tools()"] = codex_plan.plan_tools()
    payload["codex_plan.output_schema()"] = codex_plan.output_schema()
    payload["plan_schema.plan_schema(TOOLS)"] = plan_schema.plan_schema(tool_specs.TOOLS)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=lambda v: sorted(map(repr, v))
                         if isinstance(v, (set, frozenset)) else repr(v))
    print(json.dumps({{"digest": hashlib.sha256(encoded.encode()).hexdigest(), "entries": len(payload),
                      "marked": "\\u27e6" in encoded}}))
''')


def model_payload(addon_root, use_pseudo):
    script = DIGEST.format(addon=str(addon_root), tools=str(ROOT / "scripts/angelo"), pseudo=use_pseudo)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def pseudo_locale():
    """Make every lookup kind answer in the pseudo-locale; returns a function that restores the previous state."""
    saved = dict(ui_text._pgettext)
    for kind in ("iface", "tip", "rpt"):
        ui_text._pgettext[kind] = lambda message, context=None: pseudo.pseudo(message)
    return lambda: (ui_text._pgettext.clear(), ui_text._pgettext.update(saved))


class ModelPayloadTests(unittest.TestCase):
    def test_model_payloads_are_identical_with_the_pseudo_locale_active(self):
        english = model_payload(ROOT / "scripts/addons_core", False)
        translated = model_payload(ROOT / "scripts/addons_core", True)
        self.assertGreater(english["entries"], 300, "the digest covers the model-facing modules")
        self.assertFalse(translated["marked"], "no pseudo-locale byte in any model-facing payload")
        self.assertEqual(english["digest"], translated["digest"])


class RelocatedTextTests(unittest.TestCase):
    """Model-read text that moved to become a display template reads byte for byte as before."""

    def test_the_context_refresh_notice_reads_as_before(self):
        import ast
        before = ("Started a fresh model context for this chat: the previous one reached about {tokens} tokens. "
                  "The host carried over the task memory, your requests, recent results and open previews.")
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/controller.py").read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == "_rebase_context_if_due")
        templates = [call.args[0].value for call in ast.walk(method) if isinstance(call, ast.Call)
                     and getattr(call.func, "attr", "") == "notice" and isinstance(call.args[0], ast.Constant)]
        self.assertEqual([before], templates)
        self.assertEqual(before.format(tokens=91234), ui_text.notice(templates[0], tokens=91234)["text"])


class StoredTextTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(pseudo_locale())

    def test_notices_store_english_and_display_translated(self):
        row = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
        self.assertEqual("Moved to Library.", row["text"])
        self.assertIn(pseudo.OPEN, ui_text.display_notice(row), "the pseudo-locale is active for display")

    def test_what_a_model_reads_has_no_translated_byte(self):
        rows = [{"role": "user", "text": "Make a cube"},
                {"role": "system", **ui_text.notice("Auto asked {model} instead ({reason}); nothing had been applied to "
                                                    "the scene.", model="sonnet", reason="low confidence")},
                {"role": "error", **display_text.error_notice(RuntimeError(display_text.Text(
                    ui_text.n_("An agent turn is already running"))))},
                {"role": "assistant", "text": "Done."}]
        saved, verified = session_validation.messages(json.loads(json.dumps(rows)))
        self.assertTrue(verified)
        text, _ = handoff(saved)
        seed = context_rebase.seed(saved)
        for payload in (text, seed, json.dumps([row["text"] for row in saved])):
            self.assertNotIn(pseudo.OPEN, payload)
        self.assertIn("An agent turn is already running", text)
        self.assertTrue(all(pseudo.OPEN in ui_text.display_notice(row) for row in saved if row["role"] in ("system", "error")))

    def test_errors_keep_english_text_for_logs_tool_results_and_storage(self):
        error = studio_state.StudioValueError(ui_text.n_("{variable} must be an absolute path"), variable="X")
        self.assertEqual("X must be an absolute path", str(error))
        self.assertIn(pseudo.OPEN, studio_state.error_text(error))
        text = display_text.Text(ui_text.n_("Contact your admin: {contact}"), contact="IT")
        self.assertEqual("Contact your admin: IT", str(text))
        self.assertIn(pseudo.OPEN, display_text.shown(text))

    def test_every_template_message_is_pseudo_translatable_without_breaking_its_fields(self):
        from i18n import po
        _, template = po.read(str(sources.POT))
        for entry in template:
            fields = [name for _, name, _, _ in __import__("string").Formatter().parse(entry.id) if name]
            marked = ui_text.iface_(entry.id, **{name.split(".")[0].split("[")[0]: 1 for name in fields})
            self.assertIn(pseudo.OPEN, marked, entry.id)


if __name__ == "__main__":
    unittest.main()
