/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The record of paths Angelo's own code may write because the person (or a program they connected) named them
 * (doc/angelo/script_confinement.rst, "Angelo's own I/O"). Nothing here outlives the process, and nothing is wider
 * than what was named:
 *
 * - SESSION: a file Angelo's code vouched for where it entered (a settings file Connect found, an engine asset the
 *   editor named, a worker's part). A file, never a folder.
 * - CALL: one call of Angelo's (`vouched_during`): a file, or a folder of Blender's own the call writes into (a
 *   settings import), whole; forgotten when the call ends.
 * - PICK: the person's confirm in a file browser an Angelo operator opened: what was chosen, for that operator's own
 *   run only (the window manager ends it when the operator's execute returns).
 * - TYPED: a path the person typed into a file or folder property (a render's output): that exact file, or files
 *   directly in that folder, for the writer that reads that very property only (`matches_typed`, by the property's
 *   tag); forgotten as soon as anything else sets the property.
 * - ROOT: a folder of Blender's own or the environment's named before any script region began (the cache's updates
 *   folder, a library folder the environment names): no script could have chosen it.
 *
 * An existing file's identity (device and file number) is kept: a file at a named name that has other names too (a
 * hard link a script made to a file of the person's) is written only when it is that very file (writable.cc).
 */

#include <cstring>
#include <mutex>
#include <regex>
#include <string>
#include <vector>

#ifdef _WIN32
#  include <windows.h>
#else
#  include <sys/stat.h>
#endif

#include "BLI_file_time.hh"

#include "bpy_angelo_guard_confine_vouches.hh"
#include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard::vouches {

namespace {

#ifdef _WIN32
constexpr char SEP = '\\';
#else
constexpr char SEP = '/';
#endif

constexpr size_t KEPT = 4096;         /* Entries kept in one session (the oldest go first, never a program). */
constexpr size_t PROGRAMS_KEPT = 256; /* Programs recorded to run (Scope::Run): past it, a new one is refused. */

struct Entry {
  std::string key;
  bool folder = false;
  bool recursive = false;
  Scope scope = Scope::Session;
  std::string tag; /* TYPED: the property it came from. */
  Facts facts;     /* An existing file's identity when it was named. */
};

std::mutex lock;
std::vector<Entry> entries;

std::string parent_of(const std::string &key)
{
  const size_t cut = key.find_last_of(SEP);
  if (cut == std::string::npos || cut == 0) {
    return std::string(1, SEP);
  }
  return key.substr(0, cut);
}

/** The oldest entries past KEPT go, never a program's (Scope::Run): its captured facts are what refuses a program
 * changed since, and one dropped would be recorded afresh when Angelo finds it again (the re-review of 4be92cfb84f,
 * S-1). Programs are bounded on their own (PROGRAMS_KEPT). */
void trim()
{
  for (auto it = entries.begin(); entries.size() > KEPT && it != entries.end();) {
    it = it->scope == Scope::Run ? it + 1 : entries.erase(it);
  }
}

bool inside(const std::string &key, const std::string &folder)
{
  return key.size() > folder.size() && key.compare(0, folder.size(), folder) == 0 &&
         (key[folder.size()] == SEP || folder.back() == SEP);
}

bool same_file(const Facts &a, const Facts &b)
{
  return a.exists && b.exists && a.device == b.device && a.number[0] == b.number[0] && a.number[1] == b.number[1];
}

/** The very file, unchanged: an in-place rewrite (O_TRUNC keeps the inode) changes its change time. */
bool same_content(const Facts &a, const Facts &b)
{
  return same_file(a, b) && !a.directory && a.size == b.size && a.modified == b.modified && a.changed == b.changed;
}

/** One part of a typed output pattern against one part of a path a writer expanded it to: `#` runs are frame digits,
 * `{name}` one name (template string values never hold a separator), and the last part may gain only what Blender
 * appends (frame digits, an extension). */
bool part_matches(const std::string &pattern, const std::string &part, const bool last)
{
  std::string expression = "^";
  for (size_t i = 0; i < pattern.size(); i++) {
    const char c = pattern[i];
    if ((c == '{' || c == '}') && i + 1 < pattern.size() && pattern[i + 1] == c) {
      expression += std::string("\\") + c; /* `{{`, `}}`: a literal brace. */
      i++;
      continue;
    }
    if (c == '#') {
      while (i + 1 < pattern.size() && pattern[i + 1] == '#') {
        i++;
      }
      expression += "[0-9]+";
      continue;
    }
    if (c == '{') {
      const size_t end = pattern.find('}', i);
      if (end != std::string::npos) {
        expression += "[^/\\\\]*";
        i = end;
        continue;
      }
    }
    if (std::strchr(".^$|()[]*+?\\{}", c) != nullptr) {
      expression += '\\';
    }
    expression += c;
  }
  if (last) {
    expression += "[0-9]*(\\.[A-Za-z0-9]{1,8})?";
  }
  expression += "$";
  try {
    return std::regex_match(part, std::regex(expression));
  }
  catch (const std::regex_error &) {
    return false;
  }
}

/** Whether `key` is what the typed output pattern `pattern` (a resolved key) expands to: the same folders part by
 * part (a part holding `#` or `{name}` by its pattern), and a last part as above. */
bool pattern_matches(const std::string &pattern, const std::string &key)
{
  const auto split = [](const std::string &text) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (start <= text.size()) {
      size_t end = text.find(SEP, start);
      end = end == std::string::npos ? text.size() : end;
      parts.push_back(text.substr(start, end - start));
      start = end + 1;
    }
    return parts;
  };
  const std::vector<std::string> wanted = split(pattern), have = split(key);
  if (wanted.size() != have.size()) {
    return false;
  }
  for (size_t i = 0; i < wanted.size(); i++) {
    const bool last = i + 1 == wanted.size();
    if (wanted[i] != have[i] && !part_matches(wanted[i], have[i], last)) {
      return false;
    }
  }
  return true;
}

bool entry_matches(const Entry &entry, const std::string &key, const Facts & /*now*/)
{
  if (!entry.folder) {
    /* By its resolved name. Its identity decides only whether a file with other names (hard links) may be written
     * (`identity_named`, writable.cc): a single-linked file at the name names nothing else, and Angelo's own
     * rewrites (a temporary renamed onto it, an editor's save) give it a new identity legitimately. */
    return key == entry.key;
  }
  if (key == entry.key) {
    return true;
  }
  return entry.recursive ? inside(key, entry.key) : parent_of(key) == entry.key;
}

}  // namespace

Facts facts_of(const std::string &path)
{
  Facts facts;
#ifdef _WIN32
  std::wstring wide;
  if (!utf8_to_wide(path, wide)) {
    return facts;
  }
  HANDLE handle = HANDLE(open_for_lookup(wide, false));
  if (handle == INVALID_HANDLE_VALUE) {
    return facts;
  }
  BY_HANDLE_FILE_INFORMATION info;
  if (GetFileInformationByHandle(handle, &info)) {
    facts.exists = true;
    facts.directory = (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    facts.link = (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
    facts.links = info.nNumberOfLinks;
    facts.device = info.dwVolumeSerialNumber;
    facts.number[0] = (uint64_t(info.nFileIndexHigh) << 32) | info.nFileIndexLow;
    facts.size = (uint64_t(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
    const uint64_t written = (uint64_t(info.ftLastWriteTime.dwHighDateTime) << 32) | info.ftLastWriteTime.dwLowDateTime;
    facts.modified = blender::file_time::nanoseconds_from_filetime(written);
    FILE_BASIC_INFO basic;
    if (GetFileInformationByHandleEx(handle, FileBasicInfo, &basic, sizeof(basic))) {
      facts.changed = blender::file_time::nanoseconds_from_filetime(uint64_t(basic.ChangeTime.QuadPart));
    }
  }
  CloseHandle(handle);
#else
  struct stat info;
  if (lstat(path.c_str(), &info) == 0) {
    facts.exists = true;
    facts.directory = S_ISDIR(info.st_mode);
    facts.link = S_ISLNK(info.st_mode);
    facts.links = uint64_t(info.st_nlink);
    facts.device = uint64_t(info.st_dev);
    facts.number[0] = uint64_t(info.st_ino);
    facts.size = uint64_t(info.st_size);
#  ifdef __APPLE__
    facts.modified = blender::file_time::nanoseconds_saturated(info.st_mtimespec.tv_sec, info.st_mtimespec.tv_nsec);
    facts.changed = blender::file_time::nanoseconds_saturated(info.st_ctimespec.tv_sec, info.st_ctimespec.tv_nsec);
#  else
    facts.modified = blender::file_time::nanoseconds_saturated(info.st_mtim.tv_sec, info.st_mtim.tv_nsec);
    facts.changed = blender::file_time::nanoseconds_saturated(info.st_ctim.tv_sec, info.st_ctim.tv_nsec);
#  endif
  }
#endif
  return facts;
}

bool add(const std::string &key, const Facts &facts, bool folder, bool recursive, Scope scope, const std::string &tag)
{
  if (key.empty() || facts.link) {
    return false;
  }
  std::lock_guard<std::mutex> guard(lock);
  if (scope == Scope::Typed) { /* A property holds one value: what was typed before is no longer the target. */
    for (auto it = entries.begin(); it != entries.end();) {
      it = (it->scope == Scope::Typed && it->tag == tag) ? entries.erase(it) : it + 1;
    }
  }
  for (auto it = entries.begin(); it != entries.end(); ++it) {
    if (it->key == key && it->folder == folder && it->scope == scope && it->tag == tag) {
      it->recursive = it->recursive || recursive;
      it->facts = facts;
      return true;
    }
  }
  entries.push_back(Entry{key, folder, recursive, scope, tag, facts});
  trim();
  return true;
}

bool matches(const std::string &key, const Facts &now, const bool picks)
{
  std::lock_guard<std::mutex> guard(lock);
  for (const Entry &entry : entries) {
    if (entry.scope != Scope::Typed && entry.scope != Scope::Run && (picks || entry.scope != Scope::Pick) &&
        entry_matches(entry, key, now))
    {
      return true;
    }
  }
  return false;
}

bool matches_typed(const std::string &key, const std::string &tag)
{
  std::lock_guard<std::mutex> guard(lock);
  for (const Entry &entry : entries) {
    if (entry.scope == Scope::Typed && entry.tag == tag &&
        (entry_matches(entry, key, Facts()) || (!entry.folder && pattern_matches(entry.key, key))))
    {
      return true;
    }
  }
  return false;
}

bool file_named_in(const std::string &folder, const bool at_any_depth)
{
  std::lock_guard<std::mutex> guard(lock);
  for (const Entry &entry : entries) {
    if (entry.scope == Scope::Session && !entry.folder &&
        (at_any_depth ? inside(entry.key, folder) : parent_of(entry.key) == folder))
    {
      return true;
    }
  }
  return false;
}

bool identity_named(const Facts &now)
{
  std::lock_guard<std::mutex> guard(lock);
  for (const Entry &entry : entries) {
    if (!entry.folder && entry.scope != Scope::Run && same_file(entry.facts, now)) {
      return true;
    }
  }
  return false;
}

bool program_named(const std::string &key, const Facts &now)
{
  std::lock_guard<std::mutex> guard(lock);
  for (const Entry &entry : entries) {
    if (!entry.folder && entry_matches(entry, key, now) && same_content(entry.facts, now)) {
      return true;
    }
  }
  return false;
}

bool add_program(const std::string &key, const Facts &facts, const bool keep_captured)
{
  if (key.empty() || facts.link || !facts.exists || facts.directory) {
    return false;
  }
  std::lock_guard<std::mutex> guard(lock);
  for (Entry &entry : entries) {
    if (entry.key == key && entry.scope == Scope::Run) {
      if (keep_captured && !same_content(entry.facts, facts)) {
        return false; /* Changed since it was captured, after a script ran: never blessed by finding it again. */
      }
      entry.facts = facts;
      return true;
    }
  }
  size_t programs = 0;
  for (const Entry &entry : entries) {
    programs += entry.scope == Scope::Run ? 1 : 0;
  }
  if (programs >= PROGRAMS_KEPT) {
    return false; /* Never evicted to make room: a program past the bound is not recorded, so it does not run. */
  }
  entries.push_back(Entry{key, false, false, Scope::Run, "", facts});
  trim();
  return true;
}

bool program_changed(const std::string &key, const Facts &now)
{
  std::lock_guard<std::mutex> guard(lock);
  for (const Entry &entry : entries) {
    if (entry.scope == Scope::Run && entry.key == key) {
      return !same_content(entry.facts, now);
    }
  }
  return false;
}

void forget(const std::string &key, bool folder, bool calls_only)
{
  std::lock_guard<std::mutex> guard(lock);
  for (auto it = entries.begin(); it != entries.end();) {
    /* Never a program's record (Scope::Run): narrowing is anyone's, and a program forgotten would be recorded afresh,
     * changed, when Angelo finds it again (the re-review of 4be92cfb84f, S-1). A program never widens writing. */
    const bool match = it->key == key && it->folder == folder && it->scope != Scope::Run &&
                       (!calls_only || it->scope == Scope::Call);
    it = match ? entries.erase(it) : it + 1;
  }
}

void forget_scope(Scope scope)
{
  std::lock_guard<std::mutex> guard(lock);
  for (auto it = entries.begin(); it != entries.end();) {
    it = it->scope == scope ? entries.erase(it) : it + 1;
  }
}

void forget_tag(const std::string &tag)
{
  std::lock_guard<std::mutex> guard(lock);
  for (auto it = entries.begin(); it != entries.end();) {
    it = (it->scope == Scope::Typed && it->tag == tag) ? entries.erase(it) : it + 1;
  }
}

size_t count()
{
  std::lock_guard<std::mutex> guard(lock);
  return entries.size();
}

}  // namespace blender::angelo_guard::vouches
