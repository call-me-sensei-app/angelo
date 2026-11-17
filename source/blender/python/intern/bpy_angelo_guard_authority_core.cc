/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The authority's tickets and ledger (`bpy_angelo_guard_authority_core.hh`).
 */

#include <algorithm>
#include <limits>

#include "bpy_angelo_guard_authority_core.hh"

namespace blender::angelo_guard {

/** Longest text value the ledger keeps (a policy's list, a digest, a client name). */
static constexpr size_t MAX_VALUE_TEXT = 4096;

static thread_local int scope_depth[3] = {0, 0, 0}; /* PERSON, AUTOMATIC, HARNESS. */

bool Authority::inside(const RegionSpec &region, const int x, const int y)
{
  return region.width > 0 && region.height > 0 && region.x <= x && x < region.x + region.width &&
         region.y <= y && y < region.y + region.height;
}

/** When frame `frame` of `window` reached the screen (`Authority::shown`), under the lock. */
static double frame_shown(const int presented,
                          const uint64_t drawing,
                          const double *presents,
                          const uint64_t frame,
                          const double drawn)
{
  if (presented == 0) {
    return drawn; /* A window that never swapped (no screen): the time it was drawn. */
  }
  if (frame >= drawing) {
    return std::numeric_limits<double>::infinity(); /* Not on screen yet. */
  }
  if (drawing - frame > uint64_t(presented)) {
    return -1.0e9; /* Older than the frames kept: on screen longer than any arm waits. */
  }
  return presents[frame % PRESENTS_KEPT];
}

double Authority::region_shown(const Window &window, const Region &region)
{
  /* Its first frame on screen, or later when the window's history started again (`sync`). */
  return std::max(region.since,
                  frame_shown(window.presented, window.frame, window.presents, region.frame,
                              region.since));
}

void Authority::note_presented(const WindowKey window_key, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  window.presents[window.frame % PRESENTS_KEPT] = now;
  window.frame++;
  window.presented = std::min(window.presented + 1, PRESENTS_KEPT);
}

uint64_t Authority::drawing_frame(const WindowKey window_key) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  return found == windows_.end() ? 0 : found->second.frame;
}

uint64_t Authority::epoch(const WindowKey window_key) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  return found == windows_.end() ? 0 : found->second.epoch;
}

double Authority::shown(const WindowKey window_key, const uint64_t frame, const double drawn) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  if (found == windows_.end()) {
    return drawn;
  }
  const Window &window = found->second;
  return frame_shown(window.presented, window.frame, window.presents, frame, drawn);
}

void Authority::note_unarmed_button(const WindowKey window_key,
                                    const std::string &idname,
                                    const int x,
                                    const int y,
                                    const int width,
                                    const int height,
                                    const double now)
{
  if (idname.size() > MAX_TEXT) {
    return;
  }
  std::lock_guard lock(mutex_);
  unarmed_ = {window_key, x, y, width, height, idname, std::string(), now};
}

std::optional<Authority::Unarmed> Authority::take_unarmed(const std::string &idname,
                                                          const std::string &digest,
                                                          const double now)
{
  std::lock_guard lock(mutex_);
  if (unarmed_.idname.empty() || unarmed_.idname != idname ||
      now - unarmed_.at > UNARMED_SECONDS || now < unarmed_.at ||
      (!unarmed_.digest.empty() && unarmed_.digest != digest))
  {
    return std::nullopt;
  }
  Unarmed press = unarmed_;
  unarmed_ = Unarmed();
  return press;
}

void Authority::sync(Window &window, const double now)
{
  for (auto &surface : window.surfaces) {
    for (Region &region : surface.second) {
      if (region.epoch != window.epoch) {
        region.epoch = window.epoch;
        region.since = now;
        region.entered = false;
        region.moved_in = false;
      }
    }
  }
}

bool Authority::set_regions(const WindowKey window_key,
                            const std::string &surface,
                            const std::vector<RegionSpec> &regions,
                            const double now)
{
  if (surface.size() > MAX_TEXT || regions.size() > size_t(MAX_REGIONS_PER_SURFACE)) {
    return false;
  }
  for (const RegionSpec &spec : regions) {
    if (spec.idname.size() > MAX_TEXT || spec.digest.size() > MAX_TEXT) {
      return false;
    }
  }
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  auto existing = window.surfaces.find(surface);
  if (existing == window.surfaces.end() && window.surfaces.size() >= size_t(MAX_SURFACES)) {
    return false;
  }
  sync(window, now);
  /* The pointer rests where code put it: the person hasn't moved since, or hasn't left the
   * control it landed in (redrawn there with new values, it is the same trap). */
  bool warped_here = window.warped;
  for (const auto &drawn : window.surfaces) {
    for (const Region &before : drawn.second) {
      warped_here = warped_here || (before.warped && inside(before.spec, window.px, window.py));
    }
  }
  warped_here = warped_here && window.pointer_known;
  std::vector<Region> next;
  next.reserve(regions.size());
  for (const RegionSpec &spec : regions) {
    Region region{spec, now, window.frame, window.epoch, false};
    /* Drawn under that pointer: as warped into. */
    region.warped = warped_here && inside(spec, window.px, window.py);
    if (existing != window.surfaces.end()) {
      for (const Region &before : existing->second) {
        const RegionSpec &old = before.spec;
        if (old.idname == spec.idname && old.digest == spec.digest && old.x == spec.x &&
            old.y == spec.y && old.width == spec.width && old.height == spec.height &&
            old.shield == spec.shield)
        {
          region = before; /* Drawn where it was: its shield and its move into it stand. */
          region.spec = spec;
          break;
        }
      }
    }
    next.push_back(std::move(region));
  }
  window.surfaces[surface] = std::move(next);
  return true;
}

void Authority::note_move(const WindowKey window_key, const int x, const int y, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  sync(window, now);
  if (!window.button_down) { /* A drag's hold ends once its button is up, release seen or not. */
    std::erase_if(tickets_, [](const Ticket &ticket) { return ticket.gesture; });
  }
  if (!window.pointer_known) { /* Where it came from is unknown: never a move into anything. */
    window.pointer_known = true;
    window.px = x;
    window.py = y;
    return;
  }
  if (x == window.px && y == window.py) {
    return; /* A refresh, not a move. */
  }
  window.warped = false; /* The person moved it. */
  window.real_move_at = now;
  window.real_x = x;
  window.real_y = y;
  if (window.warp_live) { /* Where it has been since code put it somewhere (`warped_into`). */
    window.warp_x0 = std::min(window.warp_x0, x);
    window.warp_x1 = std::max(window.warp_x1, x);
    window.warp_y0 = std::min(window.warp_y0, y);
    window.warp_y1 = std::max(window.warp_y1, y);
  }
  for (auto &surface : window.surfaces) {
    for (Region &region : surface.second) {
      if (!inside(region.spec, x, y)) {
        region.warped = false; /* Left it: the person's next move in counts again. */
        continue;
      }
      if (region.warped) {
        continue;
      }
      const double shown = region_shown(window, region);
      if (now - shown >= ARM_SECONDS) {
        region.moved_in = true; /* A real move inside it once it has been there ARM_SECONDS. */
      }
      if (now - shown >= SHIELD_SECONDS && !inside(region.spec, window.px, window.py)) {
        region.entered = true;
      }
    }
  }
  window.px = x;
  window.py = y;
}

void Authority::note_deactivate(const WindowKey window_key, const double /*now*/)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  window.epoch++;
  window.returning = true;
  window.button_down = false;
  window.focus_idname.clear(); /* Focus comes back only by the person's hand. */
  window.focus_digest.clear();
  window.focus_surface.clear();
  window.opened.clear(); /* A popover the person opened closes with focus. */
  std::erase_if(tickets_, [](const Ticket &ticket) { return ticket.gesture; });
}

void Authority::note_warp(const WindowKey window_key, const int x, const int y, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  sync(window, now);
  for (auto &surface : window.surfaces) {
    for (Region &region : surface.second) {
      if (inside(region.spec, x, y)) {
        region.entered = false; /* Only the person's own move in from outside arms it again. */
        region.warped = true;
      }
    }
  }
  window.pointer_known = true;
  window.warped = true;
  window.px = x;
  window.py = y;
  window.warp_live = true;
  window.warp_x0 = window.warp_x1 = x;
  window.warp_y0 = window.warp_y1 = y;
}

bool Authority::moved_here_since(const WindowKey window_key,
                                 const int x,
                                 const int y,
                                 const double since) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  return found != windows_.end() && found->second.real_x == x && found->second.real_y == y &&
         found->second.real_move_at >= since;
}

bool Authority::warped_into(
    const WindowKey window_key, const int x, const int y, const int width, const int height) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  if (found == windows_.end() || !found->second.warp_live) {
    return false;
  }
  const Window &window = found->second;
  return window.warp_x0 >= x && window.warp_x1 < x + width && window.warp_y0 >= y &&
         window.warp_y1 < y + height;
}

void Authority::hold_gesture(const std::string &idname,
                             const std::string &digest,
                             const double now)
{
  if (idname.size() > MAX_TEXT || digest.size() > MAX_TEXT) {
    return;
  }
  std::lock_guard lock(mutex_);
  push_ticket({idname, digest, now, TICKET_SECONDS, true});
}

void Authority::set_key_types(const KeyTypes &types)
{
  std::lock_guard lock(mutex_);
  keys_ = types;
}

void Authority::note_key(const WindowKey window_key, const int event_type, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  if (event_type == keys_.tab && keys_.tab != 0) {
    window.tabbed = now;
    return;
  }
  bool activates = false, moves = false;
  for (const int key : keys_.activate) {
    activates = activates || (key != 0 && key == event_type);
  }
  for (const int key : keys_.move) {
    moves = moves || (key != 0 && key == event_type);
  }
  if (moves) {
    window.moved = now; /* The person's own key moves the focus in their popover (`focus`). */
  }
  /* A movement key acts only on a moving control (a slider) of a popover the person opened. */
  activates = activates || (moves && window.focus_moves && !window.opened.empty() &&
                            window.focus_surface == window.opened);
  /* Losing window focus already cleared it; the person Tabs back to the control. */
  if (!activates || window.focus_idname.empty() || now - window.focus_since < SHIELD_SECONDS)
  {
    return;
  }
  push_ticket({window.focus_idname, window.focus_digest, now});
}

bool Authority::focus(const WindowKey window_key,
                      const std::string &surface,
                      const std::string &idname,
                      const std::string &digest,
                      const double now,
                      const bool moves)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  if (!idname.empty() && idname == window.focus_idname && digest == window.focus_digest &&
      surface == window.focus_surface)
  {
    window.focus_moves = moves;
    return true; /* The same focus reported again (a redraw): it stands, its shield as it was. */
  }
  if (idname.empty() && surface != window.focus_surface) {
    return false; /* Nothing focused on another surface: the focus held elsewhere stands. */
  }
  window.focus_idname.clear();
  window.focus_digest.clear();
  window.focus_surface.clear();
  window.focus_moves = false;
  if (idname.empty() || idname.size() > MAX_TEXT || digest.size() > MAX_TEXT) {
    return false;
  }
  /* In a popover or list the person opened, its focus moves only by their keys or pointer. */
  bool persons = (!window.opened.empty() && surface == window.opened) ||
                 (now - window.tabbed <= FOCUS_SECONDS && now >= window.tabbed);
  if (!persons && now - window.pressed <= FOCUS_SECONDS && now >= window.pressed) {
    const auto found = window.surfaces.find(surface);
    if (found != window.surfaces.end()) {
      for (const Region &region : found->second) {
        persons = persons || (region.spec.idname == idname && region.spec.digest == digest &&
                              inside(region.spec, window.press_x, window.press_y));
      }
    }
  }
  if (!persons) {
    return false; /* Code moved it: no key on it grants. */
  }
  window.focus_idname = idname;
  window.focus_digest = digest;
  window.focus_surface = surface;
  window.focus_moves = moves;
  /* Focus the person's own key just moved inside their popover is theirs at once (Down, Down,
   * Return); focus that arrived any other way waits the shield. */
  const bool keyed = !window.opened.empty() && surface == window.opened &&
                     now - window.moved <= FOCUS_SECONDS && now >= window.moved;
  window.focus_since = keyed ? now - SHIELD_SECONDS : now;
  return true;
}

void Authority::open_surface(const WindowKey window_key, const std::string &surface)
{
  if (surface.empty() || surface.size() > MAX_TEXT) {
    return;
  }
  std::lock_guard lock(mutex_);
  windows_[window_key].opened = surface;
}

void Authority::close_surface(const WindowKey window_key, const std::string &surface)
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  if (found == windows_.end() || found->second.opened != surface) {
    return;
  }
  found->second.opened.clear();
  if (found->second.focus_surface == surface) { /* Its focus goes with it. */
    found->second.focus_idname.clear();
    found->second.focus_digest.clear();
    found->second.focus_surface.clear();
  }
}

void Authority::note_agent_run()
{
  std::lock_guard lock(mutex_);
  for (auto &[key, window] : windows_) {
    window.tabbed = window.pressed = -1.0e9;
    window.focus_idname.clear();
    window.focus_digest.clear();
    window.focus_surface.clear();
    window.opened.clear(); /* A popover open before the script is no longer the person's. */
    window.moved = -1.0e9;
  }
  /* A gesture's next step, and a dialog's confirm, were the person's for what they saw: after an
   * agent turn they need a fresh press (the reviewer's read of f5604750e18). */
  std::erase_if(tickets_, [](const Ticket &ticket) { return ticket.gesture || ticket.dialog; });
}

void Authority::note_outside_work()
{
  std::lock_guard lock(mutex_);
  /* Work from outside the person's input landed (a generation's result, a job's end, an import): a
   * dialog's confirm and a gesture's next step were for what the person saw before it. Their own
   * presses, moves and focus stay theirs: outside work synthesizes no input (the review of a6edbf3). */
  std::erase_if(tickets_, [](const Ticket &ticket) { return ticket.gesture || ticket.dialog; });
}

void Authority::allow_automatic(const std::string &idname)
{
  std::lock_guard lock(mutex_);
  if (idname.size() <= MAX_TEXT &&
      std::find(automatic_.begin(), automatic_.end(), idname) == automatic_.end())
  {
    automatic_.push_back(idname);
  }
}

bool Authority::automatic_allows(const std::string &idname) const
{
  std::lock_guard lock(mutex_);
  return std::find(automatic_.begin(), automatic_.end(), idname) != automatic_.end();
}

void Authority::note_release(const WindowKey window_key, const double /*now*/)
{
  std::lock_guard lock(mutex_);
  windows_[window_key].button_down = false;
}

bool Authority::note_press(const WindowKey window_key, const int x, const int y, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  window.pressed = now; /* Where the person pressed: a focus report on that control is theirs. */
  window.button_down = true;
  window.press_x = x;
  window.press_y = y;
  if (window.returning) { /* The click that brings the window forward, wherever it lands. */
    window.returning = false;
    window.epoch++;
    sync(window, now);
    return false;
  }
  sync(window, now);
  const Region *hit = nullptr;
  for (const auto &surface : window.surfaces) {
    for (const Region &region : surface.second) {
      if (inside(region.spec, x, y)) {
        if (hit != nullptr) {
          return false; /* Two regions under one press: which one was meant is unknown. */
        }
        hit = &region;
      }
    }
  }
  if (hit == nullptr) {
    return false;
  }
  if (hit->warped || !hit->moved_in ||
      (hit->spec.shield && (!hit->entered || now - region_shown(window, *hit) < SHIELD_SECONDS)))
  {
    /* Not armed: a pointer code put there, a press too soon after it was shown, or no move onto it
     * since. The guard answers it quietly with a hint on the control (`take_unarmed`). */
    unarmed_ = {window_key, hit->spec.x, hit->spec.y, hit->spec.width, hit->spec.height,
                hit->spec.idname, hit->spec.digest, now};
    return false;
  }
  if (unarmed_.window == window_key) {
    unarmed_ = Unarmed(); /* The person's armed press supersedes their too-quick one. */
  }
  push_ticket({hit->spec.idname, hit->spec.digest, now});
  return true;
}

void Authority::mint(const std::string &idname,
                     const std::string &digest,
                     const double now,
                     const double lasts)
{
  if (idname.size() > MAX_TEXT || digest.size() > MAX_TEXT) {
    return;
  }
  std::lock_guard lock(mutex_);
  push_ticket({idname, digest, now, lasts});
}

void Authority::forget_window(const WindowKey window_key)
{
  std::lock_guard lock(mutex_);
  windows_.erase(window_key);
  if (unarmed_.window == window_key) {
    unarmed_ = Unarmed();
  }
}

void Authority::push_ticket(const Ticket &ticket)
{
  tickets_.push_back(ticket);
  if (tickets_.size() > size_t(MAX_TICKETS)) {
    /* The oldest press goes first; a dialog's ticket waits for its confirm. */
    auto oldest = std::find_if(tickets_.begin(), tickets_.end(), [](const Ticket &t) {
      return t.lasts <= TICKET_SECONDS;
    });
    tickets_.erase(oldest != tickets_.end() ? oldest : tickets_.begin());
  }
}

bool Authority::carry(const std::string &idname,
                      const std::string &from,
                      const std::string &to,
                      const double now,
                      const bool browse)
{
  if (to.size() > MAX_TEXT || !take(idname, from, now)) {
    return false;
  }
  std::lock_guard lock(mutex_);
  push_ticket({idname, to, now, browse ? BROWSE_SECONDS : DIALOG_SECONDS, false, true});
  return true;
}

void Authority::hold_dialog(const std::string &idname,
                            const std::string &to,
                            const double now,
                            const bool browse)
{
  if (idname.size() > MAX_TEXT || to.size() > MAX_TEXT) {
    return;
  }
  std::lock_guard lock(mutex_);
  push_ticket({idname, to, now, browse ? BROWSE_SECONDS : DIALOG_SECONDS, false, true});
}

bool Authority::peek(const std::string &idname, const std::string &digest, const double now)
{
  std::lock_guard lock(mutex_);
  for (const Ticket &ticket : tickets_) {
    if (ticket.idname == idname && ticket.digest == digest && now >= ticket.minted &&
        now - ticket.minted <= ticket.lasts)
    {
      return true;
    }
  }
  return false;
}

bool Authority::take(const std::string &idname, const std::string &digest, const double now)
{
  std::lock_guard lock(mutex_);
  std::erase_if(tickets_, [&](const Ticket &ticket) {
    return now - ticket.minted > ticket.lasts || ticket.minted > now;
  });
  for (auto it = tickets_.begin(); it != tickets_.end(); ++it) {
    if (it->idname == idname && it->digest == digest) {
      tickets_.erase(it);
      return true;
    }
  }
  return false;
}

bool Authority::define(const std::string &name, const Value &narrow)
{
  if (name.size() > MAX_TEXT) {
    return false;
  }
  std::lock_guard lock(mutex_);
  return ledger_.emplace(name, Namespace{narrow, {}}).second;
}

std::optional<Value> Authority::get(const std::string &name, const std::string &key) const
{
  std::lock_guard lock(mutex_);
  const auto ns = ledger_.find(name);
  if (ns == ledger_.end()) {
    return std::nullopt;
  }
  const auto value = ns->second.values.find(key);
  if (value == ns->second.values.end()) {
    return std::nullopt;
  }
  return value->second;
}

std::vector<std::pair<std::string, Value>> Authority::items(const std::string &name) const
{
  std::lock_guard lock(mutex_);
  std::vector<std::pair<std::string, Value>> result;
  const auto ns = ledger_.find(name);
  if (ns != ledger_.end()) {
    result.assign(ns->second.values.begin(), ns->second.values.end());
  }
  return result;
}

SetResult Authority::set(const std::string &name,
                         const std::string &key,
                         const Value &value,
                         const bool granting)
{
  const std::string *text = std::get_if<std::string>(&value);
  if (key.size() > MAX_TEXT || (text != nullptr && text->size() > MAX_VALUE_TEXT)) {
    return SetResult::Full;
  }
  std::lock_guard lock(mutex_);
  const auto ns = ledger_.find(name);
  if (ns == ledger_.end()) {
    return SetResult::UnknownNamespace;
  }
  auto &values = ns->second.values;
  const auto existing = values.find(key);
  if (existing != values.end() && existing->second == value) {
    return SetResult::Unchanged;
  }
  if (!(value == ns->second.narrow) && !granting) {
    return SetResult::Refused;
  }
  if (existing == values.end()) {
    if (ledger_keys_ >= MAX_LEDGER_KEYS) {
      return SetResult::Full;
    }
    ledger_keys_++;
    values.emplace(key, value);
  }
  else {
    existing->second = value;
  }
  return SetResult::Set;
}

bool Authority::remove(const std::string &name, const std::string &key)
{
  std::lock_guard lock(mutex_);
  const auto ns = ledger_.find(name);
  if (ns == ledger_.end() || ns->second.values.erase(key) == 0) {
    return false;
  }
  ledger_keys_--;
  return true;
}

void Authority::define_standard()
{
  define(APPROVED_SCRIPTS, Value());
  define(TRUSTED_CHATS, Value(false));
  define(ALWAYS_ALLOW, Value(false));
  define(CHAT_CLIENTS, Value());
  define(SCRIPT_FILES, Value(false));
  define(APPROVED_CALLS, Value());
  define(APPROVAL_MODES, Value(std::string("ASK"))); /* Ask narrows; Auto is set only in a scope. */
}

bool Authority::admits(const std::string &call_id, const std::string &digest)
{
  if (digest.empty() || call_id.empty()) {
    return false;
  }
  std::lock_guard lock(mutex_);
  const auto space = [&](const char *name) -> std::map<std::string, Value> * {
    const auto ns = ledger_.find(name);
    return ns == ledger_.end() ? nullptr : &ns->second.values;
  };
  if (auto *approved = space(APPROVED_SCRIPTS)) {
    const auto found = approved->find(call_id);
    if (found != approved->end() && found->second == Value(digest)) {
      approved->erase(found); /* That exact text, once. */
      ledger_keys_--;
      return true;
    }
  }
  return false;
}

void Authority::reset()
{
  std::lock_guard lock(mutex_);
  automatic_.clear();
  windows_.clear();
  tickets_.clear();
  ledger_.clear();
  ledger_keys_ = 0;
  unarmed_ = Unarmed();
}

std::string to_hex(const uint8_t digest[32])
{
  static constexpr char digits[] = "0123456789abcdef";
  std::string hex(64, '0');
  for (int i = 0; i < 32; i++) {
    hex[2 * i] = digits[digest[i] >> 4];
    hex[2 * i + 1] = digits[digest[i] & 0xf];
  }
  return hex;
}

Authority &authority()
{
  static Authority one;
  return one;
}

Scope::Scope(const Kind kind) : kind_(kind)
{
  scope_depth[kind_]++;
}

Scope::~Scope()
{
  scope_depth[kind_]--;
}

bool Scope::active()
{
  return scope_depth[PERSON] > 0 || scope_depth[AUTOMATIC] > 0 || scope_depth[HARNESS] > 0;
}

bool Scope::person()
{
  return scope_depth[PERSON] > 0 || scope_depth[HARNESS] > 0;
}

bool Scope::admits_operator(const Authority &authority, const std::string &idname)
{
  if (scope_depth[PERSON] > 0 || scope_depth[HARNESS] > 0) {
    return true;
  }
  return scope_depth[AUTOMATIC] > 0 && authority.automatic_allows(idname);
}

}  // namespace blender::angelo_guard
