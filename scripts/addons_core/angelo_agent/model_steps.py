# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a request's model steps end, and which step wrote a call (the recovery hold's clock, request_recovery).

A recovery hold admits a scene edit only from a model step after the one that completed its reads
(``RecoveryBarrier.check``). ``ModelSteps`` keeps that count for one request: Codex usage reports and turn ends,
Claude rounds, and the bundled runtime's numbering of a turn's model requests (``turn/modelRequestStarted`` and
``item/tool/call``'s ``modelStep``), so a Codex call is judged by the step that wrote it (a yielded exec cell's later
call keeps its cell's step). ``ContextStepHooks`` are the lead request context's entries into it; collaborator and
voice contexts have none or are refused. Memory-only, cleared with the request (``ModelSteps.clear_steps``).
"""

# Codex turns of one request whose numbering the barrier keeps (``RecoveryBarrier.call_step``); a turn past them is
# never numbered (its calls count in the step they are checked in), and none is forgotten within the request.
MAX_TURNS = 256


def record_model_step(context, turn=None, *, ended=False):
    """A model response of the lead request's Codex turn ``turn`` ended (``ended``: the turn did):
    ``RecoveryBarrier.step`` (never a collaborator's context)."""
    if not context.cancelled:
        context.host.recovery_barrier.step(turn, ended=ended)


def record_request_started(context, turn, request):
    """The lead request's Codex turn ``turn`` sends its model request ``request`` (``turn/modelRequestStarted``):
    ``RecoveryBarrier.request_started``. Never a collaborator's context or a voice thread's."""
    if not context.cancelled and not getattr(context, "voice", None):
        context.host.recovery_barrier.request_started(turn, request)


def record_call_step(context, params):
    """The step that wrote one of the lead request's Codex calls, from its ``item/tool/call`` parameters.

    The bundled runtime names, in ``modelStep``, the model request of the turn that wrote the call (for a call an
    exec cell made, the request that wrote the cell: ``writtenIn``), the turn's latest request (``current``) and the
    cell (``execCellId``). None when it names none (a runtime without the field, a call outside a numbered request,
    a malformed value): the barrier then counts the call in the step it is checked in. Never a collaborator's
    context or a voice thread's, whose turns are not the lead request's.
    """
    step = params.get("modelStep") if isinstance(params, dict) else None
    if context.cancelled or getattr(context, "voice", None) or not isinstance(step, dict):
        return None
    return context.host.recovery_barrier.call_step(params.get("turnId"), step.get("writtenIn"), step.get("current"))


class ContextStepHooks:
    """The lead request context's hooks into its host's step count (``routing.RuntimeContext``)."""

    record_model_step = record_model_step  # A Codex model response ended (provider_usage).
    record_call_step = record_call_step  # The step that wrote a Codex call (codex_delivery).
    record_request_started = record_request_started  # turn/modelRequestStarted (provider_usage).


class ModelSteps:
    """A request's model-step count. Its owner (``RecoveryBarrier``) provides ``self._lock``, held around every read
    of ``steps``, and calls ``clear_steps`` when the request starts."""

    def clear_steps(self):
        with self._lock:
            self.steps = 0  # Model steps of this request (``step``).
            self.turns = {}  # Codex turn id -> the step count its request 0 starts at (``call_step``).
            self.announced = set()  # Turns whose runtime announces each model request (``request_started``).

    def _turn_start(self, turn):
        """The step count the Codex turn ``turn``'s first model request starts at, fixed when first heard of; None
        for a turn past ``MAX_TURNS``, which is never numbered."""
        start = self.turns.get(turn)
        if start is None and len(self.turns) < MAX_TURNS:
            start = self.turns[turn] = self.steps
        return start

    def step(self, turn=None, *, ended=False):
        """A model step ended: a later call was written by a model that had this step's results.

        Codex: each ``thread/tokenUsage/updated`` of the lead request's turn (``provider_usage``), which Codex
        sends only after every tool call of that response, an exec cell's included, has resolved or yielded, and
        each turn end (``ended``, also ``provider_usage``). Claude: each round (``Controller.record_model_round``),
        submitted after the previous round's plan ran and before its own plan runs. ``turn`` (Codex) fixes where
        that turn's numbering starts (``call_step``) when this is the first the barrier hears of it. A usage report
        of a turn whose runtime announces its requests (``request_started``) ends nothing: the announcement of the
        next request does, and Codex also reports usage for work that is no model request (a compaction).
        """
        with self._lock:
            if isinstance(turn, str):
                self._turn_start(turn)
                if turn in self.announced and not ended:
                    return
            self.steps += 1

    def request_started(self, turn, request):
        """The bundled runtime announced that Codex turn ``turn`` sends its model request ``request``
        (``turn/modelRequestStarted``, numbered as ``call_step`` numbers them), before that request's input is
        built: every step before it has ended, whether or not its usage was reported, and a result that arrives
        after it can only be seen by a later request. From here the turn's usage reports end no step (``step``).
        """
        if not isinstance(turn, str) or type(request) is not int or request < 0:
            return
        with self._lock:
            start = self._turn_start(turn)
            if start is None:
                return
            self.announced.add(turn)
            self.steps = max(self.steps, start + request)

    def call_step(self, turn, written_in, current):
        """The step a Codex call was written in, from the bundled runtime's numbering of its turn's model requests.

        The runtime numbers a turn's model requests from 0 and names, for each dynamic call, the request that wrote
        it (``written_in``: for a call an exec cell made, the request that wrote the cell, even after the cell
        yielded) and the turn's latest (``current``). Request ``n`` of a turn is step ``start + n``, where ``start``
        is the step count when the barrier first heard of the turn (its first announcement, call or usage report).
        The call also tells that requests up to ``current`` have begun, so the step count moves up to
        ``start + current``. None for values the runtime would never send, and for a turn past ``MAX_TURNS``.
        """
        if (not isinstance(turn, str) or type(written_in) is not int or type(current) is not int
                or not 0 <= written_in <= current):
            return None
        with self._lock:
            start = self._turn_start(turn)
            if start is None:
                return None
            self.steps = max(self.steps, start + current)
            return start + written_in
