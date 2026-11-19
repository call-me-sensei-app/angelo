# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main thread: a connected app's channel to Angelo's agent in its External conversation (part of ``external_host``).

An app never calls a tool: Angelo's MCP is an interface to Angelo's own agent, which the app starts and supervises like
a sub-session, with the benefit a person gets in Angelo's chat. It sends the agent messages (``send``: a turn starts
when the conversation is idle, else the message is steered into the running turn or queued after it, exactly as the
person's Enter does), stops the turn (``stop``: the person's Stop), follows the task it started (``status``: the step in
progress, elapsed time, usage so far and, once the turn ends, its result) and observes: the transcript as the agent sees
it (``read``, a long poll on the chat's change counter), a picture the agent got (``image``), the scene observation the
next turn carries (``scene``) and the viewport as ``angelo_view`` shows it to the agent (``viewport``). Approvals, Go,
modes and consent stay the person's presses in the window; no action here reaches them. doc/angelo/external_mcp.rst,
"Working through Angelo's agent".

A document load invalidates an External conversation as it does any chat; the app's next ``send`` moves it to a fresh
conversation on what the window shows and says so (``moved``).
"""

import time
from pathlib import Path

from . import external_send_facts as send_facts, external_status as status
from .external_look import AgentLooks
from . import external_visibility as visibility
from .external_settings import WAITS_FOR_PERSON, choose
from . import external_transcript as transcript
from .external_clients import APP_NAMES

SENDS_PER_MINUTE = 30
# A goal sent with the task is the app's own words (goal_mode.rst, the person's-words rule): Angelo's agent may hold it
# with angelo_goal; under Ask it is proposed and the person's Start makes it active.
GOAL_LINE = "\n\nKeep working until this is done (a goal): %s"
# The goal an app sent, in its state until the agent holds one (then the held goal's summary): never invisible.
GOAL_WITH_TASK = ("Sent with the task's message. Angelo's agent holds it as a goal (rounds, budget, score) when the "
                  "work needs more than one round; the state then shows that goal")
# Paused messages (steering.PAUSED: "Not sent" in the chat's tray): Angelo sends none of them by itself.
NOT_SENT_WHY = {"stopped": "the person stopped the turn before them", "failed": "the turn before them ended with a "
                "problem", "not_started": "they did not reach Angelo's agent", "relaunch": "Angelo closed before "
                "they were sent"}
NOT_SENT_NEXT = ("Angelo never sends them by itself: the person sends or removes them in the chat's tray in Angelo, "
                 "or send yours again with angelo_send")
MAX_TASKS = 64   # Task ids an app's status can name (its newest); an older one reads as the conversation's.
MAX_MESSAGE = 32000
# A parked read answers a change no sooner than this after it was parked, so a reply streaming every tick costs the
# launcher a few local reads a second, never one per tick; a turn that settles answers at once.
MIN_WAKE = 0.25
# Parked reads hold a CLI connection each (external_cli.MAX_CONNECTIONS, 8): at most this many per app and in all,
# so a Stop, a status or another app's command always finds one free.
MAX_PARKED_PER_APP, MAX_PARKED = 2, 5
NO_CONNECTION = ("Angelo has no AI connection for this conversation. Ask the person to connect Claude or ChatGPT in "
                 "Angelo (the conversation \"External: {app}\" shows how), then send again.")


def app_name(registry_record):
    return APP_NAMES.get((registry_record or {}).get("app"), APP_NAMES["other"])


class AgentUi:
    """Mixed into ``external_host.UiEnvironment``: the window's side of an app's message, Stop and looks (``self.ui``
    is Angelo's ``ui`` module); tests substitute a bpy-free double."""

    def attach(self, controller, attachments):
        """The app's attachments as the conversation's own references (``external_attach``)."""
        from .external_attach import attach
        return attach(self, controller, attachments)

    def deliver(self, controller, text, references=()):
        """An app's message to Angelo's agent, as the person's Enter sends one (``console.send_message``): while the
        conversation's turn runs, or the person's tool holds the viewport, it is steered into the turn or queued after
        it (``chat_pending.hold``), else a turn starts (``console.submit``: the connection prepared, the scene
        observed). Never the window's composer pictures. Returns ``started``, ``steered`` or ``queued``; raises what
        refused it."""
        from . import chat_pending, console, edit_tool_hold
        from .controller_external import external_send
        from .steering import STEER
        if controller.turn_running() or edit_tool_hold.running():
            item = chat_pending.hold(controller, text, list(references))
            return "steered" if item.mode == STEER else "queued"
        with external_send(controller):
            console.submit(controller, text, references=list(references))
        return "started"

    def stop(self, controller):
        """The person's Stop (``ANGELO_OT_stop``): its notice, a held goal's end, then the turn's."""
        from .stop_notice import person_stop
        controller.emit("message", {"role": "system", **person_stop(controller)})
        controller.stop()

    def models(self, controller):
        """The models the person has connected, as the chat's model picker lists them (``ui.model_choices``)."""
        return self.ui.model_choices(controller)

    def models_loading(self, controller):
        """``(loading, error)`` of the connected models' catalogue; an empty one is fetched now, as the picker's
        opening fetches it, so an app's next read lists them (a fresh profile has asked no connection yet)."""
        ready = getattr(self.ui, "ready_connections", None)
        if ready is not None and not ready():
            from .provider_registry import is_coding
            coding = [record for record in getattr(self.ui, "connection_records", lambda: {})().values()
                      if is_coding(record)]
            running = getattr(getattr(self.ui, "_authentication", None), "running", ())
            if any((record["mode"] if record.get("reuse_native") else record["id"]) in running for record in coding):
                return True, ""   # A sign-in check still runs (right after a start): read again shortly.
            if coding:   # Checked, and none confirmed: the person's to look at, never "loading" forever.
                return False, ("No connected AI account has a confirmed sign-in in Angelo; ask the person to check "
                               "AI Providers")
            return False, "No AI connection is connected in Angelo"
        loading, error = self.ui.model_catalog_notice(controller)
        if not loading and not self.ui.model_choices(controller):
            self.ui.refresh_connected_models()
            loading, error = self.ui.model_catalog_notice(controller)
            loading = loading or not error
        return bool(loading), str(error or "")

    def choose_model(self, controller, model, connection_id=""):
        """The model picker's own choice (``ANGELO_OT_select_model``), for one of the person's connected models."""
        from .chat_settings import carried_effort, clean_options
        identity = connection_id or controller.selection_connection_id
        if identity:
            self.ui.choose_model_connection(controller, identity, model, remember=False)   # Never the person's default.
            return
        found = next((m for m in controller.selection_models if m["id"] == model), None)
        options = clean_options(controller.state.get("options"))
        controller.set_options(model=model, fast=options["fast"] and bool(found and found["fast_tier"]),
                               effort=carried_effort(options, found))

    def scene(self, controller):
        """The scene observation the conversation's next turn carries (``turn_context``), as the agent gets it."""
        from . import turn_context
        return turn_context.build(self.ui.main_window())

    def viewport(self, controller):
        """``angelo_view`` from the person's viewport direction, captured into the conversation's own pictures as the
        agent's views are (no row, no request); the picture's id."""
        from .blender_tools import BlenderExecutor
        executor = BlenderExecutor(self.ui.main_window())
        executor.observation_store, executor.studio_context = controller.references, controller
        result = executor.execute("angelo_view", {"views": ["FIT"]})
        views = result.get("views") if isinstance(result, dict) else None
        if not views:
            raise RuntimeError(str((result or {}).get("error") or "Angelo could not capture the viewport"))
        return views[0]["id"]


class AgentChannels(AgentLooks, visibility.AgentVisibility):
    """Mixed into ``ExternalHost``: ``self.env``, ``self.conversation``, ``self._reply``, ``self.apps``."""

    # Conversation ---------------------------------------------------------------------------------------------------

    def _current(self, label):
        """The app's External conversation for the shown document, moved to a fresh one after a load or a switch to
        another scene; (controller, moved, error)."""
        controller = self.conversation(label, create=True)
        document = self.env.document_key()
        if not controller.document_invalid and controller.document in (None, document):
            return controller, False, None
        if controller.turn_running() or controller.pending:
            return controller, False, ("busy", "Angelo's window now shows another scene or file and this app's "
                                       "earlier turn is still finishing; read until it ends, then send again.")
        self._move(label, controller, "The window now shows another scene or file; '{label}' continues in "
                                                 "conversation {identity}. This transcript stays readable.")
        return self.conversation(label, create=False), True, None

    def _move(self, label, old, template):
        """A fresh External conversation for ``label``; ``old`` keeps a notice (``template``) and stays readable."""
        from .chat_settings import clean_options
        from .ui_text import notice
        from . import goal_loop
        from .external_settings import record_end
        mode = clean_options(old.state.get("options"))["approval"]
        record_end(self.env.state_directory(), label, old.state, goal_loop.goal_of(old))
        identity = self.create_conversation(label, previous=old.state["id"], approval=mode)
        old.emit("message", {"role": "system", **notice(template, label=label, identity=identity)})
        _end_page(old.state["id"], "moved")
        self._owned.setdefault(label, set()).add(identity)
        return identity

    def _named(self, label, conversation):
        """The app's conversation by id (an earlier one stays readable), else its current one."""
        current = self.conversation(label, create=False)
        if conversation and (current is None or current.state["id"] != conversation):
            owned = self._owned.get(label, ())
            controller = self.env.controllers().get(conversation) if conversation in owned else None
            return controller
        return current

    # Commands -------------------------------------------------------------------------------------------------------

    def handle_agent(self, work):
        label, payload = work.client.label, work.payload
        action = payload.get("action")
        if action == "send":
            return self._send(work, label, payload)
        if action in {"read", "status"}:
            return self._read(work, label, payload)
        if action == "new_chat":
            return self._new_chat(work, label)
        controller = self._named(label, payload.get("conversation"))
        if controller is None and action in visibility.WITHOUT_CONVERSATION:
            return self._read_only(work, None, action, payload)   # State and usage create nothing.
        if controller is None and action in {"scene", "viewport", "settings", "look", "window"} \
                and not payload.get("conversation"):
            controller = self.conversation(label, create=True)   # Before its first message: as that message would.
            self._owned.setdefault(label, set()).add(controller.state["id"])
        if controller is None:
            return self._reply(work, None, "ok", "No conversation yet", result=self._state(None, label))
        if action == "stop":
            return self._stop(work, controller)
        if action in visibility.READ_ONLY:   # One guard for every read (external_visibility._read_only).
            return self._read_only(work, controller, action, payload)
        if action == "settings":
            return self._settings(work, controller, payload)
        return self._reply(work, controller, "invalid", "Unknown agent action")

    def _send(self, work, label, payload):
        if not self._send_allowed(label):
            return self._reply(work, None, "busy", "This app sent %d messages in the last minute; wait, read the "
                               "conversation, then send again" % SENDS_PER_MINUTE)
        controller, moved, error = self._current(label)
        if error:
            return self._reply(work, controller, *error, result=self._state(controller, label))
        self._owned.setdefault(label, set()).add(controller.state["id"])
        app = app_name(self.apps().get(label))
        words = payload["message"] + (GOAL_LINE % payload["goal"] if payload.get("goal") else "")
        text = transcript.framed(app, words)
        try:   # Attachments first: a refused one changes nothing, the model and effort included.
            references = self.env.attach(controller, payload.get("attachments") or ())
        except (OSError, ValueError) as error:
            return self._reply(work, controller, "invalid", str(error), result=self._state(controller, label))
        if payload.get("model") or "effort" in payload:   # Its model and effort for this turn on, as in settings.
            changed, _needs, error = choose(self.env, controller, app, {key: payload[key] for key in
                                                                    ("model", "connection", "effort") if key in payload})
            for row in changed:
                controller.emit("message", {"role": "system", **row})
            if error:
                return self._reply(work, controller, *error, result=self._state(controller, label))
        before = len(controller.state["messages"])
        controller.setup_required = False   # A one-shot signal: only this send's own refusal may set it again.
        try:
            delivered = self.env.deliver(controller, text, references)
        except OverflowError as error:   # The tray's own limit: too many messages waiting.
            return self._reply(work, controller, "busy", str(error), result=self._state(controller, label))
        except (RuntimeError, ValueError) as error:
            # No usable connection: ``ui.prepare_controller`` raised and set the chat's one-shot connect signal, so
            # the conversation shows Connect when the person opens it (``chat_welcome``).
            if getattr(controller, "setup_required", False):   # The CLI's code; the launcher names the reason.
                return self._reply(work, controller, "not_applied",
                                   NO_CONNECTION.format(app=app_name(self.apps().get(label))),
                                   result=dict(self._state(controller, label), reason="needs_connection"))
            return self._reply(work, controller, "not_applied", str(error), result=self._state(controller, label))
        self._sends.setdefault(label, []).append(time.monotonic())
        from . import session_page   # The session's read-only live page (doc/angelo/external_mcp.rst), its share
        session_page.link(controller, label, app_name(self.apps().get(label)))   # notice before the cursor.
        self._goals.setdefault(label, {})[controller.state["id"]] = payload.get("goal") or None   # The newest send's.
        task = status.track(self._tasks.setdefault(label, {}), self._held.setdefault(label, {}), controller, delivered,
                            before)   # A held message's own id, bound when it is delivered (2.0.1 audit P2-3).
        while len(self._tasks[label]) > MAX_TASKS:
            oldest = next(iter(self._tasks[label]))
            self._tasks[label].pop(oldest), self._held[label].pop(oldest, None)
        result = dict(self._state(controller, label), delivered=delivered, task=task,   # live_page: its state's.
                      cursor=transcript.cursor(controller.state["id"], before, controller.version))
        if moved:
            result["moved"] = True
        wait = payload.get("wait", 0)
        if wait:
            return self._park(work, controller, label, result["cursor"], wait, payload.get("detail", "brief"), result)
        return self._reply(work, controller, "ok", delivered, result=result)

    def _send_allowed(self, label):
        now = time.monotonic()
        recent = [stamp for stamp in self._sends.get(label, ()) if now - stamp < 60.0]
        self._sends[label] = recent
        return len(recent) < SENDS_PER_MINUTE

    def _stop(self, work, controller):
        if controller.turn_running():
            self.env.stop(controller)
            return self._reply(work, controller, "ok", "Stopping", result=self._state(controller, work.client.label))
        return self._reply(work, controller, "ok", "Nothing was running", result=self._state(controller, work.client.label))

    def _read(self, work, label, payload):
        controller = self._named(label, payload.get("conversation"))
        if controller is None:
            return self._reply(work, None, "ok", "No conversation yet",
                               result=dict(self._state(None, label), rows=[], cursor=None, more=False))
        after, wait = payload.get("after"), payload.get("wait", 0)
        extra = {"task": payload.get("task") or ""} if payload.get("action") == "status" else None
        if wait and not transcript.changed_since(after, controller.state["id"], controller.version) \
                and self._live(controller):
            return self._park(work, controller, label, after, wait, payload.get("detail", "brief"), extra)
        return self._answer_read(work, controller, label, after, payload.get("detail", "brief"), extra)

    def _live(self, controller):
        """Whether anything can still change without the app: a turn, a pending call, a queued message."""
        return bool(controller.turn_running() or controller.pending or len(getattr(controller, "inbox", ()) or ()))

    def _park(self, work, controller, label, after, wait, detail, extra=None):
        now = time.monotonic()
        mine = sum(1 for waiter in self._agent_waiters if waiter["label"] == label)
        if mine >= MAX_PARKED_PER_APP or len(self._agent_waiters) >= MAX_PARKED:
            if extra is not None and "delivered" in extra:   # A send already went: answered now, never "busy" (which
                return self._reply(work, controller, "ok", extra["delivered"], result=extra)   # would send it twice).
            return self._reply(work, controller, "busy", "This app already has reads waiting; read again when they "
                               "answer", result=self._state(controller, label))
        self._agent_waiters.append({"work": work, "controller": controller, "label": label, "after": after,
                              "detail": detail, "deadline": now + wait, "extra": extra, "parked": now,
                              "version": controller.version, "turn": self._state(controller, label)["turn"]})
        return None

    def _answer_read(self, work, controller, label, after, detail, extra=None):
        from .worker_consent import awaiting_person   # The card's own predicate (approval_wait.awaiting).
        waiting = {call.id for call in tuple((controller.pending or {}).values()) if awaiting_person(call)}
        rows, position, more = transcript.read(controller.state["messages"], controller.state["id"], after,
                                               controller.version, running=self._live(controller), detail=detail,
                                               waiting=waiting)
        # A parked send's own facts only (``external_send_facts``): the state is this read's, never the send's.
        result = send_facts.answer(extra, dict(self._state(controller, label), rows=rows, cursor=position, more=more))
        if extra is not None and "task" in extra:   # ``status``: the task's progress, spend and result.
            result.update(self._task_status(controller, label, extra.get("task")))
        return self._reply(work, controller, "ok", result["turn"], result=result)

    def _task_status(self, controller, label, task):
        """The ``status`` fields of ``task`` (this app's latest when unnamed) in ``controller``."""
        tasks, held = self._tasks.get(label, {}), self._held.get(label, {})
        status.settle(tasks, held, controller)   # Held messages delivered since: bound to their request's row.
        mine = [name for name in tasks if status.belongs(name, controller.state["id"])]
        task = task if task in tasks else (mine[-1] if mine and not task else None)
        now = status.usage(controller)
        from .external_result import completed, cost   # Ends at the next task's start: the result's own figure (P2-4).
        mine_tasks = {name: tasks[name] for name in mine}
        found = {"task": task, "step": status.step(controller), "elapsed_seconds": status.elapsed(controller),
                 "usage": cost(mine_tasks, task, now) if task else None, "conversation_usage": now}
        row = status.task_row(task, controller, held) if task else None
        if row is not None and not self._live(controller):
            found["result"] = completed(status.result(controller.state["messages"], row), controller.state["messages"],
                                        row, mine_tasks, task, now)
        return found

    def pump_agents(self):
        """Main thread, each tick: answer parked reads whose conversation changed, ended or timed out."""
        now = time.monotonic()
        for waiter in list(self._agent_waiters):
            controller, work = waiter["controller"], waiter["work"]
            settled = not self._live(controller) or self._state(controller, waiter["label"])["turn"] != waiter["turn"]
            changed = settled or (controller.version != waiter["version"] and now - waiter["parked"] >= MIN_WAKE)
            if changed or now >= waiter["deadline"] or work.response is not None:
                self._agent_waiters.remove(waiter)
                if work.response is None:
                    after = waiter["extra"].get("cursor", waiter["after"]) if waiter["extra"] else waiter["after"]
                    self._answer_read(work, controller, waiter["label"], after, waiter["detail"], waiter["extra"])

    def close_agents(self, why):
        """Access turned off: every parked read is answered now (its work is the app's to read again later)."""
        for waiter in list(self._agent_waiters):
            if waiter["work"].response is None:
                waiter["work"].complete({"code": "unavailable", "message": why})
        self._agent_waiters.clear()
        for owned in self._owned.values():
            for identity in owned:
                _end_page(identity, why)

    def _new_chat(self, work, label):
        """A fresh External conversation for this app, as ``angelo session new`` makes one; the old stays readable."""
        old = self.conversation(label, create=False)
        if old is not None and (old.turn_running() or old.pending):
            return self._reply(work, old, "busy", "Angelo's agent is still working in this conversation; stop it "
                               "(angelo_stop) or wait for it, then start a new one")
        if old is None:
            controller, previous = self.conversation(label, create=True), None
        else:
            previous = old.state["id"]
            self._move(label, old, "'{label}' started a new conversation, {identity}. This transcript stays readable.")
            controller = self.conversation(label, create=False)
        self._owned.setdefault(label, set()).add(controller.state["id"])
        return self._reply(work, controller, "ok", "New conversation", result=dict(
            self._state(controller, label), previous=previous,
            cursor=transcript.cursor(controller.state["id"], 0, controller.version)))

    def _settings(self, work, controller, payload):
        """Read the conversation's settings; set its model and effort among the person's connected models; narrow its
        approval. Widening the approval or any consent is the person's press: said, and shown to them in Angelo."""
        from .chat_popovers import effort_choices
        from .chat_settings import chat_effort, clean_options, selected_model
        label = work.client.label
        changed, needs, error = choose(self.env, controller, app_name(self.apps().get(label)), payload)
        for row in changed:   # Visible in the conversation, as the person's own choices are (applied ones too).
            controller.emit("message", {"role": "system", **row})
        if error:
            return self._reply(work, controller, *error)
        options = clean_options(controller.state.get("options"))
        model = selected_model(controller)
        models = self.env.models(controller)
        loading, error = self.env.models_loading(controller) if not models else (False, "")
        result = dict(self._state(controller, label), model=(model or {}).get("id") or options["model"],
                      effort=chat_effort(options, model), efforts=effort_choices(model), approval=options["approval"],
                      fast=options["fast"], models=[{key: m.get(key) for key in ("id", "name", "connection_id",
                                                                                   "connection_name", "billing")}
                                                    for m in models],
                      consents={"trusted_scripts": bool(getattr(controller, "trusted_scripts", False))})
        if not models:   # Never an empty list the app can't tell from "nothing connected": say which it is.
            result["models_loading"], result["models_error"] = loading, error
        if needs:
            result["needs"] = needs
        return self._reply(work, controller, "ok", "Settings", result=result)

    # State ----------------------------------------------------------------------------------------------------------

    def _state(self, controller, label):
        asked = self._goals.get(label, {}).get(controller.state["id"]) if controller is not None else None
        state = conversation_state(controller, asked)
        from .session_page_ended import ended   # Its page ended after an error: said, never a dead link kept.
        if controller is not None and ended(controller.state["id"]):
            state["live_page_ended"] = ended(controller.state["id"])
        return send_facts.with_live_page(state, controller) if controller is not None else state


def not_sent_why(reasons):
    """Why paused messages were not sent, in words (a refused send's own notice reads as not sent)."""
    words = sorted({NOT_SENT_WHY.get(reason, "they were not sent") if isinstance(reason, str) else "they were not sent"
                    for reason in reasons})
    return "; ".join(words)


def conversation_state(controller, asked_goal=None):
    """The conversation's state as the app reads it (doc/angelo/external_mcp.rst, "State"): a function of its
    arguments alone, so the live session page (which has no channel) reads it too. ``asked_goal``: the goal the app's
    newest send carried, shown ``with_task`` until the agent holds one."""
    if controller is None:
        return {"turn": "idle", "conversation": None, "queued": 0}
    from . import edit_tool_hold
    from .approval_wait import awaiting
    call = awaiting(controller)
    if call is not None and not _shown(controller, call):
        call = None   # Its card's row is not in the transcript yet: the state and the rows an app reads agree.
    running = controller.turn_running()
    if call is not None:
        turn = "waiting_for_approval"
    elif running and controller.cancelled:
        turn = "stopping"
    elif running and edit_tool_hold.running():
        turn = "waiting_for_person"
    else:
        turn = "running" if running or controller.pending else "idle"
    inbox = getattr(controller, "inbox", None)
    items = inbox.items() if hasattr(inbox, "items") else list(inbox or ())
    from .steering import PAUSED
    paused = [item for item in items if getattr(item, "phase", None) == PAUSED]   # Never sent by itself: not queued.
    state = {"turn": turn, "conversation": controller.state["id"], "queued": len(items) - len(paused),
             "connection": status.route(controller)}
    if paused:
        state["not_sent"] = {"count": len(paused), "why": not_sent_why(item.reason for item in paused),
                             "next": NOT_SENT_NEXT}
    if call is not None:
        state["approval"] = {"tool": call.name}
        state["needs"] = WAITS_FOR_PERSON.format(what="their approval or decline of " + call.name)
    elif turn == "waiting_for_person":
        state["needs"] = WAITS_FOR_PERSON.format(what="the tool they are using in the viewport")
    from . import headless_window_relay   # A hidden-window session: its caps, and its card relayed in full.
    headless_window_relay.apply(state, controller, call)
    from . import goal_controls, goal_loop, goal_state
    goal = goal_loop.goal_of(controller)
    if goal is not None:   # Held from the app's words: its budget and spend as the chip shows them.
        state["goal"] = goal_controls.summary(goal)
        if goal["status"] == goal_state.PROPOSED and "needs" not in state:
            state["needs"] = WAITS_FOR_PERSON.format(what="their Start of the proposed goal")
        elif goal["status"] == goal_state.PAUSED and "needs" not in state:
            state["needs"] = WAITS_FOR_PERSON.format(what="their Resume of the paused goal (answer the agent's "
                                                     "question with angelo_send first if it asked one)")
    elif asked_goal:   # The agent holds none yet: the app's own goal, as it sent it.
        state["goal"] = {"status": "with_task", "objective": asked_goal, "note": GOAL_WITH_TASK}
    return state


def _shown(controller, call, recent=64):
    """Whether ``call``'s activity row is in the transcript (its newest rows): only then does an app read it waiting."""
    messages = controller.state.get("messages", ())
    return any(message.get("call_id") == call.id for message in messages[-recent:])


def _end_page(identity, why):
    """The conversation's live session page ends with its session (``session_page``, when this build has it)."""
    try:
        from . import session_page
    except ImportError:
        return
    session_page.close(identity, why)


def waiting(controllers, active_id, chat_shown=True):
    """External subset of unseen cards; a hidden active chat is unseen too."""
    from .approval_wait import unseen
    return [(controller.external_client, controller.state["id"])
            for controller in unseen(controllers, active_id, chat_shown)
            if getattr(controller, "external_client", "")]
