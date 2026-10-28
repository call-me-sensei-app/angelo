# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's Python under the lead's rules: consent, the card's own chat, and the words a worker reads.

The maintainer's decisions (2026-10-25, parallel edit in 1.0.6), pinned with real ``Controller`` objects and the
window's actual card operators (``ui_class``), no Blender, no provider:

* Ask: every worker script asks. Auto: a script asks unless trusted. A worker chat inherits the lead's "Trust scripts
  for this chat" when it is created and whenever the lead trusts later (``worker_trust``); Always allow scripts works
  as for any Auto chat; a script naming preferences or ``angelo_agent`` always asks.
* Approving from any surface approves the worker chat's own call: the card's operators act on the chat whose card it
  is, never just the chat shown now.
* The worker's words state facts only: its fixed briefing is its base instructions (one cached prefix for every
  worker) and says it has Python; its first message carries only its part. A failed call in a worker chat never
  sends its model to the request snapshot Restore refuses there.

The in-Blender proof of the script route is ``test_blender_worker_python.py`` (lane P3).
"""

import ast
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import script_errors, worker_spec  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.managed_policy import combine  # noqa: E402
from test_native_dispatch_fixes import ui_class  # noqa: E402

SCRIPT = {"description": "Set a value", "code": "result = 1"}
CHECKPOINT = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"


class Part:
    """A worker Blender as its chat's executor sees it (``RemoteExecutor``): the part's document, what ran there."""

    document_key = ("worker-part", 7)

    def __init__(self, result=None, error=None):
        self.ran, self.result, self.error = [], result, error

    def precondition(self, *_arguments):
        return "current"

    def execute(self, name, arguments):
        self.ran.append(arguments["code"])
        if self.error is not None:
            raise self.error
        return dict(self.result or {"success": True, "result": 1})


class ChatFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = SimpleNamespace(precondition=Mock(return_value="current"),
                                      execute=Mock(return_value={"success": True, "result": 1}))

    def chat(self, mode, part=None):
        folder = Path(self.directory.name) / uuid.uuid4().hex
        folder.mkdir()
        chat = Controller(str(folder))
        self.addCleanup(chat.close)
        chat.busy = True
        chat.state["options"]["approval"] = mode
        if part is not None:
            chat.remote_executor = part   # Bound to a worker (``worker_routing.bind``).
        chat.set_document(("angelo-file", 1) if part is None else part.document_key)
        chat.request_checkpoint = CHECKPOINT
        return chat

    def worker_of(self, lead, mode=None, part=None):
        from angelo_agent import worker_trust
        worker = self.chat(mode or lead.state["options"]["approval"], part or Part())
        worker_trust.link(lead, worker)   # What ``worker_sessions._new_chat`` does (pinned below).
        return worker

    def script(self, chat, code=None, identity=None):
        results = []
        identity = identity or uuid.uuid4().hex[:8]
        chat.request_tool(identity, "angelo_execute_python", dict(SCRIPT, code=code or SCRIPT["code"]), results.append)
        call = chat.pending[chat.state["id"] + "/" + identity]
        self.pump(chat)
        return call, results

    def pump(self, chat):
        chat.pump_tools(self.window, chat.document,
                        apply_mutation=lambda call: chat.execute_tool(call, self.window, chat.document),
                        budget=ToolDispatchBudget(seconds=60))

    def assertAsks(self, chat, call, results):
        self.assertTrue(call.approval and not call.executing)
        self.assertEqual(results, [])
        self.assertFalse(chat._automatic_script(call))

    def assertRanUnasked(self, results, consent="chat_session"):
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].get("success"), results[0])
        self.assertEqual(results[0]["consent"], consent)


class InheritedTrustTests(ChatFixture):
    def test_a_worker_started_while_the_lead_trusts_runs_its_scripts_unasked_in_auto(self):
        lead = self.chat("AUTO_BOUNDED")
        lead.set_trusted_scripts(True)
        worker = self.worker_of(lead)
        self.assertTrue(worker.trusted_scripts)
        call, results = self.script(worker)
        self.assertIs(call.script_consent, lead._script_consent)   # The lead's own token, read live.
        self.assertRanUnasked(results)
        self.assertEqual(worker.remote_executor.ran, [SCRIPT["code"]])   # It ran in the worker's part.

    def test_a_worker_inherits_trust_the_lead_turns_on_later_and_a_queued_script_still_asks(self):
        lead = self.chat("AUTO_BOUNDED")
        worker = self.worker_of(lead)
        earlier, earlier_results = self.script(worker)
        self.assertAsks(worker, earlier, earlier_results)
        lead.set_trusted_scripts(True)
        self.assertTrue(worker.trusted_scripts)
        call, results = self.script(worker)
        self.assertRanUnasked(results)
        self.pump(worker)
        self.assertAsks(worker, earlier, earlier_results)   # Admitted before the trust: exact-code review, as the lead.

    def test_ask_asks_for_every_worker_script_whatever_is_trusted(self):
        lead = self.chat("ASK")
        lead.set_trusted_scripts(True)
        own, own_results = self.script(lead)
        self.assertRanUnasked(own_results)   # The lead's own rule is unchanged: its trust covers its Ask scripts.
        worker = self.worker_of(lead)
        self.assertFalse(worker.trusted_scripts)
        self.assertAsks(worker, *self.script(worker))
        with self.assertRaises(RuntimeError):
            worker.set_trusted_scripts(True)   # Nor can a worker chat in Ask be trusted on its own.
        worker.state["options"]["approval"] = "AUTO_BOUNDED"   # The person switches the worker's chat to Auto.
        self.assertTrue(worker.trusted_scripts)

    def test_the_leads_trust_ending_ends_the_workers_and_its_queued_script_asks(self):
        for end in ("disable", "read_only", "new_conversation", "document"):
            with self.subTest(end=end):
                lead = self.chat("AUTO_BOUNDED")
                lead.set_trusted_scripts(True)
                worker = self.worker_of(lead)
                self.assertTrue(worker.trusted_scripts)
                call = SimpleNamespace(name="angelo_execute_python", arguments=SCRIPT, script_consent=lead._script_consent)
                self.assertEqual(worker.script_consent_kind(call), "chat_session")
                if end == "disable":
                    lead.set_trusted_scripts(False)
                elif end == "read_only":
                    lead.set_options(approval="READ_ONLY")
                elif end == "new_conversation":
                    lead.state = dict(lead.state, id=str(uuid.uuid4()))   # New Chat keeps the object, not the id.
                else:
                    lead.set_document(("another-file", 2))
                self.assertFalse(worker.trusted_scripts)
                self.assertIsNone(worker.script_consent_kind(call))

    def test_the_person_turns_a_workers_inherited_trust_off_until_the_lead_trusts_again(self):
        lead = self.chat("AUTO_BOUNDED")
        lead.set_trusted_scripts(True)
        worker, other = self.worker_of(lead), self.worker_of(lead)
        version = worker.version
        worker.set_trusted_scripts(False)   # The trust menu in the worker's own chat.
        self.assertFalse(worker.trusted_scripts)
        self.assertTrue(other.trusted_scripts)   # Only that worker.
        self.assertAsks(worker, *self.script(worker))
        lead.set_trusted_scripts(False)
        lead.set_trusted_scripts(True)
        self.assertGreater(worker.version, version)   # Its menu and composer redraw.
        self.assertTrue(worker.trusted_scripts)
        self.assertRanUnasked(self.script(worker)[1])

    def test_a_worker_trusted_on_its_own_keeps_it_when_the_lead_stops(self):
        lead = self.chat("AUTO_BOUNDED")
        worker = self.worker_of(lead)
        worker.set_trusted_scripts(True)
        lead.set_trusted_scripts(True)
        lead.set_trusted_scripts(False)
        self.assertTrue(worker.trusted_scripts)
        self.assertIs(self.script(worker)[0].script_consent, worker._script_consent)

    def test_only_the_lead_that_started_a_worker_lends_it_trust(self):
        lead, stranger = self.chat("AUTO_BOUNDED"), self.chat("AUTO_BOUNDED")
        stranger.set_trusted_scripts(True)
        worker = self.worker_of(lead)
        self.assertFalse(worker.trusted_scripts)
        lead.set_trusted_scripts(True)
        unbound = self.chat("AUTO_BOUNDED")   # A chat the lead's link never made a worker: no part, no inheritance.
        from angelo_agent import worker_trust
        worker_trust.link(lead, unbound)
        self.assertFalse(unbound.trusted_scripts)

    def test_always_allow_covers_an_auto_worker_and_never_an_ask_one(self):
        token = object()
        with patch("angelo_agent.always_allow.token", return_value=token):
            lead = self.chat("AUTO_BOUNDED")
            auto, ask = self.worker_of(lead), self.worker_of(lead, mode="ASK")
            self.assertRanUnasked(self.script(auto)[1], consent="always_allow")
            self.assertAsks(ask, *self.script(ask))

    def test_a_script_naming_preferences_or_the_addon_always_asks_in_a_worker(self):
        lead = self.chat("AUTO_BOUNDED")
        lead.set_trusted_scripts(True)
        worker = self.worker_of(lead)
        for code in ("import angelo_agent\nresult = 1", "bpy.context.preferences.view.show_tooltips = False"):
            with self.subTest(code=code):
                self.assertAsks(worker, *self.script(worker, code=code))

    def test_a_policy_without_auto_approval_ends_inherited_trust(self):
        lead = self.chat("AUTO_BOUNDED")
        lead.set_trusted_scripts(True)
        worker = self.worker_of(lead)
        call = SimpleNamespace(name="angelo_execute_python", arguments=SCRIPT, script_consent=lead._script_consent)
        with patch("angelo_agent.managed_policy.current",
                   return_value=combine([("test", {"AllowAutoApproval": False})])):
            self.assertFalse(worker.trusted_scripts)
            self.assertIsNone(worker.script_consent_kind(call))

    def test_a_new_worker_chat_follows_its_lead(self):
        """``worker_sessions._new_chat`` itself links the chat it makes to its lead (no Blender: a stand-in ``ui``)."""
        import angelo_agent
        from angelo_agent import worker_sessions
        lead = self.chat("AUTO_BOUNDED")
        lead.set_trusted_scripts(True)

        class Sessions:
            active = "shown"

            def create(self, provider):
                return str(uuid.uuid4())

            def activate(self, identity):
                self.active = identity
        ui = SimpleNamespace(_sessions=Sessions(), state_directory=lambda: self.directory.name, _controllers={},
                             resolve_collaborator=None, connection_ready=lambda record: False)
        with patch.dict(sys.modules, {"angelo_agent.ui": ui}), patch.object(angelo_agent, "ui", ui, create=True), \
                patch("angelo_agent.connection_access.find_connection", return_value=None):
            worker = worker_sessions._new_chat(lead)
        self.addCleanup(worker.close)
        self.assertEqual(ui._sessions.active, "shown")
        worker.remote_executor = Part()   # ``worker_routing.adopt`` binds it next.
        worker.set_document(Part.document_key)
        self.assertTrue(worker.trusted_scripts)


class CardBindingTests(ChatFixture):
    """The person approves a worker's script on its card while another chat is shown (the lead's, say)."""

    def operators(self, shown, chats):
        controllers = {chat.state["id"]: chat for chat in chats}
        dispatch = SimpleNamespace(approve=Mock())
        names = {"_controllers": controllers, "get_controller": lambda: shown, "worker_dispatch": dispatch,
                 "live_windows": SimpleNamespace(window_areas=lambda manager: []), "console": None, "chat_view": None}

        def review_tool(**properties):
            operator = ui_class("ANGELO_OT_review_tool", names)()
            operator.call_id, operator.session_id = properties["call_id"], properties.get("session_id", "")
            return operator.execute(SimpleNamespace(window_manager=None))
        names["bpy"] = SimpleNamespace(ops=SimpleNamespace(angelo=SimpleNamespace(review_tool=review_tool)))

        def run(name, call_id, session_id):
            operator = ui_class(name, names)()
            operator.call_id, operator.session_id, operator.report = call_id, session_id, Mock()
            return operator.execute(SimpleNamespace(window_manager=None, preferences=SimpleNamespace(
                edit=SimpleNamespace(use_global_undo=True))))
        return run, dispatch

    def test_approving_a_workers_script_from_another_shown_chat_reviews_and_approves_that_workers_call(self):
        lead, worker = self.chat("ASK"), self.chat("ASK", Part())
        call, results = self.script(worker)
        run, dispatch = self.operators(shown=lead, chats=(lead, worker))
        self.assertEqual(run("ANGELO_OT_approve", call.id, worker.state["id"]), {"CANCELLED"})   # Reveals the code.
        self.assertEqual(call.reviewed_script, call.invocation_digest)   # The worker's own call was reviewed.
        self.assertEqual(lead.reviewed_calls, set())
        self.assertEqual(run("ANGELO_OT_approve", call.id, worker.state["id"]), {"FINISHED"})
        dispatch.approve.assert_called_once_with(worker, call)   # On the worker chat's lane, its own call.

    def test_review_and_decline_act_on_the_cards_own_chat(self):
        lead, worker = self.chat("ASK"), self.chat("ASK", Part())
        call, results = self.script(worker)
        mine, mine_results = self.script(lead)
        run, _dispatch = self.operators(shown=lead, chats=(lead, worker))
        self.assertEqual(run("ANGELO_OT_review_tool", call.id, worker.state["id"]), {"FINISHED"})
        self.assertIn(call.id, worker.reviewed_calls)
        run("ANGELO_OT_decline", call.id, worker.state["id"])
        self.assertEqual(results[0]["error_code"], "DECLINED")
        self.assertIn(mine.id, lead.pending)   # The shown chat's own card is untouched.
        self.assertEqual(mine_results, [])
        self.assertEqual(run("ANGELO_OT_decline", mine.id, "gone-chat"), {"FINISHED"})   # A chat that left: nothing.
        self.assertIn(mine.id, lead.pending)

    def test_the_chat_view_sends_its_cards_own_chat(self):
        tree = ast.parse((ADDON / "chat_view.py").read_text(encoding="utf-8"))
        dispatch = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and node.name == "dispatch_action")
        calls = {node.func.attr: {k.arg: ast.unparse(k.value) for k in node.keywords} for node in ast.walk(dispatch)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {"approve", "decline", "review_tool"}
                 and ast.unparse(node.func.value) == "bpy.ops.angelo"}
        self.assertEqual(set(calls), {"approve", "decline", "review_tool"})
        for name, keywords in calls.items():
            with self.subTest(operator=name):
                self.assertEqual(keywords.get("session_id"), "controller.state['id']")


# Words that open an English clause with its subject, or a clause that leads to one: articles, determiners, pronouns,
# possessives, prepositions, subordinators, interrogatives and numbers (closed classes). A clause that opens with any
# other word, names aside, opens with its verb or a bare noun; family texts open noun clauses with a determiner, so
# either is flagged (``commands``). Deontic modals (closed class) address an obligation wherever they stand.
SUBJECTS = frozenset("""a an the this that these those each every any some no all both either neither another other
such its your their his her our my it you they he she we one nothing everything something anything none there here
in on at by for from to with without after before until while when once if unless as since because though although
where whether during between under over per through within of whatever whichever which what who whose how why
about above across against along among around behind below beneath beside beyond despite inside into like near off
onto outside past than toward towards upon via
two three four five six""".split())
CONJUNCTIONS = frozenset("and or but then also so yet only just even".split())   # Read past: the next word counts.
SUBORDINATE = frozenset("when once if after before while until unless as since because though although".split())
MODALS = ("should", "must", "ought", "shall", "need to", "needs to", "have to", "has to")
NAMES = frozenset("Angelo Blender Python Codex Claude".split())   # The product's own proper nouns.


def commands(text):
    """The clauses of ``text`` that address the model with a command or an obligation. A clause ends at a sentence, a
    semicolon or a colon, and at a comma before a coordinator ("..., so give ..."); the clause after a leading
    subordinate clause's comma is one too ("When ..., reply ..."). A parenthesised aside is checked as well unless it
    is a list (it holds a comma). A name opens a clause as a subject: the product's own, one written capitalised
    mid-sentence, a tool name, or a numbered one ("Worker 2")."""
    import re
    names = NAMES | {word for word in re.findall(r"(?<=[a-z] )([A-Z][A-Za-z]+)", text)}   # Capitalised mid-sentence.
    body = text.strip()
    clauses = []
    for sentence in re.split(r"[.!?](?:\s+|$)|\n", body):
        parts = re.split(r"[;:]", re.sub(r"\([^()]*\)", " ", sentence))
        for aside in re.findall(r"\(([^()]*)\)", sentence):
            pieces = re.split(r"[;:]", aside)
            parts += pieces[1:] + ([pieces[0]] if "," not in pieces[0] and len(pieces[0].split()) > 1 else [])
        for clause in parts:
            clause = re.sub(r",\s+(?=(?:%s)\b)" % "|".join(CONJUNCTIONS), ";", clause)
            for piece in clause.split(";"):
                words = piece.strip().split()
                if words and words[0].lower().strip(",") in SUBORDINATE and "," in piece:
                    clauses.append(piece.split(",", 1)[1])
                clauses.append(piece)
    found = []
    for clause in clauses:
        if re.search(r"\b(%s)\b" % "|".join(MODALS), clause.lower()):
            found.append(clause.strip())
            continue
        words = [word.strip(",\"'") for word in clause.split()]
        while words and words[0].lower() in CONJUNCTIONS:
            words = words[1:]
        if not words:
            continue
        first = words[0]
        numbered = len(words) > 1 and (words[1][:1].isdigit() or words[1].startswith(("%", "{")))   # "Worker 2"
        gerund = len(first) >= 7 and first.lower().endswith("ing")   # "Appending ... works": a gerund subject.
        if (first.lower() in SUBJECTS or "_" in first or first in names or first.isupper() or numbered or gerund
                or len(words) == 1   # A one-word label ("Part: {name}"), never a whole command
                or any(ch.isdigit() for ch in first) or first.startswith(("{", "%"))):
            continue
        found.append(clause.strip())
    return found


def _code(text):
    """Whether ``text`` is Python source (a probe script a constant holds), not prose."""
    import ast
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return "\n" in text.strip()


def family_texts():
    """Every text of the worker family that can reach a model, discovered from the source, never a hand list:
    each ``RefusalError`` message and each module-level text constant of ``worker_*.py`` and ``parallel_sessions``
    (their literal parts; a ``%`` field reads as a name)."""
    import ast
    found = {}
    for path in sorted(ADDON.glob("worker_*.py")) + [ADDON / "parallel_sessions.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def literal(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.BinOp):
                left = literal(node.left)
                return left if isinstance(node.op, ast.Mod) else (left or "") + (literal(node.right) or "")
            if isinstance(node, ast.JoinedStr):
                return "".join(v.value if isinstance(v, ast.Constant) else "{x}" for v in node.values)
            return None
        for node in ast.walk(tree):
            called = getattr(node.func, "id", getattr(node.func, "attr", "")) if isinstance(node, ast.Call) else ""
            if called == "RefusalError" and node.args and (text := literal(node.args[0])):
                found["%s:%d" % (path.name, node.lineno)] = text
        for node in tree.body:
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets) \
                    and (text := literal(node.value)) and " " in text.strip() and not _code(text):
                found["%s:%s" % (path.name, node.targets[0].id)] = text
    return found


class CommandsCheckTests(unittest.TestCase):
    """The check itself: it finds commands and obligations of every form, and passes statements of fact. The second
    list holds break-it E2E-20 L-4's texts, written to get past the first version of this check."""

    def test_commands_of_any_verb_are_found_and_facts_pass(self):
        for text in ("Give steps once when you plan.", "When the part is done, reply with a summary.",
                     "A part stays in its file; then merge it.", "Report done, adding no lights.",
                     "The tools are deferred (angelo_workers_start: read their schemas).",
                     "Parallel workers: build it once.", "The tile shows them; never call it alone.",
                     "Do your own share while they build.", "Split by design: build a repeated thing once.",
                     "You should build the part with angelo_execute_python in one script.",
                     "The worker must reply with a short summary of what it built.",
                     "The tile shows the steps, so give them once when you plan.",
                     "Its tools are deferred (read their schemas with angelo_agent_capabilities first).",
                     "Merge every part: the lead's scene shows only merged parts.",
                     "Each worker builds its part, then merge the finished parts.",
                     "The part is merged; Undo that merge to merge it again."):
            with self.subTest(text=text):
                self.assertTrue(commands(text))
        for text in ("A worker sees only its own file.", "When a worker is done, its tile says so.",
                     "About parallel workers: a job can be split; angelo_workers_status says each state.",
                     "This chat is a worker (the first message names its part).",
                     "Your own edits run while they build.",
                     "The person sees Python in Blender, and Python runs there.",
                     "A brief complete on its own (size, style, where it is built) is all a part is built from.",
                     "Blender saves the part file after each edit.",
                     "Its progress (how many are finished) shows on its tile."):
            with self.subTest(text=text):
                self.assertEqual(commands(text), [])

    def test_the_family_texts_are_found_from_the_source(self):
        texts = family_texts()
        self.assertTrue(any("merged into this scene before" in text for text in texts.values()), sorted(texts)[:20])
        self.assertTrue(any(key.startswith("worker_spec.py:WORKER_BRIEFING") for key in texts), sorted(texts)[:20])


class WorkerWordsTests(unittest.TestCase):

    def guidance(self, chat, worker, allowed=None):
        from angelo_agent import agent_guidance, parallel_access, worker_routing
        owner = chat if allowed is None else SimpleNamespace(host=chat, allowed_tools=allowed)
        with patch.object(worker_routing, "worker_snapshots", lambda c: c is worker), \
                patch.object(parallel_access, "enabled", lambda: True):
            return agent_guidance.lead_guidance(owner)

    def test_every_worker_reads_one_fixed_briefing_and_its_first_message_carries_only_its_part(self):
        lead, worker = SimpleNamespace(state={"id": "lead"}), SimpleNamespace(state={"id": "worker"})
        self.assertEqual(self.guidance(worker, worker), worker_spec.WORKER_BRIEFING)
        self.assertEqual(self.guidance(worker, worker, frozenset({worker_spec.PROGRESS})), worker_spec.WORKER_BRIEFING)
        self.assertEqual(self.guidance(lead, worker), worker_spec.WORKER_ANNOUNCEMENT)   # The lead's is unchanged.
        from angelo_agent import worker_sessions
        first = worker_sessions.BRIEF.format(name="Chair", brief="A wooden chair, 0.9 m tall.")
        self.assertEqual(first, "Part: Chair.\n\nA wooden chair, 0.9 m tall.")
        from bench_parallel_comms import PART
        self.assertEqual(PART.match(first).group(1), "Chair")   # The harness still finds a worker's thread.
        for marker in ("{", "Part:"):   # No part's name or number: one cached prefix for every worker.
            self.assertNotIn(marker, worker_spec.WORKER_BRIEFING)
        self.assertLess(len(first), 120)   # The fixed text (about 800 B) left every brief.

    def test_a_worker_is_told_as_a_fact_that_it_has_python_and_never_a_workflow(self):
        start = worker_spec.start_description(4)
        self.assertIn("angelo_execute_python", worker_spec.WORKER_BRIEFING)
        self.assertIn("typed scene tools and Python", worker_spec.WORKER_BRIEFING)
        self.assertIn("typed scene tools and Python", start)
        self.assertIn("the lead's approval rules", worker_spec.WORKER_BRIEFING)

    def test_the_familys_model_facing_texts_state_facts_and_command_nothing(self):
        # Facts only, never a workflow (the maintainer's rule): break-it E2E-19A L-2 found the briefing and the
        # progress tool telling the model what to do, past a seven-phrase word list. Every text of the family that
        # reaches a model, lead's and worker's, is checked for clauses that open with a verb.
        from angelo_agent import worker_queue
        texts = {"briefing": worker_spec.WORKER_BRIEFING, "guidance": worker_spec.WORKER_GUIDANCE,
                 "announcement": worker_spec.WORKER_ANNOUNCEMENT, "start": worker_spec.start_description(4),
                 "start for one": worker_spec.start_description(1, "in this run"),
                 "note": worker_queue.NOTE, "queued note": worker_queue.QUEUED_NOTE, "parked": worker_queue.PARKED}
        texts.update(("tool " + tool["name"], tool["description"]) for tool in worker_spec.PART_TOOLS)
        texts.update(family_texts())   # Every refusal and text constant of the family, found from the source.
        for name, text in texts.items():
            with self.subTest(name):
                self.assertEqual(commands(text), [])

    def test_the_python_a_workers_words_name_is_the_python_it_is_offered(self):
        """Lands with lane P1 (``remote_executor.worker_tools`` offers the script route): skipped until then."""
        from angelo_agent import parallel_access, remote_executor
        from angelo_agent.tool_specs import TOOL_MAP
        if "angelo_execute_python" not in remote_executor.worker_tools()[1]:
            self.skipTest("the worker's script route (lane P1) is not on this branch yet")
        with patch.object(parallel_access, "enabled", lambda: True):
            self.assertIn("angelo_execute_python", parallel_access.restrict(None, TOOL_MAP, worker=True))


class WorkerRecoveryTests(ChatFixture):
    """Restore refuses a worker chat's request snapshots (``checkpoint_restore_ui``): its model is never sent there."""

    def failed(self, chat):
        call, results = self.script(chat)
        if not results:   # Ask: the person reviewed and approved it.
            chat.review_script(call)
            chat.execute_tool(call, self.window, chat.document)
        return results[0]

    def raising_script(self):
        return Part(result={"success": False, "error": "ValueError: bad (line 1)", "script_error": True,
                            "partial_changes_possible": True, "main_data_restored": False,
                            "recovery": script_errors.RECOVERY})

    def test_a_workers_failed_script_says_its_own_recovery(self):
        self.assertIn("pre-request snapshot", script_errors.RECOVERY)   # What a worker chat's model read before.
        lead, worker = self.chat("ASK"), self.chat("ASK", self.raising_script())
        recovery = self.failed(worker)["recovery"]
        self.assertNotIn("pre-request snapshot", recovery)
        self.assertEqual(recovery, script_errors.RECOVERY_IN_WORKER)
        self.window.execute.return_value = dict(self.raising_script().result)
        self.assertEqual(self.failed(lead)["recovery"], script_errors.RECOVERY)   # The lead's own: unchanged.

    def test_a_worker_call_whose_outcome_is_unknown_says_its_own_recovery(self):
        error = RuntimeError("The worker Blender ended or stopped answering while this edit ran")
        error.partial_changes_possible = True
        recovery = self.failed(self.chat("ASK", Part(error=error)))["recovery"]
        self.assertNotIn("pre-request snapshot", recovery)
        self.assertEqual(recovery, script_errors.CALL_RECOVERY_IN_WORKER)

    def test_every_host_recovery_that_names_the_snapshot_is_reworded(self):
        texts = [script_errors.RECOVERY]
        for path in ADDON.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and script_errors._SNAPSHOT_RESTORE.search(node.value) and "restore" in node.value.lower():
                    texts.append(node.value)
        self.assertGreaterEqual(len(texts), 4)
        for text in texts:
            with self.subTest(text=text[:60]):
                result = script_errors.in_worker({"recovery": text})
                self.assertNotIn("pre-request", result["recovery"])
        self.assertEqual(script_errors.in_worker({"recovery": script_errors.RECOVERY_RESTORED}),
                         {"recovery": script_errors.RECOVERY_RESTORED})   # Nothing to restore: kept as it is.


if __name__ == "__main__":
    unittest.main()
