/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What a writer may write when its data is the script's, and what Angelo may make below a place C accepts
 * (doc/angelo/script_confinement.rst, "Output paths in data"; the re-reads of 9dcfb883ebb and f9f24cc6ffd).
 *
 * `BPY_angelo_guard_output_allowed(path, owner, data, prop)`: a place C says Angelo may write, what the person typed
 * into THAT property (its pattern covers its own expansions, vouches.cc), or a NEW file below an anchor C accepts
 * (the open document's folder, or the folder of a file the person named): made here, exclusively, each missing
 * folder too, following no link, named as written, and recorded as Angelo's (so its own later frames are
 * rewritten), never over an existing file of the person's. Writers ask it only when the data they read is the
 * script's (tagged.cc).
 *
 * `make_dir_beside(anchor, name)` is the same rule for Angelo's own folders in such places (a document's
 * `assets/downloads`, an engine project's `.angelo`): each part made by C below an anchor C accepts, or an existing
 * real one adopted, and recorded.
 *
 * (Round 2's place-wide `script_named` and the place record went with the writers lane's fold: writers ask by the
 * data their path is built from.) No Python: the writers call these from job threads; the open
 * document's folder is read on the main thread only. Records live for the process only.
 */

#include <mutex>
#include <string>
#include <vector>

#ifdef _WIN32
#  include <cstdio>
#else
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

#include "BKE_main.hh"

#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.hh"
#include "BLI_threads.hh"

#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_remote.hh"
#include "bpy_angelo_guard_confine_vouches.hh"

namespace blender::angelo_guard {

namespace {

/* `SEP`: blenlib's native separator (BLI_path_utils.hh). */

/** The folder above `key` (a drive's or the file system's root keeps its separator); empty for none. */
std::string parent_key(const std::string &key)
{
  const size_t cut = key.find_last_of(SEP);
  if (cut == std::string::npos || cut == 0 || (cut == 2 && key[1] == ':')) {
    return cut == std::string::npos ? std::string() : key.substr(0, cut + 1);
  }
  return key.substr(0, cut);
}

/** The folder of the document the PERSON last opened or saved (`BPY_angelo_guard_document_settled`), resolved;
 * empty before one, or after a script's turn opened or saved the document elsewhere: a document a confined turn
 * chose never makes its folder an anchor (the coordinator's ruling on the re-read of f9f24cc6ffd). Set on the main
 * thread, read under the lock from any (a job thread never reads Blender's global path). */
std::mutex document_lock;
std::string person_document;

std::string document_key()
{
  std::lock_guard<std::mutex> guard(document_lock);
  return person_document;
}

/** The last `count` components of `plain` as written (case kept): what `make_below` names, so a file made on a
 * case-insensitive system has the very name the writer opens (the re-read of f9f24cc6ffd, NIT). */
bool spelled_parts(const std::string &plain, const size_t count, std::vector<std::string> &parts)
{
  char text[FILE_MAX];
  STRNCPY(text, plain.c_str());
  if (!BLI_path_is_abs_from_cwd(text)) {
    BLI_path_abs_from_cwd(text, sizeof(text));
  }
  BLI_path_normalize_native(text);
  std::vector<std::string> all;
  const std::string normalized = text;
  size_t start = 0;
  while (start < normalized.size()) {
    size_t end = normalized.find_first_of("/\\", start);
    end = end == std::string::npos ? normalized.size() : end;
    if (end > start) {
      all.push_back(normalized.substr(start, end - start));
    }
    start = end + 1;
  }
  if (all.size() < count) {
    return false;
  }
  parts.assign(all.end() - count, all.end());
  return true;
}

/** The anchor C accepts above `key` for something new: the open document's folder when `key` lies in it, else the
 * nearest folder above it that directly holds a file the person named for the session (never a too-wide one).
 * Empty: none. */
std::string anchor_above(const std::string &key)
{
  const std::string document = document_key();
  if (!document.empty() && write_inside(key, document)) {
    return document;
  }
  for (std::string current = parent_key(key); !current.empty() && !too_wide(current);
       current = parent_key(current))
  {
    if (vouches::file_named_in(current, false)) {
      return current;
    }
    if (parent_key(current) == current) {
      break;
    }
  }
  return "";
}

/** The parts of `relative` (separated by either slash): none empty, `.`, `..`, or (Windows) naming a stream. */
bool split_parts(const std::string &relative, std::vector<std::string> &parts)
{
  size_t start = 0;
  while (start <= relative.size()) {
    size_t end = relative.find_first_of("/\\", start);
    end = end == std::string::npos ? relative.size() : end;
    const std::string part = relative.substr(start, end - start);
    if (part.empty() || part == "." || part == ".." || part.find(':') != std::string::npos) {
      return false;
    }
    parts.push_back(part);
    start = end + 1;
  }
  return !parts.empty();
}

enum class Made { No, Exists, New };

/** Makes the folder `path` (its parent exists and is no link): new, or an existing real folder. */
Made make_folder(const std::string &path, const int mode)
{
  const vouches::Facts facts = vouches::facts_of(path);
  if (facts.exists) {
    return (facts.directory && !facts.link && !write_link_at(path)) ? Made::Exists : Made::No;
  }
#ifdef _WIN32
  (void)mode;
  if (!BLI_dir_create_recursive(path.c_str())) {
    return Made::No;
  }
#else
  if (mkdir(path.c_str(), mode_t(mode)) != 0) {
    return Made::No;
  }
#endif
  return record_owned(path) ? Made::New : Made::No;
}

/** Makes `parts` below `anchor` (a resolved key), one by one, following no link: the last as a file (exclusively,
 * never an existing one) when `file`, else a folder. Each made part is recorded as Angelo's; an existing folder
 * on the way is used as it is (`adopt_last`: the last one, existing, becomes Angelo's when it is a real folder no
 * script made). The path made, or empty. */
std::string make_below(const std::string &anchor,
                       const std::vector<std::string> &parts,
                       const bool file,
                       const int mode,
                       const bool adopt_last)
{
  std::string current = anchor;
  for (size_t i = 0; i < parts.size(); i++) {
    const bool last = i + 1 == parts.size();
    const std::string next = current + (current.back() == SEP ? "" : std::string(1, SEP)) + parts[i];
    if (last && file) {
#ifdef _WIN32
      if (!lookup_may_touch(next)) {
        return ""; /* A remote name on a share nobody connected: never created (remote.hh). */
      }
      FILE *handle = BLI_fopen(next.c_str(), "wbx"); /* Exclusive: never an existing file, nor a link put there. */
      if (handle == nullptr) {
        return "";
      }
      fclose(handle);
#else
      const int fd = open(next.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0666);
      if (fd < 0) {
        return "";
      }
      close(fd);
#endif
      if (!record_owned(next)) {
        BLI_delete(next.c_str(), false, false);
        return "";
      }
      return next;
    }
    const Made made = make_folder(next, mode);
    if (made == Made::No) {
      return "";
    }
    if (made == Made::Exists && last && adopt_last && !owned_exactly(next)) {
      if (write_script_made_on_the_way(next) || born_since_first_region(next) || !record_owned(next)) {
        return ""; /* A folder a script made, or could have made since a script ran: never Angelo's. */
      }
    }
    current = next;
  }
  return current;
}

std::string text_of(PyObject *arg)
{
  PyObject *bytes = nullptr;
  if (!PyUnicode_FSConverter(arg, &bytes)) {
    PyErr_Clear();
    return "";
  }
  std::string out = PyBytes_AS_STRING(bytes);
  Py_DECREF(bytes);
  return out.find('\0') == std::string::npos ? out : "";
}

bool angelo_calling_now()
{
  return !confined_now() && !frames_confined();
}

}  // namespace

PyObject *guard_output_allowed_for(PyObject * /*self*/, PyObject *args)
{
  PyObject *path_arg;
  unsigned long long owner = 0, data = 0;
  const char *identifier = nullptr;
  if (!PyArg_ParseTuple(args, "OKKs:output_allowed_for", &path_arg, &owner, &data, &identifier)) {
    return nullptr;
  }
  if (!angelo_calling_now()) {
    refuse(GUARD, "output_allowed_for");
    return nullptr;
  }
  const std::string path = text_of(path_arg);
  return PyBool_FromLong(!path.empty() && BPY_angelo_guard_output_allowed(path.c_str(),
                                                                         reinterpret_cast<const ID *>(owner),
                                                                         reinterpret_cast<const void *>(data),
                                                                         identifier));
}

PyObject *guard_folder_allowed_for(PyObject * /*self*/, PyObject *args)
{
  PyObject *path_arg;
  unsigned long long owner = 0, data = 0;
  const char *identifier = nullptr;
  if (!PyArg_ParseTuple(args, "OKKs:folder_allowed_for", &path_arg, &owner, &data, &identifier)) {
    return nullptr;
  }
  if (!angelo_calling_now()) {
    refuse(GUARD, "folder_allowed_for");
    return nullptr;
  }
  const std::string path = text_of(path_arg);
  return PyBool_FromLong(!path.empty() && BPY_angelo_guard_folder_allowed(path.c_str(),
                                                                         reinterpret_cast<const ID *>(owner),
                                                                         reinterpret_cast<const void *>(data),
                                                                         identifier));
}

/** `make_dir_beside(anchor, name, mode=0o700)`: the folder `name` (one or more fixed parts) below `anchor`, made by C
 * part by part following no link, or an existing real one no script made, recorded as Angelo's. The anchor must be
 * one C accepts: the open document's folder (or a folder in it), a folder holding a file the person named for the
 * session, or one Angelo may write. Its path, or None. */
PyObject *guard_make_dir_beside(PyObject * /*self*/, PyObject *args)
{
  PyObject *anchor_arg, *name_arg;
  int mode = 0700;
  if (!PyArg_ParseTuple(args, "OO|i:make_dir_beside", &anchor_arg, &name_arg, &mode)) {
    return nullptr;
  }
  if (!angelo_calling_now()) {
    refuse(GUARD, "make_dir_beside");
    return nullptr;
  }
  const std::string anchor = text_of(anchor_arg), name = text_of(name_arg);
  std::vector<std::string> parts;
  if (anchor.empty() || !split_parts(name, parts)) {
    Py_RETURN_NONE;
  }
  const std::string key = write_key(anchor, true);
  const vouches::Facts facts = vouches::facts_of(write_plain(anchor));
  if (key.empty() || !facts.directory || facts.link || write_link_at(write_plain(anchor))) {
    Py_RETURN_NONE;
  }
  const std::string document = document_key();
  const bool accepted = (!document.empty() && (key == document || write_inside(key, document))) ||
                        (!too_wide(key) && vouches::file_named_in(key, true)) ||
                        may_write(key + SEP + parts.front());
  if (!accepted) {
    Py_RETURN_NONE;
  }
  const std::string made = make_below(key, parts, false, mode, true);
  if (made.empty()) {
    Py_RETURN_NONE;
  }
  return PyUnicode_DecodeFSDefault(made.c_str());
}

}  // namespace blender::angelo_guard

namespace blender {

using namespace angelo_guard;

void BPY_angelo_guard_connections_at_start()
{
  remote_connections_at_start();
}

void BPY_angelo_guard_connections_ready()
{
  remote_connections_ready();
}

void BPY_angelo_guard_document_settled()
{
  const char *document = BKE_main_blendfile_path_from_global();
  const std::string key = (document == nullptr || document[0] == '\0') ? std::string() :
                                                                          parent_key(write_key(document, false));
  /* The thread's confinement only: the window manager calls this with no GIL held (startup's own load). */
  const bool scripts = confined_now();
  /* The person's document on a share: theirs, so it stays usable after scripts ran (remote.hh). Not while any
   * thread is confined: a script's queued open never counts. */
  if (document != nullptr && document[0] != '\0') {
    note_person_document(document,
                         !scripts && BLI_fileops_confine_open.load(std::memory_order_acquire) == 0 &&
                             !python_on_stack()); /* The person's own open only (head-10/11 re-read SHOULD). */
  }
  std::lock_guard<std::mutex> guard(document_lock);
  if (!scripts) {
    person_document = key; /* The person's own open or save (or Angelo's on their behalf, unconfined). */
  }
  else if (key != person_document) {
    person_document.clear(); /* A script opened or saved the document elsewhere: no anchor until the person's. */
  }
}

/** A NEW file at `plain` (resolved `key`) below an anchor C accepts (the open document's folder, the folder of a
 * file the person named): made here, each missing folder too, following no link, named as written, recorded as
 * Angelo's; never over an existing file. */
static bool make_new_below_anchor(const std::string &plain, const std::string &key)
{
  const std::string anchor = anchor_above(key);
  if (anchor.empty() || vouches::facts_of(plain).exists) {
    return false;
  }
  std::vector<std::string> keyed, parts;
  if (!split_parts(key.substr(anchor.size() + (anchor.back() == SEP ? 0 : 1)), keyed) ||
      !spelled_parts(plain, keyed.size(), parts))
  {
    return false;
  }
  /* The parts as written must name the resolved file: a link on the way below the anchor (`Proj/link/new.png`)
   * would otherwise make a stray `<anchor>/link/new.png` while the writer writes the link's target. */
  std::string target = anchor;
  for (const std::string &part : parts) {
    target += (target.back() == SEP ? "" : std::string(1, SEP)) + part;
  }
  if (write_key(target, false) != key) {
    return false;
  }
  return !make_below(anchor, parts, true, 0777, false).empty();
}

bool BPY_angelo_guard_output_allowed(const char *path, const ID *owner, const void *data, const char *prop)
{
  if (path == nullptr || path[0] == '\0') {
    return false;
  }
  if (!write_confinement_active()) {
    return true; /* No script region began in this process: no data is the script's. */
  }
  const std::string plain = write_plain(path);
  const std::string key = plain.empty() ? std::string() : write_key(plain, false);
  if (key.empty()) {
    return false;
  }
  if (may_write(plain)) {
    return true;
  }
  /* What the person typed into THAT property (its pattern covers its own frames and template values). */
  if (prop != nullptr && vouches::matches_typed(key, typed_tag(owner, data, prop))) {
    return true;
  }
  return make_new_below_anchor(plain, key);
}

bool BPY_angelo_guard_folder_allowed(const char *dir, const ID *owner, const void *data, const char *prop)
{
  if (dir == nullptr || dir[0] == '\0') {
    return false;
  }
  if (!write_confinement_active()) {
    return true;
  }
  const std::string plain = write_plain(dir);
  const std::string key = plain.empty() ? std::string() : write_key(plain, true);
  if (key.empty()) {
    return false;
  }
  if (may_write(plain) || may_write(plain + SEP + "angelo-output")) {
    return true; /* Angelo's own folder, or one the person named (whole). */
  }
  if (prop != nullptr && vouches::matches_typed(key, typed_tag(owner, data, prop))) {
    return true; /* The folder the person typed into THAT property. */
  }
  /* A NEW folder below an anchor C accepts, made here part by part following no link, recorded as Angelo's (what
   * the writer then writes in it is Angelo's own place). An existing folder of the person's never becomes one. */
  if (vouches::facts_of(plain).exists) {
    return false;
  }
  const std::string anchor = anchor_above(key);
  std::vector<std::string> keyed, parts;
  if (anchor.empty() || !split_parts(key.substr(anchor.size() + (anchor.back() == SEP ? 0 : 1)), keyed) ||
      !spelled_parts(plain, keyed.size(), parts))
  {
    return false;
  }
  return !make_below(anchor, parts, false, 0777, false).empty();
}

}  // namespace blender
