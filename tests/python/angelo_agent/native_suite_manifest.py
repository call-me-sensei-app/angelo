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

from dataclasses import dataclass, replace
from typing import Optional, Tuple

from native_suite_other_files import EXCLUDED, HELPERS  # noqa: F401  (part of this manifest)

BACKGROUND, GUI, LIVE = "BACKGROUND", "GUI", "LIVE"
# A LIVE suite that bills a real provider declares this opt-in: the caller's evaluation-account profile
# (provider_isolation.OPT_IN; run_native_suites pins the suite's runtime profile to it).
REAL_PROVIDERS = "ANGELO_TEST_REAL_PROVIDERS"
CLASSES = (BACKGROUND, GUI, LIVE)
DEFAULT_TIMEOUT = 300
# The longest one load of Cycles' render kernels may take in a production job suite (production_jobs_harness): a
# cold Metal compile on a new Cycles version took longer than a phase (1.1 render_deadline). Such loads count against
# neither the suite's phases nor its timeout, and the runner extends its kill timeout by this much.
KERNEL_LOAD_CAP_S = 900
# The options naming a game-engine editor a suite starts. Every Angelo on the machine detects a running editor through
# the process table (engine_detect) and shows its one-time suggestion card, so such a suite runs alone (serial): run
# beside test_blender_chat_start_states, a headless Godot put "Godot is open" over its window (1.0.8 re-cut).
ENGINE_EDITOR_OPTIONS = ("ANGELO_QA_GODOT",)
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
class Variant:
    """One declared way to run a suite: ``env`` (name, value) pairs the runner sets for that run, over the caller's."""

    name: str
    env: Tuple[Tuple[str, str], ...]
    purpose: str = ""
    window: Optional[Tuple[int, int]] = None   # A GUI run's window (width, height), with the shared window flags


# The + menu's fold drawn at the windows of the common laptop screens (less the macOS menu bar): each run starts
# Blender at that size and fails if the window is not that size (test_blender_plus_menu_fold, 1.0.8).
PLUS_MENU_WINDOWS = tuple(
    Variant("w%d" % w, (("ANGELO_PLUS_MENU_WINDOW", "%dx%d" % (w, h)),), "A %d x %d window" % (w, h), (w, h))
    for w, h in ((1440, 875), (1280, 775)))

# Design A′'s rollout switch (``worker_chat_seams.enabled``): every worker-chat suite runs both ways, never pinned to
# one, so neither path loses coverage while the switch exists (doc/angelo/parallel_agents.rst).
WORKER_CHAT_HOST = (
    Variant("hosted", (("ANGELO_WORKER_CHAT_HOST", "1"),),
            "A worker chat's Controller and provider run in its worker; Angelo keeps a mirror (the default)"),
    Variant("lanes", (("ANGELO_WORKER_CHAT_HOST", "0"),),
            "The rollout fallback: the chat's Controller in Angelo, its edits on lanes to the worker"),
)


@dataclass(frozen=True)
class Suite:
    script: str
    kind: str
    purpose: str
    report: Optional[Report] = None
    marker: Optional[str] = None     # stdout completion line, for suites without a JSON receipt
    window: Optional[bool] = None    # LIVE only: whether the suite needs a window
    event_simulate: bool = False     # suite asserts or calls Window.event_simulate
    harness: Optional[bool] = None   # True: stands in for the person through C's harness scope, so it runs with
                                     # --enable-event-simulate, BACKGROUND included; False: proves the harness is
                                     # refused without it; None: never uses it (native_suite_harness checks)
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
    serial: bool = False             # documented to run alone, never beside other suites; every suite that starts
                                     # a game-engine editor (ENGINE_EDITOR_OPTIONS) is one: every Angelo on the
                                     # machine detects it and shows its suggestion card over the suite beside it
    state_in_qa: bool = False        # the suite requires its state, accounts and Blender user
                                     # resources inside ANGELO_QA_DIR, so the runner nests them there
    extra_args: Tuple[str, ...] = ()  # additional Blender arguments before --python
    # The preference drives auto-execution, so no --disable-autoexec: a command line override holds for the whole
    # session. Factory preferences start it off.
    autoexec_preference: bool = False
    timeout: int = DEFAULT_TIMEOUT
    kernel_load_cap: int = 0         # seconds of Cycles kernel loading its harness discounts (KERNEL_LOAD_CAPPED);
                                     # the runner adds them to the kill timeout
    live_env: Tuple[str, ...] = ()   # opt-in variables a LIVE suite reads from the caller
    options_env: Tuple[str, ...] = ()  # non-secret options any suite may take from the caller (languages ...)
    inputs: Tuple[str, ...] = ()     # machine inputs (native_suite_inputs.RESOLVERS): resolved by the runner into
                                     # their option variable, or the suite is skipped with the reason
    platforms: Tuple[str, ...] = ()  # sys.platform values the suite's code can run on; empty: every platform
    platform_reason: str = ""        # why it cannot run elsewhere (the runner's skip reason)
    variants: Tuple[Variant, ...] = ()  # each a separate run (``expand``), named ``<suite>@<variant>``
    variant: Optional[Variant] = None   # set on an expanded run only
    needs_opengl: bool = False       # needs a working OpenGL driver; skipped (never passed) where the runner is told
                                     # the machine has none (ANGELO_QA_NO_OPENGL), counted from a GPU machine's run
    notes: str = ""

    @property
    def name(self):
        return self.script[:-3] + ("@" + self.variant.name if self.variant is not None else "")

    @property
    def needs_window(self):
        return self.window if self.kind == LIVE else self.kind == GUI

    @property
    def host_launcher(self):
        return bool(self.host_args)

    def runs_on(self, platform):
        return not self.platforms or platform in self.platforms


def expand(suites):
    """The runs ``suites`` declare: one per variant for a suite that declares variants, else the suite itself."""
    runs = []
    for suite in suites:
        runs += [replace(suite, variants=(), variant=variant) for variant in suite.variants] or [suite]
    return tuple(runs)


INSTALLED_HOST = ("Imports the app's installed host modules (bpy.utils.system_resource), so it "
                  "validates the binary's bundled add-on, not this checkout's Python.")

SUITES = (
    Suite("test_blender_bake_auto_smooth.py", BACKGROUND,
          "Bake Auto Smooth: exact shading on held-out meshes, one evaluated mesh for every user (unselected and "
          "hidden too), each refusal changes nothing, one Undo step, Apply names it; 10,000 shared users timed",
          Report("results.json"), timeout=600),
    Suite("test_blender_stock_asset_brush_dependencies.py", BACKGROUND,
          "Load-only Brush: shared dependencies, Undo/Redo, branch pruning and rollback",
          Report("stock-asset-brush-dependencies.json", require_complete_phase=True),
          timeout=300, serial=True),

    Suite("test_blender_stock_asset_recovery_gui.py", GUI,
          "Actual context refusal, retained owner and explicit Asset Recovery sidebar action",
          Report("stock-asset-recovery-gui.json", require_complete_phase=True),
          timeout=240, serial=True),

    Suite("test_blender_stock_asset_undo.py", BACKGROUND,
          "Seven blend asset modes and GLB: dirty Before, one Undo/Redo, restore and save/reopen",
          Report("stock-asset-undo.json", require_complete_phase=True), timeout=600, serial=True),

    Suite("test_blender_report_untranslated.py", BACKGROUND,
          "Report.message_untranslated in a Japanese interface: a plain report's message ID, a %s report's message ID "
          "filled in English, empty for text translated before the report call and when nothing was translated",
          Report("report-untranslated.json"), timeout=120,
          notes="Saves and reopens one small .blend in its QA folder; a Python render engine raises translated and English errors."),

    Suite("test_blender_external_visibility.py", BACKGROUND,
          "MCP visibility readers on real bpy: every window-state section, Edit Mode counts, scene cameras and orbits "
          "as free cameras that angelo_view accepts", marker="EXTERNAL VISIBILITY ON BPY: OK", timeout=120),

    Suite("test_blender_external_state.py", BACKGROUND,
          "angelo_state's tool section on real RNA in every context mode of the workspace-tool enum (an object of "
          "each kind in each mode): Blender's own context mode, nothing unavailable, each paint mode's brush with "
          "the size its own paint settings apply", marker="EXTERNAL STATE ON BPY: OK", timeout=180),

    Suite("test_blender_script_kit.py", BACKGROUND,
          "The reviewed-script kit: exact solids, cuts that refuse what they cannot prove, rows, self-checks",
          Report("results.json")),
    Suite("test_blender_script_kit_reviews.py", BACKGROUND,
          "The reviewed-script kit, its adversarial reviews' cases: refusals, reached parts, slots, checks, cutters",
          Report("results.json")),
    Suite("test_blender_script_kit_support.py", BACKGROUND,
          "The kit's support check through the scene: decks, tables, shelves, walls, instances, real floats",
          Report("results.json")),
    Suite("test_blender_engine_preflight.py", BACKGROUND,
          "The engine preflight in Blender: its typed fixes through angelo_engine_fix's host route (the finding gone, "
          "one Undo step for all), the stale-fix refusal, and .glb and .fbx round trips that find units, leaf bones, "
          "dropped weights and local paths and leave nothing in the file",
          Report("results.json"), timeout=240, notes="No provider, account or network."),
    Suite("test_blender_script_booleans.py", BACKGROUND,
          "A script's Booleans measured where the executor runs it: applied and live rows, the facts at the extremes, "
          "bpy.ops put back, and the kit's crowded and retried cuts proving the volume they removed",
          Report("script-booleans.json"), timeout=240, notes="No provider, account or network.", harness=True),
    Suite("test_blender_script_operator_time.py", BACKGROUND,
          "Where a script's time went in bpy.ops, through the executor: real calls counted and named, the cost fact, "
          "a failed script, the Boolean observer stacked, a kept callable, bpy.ops put back",
          Report("script-operator-time.json"), timeout=240, notes="No provider, account or network.", harness=True),
    Suite("test_blender_script_parent_inverse.py", BACKGROUND,
          "A parent-inverse statement reads its parent's current matrix: the recorded car, fresh, keyed, many parts",
          Report("results.json")),
    Suite("test_blender_stage_weight_settings.py", BACKGROUND,
          "A staged vertex group operator reads the person's Auto Normalize and Weight, compared with Blender directly",
          Report("results.json")),
    Suite("test_blender_compositor_stack.py", BACKGROUND,
          "The compositor effects a render runs: stack order, flags keyed, driven or on an NLA strip, paths by name, "
          "Render Layers in a group", Report("results.json")),
    Suite("test_blender_gpu_subdivision_status.py", BACKGROUND,
          "GPU subdivision status: NO_GPU before the GPU starts, then GPU, NOT_LAST, SPLIT_NORMALS and "
          "NOT_EVALUATED on held-out meshes and stacks, copied to the original, reset when a file is read",
          Report("results.json"), needs_opengl=True),
    Suite("test_blender_grant_inventory.py", BACKGROUND,
          "The grant inventory against the live operator registry and C's denied-operator list",
          Report("results.json")),
    Suite("test_blender_script_api_removed.py", BACKGROUND,
          "The removed-API table against Blender's own RNA: removed names absent, replacements present",
          Report("results.json")),
    Suite("test_blender_select_through.py", BACKGROUND,
          "Select Through: off by default, saved per viewport, offered where it changes selection, no default key",
          Report("results.json")),
    Suite("test_blender_game_ready_check.py", BACKGROUND,
          "The game-ready check: nothing loaded while unused, the bundled meshoptimizer found and checked, bad "
          "arguments refused without a crash, native kernels equal to the brute-force reference, seams kept, Decimate "
          "equal to the modifier, every fact and the scene unchanged through the host route, skinned meshes",
          Report("results.json"), timeout=300, notes="No provider, account or network."),
    Suite("test_blender_tension_map.py", BACKGROUND,
          "Tension modifier: closed-form strain on stretched, squashed and sheared grids, shape-key and captured rest "
          "shapes, an armature bend, placement after the deform-only modifiers with Rest Position, refused built-in "
          "names, Cycles reading the attribute, save/reload, overlay defaults, the agent's generic settings",
          Report("results.json")),
    Suite("test_blender_select_through_draw.py", BACKGROUND,
          "Select Through's drawing: hidden selected elements shown only where they are, no other pixel changes, "
          "X-ray unchanged, edit-mesh drawing untouched; draw time off/on",
          Report("results.json"), needs_opengl=True),
    Suite("test_blender_uv_checker_draw.py", BACKGROUND,
          "Viewport UV checker: defaults by versioning, every pattern drawn with no data-block made or saved, UV map "
          "choice and missing maps, live Edit Mode edits, UDIM, texel density, stretch, flipped, selected only, "
          "X-ray; draw time off/on on a heavy scene",
          Report("results.json"), needs_opengl=True),
    Suite("test_blender_color_grid_threads.py", BACKGROUND,
          "Color Grid images made at once on Cycles' loading threads (stamping on) equal one made alone: the labels "
          "never share a font across threads; crashed in blf_font_draw_buffer before the fix",
          Report("results.json"), timeout=600),
    Suite("test_blender_color_grid_font_reload.py", BACKGROUND,
          "The first Color Grid of a session made on Cycles' threads while another thread reloads the interface fonts: "
          "its labels' font is private before it is published, so every grid has its labels and equals one made alone",
          Report("results.json"), timeout=600),
    Suite("test_blender_voxel_remesh_guard.py", BACKGROUND,
          "Voxel remesh guard: too small a size is refused at once from scripts, Sculpt Mode and the modifier, "
          "a fitting size runs near the estimate, a new modifier's size follows the object",
          Report("results.json"), timeout=420, notes="Runs one remesh of up to 10 million vertices (several GB)."),
    Suite("test_blender_rigid_body_teardown.py", BACKGROUND,
          "Rigid bodies joined by a constraint are freed in any order: Undo and Redo across a simulation, loading a "
          "file, an object of a constraint or of a body removed while the world lists it, two scenes that both "
          "simulate; each order in its own process with freed memory filled where the platform can",
          Report("results.json"), timeout=900,
          notes="Five child processes of the binary. No provider, account or network."),
    Suite("test_blender_path_without_segment.py", BACKGROUND,
          "A path whose points sit at one place: text on it keeps a finite, straight layout and texture space; "
          "Spline IK and Follow Path on it pose finitely; text on a real path still bends",
          Report("results.json"), notes="No provider, account or network."),
    Suite("test_blender_ptcache_ownerless.py", BACKGROUND,
          "A rigid body world's point cache (no owning object) from scripts: its disk cache stays in memory and "
          "writes nothing, name/index/external and the ptcache operators are safe; an object's disk cache is unchanged",
          Report("results.json"), options_env=("ANGELO_PTCACHE_CASE",), notes="No provider, account or network."),
    Suite("test_blender_evaluation_repeatable.py", BACKGROUND,
          "Re-evaluating an unchanged object gives the same bits wherever the heap puts its elements: two meshes "
          "under 25 modifier and geometry-node evaluators, Bevel's UV merge among them",
          Report("results.json"), notes="No provider, account or network."),
    Suite("test_blender_bmesh_operator_order.py", BACKGROUND,
          "BMesh operators give one element order across four processes: those that iterate a mapping slot "
          "(extrude region and edges, spin, solidify, the UV sphere, bmesh.ops map results) or free and split "
          "from a pointer set (bevel then triangulate, an uneven grid fill, Weld Edges into Faces), and a Float "
          "boolean with a UV sphere cutter (stock 5.2 differed every run)",
          Report("results.json"), timeout=600,
          notes="Four child processes of the binary. No provider, account or network."),
    Suite("test_blender_metal_safe_list_exit.py", BACKGROUND,
          "Metal's buffer pool releases a safe-free list's chained chunks at exit: 24,576 GPU buffers freed just "
          "before quitting leave no unfreed memory",
          Report("results.json"), needs_opengl=True,   # gpu.init(): skipped where no GPU driver is (as every such suite).
          notes="Metal only; another backend writes a note and passes."),
    Suite("test_blender_upstream_relations.py", BACKGROUND,
          "ViewLayer.angelo_upstream from Python: the (status, records) tuple, records as session UID, ID type "
          "and node type, NO_GRAPH, RELATIONS_STALE then OK, UNDECLARED_READS, NOT_MAIN_THREAD, an evaluated copy "
          "refused; a generated Rigify rig and a mesh skinned to it, custom shapes never upstream, cost per call",
          Report("results.json"), timeout=600, notes="Enables the bundled Rigify; no provider, account or network."),
    Suite("test_blender_depsgraph_update_recalc.py", BACKGROUND,
          "DepsgraphUpdate.angelo_recalc from real updates: selection reports SELECT, a location edit TRANSFORM, "
          "a material setting SHADING, and no reported update has an empty set",
          Report("results.json"), notes="No provider, account or network."),
    Suite("test_blender_upstream_fix_backports.py", BACKGROUND,
          "Blender fixes taken from upstream main ahead of the base release: an imported OBJ has an active vertex "
          "group, a USD prim that resets the transform stack ignores its ancestors, a Geometry Nodes bake that "
          "cannot write its files reports an error, and a kept Cycles session renders what a new one does after "
          "the denoiser's guiding passes change (on the CPU, and with the GPU denoisers where the machine has a "
          "Cycles GPU device)",
          Report("results.json"), timeout=1200,   # A GPU machine loads Cycles' kernels for its device first.
          notes="Renders 64 to 96 pixel pictures in Cycles, on the CPU and on a GPU device when there is one. No "
          "provider, account or network."),
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
    Suite("test_blender_gpu_subdivision_status_gui.py", GUI,
          "GPU subdivision status in the Modifiers panel: plain, Smooth by Angle after it, before it; screenshots "
          "and edit-and-redraw times per state",
          Report("gpu-subdivision-status-gui.json", require_complete_phase=True)),
    Suite("test_blender_subdiv_uploads.py", GUI,
          "Subdivision loose geometry, edit/paint overlays and exact viewport pixels",
          Report("results.json"), serial=True),
    Suite("test_blender_action_audit_fixes.py", BACKGROUND, "Production-route action audit regressions",
          Report("action-audit-fixes.json", "all_passed")),
    Suite("test_blender_program_material_slots.py", BACKGROUND, "A material appended after a step that changed "
          "its object's slots is refused clearly; a staging failure is a correctable refusal",
          Report("program-material-slots.json", "all_passed")),
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
    Suite("test_blender_keymap_event_types.py", BACKGROUND,
          "No key-map item takes the input method's composition events (Event.type only): the key-map editor's "
          "list, KeyMapItem.type of every map type and keymap_items.new refuse them",
          Report("keymap-event-types.json")),
    Suite("test_blender_api_providers.py", GUI, "Provider settings, forms and picker with synthetic health",
          Report("api-providers.json", require_complete_phase=True), harness=True),
    Suite("test_blender_api_tls.py", LIVE, "Unauthenticated HTTPS probes of real provider endpoints",
          Report("api-tls.json", env="ANGELO_PROVIDER_QA_REPORT"), window=False,
          notes="Network access to third-party provider endpoints; no keys or billing."),
    Suite("test_blender_app_identity.py", GUI, "Default user folders, bundle identity, Help menu, About and splash",
          Report("fixture/app-identity.json"), marker="ANGELO_APP_IDENTITY_OK", timeout=420, event_simulate=True,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver; launches one background and one window process itself with only HOME-style "
                "isolation (no BLENDER_USER_* override) so the product's real default folders are exercised."),
    Suite("test_blender_godot_link_e2e.py", BACKGROUND,
          "Godot live link end to end: a .glb and a .blend asset through Angelo -b and a headless Godot editor",
          Report("fixture/report.json"), host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          inputs=("godot",), options_env=("ANGELO_QA_GODOT",), serial=True, platforms=("darwin",),
          platform_reason="The harness writes Godot's macOS editor-settings path; the plugin itself is cross-platform.",
          timeout=600, notes="Host-Python driver; needs Godot 4.4+ (ANGELO_QA_GODOT)."),
    Suite("test_blender_godot_link_busy.py", BACKGROUND,
          "Godot live link while Godot imports: saves in a burst, during a slow import, across a link switch, and past "
          "the re-import give-up, through Angelo -b and a headless Godot editor",
          Report("fixture/report.json"), host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          inputs=("godot",), options_env=("ANGELO_QA_GODOT",), serial=True, platforms=("darwin",),
          platform_reason="The harness writes Godot's macOS editor-settings path; the plugin itself is cross-platform.",
          timeout=480, notes="Host-Python driver; needs Godot 4.4+ (ANGELO_QA_GODOT)."),
    Suite("test_blender_crash_gpu_card.py", GUI,
          "Crash dialog after a crash in the graphics driver: Restart with OpenGL on Vulkan, nothing on Metal",
          Report("crash-gpu-card.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_extension_row_drawers.py", GUI,
          "Extensions list in a window with an add-on's 5.2-shaped row drawer (BlenderKit, #166392)",
          Report("extension-row-drawers.json", require_complete_phase=True)),
    Suite("test_blender_game_physics_gui.py", GUI,
          "Game Physics in a window: the Physics tab's panel, the + menu row and its popup, Edit Fitted Size",
          Report("game-physics-gui.json", require_complete_phase=True)),
    Suite("test_blender_godot_physics_e2e.py", BACKGROUND,
          "Game physics end to end: held-out models marked in Angelo and imported by real Godot editors, headless; "
          ".blend files through Angelo as Godot's Blender",
          Report("fixture/report.json"), host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          inputs=("godot", "physics_heldout"), options_env=("ANGELO_QA_GODOT", "ANGELO_QA_PHYSICS_ASSETS"),
          serial=True, platforms=("darwin",),
          platform_reason="The harness writes Godot's macOS editor-settings path for the .blend imports.",
          timeout=900, notes="Host-Python driver; needs Godot 4.4+ editors (ANGELO_QA_GODOT, several joined by ':'; "
                             "default /Applications/Godot.app) and held-out .glb models (ANGELO_QA_PHYSICS_ASSETS; "
                             "default: the pinned set, fetched into the runner's cache by native_suite_inputs)."),
    Suite("test_blender_godot_link_gui.py", GUI,
          "Godot live link in Director: + menu, request card variants, Open, status bar, live edit to a headless Godot",
          Report("godot-link-gui.json", require_complete_phase=True), default_chat=True,
          inputs=("godot",), options_env=("ANGELO_QA_GODOT",), serial=True, platforms=("darwin",),
          platform_reason="The harness writes Godot's macOS editor-settings path; the plugin itself is cross-platform.",
          timeout=420, harness=True),
    Suite("test_blender_engine_suggest_gui.py", GUI,
          "Feature discovery in Director: + menu engine rows, a real headless Godot detected, the one-time suggestion "
          "card, the agent's note and FEATURES lookup, angelo_app_feature, Unity/Unreal plugin installs, the picker",
          Report("engine-suggest-gui.json", require_complete_phase=True), default_chat=True,
          inputs=("godot",), options_env=("ANGELO_QA_GODOT",), serial=True, platforms=("darwin",),
          platform_reason="Detects a real Godot editor through macOS's process table (Windows' reader is host-tested).",
          timeout=420, harness=True, event_simulate=True),
    Suite("test_blender_about_card.py", GUI, "About Angelo card from the Angelo menu: text, links, Close; en, ar, large scale",
          Report("about-card.json", require_complete_phase=True), default_chat=True),
    Suite("test_blender_custom_endpoints.py", GUI,
          "An organization's model server: listed from the console, the key sheet, Test connection against a local "
          "fake server, ready with the untested label, seat removed and given back, sign-out forgets the key",
          Report("custom-endpoints.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_team_account.py", GUI,
          "Team account: status-bar item and menu, agent usage report, AI Providers, managed policy",
          Report("team-account.json", require_complete_phase=True)),
    Suite("test_blender_select_through_gui.py", GUI,
          "Select Through: box, lasso and circle reach what X-ray reaches in edit mesh, the paint vertex mask and "
          "Particle Edit, clicks stay visible-only, a user key toggles it; header and angle screenshots",
          Report("select-through-gui.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_select_through_director_gui.py", GUI,
          "Select Through in Director: Tab, a box, Z's shading pie to turn it on, the box reaching the hidden cube, "
          "Ctrl+Z / Ctrl+Shift+Z, the pie to turn it off; window events only",
          Report("select-through-director.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer start-up sign-in checks; no account or inference.",
          harness=True),
    Suite("test_blender_uv_checker_director_gui.py", GUI,
          "Viewport UV checker in Director: F3 turns it on, the + menu's row opens its card, clicks choose patterns, "
          "repeats, objects and a named UV map, Edit Mode stays live, heavy-scene draw time off/on, F3 turns it off",
          Report("uv-checker-director.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=600, platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer start-up sign-in checks; no account or inference.",
          harness=True),
    Suite("test_blender_voxel_remesh_guard_gui.py", GUI,
          "Voxel remesh guard dialogs from Sculpt Mode: refusal (Return cancels), large (Escape keeps, Return "
          "remeshes), fitting (no dialog); screenshots",
          Report("voxel-remesh-guard-gui.json", require_complete_phase=True), event_simulate=True, timeout=600),
    Suite("test_blender_engine_skeleton_gui.py", GUI,
          "Rig check card in Director: the + menu's row opens it, clicks switch Unreal, Unity and Godot and the file "
          "format, open More problems, Notes and Details, Copy Report, Check Again after a fix, close",
          Report("engine-skeleton-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=300, platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer start-up sign-in checks; no account or inference.",
          harness=True),
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
          notes="Adversarial reproductions; fails while a recorded fault reproduces. No provider or network.",
          harness=True),
    Suite("test_blender_engine_fix_gui.py", GUI,
          "The engine fix in a window: its approval card, the Approve click, one Ctrl+Z restoring every fix, redo",
          Report("engine-fix-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude CLI (fake_provider_cli.py); no account, network or inference."),
    Suite("test_blender_chat_cards.py", GUI, "Plain approval cards, limit/offline notices, voice visibility and snapshot text",
          Report("chat-cards.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer a turn with a limit result; no account or inference."),
    Suite("test_blender_download_card.py", GUI,
          "angelo_download's card in Ask and in Auto (host, link, size cap, folder), a real Approve, the file in "
          "assets/downloads, cloud metadata and a page refused with no file",
          Report("download-card-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude CLI (fake_provider_cli.py); steps requested as a provider's MCP call requests them; "
                "a stand-in network for download_fetch only; no account, network or inference.", harness=True),
    Suite("test_blender_approval_wait.py", GUI,
          "Waiting for approval is the card: Auto's why-line, nothing says it waits after decline, expiry or approve, "
          "the pill and its double-click shield",
          Report("approval-wait-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude CLI (fake_provider_cli.py) for the connection; steps requested as a provider's MCP "
                "call requests them; the app's own tool pump; no account, network or inference.", harness=True),
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
    Suite("test_blender_steering.py", GUI,
          "Messages sent while a Claude turn runs: a steer read at the next round, three queued (removed, edited, "
          "switched) sent as one request, a model chosen meanwhile taking over after the turn, Stop pausing until Send",
          Report("steering.json", require_complete_phase=True), timeout=480, event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="The stand-in claude CLI (fake_provider_cli.py) plays scripted, held rounds; no account, network or "
                "inference.", harness=True),
    Suite("test_blender_steering_codex.py", GUI,
          "Steering a Codex turn: a native turn/steer when no call is outstanding, read before the reply naming it; "
          "a steer waits while an approval holds the step and approves nothing",
          Report("steering-codex.json", require_complete_phase=True), timeout=420, event_simulate=True,
          default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="The stand-in codex app-server (fake_provider_cli.py) holds its call turns and keeps turn/steer; "
                "no account, network or inference."),
    Suite("test_blender_chat_history.py", GUI,
          "10,000 activities: wheel through grouped/withdrawn pages to first/latest, append and resize",
          Report("chat-history.json", require_complete_phase=True), timeout=420, event_simulate=True,
          default_chat=True, notes="Synthetic transcript only; no provider/account/network."),
    Suite("test_blender_chat_scroll_caret.py", GUI,
          "A trackpad swipe moves the transcript as Blender's Outliner; caret, click and typing land on the drawn "
          "glyphs (wrapped, accented, Japanese, emoji) at three Resolution Scales",
          Report("chat-scroll-caret.json", require_complete_phase=True), timeout=420, event_simulate=True,
          default_chat=True, notes="Transcript rows set directly; no provider, account or network."),
    Suite("test_blender_composer_gui.py", GUI,
          "A long draft scrolls in the composer (wheel, trackpad, scrollbar, click, typing, drag-select, paste; 1x, "
          "1.5x, Arabic) and never the transcript; every text-field key of this platform's table, double and "
          "triple clicks, the draft's own Undo, and the keys after a keyconfig preset reload",
          Report("composer-gui.json", require_complete_phase=True), timeout=600, event_simulate=True,
          default_chat=True, notes="Transcript rows set directly; no provider, account or network."),
    Suite("test_blender_composer_ime_gui.py", GUI,
          "An input method types into the composer: the session and candidate window at the drawn caret, the "
          "composition inline over the selection, its keys never editing the draft, the commit with one Undo, and "
          "the session through the pointer leaving, a menu and Escape",
          Report("composer-ime-gui.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, platforms=("darwin", "win32"),
          platform_reason="Blender's input method support (WM_CAPABILITY_INPUT_IME) is macOS and Windows; "
                          "X11 has none.",
          notes="Compositions through Window.ime_simulate (WM_event_IME_apply); no provider, account or network."),
    Suite("test_blender_confinement_attacks.py", BACKGROUND,
          "Every confinement attack (aliases, private paths, programs, native code, the network, unhooking, work "
          "left for later) against the C confinement in the app; the guard module exposes no state or settable "
          "attribute", Report("confinement-attacks.json"), timeout=300),
    Suite("test_blender_confinement_escapes.py", BACKGROUND,
          "Nothing a script leaves behind runs unconfined (every trigger x carrier: destructors, the read-back, "
          "finalizers, weak-reference and gc callbacks, class attributes, module names and registries, held "
          "objects); class attributes and forged definitions are put back; SQLite, load-time texts and held "
          "descriptors leak nothing; ordinary code still runs", Report("confinement-escapes.json"), timeout=300),
    Suite("test_blender_confinement_writers.py", BACKGROUND,
          "Blender's writers judged by the data their paths are built from: a script's output path, view suffix, "
          "material or cache name, File Output folder, image or text file, or a project it wrote and appended never "
          "makes a render, bake, save or unpack write over the person's file; a person's render or bake of their own "
          "data writes everything while scripts run", Report("confinement-writers.json"), timeout=900,
          needs_opengl=True),   # It renders and composites: a GPU path may be taken (Windows build4 has no GPU).
    Suite("test_blender_guard_diag.py", BACKGROUND,
          "A diagnostic build without the agent-script confinement (the guard's cost-bisect switch) refuses every "
          "agent script with a text that says why; any other build runs an admitted one",
          marker="ANGELO_GUARD_DIAG_OK", timeout=120),
    Suite("test_blender_confinement_pipeline.py", BACKGROUND,
          "Pipeline libraries (USD, OpenImageIO, OpenEXR, OpenVDB, MaterialX, OpenColorIO, OpenTimelineIO, OSL "
          "queries, Audaspace, Mantaflow) work on a confined script's own files; a private path named directly is "
          "refused, one reached through composition, XIncludes, LUT search paths or packaging is never read; USD's "
          "resolver is the confined one; every file entry point is classified",
          Report("confinement-pipeline.json"), timeout=420),
    Suite("test_blender_confinement_usd_unconfined.py", BACKGROUND,
          "When USD's resolver is not the confined one, a confined script reaches no USD (pxr modules Angelo "
          "loaded, Blender's USD operators, cache files, Hydra) while Angelo's own USD works; another primary "
          "resolver plugin in the environment never displaces the confined one",
          Report("confinement-usd-unconfined.json"), timeout=420,
          variants=(Variant("no_plugin_resolver", (("PXR_AR_DISABLE_PLUGIN_RESOLVER", "1"),),
                            "USD keeps its default resolver, as when the confined resolver's plugin is missing"),)),
    Suite("test_blender_script_confinement.py", BACKGROUND,
          "Agent-script confinement in the app: operators that open programs, install code, save preferences, "
          "grant or write private or startup files are refused; timers, draw handlers, handlers, driver functions "
          "and load-time texts a script leaves are confined; Angelo keeps its access; cost measured",
          Report("script-confinement.json"), timeout=300),
    Suite("test_blender_runtime_symlink.py", BACKGROUND,
          "A CLI runtime reached through a symbolic link (an installer's ~/.local/bin/claude -> versions/X), on PATH or "
          "as the Preferences pick, still starts after an agent script ran; a link retargeted later is refused; a "
          "refused start says so, never \"too old\"",
          Report("runtime-symlink.json"), timeout=120, notes="Stand-in CLIs; no provider, account or network."),
    Suite("test_blender_update_confined.py", BACKGROUND,
          "The self-update's files against a confined script: an offer whose version a script changed in place to "
          "name Angelo's state removes nothing when withdrawn, a script writes nothing in the updates folder, and "
          "Angelo still writes in a version's folder",
          Report("update-confined.json"), timeout=300, notes="No provider, account or network."),
    Suite("test_blender_trusted_scripts.py", GUI,
          "Trusted scripts: exact review runs once, chat consent runs automatically, revoked or Read-only never runs",
          Report("trusted-scripts.json", require_complete_phase=True), timeout=120,
          notes="A stand-in conversation through the real admission, approval operator, executor and snapshots; "
                "no provider or account.", harness=True),
    Suite("test_blender_trusted_scripts_consent.py", GUI,
          "The trusted-scripts consent dialog opened by a real click on the Approval Policy menu's item (a script's "
          "invoke refused; default off, Escape cancels, Enter grants, the item again revokes), then a hosted worker "
          "chat's approval cards approved, declined and stale in the window",
          Report("trusted-consent-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=120, serial=True,
          notes="A real pointer press on the menu item (its C ticket) and synthetic keys on the real dialog, then "
                "(hosted variant) a worker chat's cards clicked in the "
                "window: a typed edit approved runs in the worker, a script's card is shown and declined so it never "
                "runs, a stale card is refused. The stand-in codex (hosted_codex_stand_in.py) plays the model; no "
                "account or inference. The lanes variant records the hosted cases skipped.",
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_grant_surfaces.py", GUI,
          "Grants by the person's real presses, end to end: Auto from the composer's popover, a script's Review, a "
          "connection's key saved in the AI Providers sheet; each refused first from a script",
          Report("grant-surfaces.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=240, serial=True,
          notes="The coordinator's 2.0.0 condition: suites testing these forms stand in for the person through the "
                "harness, so this one covers the real path (C's press tickets). No provider, account or network."),
    Suite("test_blender_writers_typed_gui.py", GUI,
          "A folder the person typed for one writer is that writer's only: typed into a File Output node's folder "
          "field, it lets that node's files be written after a script tagged the node, and never a render a script "
          "pointed into it", Report("writers-typed.json", require_complete_phase=True), event_simulate=True,
          timeout=300, serial=True,
          notes="Piece 10's re-read of f9f24cc6ffd, MUST-C: typed records need the interface. Cycles on the CPU, a "
                "CPU composite; no provider, account or network."),
    Suite("test_blender_button_arming.py", GUI,
          "Blender's own buttons arm for a grant only while the person's move put the pointer on them where they are "
          "drawn now: armed by a real move, disarmed by leaving and by a scroll that moves them on screen",
          Report("button-arming.json", require_complete_phase=True), event_simulate=True, timeout=150, serial=True,
          notes="The reviewer's read of 6392a6f (its SHOULD): a button in the Preferences window, read through "
                "_angelo_jobs.button_arming. No provider, account or network."),
    Suite("test_blender_pointer_warp.py", GUI,
          "Code never aims the person's click: a press where code warped the pointer grants nothing until the person "
          "moves out and back in; an agent script can't warp, simulate input or open a menu under the pointer",
          Report("pointer-warp.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=180, serial=True,
          notes="The independent review of 9c8b4e57077 (its MUST): the composer's approval popover's Approve for me "
                "row, opened by the person's press. No provider, account or network."),
    Suite("test_blender_status_bar_buttons.py", GUI,
          "A GUI proof finds a button the status bar draws (a window's global area, no screen's): the waiting app's "
          "notice is listed with its place in the bottom bar",
          Report("status-bar-buttons.json", require_complete_phase=True), event_simulate=True, timeout=120,
          notes="The 2.0.1 candidate proofs: every status-bar notice proof had fallen back to opening the chat. No "
                "provider, account or network."),
    Suite("test_blender_fast_press.py", GUI,
          "A press before a grant control armed does nothing, quietly: the control shows a hint, no error shows, and "
          "a small move inside it then a press grants; on the chat canvas and on Blender's own menu item",
          Report("fast-press.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=180, serial=True,
          notes="The coordinator's ruling on the 0.3 s arm (2026-11-16): the approval popover's Approve for me row and "
                "the Approval Policy menu's Trust scripts item. No provider, account or network."),
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
          notes="The stand-in claude CLI (fake_provider_cli.py) streams the timed plan at Opus 5.5's recorded pace "
                "and holds every other plan fragment by fragment until the suite has seen it; imports the add-on "
                "from the checkout; no account or inference."),
    Suite("test_blender_script_stream.py", GUI,
          "A script being written under the live row: its newest lines and count as written; refused or stopped "
          "leaves nothing; no key; the main thread keeps ticking",
          Report("script-stream.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=300,
          options_env=("ANGELO_SCRIPT_STREAM_TIMING",),
          notes="The stand-in claude CLI (fake_provider_cli.py) streams a script plan at Opus 5.5's recorded pace; "
                "imports the add-on from the checkout; no account, inference or script run."),
    Suite("test_blender_hidden_wait.py", GUI,
          "Hidden approval launcher and other-chat pointer, exact request reveals and opening input shields",
          Report("hidden-wait.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=120, serial=True,
          notes="Synthetic pending production ToolCalls, no tool queue or provider; actual window events and "
                "screenshots plus separately tagged native placement contracts. Independent pixel review required."),
    Suite("test_blender_chat_waiting.py", GUI,
          "The chat's marble wait: carved while Claude plans, tapped by real progress, revealed when the plan "
          "arrives; frames capped and stopped with the turn; Reduce Motion; draw cost and pixels",
          Report("chat-waiting.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=300, serial=True,
          options_env=("ANGELO_QA_WAIT_EXTRA_SHARE",),   # The loop-rate check's positive control; never in a round.
          notes="The stand-in claude CLI (fake_provider_cli.py) streams thinking lines and a castle plan at "
                "Opus 5.5's recorded pace, held at each moment the suite observes until it has seen it "
                "(fake_provider_cli.hold); imports the add-on from the checkout; no account or inference."),
    Suite("test_blender_everyday_turns.py", GUI,
          "Whole turns with batch, also, Subdivision, a sky and Cycles: built, edited again, verified and undone",
          Report("everyday-turns.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=480,
          notes="The stand-in claude CLI (fake_provider_cli.py) plays rounds the suite scripts; no account or inference."),
    Suite("test_blender_guard_tickets.py", GUI, "Grant tickets only from an aimed press on a region Angelo "
          "registered: never within the shield, from keys or keymap items, on overlapping or vanished regions",
          Report("guard-tickets.json"), event_simulate=True),
    Suite("test_blender_chat_hit_testing.py", GUI, "Scissored approval/preview controls and composer input",
          Report("chat-hit-testing.json"), event_simulate=True),
    Suite("test_blender_chosen_tools.py", GUI, "The composer's + menu and Start from a base mesh: menu, chip, fit, "
          "the Send step, every base mesh timed, the staged path", Report("chosen-tools.json", require_complete_phase=True)),
    Suite("test_blender_shot_video_gui.py", GUI, "Make Video from This Shot from the + menu in Director: the row, the "
          "click's three render jobs, the chat row and library frames after, settings put back, screenshots",
          Report("shot-video-gui.json", require_complete_phase=True), default_chat=True, timeout=480, harness=True,
          event_simulate=True),
    Suite("test_blender_chat_popovers.py", GUI, "Native GPU/modal model picker",
          Report("chat-popovers.json", require_complete_phase=True), harness=True),
    Suite("test_blender_chat_usage.py", GUI, "The usage bar and its card: API key, plan windows, Arabic, draw cost",
          Report("chat-usage.json", require_complete_phase=True), options_env=("ANGELO_QA_USAGE_SKIP",),
          notes="Receipts and plan windows are fixture data; no provider calls."),
    Suite("test_blender_checkpoint_restore_ui.py", GUI, "Scene recovery, injected faults and Undo Restore UI",
          Report("checkpoint-restore-ui.json"), event_simulate=True, serial=True),
    Suite("test_blender_restore_pixels.py", BACKGROUND,
          "Restore and Undo restore name the unsaved image pixel edits no snapshot holds, and the image lacks them",
          Report("restore-pixels.json")),
    Suite("test_blender_checkpoints.py", BACKGROUND, "Native snapshot capture/restore of scene and files",
          marker="ANGELO_CHECKPOINT_TEST_OK", harness=True),
    Suite("test_blender_restore_keeps_file.py", BACKGROUND,
          "After Restore and Undo restore the person's own file is the document, saved with its path style",
          Report("restore-keeps-file.json")),
    Suite("test_blender_restore_keeps_file_gui.py", GUI,
          "After a Restore: the title bar, the Save question, the close dialog, the update card and the quit prompt",
          Report("restore-keeps-file-gui.json", require_complete_phase=True), event_simulate=True, serial=True),
    Suite("test_blender_update_restart_settle.py", GUI,
          "Restart to Update's hand-over with Blender's real save timing, Director: Save hands over once with the "
          "file read as saved and its stamp still matching later; Don't Save hands over once with a matching stamp; "
          "an edit right after the save never hands over and says Angelo did not restart",
          Report("update-restart-settle.json", require_complete_phase=True), default_chat=True, timeout=300,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="launch is a recorder: no installer runs and Angelo does not quit. Stand-in claude/codex CLIs "
                "(fake_provider_cli.py) answer start-up sign-in checks; no account or inference."),
    Suite("test_blender_compiled_core.py", BACKGROUND, "Compiled RNA, editor buffers and save/load",
          Report("compiled-core.json")),
    Suite("test_blender_compiled_gui.py", GUI, "Compiled editor picker, bootstrap, docking and drops",
          Report("compiled-gui.json", require_complete_phase=True), event_simulate=True,
          notes=INSTALLED_HOST),
    Suite("test_blender_chat_usage_live.py", LIVE, "The usage bar, card and live token count on two short real turns",
          Report("chat-usage-live.json"), window=True, live_env=("ANGELO_QA_LIVE", "ANGELO_QA_USAGE_CONNECTIONS", REAL_PROVIDERS),
          notes="Two read-only turns on the Claude subscription in a dedicated connection store; refuses any other."),
    Suite("test_blender_cross_provider_live.py", LIVE, "Real Codex and Claude handoff and delegation",
          Report("cross-provider-live.json"), window=False,
          live_env=("ANGELO_QA_LIVE_BOTH", "ANGELO_QA_CLAUDE_STATE_DIR", "ANGELO_QA_CLAUDE_CONNECTION_ID", REAL_PROVIDERS),
          notes="Bills the evaluation-account Codex profile (ANGELO_TEST_REAL_PROVIDERS) and the Claude test "
                "connection."),
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
          extra_args=("--threads", "2"), timeout=960, harness=True),
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
          "Later material edits, unkeyed edits kept, in-program sharing refused early, key frame, UV map, drivers, "
          "copies as Shift+D's",
          Report("kept-values.json")),
    Suite("test_blender_commit_orphans.py", BACKGROUND,
          "Data a commit's swaps replaced is freed (Undo/Redo restore it); shared, faked, pointed-at, linked kept",
          Report("commit-orphans.json")),
    Suite("test_blender_commit_orphans_production.py", BACKGROUND,
          "The production commit (program_undo with the native bridge) frees what its swaps replaced, to a fixed "
          "point; Undo/Redo; driver-held and deleted data kept and listed",
          Report("commit-orphans-production.json"), harness=True),
    Suite("test_blender_driver_isolation.py", BACKGROUND,
          "Drivers: a Python-defined destination and reads outside the stage refuse before staging evaluates",
          Report("driver-isolation.json")),
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
    Suite("test_blender_game_physics.py", BACKGROUND,
          "Game physics: settings audit, property_set with Undo, the OMI export tree, round-trip import, Godot "
          "suffixes, findings and the web merge", Report("game-physics.json")),
    Suite("test_blender_world.py", BACKGROUND, "world_configure: background, environment image, shared Worlds and Undo",
          Report("world.json")),
    Suite("test_blender_web_world_key.py", BACKGROUND,
          "The web World picture's key against every shader node type: each setting, nested data and value changes "
          "it, editor-only edits keep it, what can't be read is never reused after an edit",
          Report("results.json"), notes="No render, provider, account or network."),
    Suite("test_blender_web_preview_key.py", BACKGROUND,
          "The live web preview's key against every writable scene, render and colour-management setting: each "
          "edit that changes the web export schedules a re-export, a selection doesn't; the key's cost",
          Report("results.json"), timeout=240, notes="Writes temporary files only; no provider, account or network."),
    Suite("test_blender_web_preview_bake.py", BACKGROUND,
          "A live preview export made while the World's picture (a baked Sky, an HDRI) is being made carries the "
          "picture the page shows, marked pending, never no light",
          Report("results.json"), timeout=300, notes="Bakes one Sky twice in a background Angelo; no provider, "
          "account or network."),
    Suite("test_blender_web_link_delta.py", BACKGROUND,
          "The web link's live frames with the real glTF exporter: unique stable keys (instances, bones), a one-object "
          "change exports that object alone, digests that move only with what the page shows (shape keys, lights, "
          "painted images), stubs for known images, removals, and full against delta export times",
          Report("web-link-delta.json"), notes="Writes temporary files only; no provider, account or network."),
    Suite("test_blender_web_link_changes.py", BACKGROUND,
          "What the web link's live frames count as changed: pixel writes with no depsgraph update (the native "
          "pixel revision, also while a check waits), data whose users only the depsgraph knows, and rewritten "
          "tile, frame and renamed files reach the page on their own",
          Report("web-link-changes.json"), notes="Writes temporary files only; no provider, account or network."),
    Suite("test_blender_web_link_cost.py", BACKGROUND,
          "What a web-link delta costs to decide as geometry-nodes instances grow (10k, 100k, 1M) beside a typical "
          "scene: flat, never a walk of every instance",
          Report("web-link-cost.json"), timeout=600,
          notes="Writes temporary files only; no provider, account or network. Times are relative to each other."),
    Suite("test_blender_image_source_revision.py", BACKGROUND,
          "Image.angelo_source_revision: changes on reload, a new path, packing and unpacking, and a file changed "
          "on disk read again after its buffers were freed; never on reading, re-reading an unchanged source, "
          "freeing buffers, undo or a pixel edit; values never repeat",
          Report("image-source-revision.json"), notes="Writes temporary files only; no provider, account or network."),
    Suite("test_blender_engine_link_frame_changes.py", BACKGROUND,
          "The engine link schedules a live export after edits that end in a frame change, a frame change of an "
          "animated scene, and an untagged edit the executor reports; a selection schedules none",
          Report("results.json"), notes="No provider, account or network."),
    Suite("test_blender_engine_link_key.py", BACKGROUND,
          "The engine link's live key against every writable scene and render setting, for each engine's live glTF "
          "options: each edit that changes the live .glb schedules a live export, a selection doesn't; the key's cost",
          Report("results.json"), timeout=300, notes="Writes temporary files only; no provider, account or network."),
    Suite("test_blender_web_export_gltfpack.py", BACKGROUND,
          "The web export's KTX2 route through a gltfpack stand-in: flags, the look and World picture put back "
          "after gltfpack, the budget, and the WebP fallback",
          Report("results.json"), timeout=240, notes="Writes temporary files only; no provider, account or network."),
    Suite("test_blender_visibility_gaps.py", BACKGROUND,
          "Frame-free revisions across a frame change, where typed programs end (Math, mesh limits, leftovers "
          "a save writes) and script ids, in the compiled app",
          Report("visibility-gaps.json"), timeout=240,
          notes="Saves and reopens one temporary file; no provider, account or network.", harness=True),
    Suite("test_blender_world_light.py", BACKGROUND,
          "The World's sun and brightness facts against Cycles renders and Blender's own save: the bearing "
          "convention, the view-transform reading, the measured sky table, results and the settings read",
          Report("world-light.json"), timeout=240,
          notes="Tiny Cycles CPU renders; no provider, account or network."),
    Suite("test_blender_rc10_gaps.py", BACKGROUND,
          "Revisions the host left out (read-backs, small revisions, the scene settings), "
          "rule-based selection up to the mesh limit and the facts typed results carry",
          Report("rc10-gaps.json"), timeout=240, notes="No provider, account or network.", harness=True),
    Suite("test_blender_route_parity.py", BACKGROUND,
          "Route parity for data on fresh results (a script, checked and preview programs, a light, the World, "
          "lookups): every field reaches every route or a registered filter drops it",
          Report("route-parity.json"), timeout=240, options_env=("ANGELO_ROUTE_PARITY_RECORD",),
          notes="No provider, account or network. ANGELO_ROUTE_PARITY_RECORD=<folder> also writes the fresh results "
                "as a corpus file (route_parity_corpus.py's format) to refresh the host suite's corpus.", harness=True),
    Suite("test_blender_motion_readback.py", BACKGROUND,
          "Where animated objects are at a few frames: held-out rigs against Blender's own evaluation, the scene "
          "put back, results of scripts, programs and angelo_scene_object frames, not-measured cases, cost",
          Report("motion-readback.json"), timeout=240, notes="Bakes one small cloth cache; no provider or network.",
          harness=True),
    Suite("test_blender_motion_guards.py", BACKGROUND,
          "The motion read's guards: negative frames, simulation zones, static hair, bone and deform parents, "
          "scope, unevaluated objects, instancers, budgets, Python handlers, revisions, shape keys, wall time",
          Report("motion-guards.json"), timeout=300, notes="No provider or network.", harness=True),
    Suite("test_blender_motion_candidates.py", BACKGROUND,
          "What a motion read finds and states past its caps: edits past the change list, rig curves, drivers and "
          "NLA strips; particle and vertex instances; a null box past the instance cap; Pose mode; budgets",
          Report("motion-candidates.json"), timeout=300, notes="No provider or network.", harness=True),
    Suite("test_blender_motion_interface.py", GUI,
          "A motion read in the interface: unkeyed edits outlive Blender's notifiers; no frame subscriber runs",
          Report("motion-interface.json", require_complete_phase=True), notes="No provider or network."),
    Suite("test_blender_script_renames.py", BACKGROUND,
          "Names Blender gave a script's data-blocks in place of the names asked for, against Blender 5.2's own "
          "naming: assigned names, appends, imports and overrides, the cap, the read limit and the costs",
          Report("script-renames.json"), timeout=300, notes="No provider, account or network.", harness=True),
    Suite("test_blender_script_api_notes.py", BACKGROUND,
          "Renamed 4.x names a script's result notes, checked against 5.2, and what a real World shows",
          Report("script-api-notes.json")),
    Suite("test_blender_modifier_stack.py", BACKGROUND, "New modifier types, stack remove/move/apply, rollback and Undo",
          Report("modifier-stack.json")),
    Suite("test_blender_modifier_rollback_inputs.py", BACKGROUND,
          "A removed modifier comes back exactly on rollback: 5.3 Geometry Nodes inputs and every owned store",
          Report("modifier-rollback-inputs.json")),
    Suite("test_blender_object_management.py", BACKGROUND, "Rename, duplicate, delete, apply transforms, origin, collections",
          Report("object-management.json")),
    Suite("test_blender_custom_normals_transform.py", BACKGROUND,
          "Custom normals survive the typed apply, Mesh.transform() with flip_normals() and Blender's Apply",
          Report("custom-normals.json"), needs_opengl=True),   # A Workbench render of the shading before and after.
    Suite("test_blender_color_config.py", BACKGROUND,
          "Colour-space writes under the OCIO configurations a 5.3 Project can switch to (ACES 1.3, 2.0)",
          Report("color-config.json"), timeout=900),
    Suite("test_blender_animation_edits.py", BACKGROUND, "Key delete/configure, shape keys, NLA push-down, rollback and Undo",
          Report("animation-edits.json")),
    Suite("test_blender_revision_deformers.py", BACKGROUND,
          "Revisions follow every deformer object (RNA-pinned pointer set) across frames",
          Report("revision-deformers.json")),
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
    Suite("test_blender_stage_path_getters.py", BACKGROUND,
          "Six actual RNA callback guards: getter, setter, transform and named-collection lookup stay untouched",
          Report("results.json"), timeout=120,
          notes="Requires the reviewed native RNA ABI; actual six-case counts and zero failures/errors/skips/soft passes are recorded."),
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
    Suite("test_blender_program_bone_depth.py", BACKGROUND, "Programs on deep hierarchies (humanoid rigs with fingers, "
          "long chains, nested bone collections and node frames) bind, commit and undo; links equal the old walk's",
          Report("bone-depth.json"), timeout=900),
    Suite("test_blender_program_struct_views.py", BACKGROUND, "Programs see every RNA view of their inputs (collision, "
          "display, field) and pose bones' and bones' ID properties; link-free collections; fingerprint timings",
          Report("struct-views.json"), timeout=900),
    Suite("test_blender_program_read_inputs.py", BACKGROUND, "Input admission: a mesh shared with an object in Edit "
          "Mode or a dynamic-topology Sculpt session is refused at bind and at commit, naming that object",
          Report("read-inputs.json")),
    Suite("test_blender_engine_skeleton.py", BACKGROUND, "The engine-skeleton check through angelo_skeleton_check's "
          "host route: Rigify's metarig and generated rig against Unreal, Unity and Godot (roles by layout, the DEF- "
          "prefix Unreal keeps), a skinned rig's influences, constraint, driver, scale and A-pose, the refusals, "
          "and nothing in the scene changed", Report("engine-skeleton.json"), notes="No provider, account or network."),
    Suite("test_blender_rig_tools.py", BACKGROUND, "Hand-rigging tools on skinned metaball hands: detection, palm "
          "normals, skinning against Blender's, fused fingers, weight cuts, the fold-to-limit fist applied through a "
          "scene program and read back", Report("rig-tools.json"), gui_superset=True,
          notes="With --gui it adds the picture tools: hand views, a fist picture and a range sheet."),
    Suite("test_blender_hand_inherit.py", BACKGROUND, "Hand tools' predicted poses against Blender's evaluated pose "
          "for every bone inheritance setting, the refusals, and (--timing) the fist with unrelated vertices",
          Report("hand-inherit.json")),
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
    Suite("test_blender_parallel_workers.py", BACKGROUND,
          "Worker Blenders: four chats each build a held-out part in their own worker through RemoteExecutor; "
          "snapshots, approvals, refusals, saved parts and a worker that dies",
          Report("parallel-workers.json"), timeout=600, needs_opengl=True,
          variants=WORKER_CHAT_HOST, harness=True),   # A worker's picture draws.
    Suite("test_blender_worker_python.py", BACKGROUND,
          "Worker Python: a worker chat's script under the lead's rules, run in its worker Blender: the card and "
          "exact-code review, one worker Undo step, the saved part; Auto, trusted, Always allow and the lead's "
          "inherited trust; stale, forged and file-loading scripts refused; a hidden load contained; save-as; "
          "raises; Angelo's main thread never waits; other traffic while a card waits",
          Report("worker-python.json"), timeout=600, needs_opengl=True,
          variants=WORKER_CHAT_HOST, harness=True),   # Workers draw, as parallel_workers.
    Suite("test_blender_live_view.py", BACKGROUND,
          "Live views of worker parts: private Mains read after each worker save, drawn offscreen, never in a saved "
          "main file; the input hold; navigation; tiles, progress and merged state; the worker's own size",
          Report("live-view.json"), timeout=600, needs_opengl=True,
          variants=WORKER_CHAT_HOST, harness=True),   # gpu.init() and offscreen draws.
    Suite("test_blender_object_bounds.py", BACKGROUND,
          "Every size reader (Blender's bounds and dimensions, each instance's box, the live part's size, Angelo's "
          "script bounds, per-turn dimensions and framing) against held-out geometry's own truth",
          Report("object-bounds.json"), timeout=120),
    Suite("test_blender_gn_radius_bounds.py", BACKGROUND,
          "Each evaluated component's radius counts in an object's size where it draws, on every original type "
          "(mesh, curve, text, surface, empty, curves, point cloud, volume, Grease Pencil): points and strokes keep "
          "it, curves only on a Curves object; joins and two radii, exact min/max in every reader",
          Report("gn-radius-bounds.json"), timeout=120),
    Suite("test_blender_rail_main_view.py", BACKGROUND,
          "The Main tile's picture as ruled (R3-M2): the scene camera, then the artist's view, each only if the "
          "geometry fills FILL of its grid of cells, else the geometry bounds, decided afresh at each redraw, drawn "
          "and saved on held-out scenes at millimetre to kilometre scale; the choice's cost in decision pictures at "
          "2,000 and 8,000 objects and ~200k instances",
          Report("rail-main-view.json"), timeout=300, needs_opengl=True),   # gpu offscreen draws.
    Suite("test_blender_view_warm.py", BACKGROUND,
          "The native half of an agent view's shader wait: shader_state counts only the materials EEVEE asks for "
          "(not a Bounds mesh's, a point cloud's second slot, or slots under a material override); a compile-ahead "
          "draw queues them; draw(only=) leaves every object drawn and visible, after a refusal and a re-evaluation",
          Report("view-warm.json"), timeout=180, needs_opengl=True,   # gpu offscreen draws.
          # It times a compile-ahead draw against a synchronous render: a GPU shader compiler's behaviour, which a
          # software rasterizer turns around (llvmpipe: a steady 0.40 where a GPU gives under a third).
          inputs=("hardware_gpu",), options_env=("ANGELO_QA_GPU_RENDERER",)),
    Suite("test_blender_compile_ahead.py", BACKGROUND,
          "Compile-ahead draws prepare a view off the main thread (inventory C5): a first draw of new materials "
          "queues their Metal pipeline states instead of building them, they build in the background and the view "
          "builds none; a new LIT world is queued, never compiled in the draw, on every backend; the view then shows "
          "each material and the world in its own colour; deleted materials and a quit with builds pending are safe",
          Report("compile-ahead.json"), timeout=240, needs_opengl=True),   # gpu offscreen draws.
    Suite("test_blender_compile_ahead_window.py", GUI,
          "A world a compile-ahead draw queued is waited for, never stood in for, by the person's viewport, "
          "render.opengl and an F12 render that meet it still queued: each waits for a shader and shows the world",
          Report("compile-ahead-window.json"), timeout=240,
          # It reads the viewport from a screenshot of the window, which a software OpenGL window on a virtual display
          # gives black: skipped there with that reason, by what the window can do, never by platform.
          inputs=("window_capture",), options_env=("ANGELO_QA_WINDOW_CAPTURE",)),
    Suite("test_blender_parallel_below_bar.py", BACKGROUND,
          "Below the bar (a background Angelo, unforced): the machine check fails, no worker tool is declared, no "
          "rail, live view, hold or timer is registered, nothing GPU-related is made, and Settings says what it needs",
          Report("parallel-below-bar.json"), timeout=120,
          variants=WORKER_CHAT_HOST),
    Suite("test_blender_parallel_lead.py", BACKGROUND,
          "The lead's angelo_workers_start through Angelo's executor: two real workers with chats, live scenes, "
          "tiles and briefs; the person's chat stays active; status; refusals",
          Report("parallel-lead.json"), timeout=300,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_worker_pool.py", BACKGROUND,
          "Warm workers: starts that take a worker launched ahead against cold starts (start to brief); one worker "
          "process builds two parts, the second with a new chat and its own number, nothing of the first in it, "
          "both merging; untaken workers stop after their idle time and at a turn's end",
          Report("worker-pool.json"), timeout=600,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_parallel_merge.py", BACKGROUND,
          "The lead's angelo_workers_merge: four held-out parts from real workers placed into its scene; linked "
          "copies with their hierarchy, modifiers and a per-placement material variant; one Undo step; refusals "
          "change nothing; an ordinary saved file",
          Report("parallel-merge.json"), timeout=600,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_parallel_merge_materials.py", BACKGROUND,
          "Per-placement material swaps reach every material a copy shows: slots by their evaluated material, a "
          "modifier's own material, Geometry Nodes inputs and Set or Replace Material in nested groups; each copy "
          "checked before and after; a swap a node reads, or instancing a placement cannot own, refused with "
          "nothing added",
          Report("parallel-merge-materials.json"), timeout=600,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_parallel_merge_privacy.py", BACKGROUND,
          "Nothing of a worker's own reaches the person's file through a merge: its Scene, the part file's Library, "
          "weak references and paths into Angelo's private folders, checked in the saved file's bytes (saved and "
          "unsaved lead, after Undo and Redo); private images, fonts and sounds packed, the person's own texture kept",
          Report("parallel-merge-privacy.json"), timeout=300, harness=True),
    Suite("test_blender_redo_packed_linked_tracking.py", BACKGROUND,
          "Redo after an Undo of packed linked data that only the person's linked data uses never crashes (fixed "
          "for 2.0.0): red if the packed case or its unpacked control crashes again",
          Report("fixture/report.json"), timeout=900,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver; two --background children with Angelo's add-on disabled, each with its own HOME "
                "and temporary folder outside the suite's (a crash log, from a regression, is kept renamed)."),
    Suite("test_blender_parallel_merge_instances.py", BACKGROUND,
          "Per-placement material swaps checked on everything a placement shows: collection instances, nested ones "
          "included, and particle instances; a swap every placement makes merges (Geometry Nodes or slot materials, "
          "a kit-bash part, one placement owning its instancer); a disabled Boolean and a particle member weighted "
          "zero merge; a swap reaching, or reached by, another placement that does not make the same swap (instances, "
          "Boolean operands, node references, render-only sources), a slot swap on a material list evaluation builds, "
          "and instancing nested deeper than the check reads refused by name with nothing added",
          Report("parallel-merge-instances.json"), timeout=600,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_parallel_resume.py", BACKGROUND,
          "The worker-done event: a lead idle while its real worker builds is continued once its worker ends, with "
          "the parts not merged into its scene, its row Angelo's; then merges; never during its own turn, after its "
          "Stop, or in Ask",
          Report("parallel-resume.json"), timeout=600,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_parallel_scene_switch.py", BACKGROUND,
          "Selecting a session only changes what is displayed: tile clicks change no job state; another scene in the "
          "window invalidates nothing, workers build and the queue starts, a merge never lands there and the lead's "
          "tile brings its scene back; Restore (also after scenes swapped names, and its Undo after a rename), a "
          "deleted-then-undone scene and two leads keep each chat on its own scene; a staged preview and an approval "
          "survive a look away; only a real load stops the job",
          Report("parallel-scene-switch.json"), timeout=900, needs_opengl=True,
          variants=WORKER_CHAT_HOST, harness=True),   # gpu.init(): S7's preview.
    Suite("test_blender_parallel_restore.py", BACKGROUND,
          "A merged part stays merged through Angelo's Restore (in this scene, mergeable nowhere, a second merge "
          "refused) and an Undo of its merge still takes it out: the merged collection records its part file itself",
          Report("parallel-restore.json"), timeout=300,
          variants=WORKER_CHAT_HOST),
    Suite("test_blender_concurrent_edit.py", BACKGROUND,
          "3,072 bounded lead/worker edits: independent phase manifests, actual mutation overlap, protected "
          "sentinels, merge Undo/Redo and save/reopen; fixed stand-in provider, no paid calls",
          Report("concurrent-edit.json"), timeout=900, serial=True, state_in_qa=True,
          options_env=("ANGELO_CONCURRENT_EDIT_MODE",),
          notes="Mode serial or parallel; missing parallel mutation overlap is incomplete on the hosted variant, "
                "reported and not required on the lanes. Raw headless timings only.",
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_parallel_away.py", BACKGROUND,
          "A worker chat keeps working while the lead's script holds Blender's main thread: its calls are answered "
          "during the script, the script completes with its own results, and nothing touches bpy off the main thread",
          Report("parallel-away.json"), timeout=300,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_worker_chat_hosted.py", BACKGROUND,
          "Design A': worker chats hosted in real worker Blenders, played by the stand-in codex through Angelo's "
          "runtime service and relay: dispatch, python consent through the mirror, merge hold and part lease, "
          "scene-switch reopen with resume, pool reuse, a worker crash mid-call, Stop on a busy worker, a consent "
          "revocation",
          Report("worker-chat-hosted.json"), timeout=900, variants=WORKER_CHAT_HOST,
          notes="The lanes variant records skipped: the other worker suites' lanes variants cover that path.",
          harness=True),
    Suite("test_blender_parallel_placement.py", BACKGROUND,
          "How a merge writes each placed root: every rotation form kept, partial keys that would land a copy in "
          "neither pose warned, skews, evaluated and driven roots, a bone child, a part whose worker died; part files "
          "merged through the lead chat, no workers",
          Report("parallel-placement.json"), timeout=300,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_session_rail.py", GUI,
          "The session rail and live views with real events: tiles with pictures, switching, orbit, held edits, "
          "framing, inspect, an approval on a tile, back to the main view, a saved file without libraries",
          Report("session-rail.json"), event_simulate=True, timeout=420,
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_worker_brief_person.py", GUI,
          "Design A': a real worker started by the lead in Director Mode; its first message is its part, then the "
          "person's own messages (never the lead's reply), shown in the chat area when its tile is clicked",
          Report("worker-brief-person.json"), event_simulate=True, timeout=300, variants=WORKER_CHAT_HOST,
          notes="Stand-in model, no provider or paid call. The lanes variant records skipped: it has no mirror.",
          harness=True),
    Suite("test_blender_worker_approval_lead.py", GUI,
          "Design A': a worker's script approval in Auto shown on its rail tile in Director Mode, answered from the "
          "lead as the benchmark's tester answers it (no chat area drew the card) and by a person's clicks on the "
          "tile and the card; each runs in the worker within seconds",
          Report("worker-approval-lead.json"), event_simulate=True, timeout=420, variants=WORKER_CHAT_HOST,
          notes="Stand-in model, no provider or paid call. The lanes variant records skipped: it has no mirror.",
          harness=True),
    Suite("test_blender_rail_crowd.py", GUI,
          "The rail at 16 workers at 1x and 2x (pages with Main first, hidden counted, one column wide, the wheel "
          "scrolls, a switch shows a hidden worker) and the Main picture as ruled (the scene camera, else the artist's "
          "view when it sees geometry, else framed on the geometry) or empty",
          Report("rail-crowd.json"), event_simulate=True, timeout=300),
    Suite("test_blender_rail_positions.py", GUI,
          "The Sessions panel left, top and bottom and minimized, in Director with its chat and in Manual, with 1, 3 "
          "and 16 workers at 1x and 2x (16 also at 1.5x, 3x, a short view and an open shelf): clear of the headers, "
          "toolbar, sidebar, gizmo, shelf and live-view label; paging, wheel, swipe and +N more; a tile click shows "
          "that worker and changes no job; the round button's place, count, approval mark and restore; both kept in "
          "the preferences",
          Report("rail-positions.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=1200, notes="Model-free: stand-in workers, no provider, account or network. Saves its isolated profile's "
                "preferences once and reads them back in a new window Angelo, behind, and a background one."),
    Suite("test_blender_window_pixels_gui.py", GUI,
          "The lead's capture (live_view.window_pixels) shows every region as the screen does: 17 editors with their "
          "sidebars and headers, every Properties tab, every Preferences section and the Director top bar, two "
          "captures in a row each, then the agent's own screenshot with stand-in workers on the rail",
          Report("window-pixels-gui.json", require_complete_phase=True), default_chat=True, timeout=600,
          notes="Model-free: stand-in workers, no provider, account or network."),
    Suite("test_blender_parallel_setting.py", GUI,
          "Workers at once on AI Providers with real clicks: Suggested follows the machine, a number sets admission's "
          "count and draws chosen, more than suggested says what it may cost, the start tool keeps its launch count",
          Report("parallel-setting.json"), event_simulate=True, timeout=240,
          variants=WORKER_CHAT_HOST),
    Suite("test_blender_batch.py", BACKGROUND,
          "batch: rows, positions, paths, scatter, units of parts, mirror and variation; copies check, bounds, Undo",
          Report("batch.json")),
    Suite("test_blender_subdivision_levels.py", BACKGROUND,
          "Subdivision render levels to 6 and adaptive settings: typed fields, the render ceiling, no lock-out, Undo",
          Report("subdivision-levels.json")),
    Suite("test_blender_material_sharing.py", BACKGROUND,
          "material_assign, one material per program and edits for all users: commit, Undo, Redo, rollback, stale state",
          Report("material-sharing.json")),
    Suite("test_blender_undo_unrecorded_change.py", GUI, "Undo and Redo after changes no undo step recorded (data-blocks "
          "added, removed, relinked or linked in from a script or add-on): no crash, the recorded step undone, every "
          "object, collection and view layer consistent, the outliner drawn, a chained Undo still reusing IDs",
          Report("undo-unrecorded-change.json", require_complete_phase=True), event_simulate=True, timeout=1200,
          options_env=("ANGELO_UNDO_CASES", "ANGELO_UNDO_HEAVY_OBJECTS")),   # Per case alone; heavy-scene size.
    Suite("test_blender_rna_none_pointer_params.py", GUI,
          "None for a pointer parameter whose C function dereferences it (fileselect_add, the invoke popups, "
          "collection unlink, keymap matching, pose from action, render engine camera calls, every node type's poll, "
          "UILayout node templates and more) or an omitted pie event raises a TypeError in its own process, never "
          "crashes; None clears an active track or layer group; a slotless pose action applies nothing; each family "
          "with a real value returns",
          Report("fixture/rna-none-pointer-params.json", require_complete_phase=True), timeout=900,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver; one Blender process per case, background or a window (UILayout, node editor)."),
    Suite("test_blender_upstream_fix_guards.py", GUI, "Upstream picks' guards: compositor node and sequencer strip "
          "moves with no active object, Grease Pencil Draw-mode transform of the active object only, Project Edit "
          "with a canvas image that cannot load, then the viewer lock used twenty times",
          Report("upstream-fix-guards.json"), timeout=300),
    Suite("test_blender_undo_unrecorded_modes.py", GUI, "Undo and Redo across Edit, Sculpt and Vertex Paint mode "
          "boundaries with changes no undo step recorded: the scene stays consistent after every key",
          Report("undo-unrecorded-modes.json", require_complete_phase=True), event_simulate=True, timeout=420),
    Suite("test_blender_director.py", GUI, "Default Director layout and chat-only Studio tools",
          Report("director-qa.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          harness=True),
    Suite("test_blender_director_reports.py", GUI,
          "Director's top bar shows operator errors and running jobs (the status bar is hidden there), en and ar",
          Report("director-reports.json", require_complete_phase=True), event_simulate=True, default_chat=True),
    Suite("test_blender_director_navigation.py", GUI, "Director passes every viewport navigation, selection and transform input",
          Report("director-navigation.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py) answer start-up sign-in checks; no account or inference.",
          harness=True),
    Suite("test_blender_update_quit_cancel_gui.py", GUI,
          "Updater-owned delayed quit: invalid descriptor refuses; explicit revoke, rejected preflight, "
          "capsule collection and synchronous requesting screen replacement each release writer and ordinary input",
          Report("update-quit-cancel-gui.json", require_complete_phase=True), event_simulate=True, timeout=60,
          notes="No installer, positive grant, browser, login or provider; actual compiled private updater hook required."),
    Suite("test_blender_change_chip_details.py", GUI,
          "Actual rendered Changes chip mouse click, popup screenshots and Escape preserve fixture scene and chat",
          Report("change-chip-details.json", require_complete_phase=True), event_simulate=True, timeout=120,
          platforms=("darwin",), platform_reason="Reviewed Mac-only isolated native mouse/screenshot fixture; Windows remains separately qualified",
          notes="Structural result only; all39 names, legibility and Escape dismissal require independent review of three saved PNGs. No product callback patches or providers."),
    Suite("test_blender_signin_reconnect.py", GUI,
          "Expired sign-in notice clicks reconnect the exact profile with progress, Cancel and retry; Your team opens its actual owner",
          Report("signin-reconnect.json", require_complete_phase=True), timeout=150,
          event_simulate=True, default_chat=True, platforms=("darwin",),
          platform_reason="Reviewed Mac-only offline fixture; actual Windows UI/account login remain separate",
          notes="Final auth launch/browser intercepted; real owned team loopback listener is canceled without a code, no HTTP/provider call."),
    Suite("test_blender_first_run.py", GUI, "Empty chat connects in one step, then offers example prompts",
          Report("first-run.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          first_launch=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py); no browser, account, network or inference."),
    Suite("test_blender_chat_toggle.py", GUI,
          "The top bar's show/hide chat toggle and Ctrl+Option+N: hide, show with the draft, Show/Hide twice keeps "
          "the 3D view at UI scale 1 and 2, pop-out, Director",
          Report("chat-toggle.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated keys; no CLI, account, network or inference."),
    Suite("test_blender_mode_basics.py", GUI,
          "Every mode's basics: Director keeps Blender's menus, Save and Undo keys in both modes, the Chat button in "
          "both modes, the chat following workspace tabs",
          Report("mode-basics.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated keys; saves a temporary .blend; no CLI, account, network or inference."),
    Suite("test_blender_chat_launcher.py", GUI,
          "Angelo in the 3D view's corner while the chat is hidden: placement and pixels, tooltip, click opens the chat, "
          "scene clicks pass, Director, Manual, another tab, UI scale 2 (a click docks the chat there), Reduce Motion, "
          "draw cost, an editor type change moving it with the watch stopped",
          Report("chat-launcher.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_feedback_gui.py", GUI,
          "Feedback in Director: the + menu's Report a bug / Request a feature rows (real clicks) and Help's entries "
          "open the feedback form with the details in the link's fragment; About has no donation link; Japanese",
          Report("feedback-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; the opened link is recorded, no browser, account or network."),
    Suite("test_blender_plus_menu_fold.py", GUI,
          "The chat bar's + menu in Director: every feature in view with nothing scrolled, drawn in English, German, "
          "Japanese and Swedish, and laid out with the app's fonts at 1440x875 and 1280x775 windows in all 21 languages",
          Report("plus-menu-fold.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          variants=PLUS_MENU_WINDOWS,
          notes="Real default startup and simulated input; drawn at each variant's window (checked), and also laid out "
                "by the draw's own code for both sizes from that window's chat proportions (1.0.8)."),
    Suite("test_blender_plus_menu_audit.py", GUI,
          "Every state of the chat bar's + menu in Director, captured and checked in English, German, Japanese, "
          "Swedish and Arabic: the top level, keyboard focus, every group's page scrolled to its top, middle and end, "
          "a chosen base mesh with its fit toggle, search with and without results, a one-column chat",
          Report("plus-menu-audit.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          variants=PLUS_MENU_WINDOWS, timeout=400,
          notes="No text crosses from one band of the menu into another (qa_plus_menu.crossings); captures are for "
                "people to look at (1.0.8 audit after two menu defects reached cuts)."),
    Suite("test_blender_studio_discoverability.py", GUI,
          "Media Studio from Director's Studio button (real clicks, own window, layout kept), the Angelo menu in Manual "
          "mode, and the welcome's card (fits in English, German and French; a click opens it)",
          Report("studio-discoverability.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_studio_tabs.py", GUI,
          "Manual's Media Studio and Generation Flows tabs: added once in order, Studio and Flows switch to them and "
          "back (pixels: full area, no stock header, the Flows start page), the strip in en/de at scale 1 and 2, and a "
          "deleted tab stays deleted after the file is saved and opened again",
          Report("studio-tabs.json", require_complete_phase=True), timeout=360, event_simulate=True,
          default_chat=True, notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_workspace_stored_layout.py", GUI,
          "A switch applied before a duplicated workspace's own never takes the copy's stored layout: the window ends "
          "on the copy's own screen and the original keeps its screens (a build without the guard crashes here)",
          Report("workspace-stored-layout.json", require_complete_phase=True), timeout=120,
          notes="Real default startup; no input, CLI, account, network or inference."),
    Suite("test_blender_first_launch_tabs.py", GUI,
          "A fresh profile's first launch: the setup card and the welcome show with no tab built under them; Manual's "
          "Media Studio and Generation Flows tabs appear filled once they close, the window in Director with its "
          "chat; a build that stops part-way rolls back and is built at the next quiet moment",
          Report("first-launch-tabs.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, first_launch=True,
          notes="Real first launch (splash, setup card, welcome) and simulated input; no CLI, account, network or "
                "inference."),
    Suite("test_blender_studio_mode_switch.py", GUI,
          "A Media Studio window keeps its tab, draft and references across Director/Manual switches and a mid-session "
          "tab build (Blender shows it a copied area); a closed and reopened Studio starts fresh; nothing carries "
          "across a file load",
          Report("studio-mode-switch.json", require_complete_phase=True), timeout=260,
          notes="Real app; Studio state set through Studio's own click and attach. No account, network or inference.",
          harness=True),
    Suite("test_blender_tab_build_keeps_mode.py", GUI,
          "A file opened while Manual's tab build runs (the same file with Load UI off, a copy saved mid-build "
          "opened with its layout, and Load UI off with Director rebuilt at once) never moves the window to another "
          "tab or a sculpt out of Sculpt Mode, removes no workspace Angelo did not make, leaves no marked copy, and "
          "builds the tabs in the sculpt's mode with no screen left without users",
          Report("tab-build-keeps-mode.json", require_complete_phase=True), timeout=440, default_chat=True,
          notes="Real default startup; no input, CLI, account, network or inference. A test hold keeps the build on "
                "its half-made tab for a moment (timing only)."),
    Suite("test_blender_idle_redraw.py", GUI,
          "Left alone, the app redraws nothing: no region of the window is drawn in ten seconds with nobody touching "
          "it, on Director, Layout, Media Studio and Generation Flows; every area is counted when asked to draw, so "
          "zero is a window that was not drawn",
          Report("idle-redraw.json", require_complete_phase=True), timeout=360, default_chat=True, serial=True,
          notes="Real default startup; no input, CLI, account, network or inference. A draw handler on every editor "
                "and region type counts the draws, from the moment Angelo's start-up is over (the Media Studio asset "
                "library's first sync included) and each tab has come to rest. Serial: it needs the display to itself. "
                "The window system draws a window too, and on a display without a compositor (Xvfb) a window that "
                "covers this one and leaves makes all of it draw again: beside other suites it failed 5 of 5 on "
                "Linux. A second runner on the same display does the same; give each its own."),
    Suite("test_blender_stale_frames.py", GUI,
          "The stale-frame watch (qa_stale_frames) reports a rested region whose redraw shows another picture, and "
          "only that: silent while the chat rests, one report with both pictures for a change nothing asked a redraw "
          "for, none for a change that asked",
          Report("stale-frames-proof.json", require_complete_phase=True), timeout=240, default_chat=True,
          notes="Real default startup; no input, CLI, account, network or inference. The changed state is a square "
                "the suite itself draws in the chat."),
    Suite("test_blender_tools_wait_for_tab_build.py", GUI,
          "Tool calls that land while Manual's tab build runs (at start-up, and a 1.0.4 file opened mid-session) are "
          "held by the GUI pump until the build ends, then each runs exactly once, in order, under the cap, no crash",
          Report("tools-wait-for-tab-build.json", require_complete_phase=True), timeout=240,
          notes="Real app and UI pump; the call is requested on the chat's controller as the build starts. No "
                "account, network or inference."),
    Suite("test_blender_welcome_slides.py", GUI,
          "The welcome's feature slideshow: every slide drawn, translated and fitting in English, Arabic, German and "
          "Russian, and at UI scale 2; arrows, dots, Left/Right, auto-advance, hover hold and pill tooltips; each pill "
          "opens its feature by a real click through the shared openers, in Director and Manual",
          Report("welcome-slides.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_top_bar_permanence.py", GUI,
          "Studio and Flows are permanent top-bar buttons: present and visible by pixels in Director and three Manual "
          "tabs, at UI scale 1 and 2, in English, German and Russian; a real click opens Media Studio and the Flows rows; "
          "the Asset Browser's pills at three widths never cover Blender's header (with/without captures)",
          Report("top-bar-permanence.json", require_complete_phase=True), timeout=480, event_simulate=True,
          default_chat=True, notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_asset_shelf_add.py", GUI,
          "The Media Studio and Generation Flows pills on the sculpt and texture paint shelves at UI scale 1 and 2, "
          "English and German: before/after "
          "pixels, tooltip, a real click opens Media Studio on the 3D or Image tab (read from its window)",
          Report("asset-shelf-add.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_essentials.py", GUI,
          "Actual native executor: seven explicitly synthetic effect modes with dirty vertex/property/camera/light "
          "preimages, one Undo, same-Main sequential imports and final saved-copy readback; "
          "distinct real bundled Essentials modifier import, "
          "Undo/Redo, Stop, stale targets and save/reopen without source files",
          Report("essentials.json", require_complete_phase=True), timeout=300,
          options_env=("ANGELO_QA_ESSENTIALS_ONLINE",),
          notes="Requires the complete actual native asset owner; an unavailable owner is blocked, never PASS. "
                "Synthetic generic-effect fixtures are not official catalog/acquisition proof. "
                "Isolated profile; offline by default, no account or inference. Explicit online option additionally "
                "tests the public Blender catalog, on-demand downloads, cache reuse and offline reuse."),
    Suite("test_blender_fab_formats.py", GUI,
          "Every Fab format after its download, in the default window: content check and the sandboxed converter on a "
          "worker while the main thread ticks, the native append of the clean .blend (FBX, OBJ, glTF, USD, .blend "
          "models; a texture set on the selected cube and in the asset library), packed textures, no Text block or "
          "Python driver, one Undo, a reused conversion on the second add, times and a screenshot per format",
          Report("fab-formats.json", require_complete_phase=True), timeout=900, default_chat=True,
          options_env=("ANGELO_QA_FAB_SAMPLES",),
          notes="Samples generated by Blender's own exporters in a background Angelo; ANGELO_QA_FAB_SAMPLES adds real "
                "Fab downloads (local files). No Fab account, browser or network: the download itself is not tested."),
    Suite("test_blender_library.py", GUI,
          "The Angelo Library in Director against a loopback mirror: the search pill, typed search, a real drag of a "
          "model onto the ground (standing, with provenance), a timed warm-cache drop, a material dropped on the cube, "
          "an HDRI as the world shown in the view, Undo, credits, the docked browser, and main-thread smoothness while "
          "60 thumbnails stream in",
          Report("library.json", require_complete_phase=True), timeout=600, event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; a loopback library mirror in-process; no CLI, account, "
                "network or inference."),
    Suite("test_blender_media_shelf.py", GUI,
          "Object mode's Asset Shelf of models and materials: stock has none (before), shown by default (after) with "
          "its pills, a fixture library's model tile, drag a model into the scene, drop a material on the cube, the "
          "Angelo Media Studio tab offered, Show my assets on it, the open/closed choice remembered",
          Report("media-shelf.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; writes a fixture asset library in its QA folder; no CLI, "
                "account, network or inference."),
    Suite("test_blender_shelf_empty.py", GUI,
          "Angelo Media Studio's empty shelf: none on an empty Current File shelf, Show my assets lands on the "
          "library's tab, the message and a teal Generate in Media Studio in English and German at UI scale 1 and 2 "
          "(narrow: message dropped, button kept), a real click opens Media Studio on 3D, a new library item lists "
          "through the library's refresh and the state goes (1.0.5 binary only)",
          Report("shelf-empty.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; writes one fixture model into the fresh profile's Media "
                "Studio library folder; no CLI, account, network or inference."),
    Suite("test_blender_engine_setup.py", GUI,
          "A render engine add-on a vendor's installer put in Blender's add-ons folder (a stand-in, no Blender) is "
          "offered in Director's engine dropdown under its own name; clicking it links it into Angelo, turns it on "
          "and makes its engine the scene's; F12 renders through it; the corner card asks for the vendor's sign-in, "
          "calls the add-on's own operator, then says it is ready with its edition",
          Report("engine-setup.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; a tiny render; links one stand-in add-on into the fresh "
                "profile's add-ons folder and removes it; no CLI, account, network or inference."),
    Suite("test_blender_installed_engines.py", GUI,
          "A person's render engine add-on (a stand-in outside the shipped scripts) is listed in Director's engine "
          "dropdown after Blender's own, under its own name; choosing it sets the scene's engine and F12 renders "
          "through it; a render job names it as extension code; the settings read lists it; Auto asks before a "
          "program sets it (not Cycles); disabling the add-on removes it from the dropdown",
          Report("installed-engines.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; a tiny render; no CLI, account, network or inference."),
    Suite("test_blender_render_engines.py", GUI,
          "Director's engine button opens Angelo's dropdown (EEVEE, Cycles Preview and Final, AI render) with a tile on "
          "every row; Cycles Final lands on the GPU and a real render runs there; the Render menu lists Render Engine "
          "and AI Render; the AI render card composes with the remembered style, builds the flow and captures the "
          "camera, refuses an unconnected account, and with a stand-in run moves to the corner and opens Media Studio",
          Report("render-engines.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; one small Cycles render; no CLI, account, network or "
                "inference (the card's run is a stand-in).", harness=True),
    Suite("test_blender_render_region.py", GUI,
          "A region rendered over the last render from the render window's pill: the drag hint, the last render kept on "
          "screen while the region renders, the applied card, every pass outside the region bit-identical, Revert "
          "bit-identical to the full render, a stopped render keeps the last render; the + menu and the Render menu list it",
          Report("render-region.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=420, notes="Real default startup and simulated input; small Cycles renders; no account, "
                             "network or inference."),
    Suite("test_blender_render_region_perf.py", GUI,
          "Region render performance: full F12 renders (Cycles, EEVEE), Director and render-window redraws, a region "
          "of 20% rendered over the last render, and the agent's RENDER_REGION job, each with a warm-up and N timed runs",
          Report("render-region-bench.json", require_complete_phase=True), default_chat=True, serial=True, timeout=1800,
          extra_args=("--log", "render.region", "--log-level", "debug"),
          options_env=("ANGELO_RR_BENCH_VARIANT", "ANGELO_RR_BENCH_REPS"),
          notes="A measurement, not a check: run it on a quiet machine, alternating binaries (A B A B). Cycles on the "
                "GPU when the machine has one."),
    Suite("test_blender_render_region_pixels.py", BACKGROUND,
          "Region renders over the last render in Cycles and EEVEE on three unrelated scenes: every pass outside the "
          "region bit-identical, the edge seam measured, revert and reapply bit-identical, render slots kept, the "
          "scene's render region settings restored",
          Report("render-region-pixels.json"), timeout=900, needs_opengl=True,
          notes="Native operator only; small frames and few samples; no account or network. Its EEVEE cases need a "
                "GPU (EEVEE's first render on a machine without OpenGL ends Blender), so it runs on a GPU machine."),
    Suite("test_blender_flow_menu.py", GUI,
          "Director's Flows and a flow's Add step open Angelo's own popup, read from pixels (CARD panel, a tile only "
          "where a glyph is), never Blender's dropdown or node Add menu; a template row opens its flow, the bar names "
          "it with its step count and links back to Media Studio, the Text row adds a Text step, Run all reaches "
          "flow_run, the Media Studio pill opens it",
          Report("flow-menu.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup and simulated input; no CLI, account, network or inference."),
    Suite("test_blender_flow_start.py", GUI,
          "The start page with no flow open, in English and Arabic: Director's flow window and Manual's Generation "
          "Flows tab title it Generation Flows with the whole Flows why line under it (translated, never cut, its "
          "pixels where drawn, mirrored in Arabic) and the Flows popup's rows, each shown or counted",
          Report("flow-start.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, notes="Real default startup; no CLI, account, network or inference."),
    Suite("test_blender_studio_library_cards.py", GUI,
          "Media Studio's library row asks in centred Angelo cards (review L1-L4): Turn off (destructive while it "
          "deletes, teal when it keeps the copies), the Off row with its kept size, Free up space (says Media Studio "
          "keeps everything), the question after removing the library in Preferences, Turn off in de and ar",
          Report("studio-library-cards.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, notes="Real default startup; seeds two library copies in its isolated state folder; no "
                                   "CLI, account, network or inference."),
    Suite("test_blender_studio_palette.py", GUI,
          "Media Studio in the shared tokens: both tabs, empty and populated, English and German at UI scale 1 and 2, "
          "the Light theme; no violet left, readable text (contrast 4.5), before (violet) and after captures",
          Report("studio-palette.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup; imports three generated fixture images into Media Studio's library in its "
                "isolated state folder; no CLI, account, network or inference."),
    Suite("test_blender_shelf_preview_race.py", GUI,
          "An asset library added (every library re-listed) just before each of four brush shelves first shows, from "
          "a cold preview cache: no crash (PreviewLoadJob use-after-free), the main thread's longest stall per attempt",
          Report("shelf-preview-race.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, notes="Real default startup; adds empty asset library folders in its QA folder to its "
                                   "isolated Preferences; no CLI, account, network or inference."),
    Suite("test_blender_studio_workflows.py", GUI,
          "Media Studio's Workflows tab: opened on its tab (open_media_studio WORKFLOW, the chat bar's + menu row; own "
          "window, layout kept), Character in parts from input to review stop to placed parts (Place from the Studio "
          "window, Undo), declined approvals run nothing, Edit in Generation Flow, Runs",
          Report("studio-workflows.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          options_env=("ANGELO_QA_CHARACTER", "ANGELO_QA_SHEET", "ANGELO_QA_PARTS", "ANGELO_QA_GROUNDING"),
          notes="Fixture accounts; the paid steps are stood in by library outputs with their input keys. No provider "
                "call, network or credit. ANGELO_QA_CHARACTER / _SHEET / _PARTS swap in real pictures and models."),
    Suite("test_blender_chat_hint.py", GUI,
          "The one-time show/hide chat hint and the labelled Chat toggle: first Manual, its buttons, Close Area, Director",
          Report("chat-hint.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Real default startup, simulated clicks and keys; no CLI, account, network or inference."),
    Suite("test_blender_director_shortcut.py", GUI,
          "Ctrl+Option+M switches Director over every editor; Ctrl+Option+D over the 3D view; Blender's Ctrl+Alt+D elsewhere",
          Report("director-shortcut.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=600, notes="Real default startup and simulated keys; no CLI, account, network or inference."),
    Suite("test_blender_learn_cards.py", GUI,
          "Learn mode's how-to card in the real chat: a real click opens a typed step's card (the command's name, a "
          "key cap, where it is, settings, needs); the + menu's Learn link opens the Learn card, whose switch turns "
          "Teach me as you work on for the chat; the latest request's steps then open by themselves; Japanese and Arabic",
          Report("learn-cards.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=300, notes="Real default startup and simulated input; rows written as a finished request leaves "
                             "them; no CLI, account, network or inference."),
    Suite("test_blender_learn_routes.py", GUI,
          "Learn mode's hand routes in the running app: every command, menu, choice and setting exists; the keys "
          "shown are Blender's own answer in the default and Industry Compatible key configurations and after a "
          "rebinding; labels follow the language; one card's cost",
          Report("learn-routes.json", require_complete_phase=True), default_chat=True, timeout=300,
          notes="Real default startup; no simulated input, CLI, account, network or inference."),
    Suite("test_blender_autosave_passive_modals.py", GUI,
          "Auto Save writes with the chat open (its handler and other passive modals) and still waits for a real "
          "modal tool, writing as soon as it ends",
          Report("autosave-passive-modals.json", require_complete_phase=True), event_simulate=True,
          default_chat=True, timeout=480,
          notes="Real default startup, Auto Save at one minute, Add Cube edits and simulated events; about 2.5 min "
                "of waiting for the timer. No CLI, account, network or inference."),
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
          "the Look control, automation's opt-out, the add-on turned off, a failed start",
          Report("fixture/first-launch-frames.json"), marker="ANGELO_FIRST_LAUNCH_FRAMES_OK", timeout=1200,
          event_simulate=True, host_args=("--binary", "{binary}", "--out", "{qa}/fixture"),
          notes="Host-Python driver: nine window processes, each with its own sandboxed profile (BLENDER_USER_* and "
                "HOME inside the QA folder), no provider CLI on PATH, --offline-mode; the showcase stays on."),
    Suite("test_blender_prefs_and_quit.py", GUI,
          "Factory/saved/template preferences keep Angelo's chat and connections; a quit passes the welcome and cards",
          Report("fixture/prefs-and-quit.json"), marker="ANGELO_PREFS_AND_QUIT_OK", timeout=900, event_simulate=True,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("darwin", "linux"),
          platform_reason=POSIX_STAND_IN_CLIS,
          notes="Host-Python driver: four window processes (prefs_quit_phases.py), each sandboxed (HOME, Blender's "
                "user folders, Angelo's state and connections inside --out) with the stand-in claude CLI; the host "
                "times each quit from the moment the receipt says it was asked for."),
    Suite("test_blender_gpu_startup_fallback.py", GUI,
          "A Vulkan start that died before its first frame is not tried again: OpenGL at the next launch, kept "
          "through preference files read later, Vulkan again when chosen in Preferences, never against "
          "--gpu-backend; a death while start-up scripts run does not count, except a crash on another thread; a crash "
          "marks the record at once; a failed record that cannot be taken is still believed",
          Report("fixture/gpu-startup-fallback.json"), marker="ANGELO_GPU_STARTUP_FALLBACK_OK", timeout=2400,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("win32", "linux"),
          platform_reason="Needs a build that can draw with Vulkan and with OpenGL; macOS draws with Metal only.",
          notes="Host-Python driver: twenty-one window processes in one sandboxed profile (gpu_startup_phases.py); four "
                "end themselves during their start and three crash natively (each crash dialog on Windows is closed "
                "as a person closes it, crash_dialog.py). No provider, account or network."),
    Suite("test_blender_start_panel.py", GUI, "Returning user: Angelo's start panel with recent scenes instead of Blender's splash page",
          Report("start-panel.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Makes the isolated profile a returning one before startup; no network, account or inference."),
    Suite("test_blender_fresh_chat_document.py", GUI, "A fresh chat stays a welcome when a file opens after startup; "
          "a chat with a message still says the document changed", Report("fresh-chat-document.json",
                                                                          require_complete_phase=True),
          default_chat=True, notes="Real default startup and wm.open_mainfile of a saved copy; no provider or account."),
    Suite("test_blender_canvas_states.py", GUI, "Hover, pressed, focus and disabled states on the chat canvas and welcome screen",
          Report("canvas-states.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py); no browser, account, network or inference.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_connect_hero.py", GUI, "Before a connection: a waiting field, a showcase over the 3D view, then a focused composer",
          Report("connect-hero.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="Stand-in claude/codex CLIs (fake_provider_cli.py); no browser, account, network or inference.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_team_chat.py", GUI, "The chat follows the team account: sign-in, access reasons, keys arriving, ready on the team",
          Report("team-chat.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="In-process fake console (fake_team_console.py) and stand-in CLIs; no network, account or inference.",
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS),
    Suite("test_blender_crash_handler.py", BACKGROUND, "Native crash handler: seven real crashes in background "
          "children (main and worker threads, a fault inside the handler, held stdout/stderr locks, SIGBUS, a stack "
          "overflow) each leave a readable crash log and end the process within the watchdog bound",
          Report("fixture/crash-handler.json"), marker="ANGELO_CRASH_HANDLER_OK", timeout=900,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("darwin",),
          platform_reason="POSIX signals: on Windows the crash path is the unhandled-exception filter, and ctypes "
                          "turns an access violation into OSError instead of crashing. Angelo 2.0.0 ships on macOS "
                          "and Windows; Linux crash-log support (its header and frame names) comes with Linux in "
                          "2.1.0.",
          notes="Host-Python driver; launches seven --background children, each with its own HOME and temporary "
                "folder. Its crash logs stay outside the suite folder (kept copies are renamed)."),
    Suite("test_blender_update_crash.py", GUI, "Crash dialog, What's New and update notice: states, input, pixels and what is sent",
          Report("update-crash.json", require_complete_phase=True), event_simulate=True, default_chat=True, timeout=720,
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
          Report("dispatch-fixes.json"), event_simulate=True, harness=True),
    Suite("test_blender_edit_context_windows.py", GUI, "Staged Edit-mode kinds keep the stage scene with a second window open",
          Report("edit-context-windows.json")),
    Suite("test_blender_region_visibility_owner.py", GUI, "Visibility RNA preserves inactive screens and the caller context",
          Report("region-visibility-owner.json", require_complete_phase=True), event_simulate=True,
          extra_args=("--debug-memory",)),
    Suite("test_blender_editor_regressions.py", GUI, "Hidden drafts, cached editors and pop-out return",
          Report("editor-regressions.json", require_complete_phase=True), notes=INSTALLED_HOST),
    Suite("test_blender_editor_types.py", GUI, "Editor menu and Python interpreter isolation",
          Report("editor-types.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_eval_live_agent_dryrun.py", GUI, "Live-eval driver against a scripted stand-in runtime",
          Report("eval-dryrun.json"), state_in_qa=True, harness=True,
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
    Suite("test_blender_autosave_passive_flags.py", BACKGROUND,
          "The build has the MODAL_PASSIVE operator option, every registered Angelo canvas input and passive overlay "
          "carries it and no holding tool does, and auto-save writes <pid>_autosave.blend in the temp folder",
          Report("autosave-passive-flags.json")),
    Suite("test_blender_editing_tool_flags.py", BACKGROUND,
          "The person's operation in progress (input_policy.editing_tools, what agent work and snapshots wait for) "
          "holds for transform, mesh edit, sculpt, paint, gesture and navigation tools read with their real flags "
          "(a sculpt stroke has no Undo flag), and never for Angelo's own operators or an ended render",
          Report("editing-tool-flags.json")),
    Suite("test_blender_script_document_replaced.py", BACKGROUND,
          "An approved script that loads, reverts or reads the startup file (computed names and a raise after it "
          "included) is refused as DOCUMENT_REPLACED and never crashes the background process; controls unchanged",
          Report("script-document-replaced.json"), harness=True),
    Suite("test_blender_script_invalid_mesh.py", BACKGROUND,
          "An approved script that writes a mesh Blender cannot evaluate (a face or an edge naming a missing vertex, "
          "a face past the corner array, with and without a modifier) leaves every mesh valid and never crashes the "
          "background process; valid meshes written the same ways come through untouched",
          Report("script-invalid-mesh.json"), harness=True),
    Suite("test_blender_second_process_file_load.py", BACKGROUND,
          "A second Angelo started in the background on a .blend (this build's binary, as a terminal or an engine's "
          "import starts it) writes no chat of the first: byte-identical with the chat open and with it closed, and "
          "the open chat's next save succeeds; each run records who opened or saved a chat in the second process",
          Report("second-process-file-load.json"),
          notes="Background; starts this binary twice more in the background (a few seconds each); no account."),
    Suite("test_blender_mesh_topology_seal.py", BACKGROUND,
          "A mesh's topology seal (Mesh.topology_seal, Mesh.is_topology_sealed) breaks on every way a script can "
          "write topology: item properties through fresh and kept items, foreach_set, the attribute API, building, "
          "resizing, bmesh and Edit Mode; reading counts, writing positions and evaluating keep it; a new, copied "
          "or loaded mesh is not sealed",
          Report("mesh-topology-seal.json")),
    Suite("test_blender_background_undo_history.py", BACKGROUND, "After the shared headless start-up every background "
          "load starts that file's own Undo history (unmodified, nothing before it, never an earlier file's data); "
          "Global Undo and a bounded step count are forced", Report("background-undo-history.json")),
    Suite("test_blender_fingerprint_packing.py", BACKGROUND, "Exact fingerprint coverage and stale gates",
          Report("results.json")),
    Suite("test_blender_input_capture.py", GUI, "Native Scale/Escape and canvas pointer isolation",
          Report("input-capture.json"), event_simulate=True),
    Suite("test_blender_credential_manager_live.py", LIVE,
          "Real Windows Credential Manager round trips of throwaway test-only items, as an endpoint key is kept",
          Report("fixture/credential-manager-live.json"), window=False, live_env=("ANGELO_QA_LIVE_KEYCHAIN",),
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("win32",),
          platform_reason="Round trips through Windows Credential Manager; the driver refuses elsewhere.",
          notes="No provider or billing. Host-Python driver; launches one background app process. Needs a signed-in "
                "desktop session. Writes, then deletes, fake-key generic credentials under unique test-only "
                "services; another person's, organization's and profile's slots must read nothing."),
    Suite("test_blender_operator_consumed_input.py", GUI, "Operator.has_answered_input and has_consumed_input on "
          "real modal operators: nothing known before any input; observers and a timer worker answered, never "
          "consumed; RUNNING_MODAL with PASS_THROUGH, a tool's first kept key, a macro's step and a key Scale's "
          "modal key map renames consumed", Report("consumed-input.json"), event_simulate=True),
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
          marker="ANGELO_NATIVE_TEST_OK", harness=True),
    Suite("test_blender_native_programs.py", BACKGROUND, "RNA stage isolation, bundles, stale gates, Undo",
          Report("results.json"), gui_superset=True,
          notes="With --gui it runs in a window and adds preview-pixel and preview acceptance checks."),
    Suite("test_blender_context_override_pair.py", BACKGROUND,
          "temp_override never pairs a scene with another scene's view layer: TypeError or the scene's own layer",
          Report("context-override-pair.json"), gui_superset=True,
          notes="With --gui it checks live window restoration after blend_data=None and unavailable screen switches, "
                "plus Edit mode entered with a second window open (the scene dropped mid-operator)."),
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
    Suite("test_blender_asset_browser_states.py", GUI, "An Asset Browser's empty and no-results states from its real "
          "counts (RNA), in pixels, in English and German at UI scale 1 and 2; a list with assets draws neither; "
          "Clear search and Generate in Media Studio by real clicks",
          Report("asset-browser-states.json", require_complete_phase=True), event_simulate=True, timeout=420,
          notes="Current File library; one material marked as an asset; no account, provider or network. Needs a "
                "build with SpaceFileBrowser.angelo_listed_count (1.0.5 C++)."),
    Suite("test_blender_file_browser_before_refresh.py", GUI, "A File/Asset Browser not refreshed yet survives what "
          "can reach it in the tick that makes it: a language change, activate_asset_by_id, and a deferred "
          "activation on a just-split browser; each list then reads and the activation lands",
          Report("file-browser-before-refresh.json", require_complete_phase=True), timeout=240,
          notes="Current File asset (one material); no account, provider or network. Each case crashed 1.0.5 and "
                "1.0.6 rehearsal builds with SIGSEGV."),
    Suite("test_blender_file_browser_uninitialized_background.py", BACKGROUND, "An Asset Browser that was never "
          "initialized or refreshed (background mode) survives activate_asset_by_id, its deferred form and "
          "activate_file_by_relative_path, and its Delete poll answers False",
          Report("file-browser-uninitialized-background.json"),
          notes="Background; one Current File material asset; no account, provider or network. Crashed the 1.0.6 "
                "rehearsal build 7ea7499b79b with SIGSEGV."),
    Suite("test_blender_flow_drop_items.py", BACKGROUND, "Images dropped into a Generation Flow become Image steps in "
          "drop order at the drop point, in Media Studio's library; a model is skipped and a non-image reported",
          Report("flow-drop-items.json"), notes="Background; a local library only; no account, provider or network."),
    Suite("test_blender_node_drop_targets.py", GUI, "Real drags from an Asset Browser into a node editor: one or two "
          "images become Image steps of a flow or Image Texture nodes of a material at the drop point, one Undo each; "
          "a 3D model over a flow shows why it can't be dropped (alert pixels, en and de) and adds nothing; Media "
          "Studio's images by library id and its model refused",
          Report("node-drop-targets.json", require_complete_phase=True), event_simulate=True, timeout=540,
          notes="Current File library (three images, one object) and an isolated Media Studio library (two images, "
                "one model; its background writer runs); no account, provider or network. Needs a build with "
                "NODE_OT_angelo_drop_assets (1.0.5 C++)."),
    Suite("test_blender_flow_node_rows.py", BACKGROUND, "Every Generation Flow node's rows and wrapped fields fit in "
          "all 21 languages (Blender's own margins), and a fresh flow draws no alert",
          Report("flow-node-rows.json"), notes="Background; no generation account, provider or network."),
    Suite("test_blender_flow_setup_states.py", GUI, "A Generation Flow is red only where a run failed, in pixels: a "
          "fresh template (en, de, ru), models chosen, new Add-menu nodes, a stopped run; a failed run is red",
          Report("flow-setup-states.json", require_complete_phase=True), timeout=360,
          notes="One in-memory fixture image key; attempts are in-memory library records; no provider, network or "
                "paid request."),
    Suite("test_blender_flow_node_theme.py", GUI,
          "A Generation Flow's node bodies are card_kit's tokens and a shader node in the same session stays stock, in "
          "pixels: PANEL bodies, PANEL_HOVER/CARD buttons, Stop for review on is TEAL_SOFT not blue; en at UI scale 1 "
          "and 2, idle and with a failed step",
          Report("flow-node-theme.json", require_complete_phase=True), timeout=300,
          notes="One in-memory fixture image key; the failed attempt is an in-memory library record; no provider, "
                "network or paid request."),
    Suite("test_blender_removed_id_python_api.py", BACKGROUND,
          "A removed ID given to bpy.data.user_map, file_path_map/foreach, batch_remove, libraries.write, a custom "
          "property, GeometrySet, InlineShaderNodes or a USD hook raises ReferenceError (stock 5.2 crashed); live "
          "IDs still work",
          Report("removed-id-python-api.json"),
          notes="The USD hook case needs a build with USD; without it the report records \"not built\"."),
    Suite("test_blender_temp_data_lifetime.py", BACKGROUND,
          "Temporary BlendData has separate context and target references, survives either Python destruction "
          "order as an invalidated wrapper, and rejects repeated entry or exit",
          Report("temp-data-lifetime.json"),
          notes="Upstream temp_data ownership/lifecycle regressions; no provider, account or network."),
    Suite("test_blender_sub_data_wrappers.py", BACKGROUND,
          "Held RNA and custom-property wrappers survive unrelated edits and reject stale sub-data; "
          "exported numeric arrays keep their original storage until release",
          Report("sub-data-wrappers.json"), timeout=900, notes="One child process per case."),
    Suite("test_blender_sub_data_soak.py", BACKGROUND,
          "Held live and retired sub-data wrappers across CPU renders, a bake, Python drivers and file reload",
          Report("sub-data-soak.json"), timeout=900,
          notes="Completed background jobs only; mid-job load and quit require the separate GUI gate."),
    Suite("test_blender_sub_data_ui_lifetime.py", GUI,
          "Live UI wrappers across maximize/restore and conversion callbacks; file load and quit during CPU renders",
          Report("sub-data-ui-lifetime.json", require_complete_phase=True), timeout=360,
          notes="Isolated factory scene; clean process exit also verifies that quit joined the render worker."),
    Suite("test_blender_instance_alias_wrappers.py", BACKGROUND,
          "Every wrapper and iterator of freed data raises ReferenceError: aliases, views, sub-structs, properties, "
          "arrays and mathutils values, functions, custom properties and UI data, iterators over a freed ID or graph "
          "(view_layer.depsgraph too), a depsgraph step's instance and object, evaluated data freed or replaced, "
          "views across undo (stock 5.2 crashed or read freed memory); live ones still work",
          Report("instance-alias-wrappers.json"), notes="One child process of the binary per case."),
    Suite("test_blender_idprop_assignment_errors.py", BACKGROUND,
          "A failing custom-property assignment raises and keeps the old value: an array item out of range (static "
          "and ordinary), a longer buffer, an unencodable string, a mapping whose len() lies; nothing pending at "
          "the next call (stock 5.2 left SystemError, overflowed the heap or crashed)",
          Report("idprop-assignment-errors.json"), notes="One child process of the binary per case."),
    Suite("test_blender_native_suggestions.py", GUI, "Detected sign-ins suggested, never set up (stand-in status)",
          Report("native-suggestions.json", require_complete_phase=True), event_simulate=True,
          notes="Stand-in native status; never runs the Codex or Claude CLIs or reads a real profile.", harness=True),
    Suite("test_blender_ordered_recovery.py", BACKGROUND, "Ordered scene/file recovery and large export",
          Report("ordered-recovery.json", None, env="ANGELO_ORDERED_RECOVERY_REPORT")),
    Suite("test_blender_metadata_scan.py", BACKGROUND,
          "Descriptor metadata parity, 100k-ID pages, compressed work limits and cancellation",
          Report("metadata-scan.json"), serial=True, timeout=900,
          notes="Creates 100,013 real Blender mesh IDs and streams 2 GiB of zero DATA into gzip; "
                "production-deadline refusals remain failures alongside longer diagnostic receipts."),
    Suite("test_blender_exr_codecs.py", BACKGROUND,
          "Every OpenEXR codec the build writes read by the view reader (own decoder or the child OpenImageIO decoder), "
          "committed fixtures, damaged copies, a path with non-ASCII letters",
          Report("exr-codecs.json"), timeout=600,
          notes="Renders a 37x21 multilayer picture (Cycles on the CPU) and writes 1080p files for the timing receipt."),
    Suite("test_blender_format_restore.py", BACKGROUND,
          "Image-format settings restoration and display copies",
          Report("format-restore.json")),
    Suite("test_blender_read_access.py", BACKGROUND,
          "Const mesh/image reads, unchanged Cycles storage and computed Python getter isolation",
          Report("read-access.json"),
          notes="Requires the full-feature build with real Cycles and the native read helpers; no provider or network.",
          harness=True),
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
          "Mantaflow smoke and liquid, file I/O round trips, FFmpeg, audio and translations (skipped per feature on the development build)",
          Report("release-features.json"), platforms=("darwin", "win32"),
          platform_reason="Its agent_runtime_bundled check needs the bundled agent runtime, which ships on macOS and "
                          "Windows only (no Linux build of it in 2.0.0).",
          notes="Meaningful on build_release_macos.sh; the receipt's full_feature_build says which binary ran. "
                "No GPU kernel compile: the Metal device is only listed."),
    Suite("test_blender_program_commit_effects.py", GUI, "Staged effects, rollback and real Undo",
          Report("results.json")),
    Suite("test_blender_program_commit_file.py", GUI, "Same feature and Undo matrix through file-backed stages",
          Report("results.json")),
    Suite("test_blender_program_sculpt_undo.py", GUI, "Typed commits preserve artist Sculpt mode and individual Undo steps",
          Report("program-sculpt-undo.json"), harness=True),
    Suite("test_blender_program_names.py", GUI,
          "Object names in program order, joins and conversions, by QA commit and the production Undo bridge",
          Report("program-names.json"), harness=True),
    Suite("test_blender_stage_id_names.py", GUI,
          "Staged ID copies keep their live names (materials, groups, Worlds, Actions); refusals stay correctable",
          Report("stage-id-names.json"), harness=True),
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
    Suite("test_blender_work_guard_jobs.py", BACKGROUND,
          "Restart to Update's work guard, the live view and bindings see a real Ocean bake and an Alembic import "
          "job, which no closed list names; the asset catalog checks read and put back their setting",
          Report("work-guard-jobs.json"),
          notes="Needs Alembic (every release build has it); the jobs end when the process quits."),
    Suite("test_blender_checkpoint_preview_jobs_background.py", BACKGROUND,
          "Ten Restores and ten Python-started open_mainfile calls, each with preview jobs started first",
          Report("checkpoint-preview-jobs-background.json"), serial=True, needs_opengl=True,
          notes="The runner's timeout is the hang watchdog for a read joining a preview job under the GIL."),
    Suite("test_blender_sculpt_checkpoint_review.py", GUI,
          "Same Multires object in two graphs: exact Restore/Undo restore, late-failure reload, one commit step, sealed pair",
          Report("sculpt-checkpoint-review.json", require_complete_phase=True), serial=True, state_in_qa=True,
          notes="Needs a WITH_GTESTS build (native fixture seams); fresh factory process; no provider.", harness=True),
    Suite("test_blender_native_checkpoint_cold_recovery.py", GUI,
          "Native checkpoint capsule, cold reader, safety and load-handler conflicts",
          Report("native-checkpoint-cold-recovery.json"), serial=True),
    Suite("test_blender_harness_confined.py", BACKGROUND,
          "The test harness's grant scope, which exists with --enable-event-simulate, is never an agent script's: "
          "every way a confined script could reach it is refused and nothing is granted",
          Report("harness-confined.json"), timeout=180, harness=True,
          notes="The coordinator's 2.0.0 condition for suites that stand in for the person: the harness is inert in a "
                "shipped app (test_blender_guard_authority) and refused to agent code where it exists (this suite)."),
    Suite("test_blender_guard_authority.py", BACKGROUND,
          "Grant authority in C: the module holds no settable state, the ledger widens only in a grant scope, no "
          "ticket without a window, and a confined script is admitted only for its exact text and refused every grant",
          Report("guard-authority.json"), harness=False),   # Proves the harness refused without the flag.
    Suite("test_blender_checkpoint_content_binding.py", BACKGROUND,
          "Content binding: a same-size rewrite keeping the stamp refuses safety, pair and Restore; POST and hasher "
          "admission; no API returns the binding",
          Report("checkpoint-content-binding.json"), serial=True, platforms=("darwin", "win32"),
          platform_reason="Linux coarse kernel mtimes: checkpoint_seal.coarse_timestamps predicts an ext4 volume "
                          "coarse there. Linux support comes in 2.1.0.",
          notes="Restores the factory scene per case; the Sculpt-gate case runs only in a WITH_GTESTS build."),
    Suite("test_blender_program_preview.py", GUI, "Preview pixels and artist-state restoration",
          Report("results.json")),
    Suite("test_blender_mirror_object.py", GUI, "Mirror origin identity, staged geometry, stale bindings, Undo and partial checks",
          Report("mirror-object.json", require_complete_phase=True), timeout=600,
          notes="Finite native stage/commit/Stop cases; no provider, runtime, account or network.", harness=True),
    Suite("test_blender_program_stress.py", GUI, "Recorded Claude plans replayed through the live-eval driver under timing stress",
          Report("program-stress.json"), event_simulate=True, state_in_qa=True, timeout=2400, harness=True,
          notes="No provider process, network or account; ANGELO_STRESS_CYCLES (default 100) x 6 tasks."),
    Suite("test_blender_provider_pool.py", GUI, "Preferences and model pool with fixture catalogs",
          Report("provider-pool.json", require_complete_phase=True),
          variants=WORKER_CHAT_HOST, harness=True),
    Suite("test_blender_runtime_recovery.py", BACKGROUND, "Exact-binary runtime recovery regressions",
          Report("runtime-recovery.json", None), harness=True),
    Suite("test_blender_saved_keys.py", BACKGROUND, "Saved API keys through real operators with a fake Keychain",
          Report("saved-keys.json"), harness=True),
    Suite("test_blender_shared_keys.py", BACKGROUND, "One Google key for Gemini Images and coding, real operators",
          Report("shared-keys.json"), harness=True),
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
          Report("script-rollback.json"), harness=True),
    Suite("test_blender_renamed_api_errors.py", BACKGROUND,
          "Errors for names Blender 4.x/5.x renamed or removed name the 5.2 replacement; same exception, no alias; "
          "unlisted names keep stock's text",
          Report("renamed-api-errors.json")),
    Suite("test_blender_sculpt.py", GUI, "Sculpt engine, Undo, offscreen pixels and snapshots",
          Report("sculpt-qa.json"), harness=True),
    Suite("test_blender_sculpt_engine_fixes.py", GUI, "Stroke originals, mixed Undo, tilt and input",
          Report("sculpt-engine-fixes.json")),
    Suite("test_blender_sculpt_handles.py", GUI, "Sculpt tools by scene handle on every path (binding and edit) "
          "and refusals before an edit that say nothing ran", Report("results.json"), timeout=300),
    Suite("test_blender_sculpt_regressions.py", GUI, "Five sculpt audit reproductions",
          Report("sculpt-regressions.json"), harness=True),
    Suite("test_blender_sculpt_safety_fixes.py", GUI, "Sculpt budget and view safety",
          Report("results.json", "completed")),
    Suite("test_blender_sculpt_undo_accounting.py", GUI, "Encoded-byte Undo accounting and pruning",
          Report("sculpt-undo-accounting.json"),
          extra_args=("--log", "undo,undo.sculpt", "--log-level", "debug")),
    Suite("test_blender_sculpt_workflow.py", GUI, "Sculpt programs, regions and working levels",
          Report("sculpt-workflow.json"), harness=True),
    Suite("test_blender_sculpt_workflow_fixes.py", GUI, "Landmarks, silhouettes and reference progress",
          Report("sculpt-workflow-fixes.json"), harness=True),
    Suite("test_blender_perf_ratchet.py", BACKGROUND, "Instruction-count ratchet: every benchmark operation within perf_ceilings.json",
          Report("fixture/perf-ratchet.json"), marker="ANGELO_PERF_RATCHET_OK", serial=True, timeout=2700,
          host_args=("--binary", "{binary}", "--out", "{qa}/fixture"), platforms=("darwin",),
          platform_reason="Instruction counters (proc_pid_rusage) exist only on macOS; the driver fails closed elsewhere.",
          notes="Host-Python driver; runs the execution, snapshot, stage-compression, view-check, long-session, "
                "session-journal and native-read benchmarks one after another in background app processes. macOS only (instruction counters); fails closed elsewhere. "
                "After a pass, perf_ratchet.py <receipt> --stamp records the measurement."),
    Suite("test_blender_shared_sessions.py", LIVE, "Real Codex resume with a fixture Claude lead",
          Report("shared-sessions.json"), window=False, live_env=("ANGELO_QA_LIVE_CODEX", REAL_PROVIDERS),
          notes="Sends ordinary-tier requests on the evaluation-account ChatGPT profile (ANGELO_TEST_REAL_PROVIDERS)."),
    Suite("test_blender_snapshot_dedup.py", BACKGROUND, "Deduplicated snapshots: real captures, ingestion, exact restores",
          Report("snapshot-dedup.json")),
    Suite("test_blender_bake_maps_agent_start.py", GUI,
          "The agent's own BAKE_MAPS start through angelo_job_start (precondition, then execute): the executor's and "
          "the job host's validations take the canonical request, the job ends done with the ORM files and a baked "
          "material, the person's materials unchanged, no stall",
          Report("bake-maps-agent-start.json"), timeout=480,
          notes="A real Cycles CPU bake at 128 px; stand-in conversation, no provider or account."),
    Suite("test_blender_long_jobs.py", GUI, "Background jobs on Blender's own jobs: renders, bakes and exports with "
          "progress, cancel, restore, queue, time budget, file load and crash recovery",
          Report("long-jobs.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU renders and bakes on tiny scenes; stand-in conversation, no provider or account."),
    Suite("test_blender_render_deadline.py", GUI, "A job's time budget is enforced by its own break check: a GPU still "
          "stopped while the main thread is held up, a texture bake, and neither the person's later render nor "
          "another scene's render ever stopped by it",
          Report("render-deadline.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles GPU renders and a bake on small scenes (fails when no GPU is listed); stand-in "
                "conversation, no provider or account."),
    Suite("test_blender_render_child.py", GUI, "Heavy Cycles render jobs run in a child Angelo on a copy of the scene: "
          "the person's settings back and edits never reaching the picture, Cancel and the time budget ending the "
          "process from the watchdog, the person's F12 untouched, a stopped animation's frames kept",
          Report("render-child.json", require_complete_phase=True), timeout=900,
          notes="Real Cycles renders (the GPU when one is listed) on small scenes; stand-in conversation, no "
                "provider or account."),
    Suite("test_blender_render_kernels.py", GUI, "A first Cycles render that compiles its kernels: a short budget is "
          "not spent on the compile, the card says so, the next estimate leaves it out, a later child render compiles "
          "and archives nothing again (every backend)",
          Report("render-kernels.json", require_complete_phase=True), timeout=900,
          options_env=("ANGELO_RENDER_KERNELS_FORCE_RECOMPILE",),   # Its reuse check's fail-first proof.
          notes="Real Cycles renders (the GPU when one is listed). Proves the compile path only when run cold: an app "
                "copy with its own bundle id and a fresh HOME."),
    Suite("test_blender_job_restore.py", BACKGROUND, "What a job changes for itself is put back exactly: constrained "
          "settings, settings found by name, a bake's material node and new image, also from a file saved meanwhile",
          Report("job-restore.json")),
    Suite("test_blender_production_bakes.py", GUI, "High-to-low and cage texture bakes as jobs: the planted detail in the "
          "baked pixels, the approval, Cancel, a failure, and the selection, mode and material left exactly as they were",
          Report("production-bakes.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU bakes on a 64-pixel image; stand-in conversation, no provider or account."),
    Suite("test_blender_bake_maps_correctness.py", BACKGROUND, "Bake every map at once: each channel's numbers "
          "against native bakes, a reference render and closed forms, on development and held-out materials (mixes, "
          "groups, procedurals, HDR emission, cutouts, two materials, UDIM tiles, displacement, high to low), the "
          "files read back per packing preset, and the person's materials untouched",
          Report("bake-maps-correctness.json"), timeout=600,
          notes="Real Cycles CPU bakes at 64 pixels; no job, window, provider or account."),
    Suite("test_blender_bake_maps_shading.py", BACKGROUND, "Bake every map at once on shapes a review found: film "
          "exposure and clamping, shaders passed into nested groups, a colour on the Surface socket, Principled alpha "
          "on emission, a colour in a float input, OpenEXR read back by Angelo, a bake that wrote nothing",
          Report("bake-maps-shading.json"), timeout=600,
          notes="Real Cycles CPU bakes at 64 pixels; no job, window, provider or account."),
    Suite("test_blender_baked_export.py", BACKGROUND, "Baked materials at export: the web export, the engine link's "
          "live export and its background write-back write each bound target with its baked set (one ORM image for "
          "occlusion and metallic-roughness), a twin sharing the mesh keeps its own, a mesh, UV or material edit is "
          "stale, the option off, an assigned baked material, each packing preset, and the person's slots "
          "untouched, on development and held-out targets",
          Report("baked-export.json"), timeout=600,
          notes="Real Cycles CPU bakes at 64 pixels and one background Angelo for the write-back; no window, "
                "provider or account."),
    Suite("test_blender_baked_export_shading.py", BACKGROUND,
          "Every value that computes a shader's output makes a baked material stale, display-only edits don't: "
          "each shader node type's writable properties, inner structs and socket values one at a time, ramp "
          "stops, curve points, a group's inner value, script text and file, the active output",
          Report("results.json"), notes="No provider, account or network."),
    Suite("test_blender_baked_export_stale.py", BACKGROUND, "When a baked material goes stale: every edit that changes "
          "what a bake reads (geometry, UVs, face material assignments, shading, node values, slots, painted image "
          "pixels, modifier settings, geometry within AO reach, a placement a node reads, high-to-low sources and "
          "cage) makes the binding stale, and selection, renaming and moves out of reach don't, on development and "
          "held-out scenes",
          Report("baked-export-stale.json"), timeout=600,
          notes="Digests without bakes, plus one real Cycles CPU high-to-low bake at 64 pixels; no window, provider "
                "or account. Scene files after the receipt run the same battery on them (held out)."),
    Suite("test_blender_baked_export_hidden.py", BACKGROUND, "Baked materials and the viewport: hiding with the eye or "
          "disabling in viewports (an instancer, a collection instance, a modified neighbour, a high-to-low source) "
          "leaves a binding current, an edit to what a hidden object shows makes it stale, render visibility and "
          "Show Instancer for renders count, and unsaved paint on every UDIM tile makes it stale (the native "
          "reader, _bpy._angelo_digest)",
          Report("baked-export-hidden.json"), timeout=600,
          notes="Digests without bakes, four 4K UDIM tiles; no window, provider or account. Needs the built app's "
                "native reader."),
    Suite("test_blender_baked_export_buffers.py", BACKGROUND, "A baked material is current exactly when a bake now "
          "reads the pixels its bake read: an image Cycles copies from memory (packed after its file was saved "
          "over, a UDIM image with a generated tile, unsaved edits) and plain files, packed and generated images "
          "across a Reload and a save and reopen, with real bakes (the native reader, _bpy._angelo_digest.pixels)",
          Report("baked-export-buffers.json"), timeout=600,
          notes="Real Cycles CPU emission bakes at 8 pixels; no window, provider or account. Needs the built app's "
                "native reader."),
    Suite("test_blender_saved_file.py", BACKGROUND, "A save's save_post handlers name the file it wrote as the save "
          "identified it (os.stat's values), and a file put at the path after the rename is never "
          "taken for the save", Report("saved-file.json"),
          notes="Small .blend saves in a temporary folder; no window, provider or account. Needs the built app's "
                "_bpy._angelo_saved."),
    Suite("test_blender_image_read_files.py", BACKGROUND, "The file Blender reads for an image's frame, tile or "
          "view: frame file names and frame numbers equal Blender's own, a sequence frame's or a view's file saved "
          "again makes a baked material stale (another frame's doesn't), and the open file lists a tile set "
          "without tile 1001 as present",
          Report("image-read-files.json"), notes="Digests without bakes, 8-pixel PNGs; no window, provider or "
                                                 "account."),
    Suite("test_blender_bake_maps_job.py", GUI, "The Bake Maps card and its job: the + menu row, the card on the "
          "selection, the job's progress in the corner, the Unreal preset's files, the baked material and Use, a "
          "stopped bake that leaves nothing behind, the main thread's longest gap and the GPU",
          Report("bake-maps-job.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          timeout=600,
          notes="Real Cycles bakes at 2K and 4K on this machine's device; no provider or account."),
    Suite("test_blender_bake_output_safety.py", BACKGROUND, "Mixed bake recovery over native image buffers and "
          "individual compositor output suffix admission",
          Report("bake-output-safety.json"), notes="Tiny native images and compositor nodes; no render or bake job."),
    Suite("test_blender_production_renders.py", GUI, "Stereo, multi-view and File Output renders as jobs: exactly the "
          "approved files with Blender's own pixels per view and item, whole frames on cancel, paths restored",
          Report("production-renders.json", require_complete_phase=True), timeout=720,
          notes="Real Cycles CPU renders at 64 pixels; stand-in conversation, no provider or account."),
    Suite("test_blender_render_deliverables.py", GUI, "Render deliverables as jobs (VFX audit A2): the scene's own "
          "format (multilayer OpenEXR with passes, TIFF), shot names and frame step, refusals, settings untouched",
          Report("render-deliverables.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU renders at 48 pixels, one sample; stand-in conversation, no provider or account."),
    Suite("test_blender_shot_video.py", GUI, "Video from the scene: a format MP4 animation publishes its frames and a "
          "complete H.264 MP4; a shot from the camera renders EEVEE first/last frames and a Workbench MP4 move",
          Report("shot-video.json", require_complete_phase=True), timeout=600,
          notes="Needs a build with FFmpeg (release config). Real renders at 97x54 to 854x480; stand-in conversation."),
    Suite("test_blender_udim_bakes.py", GUI, "UDIM texture bakes as jobs: only the tiles the faces lie in change, every "
          "tile published with its number, a cancel reloads the saved tiles, the person's tile files never written",
          Report("udim-bakes.json", require_complete_phase=True), timeout=600,
          notes="Real Cycles CPU bakes on 32- and 256-pixel tiles; stand-in conversation, no provider or account."),
    Suite("test_blender_disk_simulation.py", GUI, "Simulation bakes cached on disk as jobs: a file per frame inside the "
          "job's output, read by a path from the .blend after reopening and moving the project, restored on cancel",
          Report("disk-simulation.json", require_complete_phase=True), timeout=720,
          notes="Real geometry-nodes bakes on a tiny simulation; stand-in conversation, no provider or account."),
    Suite("test_blender_mantaflow.py", GUI, "Mantaflow smoke baked as Blender's own job in a saved project: the main "
          "thread keeps serving timers, a data file per frame, frames read back, the smoke in the viewport, Pause Bake",
          Report("mantaflow.json", require_complete_phase=True), timeout=720,
          notes="Real Mantaflow CPU bakes at 32 and 64 voxels; no Angelo job tool, provider or account. Skips on a "
                "build without Mantaflow."),
    Suite("test_blender_fluid_jobs.py", GUI, "Mantaflow bakes as jobs: refusals, a file per frame published and read by "
          "a path from the .blend, a cancel through the job's stop with the domain state and an earlier cache put "
          "back exactly, a file saved mid-bake, the disk guard",
          Report("fluid-jobs.json", require_complete_phase=True), timeout=900,
          notes="Real Mantaflow CPU bakes at 24 voxels; stand-in conversation, no provider or account. Skips on a "
                "build without Mantaflow or the fluid job."),
    Suite("test_blender_fluid_varied.py", GUI, "Held-out Mantaflow bakes as jobs: fire with noise at frames 3-12 in a "
          "non-uniformly scaled domain, a 15 cm Modular smoke-and-fire domain with non-ASCII names, a Replay liquid "
          "with mesh, speed vectors and spray; Blender's own grid, every cache published and played",
          Report("fluid-varied.json", require_complete_phase=True), timeout=1200,
          notes="Real Mantaflow CPU bakes at 32-44 voxels; stand-in conversation, no provider or account. Skips on "
                "a build without Mantaflow or the fluid job."),
    Suite("test_blender_job_desktop.py", GUI, "A real job holds the system awake while it runs (read back from the "
          "OS), releases it when none is left, honours the preference, and its long end reaches the desktop calls",
          Report("job-desktop.json", require_complete_phase=True), options_env=("ANGELO_WINDOWS_JOB_DESKTOP",),
          notes="Real Cycles CPU renders at 64 pixels; stand-in conversation, no provider or account. The system's "
                "permission question is never shown in an automation run."),
    Suite("test_blender_job_card.py", GUI, "Background-job approval, chat card states (hover, pressed, focus, "
          "disabled), Cancel and Open clicks, notices and the status-bar item",
          Report("job-card.json", require_complete_phase=True), event_simulate=True,
          notes="Stand-in job runners in the real manager; screenshots of every card state."),
    Suite("test_blender_flows.py", GUI, "Generation Flows: node tree, captures, runs, reuse, approval and run cards with fixture providers",
          Report("flows-qa.json", require_complete_phase=True)),
    Suite("test_blender_flow_recipes.py", GUI, "Flow recipes and Subflow nodes: interface sockets, per-use choices, reuse, "
          "recipe library copy-in and staleness with fixture providers",
          Report("flow-recipes-qa.json", require_complete_phase=True)),
    Suite("test_blender_reserved_property_names.py", BACKGROUND,
          "No property of any operator or other registered type, native or the add-on's, has a name RNA reserves "
          "(a Python keyword, or keys, values, items, get)",
          Report("reserved-property-names.json"),
          notes="Background; the add-on enabled; no account, provider or network."),
    Suite("test_blender_export_internal_properties.py", BACKGROUND,
          "No exporter that writes custom properties (glTF extras, FBX, USD, Alembic) writes Angelo's own saved "
          "angelo_* and _angelo_* keys, held-out keys included; the person's own property is still written",
          Report("export-internal-properties.json"),
          notes="Background; real exports of a small scene it builds; no add-on of Angelo's, account or network."),
    Suite("test_blender_media_drop.py", BACKGROUND,
          "Media Studio items where they land: models at a point at their GLB's size (append, link, several), "
          "references, materials, the drop operator, the menu actions, writing at once, the library off",
          Report("media-drop.json"), options_env=("ANGELO_QA_NO_OPENGL",),
          notes="Fixture GLBs and images it writes itself; no provider or network. Without OpenGL its "
                "writer runs without preview renders (they end a background Blender there)."),
    Suite("test_blender_media_drop_gui.py", GUI,
          "Media Studio items dragged like assets with real drags: into the 3D view (model under the pointer, image on "
          "an object or empty space), the Image Editor, the Outliner, the chat and Media Studio; refusals; several at "
          "once; one undo per drop; from Media Studio's own window",
          Report("media-drop-gui.json", require_complete_phase=True), event_simulate=True,
          notes="Needs the native drag (ASSET_OT_angelo_drag); fixture items, no provider, account or network."),
    Suite("test_blender_photo_drop_gui.py", GUI,
          "A photo dropped on the 3D view in Director: only Angelo's handler takes it, the card at the drop point, "
          "Make a 3D model placed at the drop point (one Undo), Escape changes nothing, material on the object under "
          "the pointer, no 3D account asks to connect, a TIFF offers material and picture only, the + menu entry",
          Report("photo-drop-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="A fixture Meshy account and a stand-in generator (a cone GLB); no provider call, network or credit."),
    Suite("test_blender_tension_map_gui.py", GUI,
          "Tension map in Director: the + menu's operator on a posed base-mesh character, the corner card, a range "
          "click, a new pose updating live, Material Preview and EEVEE reading the attribute, a simulated cloth, "
          "Remove and one Undo",
          Report("tension-map-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          notes="A bundled Human Base Mesh on a test rig and a pinned cloth; no provider, account or network."),
    Suite("test_blender_studio_item_menu.py", GUI,
          "Media Studio's item menu and the Properties editor's drops: a right-click opens the menu for the item or "
          "its selection, Open in Image Editor shows the picture, a model and an image off a material slot dragged "
          "over Properties are refused with the red reason and add nothing",
          Report("studio-item-menu.json", require_complete_phase=True), event_simulate=True,
          notes="Needs the native drag (ASSET_OT_angelo_drag); fixture items, no provider, account or network."),
    Suite("test_blender_fal_trellis_gui.py", GUI,
          "TRELLIS 2 in Media Studio's 3D tab: the fal account's 3D model, a library image as its reference, the "
          "confirmation, one submit with the polygon target and the key only on fal's hosts, the GLB, Import into "
          "the scene and one Undo",
          Report("fal-trellis-gui.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, notes="A fixture fal account and a stand-in fal (StudioJobs.transport); exports its own "
                                   "GLB with the glTF exporter; no provider call, network or credit."),
    Suite("test_blender_fal_video_gui.py", GUI,
          "Media Studio's Video tab: the + menu's Video row opens it in its own window (Director's layout kept), the "
          "fal account's model, a library image as the first frame, the length menu, the price before Generate and "
          "in the confirmation, one image-to-video submit with the key only on fal's hosts, the finished card's "
          "picture, Add to Sequencer and one Undo",
          Report("fal-video-gui.json", require_complete_phase=True), timeout=300, event_simulate=True,
          default_chat=True, notes="A fixture fal account and a stand-in fal (StudioJobs.transport); renders its own "
                                   "clip with Workbench (needs FFmpeg); no provider call, network or credit."),
    Suite("test_blender_video_card_picture.py", BACKGROUND,
          "Media Studio's video card shows a clip as Blender shows it at every bit depth: clips the suite writes in "
          "each codec and depth the build offers (8, 10 and 12 bits a channel), the reader's kind of buffer, the "
          "colour of the clip as a movie strip, and the card picture's colour beside it; no data-block left",
          Report("video-card-picture.json"),
          notes="Needs FFmpeg (every release build has it); the Sequencer renders, so no GPU. No provider call."),
    Suite("test_blender_studio.py", GUI, "Studio canvas with synthetic events and fixture APIs",
          Report("studio-qa.json", require_complete_phase=True), event_simulate=True),
    Suite("test_blender_studio_assets.py", BACKGROUND,
          "Media Studio as a Blender asset library: the Preferences entry (added once, renamed, removed, a preference "
          "read), the real background writer's Object, Collection and Material assets, size, base, one-way sync",
          Report("studio-assets.json"), options_env=("ANGELO_QA_NO_OPENGL",),
          notes="Fixture GLBs and images it writes itself; no provider or network. Without OpenGL its "
                "writer runs without preview renders (they end a background Blender there)."),
    Suite("test_blender_gltf_morph_weld.py", BACKGROUND,
          "glTF files with morph targets: Blender's import default still splits seams and now warns; Angelo's import "
          "options and model path weld them, so no seam shades apart once a shape key moves; plain files unchanged",
          Report("gltf-morph-weld.json"), notes="Fixture GLB/glTF files it writes itself (sphere, held-out torus, "
          "cylinder and a mixed file); no provider or network."),
    Suite("test_blender_studio_previews.py", BACKGROUND,
          "A Media Studio model written without its preview (the render ended the writer, forced) is written again "
          "with a real preview in the next session, once; a raising render leaves renders on",
          Report("studio-previews.json"), needs_opengl=True,
          notes="Forces the failure with ANGELO_QA_FORCE_PREVIEW_FAILURE; the retry renders, so it needs OpenGL."),
    Suite("test_blender_studio_assets_gui.py", GUI,
          "Media Studio items in the Asset Browser under Angelo Media Studio (pixels), dragged into the 3D view: "
          "the model lands under the pointer, standing on the floor, at the GLB's size; an image material onto an object",
          Report("studio-assets-gui.json", require_complete_phase=True), event_simulate=True,
          notes="Real window and simulated drags; fixture items, no provider, account or network."),
    Suite("test_blender_studio_picture_colour_gui.py", GUI,
          "Media Studio's grid shows each picture in its file's colours: mid-tone PNG, JPEG and 16-bit PNG fixtures "
          "read back from a screenshot, each channel within 4/255",
          Report("studio-picture-colour-gui.json", require_complete_phase=True),
          notes="Real window; fixture pictures, no provider, account or network."),
    Suite("test_blender_studio_export_checkpoints.py", BACKGROUND, "Large Studio export snapshot round trip",
          marker="ANGELO_STUDIO_EXPORT_CHECKPOINT_OK", serial=True),
    Suite("test_blender_survey_fixes.py", GUI, "Request-survey host defects through real staging, commit and Undo",
          Report("survey-fixes.json")),
    Suite("test_blender_survey_grading_dryrun.py", GUI, "Request-survey answer, evidence and result grading against a "
          "scripted stand-in runtime", Report("survey-grading-dryrun.json"), state_in_qa=True, harness=True,
          notes="Runs eval_live_agent_session.py on a short survey list; no provider process, network or account."),
    Suite("test_blender_team_library.py", GUI, "Team library and IP tools: real imports of every admitted format, "
          "safety scrub, Undo and uploads against the in-process fake console",
          Report("team-library-qa.json", require_complete_phase=True)),
    Suite("test_blender_turn_context.py", BACKGROUND, "Turn context v2 ordering, revisions and change feed",
          Report("results.json", "completed"), harness=True),
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
    Suite("test_blender_goal_gui.py", GUI,
          "A goal held across turns in Director Mode: the + menu's Goal row, the chip and its card, a real loop over "
          "the stand-in codex (Angelo's round sent as its own request, two unchanged rounds end it stalled, nothing "
          "more sent), a draft holding a round, Stop goal, and the chip and card in five languages",
          Report("goal-gui.json", require_complete_phase=True), event_simulate=True, default_chat=True,
          platforms=("darwin", "linux"), platform_reason=POSIX_STAND_IN_CLIS, timeout=300),
    Suite("test_blender_goal_measure.py", GUI, "A held goal's host measurement: a drawn reference disc against a "
          "cube (pi / 4) and a sphere (0.95 or more), target rules resolved per measurement (an object added later, a "
          "collection, one off camera, a frame-filling backdrop noted, Undo between rounds), SCENE checks bound to "
          "current revisions, read-only and no file written, its time at 512 px", Report("results.json"), timeout=300),
    Suite("test_blender_view_outline.py", GUI, "Reference comparisons checked against Blender's own drawing and "
          "projection: OUTLINE matches itself, a known move and scale give Blender's projected offset and their "
          "ratio, hair silhouettes hold every projected strand point, cut-out and busy references, OVERLAY, "
          "MATCAP and CURVATURE looks, VALIDATION refusals, every call read-only", Report("results.json"),
          timeout=300),
    Suite("test_blender_render_extensions.py", BACKGROUND,
          "Add-on or script Python a render or bake would run, found in the real app without false alarms; "
          "keyed render settings held for a job's frames; Sequencer scene strips a render reaches",
          Report("render-extensions.json"), timeout=180, autoexec_preference=True,
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
    Suite("test_blender_external_window_gui.py", GUI, "An app's MATERIAL and LIT looks on cold shaders, a look "
          "failing mid-capture and the window picture change no data-block, the unsaved flag, Undo, shading, "
          "selection or mode; the window picture draws only its 3D viewports",
          Report("external-window.json", require_complete_phase=True), timeout=300,
          notes="A fresh material per run, so EEVEE compiles its shaders during the looks. No provider or account."),
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
    Suite("test_blender_linked_packed_redo.py", BACKGROUND, "Undo and Redo of a linked asset whose data uses packed "
          "linked data (a node group, a mesh, an image), and of a packed brush Undo keeps: Blender lives through it "
          "and the link returns as it was",
          Report("linked-packed-redo.json"),
          notes="Blender's own Undo, the add-on disabled first. Plain 5.2.3 and 5.3.0 died in the dependency "
                "graph's build on Redo: Undo freed the packed data and kept the linked data that used it (and "
                "freed a kept packed brush's library). The linked-only cases are the control and run first, since "
                "a crash ends the process."),
    Suite("test_blender_linked_packed_undo_chains.py", BACKGROUND, "Undo and Redo keep valid the packed linked data "
          "that kept linked data uses: relinked after a purge (same and changed asset), nested and cross-library "
          "chains, ID property and driver references, an archive library stored without the datablock",
          Report("linked-packed-undo-chains.json"),
          notes="Blender's own Undo, the add-on disabled first. Companion of test_blender_linked_packed_redo.py. "
                "Every state is evaluated and written; packed counts, libraries and duplicates are compared. Each "
                "case runs in a child Blender under Undo's log, which must show the keep paths it exists for."),
    Suite("test_blender_linked_packed_undo_data.py", BACKGROUND, "Undo keeps linked objects with their own packed "
          "object data whole: an armature with its pose, a mesh with its shape key, a mesh with its material slots, "
          "relinked after a purge (same and changed asset, libraries removed)",
          Report("linked-packed-undo-data.json"),
          notes="Blender's own Undo, the add-on disabled first. The object must keep the very data it was linked "
                "with, never one merely of the same name or content (pose channels point into its bones). Full-read "
                "cases (legacy undo, an asset import); each case in a child Blender, its keep paths checked in "
                "Undo's log."),
    Suite("test_blender_packed_link_same_second.py", BACKGROUND, "An asset file changed within the same second is a "
          "new version: linking from it packed takes the new content (copied with another size, copied with the "
          "same size and time, written by Blender)",
          Report("packed-link-same-second.json"),
          notes="Blender's packed data dedupe by deep hash, whose file hash was cached by path and modification "
                "time in whole seconds (id_hash.cc). Upstream candidate."),
    Suite("test_blender_particle_object_delete.py", BACKGROUND, "An object with several particle systems deleted "
          "together with their settings (removed, purged, linked packed): nothing reads freed memory",
          Report("particle-object-delete.json"),
          notes="Proves the fix only under AddressSanitizer (a release build reads the freed settings silently). "
                "Upstream candidate."),
)
# The suites on production_jobs_harness, which discounts Cycles' kernel loads (test_native_kernel_load keeps it whole).
KERNEL_LOAD_CAPPED = ("test_blender_disk_simulation.py", "test_blender_fluid_jobs.py", "test_blender_fluid_varied.py",
                      "test_blender_job_desktop.py", "test_blender_mantaflow.py", "test_blender_production_bakes.py",
                      "test_blender_production_renders.py", "test_blender_render_child.py",
                      "test_blender_render_deadline.py", "test_blender_render_deliverables.py",
                      "test_blender_render_kernels.py", "test_blender_render_samples.py", "test_blender_shot_video.py", "test_blender_udim_bakes.py")
SUITES = tuple(replace(suite, kernel_load_cap=KERNEL_LOAD_CAP_S) if suite.script in KERNEL_LOAD_CAPPED else suite
               for suite in SUITES)



def by_name():
    return {suite.name: suite for suite in SUITES}
