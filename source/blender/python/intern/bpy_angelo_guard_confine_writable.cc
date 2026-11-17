/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Where Angelo's own unconfined code may write, replace or remove a file whose path comes from Python state
 * (doc/angelo/script_confinement.rst, "Angelo's own I/O"). A script that reached that state could change it in
 * place, and Angelo's next write would go where the script named, over a file of the person's. So the path is
 * judged against facts C holds, never against the Python state alone:
 *
 * - inside Angelo's private folders (where a script can write nothing, or a workspace in them it may use: still
 *   Angelo's own, and a link there is never followed to elsewhere: its folders are resolved first);
 * - a file or folder Angelo made or adopted (owned.cc's record, by identity), or anything inside such a folder;
 * - a path the person named (vouches.cc): a file Angelo's code vouched for where it entered, one call's folder of
 *   Blender's own, the person's pick in a file browser for that operator's run, a path typed into a file property,
 *   or a folder named before any script region. Nothing is kept past the process.
 *
 * Never a path whose last part is a link, and never a file with another hard link unless that very file (by
 * identity) was made or named: a script could link a file of the person's into a folder Angelo writes. The file
 * tools' own folder, the open document's, is no writable folder: it anchors only the file tools' descriptors
 * (`document_folder`). Before any script region began in the process (startup's migrations and carry-over), and
 * in a process with no confinement, any path is writable.
 */

#include <Python.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#ifdef _WIN32
#  include <windows.h>
#else
#  include <climits>
#  include <cstdlib>
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

#include "BKE_appdir.hh"
#include "BKE_main.hh"

#include "BLI_angelo_writers.hh"
#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.hh"

#include "DNA_ID.h"

#include "WM_api.hh"

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_vouches.hh"
#include "bpy_angelo_guard_confine_winpath.hh"

namespace blender::angelo_guard {

namespace {

#ifdef _WIN32
constexpr char SEP = '\\';
#else
constexpr char SEP = '/';
#endif

std::mutex lock; /* `store`. */
/* Whether any script region began in this process: before one, every Python value is Angelo's own (a startup
 * migration, a carry-over from an earlier version), so nothing a script changed can name a target. */
std::atomic<bool> any_region{false};
std::string store; /* 2.0.0 betas kept vouches there: never written, removed when named. */

/** Windows: the one spelling C names and judges a path by (winpath.hh, `windows_plain`): `\\?\X` only when it is the
 * very file `X` names, never a device, NT, root-relative or drive-relative path (empty: refused). Elsewhere:
 * unchanged. */
std::string win_plain(const std::string &path)
{
  return windows_plain(path);
}

std::string normalized(const std::string &path)
{
  char text[FILE_MAX];
  STRNCPY(text, path.c_str());
  if (!BLI_path_is_abs_from_cwd(text)) {
    BLI_path_abs_from_cwd(text, sizeof(text));
  }
  BLI_path_normalize_native(text);
  std::string key = text;
#ifdef _WIN32
  for (char &c : key) {
    c = (c == '/') ? '\\' : char(tolower((unsigned char)c));
  }
#endif
  while (key.size() > 1 && key.back() == SEP && !(key.size() == 3 && key[1] == ':')) {
    key.pop_back();
  }
  return key;
}

#ifndef _WIN32
/** `plain` with its longest existing ancestor resolved (links followed) and the rest appended as named. */
std::string resolved_prefix(const std::string &plain)
{
  std::string head = plain, tail;
  for (int hops = 0; hops < 256; hops++) {
    char real[PATH_MAX];
    if (realpath(head.c_str(), real) != nullptr) {
      std::string key = real;
      if (!tail.empty()) {
        key += (key == "/" ? "" : "/") + tail;
      }
      return key;
    }
    const size_t slash = head.rfind('/');
    if (slash == std::string::npos || head == "/") {
      return "";
    }
    const std::string part = head.substr(slash + 1);
    tail = tail.empty() ? part : part + "/" + tail;
    head = slash == 0 ? "/" : head.substr(0, slash);
  }
  return "";
}
#endif

/** `path` with its folders resolved (links followed; a part not made yet kept as named) and its last part kept as
 * named, so a folder vouched for before it exists matches what is later written in it. Windows compares the
 * normalized path (its links: a residual the documentation lists). */
std::string key_of(const std::string &path, bool folder)
{
  const std::string plain = normalized(path);
#ifdef _WIN32
  (void)folder;
  /* As the file system spells it, every junction and link above resolved (winpath.cc): a spelling is never trusted
   * to be the place (the review of piece 10, ruling 4). Empty: it can't be answered, and the caller refuses. */
  std::string real = final_path(plain);
  for (char &c : real) {
    c = (c == '/') ? '\\' : char(tolower((unsigned char)c));
  }
  while (real.size() > 3 && real.back() == '\\') {
    real.pop_back();
  }
  return real;
#else
  if (folder) {
    return resolved_prefix(plain);
  }
  const size_t slash = plain.rfind('/');
  if (slash == std::string::npos) {
    return "";
  }
  const std::string parent = resolved_prefix(slash == 0 ? "/" : plain.substr(0, slash));
  if (parent.empty()) {
    return "";
  }
  return parent + (parent == "/" ? "" : "/") + plain.substr(slash + 1);
#endif
}

bool last_part_is_link(const std::string &path)
{
  return last_component_links(path, -1); /* A symbolic link; on Windows any name surrogate (a junction). */
}

bool inside(const std::string &key, const std::string &folder)
{
  return key.size() > folder.size() && key.compare(0, folder.size(), folder) == 0 &&
         (key[folder.size()] == SEP || folder.back() == SEP);
}

bool text_argument(PyObject *arg, std::string &out)
{
  PyObject *bytes = nullptr;
  if (!PyUnicode_FSConverter(arg, &bytes)) {
    PyErr_Clear();
    return false;
  }
  out = PyBytes_AS_STRING(bytes);
  Py_DECREF(bytes);
  return !out.empty() && out.find('\0') == std::string::npos;
}

}  // namespace

/** Whether the resolved `key` is, or lies in, the open document's folder (Blender's own path for the open file). */
static bool in_document_folder(const std::string &key)
{
  const char *document = BKE_main_blendfile_path_from_global();
  if (document == nullptr || document[0] == '\0') {
    return false;
  }
  const std::string folder = key_of(document, false);
  const size_t cut = folder.find_last_of(SEP);
  if (cut == std::string::npos) {
    return false;
  }
  const std::string parent = cut == 0 ? std::string(1, SEP) : folder.substr(0, cut);
  return key == parent || inside(key, parent);
}

/** The tag a typed path is kept under: the property `identifier` of the data at `data` (the interface's edit and
 * Python's set name it the same way, and so does a writer that reads that property), keyed by its data-block's
 * session id: a data-block freed and another made at the same address never inherits what was typed in the first
 * (the re-read of 9dcfb883ebb, NIT). Data in no data-block: by its address. */
std::string typed_tag(const ID *owner, const void *data, const char *identifier)
{
  char tag[64 + 128];
  if (owner != nullptr) {
    SNPRINTF(tag, "id%u:%p/%s", owner->session_uid, data, identifier ? identifier : "");
  }
  else {
    SNPRINTF(tag, "%p/%s", data, identifier ? identifier : "");
  }
  return tag;
}

/** Windows: whether `path` names an NTFS stream (`name:stream`, a colon past the drive's): another file's data hidden
 * at a name that looks like one the person gave (the re-review of piece 10, NIT). Elsewhere a colon is a name's. */
static bool names_a_stream(const std::string &path)
{
#ifdef _WIN32
  const size_t from = (path.size() > 1 && path[1] == ':') ? 2 : 0;
  return path.find(':', from) != std::string::npos;
#else
  (void)path;
  return false;
#endif
}

/** `may_write`, for a writer that reads the property `tag` names: also a path the person typed there. */
bool may_write_for(const std::string &path, const std::string *tag);

/** Whether the person's pick in a file browser (for that operator's run) counts for this caller: never for agent
 * code (a handler or timer a script left, running inside the person's operator), an agent's modal, or a writer whose
 * job began inside a script region (the re-read of 70600d6438b, MUST-1). */
static bool picks_count()
{
  if (confined_now() || WM_angelo_in_agent_modal()) {
    return false;
  }
  const angelo_writers::Job *job = angelo_writers::current_job();
  return job == nullptr || (job->begun && !job->inside);
}

bool may_write(const std::string &path)
{
  return may_write_for(path, nullptr);
}

bool may_write_for(const std::string &spelled, const std::string *tag)
{
  const std::string path = win_plain(spelled);
  if (folders().private_roots.size() == 0 || !any_region.load(std::memory_order_relaxed)) {
    return true; /* No confinement in this process (a plain child), or no script ran yet: nothing changed state. */
  }
  if (path.empty() || last_part_is_link(path)) {
    return false;
  }
  const std::string key = key_of(path, false);
  if (key.empty()) {
    return false;
  }
  {
    std::lock_guard<std::mutex> guard(lock);
    if (!store.empty() && (key == key_of(store, false) || key == key_of(store + ".new", false))) {
      return false;
    }
  }
  const vouches::Facts now = vouches::facts_of(path);
  const bool sealed = folders().judge(path, true) == Refusal::Private; /* No script writes there at all. */
  if (!sealed && (reparse_in_any_component(path) || names_a_stream(path))) {
    return false; /* Windows: a junction or link on the way leads elsewhere, or a stream hides behind a name. */
  }
  if (now.exists && !now.directory && now.links > 1 && !sealed) {
    /* Another name for this file exists: a script could have linked a file of the person's here. Only the very
     * file Angelo made or was given (by identity) may be written. */
    return owned_exactly(path) || vouches::identity_named(now);
  }
  if (sealed || folders().private_roots.deepest(key).has_value()) {
    return true; /* Inside Angelo's own folders (a workspace scripts use in them included): never the person's. */
  }
  if (owned_or_inside_owned(key)) {
    return true;
  }
  return vouches::matches(key, now, picks_count()) || (tag != nullptr && vouches::matches_typed(key, *tag));
}

/* Shared with what makes and judges beside a named place (beside.cc), by names no file's own helper uses. */
std::string write_plain(const std::string &path)
{
  return win_plain(path);
}

std::string write_key(const std::string &path, const bool folder)
{
  const std::string plain = win_plain(path);
  return plain.empty() ? std::string() : key_of(plain, folder);
}

bool write_link_at(const std::string &path)
{
  return last_part_is_link(path);
}

bool write_inside(const std::string &key, const std::string &folder)
{
  return inside(key, folder);
}

bool write_confinement_active()
{
  return folders().private_roots.size() != 0 && any_region.load(std::memory_order_relaxed);
}

bool write_in_document_folder(const std::string &key)
{
  return in_document_folder(key);
}

void writable_region_began()
{
  any_region.store(true, std::memory_order_relaxed);
}

/** Whether a folder is too wide to name whole: the file system's root or a top-level folder (`/Users`,
 * `C:\\Users`), or the person's home folder or one above it. */
bool too_wide(const std::string &key)
{
  size_t parts = 0;
  for (size_t i = 0; i < key.size(); i++) {
    if (key[i] == SEP && i + 1 < key.size()) {
      parts++;
    }
  }
#ifdef _WIN32
  const char *home_env = getenv("USERPROFILE");
#else
  const char *home_env = getenv("HOME");
#endif
  if (parts <= 1) { /* "/" or "/Users"; "c:\\" or "c:\\users". */
    return true;
  }
  if (home_env == nullptr || home_env[0] == '\0') {
    return false;
  }
  const std::string home = key_of(home_env, true);
  return !home.empty() && (key == home || inside(home, key));
}

/** Whether `path` names a parent folder (`..`) anywhere: one that came from Python state (a module value changed in
 * place, p10c-3-library) would otherwise be normalized onto a folder of the person's. */
static bool names_a_parent(const std::string &path)
{
  size_t start = 0;
  while (start <= path.size()) {
    size_t end = path.find_first_of("/\\", start);
    if (end == std::string::npos) {
      end = path.size();
    }
    if (path.compare(start, end - start, "..") == 0) {
      return true;
    }
    start = end + 1;
  }
  return false;
}


/** A path the person named: `folder` only for one call (`persist` false) or a pick, never for the session. */
static bool name(const std::string &spelled, bool folder, bool recursive, vouches::Scope scope, const std::string &tag)
{
  const std::string path = win_plain(spelled);
  if (path.empty()) {
    return false;
  }
  if (names_a_parent(path) || names_a_stream(path)) {
    return false;
  }
  const std::string key = key_of(path, folder);
  if (key.empty() || last_part_is_link(path) || reparse_in_any_component(path) || (folder && too_wide(key))) {
    return false;
  }
  return vouches::add(key, vouches::facts_of(path), folder, recursive, scope, tag);
}

bool vouch(const std::string &path, bool folder, bool persist)
{
  if (folder && persist) {
    return false; /* A folder is never named for the session (the review of piece 10, ruling 1). */
  }
  return name(path, folder, true, persist ? vouches::Scope::Session : vouches::Scope::Call, "");
}

/** Whether Angelo's own code may start the program `path` names, a path from Python state: never one a script wrote,
 * moved or linked (C's change record: a program planted in a folder on PATH included); else one inside an installed
 * folder, a file the person named, or one in a folder on PATH (the environment, which no script changes). Before
 * any region, and without confinement, any. */
bool may_run(const std::string &spelled)
{
  const std::string path = win_plain(spelled);
  if (folders().private_roots.size() == 0 || !any_region.load(std::memory_order_relaxed)) {
    return true;
  }
  if (path.empty() || names_a_stream(path)) {
    return false; /* A stream hides another file's data behind a program's name (the re-read of piece 10, NIT). */
  }
  std::string resolved = normalized(path);
#ifndef _WIN32
  char real[PATH_MAX];
  if (realpath(resolved.c_str(), real) != nullptr) {
    resolved = real;
  }
#endif
  if (folders().agent_made(path) || folders().agent_made(resolved)) {
    return false;
  }
  if (folders().trusted(resolved)) {
    return true;
  }
  std::string self = normalized(BKE_appdir_program_path()); /* This app's own binary (its children). */
#ifndef _WIN32
  if (realpath(self.c_str(), real) != nullptr) {
    self = real;
  }
#endif
  if (BLI_path_cmp(self.c_str(), resolved.c_str()) == 0) {
    return true;
  }
  const std::string key = key_of(resolved, false);
  if (vouches::program_named(key, vouches::facts_of(resolved))) {
    return true;
  }
  const char *search = getenv("PATH");
  if (search == nullptr) {
    return false;
  }
#ifdef _WIN32
  const char list_sep = ';';
#else
  const char list_sep = ':';
#endif
  const size_t cut = key.find_last_of(SEP);
  const std::string parent = cut == std::string::npos ? std::string() : key.substr(0, cut);
  std::string folder;
  for (const char *c = search;; c++) {
    if (*c == list_sep || *c == '\0') {
      if (!folder.empty() && !parent.empty() && key_of(folder, true) == parent) {
        return true;
      }
      folder.clear();
      if (*c == '\0') {
        break;
      }
    }
    else {
      folder += *c;
    }
  }
  return false;
}

static PyObject *guard_may_run(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(may_run(path));
}

static PyObject *guard_may_write(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(may_write(path));
}

/** `script_made(path)`: whether a confined script wrote, moved or linked the path (C's change record). */
static PyObject *guard_script_made(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_TRUE; /* Unreadable: fail closed. */
  }
  return PyBool_FromLong(folders().agent_made(path));
}

static bool angelo_calling()
{
  return !confined_now() && !frames_confined();
}

static PyObject *guard_vouch_path(PyObject * /*self*/, PyObject *args)
{
  PyObject *arg;
  int folder = 0, persist = 1;
  if (!PyArg_ParseTuple(args, "O|pp:vouch_path", &arg, &folder, &persist)) {
    return nullptr;
  }
  if (!angelo_calling()) {
    refuse(GUARD, "vouch_path"); /* What Angelo may write is never a script's to widen. */
    return nullptr;
  }
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(vouch(path, folder != 0, persist != 0));
}

static PyObject *guard_unvouch_path(PyObject * /*self*/, PyObject *args)
{
  PyObject *arg;
  int transient_only = 0;
  if (!PyArg_ParseTuple(args, "O|p:unvouch_path", &arg, &transient_only)) {
    return nullptr;
  }
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_NONE;
  }
  for (const bool folder : {false, true}) { /* Narrowing: anyone may. */
    vouches::forget(key_of(path, folder), folder, transient_only != 0);
  }
  Py_RETURN_NONE;
}

/** Whether a script made `plain` or any folder above it (C's change record holds the links it made too). */
static bool script_made_on_the_way(const std::string &plain)
{
  std::string current = plain;
  while (!current.empty()) {
    if (folders().agent_made(current)) {
      return true;
    }
    const size_t cut = current.find_last_of(SEP);
    if (cut == std::string::npos || cut == 0 || (cut == 2 && current[1] == ':')) {
      break;
    }
    current.resize(cut);
  }
  return false;
}

bool write_script_made_on_the_way(const std::string &plain)
{
  return script_made_on_the_way(plain);
}

/** `adopt_dir(path)`: record an existing folder Angelo's own tools use (an engine project's working folder, a
 * document's downloads folder), made in an earlier session, as Angelo's, by identity: a real folder (no link at its
 * name), nothing on the way made by a script, never too wide, never one scripts may not write. */
static PyObject *guard_adopt_dir(PyObject * /*self*/, PyObject *arg)
{
  if (!angelo_calling()) {
    refuse(GUARD, "adopt_dir");
    return nullptr;
  }
  std::string path;
  if (!text_argument(arg, path) || names_a_parent(path)) {
    Py_RETURN_FALSE;
  }
  if (owned_exactly(path)) {
    Py_RETURN_TRUE; /* Already Angelo's (made, or adopted earlier in this process). */
  }
  const std::string plain = normalized(path), key = key_of(path, true);
  const vouches::Facts facts = vouches::facts_of(path);
  if (key.empty() || !facts.directory || facts.link || too_wide(key) || script_made_on_the_way(plain) ||
      script_made_on_the_way(key) || born_since_first_region(path) || folders().judge(path, true) != Refusal::None)
  {
    Py_RETURN_FALSE; /* Not a real folder; one a script made (or made a way to), or could have since a script ran;
                      * or one scripts may not write. */
  }
  return PyBool_FromLong(record_owned(path));
}

/** `register_writable(path)`: a folder of Blender's own or the environment's that Angelo writes into, named before
 * any script region began in the process (refused after: then a script could have chosen it). */
static PyObject *guard_register_writable(PyObject * /*self*/, PyObject *arg)
{
  if (!angelo_calling()) {
    refuse(GUARD, "register_writable");
    return nullptr;
  }
  std::string path;
  if (any_region.load(std::memory_order_relaxed) || !text_argument(arg, path)) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(name(path, true, true, vouches::Scope::Root, ""));
}

/** `document_folder(path)`: whether the path is, or lies in, the open document's folder (the file tools' own
 * folder, anchored by their descriptors; never a folder other writers may use). */
static PyObject *guard_document_folder(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_FALSE;
  }
  const std::string key = key_of(path, true);
  return PyBool_FromLong(!key.empty() && !last_part_is_link(path) && in_document_folder(key));
}

/** `create_beside(named, name)`: a new file `name` (one name, made exclusively, never an existing one) in the folder of
 * `named`, a file Angelo may write (`may_write`), recorded as Angelo's: a temporary file or an output Angelo then
 * writes and renames onto `named`, in a folder that is not otherwise writable. Its path, or None. */
static PyObject *guard_create_beside(PyObject * /*self*/, PyObject *args)
{
  PyObject *named_arg, *name_arg;
  if (!PyArg_ParseTuple(args, "OO:create_beside", &named_arg, &name_arg)) {
    return nullptr;
  }
  if (!angelo_calling()) {
    refuse(GUARD, "create_beside");
    return nullptr;
  }
  std::string named, name;
  if (!text_argument(named_arg, named) || !text_argument(name_arg, name) ||
      name.find_first_of("/\\") != std::string::npos || name == "." || name == ".." || !may_write(named))
  {
    Py_RETURN_NONE;
  }
  const std::string key = key_of(named, false);
  const size_t cut = key.find_last_of(SEP);
  if (cut == std::string::npos) {
    Py_RETURN_NONE;
  }
  const std::string path = key.substr(0, cut == 0 ? 1 : cut) + (cut == 0 ? "" : std::string(1, SEP)) + name;
#ifdef _WIN32
  FILE *file = BLI_fopen(path.c_str(), "wbx"); /* Exclusive: never an existing file (nor a link put there). */
  if (file == nullptr) {
    Py_RETURN_NONE;
  }
  fclose(file);
#else
  const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0) {
    Py_RETURN_NONE;
  }
  close(fd);
#endif
  if (!record_owned(path)) {
    BLI_delete(path.c_str(), false, false);
    Py_RETURN_NONE;
  }
  return PyUnicode_DecodeFSDefault(path.c_str());
}

static PyObject *guard_writable_store(PyObject * /*self*/, PyObject *arg)
{
  if (!angelo_calling()) {
    refuse(GUARD, "writable_store");
    return nullptr;
  }
  std::string path;
  if (!text_argument(arg, path) || folders().judge(path, true) != Refusal::Private) {
    Py_RETURN_FALSE; /* Only a file in Angelo's private folders, where no script writes. */
  }
  std::lock_guard<std::mutex> guard(lock);
  if (!store.empty()) {
    return PyBool_FromLong(store == path);
  }
  store = path;
  BLI_delete(path.c_str(), false, false); /* What a 2.0.0 beta kept there: nothing is kept past the process now. */
  Py_RETURN_TRUE;
}

PyMethodDef writable_methods[] = {
    {"may_write", guard_may_write, METH_O,
     "may_write(path) -> bool: whether Angelo's own code may write, replace or remove the path (private, made by "
     "Angelo, or named by the person), whatever Python state named it."},
    {"vouch_path", (PyCFunction)guard_vouch_path, METH_VARARGS,
     "vouch_path(path, folder=False, persist=True) -> bool: the person, or a program they connected, gave Angelo "
     "this file to write, for this process (refused while a script runs); persist=False: for one call, and only "
     "then a folder."},
    {"may_run", guard_may_run, METH_O,
     "may_run(path) -> bool: whether Angelo's own code may start the program (installed, named, or on PATH; never "
     "one a script made), whatever Python state named it."},
    {"script_made", guard_script_made, METH_O,
     "script_made(path) -> bool: whether a confined script wrote, moved or linked the path."},
    {"unvouch_path", (PyCFunction)guard_unvouch_path, METH_VARARGS,
     "unvouch_path(path, transient_only=False): forget a named path (narrowing); transient_only: one call's only."},
    {"script_set", guard_script_set, METH_VARARGS,
     "script_set(owner_pointer, identifier) -> bool: whether a confined script set that string property of data in "
     "that data-block (its as_pointer()), or made the data-block."},
    {"script_originated", guard_script_originated, METH_O,
     "script_originated(owner_pointer) -> bool: whether a confined script made, copied, read, appended or linked it."},
    {"output_allowed_for", guard_output_allowed_for, METH_VARARGS,
     "output_allowed_for(path, owner_pointer, data_pointer, identifier) -> bool: what a writer that read that "
     "property asks when its data is the script's (a new file below an accepted folder is made and recorded here)."},
    {"folder_allowed_for", guard_folder_allowed_for, METH_VARARGS,
     "folder_allowed_for(folder, owner_pointer, data_pointer, identifier) -> bool: what a writer that writes into a "
     "folder it read from that property asks (a new folder below an accepted one is made and recorded here)."},
    {"make_dir_beside", guard_make_dir_beside, METH_VARARGS,
     "make_dir_beside(anchor, name, mode=0o700) -> str | None: the folder name (fixed parts) below an anchor C "
     "accepts, made by C following no link, or an existing real one no script made; recorded as Angelo's."},
    {"adopt_dir", guard_adopt_dir, METH_O,
     "adopt_dir(path) -> bool: record an existing real folder (no link on the way, not a script's) as Angelo's."},
    {"register_writable", guard_register_writable, METH_O,
     "register_writable(path) -> bool: a fixed folder Angelo writes into, named before any script region."},
    {"document_folder", guard_document_folder, METH_O,
     "document_folder(path) -> bool: whether the path is in the open document's folder (the file tools')."},
    {"create_beside", guard_create_beside, METH_VARARGS,
     "create_beside(named, name) -> str | None: a new file beside a file Angelo may write, made exclusively and "
     "recorded as Angelo's (a temporary to rename onto it)."},
    {"writable_store", guard_writable_store, METH_O,
     "writable_store(path) -> bool: the private file earlier builds kept vouches in (removed; never written)."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace blender::angelo_guard

namespace blender {

void BPY_angelo_guard_note_picked(const char *path, bool folder)
{
  /* The window manager calls this for the person's confirm in a file browser an Angelo operator opened, and
   * `BPY_angelo_guard_picks_done` when that operator's execute returned. A script that drove the confirm itself
   * (`file.execute` while it runs) names nothing. */
  if (path == nullptr || path[0] == '\0' || angelo_guard::confined_now()) {
    return;
  }
  angelo_guard::name(path, folder, true, angelo_guard::vouches::Scope::Pick, "");
}

void BPY_angelo_guard_picks_done()
{
  angelo_guard::vouches::forget_scope(angelo_guard::vouches::Scope::Pick);
}

void BPY_angelo_guard_note_typed(
    const char *path, bool folder, const ID *owner, const void *data, const char *identifier)
{
  /* The person typed `path` into a file or folder property (the interface's own edit, never Python): that file, or
   * files directly in that folder, until anything else sets the property (`BPY_angelo_guard_forget_typed`). */
  const std::string tag = angelo_guard::typed_tag(owner, data, identifier);
  angelo_guard::vouches::forget_tag(tag); /* The value typed before is no longer the target, whatever this one is. */
  if (path == nullptr || path[0] == '\0' || angelo_guard::confined_now()) {
    return;
  }
  char absolute[FILE_MAX];
  STRNCPY(absolute, path);
  BLI_path_abs(absolute, BKE_main_blendfile_path_from_global());
  angelo_guard::name(absolute, folder, false, angelo_guard::vouches::Scope::Typed, tag);
}

void BPY_angelo_guard_forget_typed(const ID *owner, const void *data, const char *identifier)
{
  angelo_guard::vouches::forget_tag(angelo_guard::typed_tag(owner, data, identifier));
}

}  // namespace blender
