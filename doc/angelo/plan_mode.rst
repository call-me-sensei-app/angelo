..
   SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
   SPDX-License-Identifier: GPL-2.0-or-later

#########
Plan mode
#########

Status: 2.1.0, ``feature/v2.1.0-plan-first``. The safety-critical parts get an independent break-it review
(`Validation`_).

Plan mode is a chat's deliberate "plan first" step. The person asks the agent to research, look at the scene and lay
out approaches before anything is built; the agent ends the planning turn with a structured plan (two or three
approaches, each with steps, risks and an estimate, a recommendation and its open questions), which Angelo draws as a
native card. The person picks an approach (or edits it), and one press, **Go with this**, switches the chat to the
approval setting they chose and sends the chosen plan as the next turn's instruction. While planning, nothing in the
scene can change. It lives in the host, as goal mode does (`goal_mode.rst <goal_mode.rst>`_), so it works the same on
Claude and Codex.

.. contents::
   :local:


Why
===

Maintainer, 2026-11-18: "are users able to chat the same way like in Claude Code? For example, asking the agent to go
do research and discuss approaches, etc, first" → "Yes, add to 2.1.0".

In 2.0.0 a person can chat, the agent asks clarifying questions in plain chat, it has web search (WebSearch/WebFetch on
Claude, Codex's native web search on OpenAI), and *Read only* rejects every mutation. Three things are missing:

* **A deliberate flow.** Nothing says "this turn plans, the next one builds". A person who asks to discuss first must
  remember to switch to Read only, then back, and re-type the approach they liked.
* **A shape for the plan.** Approaches come back as prose, so comparing them, choosing one and editing it is reading
  and re-typing.
* **One press to build.** Going from the chosen approach to building with the right approval setting is three manual
  steps (approval chip, copy, send).

Claude Code's own planning and user-input tools are deliberately outside Angelo's host contract (``claude_text``:
"Native planning, user-input, goal, file and subagent tools are outside this host contract"), and Codex has no
counterpart with Angelo's approvals, so neither runtime's own plan mode can be used: the flow is the host's.


Principles
==========

* **Read only is the enforcement.** A planning chat *is* a Read only chat with one more flag. Every refusal is the
  existing Read only refusal at the existing sites; there is no second, weaker copy of the rule.
* **The person decides; the model proposes.** The agent may start planning (it only narrows), never end it. Only the
  person's own press on the card leaves planning, and only to the approval setting the card shows.
* **The press is the person's.** Go is a grant operator: it takes effect only in the C grant scope of the person's
  ticketed press (`grant_operators.rst <grant_operators.rst>`_). An agent script, an add-on, a keymap entry or
  Angelo's own automatic decision cannot press it.
* **Same on every runtime, cheap when unused.** One deferred tool, ``angelo_plan``; one instruction clause; one small
  state field only while planning.
* **History, not hidden state.** The plan is the plan tool call's row in the chat; the person's choice is their
  request row. Both are in the chat file, so a plan survives restarts and reads as what it was.


Flow
====

1. **Start.** Either the + menu's **Plan first** row (``chat_destinations`` feature ``plan``, beside Attach and Goal),
   or the person's words ("let's plan first", in any language: recognising it is the model's), on which the model
   calls ``angelo_plan`` ``START``. Both run ``planning_controls.start``: the chat becomes Read only with
   ``planning`` set, and the approval it had (Auto only as C holds it for the chat, else Ask) is kept as the plan's
   *build setting*. Pending mutations are refused as when the person picks Read only (``Controller.set_options``).
   A row in the chat says *Planning: this chat's agent researches and proposes approaches. It changes nothing until
   you press Go with this.*, after a row naming a job of the chat that is already running (it finishes unless the
   person cancels it). Planning is per chat: other chats and connected apps keep their own settings and go on.
2. **Plan.** The agent researches (web search, scene reads, ``angelo_view``, delegated read-only research), asks
   questions in plain chat as needed, and ends the turn with ``angelo_plan`` ``PROPOSE``. The turn may end without a
   plan (a question to the person); planning stays on.
3. **Choose.** The card under the ``PROPOSE`` row shows the plan. Per approach: **Go with this · Ask** (or **· Auto**,
   the setting it switches to) and **Edit**. Edit
   copies the approach into the message box, where the person edits it with the composer's own editor (multi-line,
   input methods, right to left). Whatever the message box holds when Go is pressed goes with the chosen approach as
   the person's edits and notes. **Then:** a two-segment control, *Ask before changes* / *Auto · bounded edits*,
   preselects the build setting; the person may change it.
4. **Go.** One press: the chat leaves planning in the setting shown, and the chosen approach (with the person's notes)
   is sent as the person's request. Their row reads *Going with: <approach>* in their language; the model gets the
   approach as facts (`Contract`_).
5. **Discuss or revise.** While planning, an ordinary message is discussion: the agent answers or proposes again; a
   new ``PROPOSE`` supersedes the earlier card (its buttons become *Superseded*). After Go, the composer shows the
   plan chip *Plan · <approach>*; its click scrolls to the card, and its **Revise plan** returns the chat to planning
   (the build setting is the one it is in then) so the person's next message revises the plan.
6. **Leave without building.** The planning chip's close (*Stop planning*) ends planning in Ask without sending
   anything (never the build setting: that is Python state, and this press shows no setting; break-it review M1).
   Auto is then the person's own choice in the approval control. Choosing Ask or Auto in the chat's own approval
   control also ends planning (a Read only chat that is no longer Read only is not planning); choosing Read only there
   keeps it planning.


Contract
========

State
-----

``options.planning`` (bool) and ``options.plan_return`` (``"ASK"`` or ``"AUTO_BOUNDED"``), kept by
``chat_settings.stored_options``. ``planning`` holds only with ``approval == "READ_ONLY"``: any other approval reads as
not planning, so the flag can never weaken Read only and a chat is never "planning" in a mode that mutates. The chat
file already stores ``options``; a build from before 2.1.0 drops the two keys and reads the chat as Read only, the
restrictive reading. After a restart planning resumes as it was (it is Read only; nothing runs by itself).

``options_widen`` (the guard on ``Controller.set_options``) reads ``planning: True`` as narrowing and
``planning: False`` as widening, and ``plan_return`` as narrowing only when it is ``"ASK"``: a script can start
planning or lower the build setting, never end planning or raise it.

**A widening press never reads its target from Python state** (break-it review M1). ``plan_return`` only preselects the
card's *Then* control; the approval Go applies is the one its button names (*Go with this · Auto*) and its ticket's
digest covers, so a changed ``plan_return`` can change what the person sees before they press, never what a press does
unseen. Stop planning shows no setting, so it ends planning in Ask, which narrows from Read only's C-held Ask.

The tool
--------

``angelo_plan`` (``planning_spec``; validated by ``tool_specs.validate_call``; ``readOnlyHint``, so Read only admits
it). Actions:

``{"action": "START"}``
   Starts planning in this chat. Refused in a request Angelo made (a goal round, the worker-done event), a voice turn,
   a worker's chat, and a connected app's conversation, whose tool list leaves it out (``NEVER_FOR_APPS``). Already
   planning: a result saying so (no error).
``{"action": "PROPOSE", "summary": str, "approaches": [Approach], "recommended": int, "questions"?: [str]}``
   Only while planning (else ``NOT_PLANNING``). ``Approach`` is ``{"title": str, "steps": [str], "risks"?: [str],
   "estimate": str}``: 1-3 approaches, 1-12 steps, at most 6 risks, ``recommended`` an index into ``approaches``, at
   most 5 questions. Lengths: summary 600, title 80, step, risk and question 240, estimate 80 characters. Control,
   and hidden characters are refused: ``hidden_text``, the agent add-on's one check for text a person cannot see,
   decides by Unicode property (controls, format, private-use and unassigned characters, every Default_Ignorable code
   point: variation and tag characters, the soft hyphen, the Hangul fillers) and allows what real writing needs in its
   context (Thai, Lao, Khmer and Myanmar word breaks; joiners in joining and Indic scripts and emoji sequences; bidi
   marks in right-to-left text; an emoji's variation selector). Every one of the 20 catalogues reads as visible to it. The result is
   ``{"status": "SHOWN", "plan": <the validated plan>, "note": "The person chooses on the card; end this turn."}``:
   the activity row stores it, so the plan is in the chat history and drawn from there.

There is no action that ends planning or presses Go.

What the model sees
-------------------

* The instructions' clause, both runtimes (``agent_guidance.PLAN_SENTENCE``): *angelo_plan: plan mode, when the
  person asks to plan or discuss first.*
* While planning, the observation's state field ``plan_mode: "PLANNING"`` (``planning_controls.observed_id``).
  The tool's deferred description says what planning means: read only, research and inspect, end with ``PROPOSE``.
* Go's request (model-facing, English, facts only; the person's notes and the plan's own text quoted as data)::

     The person chose approach 2 of the plan (angelo_plan call <id>) and pressed Go; approvals are now Auto · bounded.
     Build it.
     Approach: <title>
     Steps:
     1. ...
     Risks: ...
     The person's edits and notes (their words): <message box text, or "none">

  When the person pressed Edit on this plan before Go, the last line says so instead: *The person's edits (an approach
  of this plan copied into their message box with Edit, then changed) and notes*, since much of it is the model's.

  The user row carries ``plan_choice`` (``{call_id, approach, title, approval, notes, edited}``;
  ``request_rows.kept_fields``),
  so it is drawn as *Going with approach 2: <title>* and *Your changes: <notes>* in the person's language
  (``console.display_text``, ``planning_text.row_display``), and the card knows it was chosen. The row is the person's
  request (``persons_send``): it takes the message box's attachments, can start a chat-bar goal chosen in the composer,
  and pauses an active one as any message does. **Its text is not the person's words** (break-it review S2): every
  reader of those (memory evidence ``memory_host``, a worker's brief ``worker_request.taken``, search
  ``conversation_search``, a goal's objective ``planning_state.goal_objective``) reads ``request_rows.persons_text``:
  the notes only, none when they began with Edit; a goal's objective falls back to *Go with approach N: <title>*.
  Provenance travels with the text on every copy path (round 2, N2): a Go sent again (Resend after a failed turn)
  finds its choice in the chat's history by its exact text (``planning_state.choice_for_text``); *Put message back*
  and an unsent Go put back only the notes (``planning_state.draft_for``), with Edit's mark when they began with Edit.

  **Edit's mark lives on the draft** (N2b): ``options.plan_draft`` (the copied plan and its lines, saved with the chat,
  so it survives a restart). A send whose text still holds a copied line is the model's words (``from_plan`` on its
  row, which ``persons_text`` reads as none); a draft emptied and retyped is the person's. Every send clears the mark;
  Go labels its notes from it.

Go
--

``angelo.plan_go`` (``planning_ui``), a grant operator (``grant_guard.guard``, never ``auto``), runs
``planning_controls.go(controller, call_id, approach, approval)``, itself a grant entry (``grant_guard.entry``: refused
inside agent code) that also requires C to admit ``angelo.plan_go`` in the current scope. ``go`` refuses, with a notice
in the chat, when: the chat is not planning; ``call_id`` is not the chat's newest ``PROPOSE``; the approach index is
out of range; ``approval`` is not Ask or Auto (or Auto while ``RequireAskForEdits``, which reads as Ask); a turn is
running (the plan card appears at a turn's end, so this is rare: *wait or Stop*); the person's own tool is in use
(``edit_tool_hold``: their send would wait in the tray without its choice). Otherwise it sets the approval with
``planning`` off (``Controller.set_options`` in the press's scope, so Auto is held in C as the person's choice), then
sends the request through ``console.send_message`` (the person's send path). If the send is refused, planning is
restored (narrowing) and the refusal is said.

Revise and Stop planning
------------------------

``angelo.plan_stop`` (the planning chip's close) ends planning in Ask. It stays a grant operator and a grant entry
(ending planning is the person's alone; ``options_widen`` reads ``planning: False`` as widening), and its control is
shielded as Go's is. **Revise plan** only narrows (``start``), so it needs no grant, but it can end a goal
(`Goals`_) and appears in the frame Go may start one: its control is shielded too, and its click acts only in the
scope of the person's own armed press (``planning_card.dispatch``; break-it review of e327003b8c2, nit 5).

Goals
-----

Read only holds no goal (goal_mode.rst), so planning would end one, and a goal is the person's: an agent's ``START``
is refused while the chat holds a goal (active, paused or proposed) or has one chosen for its next message, with a
message saying the person can start planning from the + menu. Only the person's own press (the + menu's Plan first,
or Revise plan) ends the goal to plan (``goal_controls.stop``, as the chip's Stop goal), and the notice row and the
Planning chip say the goal ended (coordinator's ruling, 2026-11-18). A goal can follow a plan: the person chooses *Keep going until it's done* in the + menu before Go, and Go's send (the
person's) starts it with the request as its objective, as any Send of a chosen goal does. Go never chooses a goal by
itself.


The card and chips
==================

The plan card is drawn below the ``PROPOSE`` activity row on the chat canvas (``chat_cards``), with the kit's tokens
and controls (`ui_kit.rst <ui_kit.rst>`_): card_kit's navy-charcoal panel, a teal *Recommended* chip on the
recommended approach, each approach a section with its title, numbered steps, risks (amber dot) and estimate (muted);
the summary above, open questions below. Each approach has **Go with this · Ask** or **· Auto** (teal primary,
naming the setting it switches to) and **Edit**
(secondary). The footer holds *Then:* and its segmented control, and one line: *Changes you type in the message box go
with your choice. Send a message to discuss or revise.* Approaches longer than the canvas width wrap; a narrow chat
stacks the buttons. States: live (planning, newest plan), chosen (*You went with approach 2*, buttons gone), superseded
(*A newer plan replaced this one*), and inactive (planning ended without a choice: *Planning ended*).

Chips in the composer's chip row (beside the goal chip): *Planning · read only* while planning (close: Stop planning);
after Go, *Plan · <approach>* (click: scroll to the card; *Revise plan*). The approval control's label reads
*Planning* while planning.

Every string is translatable (``ui_text.n_``/``iface_``; `localization.rst <localization.rst>`_), checked in all 21
languages, right to left, and at two chat widths. The card meets the QA-target rules: no stock Blender look, on-brand.


Safety
======

* **No mutation while planning.** Planning is Read only (`Contract`_), so the existing refusals apply at every site:
  dispatch (``Controller`` tool pump), ``execute_tool``, ``approve_tool``, retained previews, typed commits
  (``program_commit_controller``), memory updates, owned files, storage, render extensions and connected apps. Starting
  planning refuses queued mutations as a switch to Read only does. ``angelo_plan`` is the only new tool and changes
  nothing in the scene. Research is unchanged: web search, scene reads, views, delegation (read-only collaborators).
* **Work admitted earlier is cancelled, never held** (break-it reviews S1 of 0a05589aebe; S1, S2 and S3 of
  e327003b8c2; S1-S3 and NIT 2 of 55525904e80). As planning starts, on every route into it (``controller_entries``
  wraps ``set_options``, in a ``finally`` keyed on the planning state, so a chat save that fails still cancels), the
  chat's queued background jobs that the agent's calls admitted and that change things (bakes, renders, exports:
  ``job_manager``) are cancelled (``planning_controls.cancel_waiting_jobs``): each job's own row in the chat reads
  *Cancelled: its chat started planning*, and the agent reads their IDs in ``START``'s result and again in Go's
  request, to queue them again if the chosen approach needs them. Nothing waits for a press, so no press that ends
  planning starts work admitted under an earlier setting (Stop planning and Go · Ask do only what they say), and
  nothing waits on the chat's Python state or its file (a deleted chat's queued job runs or ends as it did before plan
  mode). Planning restricts the agent, not the person: their own queued presses (a Bake Maps card's Bake, Make Video
  from This Shot's renders, Render the missing frames; no agent call) run, as their F12 does. A look the agent asks
  for while planning (``RENDER_PREVIEW``, read only) runs. A job already running goes on, named in the chat as
  planning starts, and so does work outside Angelo's queues that was set up before planning (a Media Studio
  generation already submitted to its provider, an earlier script's timers, handlers and drivers): planning stops
  this chat's agent's new changes, not what is already under way. Planning is per chat: other chats and connected
  apps on the same document keep their own settings.
* **Who can end it.** Ending planning widens (``options_widen``), so ``set_options`` refuses it inside agent code; Go
  and Stop planning are grant operators; the model has no action for it. Ask through the approval control
  (``angelo.set_approval``) or ``angelo.narrow`` narrows, so any caller but agent code may end planning that way: a
  keymap, an add-on, a timer. Python in the process can also write the chat's state directly: an add-on, or an agent
  script that reaches Angelo's own modules (`script_confinement.rst <script_confinement.rst>`_), such as one still
  running when planning starts, a timer an earlier script left, or a script in another chat. None of these starts
  work planning cancelled, and none reaches more than Ask (*What C enforces*, below).
* **The press can't be scripted.** Go runs only in the C scope of the person's ticketed press on the canvas, whose
  digest covers ``(call_id, approach, approval)``: a different approach or setting is a different ticket. It is never
  listed for automatic scopes, refused while an agent script runs and in background mode, and its function refuses
  inside agent code and outside a scope that admits it. A host test pins each.
* **Data, not instructions.** The plan's text is the model's; the person's notes are theirs. Both are quoted as data
  in Go's request (each a JSON string) and drawn as text, never as markup that acts.
* **A card that just appeared.** ``plan_go``, ``plan_chip_stop`` and ``plan_chip_revise`` are shielded actions (``grant_guard.SHIELDED``): a
  press that lands on one the moment it appears is not taken, as for Approve.
* **What C enforces.** Planning holds exactly Read only's guarantee, no more: the mode is the chat's Python state, and
  C holds Ask for a Read only chat, planning included (``approval_modes``; C holds no Read only). The guarantee that
  holds against any Python in the process is C's: after planning ends, by any route, each edit needs the person's own
  Approve, unless the person pressed Go · Auto (its ticket holds Auto in C as theirs). The Python refusals (Read only
  at every site above, ``set_options`` inside agent code) hold against the agent's tools and calls, not against Python
  that writes the chat's state in place. Planning's only effect that trusted Python state, a hold on queued jobs that
  a press released, is gone: they are cancelled as planning starts. Holding Read only in C, and agent scripts in a
  process of their own, would close the rest; both are outside this feature.


Budgets
=======

Every always-sent schema byte is paid on every turn, so:

* ``angelo_plan`` is a *deferred* tool on both runtimes (``plan_kinds.DEFERRED_TOOLS``), as ``angelo_goal`` is; an
  ordinary request pays its name in the deferred list and one instruction clause (``agent_guidance.PLAN_SENTENCE``:
  *angelo_plan: plan mode, when the person asks to plan or discuss first.*). Measured on the merged tree over toolset 58 (UV
  layout) with ``coverage_prefix.py`` (compact JSON), toolset 59; the deltas approved by the coordinator:

  ========================================  ======================  =======
  What every request carries                Bytes, before -> after  Change
  ========================================  ======================  =======
  Claude plan schema (parallel edit ON)     57,334 -> 57,348        +14
  Claude instructions                       14,734 -> 14,805        +71
  Codex instructions (plan, team)           26,525 -> 26,609        +84
  Codex code-mode tools (parallel edit ON)  111,868 -> 113,185      +1,317
  Every tool in full (voice)                109,740 -> 109,740      0
  ========================================  ======================  =======

  For comparison, ``angelo_goal`` (toolset 51): +14, +39, +52 and +1,572 B. The code-mode figure is the deferred
  declaration in the ``more`` namespace (1,360 characters; Codex code mode's budget is about 2,000 per tool). Budget
  pins moved with these numbers: ``PLAN_BUDGET_BYTES`` 57,340 -> 57,350 and the Codex instructions' 26,525/26,554
  -> 26,609/26,638 (``test_native_per_model_text``, ``test_native_codex_launch``). The toolset number is set at the
  merge (v210-features).
* While planning, the ``plan_mode`` state field (about 25 bytes in a planning request's observation).
* A planning turn costs what the research costs; Go's request is one ordinary request.
* A chat that never plans: nothing else changes.


Scope
=====

New modules: ``planning_spec`` (the tool, no bpy), ``planning_state`` (the plan, the choice and Go's request read
from history, no bpy), ``planning_text`` (every interface string), ``planning_controls`` (the tool's execution,
start, revise, Go, Stop planning, the observed field), ``planning_card`` (the card's pure layout, its drawing and
clicks, the chip) and ``planning_ui`` (the two grant operators). Touched owners: ``chat_settings`` (the options and
``options_widen``), ``controller_entries`` (ending planning refused inside agent code), ``request_rows``
(``plan_choice``), ``console`` (the Go row), ``chat_cards`` (the card under its row), ``chat_view`` (``plan_*``
clicks routed with the other cards', no lines added), ``chat_composer`` (the chip and the *Planning* label),
``chat_destinations``, ``chat_add_nav``, ``chat_add_menu`` and ``chat_add_draw`` (the + menu row and its glyph),
``chat_titles`` (the row's title), ``grant_guard`` (``SHIELDED``), ``tool_specs``, ``plan_kinds``,
``external_clients``, ``blender_tools``, ``turn_context``, ``agent_guidance``, ``claude_text`` and ``codex_text``,
``routing.TOOLSET_VERSION`` (58; set at the merge), ``feature_guide`` and ``features.rst``. The pinned modules (``controller``, ``ui``,
``chat_view``) gain no lines.


Validation
==========

* **Host tests, failing first**, each touched file alone and then the full suite in one process: options (planning only
  in Read only; widening rules); the tool's validation and declared size; ``START`` refusals; ``PROPOSE`` only while
  planning; Go's refusals (not planning, stale plan, bad approach, bad setting, a turn running, inside agent code,
  outside a scope); Go's request text (facts, data quoted) and row fields; superseded and chosen states; the observed
  field; the + menu registration, its fit test and the feature guide; every mutation site refuses while planning;
  ``angelo_plan`` deferred on both runtimes and left out for apps and voice.
* **GUI suite** in the running app (``test_blender_plan_gui``, run through ``run_native_suites.py``, Director Mode):
  the + menu row starts planning; a stand-in runtime's ``PROPOSE`` draws the card; a scripted call of ``angelo.plan_go``
  and of ``planning_controls.go`` is refused; the person's press (the harness's real click) leaves planning in the
  setting shown and sends the request; the chip and its Revise; screenshots in English, Japanese and Arabic.
* **Live check** on Opus 5.5 and GPT 6.2 Sol with the bench accounts the kit names (no new sign-in): "let's plan first"
  in English and one other language starts planning, the turn ends with a plan, a Go builds it; a held-out brief that
  must not plan does not call the tool.
* **Break-it review** of the two safety claims (no mutation while planning; the press can't be scripted), arranged by
  the coordinator.
  The live check runs on the 2.1.0 integration installer on the gate machines, alongside the candidate's other checks
  (coordinator, 2026-11-18); its script is in the lane's hand-over notes.


Review findings
===============

The break-it review of 0a05589aebe (2026-11-18) failed it on two MUSTs; each finding is now a failing-first host test
(``test_native_plan_controls``, ``test_native_plan_sites``, ``test_native_plan_spec``, ``test_native_plan_card``):

* **M1** Stop planning widened to ``plan_return``, read from Python state and shown nowhere: a script's in-place write
  turned an Ask chat into C-held Auto on the person's dismiss. Stop now ends in Ask; Go's button names the setting its
  digest covers. Class rule: a widening press never reads its target from Python state.
* **M2** An agent's ``START`` ended the person's goal (Read only holds none): refused while a goal is held (`Goals`_).
* **S1** Work admitted before planning still ran: the chat's queued jobs now wait for Go; what is already running is
  stated (`Safety`_). **S2** Go's row passed the model's plan off as the person's words: readers take
  ``persons_text``; invisible characters are refused; notes from Edit are labelled. **S3** catalogues (all 20).
  **S4** tests at the real dispatch, approve, execute, commit and preview sites, and Stop from Ask.
* Nits: ``plan_chip_stop`` shielded; Go refuses while the person's tool runs (a queued send lost its choice); the
  clause quoted here matches ``PLAN_SENTENCE``; unused constants removed.

Its round-2 re-read of 5cc731c13aa passed (no MUST) and asked, now done with failing-first tests: **N1** held jobs
started when the planning chat was deleted (the hold reads saved options and holds closed; deletion cancels them);
**N2** Resend, Put message back and an unsent Go turned the model's plan into the person's words (provenance travels
with the text); **N2b** Edit's mark keyed by the plan, not the draft (now on the draft, kept across a restart);
**N3** the hidden-character list was wrong both ways (one helper by Unicode property, tested on all 20 catalogues);
nits: Stop's tooltip, Go's label wraps rather than losing its setting, the bar's search keeps its least width, and Go
reads and clears only the pressed chat's own draft.

Its round-3 review of e327003b8c2 passed (no MUST) and asked, now done with failing-first tests: **S1** a deleted chat
that was not planning kept its queued job until a restart (the hold read the chat's file, and a missing file held);
**S2** Stop planning and Go · Ask started jobs admitted under Auto while saying the chat asks before changes; **S3**
the safety text claimed more than C enforces. One change settles all three: as planning starts, the chat's queued
changes are cancelled and said, and nothing is held, so there is no hold to leak, nothing a press releases and no
release that trusts Python state (`Safety`_, ``test_native_plan_sites``, ``test_blender_plan_gui``); the safety text
now says what C enforces.
Nits: a refused start ends no goal (it narrows first), Revise plan is shielded and takes only the person's armed
press, the chip reads the chosen plan once per change of the rows, and a stale test comment.

Its delta re-read of 55525904e80 passed (no MUST) and asked, now done with failing-first tests: **S1** the person's
own queued jobs (no agent call) were cancelled with the agent's (only the agent's calls' jobs are now); **S2** the
chat's text promised that nothing in the scene changes (it now says this chat's agent changes nothing, names a job
already running, and the doc says planning is per chat); **S3** a chat save that failed inside ``set_options`` skipped
the cancel (now in a ``finally`` keyed on the state); **NIT 2** the agent is told which jobs planning cancelled.
