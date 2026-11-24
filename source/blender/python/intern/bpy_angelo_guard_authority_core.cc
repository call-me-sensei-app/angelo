/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The authority's tickets and ledger (`bpy_angelo_guard_authority_core.hh`).
 */

#include <utility>
#include <algorithm>
#include <functional>
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

bool Authority::owns(const Window &window, const std::string &surface, const uint64_t target)
{
  const auto found = window.owners.find(surface);
  return (found == window.owners.end() ? 0 : found->second) == target;
}

bool Authority::pointer_on(const Window &window, const std::string &surface, const RegionSpec &spec)
{
  return window.pointer_known && inside(spec, window.px, window.py) && owns(window, surface, window.target);
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
        /* Its shield starts again; a shielded control's aim holds while the pointer is still on it
         * (2.0.1). Any other needs its move again, as in 2.0.0. */
        const bool on_it = pointer_on(window, surface.first, region.spec);
        region.epoch = window.epoch;
        region.since = now;
        region.entered = region.entered && on_it && region.spec.shield;
        region.moved_in = false;
        drop_tickets(region.spec);
      }
    }
  }
}

bool Authority::set_regions(const WindowKey window_key,
                            const std::string &surface,
                            const std::vector<RegionSpec> &regions,
                            const double now,
                            const uint64_t owner)
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
  /* Where it drew them before: a surface drawn in another region now is laid out anew (below). */
  const bool owner_kept = owns(window, surface, owner);
  window.owners[surface] = owner;
  std::vector<Region> next;
  next.reserve(regions.size());
  for (const RegionSpec &spec : regions) {
    Region region{spec, now, window.frame, window.epoch, false};
    /* Drawn under that pointer: as warped into. */
    region.warped = warped_here && inside(spec, window.px, window.py);
    if (existing != window.surfaces.end() && owner_kept) {
      /* The same control drawn where it was, when one is: duplicates of a control prefer it. */
      const Region *same = nullptr;
      for (const Region &before : existing->second) {
        const RegionSpec &old = before.spec;
        if (old.idname == spec.idname && old.digest == spec.digest && old.shield == spec.shield &&
            old.x == spec.x && old.y == spec.y && old.width == spec.width && old.height == spec.height)
        {
          same = &before;
          break;
        }
      }
      if (same != nullptr) {
        region = *same; /* Drawn where it was: its shield and its aim stand. */
        region.spec = spec;
        next.push_back(std::move(region));
        continue;
      }
      for (const Region &before : existing->second) {
        const RegionSpec &old = before.spec;
        if (old.idname != spec.idname || old.digest != spec.digest || old.shield != spec.shield) {
          continue;
        }
        if (!spec.shield) {
          continue; /* Any other control re-laid out starts again, as in 2.0.0 (its ARM). */
        }
        /* Laid out again elsewhere (rows streaming in around its card, a scroll that keeps it in
         * view): it keeps its shield, and its aim only while the pointer was on it before and is on it
         * now (2.0.1: a card that moved on every streamed row dropped every aim). One that arrives
         * under a pointer that was not on it is a new control (the break-it review's M1). */
        const bool before_on = pointer_on(window, surface, old);
        const bool after_on = pointer_on(window, surface, spec);
        if (after_on && !before_on) {
          break; /* `region` as made above: shown now, unaimed. */
        }
        region = before;
        region.spec = spec;
        region.entered = before.entered && before_on && after_on;
        region.moved_in = before.moved_in && before_on && after_on;
        region.warped = (before.warped || warped_here) && after_on;
        break;
      }
    }
    next.push_back(std::move(region));
  }
  window.surfaces[surface] = std::move(next);
  return true;
}

void Authority::note_move(
    const WindowKey window_key, const int x, const int y, const uint64_t target, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  sync(window, now);
  const uint64_t target_before = window.target;
  window.target = target; /* Where Blender routes the pointer now, moved or not (a sidebar opened under it). */
  if (!window.button_down) { /* A drag's hold ends once its button is up, release seen or not. */
    std::erase_if(tickets_, [](const Ticket &ticket) { return ticket.gesture; });
  }
  if (!window.pointer_known || window.activation_move) {
    /* Where it came from is unknown, or the activation's own move to where the pointer is now (it
     * may have moved while the window was in the background): never a move into anything. */
    window.pointer_known = true;
    window.activation_move = false;
    window.px = x;
    window.py = y;
    return;
  }
  if (x == window.px && y == window.py) {
    return; /* A refresh, not a move. */
  }
  if (window.active) {
    window.returning = false; /* An event after the activation, before any press: it had no press. */
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
    /* Blender gives the pointer to another region (a sidebar, header, HUD or asset shelf drawn over the
     * surface's, or a popup), or it is over a popup, menu or tooltip (or another surface's own menu):
     * the pointer is over that, not the consent regions beneath. */
    const bool hidden = !owns(window, surface.first, target) ||
                        (!window.covers.empty() && covered(window, x, y, surface.first));
    /* Where it was: on this surface's region only where Blender gave it this surface's region. */
    const bool was_here = owns(window, surface.first, target_before);
    for (Region &region : surface.second) {
      if (hidden || !inside(region.spec, x, y)) {
        region.warped = false; /* Left it: the person's next move in counts again. */
        if (region.spec.shield) {
          region.entered = false; /* Leaving a shielded control drops its aim (2.0.1, review M1). */
        }
        continue;
      }
      if (region.warped) {
        continue;
      }
      const double shown = region_shown(window, region);
      if (now - shown >= ARM_SECONDS) {
        region.moved_in = true; /* A real move inside it once it has been there ARM_SECONDS. */
      }
      /* A move into it from outside aims at it whenever it happens (2.0.1: before, only after the
       * shield, so a person who moved onto Approve at once and then clicked was refused); a shielded
       * control's press still waits for SHIELD_SECONDS from when it was first shown (`note_press`). */
      if (!was_here || !inside(region.spec, window.px, window.py)) {
        region.entered = true;
        region.press_aim = -1.0; /* A move in is the person's aim itself. */
      }
      else if (region.spec.shield && now - shown >= SHIELD_SECONDS) {
        /* A real move inside it once its shield has passed, a hand's jitter included, is the
         * person's aim too (the re-review of acd0d465ee9, SHOULD-3): a click refused there recovers
         * without leaving the control. A pointer that never moved still never aims (S1). */
        region.entered = true;
        region.press_aim = -1.0;
      }
    }
  }
  window.px = x;
  window.py = y;
}

void Authority::note_activate(const WindowKey window_key, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  window.epoch++; /* Its controls start their shield again from when the person sees them. */
  window.changed = now;
  window.activated = now;
  window.activation_move = true;
  window.active = true;
}

double Authority::epoch_changed(const WindowKey window_key) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  return found == windows_.end() ? -1.0e9 : found->second.changed;
}

double Authority::restart_from(const WindowKey window_key, const double now) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  if (found == windows_.end() || !found->second.active) {
    return now; /* No record, or in the back: nothing in it was seen since any change. */
  }
  return std::min(found->second.changed, now);
}

bool Authority::press_activating(const WindowKey window_key) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  return found != windows_.end() && found->second.press_activating;
}

bool Authority::returning(const WindowKey window_key) const
{
  std::lock_guard lock(mutex_);
  const auto found = windows_.find(window_key);
  return found != windows_.end() && found->second.returning;
}

void Authority::note_deactivate(const WindowKey window_key, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  window.epoch++;
  window.changed = now;
  window.returning = true;
  window.active = false; /* Moves and keys in the background (Windows delivers moves) keep `returning`. */
  for (auto &surface : window.surfaces) { /* The person left: every aim goes (2.0.0's rule). */
    for (Region &region : surface.second) {
      region.entered = false;
      region.moved_in = false;
      region.press_aim = -1.0;
    }
  }
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
  if (window.active) {
    window.returning = false; /* A key after the activation, before any press: it had no press. */
  }
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
      const bool here = owns(window, surface, window.press_target);
      for (const Region &region : found->second) {
        persons = persons || (here && region.spec.idname == idname && region.spec.digest == digest &&
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

bool Authority::note_press(
    const WindowKey window_key, const int x, const int y, const uint64_t target, const double now)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  window.pressed = now; /* Where the person pressed: a focus report on that control is theirs. */
  window.button_down = true;
  window.press_x = x;
  window.press_y = y;
  window.press_target = target;
  /* The click that brings the window forward, wherever it lands, never grants; it aims at the control
   * under it, so the person's next press there does once its shield has passed. */
  /* Only the press that arrives with the activation brought the window forward (the re-review of
   * acd0d465ee9, SHOULD-1): after Cmd-Tab, a Dock click or `open -a`, the next press is ordinary. */
  const bool activating = window.returning &&
                          (!window.active || now - window.activated <= ACTIVATION_PRESS_SECONDS);
  window.returning = false;
  window.press_activating = activating;
  if (activating) {
    window.epoch++;
    window.changed = now; /* Every epoch change is stamped (the re-review of cf980b8, SHOULD A). */
  }
  sync(window, now);
  Region *hit = nullptr;
  for (auto &surface : window.surfaces) {
    if (!owns(window, surface.first, target)) {
      continue; /* Blender gives the press to another region (a sidebar over it, a popup): never this surface's. */
    }
    if (!window.covers.empty() && covered(window, x, y, surface.first)) {
      continue; /* On a popup, menu or tooltip drawn over it: never this surface's press. */
    }
    for (Region &region : surface.second) {
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
  const double shown = region_shown(window, *hit);
  /* A shielded control (an approval's) grants once it has been shown SHIELD_SECONDS and the person
   * aimed at it: moved into it, or brought the window forward with a press on it (below). Any other
   * needs a real move inside it after ARM_SECONDS, as in 2.0.0. */
  /* That press's aim lets a press grant only SHIELD_SECONDS (the longest double-click) after it: a
   * double-click never grants (the break-it review's M1). */
  const bool armed = hit->spec.shield ?
                         (hit->entered && now - shown >= SHIELD_SECONDS &&
                          (hit->press_aim < 0.0 || now - hit->press_aim >= SHIELD_SECONDS)) :
                         hit->moved_in;
  if (activating || hit->warped || !armed) {
    /* A shielded control's press that did not grant aims at it only when it was the click that
     * brought the window forward: the person's next press there grants. Any other press never aims,
     * so presses alone never grant a control that appeared under a resting pointer (the break-it
     * review of ca6f5ff137d, S1); the person moves onto it. Never a pointer code put there. */
    if (hit->spec.shield && !hit->warped && activating) {
      hit->entered = true;
      hit->press_aim = now;
    }
    if (activating) {
      return false;
    }
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
  define(APPROVED_CALLS, Value());
  define(APPROVAL_MODES, Value(std::string("ASK"))); /* Ask narrows; Auto is set only in a scope. */
  define(REVIEWED_SCRIPTS, Value(false));
  define(CONTEXT_FOLDERS, Value());
  define(RULE_DECISIONS, Value(false));
  define(GLOBAL_RULES, Value());
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

bool Authority::covered(const Window &window, const int x, const int y, const std::string &surface)
{
  for (const auto &entry : window.covers) {
    const Cover &cover = entry.second;
    if (cover.restart_only) {
      continue; /* An overlapping region: Blender's routing decides where its input goes (`owns`). */
    }
    if (cover.surface == surface && !surface.empty()) {
      continue; /* A surface's own menu is drawn over its own controls by that surface itself. */
    }
    if (x >= cover.x && x < cover.x + cover.width && y >= cover.y && y < cover.y + cover.height) {
      return true;
    }
  }
  return false;
}

void Authority::drop_tickets(const RegionSpec &region)
{
  std::erase_if(tickets_, [&](const Ticket &ticket) {
    return !ticket.dialog && ticket.idname == region.idname && ticket.digest == region.digest;
  });
}

void Authority::restart_covered(Window &window, const Cover &cover, const double now)
{
  for (auto &surface : window.surfaces) {
    if (!cover.surface.empty() && surface.first == cover.surface) {
      continue; /* Its own surface's regions were never covered by it. */
    }
    for (Region &region : surface.second) {
      const RegionSpec &spec = region.spec;
      if (spec.x < cover.x + cover.width && cover.x < spec.x + spec.width && spec.y < cover.y + cover.height &&
          cover.y < spec.y + spec.height)
      {
        /* On screen again from now and the window's next frame, which its close draws: not aimed at,
         * not moved into, shielded again (as `sync` restarts a region, and the button fix counts). */
        region.since = std::max(region.since, now);
        region.frame = window.frame;
        region.entered = false;
        region.moved_in = false;
        region.press_aim = -1.0;
        drop_tickets(spec); /* A press Blender gave a modal handler (a menu's outside click) minted for nothing seen. */
      }
    }
  }
}

void Authority::set_cover(const WindowKey window_key,
                          const uint64_t key,
                          const int x,
                          const int y,
                          const int width,
                          const int height,
                          const double now,
                          const bool restart_only)
{
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  const Cover cover{x, y, width, height, {}, restart_only};
  const auto found = window.covers.find(key);
  if (found != window.covers.end()) {
    const Cover &old = found->second;
    if (old.x == x && old.y == y && old.width == width && old.height == height &&
        old.restart_only == restart_only)
    {
      return; /* Drawn where it was (every redraw). */
    }
    restart_covered(window, old, now); /* Moved or resized: what it covered there is seen again. */
  }
  window.covers[key] = cover;
}

std::vector<Authority::Rect> Authority::sync_covers(const WindowKey window_key,
                                                    const std::vector<DrawnCover> &drawn,
                                                    const double now)
{
  return sync_covers(window_key, drawn, nullptr, now);
}

std::vector<Authority::Rect> Authority::sync_covers(const WindowKey window_key,
                                                    const std::vector<DrawnCover> &drawn,
                                                    const std::vector<uint64_t> &visible,
                                                    const double now)
{
  return sync_covers(window_key, drawn, &visible, now);
}

std::vector<Authority::Rect> Authority::sync_covers(const WindowKey window_key,
                                                    const std::vector<DrawnCover> &drawn,
                                                    const std::vector<uint64_t> *visible,
                                                    const double now)
{
  std::vector<uint64_t> gone;
  std::vector<Rect> uncovered; /* Where a restart-only cover was, gone or moved. */
  const auto shown = [&](const uint64_t region) {
    return visible == nullptr || std::find(visible->begin(), visible->end(), region) != visible->end();
  };
  {
    std::lock_guard lock(mutex_);
    const auto window = windows_.find(window_key);
    if (window != windows_.end()) {
      Window &w = window->second;
      /* Consent regions of a surface whose region is gone: forgotten (its pointer may be reused). */
      for (auto surface = w.surfaces.begin(); surface != w.surfaces.end();) {
        const auto owner = w.owners.find(surface->first);
        if (owner != w.owners.end() && owner->second != 0 && !shown(owner->second)) {
          w.owners.erase(owner);
          surface = w.surfaces.erase(surface);
        }
        else {
          ++surface;
        }
      }
      for (const auto &entry : w.covers) {
        if (!entry.second.surface.empty()) {
          /* A canvas surface's own menu: its surface sets and clears it (`surface_cover`), unless its region is
           * gone and no close was reported. */
          if (!shown(entry.second.owner)) {
            gone.push_back(entry.first);
          }
          continue;
        }
        const Cover &old = entry.second;
        const auto now_drawn = std::find_if(drawn.begin(), drawn.end(),
                                            [&](const DrawnCover &cover) { return cover.key == entry.first; });
        if (now_drawn == drawn.end()) {
          gone.push_back(entry.first);
        }
        if (old.restart_only &&
            (now_drawn == drawn.end() || now_drawn->x != old.x || now_drawn->y != old.y ||
             now_drawn->width != old.width || now_drawn->height != old.height))
        {
          uncovered.push_back({entry.first, old.x, old.y, old.width, old.height});
        }
      }
    }
  }
  for (const uint64_t key : gone) {
    clear_cover(window_key, key, now);
  }
  for (const DrawnCover &cover : drawn) {
    set_cover(window_key, cover.key, cover.x, cover.y, cover.width, cover.height, now, cover.restart_only);
  }
  return uncovered;
}

void Authority::surface_cover(const WindowKey window_key,
                              const std::string &surface,
                              const int x,
                              const int y,
                              const int width,
                              const int height,
                              const double now,
                              const uint64_t owner)
{
  /* Keyed apart from any region pointer (the top bit), one per surface. */
  const uint64_t key = uint64_t(std::hash<std::string>{}(surface)) | (uint64_t(1) << 63);
  if (width <= 0 || height <= 0) {
    clear_cover(window_key, key, now);
    return;
  }
  std::lock_guard lock(mutex_);
  Window &window = windows_[window_key];
  const auto found = window.covers.find(key);
  if (found != window.covers.end()) {
    const Cover &old = found->second;
    if (old.x == x && old.y == y && old.width == width && old.height == height && old.owner == owner) {
      return;
    }
    restart_covered(window, old, now);
  }
  window.covers[key] = Cover{x, y, width, height, surface, false, owner};
}

void Authority::clear_cover(const WindowKey window_key, const uint64_t key, const double now)
{
  std::lock_guard lock(mutex_);
  const auto window = windows_.find(window_key);
  if (window == windows_.end()) {
    return;
  }
  const auto found = window->second.covers.find(key);
  if (found == window->second.covers.end()) {
    return;
  }
  const Cover cover = found->second;
  window->second.covers.erase(found);
  restart_covered(window->second, cover, now);
}

Authority::Readout Authority::readout(const WindowKey window_key) const
{
  std::lock_guard lock(mutex_);
  Readout readout;
  const auto found = windows_.find(window_key);
  if (found == windows_.end()) {
    return readout;
  }
  const Window &window = found->second;
  readout.known = true;
  readout.epoch = window.epoch;
  readout.frame = window.frame;
  readout.presented = window.presented;
  if (window.presented > 0 && window.frame > 0) {
    readout.last_present = window.presents[(window.frame - 1) % PRESENTS_KEPT];
  }
  readout.activated = window.activated;
  readout.changed = window.changed;
  readout.real_move_at = window.real_move_at;
  readout.real_x = window.real_x;
  readout.real_y = window.real_y;
  readout.px = window.px;
  readout.py = window.py;
  readout.active = window.active;
  readout.returning = window.returning;
  readout.activation_move = window.activation_move;
  readout.pointer_known = window.pointer_known;
  return readout;
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

bool Scope::harness()
{
  return scope_depth[HARNESS] > 0;
}

static thread_local std::string scope_control; /* Scope::control(): the innermost person grant's control. */

const std::string &Scope::control()
{
  return scope_control;
}

Scope::Control::Control(std::string value) : previous_(std::move(scope_control))
{
  scope_control = std::move(value);
}

Scope::Control::~Control()
{
  scope_control = std::move(previous_);
}

bool Scope::admits_operator(const Authority &authority, const std::string &idname)
{
  if (scope_depth[PERSON] > 0 || scope_depth[HARNESS] > 0) {
    return true;
  }
  return scope_depth[AUTOMATIC] > 0 && authority.automatic_allows(idname);
}

}  // namespace blender::angelo_guard
