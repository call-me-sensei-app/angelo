# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Route parity's views: how each route presents a tool result to its model, run through the add-on's real
presentation code (``route_parity`` compares them; doc/angelo/prompt_audit.rst, "Route parity for data")."""

import copy
import json
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = Path(__file__).resolve().with_name("route_parity.json")
CORPUS = ROOT / "tests/python/angelo_agent/route_parity_corpus"
PROBE = "zz_route_probe"
MAX_DEPTH = 4            # Dict levels analysed below a result's root; deeper data is checked through its ancestor.
IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
RESULT_ROUTES = ("codex", "claude.direct", "mcp.window", "mcp.headless", "claude.plan.verify", "plan.handoff",
                 "plan.handoff.certified", "codex.plan.correction")


def _addon():
    path = str(ROOT / "scripts/addons_core")
    if path not in sys.path:
        sys.path.insert(0, path)


class _Context:
    """The parts of a controller and request context the presentation code reads, with a fresh identifier table."""

    def __init__(self):
        from angelo_agent.identifiers import IdentifierTable
        self.table = IdentifierTable()
        self.host = SimpleNamespace(_identifiers=self.table)
        self.short_identifiers, self.program_completions, self.references = True, None, []
        self.turn_options, self.settings = {}, {}
        self.state = {"options": {}, "provider": "codex"}
        self.models = [{"id": "probe", "default": True, "images": True}]

    def compact_identifiers(self, value):
        return self.table.compact(value)

    def observe_result(self, result):
        return None


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(",", ":"), default=str)


def _operation(tool):
    return {"tool": tool, "arguments": {}}


def _codex(tool, result):
    from angelo_agent import providers
    items = providers.tool_content(_Context(), tool, result)
    return items[0]["text"], "model_result"


def _packed(tool, result):
    from angelo_agent.claude_direct import DirectRequest
    context = _Context()
    request = DirectRequest(SimpleNamespace(_current=lambda owner: True), context, None, None)
    run = SimpleNamespace(failure=None, failed_lookups=None, refused=None, results=[result], completions=None)
    return request._packed([_operation(tool)], run, {}, [])


def _direct(tool, result):
    return _dump(_packed(tool, result)["content"]), "model_result"


def _explained(tool, result):
    from angelo_agent.external_work import ExternalWork
    stub = SimpleNamespace(controller=SimpleNamespace(document_invalid=False, cancelled=False),
                           owner=SimpleNamespace(owns_request=lambda: True))
    return ExternalWork._explained(stub, _packed(tool, result))


def _mcp_window(tool, result):
    return _dump(_explained(tool, result)["content"]), "model_result"


def _mcp_headless(tool, result):
    """The headless child answers through the same ``ExternalWork``; it adds a note (saved, reloaded) before the
    result, never in place of it (``headless_host._with_note``)."""
    try:
        from angelo_agent import headless_host
    except ImportError:   # A tree without the headless host: the route does not exist there.
        return None
    return _dump(headless_host._with_note(_explained(tool, result), "note")["content"]), "model_result"


def _plan_verify(tool, result):
    from angelo_agent import model_results, query_observations
    from angelo_agent.claude_outcomes import present_plan_checks
    context, operations = _Context(), [_operation(tool)]
    applied = present_plan_checks(operations, {"success": True, "operations": [result]}, completions=None)
    applied = model_results.plan_results(operations, applied, observation_follows=True)
    applied = query_observations.compacted(context, applied, operations, [result])
    return _dump(applied["operations"][0] if isinstance(applied, dict) else applied), "model_result+observation"


def handoff_form(tool, result, certified):
    """Which entry a plan's result becomes in the next message's handoff (``routing.handoff``). ``certified``: a host
    summary row follows it (``certified_completion`` proved the whole plan, which needs the plan's arguments or the
    completion proof, so it is the recording's fact, not recomputed here). Forms: ``terse`` (a verified line and
    what a next plan binds with), ``compact`` (a certified result with no terse form), ``unchecked`` (a clean commit
    no check covered) or ``excerpt`` (``history_result_text``: the result's first 5,000 characters)."""
    from angelo_agent.claude_outcomes import compact_receipt, terse_receipt
    from angelo_agent.program_completion import unchecked_receipt
    if certified and terse_receipt(tool, result) is not None:
        return "terse"
    if certified and compact_receipt(tool, result) is not None:
        return "compact"
    if not certified and unchecked_receipt(tool, result) is not None:
        return "unchecked"
    return "excerpt"


def _handoff(tool, result, certified=False):
    from angelo_agent import routing
    messages = [{"role": "user", "text": "request"},
                {"role": "activity", "tool": tool, "status": "completed", "result": result, "call_id": "call-1"}]
    if certified:
        messages.append({"role": "system", "text": "summary", "host_summary": True})
    text, _ = routing.handoff(messages, 0, 10 ** 7, identifiers=_Context().table)
    return text, handoff_form(tool, result, certified)


def _handoff_certified(tool, result):
    """The same after the host's summary certified the plan (the Claude and Codex plan routes' usual edit). Only
    a typed program, recipe or Accept is certified; a script never is, and a lookup's entry is the excerpt anyway."""
    from angelo_agent.program_model_result import TOOLS
    return _handoff(tool, result, certified=True) if tool in TOOLS else None


def _correction(tool, result):
    from angelo_agent.claude_outcomes import compact_receipt
    from angelo_agent.program_completion import continuation_receipt, incomplete_verification
    receipt = continuation_receipt(_operation(tool), result)
    form = ("whole" if incomplete_verification(result) else "job" if tool == "angelo_job_start"
            else "compact" if compact_receipt(tool, result) is not None else "status")
    return _dump(_Context().compact_identifiers([receipt])), form


VIEWS = {"codex": _codex, "claude.direct": _direct, "mcp.window": _mcp_window, "mcp.headless": _mcp_headless,
         "claude.plan.verify": _plan_verify, "plan.handoff": _handoff, "plan.handoff.certified": _handoff_certified,
         "codex.plan.correction": _correction}


def views(tool, result):
    """``{route: (text, form)}``: what each route's model reads of one raw result (routes absent from the tree are
    left out). Budgets are the code's own; a view's text is cut only where the route cuts it."""
    _addon()
    shown = {}
    with ExitStack() as stack:
        stack.enter_context(mock.patch("angelo_agent.request_path.codex_result_images", lambda context, result: []))
        for route in RESULT_ROUTES:
            view = VIEWS[route](tool, copy.deepcopy(result))
            if view is not None:
                shown[route] = view
    return shown


def truncated(route, form, text):
    """Whether ``text`` is a route's excerpt cut at its budget (``history_result_text`` keeps 5,000 characters)."""
    if form != "excerpt":
        return False
    for line in text.splitlines():
        if line.startswith("{"):
            entry = json.loads(line)
            excerpt = entry.get("result")
            if isinstance(excerpt, str) and len(excerpt) >= 5000:
                try:
                    json.loads(excerpt)
                except ValueError:
                    return True
    return False
