.. SPDX-FileCopyrightText: 2026 Angelo Contributors
.. SPDX-License-Identifier: GPL-2.0-or-later

##########################
Wrapped operations' budget
##########################

A typed wrapped operation (the agent-verbs design, Part E) runs one reviewed operator on a staged
copy inside the agent's turn. Every wrapped operation shares one time budget for that turn, and
input that would take longer runs as a Background Job (2.1.0; coordinator ruling 2026-11-15, which
replaced the per-operation ``budget_s`` of Part E revision 3). ``wrapped_op_budget.py`` holds it.

Contract
========

* **One budget.** ``TURN_BUDGET_S`` is ``job_start_wait.MAX_HOLD`` (10 s), imported rather than
  copied. That is how long a turn already waits on a job before handing it back, so a wrapped
  operation never holds a turn longer than a job may. Changing one changes both. A declaration
  that carries its own ``budget_s`` is refused, and a host test scans every ``WRAPPED_OPS`` in the
  add-on for one.
* **A measured bound.** Each declaration names ``input_bound``, the measures its time grows with
  (``{"triangles": N}``, ``{"faces": N}``), as positive whole counts. ``bound_from_timings`` sets it
  from timed runs:

  * Each size gets at least ``MIN_TRIALS`` (20) timed trials of stage plus run, after one untimed
    warm-up.
  * Every size is measured on Mac 1 and on the Windows VM.
  * The bound is the largest size whose nearest-rank p95 is within the budget on both machines.
  * Sizes above the first size that misses never count, and nothing is extrapolated.

  A constant-cost operation declares ``input_bound=None``, and its measured p95 must still fit.
  Report the returned ``p95`` table with the operation's hand-off.
* **Past the bound, a job.** ``admit`` runs before staging. If any measure is over its bound, it
  refuses with the correctable ``LIMIT_EXCEEDED`` and nothing runs (``scene_changed: false``). The
  refusal's details are ``{"limit": "turn_budget", "budget_s", "bound", "measured", "job_kind",
  "levers"}``.

  * With a ``job_kind``, the words tell the agent to start that job (``angelo_job_start``), which
    does the same work in the background under the job contract (cancellable, progress, its own
    approval). ``job_kind`` must be a member of ``job_spec.KINDS``, checked when the operation is
    registered.
  * Without one, the words name the ``levers``, the parameters whose values shrink the input, so
    the agent can offer a smaller call. If the bound is smaller than common real inputs, that is a
    gap: the fix is to add a job kind, never to raise the bound past what was measured.
* **Calls that are always a job.** ``job_if = (parameter, {values})`` names values of one enum
  parameter whose time does not follow the input's size, so no ``input_bound`` can separate the
  calls that fit from those that do not: a slow method, not a large input. The test is that the
  measurements contain, at or below the smallest size that fits, an input that is over the budget.
  The case behind it is UV layout's Quality (Concave) packer on the Windows VM. Box kits of 4 and
  64 parts took 4–9 s, but 8 boxes took 34–74 s and 10 plates took 19–50 s. ``admit`` refuses
  those calls the same way before it reads the bound, with ``details.job_if`` set to
  ``{parameter: value}``. An omitted parameter counts as its default, so the parameter needs a
  default or must be required: otherwise the operator's own default would decide. ``job_if`` needs a
  ``job_kind``, and the values must come from that parameter's ``enum``. The timings that justify
  it go with the hand-off, like a bound's. A row whose fastest trial is over the budget has a p95
  over it too, so a few trials suffice to show a miss. Only a fit needs ``MIN_TRIALS``.

Registration
============

Each lane declares its operations as plain data, ``WRAPPED_OPS`` in its own module, and adds the
module's name to ``wrapped_op_modules.MODULES``. A host test fails when a module declares
``WRAPPED_OPS`` and is not listed, so no declaration ships unchecked. ``wrapped_ops.registry()``
imports the listed modules once and registers every declaration. ``register`` refuses one that
breaks the design's Part E, naming the kind and the rule:

* **Fields.** The required fields (``kind``, ``operator``, ``source``, ``target``, ``mode``,
  ``parameters``, ``map``, ``reads_only``, ``creates``, ``mutates``, ``input_bound``, ``checks``,
  ``guide``) are all present, and nothing outside them and the optional ``new_ids``,
  ``creates_if``, ``receipt``, ``job_kind``, ``job_if`` and ``levers`` is accepted.
* **Identity.** The kind is a lowercase name that is not already a typed kind and is declared once.
  The operator is an idname (``module.name``). The source is ``angelo`` or
  ``bundled:<extension id>``. The target is one of Blender's object types or ``None``. The mode is
  a context mode (``OBJECT``, ``EDIT_MESH``, ``POSE``…). New ID types are Blender's, other than
  ``OBJECT``, which only ``creates`` may add. These names are pinned to Blender's RNA source.
* **Writes.** ``mutates`` names only families the program transaction journals
  (``action_schemas._mutations``). A read-only operation writes, creates and adds nothing. A
  mutating one names at least one of them. ``creates`` is one of the pattern's sets, and
  ``creates_if`` ties one of its members to values of an enum parameter.
* **Parameters.** The parameters are a closed JSON Schema object (``additionalProperties`` false,
  plain JSON), and ``map`` sends each parameter to its own operator property. ``checks`` and
  ``receipt`` are functions, and ``guide`` is a feature-guide id.
* **The budget.** ``wrapped_op_budget.check_declaration``, with the host's job kinds.

A registered declaration is a deep read-only copy (``read_only.frozen``: its parameters, their
enums and defaults, the map, ``job_if``'s values and every other mapping, list or set; functions
are kept as declared). The add-on builds the registry as it registers, before any agent script
runs, so a script that edits a declaring module's data in place changes nothing the registry or
``admit`` reads (the uv-pack lock-bypass review, S1).
What a model is sent about an operation comes from ``wrapped_ops.model_schema(kind)``: its registered
parameters as plain JSON (``read_only.thawed``), a fresh copy each call.

``wrapped_ops.admit(kind, measured, parameters)`` applies the budget to a call by its registered
kind, before anything is staged.

The runner is not built yet: staging, running the operator, the snapshots, checks and receipt, and
the deferred program kind the agent sees. Until it lands, no wrapped operation reaches the agent;
when it does, it registers through ``registry()`` and calls ``admit`` before staging.

Tests: ``tests/python/angelo_agent/test_native_wrapped_op_budget.py`` and
``tests/python/angelo_agent/test_native_wrapped_ops.py``.
