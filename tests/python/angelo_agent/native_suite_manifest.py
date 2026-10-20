# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit classification of every native Blender suite for run_native_suites.py.

Each ``test_blender_*.py`` file appears exactly once (enforced by
test_native_suite_runner.py). The class, flags and report contract below were
read from each suite's own guards, docstring and receipt code, not inferred
from its name. Update this file whenever a native suite is added, renamed or
changes how it signals success.

Classes:

* ``BACKGROUND``: runs with ``--background``; no window, provider or account.
* ``GUI``: needs a real window (timers, screenshots, synthetic events); never
  uses a provider, account or paid request.
* ``LIVE``: real provider access (paid subscription turns or network probes of
  provider endpoints) or the caller's real login Keychain (throwaway
  test-only items). Runs only with ``--allow-live`` plus an exact
  ``--include`` of its name, and only when the caller has exported the suite's
  own opt-in variables.

A suite runs on every platform unless ``platforms`` names the ``sys.platform``
values its code depends on; the runner skips it elsewhere with
``platform_reason``. ``EXCLUDED`` lists other native entry points the runner
never launches and ``HELPERS`` the imported modules (``native_suite_other_files``).
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from native_suite_other_files import EXCLUDED, HELPERS  # noqa: F401  (part of this manifest)

BACKGROUND, GUI, LIVE = "BACKGROUND", "GUI", "LIVE"
CLASSES = (BACKGROUND, GUI, LIVE)
DEFAULT_TIMEOUT = 300
# ``output_root`` resolved by the runner at run time to native_suite_paths.short_temp_root():
# /private/tmp on macOS (104-byte socket paths), the temp folder elsewhere.
SHORT_TEMP_ROOT = "<short temp root>"
# Reasons shared by suites that cannot run on every platform.
POSIX_STAND_IN_CLIS = ("The stand-in claude/codex CLIs are POSIX shell wrappers (fake_provider_cli.install); "
                       "Windows needs .cmd or .exe stand-ins.")


@dataclass(frozen=True)
class Report:
    """A JSON receipt the suite writes; checked after exit (fail closed)."""

    path: str                        # relative to the suite's ANGELO_QA_DIR
    success_key: Optional[str] = "success"   # field that must be exactly true; None: checks only
    require_complete_phase: bool = False      # GUI receipts that also record phase == "complete"
    env: Optional[str] = None        # variable the suite reads for its report path, set by the runner


@dataclass(frozen=True)
class Suite:
    script: str
    kind: str
    purpose: str
    report: Optional[Report] = None
    marker: Optional[str] = None     # stdout completion line, for suites without a JSON receipt
    window: Optional[bool] = None    # LIVE only: whether the suite needs a window
    event_simulate: bool = False     # suite asserts or calls Window.event_simulate
    real_input: bool = False         # needs the machine's own keyboard, mouse or clipboard, or Blender's idle sleep:
                                     # its window runs without the runner's input isolation (--enable-event-simulate)
    default_chat: bool = False       # needs Angelo's default chat startup (no ANGELO_DISABLE_DEFAULT_CHAT)
    first_launch: bool = False       # needs Angelo's startup cards and welcome (no ANGELO_DISABLE_INTRO): the suite
                                     # sees the real first frames, Blender's startup splash included
    gui_superset: bool = False       # BACKGROUND suite that adds window-only checks when --gui is given
    host_args: Tuple[str, ...] = ()  # non-empty: start with host Python and these arguments
                                     # ({binary}, {qa} are substituted); the suite launches the binary
    output_root: Optional[str] = None  # the suite refuses QA directories outside this root (SHORT_TEMP_ROOT:
                                       # resolved for the running platform)
    serial: bool = False             # documented to run alone, never beside other suites
    state_in_qa: bool = False        # the suite requires its state, accounts and Blender user
                                     # resources inside ANGELO_QA_DIR, so the runner nests them there
    extra_args: Tuple[str, ...] = ()  # additional Blender arguments before --python
    timeout: int = DEFAULT_TIMEOUT
    live_env: Tuple[str, ...] = ()   # opt-in variables a LIVE suite reads from the caller
    options_env: Tuple[str, ...] = ()  # non-secret options any suite may take from the caller (languages ...)
    platforms: Tuple[str, ...] = ()  # sys.platform values the suite's code can run on; empty: every platform
    platform_reason: str = ""        # why it cannot run elsewhere (the runner's skip reason)
    needs_opengl: bool = False       # needs a working OpenGL driver; skipped (never passed) where the runner is told
                                     # the machine has none (ANGELO_QA_NO_OPENGL), counted from a GPU machine's run
    notes: str = ""

    @property
    def name(self):
        return self.script[:-3]

    @property
    def needs_window(self):
        return self.window if self.kind == LIVE else self.kind == GUI

    @property
    def host_launcher(self):
        return bool(self.host_args)

    def runs_on(self, platform):
        return not self.platforms or platform in self.platforms


INSTALLED_HOST = ("Imports the app's installed host modules (bpy.utils.system_resource), so it "
                  "validates the binary's bundled add-on, not this checkout's Python.")

SUITES = (
    Suite("test_blender_script_kit.py", BACKGROUND,
          "The reviewed-script kit: exact solids, cuts that refuse what they cannot prove, rows, self-checks",
          Report("results.json")),
    Suite("test_blender_script_kit_reviews.py", BACKGROUND,
          "The reviewed-script kit, its adversarial reviews' cases: refusals, reached parts, slots, checks, cutters",
          Report("results.json")),
    Suite("test_blender_script_kit_support.py", BACKGROUND,
          "The kit's support check through the scene: decks, tables, shelves, walls, instances, real floats",
          Report("results.json")),
    Suite("test_blender_script_booleans.py", BACKGROUND,
          "A script's Booleans measured where the executor runs it: applied and live rows, the facts at the extremes, "
          "bpy.ops put back, and the kit's crowded and retried cuts proving the volume they removed",
          Report("script-booleans.json"), timeout=240, notes="No provider, account or network."),
    Suite("test_blender_script_parent_inverse.py", BACKGROUND,
          "A parent-inverse statement reads its parent's current matrix: the recorded car, fresh, keyed, many parts",
          Report("results.json")),
    Suite("test_blender_script_api_removed.py", BACKGROUND,
          "The removed-API table against Blender's own RNA: removed names absent, replacements present",
          Report("results.json")),
    Suite("test_blender_cycles_device_default.py", BACKGROUND,
          "Cycles' scene device defaults to GPU (a saved CPU stays CPU) and Angelo lists devices in a child process",
          Report("results.json"), notes="Lists this machine's GPU devices in a child of the app; no render."),
    Suite("test_blender_stage_storage.py", BACKGROUND,
          "Adaptive stage files and closed native Main ownership/lifecycle",
          Report("results.json")),
    Suite("test_blender_borrowed_materials.py", BACKGROUND,
          "Materials a material-blind program reads in place: closed stages, live users, stale refusals",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_animation.py", BACKGROUND,
          "Review repro: a staged object's Action writing through its slot into a borrowed live material",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_lifecycle.py", BACKGROUND,
          "Review pins: preview accept, inspection, recalc, failed suspension, removed groups, save while waiting",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_data_animation.py", BACKGROUND,
          "Review repro: Grease Pencil or hair data with an Action fails a move as INTERNAL_ERROR",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_review2.py", BACKGROUND,
          "Review pins: animated dependencies, shared users, races while waiting, reader refusals, ID budget",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_closure.py", BACKGROUND,
          "Every object data type in the closure: GP/hair animation, layer parents, surfaces, curve borrowing",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_final_review.py", BACKGROUND,
          "Review pins: no stage evaluation before finish_stage, element views, the reader size gate, repoints",
          Report("results.json")),
    Suite("test_blender_borrowed_materials_shared_slots.py", BACKGROUND,
          "One private copy per live material of an object when a borrowing stage is privatized (inspection, "
          "owns_animated), DATA and OBJECT slots included",
          Report("results.json")),
    Suite("test_blender_review_gp_layer_parent_swap.py", BACKGROUND,
          "Review repro (pre-existing): a Grease Pencil data swap with a parented layer is refused, not UNKNOWN",
          Report("results.json")),
    Suite("test_blender_native_depsgraph.py", BACKGROUND,
          "Native evaluation, constraints, animation, visibility, cycles, Undo and reload",
          Report("results.json")),
    Suite("test_blender_native_graph_lifecycle.py", BACKGROUND,
          "Render visibility, linked collection instances and graph rebuild/reload",
          Report("results.json"), serial=True, needs_opengl=True),
    Suite("test_blender_subdiv_uploads.py", GUI,
          "Subdivision loose geometry, edit/paint overlays and exact viewport pixels",
          Report("results.json"), serial=True),
    Suite("test_blender_action_audit_fixes.py", BACKGROUND, "Production-route action audit regressions",
          Report("action-audit-fixes.json", "all_passed")),
    Suite("test_blender_action_kernels.py", BACKGROUND, "Native action kernels on private staged data",
          marker="ANGELO_ACTION_KERNELS_TEST_OK"),
    Suite("test_blender_agent_task_eval.py", BACKGROUND, "26 staged native programs graded by host checks",
          Report("agent-task-eval.json")),
    Suite("test_blender_agent_workspace_ui.py", GUI, "Workspace UI, approvals, staging, Undo and restore",
          Report("agent-workspace-ui.json"), event_simulate=True),
    Suite("test_blender_angelo_startup.py", GUI, "Remembered Manual startup and Studio launch",
          Report("startup-qa.json", require_complete_phase=True), default_chat=True, platforms=("darwin",),
          platform_reason="Checks the macOS app bundle identity (Angelo.app and its Info.plist)."),
    Suite("test_blender_api_lookup.py", BACKGROUND, "Read-only installed API introspection",
          Report("results.json")),
    Suite("test_blender_api_providers.py", GUI, "Provider settings, forms and picker with synthetic health",
          Report("api-providers.json", require_complete_phase=True)),
    Suite("test_blender_api_tls.py", LIVE, "Unauthenticated HTTPS probes of real provider endpoints",
          Report("api-tls.json", env="ANGELO_PROVIDER_QA_REPORT"), window=False,
          notes="Network access to third-party provider endpoints; no keys or billing."),
    Suite("test_blender_app_identity.py", GUI, "Default user folders, bundle identity, Help menu, About and splash",
          Report("fixture/app-identity.json"), marker="ANGELO_APP_IDENTITY_OK", timeout=420, event_simulate=True,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver; launches one background and one window process itself with only HOME-style "
                "isolation (no BLENDER_USER_* override) so the product's real default folders are exercised."),
    Suite("test_blender_about_card.py", GUI, "About Angelo card from the Angelo menu: text, links, Close; en, ar, large scale",
          Report("about-card.json", require_complete_phase=True), default_chat=True),
    Suite("test_blender_team_account.py", GUI,
          "Team account: status-bar item and menu, agent usage report, AI Providers, managed policy",
          Report("team-account.json", require_complete_phase=True)),
    Suite("test_blender_branding.py", GUI, "Native menu/logo branding",
          Report("branding.json", require_complete_phase=True)),
    Suite("test_blender_window_chrome.py", GUI,
          "Angelo's teal on the top bar and the OS title bar: theme, pixels, menu text contrast, other windows and themes",
          Report("window-chrome.json", require_complete_phase=True),
          notes="The title-bar colour and text appearance are read from AppKit on macOS only; the theme and pixel "
                "checks run everywhere. Opens two more windows at the end."),
    Suite("test_blender_camera_light_programs.py", BACKGROUND, "Camera/light programs, revisions, Undo and stale gates",
          Report("camera-light-programs.json")),
    Suite("test_blender_review_light_rig.py", BACKGROUND,
          "Review of orbit placement through the real controller: one resolution, camera dependencies, far orbits",
          Report("review-light-rig.json"),
          notes="Adversarial reproductions; fails while a recorded fault reproduces. No provider or network."),
    Suite("test_blender_chat_cards.py", GUI, "Plain approval cards, limit/offline notices, voice visibility and snapshot text",
          Report("chat-cards.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer a turn with a limit result; no account or inference."),
    Suite("test_blender_codex_turn_errors.py", GUI,
          "A Codex turn a throttle or usage limit ends: its notice card at once, one automatic resend, an idle chat",
          Report("codex-turn-errors.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=240,
          notes="The stand-in codex app-server (fake_provider_cli.py) ends scripted turns as codex 0.162 does "
                "(HTTP 429, usage limit); no account, network or inference."),
    Suite("test_blender_chat_replies.py", GUI,
          "Markdown replies, a streaming fence, a media tile, notice cards, the transcript scrollbar, Arabic, narrow, scale",
          Report("chat-replies.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Transcript rows set directly; no provider, account or network."),
    Suite("test_blender_trusted_scripts.py", GUI,
          "Trusted scripts: exact review runs once, chat consent runs automatically, revoked or Read-only never runs",
          Report("trusted-scripts.json", require_complete_phase=True), timeout=120,
          notes="A stand-in conversation through the real admission, approval operator, executor and snapshots; "
                "no provider or account."),
    Suite("test_blender_trusted_scripts_consent.py", GUI,
          "The trusted-scripts consent dialog: default off, Escape cancels, Enter grants, invoking again revokes",
          Report("trusted-consent-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=120, serial=True, notes="Synthetic key events on the real dialog; no script runs."),
    Suite("test_blender_turn_recovery.py", GUI,
          "Failed plan steps: a long round, failed lookups, a rolled-back commit, a script that raises and its Undo steps",
          Report("turn-recovery.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=480,
          notes="The stand-in claude CLI (fake_provider_cli.py) plays rounds the suite scripts; no account or inference."),
    Suite("test_blender_reply_stream.py", GUI,
          "A streamed Claude plan reply in the chat: grows as written; refused, stopped or withdrawn leaves no row; no key",
          Report("reply-stream.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=300,
          notes="The stand-in claude CLI (fake_provider_cli.py) streams plans as fine-grained tool input; "
                "imports the add-on from the checkout; no account or inference."),
    Suite("test_blender_plan_progress.py", GUI,
          "A plan's provisional steps in the live row: drawn as it is written, labelled Planned; refused, stopped "
          "or keyed leaves nothing; Arabic",
          Report("plan-progress.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=300,
          notes="The stand-in claude CLI (fake_provider_cli.py) streams plans at Opus 5.5's recorded pace; "
                "imports the add-on from the checkout; no account or inference."),
    Suite("test_blender_chat_waiting.py", GUI,
          "The chat's marble wait: carved while Claude plans, tapped by real progress, revealed when the plan "
          "arrives; frames capped and stopped with the turn; Reduce Motion; draw cost and pixels",
          Report("chat-waiting.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=300, serial=True,
          notes="The stand-in claude CLI (fake_provider_cli.py) streams thinking lines and a castle plan at "
                "Opus 5.5's recorded pace; imports the add-on from the checkout; no account or inference."),
    Suite("test_blender_everyday_turns.py", GUI,
          "Whole turns with batch, also, Subdivision, a sky and Cycles: built, edited again, verified and undone",
          Report("everyday-turns.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=480,
          notes="The stand-in claude CLI (fake_provider_cli.py) plays rounds the suite scripts; no account or inference."),
    Suite("test_blender_chat_hit_testing.py", GUI, "Scissored approval/preview controls and composer input",
          Report("chat-hit-testing.json"), event_simulate=True),
    Suite("test_blender_chosen_tools.py", GUI, "The composer's + menu and Start from a base mesh: menu, chip, fit, "
          "the Send step, every base mesh timed, the staged path", Report("chosen-tools.json", require_complete_phase=True)),
    Suite("test_blender_chat_popovers.py", GUI, "Native GPU/modal model picker",
          Report("chat-popovers.json", require_complete_phase=True)),
    Suite("test_blender_checkpoint_restore_ui.py", GUI, "Scene recovery, injected faults and Undo Restore UI",
          Report("checkpoint-restore-ui.json"), event_simulate=True, serial=True),
    Suite("test_blender_restore_pixels.py", BACKGROUND,
          "Restore and Undo restore name the unsaved image pixel edits no snapshot holds, and the image lacks them",
          Report("restore-pixels.json")),
    Suite("test_blender_checkpoints.py", BACKGROUND, "Native snapshot capture/restore of scene and files",
          marker="ANGELO_CHECKPOINT_TEST_OK"),
    Suite("test_blender_compiled_core.py", BACKGROUND, "Compiled RNA, editor buffers and save/load",
          Report("compiled-core.json")),
    Suite("test_blender_compiled_gui.py", GUI, "Compiled editor picker, bootstrap, docking and drops",
          Report("compiled-gui.json", require_complete_phase=True), event_simulate=True,
          notes=INSTALLED_HOST),
    Suite("test_blender_cross_provider_live.py", LIVE, "Real Codex and Claude handoff and delegation",
          Report("cross-provider-live.json"), window=False,
          live_env=("ANGELO_QA_LIVE_BOTH", "ANGELO_QA_CLAUDE_STATE_DIR", "ANGELO_QA_CLAUDE_CONNECTION_ID"),
          notes="Bills the user's own Codex and Claude subscriptions."),
    Suite("test_blender_derived_checks.py", BACKGROUND, "Derived program checks on real staged data and commits",
          Report("results.json")),
    Suite("test_blender_correction_schemas.py", BACKGROUND, "Invalid parameter schema feedback and corrected native actions",
          Report("correction-schemas.json"), serial=True, extra_args=("--threads", "2")),
    Suite("test_blender_complete_checks.py", BACKGROUND, "Complete program checks, retained previews and bounded work",
          Report("complete-checks.json", "all_passed"), serial=True, extra_args=("--threads", "2"),
          options_env=("ANGELO_COMPLETE_PHASE", "ANGELO_QA_CONCURRENT_NATIVE_JOBS", "ANGELO_CAPTURE_LARGE_RESULT",
                       "ANGELO_COMPLETE_TRIALS", "ANGELO_COMPLETE_JOURNAL")),
    Suite("test_blender_complete_review_ui.py", GUI, "Complete approval reviews, bounded layout and file-load lifecycle",
          Report("complete-review-ui.json", require_complete_phase=True), event_simulate=True, serial=True,
          extra_args=("--threads", "2"), timeout=960),
    Suite("test_blender_material_identity.py", BACKGROUND, "Private material identity and staged sharing policies",
          Report("material-identity.json"), serial=True, extra_args=("--threads", "2")),
    Suite("test_blender_partial_checks.py", BACKGROUND,
          "Partial typed verification and exact qualified unlink obligations",
          Report("partial-checks.json", "passed"), serial=True, extra_args=("--threads", "2")),
    Suite("test_blender_commit_fidelity.py", BACKGROUND,
          "Commits reproduce their measured stage: linked duplicates, injected losses roll back, dependents, guard cost",
          Report("commit-fidelity.json")),
    Suite("test_blender_typed_review.py", BACKGROUND,
          "Linked copies end on their root's materials; frame moves refuse only keyed writes; orbit cameras' time",
          Report("typed-review.json")),
    Suite("test_blender_typed_review3.py", BACKGROUND,
          "What places a camera from RNA; keyed configure paths; name-only keeps; applied graphs beside a World",
          Report("typed-review3.json")),
    Suite("test_blender_typed_review4.py", BACKGROUND,
          "Drivers and node groups followed; stated defaults applied; shared graphs; rotation modes; poll resume",
          Report("typed-review4.json")),
    Suite("test_blender_camera_guards.py", BACKGROUND,
          "Orbit cameras read again at commit (moved, frame update, markers, deleted); context and quoted driver paths",
          Report("camera-guards.json")),
    Suite("test_blender_kept_values.py", BACKGROUND,
          "Later material edits, unkeyed edits kept, in-program sharing refused early, key frame, UV map, drivers",
          Report("kept-values.json")),
    Suite("test_blender_kept_settings.py", BACKGROUND,
          "Settings a call leaves out stay: every typed modifier, constraint and Principled input against Blender's",
          Report("kept-settings.json"), timeout=900),
    Suite("test_blender_geometry_measures.py", BACKGROUND,
          "Only checks that can see a geometry change certify it; point-rewriting settings; refusals before approval",
          Report("geometry-measures.json")),
    Suite("test_blender_bound_obligations.py", BACKGROUND, "Bound material obligations, provenance and retained previews",
          Report("bound-obligations.json", "passed"), serial=True, extra_args=("--threads", "2")),
    Suite("test_blender_property_set.py", BACKGROUND, "property_set on objects, data and scene settings; RNA checks and Undo",
          Report("property-set.json")),
    Suite("test_blender_world.py", BACKGROUND, "world_configure: background, environment image, shared Worlds and Undo",
          Report("world.json")),
    Suite("test_blender_visibility_gaps.py", BACKGROUND,
          "Frame-free revisions across a frame change, where typed programs end (Math, mesh limits, leftovers "
          "a save writes) and script ids, in the compiled app",
          Report("visibility-gaps.json"), timeout=240,
          notes="Saves and reopens one temporary file; no provider, account or network."),
    Suite("test_blender_world_light.py", BACKGROUND,
          "The World's sun and brightness facts against Cycles renders and Blender's own save: the bearing "
          "convention, the view-transform reading, the measured sky table, results and the settings read",
          Report("world-light.json"), timeout=240,
          notes="Tiny Cycles CPU renders; no provider, account or network."),
    Suite("test_blender_rc10_gaps.py", BACKGROUND,
          "Revisions the host left out (read-backs, small revisions, the scene settings), "
          "rule-based selection up to the mesh limit and the facts typed results carry",
          Report("rc10-gaps.json"), timeout=240, notes="No provider, account or network."),
    Suite("test_blender_motion_readback.py", BACKGROUND,
          "Where animated objects are at a few frames: held-out rigs against Blender's own evaluation, the scene "
          "put back, results of scripts, programs and angelo_scene_object frames, not-measured cases, cost",
          Report("motion-readback.json"), timeout=240, notes="Bakes one small cloth cache; no provider or network."),
    Suite("test_blender_motion_guards.py", BACKGROUND,
          "The motion read's guards: negative frames, simulation zones, static hair, bone and deform parents, "
          "scope, unevaluated objects, instancers, budgets, Python handlers, revisions, shape keys, wall time",
          Report("motion-guards.json"), timeout=300, notes="No provider or network."),
    Suite("test_blender_motion_candidates.py", BACKGROUND,
          "What a motion read finds and states past its caps: edits past the change list, rig curves, drivers and "
          "NLA strips; particle and vertex instances; a null box past the instance cap; Pose mode; budgets",
          Report("motion-candidates.json"), timeout=300, notes="No provider or network."),
    Suite("test_blender_motion_interface.py", GUI,
          "A motion read in the interface: unkeyed edits outlive Blender's notifiers; no frame subscriber runs",
          Report("motion-interface.json", require_complete_phase=True), notes="No provider or network."),
    Suite("test_blender_script_renames.py", BACKGROUND,
          "Names Blender gave a script's data-blocks in place of the names asked for, against Blender 5.2's own "
          "naming: assigned names, appends, imports and overrides, the cap, the read limit and the costs",
          Report("script-renames.json"), timeout=300, notes="No provider, account or network."),
    Suite("test_blender_script_api_notes.py", BACKGROUND,
          "Renamed 4.x names a script's result notes, checked against 5.2, and what a real World shows",
          Report("script-api-notes.json")),
    Suite("test_blender_modifier_stack.py", BACKGROUND, "New modifier types, stack remove/move/apply, rollback and Undo",
          Report("modifier-stack.json")),
    Suite("test_blender_object_management.py", BACKGROUND, "Rename, duplicate, delete, apply transforms, origin, collections",
          Report("object-management.json")),
    Suite("test_blender_animation_edits.py", BACKGROUND, "Key delete/configure, shape keys, NLA push-down, rollback and Undo",
          Report("animation-edits.json")),
    Suite("test_blender_constraints.py", BACKGROUND, "Limit, Damped Track, Copy Transforms and Child Of constraints with Undo",
          Report("constraints.json")),
    Suite("test_blender_uv_tools.py", BACKGROUND, "Smart UV Project and Pack Islands with derived checks and Undo",
          Report("uv-tools.json")),
    Suite("test_blender_mesh_tools.py", BACKGROUND, "Mesh subdivide and delete with topology steps and Undo",
          Report("mesh-tools.json")),
    Suite("test_blender_mesh_bend.py", BACKGROUND, "mesh_bend bows, refusals, Undo; an arched footbridge in one program",
          Report("mesh-bend.json")),
    Suite("test_blender_join_separate.py", BACKGROUND, "Join meshes and separate parts with materials, collections and Undo",
          Report("join-separate.json")),
    Suite("test_blender_visibility.py", BACKGROUND, "Object eye and collection visibility on the bound view layer with Undo",
          Report("visibility.json")),
    Suite("test_blender_material_images.py", BACKGROUND, "Image-texture materials from loaded images, suspend-safe, with Undo",
          Report("material-images.json")),
    Suite("test_blender_curves.py", BACKGROUND, "Explicit curve points and curve/text conversion to meshes with Undo",
          Report("curves.json")),
    Suite("test_blender_operator_run.py", BACKGROUND, "Allow-listed C operators on staged copies: modes, refusals and Undo",
          Report("operator-run.json")),
    Suite("test_blender_operator_run_safety.py", BACKGROUND, "operator_run refusals, bounds and commit fidelity on real operators",
          Report("operator-run-safety.json")),
    Suite("test_blender_operator_run_families.py", BACKGROUND, "operator_run transforms, Grease Pencil, hair and text "
          "on staged copies with Undo", Report("operator-run-families.json")),
    Suite("test_blender_operator_run_objects.py", BACKGROUND, "operator_run constraint and data-local object operators "
          "with commit, rollback and Undo", Report("operator-run-objects.json")),
    Suite("test_blender_operator_run_windows.py", GUI, "Staged operators keep the stage scene with a second window open",
          Report("operator-run-windows.json")),
    Suite("test_blender_property_paths.py", BACKGROUND, "Every property_set path against installed RNA; light, camera and render writes",
          Report("property-paths.json")),
    Suite("test_blender_generic_settings.py", BACKGROUND, "Generic modifiers, constraints and nodes with read-back and Undo",
          Report("generic-settings.json")),
    Suite("test_blender_commit_names.py", BACKGROUND, "Committed edits keep data, Action, material, node-group and World names",
          Report("commit-names.json")),
    Suite("test_blender_stage_id_hazards.py", BACKGROUND,
          "Staged copies: one per live material, free suffixed names, correctable ALL_USERS refusal, leftover data",
          Report("stage-id-hazards.json")),
    Suite("test_blender_stage_id_review_fixes.py", BACKGROUND,
          "Long names never fail checks, Worlds and copies are named as committed, no private name in a refusal, "
          "new objects commit into their staged collections",
          Report("stage-id-review-fixes.json")),
    Suite("test_blender_new_object_identity.py", BACKGROUND,
          "New objects keep their collections and reported names when the artist takes their names during approval",
          Report("new-object-identity.json")),
    Suite("test_blender_previously_excluded.py", BACKGROUND,
          "Blender's hidden previously-excluded flag: exact visibility predictions and exact rollback",
          Report("previously-excluded.json")),
    Suite("test_blender_coverage_tasks.py", BACKGROUND, "Live-eval coverage task set graded without a model",
          Report("coverage-tasks.json")),
    Suite("test_blender_survey_tasks.py", BACKGROUND, "Live-eval request-survey checks, answers and fixtures graded "
          "without a model", Report("survey-tasks.json")),
    Suite("test_blender_gate_answers.py", BACKGROUND, "Gate sets at version 2: every edit and question of the long "
          "set graded without a model, on both benchmark sides' routes", Report("gate-answers.json")),
    Suite("test_blender_bones.py", BACKGROUND, "bone_set rest edits, Bone/PoseBone settings, pose commit, stale gates and Undo",
          Report("bones.json")),
    Suite("test_blender_particles.py", BACKGROUND, "Particle systems, particle_set and Distribute Points with Undo",
          Report("particles.json")),
    Suite("test_blender_anim_keys.py", BACKGROUND, "Key configure/edit, F-Curve modifiers, key copy, shared Actions and Undo",
          Report("anim-keys.json")),
    Suite("test_blender_anim_structure.py", BACKGROUND, "action_set, nla_edit and marker_edit: intents, rebuilds and Undo",
          Report("anim-structure.json")),
    Suite("test_blender_node_edit.py", BACKGROUND, "node_edit on material, Geometry Nodes and World trees with sharing and Undo",
          Report("node-edit.json")),
    Suite("test_blender_render_setup.py", BACKGROUND,
          "A physical sky, the audited Cycles settings and a whole lighting request in one program",
          Report("render-setup.json")),
    Suite("test_blender_batch.py", BACKGROUND,
          "batch: rows, positions, paths, scatter, units of parts, mirror and variation; copies check, bounds, Undo",
          Report("batch.json")),
    Suite("test_blender_subdivision_levels.py", BACKGROUND,
          "Subdivision render levels to 6 and adaptive settings: typed fields, the render ceiling, no lock-out, Undo",
          Report("subdivision-levels.json")),
    Suite("test_blender_material_sharing.py", BACKGROUND,
          "material_assign, one material per program and edits for all users: commit, Undo, Redo, rollback, stale state",
          Report("material-sharing.json")),
    Suite("test_blender_director.py", GUI, "Default Director layout and chat-only Studio tools",
          Report("director-qa.json", require_complete_phase=True), event_simulate=True, default_chat=True),
    Suite("test_blender_director_reports.py", GUI,
          "Director's top bar shows operator errors and running jobs (the status bar is hidden there), en and ar",
          Report("director-reports.json", require_complete_phase=True), event_simulate=True, default_chat=True),
    Suite("test_blender_director_navigation.py", GUI, "Director passes every viewport navigation, selection and transform input",
          Report("director-navigation.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer start-up sign-in checks; no account or inference."),
    Suite("test_blender_first_run.py", GUI, "Empty chat connects in one step, then offers example prompts",
          Report("first-run.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          first_launch=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py); no browser, account, network or inference."),
    Suite("test_blender_chat_toggle.py", GUI,
          "The top bar's show/hide chat toggle and Ctrl+Option+N: hide, show with the draft, pop-out, Director",
          Report("chat-toggle.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated keys; no CLI, account, network or inference."),
    Suite("test_blender_chat_start_states.py", GUI,
          "A chat that looks ready never has a dead click: first launch, connected elsewhere, every no-model state",
          Report("chat-start-states.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          first_launch=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude CLI (fake_provider_cli.py) signed in on the sandbox's computer; no browser, account, "
                "network or inference."),
    Suite("test_blender_settings_import.py", GUI,
          "A Blender user's settings come over: setup card, File > Defaults import and undo, a broken add-on",
          Report("fixture/settings-import.json"), marker="ANGELO_SETTINGS_IMPORT_OK", timeout=1500,
          event_simulate=True, host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS + " Windows' profile folders cannot be "
          "redirected to a sandbox (known-folder APIs), and this suite must find Blender's folder in one.",
          options_env=("ANGELO_QA_STOCK_BLENDER",),
          notes="Host-Python driver with HOME-style isolation only (no BLENDER_USER_* variable), like "
                "test_blender_app_identity: Angelo finds a sandboxed stock-Blender folder. ANGELO_QA_STOCK_BLENDER "
                "names a stock Blender to build that folder with (default: the Angelo binary, its start-up off)."),
    Suite("test_blender_first_launch_frames.py", GUI,
          "No Blender form at first launch: splash frames warm and cold, a .blend opened, second launch, Help splash, "
          "the Look control",
          Report("fixture/first-launch-frames.json"), marker="ANGELO_FIRST_LAUNCH_FRAMES_OK", timeout=1200,
          event_simulate=True, host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver: seven window processes, each with its own sandboxed profile (BLENDER_USER_* and "
                "HOME inside the QA folder), no provider CLI on PATH, --offline-mode; the showcase stays on."),
    Suite("test_blender_prefs_and_quit.py", GUI,
          "Factory/saved/template preferences keep Angelo's chat and connections; a quit passes the welcome and cards",
          Report("fixture/prefs-and-quit.json"), marker="ANGELO_PREFS_AND_QUIT_OK", timeout=900, event_simulate=True,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("darwin", "linux"),
          platform_reason=POSIX_STAND_IN_CLIS,
          notes="Host-Python driver: four window processes (prefs_quit_phases.py), each sandboxed (HOME, Blender's "
                "user folders, Angelo's state and connections inside --out) with the stand-in claude CLI; the host "
                "times each quit from the moment the receipt says it was asked for."),
    Suite("test_blender_start_panel.py", GUI, "Returning user: Angelo's start panel with recent scenes instead of Blender's splash page",
          Report("start-panel.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Makes the isolated profile a returning one before startup; no network, account or inference."),
    Suite("test_blender_canvas_states.py", GUI, "Hover, pressed, focus and disabled states on the chat canvas and welcome screen",
          Report("canvas-states.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py); no browser, account, network or inference.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_connect_hero.py", GUI, "Before a connection: no composer, a showcase over the 3D view, then a focused composer",
          Report("connect-hero.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py); no browser, account, network or inference.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_team_chat.py", GUI, "The chat follows the team account: sign-in, access reasons, keys arriving, ready on the team",
          Report("team-chat.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="In-process fake console (fake_team_console.py) and stand-in CLIs; no network, account or inference.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_update_crash.py", GUI, "Crash dialog, What's New and update notice: states, input, pixels and what is sent",
          Report("update-crash.json", require_complete_phase=True), event_simulate=True, default_chat=True, timeout=480,
          notes="Loopback feed and crash endpoint, throwaway signing key, synthetic crash logs; no network, account or inference."),
    Suite("test_blender_ui_kit_gallery.py", GUI, "Every shared UI kit component in every state, English, Arabic and narrow",
          Report("ui-kit-gallery.json", require_complete_phase=True), event_simulate=True,
          notes="A gallery card in the real card_host and synthetic input; no network, account or inference."),
    Suite("test_blender_translation.py", GUI, "Angelo's interface in ja, ar, de and zh-TW on screen, with runtime switching",
          Report("translation.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          options_env=("ANGELO_L10N_LOCALES", "ANGELO_L10N_PSEUDO"),
          notes="Meaningful on build_release_macos.sh (WITH_INTERNATIONAL and the compiled catalogues); on the "
                "development build it records why nothing was translated. ANGELO_L10N_LOCALES picks the languages "
                "(Blender locale ids), ANGELO_L10N_PSEUDO=1 the pseudo-locale.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_dispatch_fixes.py", GUI, "UI timers, Stop, expiry and recovery",
          Report("dispatch-fixes.json"), event_simulate=True),
    Suite("test_blender_edit_context_windows.py", GUI, "Staged Edit-mode kinds keep the stage scene with a second window open",
          Report("edit-context-windows.json")),
    Suite("test_blender_editor_regressions.py", GUI, "Hidden drafts, cached editors and pop-out return",
          Report("editor-regressions.json", require_complete_phase=True), notes=INSTALLED_HOST),
    Suite("test_blender_editor_types.py", GUI, "Editor menu and Python interpreter isolation",
          Report("editor-types.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_eval_live_agent_dryrun.py", GUI, "Live-eval driver against a scripted stand-in runtime",
          Report("eval-dryrun.json"), state_in_qa=True,
          notes="Runs eval_live_agent_session.py end to end; no provider process, network or account."),
    Suite("test_blender_external_cli.py", GUI, "Real angelo CLI clients against the local read-only listener",
          Report("fixture/external-cli.json"), host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          output_root=SHORT_TEMP_ROOT, timeout=420, platforms=("darwin",),
          platform_reason="The CLI transport is a Unix socket that Windows refuses to open (no named pipe yet, "
                          "external_cli.rst), and the driver runs clients with the macOS app bundle's Python.",
          notes="Host-Python driver; launches one GUI process with --enable-event-simulate itself."),
    Suite("test_blender_external_mcp.py", GUI, "A real MCP launcher process: tools, approvals, Undo steps and the cards",
          Report("fixture/external-mcp.json"), host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          output_root=SHORT_TEMP_ROOT, timeout=420, platforms=("darwin",),
          platform_reason="Angelo's MCP launcher uses the CLI's Unix socket, which Windows refuses to open (no named "
                          "pipe yet, external_mcp.rst), and the fixture runs the macOS app bundle's Python.",
          notes="Host-Python driver; launches one GUI process with --enable-event-simulate itself. No provider."),
    Suite("test_blender_fingerprint_packing.py", BACKGROUND, "Exact fingerprint coverage and stale gates",
          Report("results.json")),
    Suite("test_blender_input_capture.py", GUI, "Native Scale/Escape and canvas pointer isolation",
          Report("input-capture.json"), event_simulate=True),
    Suite("test_blender_keychain_live.py", LIVE, "Real login-Keychain round trips of throwaway test-only items",
          Report("fixture/keychain-live.json"), window=False, live_env=("ANGELO_QA_LIVE_KEYCHAIN",),
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("darwin",),
          platform_reason="Round trips through the macOS login Keychain; the driver refuses elsewhere.",
          notes="No provider or billing. Host-Python driver; launches one background app process. Writes, "
                "then deletes, fake-key generic passwords under unique test-only services in the caller's "
                "login Keychain, with prompts disallowed."),
    Suite("test_blender_legacy_migration.py", GUI, "Pre-rename preferences, keymaps, .blend data and state",
          Report("fixture/legacy-migration.json"), marker="ANGELO_LEGACY_MIGRATION_OK", timeout=480,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver; launches two window processes (--enable-event-simulate) and one background "
                "process itself, sharing one Blender config folder inside its fixture directory."),
    Suite("test_blender_memory_project_resume.py", GUI, "Saved-project brief continuity across launches",
          Report("fixture/memory-project-resume.json"), timeout=420,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), output_root=SHORT_TEMP_ROOT,
          notes="Host-Python driver; launches four GUI processes with --enable-event-simulate itself."),
    Suite("test_blender_native.py", BACKGROUND, "Executor batch, transforms and Python tool path",
          marker="ANGELO_NATIVE_TEST_OK"),
    Suite("test_blender_native_programs.py", BACKGROUND, "RNA stage isolation, bundles, stale gates, Undo",
          Report("results.json"), gui_superset=True,
          notes="With --gui it runs in a window and adds preview-pixel and preview acceptance checks."),
    Suite("test_blender_context_override_pair.py", BACKGROUND,
          "temp_override never pairs a scene with another scene's view layer: TypeError or the scene's own layer",
          Report("context-override-pair.json"), gui_superset=True,
          notes="With --gui it adds Edit mode entered with a second window open (the scene dropped mid-operator)."),
    Suite("test_blender_connect_surfaces.py", GUI, "Welcome card, showcase, welcome screen, AI Providers and its form offer "
          "every available subscription alike; Connect Claude from AI Providers",
          Report("connect-surfaces.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py): ChatGPT signed in, Claude signed out; no browser, "
                "account, network or inference."),
    Suite("test_blender_generation_connect.py", GUI, "No generation account: Media Studio, AI Enhance, flow nodes, Subflow "
          "uses, the Flow sidebar and a run attempt point to AI Providers",
          Report("generation-connect.json", require_complete_phase=True), event_simulate=True,
          notes="One in-memory fixture image key late in the suite; no provider, network or paid request."),
    Suite("test_blender_removed_id_python_api.py", BACKGROUND,
          "A removed ID given to bpy.data.user_map, file_path_map/foreach, batch_remove, libraries.write, a custom "
          "property, GeometrySet, InlineShaderNodes or a USD hook raises ReferenceError (stock 5.2 crashed); live "
          "IDs still work",
          Report("removed-id-python-api.json"),
          notes="The USD hook case needs a build with USD; without it the report records \"not built\"."),
    Suite("test_blender_native_suggestions.py", GUI, "Detected sign-ins suggested, never set up (stand-in status)",
          Report("native-suggestions.json", require_complete_phase=True), event_simulate=True,
          notes="Stand-in native status; never runs the Codex or Claude CLIs or reads a real profile."),
    Suite("test_blender_ordered_recovery.py", BACKGROUND, "Ordered scene/file recovery and large export",
          Report("ordered-recovery.json", None, env="ANGELO_ORDERED_RECOVERY_REPORT")),
    Suite("test_blender_metadata_scan.py", BACKGROUND,
          "Descriptor metadata parity, 100k-ID pages, compressed work limits and cancellation",
          Report("metadata-scan.json"), serial=True, timeout=900,
          notes="Creates 100,013 real Blender mesh IDs and streams 2 GiB of zero DATA into gzip; "
                "production-deadline refusals remain failures alongside longer diagnostic receipts."),
    Suite("test_blender_format_restore.py", BACKGROUND,
          "Image-format settings restoration and display copies",
          Report("format-restore.json")),
    Suite("test_blender_read_access.py", BACKGROUND,
          "Const mesh/image reads, unchanged Cycles storage and computed Python getter isolation",
          Report("read-access.json"),
          notes="Requires the full-feature build with real Cycles and the native read helpers; no provider or network."),
    Suite("test_blender_read_window_lifecycle.py", GUI,
          "Real WM Window removal invalidates native read capsules while the original Window survives",
          Report("read-window-lifecycle.json", require_complete_phase=True), timeout=90,
          notes="Creates and closes only fixture-owned extra windows; drives the native address-read ABI directly."),
    Suite("test_blender_read_tools.py", BACKGROUND,
          "Live data-path reads and .blend file summaries: planted facts, read-only proof, both runtimes",
          Report("read-tools.json"), timeout=420,
          notes="Scripted stand-ins for the Claude and Codex runtimes over the real providers "
                "(read_tools_stand_ins.py); no account or inference."),
    Suite("test_blender_release_features.py", BACKGROUND, "Full-build features: Cycles and OSL, OpenVDB, remesh, Bullet, "
          "file I/O round trips, FFmpeg, audio and translations (skipped per feature on the development build)",
          Report("release-features.json"),
          notes="Meaningful on build_release_macos.sh; the receipt's full_feature_build says which binary ran. "
                "No GPU kernel compile: the Metal device is only listed."),
    Suite("test_blender_program_commit_effects.py", GUI, "Staged effects, rollback and real Undo",
          Report("results.json")),
    Suite("test_blender_program_commit_file.py", GUI, "Same feature and Undo matrix through file-backed stages",
          Report("results.json")),
    Suite("test_blender_program_sculpt_undo.py", GUI, "Typed commits preserve artist Sculpt mode and individual Undo steps",
          Report("program-sculpt-undo.json")),
    Suite("test_blender_program_names.py", GUI,
          "Object names in program order, joins and conversions, by QA commit and the production Undo bridge",
          Report("program-names.json")),
    Suite("test_blender_stage_id_names.py", GUI,
          "Staged ID copies keep their live names (materials, groups, Worlds, Actions); refusals stay correctable",
          Report("stage-id-names.json")),
    Suite("test_blender_sculpt_checkpoint_bundle.py", GUI,
          "Complete shared-mesh Sculpt capture, safety, Restore and continued editing",
          Report("sculpt-checkpoint-bundle.json"), serial=True, state_in_qa=True,
          options_env=("ANGELO_SCULPT_CHECKPOINT_VARIANT",),
          notes="Fresh factory process for each shared (default), dyntopo, corrupt and post-failure variant; no provider."),
    Suite("test_blender_sculpt_checkpoint_external.py", GUI,
          "Normal artist Save and self-contained Sculpt checkpoint recovery with external multires data",
          Report("sculpt-checkpoint-external.json", require_complete_phase=True),
          serial=True, state_in_qa=True,
          notes="Fresh factory process; relocates only owned QA external data and restores it in finally; no provider."),
    Suite("test_blender_checkpoint_preview_jobs.py", GUI,
          "Ten Sculpt and ten Object Mode Restores, each with icon and material preview jobs started in the same tick",
          Report("checkpoint-preview-jobs.json", require_complete_phase=True), serial=True, state_in_qa=True,
          notes="The runner's timeout is the hang watchdog for a read joining a preview job under the GIL."),
    Suite("test_blender_checkpoint_preview_jobs_background.py", BACKGROUND,
          "Ten Restores and ten Python-started open_mainfile calls, each with preview jobs started first",
          Report("checkpoint-preview-jobs-background.json"), serial=True, needs_opengl=True,
          notes="The runner's timeout is the hang watchdog for a read joining a preview job under the GIL."),
    Suite("test_blender_sculpt_checkpoint_review.py", GUI,
          "Same Multires object in two graphs: exact Restore/Undo restore, late-failure reload, one commit step, sealed pair",
          Report("sculpt-checkpoint-review.json", require_complete_phase=True), serial=True, state_in_qa=True,
          notes="Needs a WITH_GTESTS build (native fixture seams); fresh factory process; no provider."),
    Suite("test_blender_native_checkpoint_cold_recovery.py", GUI,
          "Native checkpoint capsule, cold reader, safety and load-handler conflicts",
          Report("native-checkpoint-cold-recovery.json"), serial=True),
    Suite("test_blender_program_preview.py", GUI, "Preview pixels and artist-state restoration",
          Report("results.json")),
    Suite("test_blender_mirror_object.py", GUI, "Mirror origin identity, staged geometry, stale bindings, Undo and partial checks",
          Report("mirror-object.json", require_complete_phase=True), timeout=600,
          notes="Finite native stage/commit/Stop cases; no provider, runtime, account or network."),
    Suite("test_blender_program_stress.py", GUI, "Recorded Claude plans replayed through the live-eval driver under timing stress",
          Report("program-stress.json"), event_simulate=True, state_in_qa=True, timeout=2400,
          notes="No provider process, network or account; ANGELO_STRESS_CYCLES (default 100) x 6 tasks."),
    Suite("test_blender_provider_pool.py", GUI, "Preferences and model pool with fixture catalogs",
          Report("provider-pool.json", require_complete_phase=True)),
    Suite("test_blender_runtime_recovery.py", BACKGROUND, "Exact-binary runtime recovery regressions",
          Report("runtime-recovery.json", None)),
    Suite("test_blender_saved_keys.py", BACKGROUND, "Saved API keys through real operators with a fake Keychain",
          Report("saved-keys.json")),
    Suite("test_blender_scene_catalog_graphs.py", BACKGROUND, "Catalog geometry-node composability",
          Report("results.json", "completed")),
    Suite("test_blender_scene_queries.py", BACKGROUND, "Scene intelligence queries",
          Report("results.json", "completed")),
    Suite("test_blender_scene_query_freshness.py", BACKGROUND, "Query freshness and two-turn Noise edit",
          Report("results.json", "completed")),
    Suite("test_blender_scene_validation.py", BACKGROUND, "Deterministic verification on staged copies",
          Report("scene-validation.json")),
    Suite("test_blender_script_rollback.py", BACKGROUND,
          "A script that raised: rolled back only when the scene is as it was; Undo steps of scripts counted",
          Report("script-rollback.json")),
    Suite("test_blender_renamed_api_errors.py", BACKGROUND,
          "Errors for names Blender 4.x/5.x renamed or removed name the 5.2 replacement; same exception, no alias; "
          "unlisted names keep stock's text",
          Report("renamed-api-errors.json")),
    Suite("test_blender_sculpt.py", GUI, "Sculpt engine, Undo, offscreen pixels and snapshots",
          Report("sculpt-qa.json")),
    Suite("test_blender_sculpt_engine_fixes.py", GUI, "Stroke originals, mixed Undo, tilt and input",
          Report("sculpt-engine-fixes.json")),
    Suite("test_blender_sculpt_handles.py", GUI, "Sculpt tools by scene handle on every path (binding and edit) "
          "and refusals before an edit that say nothing ran", Report("results.json"), timeout=300),
    Suite("test_blender_sculpt_regressions.py", GUI, "Five sculpt audit reproductions",
          Report("sculpt-regressions.json")),
    Suite("test_blender_sculpt_safety_fixes.py", GUI, "Sculpt budget and view safety",
          Report("results.json", "completed")),
    Suite("test_blender_sculpt_undo_accounting.py", GUI, "Encoded-byte Undo accounting and pruning",
          Report("sculpt-undo-accounting.json"),
          extra_args=("--log", "undo,undo.sculpt", "--log-level", "debug")),
    Suite("test_blender_sculpt_workflow.py", GUI, "Sculpt programs, regions and working levels",
          Report("sculpt-workflow.json")),
    Suite("test_blender_sculpt_workflow_fixes.py", GUI, "Landmarks, silhouettes and reference progress",
          Report("sculpt-workflow-fixes.json")),
    Suite("test_blender_perf_ratchet.py", BACKGROUND, "Instruction-count ratchet: every benchmark operation within perf_ceilings.json",
          Report("fixture/perf-ratchet.json"), marker="ANGELO_PERF_RATCHET_OK", serial=True, timeout=2700,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("darwin",),
          platform_reason="Instruction counters (proc_pid_rusage) exist only on macOS; the driver fails closed elsewhere.",
          notes="Host-Python driver; runs the execution, snapshot, stage-compression, view-check, long-session, "
                "session-journal and native-read benchmarks one after another in background app processes. macOS only (instruction counters); fails closed elsewhere. "
                "After a pass, perf_ratchet.py <receipt> --stamp records the measurement."),
    Suite("test_blender_shared_sessions.py", LIVE, "Real Codex resume with a fixture Claude lead",
          Report("shared-sessions.json"), window=False, live_env=("ANGELO_QA_LIVE_CODEX",),
          notes="Sends ordinary-tier requests on the user's own ChatGPT subscription."),
    Suite("test_blender_snapshot_dedup.py", BACKGROUND, "Deduplicated snapshots: real captures, ingestion, exact restores",
          Report("snapshot-dedup.json")),
    Suite("test_blender_long_jobs.py", GUI, "Background jobs on Blender's own jobs: renders, bakes and exports with "
          "progress, cancel, restore, queue, time budget, file load and crash recovery",
          Report("long-jobs.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU renders and bakes on tiny scenes; stand-in conversation, no provider or account."),
    Suite("test_blender_job_restore.py", BACKGROUND, "What a job changes for itself is put back exactly: constrained "
          "settings, settings found by name, a bake's material node and new image, also from a file saved meanwhile",
          Report("job-restore.json")),
    Suite("test_blender_production_bakes.py", GUI, "High-to-low and cage texture bakes as jobs: the planted detail in the "
          "baked pixels, the approval, Cancel, a failure, and the selection, mode and material left exactly as they were",
          Report("production-bakes.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU bakes on a 64-pixel image; stand-in conversation, no provider or account."),
    Suite("test_blender_bake_output_safety.py", BACKGROUND, "Mixed bake recovery over native image buffers and "
          "individual compositor output suffix admission",
          Report("bake-output-safety.json"), notes="Tiny native images and compositor nodes; no render or bake job."),
    Suite("test_blender_production_renders.py", GUI, "Stereo, multi-view and File Output renders as jobs: exactly the "
          "approved files with Blender's own pixels per view and item, whole frames on cancel, paths restored",
          Report("production-renders.json", require_complete_phase=True), timeout=720,
          notes="Real Cycles CPU renders at 64 pixels; stand-in conversation, no provider or account."),
    Suite("test_blender_udim_bakes.py", GUI, "UDIM texture bakes as jobs: only the tiles the faces lie in change, every "
          "tile published with its number, a cancel reloads the saved tiles, the person's tile files never written",
          Report("udim-bakes.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU bakes on 32- and 256-pixel tiles; stand-in conversation, no provider or account."),
    Suite("test_blender_disk_simulation.py", GUI, "Simulation bakes cached on disk as jobs: a file per frame inside the "
          "job's output, read by a path from the .blend after reopening and moving the project, restored on cancel",
          Report("disk-simulation.json", require_complete_phase=True), timeout=720,
          notes="Real geometry-nodes bakes on a tiny simulation; stand-in conversation, no provider or account."),
    Suite("test_blender_job_card.py", GUI, "Background-job approval, chat card states (hover, pressed, focus, "
          "disabled), Cancel and Open clicks, notices and the status-bar item",
          Report("job-card.json", require_complete_phase=True), event_simulate=True,
          notes="Stand-in job runners in the real manager; screenshots of every card state."),
    Suite("test_blender_flows.py", GUI, "Generation Flows: node tree, captures, runs, reuse, approval and run cards with fixture providers",
          Report("flows-qa.json", require_complete_phase=True)),
    Suite("test_blender_flow_recipes.py", GUI, "Flow recipes and Subflow nodes: interface sockets, per-use choices, reuse, "
          "recipe library copy-in and staleness with fixture providers",
          Report("flow-recipes-qa.json", require_complete_phase=True)),
    Suite("test_blender_studio.py", GUI, "Studio canvas with synthetic events and fixture APIs",
          Report("studio-qa.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_studio_export_checkpoints.py", BACKGROUND, "Large Studio export snapshot round trip",
          marker="ANGELO_STUDIO_EXPORT_CHECKPOINT_OK", serial=True),
    Suite("test_blender_survey_fixes.py", GUI, "Request-survey host defects through real staging, commit and Undo",
          Report("survey-fixes.json")),
    Suite("test_blender_survey_grading_dryrun.py", GUI, "Request-survey answer, evidence and result grading against a "
          "scripted stand-in runtime", Report("survey-grading-dryrun.json"), state_in_qa=True,
          notes="Runs eval_live_agent_session.py on a short survey list; no provider process, network or account."),
    Suite("test_blender_team_library.py", GUI, "Team library and IP tools: real imports of every admitted format, "
          "safety scrub, Undo and uploads against the in-process fake console",
          Report("team-library-qa.json", require_complete_phase=True)),
    Suite("test_blender_turn_context.py", BACKGROUND, "Turn context v2 ordering, revisions and change feed",
          Report("results.json", "completed")),
    Suite("test_blender_view_motion_blur.py", GUI, "MATERIAL and LIT views of a scene with motion blur on: "
          "each view of a call equals its drawing alone, the result says blur is left out, settings unchanged",
          Report("results.json")),
    Suite("test_blender_view_observation.py", GUI, "angelo_view pixels, cache and artist-state restoration",
          Report("results.json")),
    Suite("test_blender_view_camera.py", GUI, "Free cameras equal to an equal scene camera (perspective, "
          "orthographic, straight down), reference pairs (PNG and JPEG, landscape and portrait), six pairs a call, "
          "blur, strips, WEIGHT, VALIDATION refusals, bounded memory, and every call leaving the document, "
          "viewports and data-blocks unchanged", Report("results.json"), timeout=600),
    Suite("test_blender_view_outline_shapes.py", GUI, "OUTLINE against Blender's own drawing of every kind of "
          "shape (a torus's hole, Geometry Nodes instances, dense, sparse and particle hair, a mesh beside hair, an "
          "empty hair object), DEPTH against Blender's ray cast, a 1,024 pair delivered, the GPU depth state put "
          "back, clip planes with a box out of frame and hair near the camera, every call read-only",
          Report("results.json"), timeout=300),
    Suite("test_blender_view_outline.py", GUI, "Reference comparisons checked against Blender's own drawing and "
          "projection: OUTLINE matches itself, a known move and scale give Blender's projected offset and their "
          "ratio, hair silhouettes hold every projected strand point, cut-out and busy references, OVERLAY, "
          "MATCAP and CURVATURE looks, VALIDATION refusals, every call read-only", Report("results.json"),
          timeout=300),
    Suite("test_blender_render_extensions.py", BACKGROUND,
          "Add-on or script Python a render or bake would run, found in the real app without false alarms; "
          "keyed render settings held for a job's frames; Sequencer scene strips a render reaches",
          Report("render-extensions.json"), timeout=180,
          notes="A tiny Cycles CPU render of one frame; no provider, account or network."),
    Suite("test_blender_render_samples.py", GUI,
          "Render-layer sample ceilings, restoration and actual background-job dispatch",
          Report("render-samples.json"), marker="ANGELO_RENDER_SAMPLES", timeout=180),
    Suite("test_blender_rendered_view.py", GUI, "What angelo_view shows of the scene's own lights, world and volumes "
          "against Blender's render of the same camera: the gap of the viewport shadings, RENDERED looks with "
          "EEVEE and Cycles, both runtimes' image hand-off and a finished job's image",
          Report("rendered-view.json", require_complete_phase=True), timeout=600,
          notes="Real EEVEE and Cycles (CPU) renders of tiny scenes; stand-in conversation, no provider or account."),
    Suite("test_blender_rendered_view_pixels.py", GUI, "The pixels each runtime is handed by a rendered picture: "
          "planted defects (a wrong light, a missing sky, a transparent film, an over-exposed frame, a small "
          "defect in a region), the render window's colours, OpenEXR, 16-bit and half-transparent files, one frame "
          "of an animation job, and an edit after a look",
          Report("rendered-view-pixels.json", require_complete_phase=True), timeout=600,
          notes="Real EEVEE and Cycles (CPU) renders of tiny scenes and planted picture files; no provider or account."),
    Suite("test_blender_view_wait.py", GUI, "A real render job is started, waited for inside the turn and looked at; "
          "a job too long for the turn is an answer, and Cancel and Stop do what they say",
          Report("view-wait.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles (CPU) still jobs of a slow tiny scene; stand-in conversation, no provider or account."),
    Suite("test_blender_job_output_views.py", GUI, "One output of a production job in the pixels each runtime is "
          "handed: a baked normal map and a position map without a view transform, one stereo view, one File "
          "Output item, one frame of one view, one UDIM tile, each with a planted defect, and the refusals",
          Report("job-output-views.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles (CPU) bakes and EEVEE renders of tiny scenes; stand-in conversation, no provider or account."),
    Suite("test_blender_view_sources.py", GUI, "Pictures beyond the viewport in the pixels each runtime is handed: "
          "an Image's painted, baked and data pixels, a UV layout over its texture, vertex weights and a colour "
          "attribute, the window and one editor with a planted secret greyed out, Depth, Normal and Cryptomatte "
          "passes of an F12, and a motion strip, with an unloaded image file never loaded and nothing in the "
          "scene, editors or viewport changed",
          Report("view-sources.json", require_complete_phase=True), timeout=600,
          notes="Tiny in-memory images and meshes and one small EEVEE render; stand-in conversation, no provider "
                "or account."),
    Suite("test_blender_rendered_view_safety.py", GUI, "A RENDERED look leaves the scene, settings, preferences, "
          "Render Result and files as they were, stays inside its time budget, and stops on Cancel",
          Report("rendered-view-safety.json", require_complete_phase=True), timeout=600,
          notes="Real render jobs on tiny scenes; a file load at the end. No provider or account."),
    Suite("test_blender_undo_unlinked_pointers.py", BACKGROUND, "Memfile Undo's in-place ID swap keeps the pointers "
          "lib-linking still resolves (a World lost on Redo)",
          Report("undo-unlinked-pointers.json"), platforms=("darwin",),
          platform_reason="Calls BKE_lib_id_swap_full through ctypes; only the macOS executable exports it "
                          "(Linux hides symbols with a version script, Windows exports none).",
          notes="Deterministic: builds the address coincidence the real trigger leaves to the allocator. "
                "stress_blender_world_redo.py repeats the real Redo."),
)



def by_name():
    return {suite.name: suite for suite in SUITES}
