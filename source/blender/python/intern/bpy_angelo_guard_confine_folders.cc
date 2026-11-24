/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The confinement's folder sets and their judgement (bpy_angelo_guard_confine_paths.hh): private, startup,
 * tool-output, open and trusted roots, families and private names below an open folder. A path is resolved first
 * (bpy_angelo_guard_confine_paths.cc); the deepest root that holds it decides. No Python and no GIL.
 */

#include <cctype>
#include <algorithm>
#include <mutex>
#include <optional>
#include <shared_mutex>
#include <string>
#include <vector>

#include "bpy_angelo_guard_confine_changes.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard {

#ifdef _WIN32
static const char SEP = '\\';
#else
static const char SEP = '/';
#endif

/* One lock for every root set: adds are rare (start-up, a new connection or worker folder), checks are many. */
static std::shared_mutex &lock()
{
  static std::shared_mutex mutex;
  return mutex;
}

static bool inside(const std::string &path_key, const std::string &root)
{
  if (path_key == root) {
    return true;
  }
  const std::string prefix = (!root.empty() && (root.back() == SEP)) ? root : root + SEP;
  return path_key.compare(0, prefix.size(), prefix) == 0;
}

static std::string trimmed_key(const std::string &resolved)
{
  std::string result = key(resolved);
  while (result.size() > 1 && result.back() == SEP && !(result.size() == 3 && result[1] == ':')) {
    result.pop_back();
  }
  return result;
}

void Roots::add(const std::string &folder, const bool refusing)
{
  note_connected_root(folder); /* Not chosen by a script: noted only before the first region (remote.hh). */
  const RootLinksNoted noting; /* A link of the person's on the way to a share they use: noted too, before a region. */
  const std::optional<std::string> resolved = resolve(folder);
  if (!resolved) {
    if (const std::optional<std::string> spelled = refusing ? unconnected_remote_spelling(folder) : std::nullopt) {
      const std::string root = trimmed_key(*spelled); /* Held by its text: a key resolved onto it later matches. */
      std::unique_lock guard(lock());
      if (std::find(keys_.begin(), keys_.end(), root) == keys_.end()) {
        keys_.push_back(root);
      }
    }
    return;
  }
  const std::string root = trimmed_key(*resolved);
  const std::optional<Identity> id = folder_identity(*resolved);
  std::unique_lock guard(lock());
  if (std::find(keys_.begin(), keys_.end(), root) == keys_.end()) {
    keys_.push_back(root);
  }
  if (id && std::find(ids_.begin(), ids_.end(), *id) == ids_.end()) {
    ids_.push_back(*id);
  }
}

bool Roots::holds(const std::string &resolved, const std::vector<Identity> &chain) const
{
  const std::string path_key = key(resolved);
  std::shared_lock guard(lock());
  for (const std::string &root : keys_) {
    if (inside(path_key, root)) {
      return true;
    }
  }
  for (const Identity &id : chain) {
    if (std::find(ids_.begin(), ids_.end(), id) != ids_.end()) {
      return true;
    }
  }
  return false;
}

bool Roots::above(const std::string &resolved) const
{
  const std::string path_key = trimmed_key(resolved);
  std::shared_lock guard(lock());
  for (const std::string &root : keys_) {
    if (inside(root, path_key)) {
      return true;
    }
  }
  return false;
}

std::optional<std::string> Roots::deepest(const std::string &resolved) const
{
  const std::string path_key = key(resolved);
  std::optional<std::string> best;
  std::shared_lock guard(lock());
  for (const std::string &root : keys_) {
    if (inside(path_key, root) && (!best || root.size() > best->size())) {
      best = root;
    }
  }
  return best;
}

bool Roots::inside_of(const std::string &resolved,
                      const std::string &outer,
                      const std::vector<Identity> &chain,
                      const std::vector<Identity> &outer_chain) const
{
  const std::string path_key = key(resolved);
  std::shared_lock guard(lock());
  for (const std::string &root : keys_) {
    if (inside(root, outer) && inside(path_key, root)) {
      return true;
    }
  }
  for (const Identity &id : chain) {
    if (std::find(outer_chain.begin(), outer_chain.end(), id) == outer_chain.end() &&
        std::find(ids_.begin(), ids_.end(), id) != ids_.end())
    {
      return true;
    }
  }
  return false;
}

void Names::add(const std::string &name)
{
  const std::string folded = key(name);
  std::unique_lock guard(lock());
  if (!folded.empty() && std::find(names_.begin(), names_.end(), folded) == names_.end()) {
    names_.push_back(folded);
  }
}

bool Names::below(const std::string &resolved, const std::string &outer) const
{
  const std::string path_key = key(resolved);
  if (!inside(path_key, outer) || path_key.size() <= outer.size()) {
    return false;
  }
  std::shared_lock guard(lock());
  size_t start = outer.size();
  while (start < path_key.size()) {
    while (start < path_key.size() && path_key[start] == SEP) {
      start++;
    }
    size_t end = path_key.find(SEP, start);
    if (end == std::string::npos) {
      end = path_key.size();
    }
    if (end > start && std::find(names_.begin(), names_.end(), path_key.substr(start, end - start)) != names_.end())
    {
      return true;
    }
    start = end;
  }
  return false;
}

size_t Roots::size() const
{
  std::shared_lock guard(lock());
  return keys_.size();
}

void Families::add(const std::string &parent, const std::string &prefix)
{
  note_connected_root(parent); /* As in `Roots::add`. */
  const RootLinksNoted noting;
  /* A family is private: on a share nobody connected, its parent is held by its text (`Roots::add`). */
  std::optional<std::string> resolved = resolve(parent);
  if (!resolved) {
    resolved = unconnected_remote_spelling(parent);
  }
  if (!resolved || prefix.empty()) {
    return;
  }
  const std::pair<std::string, std::string> pair(trimmed_key(*resolved), key(prefix));
  std::unique_lock guard(lock());
  if (std::find(pairs_.begin(), pairs_.end(), pair) == pairs_.end()) {
    pairs_.push_back(pair);
  }
}

bool Families::holds(const std::string &resolved) const
{
  const std::string path_key = key(resolved);
  std::shared_lock guard(lock());
  for (const auto &[parent, prefix] : pairs_) {
    const std::string start = (!parent.empty() && parent.back() == SEP) ? parent : parent + SEP;
    if (path_key.compare(0, start.size(), start) == 0 &&
        path_key.compare(start.size(), prefix.size(), prefix) == 0)
    {
      return true;
    }
  }
  return false;
}

/** The identities of the folders strictly above `folder` (its own left out). */
static std::vector<Identity> folders_above(const std::string &folder)
{
  std::vector<Identity> chain = ancestors(folder);
  const std::optional<Identity> own = folder_identity(folder);
  if (own && !chain.empty() && chain.front() == *own) {
    chain.erase(chain.begin());
  }
  return chain;
}

bool Folders::compactable(const std::string &folder_key) const
{
  /* A folder recorded whole must hold none of Angelo's roots (a home folder holds the state folder, the chat's
   * workspace is a root itself): everything below it would count as a script's. */
  for (const Roots *roots : {&private_roots, &startup_roots, &tool_roots, &open_roots, &trusted_roots}) {
    if (roots->above(folder_key)) {
      return false;
    }
  }
  return true;
}

bool Folders::script_wrote(const std::string &path) const
{
  const std::optional<std::string> resolved = resolve(path);
  if (!resolved) {
    return true; /* Fail closed: what can't be resolved is not the person's. */
  }
  return changes_wrote(trimmed_key(*resolved)) != Made::No;
}

bool Folders::agent_made(const std::string &path) const
{
  const std::optional<std::string> resolved = resolve(path);
  if (!resolved) {
    return false;
  }
  if (tool_roots.holds(*resolved, ancestors(*resolved))) {
    return true;
  }
  /* By path everywhere; by identity and content outside installed folders, which scripts can't write: a file
   * there arrived by an install, and installed code is installed whatever its bytes. */
  const auto outside_installed = [&]() { return !trusted_roots.holds(*resolved, {}); };
  switch (changes_made(trimmed_key(*resolved), outside_installed)) {
    case Made::Yes:
      return true;
    case Made::Unknown:
      return outside_installed(); /* Fail closed: only installed code loads. */
    case Made::No:
      break;
  }
  return false;
}

int Folders::forget(const std::string &folder) const
{
  const std::optional<std::string> resolved = resolve(folder);
  if (!resolved || !folder_identity(*resolved)) {
    return -2;
  }
  return changes_forget(trimmed_key(*resolved));
}

thread_local bool judged_unrecorded = false;
thread_local bool resolve_reached_network = false; /* paths.hh */
thread_local bool judged_unreadable = false;       /* paths.hh */

Refusal Folders::judge(const std::string &path, bool write, int dir_fd, bool link_target, Change change) const
{
  judged_unrecorded = false;
  judged_unreadable = false;
  network_refusal_begin(path);
  if (names_a_remote_file(path)) {
    return Refusal::Network; /* By its text, before anything resolves (and so connects to) it. */
  }
  if (write && (change == Change::Moved || change == Change::Linked || change == Change::Metadata)) {
    links_changed(); /* What may move, appear or go: no thread trusts a folder it found plain before (M5). */
  }
#ifdef _WIN32
  /* A link on the way (a symbolic link, junction or mount point, the 8.3 name of one, a chain) whose target is a
   * share nobody connected: resolving the path would follow it, and connect. Walked without following anything
   * (bpy_angelo_guard_confine_links.hh); what can't be told is refused. With Strict script protection only
   * (`strict_lookups`): its cost is measurable against Angelo's own speed (Ruling 17). */
  if (std::wstring wide; strict_lookups() && utf8_to_wide(path, wide)) {
    switch (links_walk(wide, true)) {
      case links::Verdict::Network:
        return Refusal::Network;
      case links::Verdict::Unreadable:
        judged_unreadable = true;
        return Refusal::Private; /* Fail closed: where it leads can't be told. */
      case links::Verdict::Safe:
        break;
    }
  }
#endif
  const std::optional<std::string> resolved = resolve(path, dir_fd);
  if (!resolved) {
    /* Fail closed: what cannot be resolved is not opened while confined (through a link into /net: the network). */
    return resolve_reached_network ? Refusal::Network : Refusal::Private;
  }
  const std::vector<Identity> chain = ancestors(*resolved);
  if (const std::optional<std::string> open = open_roots.deepest(*resolved)) {
    /* Inside an open folder (it lies in a private one): private only below it. */
    if (families.holds(*resolved) || open_private_names.below(*resolved, *open) ||
        private_roots.inside_of(*resolved, *open, chain, folders_above(*open)))
    {
      return Refusal::Private;
    }
  }
  else if (private_roots.holds(*resolved, chain) || families.holds(*resolved)) {
    return Refusal::Private;
  }
  if (link_target &&
      (private_roots.above(*resolved) || startup_roots.above(*resolved) || tool_roots.above(*resolved)))
  {
    return Refusal::Private;
  }
  if ((write || link_target) && startup_roots.holds(*resolved, chain)) {
    return Refusal::Startup;
  }
  if ((write || link_target) && tool_roots.holds(*resolved, chain)) {
    return Refusal::ToolOutput;
  }
  if (write && !changes_note(trimmed_key(*resolved), change, [this](const std::string &folder) {
        return compactable(folder);
      }))
  { /* Code read from here later is the agent's (agent_made). */
    judged_unrecorded = true; /* `refuse` says why (bpy_angelo_guard_confine.cc). */
    return Refusal::Private;
  }
  return Refusal::None;
}

bool Folders::trusted(const std::string &path) const
{
  if (path.rfind("<frozen ", 0) == 0) {
    return true;
  }
  if (path.empty() || path[0] == '<') {
    return false;
  }
  const std::optional<std::string> resolved = resolve(path);
  return resolved && trusted_roots.holds(*resolved, {});
}

Folders &folders()
{
  static Folders instance;
  return instance;
}

}  // namespace blender::angelo_guard
