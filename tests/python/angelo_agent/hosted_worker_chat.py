# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``HostedWorkerChat``: one worker chat as design A′ runs it, for the worker-Blender suites (the hosted variant).

With ``ANGELO_WORKER_CHAT_HOST`` unset or ``1`` a worker chat's real Controller and provider Python run inside its
worker Blender, and this (background) Angelo keeps a mirror (``worker_mirror``). Nothing reaches into either side:

* the chat is made as the lead makes a worker's chat (``worker_sessions._new_chat``: a mirror) and started on its part
  as a worker start does (``worker_routing.start``: bind, then ``chat.open``);
* its model is the stand-in ``codex`` app-server (``hosted_codex_stand_in``) first on this Angelo's PATH: the lead's
  ``RuntimeService`` launches it with its own command and environment and relays it to the provider in the worker;
* a tool call is the model's: the suite queues a scripted turn for the stand-in, then sends the chat a message as a
  person does (``MirrorController.send``, a ``chat.send``); the worker's provider receives ``item/tool/call``, its
  Controller admits and runs the call with the worker's executor, and the model gets the answer;
* the lead's side is pumped as the window's timer pumps a mirror (``pump_events``, the consent lane's ``tick``); an
  Ask card is the mirror's pending summary, approved with Angelo's own approve operator; a result is read from the
  chat's journal as the mirror applied it (``state["results"]``), the same record the worker wrote.

``WorkerChat`` keeps the lanes (``ANGELO_WORKER_CHAT_HOST=0``); ``parallel_workers_qa.worker_chat`` picks one by the
switch. The calls the stand-in saw and their answers are in its log (``model_log``). No provider or paid call.
"""

import json
import os
import time
from pathlib import Path

import bpy

import hosted_codex_stand_in as stand_in
from angelo_agent import ui, worker_consent_lead, worker_routing

TURN_SECONDS = 180.0
BUSY_SECONDS = 5.0
OPEN_SECONDS = 15.0   # A Send's own bound: the worker answers between two pumps, a fresh one within seconds.
SEND_REFUSALS = []   # Each "already running" refusal of a send made once the mirror showed the chat idle (D3)


def hosted():
    """Whether worker chats run in their workers (``worker_chat_lifecycle.enabled``: the switch and the build)."""
    from angelo_agent import worker_chat_lifecycle
    return worker_chat_lifecycle.enabled()


def install_stand_in(folder):
    """The stand-in model first on PATH for this Angelo (and the workers it starts); its scripts and logs in
    ``folder``. Idempotent; run before any worker starts."""
    folder = Path(folder)
    if os.environ.get(stand_in.ENV) == str(folder):
        return folder
    os.environ[stand_in.ENV] = str(folder)
    from angelo_agent import agent_runtime
    # A worker checks its runtime's version against the one IT resolves (the bundled runtime: the worker does not
    # inherit ANGELO_AGENT_RUNTIME, worker_process.worker_environment), not against the stand-in the lead launched:
    # defect D1 in the FE report. The stand-in reports the pinned version so the turn can run at all.
    os.environ[stand_in.ENV + "_VERSION"] = agent_runtime.PIN["version"]
    os.environ["PATH"] = str(stand_in.install(folder / "bin")) + os.pathsep + os.environ.get("PATH", "")
    return folder


def connect(lead):
    """The lead chat's connection, as a person adds one (ChatGPT, in Angelo's connections): a worker chat inherits it,
    and the consent lane grants Auto only to a chat whose connection exists. The stand-in plays its model."""
    if lead.state.get("connection_id") and ui.connections().records.get(lead.state["connection_id"]):
        return lead.state["connection_id"]
    record = ui.connections().add("OPENAI", "SUBSCRIPTION")
    lead.state.update(provider=record["mode"], connection_id=record["id"])
    return record["id"]


def ui_tick():
    """The window timer's Angelo duties that belong to no chat: the tool pump's merge-gate sweep and the workers'
    part counts (``tool_pump.pump_controller_tools`` with no chat work), and the consent lane's deadlines. A suite
    that drives Angelo by hand calls it where the window's timer would run."""
    from angelo_agent import tool_pump
    from angelo_agent.controller import ToolDispatchBudget   # Never the tick's 8 ms (test_native_host_pump_budget).
    tool_pump.pump_controller_tools([], 0, None, None, None, budget=ToolDispatchBudget(seconds=60))
    worker_consent_lead.tick()


UNKNOWN_AT_MERGE = []   # Per hosted merge checked with ``counted_at_once``: its parts still being checked then.


def _unknown_parts():
    from angelo_agent import worker_merge, worker_part_counts, worker_sessions
    paths = [Path(part[2]) for part in list(worker_sessions._parts.values()) if Path(part[2]).is_file()]
    return [path for path in paths if worker_merge.part_object_count(path) == worker_part_counts.UNKNOWN]


def counted_at_once():
    """Right before a hosted merge, with no wait: whether every worker part's object count is known already, as the
    model's merge finds it. A worker reports its count with each save (``worker_part_counts.note``); a part still
    being checked would refuse that merge once (WRITER_BUSY, a round of the model's). Recorded per merge."""
    unknown = _unknown_parts()
    UNKNOWN_AT_MERGE.append(len(unknown))
    return not unknown


def settle_part_counts(timeout=10.0):
    """Until every known worker part's object count is read, for a file version no worker reported (a suite's own
    touch of the file): design A′ then reads a live worker's part under its lease, asked off the main thread and
    read on a UI tick, as the window's timer does. True when every count is known within ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        if not _unknown_parts():
            return True
        if time.monotonic() >= deadline:
            return False
        ui_tick()
        time.sleep(0.01)


def pump_lead(mirrors=()):
    """One lead UI pump for ``mirrors``: their forwarded frames applied, the consent lane's deadlines checked."""
    for mirror in mirrors:
        mirror.pump_events()
    worker_consent_lead.tick()


class HostedWorkerChat:
    def __init__(self, state, part, root, approval="AUTO_BOUNDED", environ=None, lead=None, worker=None):
        from angelo_agent import worker_sessions
        install_stand_in(Path(state).parent / "stand-in")
        self.lead = lead if lead is not None else ui.get_controller()
        connect(self.lead)
        self.lead.state["options"] = dict(self.lead.state.get("options") or {})
        options = dict(self.lead.state["options"])
        self.lead.state["options"]["approval"] = approval   # The new chat inherits the lead's mode, as it does.
        try:
            self.controller = worker_sessions._new_chat(self.lead)
        finally:
            self.lead.state["options"] = options
        self.session = self.controller.state["id"]
        started = time.perf_counter()
        if worker is None:
            self.executor = worker_routing.start(self.controller, part, binary=bpy.app.binary_path,
                                                 script=Path(root) / "scripts/angelo/angelo_headless.py",
                                                 environ=environ)
        else:   # A running worker that took ``part`` (warm reuse): bound as ``worker_sessions._launching`` binds it.
            worker.part = Path(part)
            worker_routing.ready(worker)
            self.executor = worker_routing.adopt(self.controller, worker)
        self.start_seconds = time.perf_counter() - started
        self.worker = worker_routing._workers[self.session]
        self.timings, self.pumps, self._count, self._sends = [], [], 0, 0
        self._outstanding = []   # (call id, answers) still waiting for their result

    @classmethod
    def of(cls, controller):
        """A driver for a worker chat Angelo itself made and started (``angelo_workers_start``): its mirror, bound to
        its worker. A ``send`` a suite put on the mirror to record a brief is taken off: this driver's messages go."""
        from angelo_agent.worker_consent_lead import _connected
        if not _connected(controller):   # Else every turn is refused: its model never starts (WRK-NO-CONNECTION).
            raise AssertionError("worker chat %s has no usable pinned connection, so its model never launches "
                                 "(worker_chat_wiring.launch_chat): connect its lead (connect) before it starts "
                                 "workers, as a person's lead has one" % controller.state.get("id"))
        chat = cls.__new__(cls)
        controller.__dict__.pop("send", None)
        chat.lead, chat.controller, chat.session = None, controller, controller.state["id"]
        chat.executor = worker_routing.bound(controller)
        chat.worker = worker_routing._workers.get(chat.session) or getattr(chat.executor, "worker", None)
        chat.start_seconds, chat.timings, chat.pumps, chat._count, chat._sends = 0.0, [], [], 0, 0
        chat._outstanding = []
        return chat

    @staticmethod
    def send_brief(controller, text, steps=({"hold": "brief"},)):
        """A worker's brief really sent to its hosted chat (the stand-in model plays ``steps``; by default it keeps
        working until the suite ends its worker, as a model building a part does). The chat's tag is appended to
        the brief's text so the stand-in finds the chat's queue (``hosted_codex_stand_in``)."""
        chat = HostedWorkerChat.of(controller)
        chat.queue_turn(list(steps))
        type(controller).send(controller, "%s %s" % (text, stand_in.tag(chat.session)))
        return chat

    # The model's side --------------------------------------------------------------------------------------------

    @property
    def folder(self):
        folder = Path(os.environ[stand_in.ENV]) / self.session
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def queue_turn(self, steps):
        """The stand-in's next turn of this chat: ``steps`` (``hosted_codex_stand_in``)."""
        sequence = int(time.time() * 1e6)
        temporary = self.folder / ("writing-%d.json" % sequence)
        temporary.write_text(json.dumps(steps), encoding="utf-8")
        os.replace(temporary, self.folder / ("queued-%d.json" % sequence))

    def model_log(self, kind=None):
        """What the stand-in model saw, in order (its ``log.jsonl``); only ``kind`` rows when given."""
        try:
            lines = (self.folder / "log.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows = [json.loads(line) for line in lines if line.strip()]
        return [row for row in rows if kind is None or row.get("kind") == kind]

    def release(self, name):
        (self.folder / ("release-" + name)).write_text("go", encoding="utf-8")

    # The person's side -------------------------------------------------------------------------------------------

    def say(self, text, steps):
        """A person's message whose turn the stand-in plays as ``steps``, sent once the mirror shows the chat idle, and
        waited for until the worker took it (its turn reached the model) or refused it.

        A Send may be answered later (mirror ops with request ids, reconciled on the UI timer): the state is waited
        for, never assumed from the call. A refusal because the worker is still busy just after its mirror went idle
        (defect D3 in the FE report: the worker forwards a turn's end before its turn thread is done) is retried for
        at most ``BUSY_SECONDS``, each one recorded in ``SEND_REFUSALS`` for the suite's report, never hidden."""
        from angelo_agent.worker_mirror import MirrorRefused
        self.wait_idle()
        self.queue_turn(steps)
        self._sends += 1
        marker = "%s #%d" % (stand_in.tag(self.session), self._sends)   # Which chat's queue; which message.
        text = "%s %s" % (text, marker)
        started = time.perf_counter()
        while True:
            try:
                self.controller.send(text)
                why = self._taken(marker)
            except MirrorRefused as error:
                why = str(error)
            if why is None:
                return
            waited = time.perf_counter() - started
            if "already running" not in why or waited > BUSY_SECONDS:
                raise MirrorRefused(why)
            SEND_REFUSALS.append({"session": self.session, "waited_s": round(waited, 4)})
            self.pump()

    def _taken(self, marker, timeout=OPEN_SECONDS):
        """None once the worker's turn for ``marker`` reached the model; else why the Send came back."""
        started = time.perf_counter()
        while time.perf_counter() - started < timeout:
            if any(marker in str(row.get("text")) for row in self.model_log("turn_started")):
                return None
            pump_lead([self.controller])
            if not self.controller.busy:   # Refused (answered at once, or reconciled later): nothing was sent.
                rows = [m for m in self.controller.state.get("messages") or () if m.get("role") == "error"]
                return str(rows[-1].get("text") if rows else "the worker did not take the message")
            time.sleep(0.002)
        return None   # Still sending: the worker may yet take it (its turn's events then show it).

    def request(self, name, arguments, text=None, hold=None):
        """The model calls ``name`` in a new turn; ``(call id, answers)``, answered by ``pump``. ``hold``: its turn then
        keeps working until ``release(hold)``, as a model's turn goes on after a call."""
        self._count += 1
        call_id = "qa-%d" % self._count
        answers = []
        steps = [{"call": {"id": call_id, "tool": name, "arguments": arguments}}] + ([{"hold": hold}] if hold else [])
        self.say(text or "Please run %s." % name, steps)
        self._outstanding.append((call_id, answers))
        return call_id, answers

    def result_of(self, call_id):
        results = self.controller.state.get("results") or {}
        for key, record in results.items():
            if (key == call_id or key.endswith("/" + call_id)) and record.get("phase") == "completed":
                return record.get("result")
        return None

    def _model_answer(self, call_id):
        for row in self.model_log("tool_result"):
            if row.get("id") == call_id:
                try:
                    return json.loads(row.get("text") or "null") or {"error": row.get("error")}
                except ValueError:
                    return {"error": row.get("text")}
        return None

    def pump(self):
        started = time.perf_counter()
        pump_lead([self.controller])
        for call_id, answers in list(self._outstanding):
            result = self.result_of(call_id)
            if result is None and not self.controller.busy and self.controller.turn_outcome:
                result = self._model_answer(call_id)   # Interrupted or refused before a journal record.
            if result is not None:
                answers.append(result)
                self._outstanding.remove((call_id, answers))
        self.pumps.append((time.perf_counter() - started, self.controller.busy))
        time.sleep(0.002)   # As the UI timer comes back, never a busy spin.

    def wait(self, answers, timeout=TURN_SECONDS):
        started = time.perf_counter()
        while not answers and time.perf_counter() - started < timeout:
            self.pump()
        if not answers:
            raise AssertionError("not answered within %d s (chat %s)" % (timeout, self.session))
        return answers[0]

    def wait_idle(self, timeout=TURN_SECONDS):
        """Until the chat's turn has ended (its mirror shows it), as a person waits before the next message."""
        started = time.perf_counter()
        while self.controller.busy and time.perf_counter() - started < timeout:
            self.pump()
        if self.controller.busy:
            raise AssertionError("the turn never ended within %d s (chat %s)" % (timeout, self.session))

    def call(self, name, arguments, timeout=TURN_SECONDS):
        """The model's call in a new turn, answered: its result; a message the worker did not take (ended, frozen)
        is an error result with nothing sent, as a lane's refused call is (``NOT_SENT``)."""
        from angelo_agent.worker_mirror import MirrorRefused
        started = time.perf_counter()
        try:
            _call_id, answers = self.request(name, arguments)
        except MirrorRefused as error:
            self.timings.append((name, time.perf_counter() - started))
            return {"error": str(error), "error_code": "NOT_SENT", "scene_changed": False}
        result = self.wait(answers, timeout)
        self.wait_idle(timeout)
        self.timings.append((name, time.perf_counter() - started))
        return result

    def call_approving(self, name, arguments, timeout=TURN_SECONDS):
        """``call``, approving its card if one is drawn (the person says yes): ``(result, asked)``."""
        call_id, answers = self.request(name, arguments)
        started, asked = time.perf_counter(), False
        while not answers and time.perf_counter() - started < timeout:
            self.pump()
            card = self.card(call_id)
            if card is not None and card.approval and not asked and not getattr(card, "approved", False):
                self.draw(card)
                asked = True
                self.approve(card)
        result = self.wait(answers, 1)
        self.wait_idle(timeout)
        return result, asked

    def card(self, call_id):
        for key, call in list(self.controller.pending.items()):
            if (key == call_id or key.endswith("/" + call_id) or getattr(call, "id", None) == call_id) \
                    and getattr(call, "needs_person", False):
                return call
        return None

    def wait_for_approval(self, key, answers=None, timeout=60.0):
        """The mirror's card for call ``key``, drawn from the worker's pending summary."""
        started = time.perf_counter()
        while time.perf_counter() - started < timeout:
            self.pump()
            call = self.card(key)
            if call is not None and call.approval and not getattr(call, "executing", False):
                self.draw(call)   # The person sees the card before any Approve.
                return call
            if answers:
                raise AssertionError("answered without an approval card: %r" % (answers[0],))
        raise AssertionError("no approval card for " + key)

    @staticmethod
    def operators():
        """The window's card operators a background Angelo does not register (``headless_main._register``)."""
        for cls in (ui.ANGELO_OT_review_tool, ui.ANGELO_OT_decline, ui.ANGELO_OT_set_approval):
            if not hasattr(bpy.types, cls.__name__):
                bpy.utils.register_class(cls)

    def draw(self, call):
        """The card as the chat's drawing records it (``worker_consent_lead.drew``, which ``chat_guard.shown_approvals``
        calls once a card is on screen): its Approve sends the digest drawn. A background Angelo has no window to
        draw in, so this is the same draw-then-approve seam the host tests use; a build without it records nothing."""
        drew = getattr(worker_consent_lead, "drew", None)
        if callable(drew):
            drew(self.controller, call.id)

    def approve(self, call):
        """Angelo's own approve operator on the card, as the person's clicks run it: a script's first Approve opens
        its exact-code review (``angelo.review_tool``, registered here as the window has it), the next one sends."""
        self.operators()
        self.draw(call)
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            done = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=self.session)
            if done != {'FINISHED'} and call.id in self.controller.reviewed_calls:
                done = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=self.session)
        return done

    def decline(self, call):
        """Angelo's own decline operator on the card."""
        self.operators()
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            return bpy.ops.angelo.decline('EXEC_DEFAULT', True, call_id=call.id, session_id=self.session)

    def new_request(self):
        """A person's next message opens a new request by itself here: nothing to do."""

    def writing(self, timeout=60.0):
        """Until the worker runs one of this chat's calls now (its pending summary says so,
        ``MirrorController.writing``)."""
        started = time.perf_counter()
        while time.perf_counter() - started < timeout:
            self.pump()
            if self.controller.writing():
                return True
        return False

    def runtime_children(self):
        """The model children Angelo's runtime service runs for this chat now (its worker's ``RuntimeService``)."""
        runtime = getattr(self.worker, "runtime", None)
        return [child for child in (runtime.children() if runtime is not None else ())
                if child.session == self.session and child.alive()]

    def objects(self):
        found = self.call("angelo_inspect_scene", {})
        if "objects" not in found:   # Say what came back instead (an error, a refusal), never a bare KeyError.
            raise AssertionError("angelo_inspect_scene answered no objects: %s" % str(found)[:400])
        return {row["name"]: row for row in found["objects"]}

    def stop(self):
        try:
            worker_routing.unbind(self.controller, worker_routing.END_CLOSING)
        finally:
            ui._controllers.pop(self.session, None)
