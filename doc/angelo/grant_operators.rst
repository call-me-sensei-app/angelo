Only the person grants
======================

Every Angelo operator or setting that grants, widens or consents to anything takes effect only from the
person's own click, never from a script, an add-on or a keymap entry (``grant_guard``). Found by the Copy setup
break-it review (2026-11-14, M1): any Python, an agent script running without review included, could call
``bpy.ops.angelo.external_app_copy_setup(app="other")`` or Connect and give an outside app access with every
default level.

The rules
---------

A guarded operator (``grant_guard.guard(...)`` at the end of the module that defines it, a statement rather than
a decorator so a class read from its source alone is just its body) runs its body only when all of these hold:

* **No agent script is running**, whichever tool runs it (``grant_guard.script_running``: see "One signal"
  below).
* **Not in background mode**, where no person is. The exception is an operator that also takes Angelo's own
  automatic decision (``auto``: Approve, applying an approved step), with an automatic token from the chat's Auto
  policy, a headless session or a worker (``grant_guard.automatic``).
* **The person's click**:

  * a fresh, single-use token that Angelo's own click paths mint as they run the operator: the AI Providers page
    (``providers_page_host.act``), the chat canvas (``chat_view.dispatch_action``), the chat bar's + menu
    (``chat_add_nav``), the welcome card, the connection sheet and the review card (``grant_guard.clicked``);
  * or an ``invoke`` that Blender runs for a click on one of its own buttons or menu items (LEFTMOUSE, Return, Space);
  * or an ``invoke`` that opens the operator's own dialog or file browser, where the person confirms or nothing
    happens.

  A direct ``bpy.ops`` call has none of these, so it is refused with a message, and nothing changes.
* A guarded call that passed may run other guarded operators (Approve applying its step) and change widening settings.

**Narrowing needs no click, but never comes from agent code's ledger writes.** Decline, Stop, Cancel a job, Ask
before changes, Read only, Fast off and the engine link off need no ticket.
What the ledger holds (approvals, approval modes, script trust, Always allow scripts, allowed script files) changes
only from the person, Angelo's own code or an add-on: C refuses an agent script's write to it in either direction
(the Fusion audit, 2026-11-15, G2), so a script can no more withdraw an approval than give one, and a narrowing that
writes the ledger fails as a whole when a script asks for it (``angelo.narrow`` reports it and changes nothing). An
operator that does both says when it widens (``widens``).

**Narrowing the person's accounts, sign-ins or consents is refused to agent code.** Team sign-out, cancelling a team
or native sign-in or a quick connect, removing a connection, disconnecting an outside app and forgetting its allowed
script files grant nothing, so the person and Angelo's own code use them without a ticket. Agent code changes none of
them, in either direction (the G2 ruling): they are ``grant_guard.ACCOUNT_OPERATORS``, which C refuses while a script
runs as it does the grant operators, and the functions behind them are entries.

**Settings that widen** (``grant_guard.setting``) change only from the Preferences editor's own checkbox or inside
a click path or a guarded operator, never while an agent script runs or in background mode. A refused change leaves
the setting as it was; narrowing is taken from the person and Angelo's own code, while agent code changes no
preference or keymap at all (the confinement refuses, in C, every write to Preferences, add-on preferences and key
configurations, and their operators; ``script_confinement.rst``). These are ``allow_local_cli``, ``helper_standing_consent``,
``reuse_codex_login``, ``codex_path`` and ``claude_path`` (a program Angelo runs). Always allow scripts keeps its
own guard (``trusted_scripts_ui.always_allow_set``).

Entry points
------------

What a guarded operator does is done by a function behind it, which a script
could call without the operator. That was the share-link break-it's class
finding (2026-11-14): a click and script check only in the operator's
``execute``, while ``publish`` checked nothing.

So each such function is wrapped too (``grant_guard.entry``, at the end of the
module that defines it, so every caller gets the guard). It refuses while agent
code runs on that thread (``grant_guard.inside_agent_code``), with
``grant_guard.Refused``, which a caller reports as itself and never as a
policy's or a file's refusal. A conditional one (``widens``) refuses only when
it widens:

* access turned on (``external_clients.write_access``);
* scripts trusted (``set_trusted_scripts``);
* Always allow scripts turned on (``always_allow.set_enabled``);
* a sign-in or a key (``NativeAuthentication.start``);
* a chat's options (``set_options``, ``chat_settings.options_widen``): Auto,
  Fast on, or a new model or effort widen; Ask, Read only, Fast off and an
  unchanged value narrow, as the operators that set them do.

Inside agent code, every argument the condition reads must be a plain built-in
value (``grant_guard.PLAIN``). It is read once, and the body gets that same
value, so a value that answers differently when read again cannot pass as
narrowing and then widen. A condition that raises refuses.

The entries:

* external access: ``AppRegistry.connect`` and ``set_levels``,
  ``external_copy.copy_setup``, ``external_setup.connect``;
* chat settings and approvals: ``Controller.set_options``, ``approve_tool`` and
  ``execute_tool``, also a worker's (``MirrorController``);
* connections and keys: ``ConnectionStore.add``, ``ui.choose_model_connection``,
  ``ui.prepare_controller``, ``saved_keys_ui.load``, ``EndpointSheet.submit``;
* sign-ins: ``quick_connect.start``, ``TeamAccount.begin_sign_in``,
  ``fab_ui.run_connection_request``;
* spending: ``flow_host.start_run``, ``studio_ops.start_job``,
  ``StudioLibrary.configure_enhancer``, ``enhance_native``, ``enhance_gemini``,
  ``ai_render.start``, ``shot_capture.start``;
* code from elsewhere: ``settings_import_ui._run_import``,
  ``engine_link_assets.install_plugin``, ``engine_link_ui.turn_on``,
  ``engine_setup.set_up``;
* accounts, sign-ins and consents, refused inside agent code whatever they do:
  ``TeamAccount.sign_out`` and ``cancel_sign_in``, ``NativeAuthentication.cancel``,
  ``quick_connect.cancel``, ``ConnectionStore.remove``, ``AppRegistry.disconnect``,
  and ``external_setup.disconnect``.

``test_native_grant_operators`` maps every audited operator to its entries
(``ENTRY_FOR``). It requires each mapped entry to be guarded and runs every
entry under ``agent_script``, so none runs.

Audit (2.0.0)
-------------

``test_native_grant_operators`` holds this list as ``AUDIT``, and requires the guarded operators to match it exactly.
It runs each one: a direct call, a script, background mode and a spent token are refused; a click token, a click on
Blender's own button, or, for Approve, Angelo's automatic token, are taken. It also requires every
``bpy.ops.angelo.<grant>(...)`` call in the add-on to carry the click, Angelo's own decision, or an ``invoke``.

.. list-table::
   :header-rows: 1

   * - Grants
     - Operators
   * - An outside app's access
     - ``external_app_connect``, ``external_app_copy_setup``; setting ``allow_local_cli``
   * - AI provider keys, accounts, programs, and where the chat goes
     - ``new_connection``, ``api_key``, ``endpoint_key``, ``connection_load_key``, ``connection_use``,
       ``use_connection``, ``connection_signin``, ``native_signin``, ``quick_connect``, ``team_sign_in``,
       ``fab_connection``; settings ``codex_path``, ``claude_path``, ``reuse_codex_login``
   * - Spending
     - ``select_model``, ``custom_model``, ``set_effort``, ``toggle_fast`` (on), ``enhancer_settings``,
       ``ai_render``, ``flow_run``, ``studio_generate``, ``studio_enhance``, ``shot_to_video``; setting
       ``helper_standing_consent``
   * - Trust and approval
     - ``trusted_scripts``, ``set_approval`` (Auto), ``approve`` and ``apply_approved_tool`` (also Angelo's
       automatic decision), ``forget_agent_changes`` (code agent scripts wrote in a folder loads as the person's
       own again; its C door, ``forget_changes``, also refuses any caller outside the person's own grant scope)
   * - Code from elsewhere
     - ``import_blender_settings`` (add-ons), ``godot_link_install``, ``godot_link`` (on), ``render_engine_setup``

Not grants: opening pages and menus, copying text, the narrowing operators above (those that narrow the person's
accounts, sign-ins or consents are refused to agent code all the same), library items added to the scene, exports,
checks and refreshes, and sending feedback, which opens the form in the browser for the person to send.

**Nothing escapes by being missing from a list.** The lists and scanners are ``grant_inventory`` (in the tests
folder). Every Angelo operator is in ``AUDIT``, in ``GROUPED`` (a guarded sibling, as Apply Approved Script is of
Approve) or in ``NOT_A_GRANT`` with a reason, and is judged by what it does: its ``execute``, ``invoke`` and ``modal``
and the functions they call, never its name. A grant widens a permission, trusts or approves anything, stores keys or
sign-ins, spends credits, connects or installs code, sends the person's data out, or writes grant state. Everything
else opens a page or a card, routes the person's own input, narrows, inspects or refreshes, edits the open document,
changes the layout, edits the person's own local data, exports or imports what the person chose, or continues an
approval already given. An operator that reaches a provider, the clipboard, Preferences, another program or files
outside the project has a reason of its own, saying what goes where.

* The operators come from Blender's live registry, in the app (``test_blender_grant_inventory``): every registered
  operator whose class is Angelo's, and every one whose idname names Angelo, C's included. The host test
  (``InventoryTests``) reads the source as a fast check: classes anywhere in a file and in subfolders, subclasses of
  Angelo operators, annotated idnames, ``type()`` classes (one with a computed idname must be named in ``DYNAMIC``),
  and C's ``ot->idname``. Two classes with one idname fail.
* C's denied-operator list must hold every guarded operator, a sibling included, and every operator in
  ``ACCOUNT_OPERATORS``: the confinement's operator gate and the refusal to draw a grant button for a script read it.
* An operator outside ``AUDIT`` whose body, or a function one level down, writes the ledger, takes Angelo's automatic
  scope, sets a chat's options or calls a grant entry fails, unless ``REACHES_GRANT_CODE`` says why it is not a grant.
* An operator outside ``AUDIT`` that writes grant state fails, unless ``WRITES_GRANT_STATE`` says it only narrows or
  puts back what the person had granted (restoring a deleted chat with its own options). A writer is a function that
  writes a path built from a ``GRANT_STATE`` name, in its body or kept on ``self`` by its class, and every function
  that calls one. A write through an object whose path arrives as an argument (a chat's session writer) is not
  followed; the chat operators' own reasons judge those.
* Every tool the agent may call only after the person's approval card (``tool_specs``: not read-only) names its
  operator twins in ``APPROVAL_TWINS``, the operators that do the same at the person's click. Each twin is a grant or
  has a reason of its own saying why the card does not apply to it.

A new operator fails these tests until someone has read it and placed it.

Known open items (break-it of the inventory, 2026-11-15): approvals other than scripts' (a pending call's
``authorized``) are Python attributes, not the ledger; a script can call an approval-carded tool's operator twin
without the card, and the confinement refuses that twin's network and private-file effects; and the call-site
check does not follow ``getattr(bpy.ops.angelo, name)``.

Runtime confinement
-------------------

The operator guard closes every path through Angelo's operators. While an agent
script runs, in every review mode, the runtime confinement
(:doc:`script_confinement`) also keeps it from:

* Angelo's private files;
* the grant operators;
* other programs, ``ctypes`` and native code from outside the app;
* taking the guard off.

What a script leaves to run later (a timer, a handler, a thread, a function it
defined) is marked as agent code and runs under the same confinement, so the guard
and the entries refuse it there too. This is no longer an accepted residual risk;
what remains is listed in :doc:`script_confinement`.

**One signal.** The guard and the confinement read the same signal,
``grant_guard.script_running()``, and the entries ``grant_guard.inside_agent_code()``.
Both read the confinement's stack check first (``script_confinement.confined()``),
which a script cannot clear. They are also true while agent code executes in this
process: ``grant_guard.agent_script()`` wraps the one ``exec`` in
``BlenderExecutor``, for every tool that runs a script, in the window, a worker or
a headless session. It is also true while any chat's call of a script-running tool
is marked executing. ``trusted_scripts_ui.agent_script_running`` is the same
signal.

Authority in C
--------------

The Copy setup break-it re-review (2026-11-14) showed that a guard held in
Python can be undone by Python in the same interpreter: tokens, wrappers, caches
and the consent flags themselves. So who may grant is decided in C, in
``_bpy._angelo_guard`` (``bpy_angelo_guard_authority*``), which a script can call
but not change: its module exposes only built-in functions and refuses every
``setattr`` (``test_blender_guard_authority``).

**Click tickets.** Angelo's canvases and card hosts tell the authority where each
control that grants is drawn (``consent_regions``: window, rectangle, the
operator's idname and a digest of its arguments). Only controls are regions: a hit
that takes no press (a reveal's shield, a disabled control, an info row) is never
one, since a press on a control under it would land in two regions, which C refuses
as ambiguous (``test_native_consent_regions``). The window manager reports
the operating system's pointer moves, presses and focus changes straight to C
(``BPY_angelo_guard_note_input``); no Python call can. A press on an approval's
own control (Approve, Always allow scripts, Allow this file, Accept) mints a ticket
only when it is armed, by the same rule as "A click the person aimed": shown for the
shield (the same control re-laid out while it stays drawn keeps it; one arriving under a
pointer that was not on it starts again), and aimed at by a move into it at any time
since it was drawn (leaving drops it) or by a real move inside it once its shield has
passed, or by the click that brings the window forward, which never grants itself and
arms a press only ``SHIELD_SECONDS`` later. That click is only the press that arrives
with the window's activation (``note_activate``, from ``wm_window``'s activation, within
``ACTIVATION_PRESS_SECONDS`` and before any other event but the activation's own
synthetic move); a window brought forward without a press has none. No other press aims:
presses alone, however far apart, never grant a control that appeared under a resting
pointer. A press on any other granting
control (a picker's model, effort, Fast or approval mode, Media Studio's Generate,
the Flows bar's Run) mints only after a real pointer move inside it at least
``ARM_SECONDS`` (0.3 s) after it was drawn there (the coordinator's ruling on the
review of d3024be): content that re-lays a control under a resting pointer, or under
a moving one, grants nothing on the person's next click, while their hand reaching a
control they read does. "Drawn there" starts again whenever the control's rect or
identity changes, and the 0.3 s count from the first frame that showed it there (its
own window's buffer swap, ``note_presented``; a control placed by an event, a scroll or
a re-flow, takes its own window's next frame, never another window's), never from when
it was made or drawn, so a slow first draw takes none of the person's time. Another
control swapped into a button's slot counts from the frame that shows it, and a
control drawn off screen counts from its first draw in a window. A window losing focus,
or coming forward again, starts every control in it again and drops every aim, as a
consent region does (``note_deactivate``'s and ``note_activate``'s epoch);
``window_state`` gives every surface's arming that one record. A press that comes sooner, or with no
move onto the control since, is answered quietly (the coordinator's ruling on the
0.3 s arm, 2026-11-16): C records it with the control (``unarmed``), and the guarded
operator it reaches, a surface's click or Blender's own button, refuses with no
report, toast or "Only your own click"; the control shows "Move onto the button, then
click" for three seconds (``grant_hints``), and a guarded setting's refused change
does the same. A refusal met by an agent (a script or an agent's tool call,
``agent_tool_scope.agent_acting``) never takes the person's record: it reports as before,
and the person's own press keeps its hint. Any small move inside the control afterwards arms it, a hand's jitter
included, so the next click works without leaving it (``test_blender_fast_press``). A real move has a non-zero delta; simulated events count only in
an app started with ``--enable-event-simulate``. Blender's own grant buttons (menus,
dialogs, Preferences) follow the same rule: each keeps when it was drawn where it is
across redraws while it stays the same control (operator, label, operator values)
in the same place on screen, and a real hover move ``ARM_SECONDS`` later arms it
unless code put the pointer there (``warped_into``). Leaving it, or its moving on
screen (a scrolled sidebar or Preferences, a re-flow), disarms it
(``test_blender_button_arming``). A confirm dialog Blender opens under the pointer
takes the person's click there without a nudge: its confirm runs in the consent the
press that opened it held (``hold_dialog``). Two consequences a person can notice: a
trackpad or pen tap on content that just appeared under a still pointer grants
nothing until the pointer moves; and accessibility tools that warp the pointer onto a
control and click are refused there, as any code that moves the pointer is. The press that brings a window forward never mints one, and two regions under
one press mint nothing. A ticket is single use, bound to its idname and digest, and
lasts five seconds, as a press may be held before its release (``take``, ``grant``).
Keymap items and buttons a script draws never mint one (``test_blender_guard_tickets``).
Code never aims the person's click (the independent review of 9c8b4e57077): wherever
code warps the pointer (``WM_cursor_warp`` reports it, ``note_warp``), the control it
lands in, or one drawn under it later, mints nothing until the person moves out of it
and back in, so their next click, meant for something else, grants nothing there. An
agent script can't warp the pointer, simulate input, or open a menu, panel or popup
under it at all (``Window.cursor_warp`` and ``event_simulate``, ``wm.call_menu`` and
its pie, panel and asset-shelf kin, ``WindowManager.popup_menu`` and the
``invoke_*`` popups: ``test_blender_pointer_warp``). Any popup that opens while an
agent script runs, whatever opened it (a C operator's invoke too), mints nothing for
its buttons, before or after it rebuilds (``PopupBlockHandle::angelo_from_agent``),
and so does one a modal operator the script started opens later. An operator an agent
script ran never enters repeat-last or redo (Shift+R, F3, F9), where it would run
again unconfined.

**The keyboard** (the maintainer's ruling, 2026-11-14: "Yes, guarded"). Each surface
reports which of its controls has keyboard focus along with its regions. C takes
that focus as the person's only right after their own real Tab or Shift+Tab, or their
own press on that control; focus that code moves is cleared. An agent script that runs
after that Tab or press voids it, with any focus it gave (the Fusion break-it review,
2026-11-15, N4): the script could change the draw state the surface reports focus from,
so a report after it needs the person's own Tab or press again (``note_agent_run``, when
a script's outermost confined region begins). A real Return, Space or
keypad Enter then mints the focused control's ticket once the arm delay has passed
since focus arrived. Losing window focus clears it; another surface drawn with nothing
focused does not. Inside a popover or list the
person opened (the coordinator's keyboard ruling, 2026-11-16, so keyboard-only use
keeps what 1.x could do): the surface tells C it opened (``open_surface``, taken only
in the scope of the person's own ticketed press or key; a popover code opens is never
theirs) and reports the control a key acts on as its focus (the effort slider, the
cursor row: ``chat_popovers.key_focus``); while it is open that focus is the person's,
and Return acts on it. An arrow, Home or End mints a ticket only where moving is the
action, a slider (``focus(..., moves=True)``); on a list's row it moves the cursor and
Return picks. Focus the person's own movement key just moved inside their popover is
theirs at once (Down, Down, Return), where focus that arrives any other way waits the
arm delay. Another surface's control with the same digest is a new focus. A field the person pressed
into is focused by them: Return there runs the field's primary action (Media Studio's
Generate, the AI Providers sheet's Save) in that field's ticket; a field code focused
takes none. Closing the popover, or an agent script running, ends it. A keymap item never takes these
tickets: they name the surface's control (``canvas.<action>``, ``card.<action>``,
``page.<action>``), which no operator call takes. Blender's own buttons stay
pointer-only: Blender moves through them with arrow keys, not Tab focus. The one
difference a person can see from before C: a widening change in Blender's Preferences
editor (Always allow scripts, letting other apps reach Angelo, an agent program's
path) now needs a real press on its checkbox or field, where any change made while
the Preferences editor was the active space used to count. Blender 5.3
exposes no accessibility actions (VoiceOver, Narrator), so no screen-reader path
grants either way.

**Scopes.** A ticket opens the person's scope, in which any guarded operator may
run. Angelo's own automatic decision (Auto, a headless session, a worker's approval
route) opens an automatic scope, in which only the operators C lists for it run:
Approve and Apply approved step, as before C (``allow_automatic``, add-only). A test
harness's scope (``stand_in``, ``harness``) exists only in an app started with
``--enable-event-simulate``. A setting widens only in the person's scope or with its
own checkbox's ticket. A dialog an operator opens for the person keeps their consent
for its confirm, for that operator instance only (``carry``, ``hold_dialog``): three
minutes, a slow reader's confirm (``DIALOG_SECONDS``; a file browser's pick five,
``BROWSE_SECONDS``, for an operator that declares ``grant_browse``), and never across
an agent change: an agent script's run, any agent tool that is not read-only, or an
agent's native job (``grant_gestures.agent_changed``, which ``BlenderExecutor`` notes
before each), and work that lands from outside the person's present input, noted where
a result applies, never for progress or a status (``grant_gestures.landed``): a job
manager job's end (``JobManager._end``, on the main thread), a generation's files
(``StudioJobs.pump``, for Media Studio, flows and the chat), a flow step that finished
(``flow_runner.advance``), a Media Studio fit or check, a placed photo drop, a Library or
Fab import, and an engine's request over its link (``engine_link._handle``). It ends
dialogs' held consent and gestures' holds only (C's ``outside_work``): the person's own
presses, moves and focus stay theirs, since outside work synthesizes no input, so a
generation running for minutes never keeps them from granting. An agent's own run or
tool resets those too (``agent_changed``). Every ``bpy.app.timers.register`` site and
every main-thread drain of a worker queue is classified as landing at named apply-sites
or as landing nothing, with its reason (``landing_classification``), and each landing
site notes once per result and never per progress tick (``test_native_landing``); work
outside that list is bound only by what the operators' ``consent_target`` reads. An
external AI app's call is an agent tool: it runs through the
chat's tool pump and ``BlenderExecutor``. After any of these the dialog needs the
person's fresh press. Streamed chat text is not a change and ends nothing. Whether a
tool writes is read from ``tool_specs.READ_ONLY_TOOLS``, a frozenset taken as the tools
register. A spec's annotations are descriptive only: they are a dict, which ``dict``'s
own methods can still change, so no decision reads ``readOnlyHint`` outside that
registration (an AST test pins it), and the call check reads the tools' schemas from
``tool_specs.SCHEMAS``, a read-only copy taken then, and every family's validator reads
its own read-only copies (``read_only``: each ``*_spec.py`` seals the tables its checks
read once its tools are built, and a validator that builds values from a schema works on
a plain copy of the registered one per call); an unknown tool counts as a scene
mutation. While a confined script runs, other threads read whatever module attribute it
rebinds (``READ_ONLY_TOOLS``, a ``sys.modules`` entry) until its region ends and the
journal puts it back; that grants nothing. What a
confirm acts on is bound as its dialog opens and must be the same at the confirm, for
every guarded operator (``grant_gestures.dialog_target``): the document and scene
Angelo's window shows, the dialog window's scene, the chat (its id, document and
connection), and its subject in Blender: the active object, the selected objects and
the node tree its editor shows (a flow), so a read-only tool that changes any of them
leaves the confirm nothing to grant. Every guarded operator that opens a dialog or a
file browser declares ``consent_target``: what else its execute reads (a flow's selected
nodes, the chat's model, a Media Studio area's run), or None when it reads nothing more;
it adds to those fields and never replaces them, and a target that can no longer be read
is absent at both ends. The dialog's own
properties are not bound: only its own drawing reaches them, the person's edits
(``test_native_dialog_consent``; ``test_blender_pointer_warp`` leaves the trust dialog
open across an agent script and across an agent's typed tool). An operator an agent script runs
never stores the last-used properties of the person's next invoke, and never enters
repeat-last or redo: F9 "Adjust Last Operation" no longer offers the agent's last
operator.

The chat canvas registers its granting controls as ``canvas.<action>`` with the
digest of the control's value (``grant_guard.canvas_regions``), and runs the click's
dispatch in that ticket's grant scope (``grant_guard.by_click``). Every surface of
Angelo's own that reaches a grant does the same with its own prefix: the cards
(``card.``), the AI Providers page and its sheet (``page.``, ``sheet.``), the
Library (``library.``), Media Studio (``studio.``: Generate, Enhance, a workflow's
run, check and fit) and the Generation Flows bar (``flow.``: Run), through
``grant_guard.ticketed``. A key runs in the ticket of the control the person
focused, so C decides whether it counts. A gesture that reaches its grant after
its press keeps that press's consent in C (``grant_gestures``): ``deferred`` for a
step that runs once a card has closed (the engine suggestion's folder picker), and
``press_hold`` / ``dragged`` for a drag (the effort slider), spent once per move.
A drag's hold lives seconds (``hold_gesture``), ends with its release
(``final=True``), lasts only while the person's button is down (the first move with
no button down ends it, release seen or not), and goes when any window loses focus
or an agent script runs, so nothing of a gesture outlives it
(``test_native_grant_gestures``, ``AGestureHoldEndsWithThePersonsButton``).
A surface that called a grant operator outside such a scope refused the person's
own click as it refuses a script's; in 2.0.0 Media Studio and the Flows bar did,
until they registered their controls. ``test_native_grant_call_sites``
(``grant_call_sites``) keeps every call of a grant operator in the shipped add-on
reached only through such a scope, a guarded operator's own body or a declared
reason. It matches openers by their exact owner and name, and Angelo's own automatic
scope covers a call only at a site listed in ``AUTOMATIC`` with why no press is
needed there, so a UI surface can't pass for covered by it.

GUI suites click a grant control as the person does: through the window
(``qa_input.press``, ``window_click``, ``aim``; ``popup_button`` and
``region_button`` find Blender's own buttons where they are drawn), so C mints the
press's ticket. Each keeps a check that the same grant from a script is refused.
A suite that tests a form rather than its click may stand in for the person
through the harness scope; ``test_blender_harness_confined`` pins that an agent
script never enters that scope where it exists, ``test_blender_guard_authority``
that a shipped app has none, and ``test_blender_grant_surfaces`` keeps each such
surface's real path (Auto, a script's Review, a connection's key) covered by real
presses.

**The ledger.** The grant state lives in C: trusted scripts per chat, Always
allow scripts, approved scripts, external clients' allowed script files, and the
rest as it moves there. Each namespace narrows to one value; any other value is
set only inside a grant scope, opened by a taken ticket (``grant``) or Angelo's
own automatic decision (``automatic``), on that thread. Agent code writes nothing
there in either direction: ``set`` and ``remove`` refuse it, narrowing included
(the Fusion audit's G2), while the person's controls and Angelo's own code still
narrow. Values are exactly None, bool, int, float
or str.

**Admission.** ``run_confined`` digests a script's text in C and runs it only
when the ledger holds that exact text approved for that call, and only once
(``BPY_angelo_guard_authority_admits``). The approval is written by the person's
Approve, or by Angelo when a standing consent held in the ledger (the chat
trusted, Always allow scripts, the external app's allowed file) covers the
script; a standing consent alone admits nothing, so a script one must not cover
(it touches preferences or Angelo itself) still asks. Auto alone admits no Python:
in Auto, a script runs on the person's Approve, or on a standing consent the
person gave that covers it.

**Which state is grant state.** The inventory finds every name a state file can be
written under by where Angelo reads or writes files, not by how a name looks: every
path argument of a filesystem call (``x / y``, ``os.path.join``, ``Path``,
``with_name``, ``open``, ``os``'s and ``shutil``'s file calls, Blender's
``user_resource``/``resource_path`` with ``path=``, ``tempfile``'s ``prefix``,
``suffix`` and ``dir``, and ``private_roots``' ``allocate`` and ``declare``), its
literals and the literal parts of its f-strings, concatenations and templates, and
every module-level constant it names, followed through imports and tuples. Every
module-level string named like a path (``FILE``, ``NAME``, ``FOLDER``, ``PREFIX``, ``DIR``,
``PATH``) or shaped like a file name is judged too. Each is in ``GRANT_STATE`` (it
holds or decides a grant: who may act or connect, standing approval and each chat's
approval policy, sign-ins and keys, trust, what is private, policy, restricting
records, which code runs, what a spend runs with), ``NOT_GRANT_STATE`` (the person's
own work, interface state, Angelo's records and scratch files) or ``NOT_STATE`` (not
a file, a bare suffix, an output name, something shipped read-only, another
program's files), with a reason. Grant state is held in the ledger or a guarded
private root; a new name fails the test until someone has judged it.

The core is plain C++ (``bpy_angelo_guard_authority_core``), with GTests that
also build on their own (``tests/angelo_guard/build_core_tests.sh``).

Residuals (2.0.0)
-----------------

The boundary is **agent code**: code the confinement marks as the agent's, by its
frames or by the confined run it belongs to. What the Fusion audit (2026-11-15)
found on that boundary, and where each stands:

* **The ledger (G2), closed.** Agent code cannot write the ledger in either
  direction, so a script can neither give nor withdraw an approval, a mode or a
  trust, its own chat's or another's.
* **The function behind a grant (G3), closed.** An entry carries no
  ``__wrapped__``; agent code cannot read an installed function's closure (the
  confinement refuses it, by attribute, ``attrgetter`` or ``inspect`` alike); and,
  in depth, the function an entry guards is sealed in C (``_angelo_guard.seal``):
  what it holds can be called only from the entry's own code, which judges the call
  first. The same seal holds each grant operator's ``execute`` and ``invoke``.
* **Unconfined code in Angelo's process (G7), accepted.** Python that is not agent
  code, Angelo's own or an installed add-on's, runs with Blender's full privileges:
  it can call ``automatic`` and every entry, as it could reimplement them. The grant
  operators themselves still take effect only from the person's own click (a press
  ticket an add-on cannot mint), and ``NOT_YOURS`` says what the guard refuses: an
  agent script or a keymap entry. The fix is a separate helper process that holds
  keys, the ledger and the consent surface (the helper-process design,
  2026-11-15), planned for 2.1 or 2.2.
