/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Who may grant in Angelo, held where Python cannot write it (doc/angelo/grant_operators.rst,
 * "Authority in C"). Found by the Copy setup break-it re-review (2026-11-14): every guard that
 * Python held (tokens, wrappers, caches, the consent flags themselves) could be undone by Python
 * in the same interpreter, an agent script included.
 *
 * Two things live here, both plain C++ (no Python, no window manager), so GTests drive them:
 *
 * - **Tickets.** Angelo's own canvases tell the authority where each control that grants is drawn
 *   (a *consent region*: window, rectangle in window pixels, the operator's idname and a digest of
 *   its arguments). A ticket is minted only from a pointer press the window manager reports
 *   (`note_press`) on a region that is *armed*: drawn where it is for `SHIELD_SECONDS`, the pointer
 *   moved into it from outside since, and the window's focus unchanged since; the first press
 *   after the window lost focus never mints one. A ticket is single use, bound to its idname and
 *   digest, and lives `TICKET_SECONDS`. Keys, keymap items and buttons a script draws never mint
 *   one: only a press inside a region Angelo registered does.
 * - **The ledger.** The grant state itself (approval mode and script trust per chat, Always allow
 *   scripts, approved calls, external access, script-file approvals, consent leases, the loaded
 *   managed policy): namespaces of keys to plain values. A namespace is defined once with the
 *   value that narrows it; a value may move to that narrow value at any time, and to anything else
 *   only inside a grant scope (`Scope`, opened by a taken ticket or Angelo's own automatic
 *   decision) on the same thread.
 *
 * Every call takes `now` (monotonic seconds) so the tests own the clock. Thread-safe. Standard
 * library only, so its GTests also build on their own (`tests/angelo_guard/build_core_tests.sh`).
 */

#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace blender::angelo_guard {

/** As `approval_wait.SHIELD_SECONDS`: Blender's longest double-click time. */
inline constexpr double SHIELD_SECONDS = 1.0;
/** Any other grant control mints only for a press after a real pointer move inside it at least
 * this long after it was drawn there (the coordinator's ruling on the review of d3024be). */
inline constexpr double ARM_SECONDS = 0.3;
/** The press that brings a window forward arrives with its activation: at most this long after it,
 * with nothing between but the activation's own synthetic move (the re-review of acd0d465ee9). */
inline constexpr double ACTIVATION_PRESS_SECONDS = 0.5;
/** How long a ticket waits for the release that runs its control (a press may be held a while). */
inline constexpr double TICKET_SECONDS = 5.0;
/** Bounds on what Python may register or store (a misbehaving caller cannot exhaust memory). */
inline constexpr int MAX_REGIONS_PER_SURFACE = 256;
inline constexpr int MAX_SURFACES = 64;
inline constexpr int MAX_TEXT = 256;
inline constexpr int MAX_LEDGER_KEYS = 4096;
inline constexpr int MAX_TICKETS = 16;
/** How soon after a real Tab (or a press inside the control) a focus report counts as the person's. */
inline constexpr double FOCUS_SECONDS = 0.5;
/** How long a dialog an operator opened for the person may wait for its confirm: what reading it
 * and pressing OK takes, a slow reader's too (the reviewer's reads of f5604750e18 and 927cd1a81a4).
 * Any agent change (a script's run, a typed tool, a native job) ends it sooner, so this bounds
 * only a dialog left idle. */
inline constexpr double DIALOG_SECONDS = 180.0;
/** The same for a file browser an operator opened: the person browses to a folder or file. */
inline constexpr double BROWSE_SECONDS = 300.0;
/** How long the person's press on a control not yet armed stays recorded for the guard to answer
 * it quietly with a hint on the control (the coordinator's ruling on the 0.3 s arm, (a)). */
inline constexpr double UNARMED_SECONDS = 1.0;
/** Presented frames a window remembers: older ones were on screen longer than any arm waits. */
inline constexpr int PRESENTS_KEPT = 32;

/** Keys that activate a focused control, and the keys that move focus (window manager event
 * types). `move`: the arrows, Home and End, which move focus (and change a slider) inside a
 * popover or list the person opened (`open_surface`). */
struct KeyTypes {
  int tab = 0, activate[3] = {0, 0, 0}, move[6] = {0, 0, 0, 0, 0, 0};
};

using WindowKey = uintptr_t;

struct RegionSpec {
  int x = 0, y = 0, width = 0, height = 0; /* Window pixels, origin bottom left (wmEvent.xy). */
  std::string idname;                      /* "angelo.approve" */
  std::string digest;                      /* Of the arguments the press grants (hex). */
  /** An approval's control (Approve, Always allow, Accept): armed only after the shield and a move
   * into it. Without it (a menu's choice, a sign-in row) a press inside it is enough. */
  bool shield = true;
};

/** A ledger value: nothing, a flag, a whole number, a number or text. */
using Value = std::variant<std::monostate, bool, int64_t, double, std::string>;

enum class SetResult { Set, Unchanged, Refused, UnknownNamespace, Full };

/**
 * The ledger's standard namespaces, defined at start-up by `define_standard`. Only APPROVED_SCRIPTS
 * admits a script (`Authority::admits`); the others are the standing consents Angelo reads when it
 * decides to approve one. A call id is "<chat id>/<call>"; a chat id never holds a "/".
 */
inline constexpr const char *APPROVED_SCRIPTS = "approved_scripts"; /* call id -> digest (hex). */
inline constexpr const char *TRUSTED_CHATS = "trusted_scripts";     /* chat id -> true. */
inline constexpr const char *ALWAYS_ALLOW = "always_allow";         /* "on" -> true. */
inline constexpr const char *CHAT_CLIENTS = "chat_clients";         /* chat id -> external client. */
inline constexpr const char *APPROVED_CALLS = "approved_calls"; /* call id -> invocation digest. */
inline constexpr const char *APPROVAL_MODES = "approval_modes"; /* chat id -> "AUTO_BOUNDED". */
/** Always run this exact script in this project (2.1.0): "project/digest" -> true. */
inline constexpr const char *REVIEWED_SCRIPTS = "reviewed_scripts";
/** Folder as context (2.1.0): chat id -> the digest of the folder's real path (hex). */
inline constexpr const char *CONTEXT_FOLDERS = "context_folders";
/** Standing rules (2.1.0): a text's digest -> Use (true) or Don't use (false). */
inline constexpr const char *RULE_DECISIONS = "rule_decisions";
/** Standing rules (2.1.0): "text" -> the digest of the All projects text (hex). */
inline constexpr const char *GLOBAL_RULES = "global_rules";

class Authority {
 public:
  /* Consent regions and input. */

  /**
   * The regions `surface` (a canvas or card host, per window) draws now, replacing what it drew
   * before. A region new to it, or drawn elsewhere than before, starts its shield again and needs
   * a move into it. Refused (false) when a text is too long or there are too many. `owner`: the
   * Blender region it draws them in (the review of f6b01a9..da9ba66, SHOULD 1; the maintainer: "Fix
   * properly in 2.1.0"): they take only a move or press Blender's own routing gives that region
   * (`note_move`'s `target`), never one a sidebar, header, HUD, asset shelf or popup drawn over
   * them takes. 0 only in tests: the binding requires a region.
   */
  bool set_regions(WindowKey window,
                   const std::string &surface,
                   const std::vector<RegionSpec> &regions,
                   double now,
                   uint64_t owner = 0);
  /**
   * The pointer at (x, y) in `window`: a move from outside an armed-in-time region into it. `target`:
   * the region Blender's own event routing gives the pointer there (`wm_event_pointer_region`: a
   * temporary region over it first, then the area's overlapping regions where they are opaque, then
   * the region under it; 0 for none). Only a surface whose `owner` it is sees the pointer.
   */
  void note_move(WindowKey window, int x, int y, uint64_t target, double now);
  void note_move(WindowKey window, int x, int y, double now)
  {
    note_move(window, x, y, 0, now);
  }
  /** `window` lost focus: every region in it starts again, and its next press mints nothing. */
  void note_deactivate(WindowKey window, double now);
  /**
   * `window` came forward (the window manager's activation, before its synthetic pointer move). Its
   * regions start their shield again. Only a press that arrives with it (`ACTIVATION_PRESS_SECONDS`,
   * nothing between but that move) is the click that brought the window forward; an activation by
   * the keyboard, the Dock or another app leaves none, so the person's next press is an ordinary one.
   */
  void note_activate(WindowKey window, double now);
  /** When `window`'s focus epoch last changed (lost focus, came forward): what a control placed before it
   * counts again from, still on screen (`ui_angelo_follow_focus`, as `sync` restarts a region). */
  double epoch_changed(WindowKey window) const;
  /** What a control placed before `window`'s last focus change counts again from: that change while the
   * window is in front (it was seen from then), `now` while it is in the back or unknown (the re-review of
   * cf980b8, SHOULD A and NIT E). */
  double restart_from(WindowKey window, double now) const;
  /** Whether the last press in `window` was the one that brought it forward (it aims, never grants). */
  bool press_activating(WindowKey window) const;
  /** Whether `window` lost focus and has taken no event since that ends its window-forward click. */
  bool returning(WindowKey window) const;
  /** A real pointer press: mints a ticket when it lands on an armed region of a surface whose `owner`
   * is `target` (the region Blender gives the press, as `note_move`). True when minted. */
  bool note_press(WindowKey window, int x, int y, uint64_t target, double now);
  bool note_press(WindowKey window, int x, int y, double now)
  {
    return note_press(window, x, y, 0, now);
  }
  /** The person's button came up: a gesture's hold ends at the next pointer move. */
  void note_release(WindowKey window, double now);
  /**
   * The person's keyboard (the maintainer's ruling, 2026-11-14: "Yes, guarded"). `note_key` takes a
   * real key press: Tab or Shift+Tab marks that focus is moving by the person's hand; an activation
   * key (Return, Space, keypad Enter) on a focused control mints its ticket once the shield has passed
   * since focus arrived. `focus` is a surface's report of the control that has focus now; it is the
   * person's only right after their own Tab or their own press inside that control, else focus is
   * cleared and no key mints anything. A surface's report of no focus clears only focus it holds.
   */
  void set_key_types(const KeyTypes &types);
  void note_key(WindowKey window, int event_type, double now);
  /** `moves`: a movement key acts on this control itself (a slider), so it mints its ticket. */
  bool focus(WindowKey window,
             const std::string &surface,
             const std::string &idname,
             const std::string &digest,
             double now,
             bool moves = false);
  /**
   * The pointer was moved by code, not by the person (`WM_cursor_warp`: Blender's own grabs, or
   * any script): the 2.0.0 break-it review's clickjacking. A region it lands in takes no press,
   * shielded or not, until the pointer has left it and come back by the person's own move.
   */
  void note_warp(WindowKey window, int x, int y, double now);
  /** Whether the pointer is in (x, y, width, height) only because code put it there: warped into
   * it and every position since inside it (Blender's own buttons ask this as they arm). */
  bool warped_into(WindowKey window, int x, int y, int width, int height) const;
  /** Whether the person's last real pointer move (a non-zero delta, not a warp) in `window` ended
   * at (x, y) at `since` or later: a Blender button's hover is the person's move only then. */
  bool moved_here_since(WindowKey window, int x, int y, double since) const;
  /**
   * A frame of `window` reached the screen (its buffers swapped). A control counts its
   * `ARM_SECONDS` from the frame that first showed it, never from when it was made or drawn (the
   * coordinator's ruling on the 0.3 s arm, (b)): a slow first draw takes none of the person's
   * time.
   */
  void note_presented(WindowKey window, double now);
  /** The frame `window` is drawing now (the next one `note_presented` counts). */
  uint64_t drawing_frame(WindowKey window) const;
  /** How many times `window` lost focus (`note_deactivate`): a control armed before the last one starts again. */
  uint64_t epoch(WindowKey window) const;
  /** When frame `frame` of `window` reached the screen: infinity while it hasn't; long ago for
   * one older than the frames kept; `drawn` in a window that never presented one (no swap). */
  double shown(WindowKey window, uint64_t frame, double drawn) const;
  /** The person's press on a control not yet armed (too soon after it was shown, or no move onto
   * it since): which control and where, in window pixels, so the guard answers it quietly with a
   * hint on that control instead of an error (the ruling's (a)). One at a time, the last. */
  struct Unarmed {
    WindowKey window = 0;
    int x = 0, y = 0, width = 0, height = 0;
    std::string idname, digest; /* A Blender button's: its operator or "prop.<name>"; no digest. */
    double at = -1.0e9;
  };
  /** The person's press on Blender's own button for `idname` that was not armed. */
  void note_unarmed_button(WindowKey window,
                           const std::string &idname,
                           int x,
                           int y,
                           int width,
                           int height,
                           double now);
  /** The unarmed press on `idname` with `digest` (any, for a Blender button's), if fresh: taken
   * (single use). */
  std::optional<Unarmed> take_unarmed(const std::string &idname,
                                      const std::string &digest,
                                      double now);
  /**
   * A gesture's consent for its next step (a slider's drag), in the person's scope only: it lasts
   * `TICKET_SECONDS` and goes when any window loses focus or an agent script runs.
   */
  void hold_gesture(const std::string &idname, const std::string &digest, double now);
  /**
   * An agent script began running: every window's Tab and press before it, and the focus they gave, stop counting as
   * the person's. A script can change the draw state a surface reports focus from (data it changed in place); a
   * report after it needs the person's own Tab or press again.
   */
  void note_agent_run();
  /** Work from outside the person's input landed: a dialog's held consent and a gesture's hold end,
   * and nothing else (the person's own presses, moves and focus stay theirs). */
  void note_outside_work();
  /**
   * A popover or list the person opened (the coordinator's keyboard ruling, 2026-11-16): `surface`
   * was opened in the scope of the person's own ticketed press or key (the binding checks that
   * scope; a popover code opened is never one). Until it closes, or an agent script runs, every
   * focus it reports is the person's, and a movement key (`KeyTypes::move`) on that focus mints
   * its ticket as an activation key does, once the shield has passed since focus arrived.
   */
  void open_surface(WindowKey window, const std::string &surface);
  /** The popover or list closed (narrowing: anything may say so). */
  void close_surface(WindowKey window, const std::string &surface);
  /** Operators Angelo's own automatic decision may run (add-only): the candidate's Approve and its step. */
  void allow_automatic(const std::string &idname);
  bool automatic_allows(const std::string &idname) const;
  /** A window closed: its regions and focus history go. */
  void forget_window(WindowKey window);

  /** A ticket for a real press on Blender's own button (no region: the button is Blender's). */
  void mint(const std::string &idname,
            const std::string &digest,
            double now,
            double lasts = TICKET_SECONDS);
  /**
   * The person's press ticket for `idname` (digest `from`) becomes one for the dialog it opens
   * (digest `to`, naming that operator instance), which lasts `DIALOG_SECONDS` (`BROWSE_SECONDS`
   * for a file browser): the person confirms there. Any agent run ends it. False, and nothing
   * changes, without the press ticket.
   */
  bool carry(const std::string &idname,
             const std::string &from,
             const std::string &to,
             double now,
             bool browse = false);
  /** In the person's scope, the dialog an operator opens keeps that consent (ticket `to`), as
   * `carry` does. */
  void hold_dialog(const std::string &idname, const std::string &to, double now, bool browse);
  /** Whether a fresh ticket for `idname` and `digest` exists (it stays). */
  bool peek(const std::string &idname, const std::string &digest, double now);
  /** Takes the one ticket for `idname` and `digest`, if fresh. Single use. */
  bool take(const std::string &idname, const std::string &digest, double now);

  /* The ledger. */

  /** Defines `name` with the value that narrows it. Once only; false when defined already. */
  bool define(const std::string &name, const Value &narrow);
  std::optional<Value> get(const std::string &name, const std::string &key) const;
  /** Keys of `name` with their values, sorted by key. */
  std::vector<std::pair<std::string, Value>> items(const std::string &name) const;
  /** The narrow value is always taken; any other only while `granting` (a grant scope is open). */
  SetResult set(const std::string &name,
                const std::string &key,
                const Value &value,
                bool granting);
  /** Removes `key`: narrowing, so always taken. */
  bool remove(const std::string &name, const std::string &key);

  /** Defines the namespaces `admits` reads, each narrowing to nothing. */
  void define_standard();
  /**
   * Whether the script whose source hashes to `digest` (SHA-256, lower-case hex) may run as call
   * `call_id`: only that exact text approved for that call, and once. A standing consent (a trusted
   * chat, Always allow, an allowed script file) is ledger state Angelo reads when it decides; it
   * admits nothing by itself, so a script a standing consent must not cover still asks.
   */
  bool admits(const std::string &call_id, const std::string &digest);

  /** Everything, for tests and a file load that drops every window's history. */
  void reset();

  /**
   * A temporary region of `window` (a menu, popover, dialog, pie, search box, tooltip; `key` its own) is
   * drawn over (x, y, width, height) window pixels: a consent region under it takes no move and no press
   * there (it is not what the person sees or clicks), and when the cover goes or moves, every region it
   * covered starts again from then and from the window's next frame (the review of cf980b8..f6b01a9,
   * SHOULD 3; the maintainer: "Prove + fix in 2.1.0").
   */
  void set_cover(WindowKey window,
                 uint64_t key,
                 int x,
                 int y,
                 int width,
                 int height,
                 double now,
                 bool restart_only = false);
  /** The temporary region `key` of `window` is gone: what it covered starts again (`set_cover`). */
  void clear_cover(WindowKey window, uint64_t key, double now);
  /** One drawn temporary region for `sync_covers`: its key and (x, y, width, height) window pixels.
   * `restart_only`: an area's overlapping region (a sidebar, toolbar, header, HUD, asset shelf drawn
   * over its area's main region). Event routing already keeps input over its opaque parts from what
   * is under it (`note_move`'s `target`), and its transparent parts pass input through, so it refuses
   * nothing; when it hides, shrinks or moves, what it covered starts again (the review of
   * f6b01a9..da9ba66, SHOULD 1). */
  struct DrawnCover {
    uint64_t key = 0;
    int x = 0, y = 0, width = 0, height = 0;
    bool restart_only = false;
  };
  /** Where cover `key` was (x, y, width, height window pixels). */
  struct Rect {
    uint64_t key = 0;
    int x = 0, y = 0, width = 0, height = 0;
  };
  /** `window` drew exactly these covers: each covers where it is (`set_cover`), and any other cover (a
   * region freed without `clear_cover`: a file load, a screen change, a sidebar hidden) goes
   * (`clear_cover`). Returns where each restart-only cover that went or moved was, so the window
   * manager restarts Blender's own buttons there too (a temporary region's close does that itself). */
  std::vector<Rect> sync_covers(WindowKey window, const std::vector<DrawnCover> &drawn, double now);
  /** As above, and `visible`: every region the window shows now (its areas' and its temporary regions). A canvas
   * surface's own menu whose region is not among them goes (a file load, a screen change, a close no code reported:
   * the last review of da9ba66..1b4e7f0, SHOULD 1), and so do the consent regions of a surface drawn in a region that
   * is gone (NIT 3: a freed region's pointer is never reused for them). */
  std::vector<Rect> sync_covers(WindowKey window,
                                const std::vector<DrawnCover> &drawn,
                                const std::vector<uint64_t> &visible,
                                double now);
  /** Angelo's own menu or popover on canvas surface `surface` is drawn over (x, y, width, height) window
   * pixels, or (`width` 0) is gone: a cover as a temporary region's, except over `surface`'s own regions
   * (the review of f6b01a9..da9ba66, SHOULD 2). It can only refuse and restart, never grant. */
  void surface_cover(WindowKey window,
                     const std::string &surface,
                     int x,
                     int y,
                     int width,
                     int height,
                     double now,
                     uint64_t owner = 0);

  /** What `window`'s arming reads now, for a GUI suite's failure details only (never a decision). */
  struct Readout {
    uint64_t epoch = 0, frame = 0;
    int presented = 0;
    double last_present = -1.0e9, activated = -1.0e9, real_move_at = -1.0e9, changed = -1.0e9;
    int real_x = 0, real_y = 0, px = 0, py = 0;
    bool known = false, active = true, returning = false, activation_move = false, pointer_known = false;
  };
  Readout readout(WindowKey window) const;

 private:
  struct Region {
    RegionSpec spec;
    double since = 0.0;
    uint64_t frame = 0; /* The frame it was first drawn in where it is (`shown`). */
    uint64_t epoch = 0;
    bool entered = false;
    bool warped = false; /* Code put the pointer in it (`note_warp`): no press until it leaves. */
    bool moved_in = false; /* A real move inside it, ARM_SECONDS after it was drawn there. */
    double press_aim = -1.0; /* When a press (not a move) aimed at it; a grant waits SHIELD_SECONDS after. */
  };
  struct Cover {
    int x = 0, y = 0, width = 0, height = 0; /* Window pixels: a temporary region drawn there. */
    std::string surface; /* A canvas surface's own menu (`surface_cover`): never over that surface's regions. */
    bool restart_only = false; /* An overlapping region: restarts what it covered, refuses nothing. */
    uint64_t owner = 0; /* A canvas menu's: the region it is drawn in (`sync_covers`'s `visible`). */
  };
  struct Window {
    uint64_t epoch = 0;
    double changed = -1.0e9;       /* When `epoch` last changed (`note_activate`, `note_deactivate`). */
    bool returning = false;
    double activated = -1.0e9;     /* When it came forward (`note_activate`). */
    bool active = true;            /* Not in the background (`note_deactivate` .. `note_activate`). */
    bool activation_move = false;  /* The activation's synthetic move is still to come. */
    bool press_activating = false; /* The last press was the one that brought it forward. */
    bool pointer_known = false;
    int px = 0, py = 0;
    uint64_t target = 0; /* The region Blender routes the pointer at (px, py) to (`note_move`). */
    double tabbed = -1.0e9, pressed = -1.0e9; /* The person's last Tab and last press, and where. */
    int press_x = 0, press_y = 0;
    uint64_t press_target = 0; /* The region Blender gave the last press (`note_press`). */
    std::string focus_idname, focus_digest, focus_surface;
    double focus_since = 0.0;
    std::string opened; /* The surface the person opened (`open_surface`), or empty. */
    bool focus_moves = false; /* The focus is a control a movement key acts on (`focus(moves)`). */
    bool warped = false; /* The pointer is where code put it; the person has not moved it since. */
    double moved = -1.0e9;    /* The person's last movement key. */
    bool button_down = false; /* Pressed, neither released nor focus lost: a drag holds. */
    bool warp_live = false; /* Since the last warp, the box every pointer position fell in. */
    double real_move_at = -1.0e9; /* The person's last real move, and where it ended. */
    int real_x = 0, real_y = 0;
    int warp_x0 = 0, warp_y0 = 0, warp_x1 = 0, warp_y1 = 0;
    std::map<std::string, std::vector<Region>> surfaces;
    std::map<std::string, uint64_t> owners; /* The Blender region each surface draws in (`set_regions`). */
    std::map<uint64_t, Cover> covers; /* The temporary regions drawn over it now (`set_cover`). */
    uint64_t frame = 0;   /* The frame being drawn; `note_presented` counts it and moves on. */
    int presented = 0;    /* How many frames reached the screen, up to PRESENTS_KEPT. */
    double presents[PRESENTS_KEPT] = {}; /* Frame f's time at presents[f % PRESENTS_KEPT]. */
  };
  /** Whether a cover of `window` is drawn over (x, y) for a region of `surface` (never its own). Under the lock. */
  static bool covered(const Window &window, int x, int y, const std::string &surface);
  /** Whether Blender routes input it gives region `target` to `surface` of `window` (its owner). */
  static bool owns(const Window &window, const std::string &surface, uint64_t target);
  /** Whether the pointer rests on `spec` of `surface`: inside it, over the region it is drawn in. */
  static bool pointer_on(const Window &window, const std::string &surface, const RegionSpec &spec);
  /** Every region of `window` under `cover` starts again from `now` and the window's next frame; a ticket its press
   * minted goes too (the last review of da9ba66..1b4e7f0, NIT 2). Under the lock. */
  void restart_covered(Window &window, const Cover &cover, double now);
  /** The tickets for `region`'s control go (not a dialog's, which names the dialog). Under the lock. */
  void drop_tickets(const RegionSpec &region);

  struct Ticket {
    std::string idname, digest;
    double minted = 0.0;
    double lasts = TICKET_SECONDS;
    bool gesture = false; /* `hold_gesture`'s: gone on focus loss and an agent run. */
    bool dialog = false;  /* `carry`'s or `hold_dialog`'s: gone on an agent run. */
  };
  struct Namespace {
    Value narrow;
    std::map<std::string, Value> values;
  };

  static bool inside(const RegionSpec &region, int x, int y);
  /** When `region` was first on screen where it is (`shown`), under the lock. */
  static double region_shown(const Window &window, const Region &region);
  void push_ticket(const Ticket &ticket); /* Under the lock. */
  void sync(Window &window, double now);
  std::vector<Rect> sync_covers(WindowKey window,
                                const std::vector<DrawnCover> &drawn,
                                const std::vector<uint64_t> *visible,
                                double now);

  mutable std::mutex mutex_;
  std::map<WindowKey, Window> windows_;
  std::vector<Ticket> tickets_;
  std::map<std::string, Namespace> ledger_;
  int64_t ledger_keys_ = 0;
  KeyTypes keys_;
  std::vector<std::string> automatic_;
  Unarmed unarmed_;
};

/** A SHA-256 digest (`bpy_angelo_guard_sha256.hh`) as lower-case hex, the form the ledger keeps. */
std::string to_hex(const uint8_t digest[32]);

/** The process's one authority (what the Python module and the window manager both reach). */
Authority &authority();

/**
 * A grant scope on this thread: a taken ticket or Angelo's own automatic decision opened it.
 * Nested scopes count. Never crosses threads (a thread a scope starts is not inside it).
 */
class Scope {
 public:
  /** PERSON: a ticket from the person's press or key. AUTOMATIC: Angelo's own decision (only the
   * operators `allow_automatic` lists). HARNESS: a test harness standing in for the person, only in an
   * app started with --enable-event-simulate (the binding checks). */
  enum Kind { PERSON = 0, AUTOMATIC = 1, HARNESS = 2 };
  explicit Scope(Kind kind = AUTOMATIC);
  ~Scope();
  Scope(const Scope &) = delete;
  Scope &operator=(const Scope &) = delete;
  static bool active();
  /** Whether the person's (or a test harness's) scope is open on this thread. */
  static bool person();
  /** Whether a test harness's scope is open on this thread (an app started with --enable-event-simulate). */
  static bool harness();
  /** The control the person's innermost grant scope on this thread was opened for (`grant`: "<idname>\n<digest>"),
   * else empty: a setting that only its own control may change asks this, not `person()`. */
  static const std::string &control();
  /** While alive, `control()` is `value` (``guard_grant``, around its person scope). */
  class Control {
   public:
    explicit Control(std::string value);
    ~Control();
    Control(const Control &) = delete;
    Control &operator=(const Control &) = delete;

   private:
    std::string previous_;
  };
  /** Whether operator `idname` may grant in the scopes open on this thread. */
  static bool admits_operator(const Authority &authority, const std::string &idname);

 private:
  Kind kind_;
};

}  // namespace blender::angelo_guard
