/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The record of what confined threads changed (bpy_angelo_guard_confine_changes.hh). Every line is one record:
 *
 *   F|M|L|D <TAB> device <TAB> path   a file written, something moved or linked here, a folder recorded whole
 *   C <TAB> device <TAB> inode <TAB> size <TAB> digest   a file a script wrote or moved, by identity and content
 *
 * The path is escaped (`%`, tab, CR and LF as `%XX`); a line with no tab is the earlier format's path. Lines are
 * appended as changes are noted (a change that can't be appended is refused) and the file is rewritten whole when
 * a session first reads it (dropping what is gone) or when it has grown well past what it holds.
 */

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <deque>
#include <filesystem>
#include <functional>
#include <iterator>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "BLI_file_time.hh"
#include "BLI_fileops.hh"

#include <sys/stat.h>

#include "bpy_angelo_guard_confine_changes.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_sha256.hh"

namespace blender::angelo_guard {

/* Files a script wrote that are watched for a later write through a handle it kept open, and for how many region
 * ends one that stays unchanged is kept watched. */
static constexpr size_t WATCHED_MAX = 256;
static constexpr int WATCH_REGIONS = 32;

#ifdef _WIN32
static const char SEP = '\\';
#else
static const char SEP = '/';
#endif

namespace {

struct PathEntry {
  char kind;     /* 'F' written, 'M' moved here, 'L' linked here, 'D' a folder recorded whole, 'N' named only. */
  uint64_t dev;  /* The volume it is on (the first existing folder above it when noted). */
  bool settled;  /* 'M'/'L': known to be a file or a folder (until then it covers what is below it). */
};

struct Content {
  uint64_t dev, ino;
  int64_t size;
  std::string digest; /* Lower-case hex. */
  bool operator==(const Content &other) const
  {
    return dev == other.dev && ino == other.ino && size == other.size && digest == other.digest;
  }
};

struct State {
  std::mutex mutex;
  std::string file; /* Empty: in memory only. */
  bool loaded = false, unreadable = false;
  std::unordered_map<std::string, PathEntry> paths;
  /* Folder -> the records directly inside it (entries a script wrote, moved or linked into it). */
  std::unordered_map<std::string, std::unordered_set<std::string>> children;
  std::deque<Content> contents;
  std::unordered_map<int64_t, int> sizes; /* How many content records have each size: a load's first test. */
  /* Files the person forgot (Forget), by identity: never the script's by content, while a copy elsewhere with the
   * same content still is. A later write by a script to that identity counts again (its content line follows). */
  std::set<Identity> forgiven;
  /* Files a script wrote, as they were when last digested: a handle it kept open and wrote through in a later step
   * changes them with no event, so each region's end digests one that changed again (until it stays quiet). */
  struct Watched {
    int64_t size, mtime_ns;
    int quiet;
  };
  std::unordered_map<std::string, Watched> watched;
  std::function<bool(const std::string &)> compactable;
  long long stamp[2] = {-1, -1};
  /* When a load last looked for another session's changes: a load looks at most every `FRESH_MS` (every import pays
   * a clock read, not a `stat`); a change this session records always looks first. */
  std::chrono::steady_clock::time_point looked{};
  size_t lines = 0; /* Lines the file holds (to know when to rewrite it). */
};

State &state()
{
  static State instance;
  return instance;
}

/* What this thread noted since its last settle: settled when its region ends, on this thread, so a region ending
 * on another thread never digests a file this one is still writing. */
thread_local std::vector<std::string> pending;
thread_local std::unordered_set<std::string> pending_set;

}  // namespace

/* -------------------------------------------------------------------- */
/** \name Paths and file facts
 * \{ */

static std::optional<std::string> parent_of(const std::string &key)
{
  const size_t cut = key.find_last_of(SEP);
  if (cut == std::string::npos || key.size() <= 1) {
    return std::nullopt;
  }
  std::string parent = key.substr(0, cut);
#ifdef _WIN32
  if (parent.size() == 2 && parent[1] == ':') {
    parent += SEP;
  }
#endif
  if (parent.empty()) {
    parent = std::string(1, SEP);
  }
  return parent == key ? std::nullopt : std::optional<std::string>(parent);
}

static bool under(const std::string &key, const std::string &folder)
{
  if (key.size() <= folder.size() || key.compare(0, folder.size(), folder) != 0) {
    return false;
  }
  return folder.back() == SEP || key[folder.size()] == SEP;
}

struct Facts {
  bool exists = false, folder = false;
  int64_t size = 0;
  int64_t mtime_ns = 0; /* Nanoseconds where the system keeps them (seconds elsewhere). */
};

static Facts facts_of(const std::string &path)
{
  Facts facts;
  BLI_stat_t info;
  if (lookup_stat(path, &info) == 0) {
    facts.exists = true;
#ifdef _WIN32
    facts.folder = (info.st_mode & _S_IFDIR) != 0;
#else
    facts.folder = S_ISDIR(info.st_mode);
#endif
    facts.size = int64_t(info.st_size);
#if defined(__APPLE__)
    facts.mtime_ns = blender::file_time::nanoseconds_saturated(info.st_mtimespec.tv_sec, info.st_mtimespec.tv_nsec);
#elif defined(__linux__)
    facts.mtime_ns = blender::file_time::nanoseconds_saturated(info.st_mtim.tv_sec, info.st_mtim.tv_nsec);
#else
    facts.mtime_ns = blender::file_time::nanoseconds_saturated(info.st_mtime, 0);
#endif
  }
  return facts;
}

/** The volume of `key`, or of the first existing folder above it. 0: none found. */
static uint64_t volume_of(const std::string &key)
{
  std::optional<std::string> current = key;
  for (int guard = 0; current && guard < 512; guard++) {
    if (const std::optional<Identity> id = entry_identity(*current)) {
      return id->first;
    }
    current = parent_of(*current);
  }
  return 0;
}

static std::string hex(const uint8_t digest[32])
{
  static const char *digits = "0123456789abcdef";
  std::string text(64, '0');
  for (int i = 0; i < 32; i++) {
    text[i * 2] = digits[digest[i] >> 4];
    text[i * 2 + 1] = digits[digest[i] & 15];
  }
  return text;
}

/** The content digest: the whole file up to 2 * EDGE_BYTES, else its size, first and last EDGE_BYTES. */
static std::optional<std::string> digest_of(const std::string &path, int64_t size)
{
  FILE *file = open_ungated(path, "rb");
  if (file == nullptr) {
    return std::nullopt;
  }
  std::vector<uint8_t> data;
  bool ok = true;
  if (size <= 2 * EDGE_BYTES) {
    data.resize(size_t(size));
    ok = std::fread(data.data(), 1, data.size(), file) == data.size();
  }
  else {
    data.resize(8 + 2 * size_t(EDGE_BYTES));
    for (int i = 0; i < 8; i++) {
      data[i] = uint8_t(uint64_t(size) >> (8 * i));
    }
    ok = std::fread(data.data() + 8, 1, size_t(EDGE_BYTES), file) == size_t(EDGE_BYTES);
#ifdef _WIN32
    ok = ok && _fseeki64(file, size - EDGE_BYTES, SEEK_SET) == 0;
#else
    ok = ok && fseeko(file, off_t(size - EDGE_BYTES), SEEK_SET) == 0;
#endif
    ok = ok && std::fread(data.data() + 8 + EDGE_BYTES, 1, size_t(EDGE_BYTES), file) == size_t(EDGE_BYTES);
  }
  std::fclose(file);
  if (!ok) {
    return std::nullopt;
  }
  uint8_t digest[32];
  sha256(data.data(), data.size(), digest);
  return hex(digest);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The file
 * \{ */

static std::string escaped(const std::string &path)
{
  std::string out;
  for (const char c : path) {
    if (c == '%' || c == '\t' || c == '\n' || c == '\r') {
      char buffer[4];
      std::snprintf(buffer, sizeof(buffer), "%%%02X", unsigned(uint8_t(c)));
      out += buffer;
    }
    else {
      out += c;
    }
  }
  return out;
}

static std::string unescaped(const std::string &text)
{
  std::string out;
  for (size_t i = 0; i < text.size(); i++) {
    if (text[i] == '%' && i + 2 < text.size()) {
      out += char(std::strtol(text.substr(i + 1, 2).c_str(), nullptr, 16));
      i += 2;
    }
    else {
      out += text[i];
    }
  }
  return out;
}

static std::string path_line(const std::string &key, const PathEntry &entry)
{
  return std::string(1, entry.kind) + "\t" + std::to_string(entry.dev) + "\t" + escaped(key) + "\n";
}

static std::string content_line(const Content &content)
{
  return "C\t" + std::to_string(content.dev) + "\t" + std::to_string(content.ino) + "\t" +
         std::to_string(content.size) + "\t" + content.digest + "\n";
}

static void stamp_now(State &s)
{
  BLI_stat_t info;
  if (!s.file.empty() && BLI_stat(s.file.c_str(), &info) == 0) {
    s.stamp[0] = (long long)info.st_size;
    s.stamp[1] = (long long)info.st_mtime;
  }
  else {
    s.stamp[0] = s.stamp[1] = -1;
  }
}

static bool changed_elsewhere(State &s)
{
  BLI_stat_t info;
  if (s.file.empty() || BLI_stat(s.file.c_str(), &info) != 0) {
    return !s.file.empty() && s.stamp[0] != -1;
  }
  return (long long)info.st_size != s.stamp[0] || (long long)info.st_mtime != s.stamp[1];
}

static bool append(State &s, const std::string &text)
{
  if (s.file.empty() || text.empty()) {
    return true;
  }
  FILE *file = open_ungated(s.file, "ab");
  bool written = file != nullptr && std::fwrite(text.data(), 1, text.size(), file) == text.size();
  if (file != nullptr) {
    written = std::fclose(file) == 0 && written;
  }
  for (const char c : text) {
    s.lines += c == '\n';
  }
  stamp_now(s);
  return written;
}

static bool rewrite(State &s)
{
  if (s.file.empty()) {
    return true;
  }
  std::string text;
  for (const auto &[key, entry] : s.paths) {
    text += path_line(key, entry);
  }
  for (const Content &content : s.contents) {
    text += content_line(content);
  }
  for (const Identity &id : s.forgiven) { /* After the contents: a content line read later un-forgives. */
    text += "X\t" + std::to_string(id.first) + "\t" + std::to_string(id.second) + "\n";
  }
  const std::string temporary = s.file + ".new";
  FILE *file = open_ungated(temporary, "wb");
  bool written = file != nullptr && std::fwrite(text.data(), 1, text.size(), file) == text.size();
  if (file != nullptr) {
    written = std::fclose(file) == 0 && written;
  }
  written = written && replace_ungated(temporary, s.file); /* Never a half-written record. */
  s.lines = s.paths.size() + s.contents.size() + s.forgiven.size();
  stamp_now(s);
  return written;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Records in memory
 * \{ */

static void index_add(State &s, const std::string &key)
{
  if (const std::optional<std::string> parent = parent_of(key)) {
    s.children[*parent].insert(key);
  }
}

/** Records below `folder`, for every folder above `key` (only when the record is full: one pass over it). */
static std::unordered_map<std::string, size_t> counts_above(const State &s, const std::string &key)
{
  std::unordered_map<std::string, size_t> counts;
  for (std::optional<std::string> parent = parent_of(key); parent; parent = parent_of(*parent)) {
    counts[*parent] = 0;
  }
  for (const auto &[held, entry] : s.paths) {
    for (std::optional<std::string> parent = parent_of(held); parent; parent = parent_of(*parent)) {
      const auto found = counts.find(*parent);
      if (found != counts.end()) {
        found->second++;
      }
    }
  }
  return counts;
}

static void reindex(State &s)
{
  s.children.clear();
  for (const auto &[key, entry] : s.paths) {
    index_add(s, key);
  }
}

static bool covers(const PathEntry &entry)
{
  return entry.kind == 'D' || ((entry.kind == 'M' || entry.kind == 'L') && !entry.settled);
}

/** Whether `key` or a folder recorded above it is the script's. */
static bool path_made(const State &s, const std::string &key)
{
  if (s.paths.empty()) {
    return false;
  }
  if (s.paths.count(key)) {
    return true;
  }
  /* The folders above it, by trimming one copy in place (every load after a script asks: no string per level). */
  std::string folder = key;
  for (int guard = 0; guard < 512; guard++) {
    const size_t cut = folder.find_last_of(SEP);
    if (cut == std::string::npos || folder.size() <= 1) {
      return false;
    }
#ifdef _WIN32
    if (cut == 2 && folder[1] == ':') {
      if (folder.size() == 3) {
        return false; /* A volume's root. */
      }
      folder.resize(3);
    }
    else
#endif
    {
      folder.resize(cut == 0 ? 1 : cut);
    }
    const auto found = s.paths.find(folder);
    if (found != s.paths.end() && covers(found->second)) {
      return true;
    }
    if (folder.size() == 1) {
      return false; /* The root. */
    }
  }
  return false;
}

static void compact(State &s, const std::string &folder)
{
  for (auto it = s.paths.begin(); it != s.paths.end();) {
    it = under(it->first, folder) ? s.paths.erase(it) : std::next(it);
  }
  s.paths[folder] = PathEntry{'D', volume_of(folder), true};
  reindex(s);
}

static bool add_content(State &s, const Content &content)
{
  for (const Content &held : s.contents) {
    if (held == content) {
      return false;
    }
  }
  s.contents.push_back(content);
  s.sizes[content.size]++;
  while (s.contents.size() > MAX_CONTENT) {
    if (--s.sizes[s.contents.front().size] == 0) {
      s.sizes.erase(s.contents.front().size);
    }
    s.contents.pop_front();
  }
  return true;
}

static void parse(State &s, const std::string &text, std::vector<std::string> *legacy)
{
  size_t start = 0;
  while (start < text.size()) {
    size_t end = text.find('\n', start);
    if (end == std::string::npos) {
      end = text.size();
    }
    std::string line = text.substr(start, end - start);
    start = end + 1;
    if (!line.empty() && line.back() == '\r') {
      line.pop_back();
    }
    std::vector<std::string> fields;
    for (size_t at = 0;;) {
      const size_t tab = line.find('\t', at);
      fields.push_back(line.substr(at, tab == std::string::npos ? std::string::npos : tab - at));
      if (tab == std::string::npos) {
        break;
      }
      at = tab + 1;
    }
    if (fields.size() == 1 && !line.empty()) { /* The earlier format: a written path. */
      s.paths.emplace(line, PathEntry{'F', 0, true});
      if (legacy) {
        legacy->push_back(line);
      }
    }
    else if (fields.size() == 3 && fields[0].size() == 1 && strchr("FMLDN", fields[0][0])) {
      const PathEntry entry{fields[0][0], std::strtoull(fields[1].c_str(), nullptr, 10), fields[0][0] != 'M' &&
                                                                                           fields[0][0] != 'L'};
      s.paths[unescaped(fields[2])] = entry;
    }
    else if (fields.size() == 5 && fields[0] == "C" && fields[4].size() == 64) {
      const Content content{std::strtoull(fields[1].c_str(), nullptr, 10),
                            std::strtoull(fields[2].c_str(), nullptr, 10),
                            std::strtoll(fields[3].c_str(), nullptr, 10),
                            fields[4]};
      s.forgiven.erase({content.dev, content.ino}); /* Written after it was forgotten: the script's again. */
      add_content(s, content);
    }
    else if (fields.size() == 3 && fields[0] == "X") {
      s.forgiven.insert(
          {std::strtoull(fields[1].c_str(), nullptr, 10), std::strtoull(fields[2].c_str(), nullptr, 10)});
    }
  }
}

static std::optional<std::string> read_file(State &s)
{
  FILE *file = open_ungated(s.file, "rb");
  if (file == nullptr) {
    if (errno == ENOENT) {
      return std::string();
    }
    return std::nullopt;
  }
  std::string text;
  char buffer[65536];
  size_t got;
  while ((got = std::fread(buffer, 1, sizeof(buffer), file)) > 0) {
    text.append(buffer, got);
  }
  const bool failed = std::ferror(file) != 0;
  std::fclose(file);
  return failed ? std::nullopt : std::optional<std::string>(text);
}

static void settle_keys(State &s, const std::vector<std::string> &keys);

/** A session's first read: what is gone is dropped (unless its volume is), then the file is rewritten. */
static void load(State &s)
{
  if (s.loaded) {
    return;
  }
  s.loaded = true;
  if (s.file.empty()) {
    return;
  }
  const std::optional<std::string> text = read_file(s);
  if (!text) {
    s.unreadable = true; /* Fail closed: only installed code loads. */
    return;
  }
  std::vector<std::string> legacy;
  parse(s, *text, &legacy);
  for (auto it = s.paths.begin(); it != s.paths.end();) {
    PathEntry &entry = it->second;
    const Facts facts = facts_of(it->first);
    bool keep = facts.exists;
    if (facts.exists && facts.folder && entry.kind == 'F') {
      keep = false; /* A folder is never a written file (the earlier format recorded folders made). */
    }
    else if (facts.exists && (entry.kind == 'M' || entry.kind == 'L')) {
      entry.kind = facts.folder ? 'D' : entry.kind;
      entry.settled = true;
    }
    else if (!facts.exists && entry.dev != 0 && volume_of(it->first) != entry.dev) {
      keep = true; /* Its volume is not here now (a removable or network drive): kept for when it is. */
    }
    it = keep ? std::next(it) : s.paths.erase(it);
  }
  reindex(s);
  settle_keys(s, legacy); /* The earlier format had no identity or digest: taken now. */
  s.unreadable = !rewrite(s); /* Couldn't put back what it held: fail closed. */
}

/** Another session changed the file: read it again (what it noted counts here too). */
static void reread(State &s)
{
  const std::optional<std::string> text = read_file(s);
  if (!text) {
    s.unreadable = true;
    stamp_now(s); /* Known unreadable as it is now: tried again when it changes (`recover`). */
    return;
  }
  s.paths.clear();
  s.contents.clear();
  s.sizes.clear();
  s.forgiven.clear();
  parse(s, *text, nullptr);
  reindex(s);
  s.lines = size_t(std::count(text->begin(), text->end(), '\n'));
  stamp_now(s);
}

/** The record could not be read or written, and its file has changed since: read it again, keep what this session
 * holds too, and write it whole. Until that works, only installed code loads and every change is refused. */
static void recover(State &s)
{
  if (!s.unreadable || s.file.empty() || !changed_elsewhere(s)) {
    return;
  }
  const std::optional<std::string> text = read_file(s);
  if (!text) {
    stamp_now(s);
    return;
  }
  State held;
  held.paths.swap(s.paths);
  held.contents.swap(s.contents);
  held.forgiven.swap(s.forgiven);
  s.sizes.clear();
  parse(s, *text, nullptr);
  s.forgiven.insert(held.forgiven.begin(), held.forgiven.end());
  for (const auto &[key, entry] : held.paths) {
    s.paths.emplace(key, entry);
  }
  for (const Content &content : held.contents) {
    add_content(s, content);
  }
  reindex(s);
  s.unreadable = !rewrite(s);
}

static void settle_keys(State &s, const std::vector<std::string> &keys)
{
  std::string lines;
  for (const std::string &key : keys) {
    auto found = s.paths.find(key);
    PathEntry covered{'F', 0, true}; /* Recorded through a folder above it (compacted): its content still counts. */
    if (found == s.paths.end() && !path_made(s, key)) {
      continue;
    }
    PathEntry &entry = found != s.paths.end() ? found->second : covered;
    const Facts facts = facts_of(key);
    if (!facts.exists) {
      continue; /* Not there (yet, or removed): its path stays recorded. */
    }
    if (facts.folder) {
      if (entry.kind == 'F') {
        if (found != s.paths.end()) {
          s.paths.erase(found); /* A folder a writer named: not a file. */
        }
        continue;
      }
      if (entry.kind == 'M' || entry.kind == 'L') {
        entry.kind = 'D';
        entry.settled = true;
        lines += path_line(key, entry);
      }
      continue;
    }
    entry.settled = true;
    if (entry.kind == 'L' || facts.size == 0) {
      continue; /* A link shares another file's identity; an empty file holds no code. */
    }
    const std::optional<Identity> id = entry_identity(key);
    const std::optional<std::string> digest = digest_of(key, facts.size);
    if (id && digest) {
      const Content content{id->first, id->second, facts.size, *digest};
      const bool was_forgiven = s.forgiven.erase(*id) != 0; /* A script wrote it again: its own once more. */
      if (add_content(s, content) || was_forgiven) {
        lines += content_line(content);
      }
      if (s.watched.size() < WATCHED_MAX || s.watched.count(key)) {
        s.watched[key] = State::Watched{facts.size, facts.mtime_ns, 0};
      }
    }
  }
  if (!append(s, lines)) {
    s.unreadable = true; /* What scripts wrote can't be kept: fail closed. */
  }
  if (s.lines > 4 * (s.paths.size() + s.contents.size()) + 4096 && !rewrite(s)) {
    s.unreadable = true;
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The doors
 * \{ */

void changes_record_set(const std::string &path)
{
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  s.file = path;
  s.looked = {};
  s.loaded = false;
  s.unreadable = false;
  s.paths.clear();
  s.contents.clear();
  s.sizes.clear();
  s.forgiven.clear();
  s.watched.clear();
  s.children.clear();
}

static bool compactable(State &s, const std::string &folder)
{
  return parent_of(folder).has_value() && s.compactable && s.compactable(folder);
}

bool changes_note(const std::string &path_key,
                  const Change change,
                  const std::function<bool(const std::string &)> &compactable_fn)
{
  if (change == Change::Metadata) {
    return true;
  }
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  s.compactable = compactable_fn;
  load(s);
  recover(s);
  if (s.unreadable) {
    return false;
  }
  if (changed_elsewhere(s)) {
    reread(s);
    if (s.unreadable) {
      return false;
    }
  }
  const char kind = change == Change::Moved  ? 'M' :
                    change == Change::Linked ? 'L' :
                    change == Change::Named  ? 'N' :
                                               'F';
  if (kind != 'N' && pending_set.insert(path_key).second) {
    pending.push_back(path_key); /* What it is, and its identity and digest, once written (`changes_settle`). */
  }
  const auto found = s.paths.find(path_key);
  if (found != s.paths.end() && (found->second.kind == kind || found->second.kind == 'D' || kind == 'N')) {
    return true; /* Named after a write: the write stands. */
  }
  if (path_made(s, path_key) && found == s.paths.end()) {
    return true; /* A folder recorded above it already covers it. */
  }
  const PathEntry entry{kind, volume_of(path_key), kind == 'F'};
  s.paths[path_key] = entry;
  index_add(s, path_key);
  std::string lines = path_line(path_key, entry);
  bool compacted = false;
  /* More than COMPACT_CHILDREN entries written into one folder: the folder is recorded whole (and is then itself
   * an entry of the folder above it). */
  for (std::optional<std::string> parent = parent_of(path_key); parent; parent = parent_of(*parent)) {
    const auto children = s.children.find(*parent);
    if (children == s.children.end() || children->second.size() <= COMPACT_CHILDREN || !compactable(s, *parent)) {
      break;
    }
    compact(s, *parent);
    compacted = true;
  }
  while (s.paths.size() > MAX_PATHS) {
    /* Full: the deepest compactable folder above the new path holding at least COMPACT_CHILDREN records, else the
     * shallowest holding more than one. */
    const std::unordered_map<std::string, size_t> counts = counts_above(s, path_key);
    std::optional<std::string> chosen, fallback;
    for (std::optional<std::string> parent = parent_of(path_key); parent; parent = parent_of(*parent)) {
      const size_t held = counts.at(*parent);
      if (held < 2 || !compactable(s, *parent)) {
        continue;
      }
      if (!chosen && held >= COMPACT_CHILDREN) {
        chosen = parent;
      }
      fallback = parent;
    }
    chosen = chosen ? chosen : fallback;
    if (!chosen) {
      s.paths.erase(path_key); /* Nothing can make room: the change is refused. */
      reindex(s);
      return false;
    }
    compact(s, *chosen);
    compacted = true;
  }
  /* A compaction is written whole at once: the file holds what the record holds, never the folder's old entries. */
  return compacted ? rewrite(s) : append(s, lines);
}

/** The files a script wrote that changed since they were digested (a handle kept open, written in a later step):
 * digested again, so the record names the content they hold now. One quiet for `WATCH_REGIONS` region ends goes. */
static void settle_watched(State &s, const std::vector<std::string> &just_settled)
{
  std::string lines;
  for (auto it = s.watched.begin(); it != s.watched.end();) {
    const Facts facts = facts_of(it->first);
    if (!facts.exists || facts.folder) {
      it = s.watched.erase(it);
      continue;
    }
    State::Watched &held = it->second;
    if (facts.size == held.size && facts.mtime_ns == held.mtime_ns) {
      const bool fresh = std::find(just_settled.begin(), just_settled.end(), it->first) != just_settled.end();
      it = (!fresh && ++held.quiet > WATCH_REGIONS) ? s.watched.erase(it) : std::next(it);
      continue;
    }
    held = State::Watched{facts.size, facts.mtime_ns, 0};
    const std::optional<Identity> id = entry_identity(it->first);
    const std::optional<std::string> digest = facts.size > 0 ? digest_of(it->first, facts.size) : std::nullopt;
    if (id && digest) {
      const Content content{id->first, id->second, facts.size, *digest};
      const bool was_forgiven = s.forgiven.erase(*id) != 0;
      if (add_content(s, content) || was_forgiven) {
        lines += content_line(content);
      }
    }
    ++it;
  }
  if (!append(s, lines)) {
    s.unreadable = true;
  }
}

static std::atomic<bool> watching{false};

void changes_settle()
{
  if (pending.empty() && !watching.load(std::memory_order_relaxed)) {
    return; /* Nothing written and nothing watched: no region's end pays for this. */
  }
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  std::vector<std::string> keys;
  keys.swap(pending);
  pending_set.clear();
  if (!s.unreadable) {
    settle_keys(s, keys);
    settle_watched(s, keys);
  }
  watching.store(!s.watched.empty(), std::memory_order_relaxed);
}

Made changes_made(const std::string &path_key, const std::function<bool()> &by_content)
{
  static constexpr std::chrono::milliseconds FRESH(FRESH_MS);
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  load(s);
  const auto now = std::chrono::steady_clock::now();
  if (now - s.looked >= FRESH) {
    s.looked = now;
    recover(s);
    if (!s.unreadable && changed_elsewhere(s)) {
      reread(s);
    }
  }
  if (s.unreadable) {
    return Made::Unknown;
  }
  if (path_made(s, path_key)) {
    return Made::Yes;
  }
  if (s.contents.empty() || !by_content()) {
    return Made::No;
  }
  if (!s.forgiven.empty()) {
    const std::optional<Identity> own = entry_identity(path_key);
    if (own && s.forgiven.count(*own)) {
      return Made::No; /* The person forgot it (Forget): this file, not a copy elsewhere. */
    }
  }
  const Facts facts = facts_of(path_key);
  if (!facts.exists || facts.folder || facts.size == 0 || !s.sizes.count(facts.size)) {
    return Made::No;
  }
  std::optional<Identity> id;
  std::optional<std::string> digest;
  for (const Content &content : s.contents) {
    if (content.size != facts.size) {
      continue; /* Only a file of the same size is identified or digested. */
    }
    if (!id) {
      id = entry_identity(path_key);
    }
    if (id && id->first == content.dev && id->second == content.ino) {
      return Made::Yes;
    }
    if (!digest) {
      digest = digest_of(path_key, facts.size);
      if (!digest) {
        return Made::Yes; /* Unreadable now: not loaded as Angelo's. */
      }
    }
    if (*digest == content.digest) {
      return Made::Yes;
    }
  }
  return Made::No;
}

Made changes_wrote(const std::string &path_key)
{
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  load(s);
  recover(s);
  if (!s.unreadable && changed_elsewhere(s)) {
    reread(s);
  }
  if (s.unreadable) {
    return Made::Unknown;
  }
  const Facts facts = facts_of(path_key);
  if (!facts.exists || facts.folder) {
    return Made::No;
  }
  const std::optional<Identity> id = entry_identity(path_key);
  if (id && s.forgiven.count(*id)) {
    return Made::No; /* The person forgot it (Forget). */
  }
  /* This very file, wherever it is now: a script wrote or moved it (by identity, never by its bytes alone). */
  bool same_size_recorded = false;
  for (const Content &content : s.contents) {
    if (id && id->first == content.dev && id->second == content.ino) {
      return Made::Yes;
    }
    same_size_recorded = same_size_recorded || content.size == facts.size;
  }
  /* Its own path recorded (a folder recorded above it, compacted, says only that a script wrote in there: a file
   * of the person's beside its writes is theirs unless its identity was recorded, above). */
  const auto recorded = s.paths.find(path_key);
  if (recorded == s.paths.end() || recorded->second.kind == 'N') {
    return Made::No; /* Never written there: only named (an operator's file to read, a path property). */
  }
  /* Still the file written there, unless it was replaced since (the person saved over it). Without a content
   * record at all (the earlier format), the path decides. */
  if (s.contents.empty()) {
    return Made::Yes;
  }
  if (!same_size_recorded) {
    return Made::No;
  }
  const std::optional<std::string> digest = digest_of(path_key, facts.size);
  if (!digest) {
    return Made::Yes; /* Unreadable now: fail toward the script. */
  }
  for (const Content &content : s.contents) {
    if (content.size == facts.size && content.digest == *digest) {
      return Made::Yes;
    }
  }
  return Made::No;
}

int changes_forget(const std::string &folder_key)
{
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  load(s);
  recover(s);
  if (!s.unreadable && changed_elsewhere(s)) {
    reread(s);
  }
  if (s.unreadable) {
    return -1;
  }
  int forgotten = 0;
  for (auto it = s.paths.begin(); it != s.paths.end();) {
    const bool inside = it->first == folder_key || under(it->first, folder_key);
    const bool above = under(folder_key, it->first) && covers(it->second);
    forgotten += inside || above;
    it = (inside || above) ? s.paths.erase(it) : std::next(it);
  }
  /* The files now in the folder that a script wrote or moved there, by identity and content: forgiven by identity, so
   * a copy of the same content elsewhere (never forgotten) still counts as the script's. Links are not followed: a
   * link in the folder names a file elsewhere, which the person did not forget. Never walked when it is a remote
   * folder on a share nobody connected (bpy_angelo_guard_confine_remote.hh). */
  if (lookup_may_touch(folder_key)) {
    std::error_code error;
    std::filesystem::recursive_directory_iterator walk(
        std::filesystem::path(reinterpret_cast<const char8_t *>(folder_key.c_str())),
        std::filesystem::directory_options::skip_permission_denied,
        error);
    size_t seen = 0;
    for (; !error && walk != std::filesystem::recursive_directory_iterator() && seen < 200000;
         walk.increment(error), seen++)
    {
      std::error_code kind_error;
      if (walk->is_symlink(kind_error) || kind_error || !walk->is_regular_file(kind_error)) {
        continue;
      }
      const std::u8string spelled = walk->path().u8string();
      const std::string path(spelled.begin(), spelled.end());
      const Facts facts = facts_of(path);
      const std::optional<Identity> id = entry_identity(path);
      if (!id || s.forgiven.count(*id)) {
        continue;
      }
      std::optional<std::string> digest;
      for (const Content &content : s.contents) {
        if (content.size != facts.size) {
          continue;
        }
        digest = digest ? digest : digest_of(path, facts.size);
        if ((id->first == content.dev && id->second == content.ino) || (digest && *digest == content.digest)) {
          s.forgiven.insert(*id);
          forgotten++;
          break;
        }
      }
    }
  }
  reindex(s);
  return rewrite(s) ? forgotten : -1;
}

size_t changes_paths_count()
{
  std::lock_guard<std::mutex> guard(state().mutex);
  return state().paths.size();
}

size_t changes_content_count()
{
  std::lock_guard<std::mutex> guard(state().mutex);
  return state().contents.size();
}

void changes_reset()
{
  State &s = state();
  std::lock_guard<std::mutex> guard(s.mutex);
  s.paths.clear();
  s.contents.clear();
  s.sizes.clear();
  s.forgiven.clear();
  s.watched.clear();
  s.children.clear();
  pending.clear();
  pending_set.clear();
  s.loaded = false;
  s.unreadable = false;
}

/** \} */

}  // namespace blender::angelo_guard
