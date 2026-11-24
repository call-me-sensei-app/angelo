/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The automounter's folders of maps of any host (bpy_angelo_guard_confine_automount.hh).
 */

#include "bpy_angelo_guard_confine_automount.hh"

#include <algorithm>
#include <sstream>

namespace blender::angelo_guard::automount {

namespace {

constexpr int MAX_INCLUDES = 8; /* Master maps that include each other stop here. */

std::vector<std::string> fields(const std::string &line)
{
  std::vector<std::string> out;
  std::istringstream stream(line.substr(0, line.find('#')));
  std::string field;
  while (stream >> field) {
    out.push_back(field);
  }
  return out;
}

std::string trimmed(std::string folder)
{
  while (folder.size() > 1 && folder.back() == '/') {
    folder.pop_back();
  }
  return folder;
}

std::string lower(std::string text)
{
  for (char &c : text) {
    c = (c >= 'A' && c <= 'Z') ? char(c - 'A' + 'a') : c;
  }
  return text;
}

/** The rest of `path` below `folder` when `path` is strictly inside it (component by component), else npos. */
size_t below(const std::string &path, const std::string &folder)
{
  if (path.size() > folder.size() + 1 && path.compare(0, folder.size(), folder) == 0 && path[folder.size()] == '/') {
    size_t at = folder.size();
    while (at < path.size() && path[at] == '/') {
      at++;
    }
    return at < path.size() ? at : std::string::npos;
  }
  return std::string::npos;
}

/** Whether a map entry's location takes its host from the key (`&:/export`, `://&/share`, `h&.lan:/x`). */
bool host_is_key(const std::string &location)
{
  std::string host;
  if (location.rfind("://", 0) == 0) {
    host = location.substr(3, location.find('/', 3) - 3); /* CIFS, `://host/share`. */
  }
  else if (location.empty() || location[0] == '/' || location[0] == ':') {
    return false; /* A local folder. */
  }
  else {
    host = location.substr(0, location.find(':'));
  }
  return host.find('&') != std::string::npos;
}

/** Whether a file map's text has a wildcard entry whose host is the key: every key is a host. */
bool wildcard_any_host(const std::string &text)
{
  std::istringstream lines(text);
  std::string line;
  while (std::getline(lines, line)) {
    const std::vector<std::string> parts = fields(line);
    if (parts.size() < 2 || parts[0] != "*") {
      continue;
    }
    for (size_t i = 1; i < parts.size(); i++) {
      if (parts[i][0] != '-' && host_is_key(parts[i])) {
        return true;
      }
    }
  }
  return false;
}

void master_points(const std::string &text, const Probe &probe, int depth, std::vector<std::string> &points)
{
  std::istringstream lines(text);
  std::string line;
  while (std::getline(lines, line)) {
    const std::vector<std::string> parts = fields(line);
    if (parts.empty()) {
      continue;
    }
    if (parts[0][0] == '+') {
      /* An include: `+dir:/folder` (its `*.autofs`), `+/file` or `+file:/file`. A name (`+auto_master`) is the
       * directory service's: nothing here can read it. */
      const std::string include = parts[0].substr(1);
      if (depth >= MAX_INCLUDES) {
        continue;
      }
      if (include.rfind("dir:", 0) == 0) {
        for (const std::string &file : probe.autofs_files(include.substr(4))) {
          master_points(probe.text(file), probe, depth + 1, points);
        }
      }
      else if (include.rfind("file:", 0) == 0 || (!include.empty() && include[0] == '/')) {
        master_points(probe.text(include[0] == '/' ? include : include.substr(5)), probe, depth + 1, points);
      }
      continue;
    }
    if (parts.size() < 2 || parts[0][0] != '/' || parts[0] == "/-") {
      continue; /* A comment or a direct map (`/-`): no folder of any host. */
    }
    if (any_host_map(parts[1], probe)) {
      add_point(points, parts[0]);
    }
  }
}

}  // namespace

void add_point(std::vector<std::string> &points, const std::string &point)
{
  const std::string folder = trimmed(point);
  if (!folder.empty() && folder[0] == '/' && folder != "/" &&
      std::find(points.begin(), points.end(), folder) == points.end())
  {
    points.push_back(folder);
  }
}

bool any_host_map(const std::string &map, const Probe &probe)
{
  if (map == "-hosts") {
    return true;
  }
  if (map.empty() || map[0] == '-') {
    return false; /* `-fstab`, `-static`, `-null`: the administrator's own list. */
  }
  std::string type, name = map;
  const size_t colon = map.find(':');
  if (colon != std::string::npos && map[0] != '/') {
    type = map.substr(0, colon); /* `type[,format]:map`: `program,sun:/x`, `file:/etc/auto.misc`, `ldap:...`. */
    type = type.substr(0, type.find(','));
    name = map.substr(colon + 1);
  }
  if (type == "program") {
    return true;
  }
  if ((!type.empty() && type != "file") || name.empty()) {
    return false; /* `yp:`, `ldap:`, `sss:`, ...: a map that names its servers (a program would say `program:`). */
  }
  const std::string path = name[0] == '/' ? name : "/etc/" + name;
  if (type.empty() && probe.is_program(path)) {
    return true; /* An executable map is a program map unless its type says `file:`. */
  }
  return wildcard_any_host(probe.text(path));
}

std::vector<std::string> any_host_points_from_master(const std::string &text, const Probe &probe)
{
  std::vector<std::string> points;
  master_points(text, probe, 0, points);
  return points;
}

std::vector<std::string> any_host_points_from_mounts(const std::vector<Mount> &mounts, const Probe &probe)
{
  std::vector<std::string> points;
  for (const Mount &mount : mounts) {
    if (mount.type != "autofs") {
      continue;
    }
    std::string map = mount.source;
    if (map.rfind("map ", 0) == 0) {
      map = map.substr(4); /* macOS: `map -hosts`, `map auto_home`. */
    }
    if (any_host_map(map, probe)) {
      add_point(points, mount.point);
    }
  }
  return points;
}

bool inside_any(const std::string &path, const std::vector<std::string> &points, const bool fold)
{
  const std::string text = fold ? lower(path) : path;
  for (const std::string &point : points) {
    if (below(text, fold ? lower(point) : point) != std::string::npos) {
      return true; /* A name below the folder: the automounter mounts a host for it. */
    }
  }
  return false;
}

Table table_from(const std::vector<std::string> &points,
                 const std::vector<std::pair<std::string, std::string>> &aliases,
                 const std::vector<std::string> &connected)
{
  Table table;
  for (const std::string &point : points) {
    add_point(table.points, point);
  }
  /* A folder inside another (`/net/nas/export`, the trigger `-hosts` sets below a host it reached) is part of the
   * outer folder's key: judged by that key, never as a folder of its own. */
  std::vector<std::string> outer;
  for (const std::string &point : table.points) {
    if (!inside_any(point, table.points, false)) {
      outer.push_back(point);
    }
  }
  table.points = outer;
  for (const std::string &folder : connected) {
    const std::string point = trimmed(folder);
    add_point(table.connected, point);
    for (const auto &[link, target] : aliases) {
      if (size_t rest = below(point, target); rest != std::string::npos) {
        add_point(table.connected, link + "/" + point.substr(rest));
      }
      if (size_t rest = below(point, link); rest != std::string::npos) {
        add_point(table.connected, target + "/" + point.substr(rest));
      }
    }
  }
  return table;
}

std::vector<std::string> connected_folders(const std::vector<std::string> *baseline,
                                           const std::vector<Mount> &live,
                                           const std::vector<std::string> &roots)
{
  std::vector<std::string> folders;
  if (baseline) {
    folders = *baseline; /* From the first region on: only what was mounted before it. */
  }
  else {
    for (const Mount &mount : live) {
      add_point(folders, mount.point);
    }
  }
  for (const std::string &root : roots) {
    add_point(folders, root);
  }
  return folders;
}

std::vector<Mount> mounts_from_proc(const std::string &text)
{
  /* `fsname dir type options freq passno`, a field's space, tab, newline or backslash written as `\040`, `\011`,
   * `\012`, `\134`. Read whole, line by line: no fixed buffer cuts a long line (an overlay's lowerdir list). */
  auto decoded = [](const std::string &field) {
    std::string out;
    for (size_t i = 0; i < field.size(); i++) {
      if (field[i] == '\\' && i + 3 < field.size() && field[i + 1] >= '0' &&
          field[i + 1] <= '3' && field[i + 2] >= '0' && field[i + 2] <= '7' && field[i + 3] >= '0' &&
          field[i + 3] <= '7')
      {
        out.push_back(char((field[i + 1] - '0') * 64 + (field[i + 2] - '0') * 8 + (field[i + 3] - '0')));
        i += 3;
      }
      else {
        out.push_back(field[i]);
      }
    }
    return out;
  };
  std::vector<Mount> mounts;
  std::istringstream lines(text);
  std::string line;
  while (std::getline(lines, line)) {
    std::istringstream stream(line);
    std::string source, point, type;
    if (stream >> source >> point >> type) {
      mounts.push_back({decoded(source), decoded(point), decoded(type)});
    }
  }
  return mounts;
}

bool names_an_unmounted_key(const std::string &path, const Table &table, const bool fold)
{
  const std::string text = fold ? lower(path) : path;
  for (const std::string &point : table.points) {
    const std::string folder = fold ? lower(point) : point;
    const size_t rest = below(text, folder);
    if (rest == std::string::npos) {
      continue;
    }
    /* `.` or `..` below the folder: what the name reaches is not what it says (`/net/nas/../other`). */
    for (size_t at = rest; at < text.size();) {
      const size_t end = std::min(text.find('/', at), text.size());
      const std::string part = text.substr(at, end - at);
      if (part == "." || part == "..") {
        return true;
      }
      at = end + 1;
    }
    const std::string key = folder + "/" + text.substr(rest, text.find('/', rest) - rest);
    const bool mounted = std::any_of(table.connected.begin(), table.connected.end(), [&](const std::string &m) {
      const std::string at = fold ? lower(m) : m;
      return at == key || (at.size() > key.size() && at.compare(0, key.size(), key) == 0 && at[key.size()] == '/');
    });
    if (!mounted) {
      return true; /* Not connected: even a stat connects to whatever host the key names. */
    }
  }
  return false;
}

}  // namespace blender::angelo_guard::automount
