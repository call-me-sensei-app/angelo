/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The link walk (bpy_angelo_guard_confine_links.hh): pure, on every system, so it is tested everywhere.
 */

#include "bpy_angelo_guard_confine_links.hh"

#include <algorithm>
#include <deque>
#include <optional>

namespace blender::angelo_guard::links {

namespace {

bool separator(const wchar_t c)
{
  return c == L'\\' || c == L'/';
}

bool ascii_alpha(const wchar_t c)
{
  return (c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z');
}

wchar_t ascii_lower(const wchar_t c)
{
  return (c >= L'A' && c <= L'Z') ? wchar_t(c - L'A' + L'a') : c;
}

/** Whether `text` starts with `prefix`, ASCII letters in any case, `\` matching either separator. */
bool starts_folded(const std::wstring &text, const wchar_t *prefix, const size_t at = 0)
{
  size_t i = 0;
  for (; prefix[i] != L'\0'; i++) {
    if (at + i >= text.size()) {
      return false;
    }
    const wchar_t a = text[at + i], b = prefix[i];
    if (b == L'\\' ? !separator(a) : ascii_lower(a) != ascii_lower(b)) {
      return false;
    }
  }
  return true;
}

std::vector<std::wstring> components(const std::wstring &text, const size_t from)
{
  std::vector<std::wstring> parts;
  std::wstring part;
  for (size_t i = from; i < text.size(); i++) {
    if (separator(text[i])) {
      if (!part.empty()) {
        parts.push_back(part);
      }
      part.clear();
    }
    else {
      part.push_back(text[i]);
    }
  }
  if (!part.empty()) {
    parts.push_back(part);
  }
  return parts;
}

/** After `\\?\` or `\??\` at `at`: a drive (`X:` then a separator or the end). */
bool drive_at(const std::wstring &text, const size_t at)
{
  return text.size() >= at + 2 && ascii_alpha(text[at]) && text[at + 1] == L':' &&
         (text.size() == at + 2 || separator(text[at + 2]));
}

/** After a prefix at `at`: `Volume{...}` then a separator or the end. Its length, or 0. */
size_t volume_at(const std::wstring &text, const size_t at)
{
  if (!starts_folded(text, L"volume{", at)) {
    return 0;
  }
  const size_t close = text.find(L'}', at);
  if (close == std::wstring::npos || (close + 1 < text.size() && !separator(text[close + 1]))) {
    return 0;
  }
  for (size_t i = at + 7; i < close; i++) {
    if (separator(text[i])) {
      return 0;
    }
  }
  return close + 1 - at;
}

/** `host\share` (each a component) from `at`, into a UNC root; false when either is missing or a dot name. */
bool unc_at(const std::wstring &text, const size_t at, Rooted &out)
{
  std::vector<std::wstring> parts = components(text, at);
  if (parts.size() < 2) {
    return false;
  }
  for (size_t i = 0; i < 2; i++) {
    if (parts[i] == L"." || parts[i] == L".." || parts[i] == L"?") {
      return false;
    }
  }
  out.type = Rooted::Type::Unc;
  out.share = L"\\\\" + parts[0] + L"\\" + parts[1];
  out.root = L"\\\\?\\UNC\\" + parts[0] + L"\\" + parts[1] + L"\\";
  out.rest.assign(parts.begin() + 2, parts.end());
  return true;
}

bool has_dot_names(const std::vector<std::wstring> &parts)
{
  for (const std::wstring &part : parts) {
    if (part == L"." || part == L"..") {
      return true;
    }
  }
  return false;
}

/** Whether a `..` in a link's target comes after a name (`a\..\b`), or, in a target with a root of its own, anywhere.
 * Windows reads such a `..` by the target's text, not by the folder the name before it really is (break-it-confine-
 * reparse-b7d499c MUST-A); the walk follows each name for real, so it could never agree. Only a relative target's
 * leading `..` (each the link's real folder's parent, the same in both readings) is walked. */
bool dot_dot_after_a_name(const std::vector<std::wstring> &parts, const bool rooted)
{
  bool named = rooted;
  for (const std::wstring &part : parts) {
    if (part == L"..") {
      if (named) {
        return true;
      }
    }
    else if (part != L".") {
      named = true;
    }
  }
  return false;
}

Rooted drive(const std::wstring &text, const size_t at)
{
  Rooted out;
  out.type = Rooted::Type::Drive;
  out.root = std::wstring(L"\\\\?\\") + wchar_t(text[at]) + L":\\";
  out.rest = components(text, at + 2);
  return out;
}

Rooted volume(const std::wstring &text, const size_t at, const size_t length)
{
  Rooted out;
  out.type = Rooted::Type::Volume;
  out.root = L"\\\\?\\" + text.substr(at, length) + L"\\";
  out.rest = components(text, at + length);
  return out;
}

uint32_t read_u16(const unsigned char *data)
{
  return uint32_t(data[0]) | (uint32_t(data[1]) << 8);
}

uint32_t read_u32(const unsigned char *data)
{
  return read_u16(data) | (read_u16(data + 2) << 16);
}

std::wstring join(const std::wstring &folder, const std::wstring &name)
{
  return (!folder.empty() && folder.back() == L'\\') ? folder + name : folder + L"\\" + name;
}

/** `folder` one level up, never above `root`. */
std::wstring parent(const std::wstring &folder, const std::wstring &root)
{
  if (folder.size() <= root.size()) {
    return root;
  }
  const size_t cut = folder.find_last_of(L'\\');
  return (cut == std::wstring::npos || cut + 1 <= root.size()) ? root : folder.substr(0, cut);
}

}  // namespace

bool reparse_tag_keeps_data(const uint32_t tag)
{
  constexpr uint32_t cloud = 0x9000001Au, dedup = 0x80000013u, wof = 0x80000017u;
  return (tag & 0xFFFF0FFFu) == cloud || tag == dedup || tag == wof;
}

Rooted split_input(const std::wstring &full)
{
  Rooted out;
  if (full.size() >= 2 && ascii_alpha(full[0]) && full[1] == L':' && (full.size() == 2 || separator(full[2]))) {
    return drive(full, 0);
  }
  const bool literal = starts_folded(full, L"\\\\?\\"), device = starts_folded(full, L"\\\\.\\");
  if (literal || device) {
    if (drive_at(full, 4)) {
      out = drive(full, 4);
    }
    else if (starts_folded(full, L"unc\\", 4)) {
      if (!unc_at(full, 8, out)) {
        return Rooted();
      }
    }
    else if (const size_t length = literal ? volume_at(full, 4) : 0) {
      out = volume(full, 4, length);
    }
    else {
      return Rooted(); /* Any other device: a pipe, GLOBALROOT, a physical drive, a volume by device name. */
    }
    if (literal && has_dot_names(out.rest)) {
      return Rooted(); /* Literal: `..` there is a name, not the parent folder. */
    }
    return out;
  }
  if (full.size() >= 3 && separator(full[0]) && separator(full[1]) && !separator(full[2])) {
    if (unc_at(full, 2, out)) {
      return out;
    }
  }
  return Rooted();
}

namespace {

Rooted split_target_text(const std::wstring &raw, const bool relative)
{
  Rooted out;
  if (relative) {
    if (raw.empty() || separator(raw[0]) || raw.find(L':') != std::wstring::npos) {
      return Rooted(); /* A relative target is a relative name: no root, no drive, no stream. */
    }
    out.type = Rooted::Type::Drive; /* Applied to the link's real folder: no root of its own. */
    out.rest = components(raw, 0);
    return out;
  }
  if (raw.size() < 4 || raw.compare(0, 4, L"\\??\\") != 0) {
    return Rooted(); /* `\Device\...`, `\GLOBAL??\`, `\DosDevices\`, a bare path: no rule admits it. */
  }
  if (drive_at(raw, 4)) {
    return drive(raw, 4);
  }
  if (starts_folded(raw, L"unc\\", 4)) {
    return unc_at(raw, 8, out) ? out : Rooted();
  }
  if (const size_t length = volume_at(raw, 4)) {
    return volume(raw, 4, length);
  }
  return Rooted(); /* `\??\Global\...`, `\??\GLOBALROOT\...`, `\??\pipe\...`, anything else. */
}

}  // namespace

Rooted split_target(const std::wstring &raw, const bool relative)
{
  Rooted out = split_target_text(raw, relative);
  if (out.type != Rooted::Type::Bad && dot_dot_after_a_name(out.rest, !relative)) {
    return Rooted(); /* Refused, failing closed (MUST-A). */
  }
  return out;
}

std::wstring without_stream(const std::wstring &component)
{
  const size_t colon = component.find(L':');
  return colon == std::wstring::npos ? component : component.substr(0, colon);
}

bool parse_reparse(const unsigned char *data, const size_t returned, std::wstring &target, bool &relative)
{
  constexpr uint32_t symlink_tag = 0xA000000Cu, mount_tag = 0xA0000003u;
  if (data == nullptr || returned < 8) {
    return false;
  }
  const uint32_t tag = read_u32(data);
  const size_t length = read_u16(data + 4);
  if (8 + length > returned) {
    return false;
  }
  const size_t header = tag == symlink_tag ? 20 : tag == mount_tag ? 16 : 0;
  if (header == 0 || header > 8 + length) {
    return false;
  }
  const size_t offset = read_u16(data + 8), size = read_u16(data + 10);
  if (offset % 2 != 0 || size % 2 != 0 || header + offset + size > 8 + length) {
    return false;
  }
  relative = tag == symlink_tag && (read_u32(data + 16) & 1u) != 0; /* SYMLINK_FLAG_RELATIVE */
  target.clear();
  for (size_t i = 0; i < size; i += 2) {
    target.push_back(wchar_t(read_u16(data + header + offset + i)));
  }
  return true;
}

namespace {

/** `at` with a drive root replaced by what it stands for (a `subst` drive, a chain of them), then the share check.
 * Unreadable when a drive can't be told or the chain is too long; Network for a share nobody connected. */
std::optional<Verdict> reroot(Rooted &at, Walker &walker)
{
  for (int hops = 0; at.type == Rooted::Type::Drive && !at.root.empty() && walker.drive_root; hops++) {
    if (hops >= 8) {
      return Verdict::Unreadable;
    }
    Rooted to = walker.drive_root(at.root);
    if (to.type == Rooted::Type::Bad) {
      return Verdict::Unreadable;
    }
    if (to.type == Rooted::Type::Drive && to.root == at.root && to.rest.empty()) {
      break; /* The drive is itself. */
    }
    to.rest.insert(to.rest.end(), at.rest.begin(), at.rest.end());
    at = std::move(to);
  }
  if (at.type == Rooted::Type::Unc && !walker.share_ok(at.share)) {
    return Verdict::Network;
  }
  return std::nullopt;
}

}  // namespace

Verdict walk(const std::wstring &full, const bool follow_last, Walker &walker)
{
  walker.probes = 0;
  Rooted start = split_input(full);
  if (start.type == Rooted::Type::Bad) {
    return Verdict::Unreadable;
  }
  if (const std::optional<Verdict> refused = reroot(start, walker)) {
    return *refused;
  }
  std::wstring root = start.root, current = start.root;
  std::deque<std::wstring> pending(start.rest.begin(), start.rest.end());
  int links = 0;
  while (!pending.empty()) {
    std::wstring name = pending.front();
    pending.pop_front();
    if (name == L".") {
      continue;
    }
    if (name == L"..") {
      if (current.size() <= root.size()) {
        /* Past its root (head-7 MUST-A'): Windows goes on above it, into the object namespace (`C:\..\UNC\host\s`,
         * `..\GLOBALROOT\Device\Mup\...`), where no rule follows it. Only a link's target brings a `..` here (a
         * full path's own are gone). */
        return Verdict::Unreadable;
      }
      current = parent(current, root); /* The real folder's parent: the prefix holds no link. */
      continue;
    }
    if (name.find(L':') != std::wstring::npos) {
      if (!pending.empty()) {
        return Verdict::Unreadable; /* A stream names a file, never a folder to go through. */
      }
      name = without_stream(name);
      if (name.empty()) {
        return Verdict::Unreadable;
      }
    }
    if (pending.empty() && !follow_last) {
      return Verdict::Safe; /* The open doesn't follow its last component. */
    }
    const std::wstring candidate = join(current, name);
    if (walker.plain_folders != nullptr && walker.plain_folders->count(candidate) != 0) {
      if (!walker.still_plain || walker.still_plain(candidate)) {
        current = candidate;
        continue;
      }
      walker.plain_folders->erase(candidate); /* Changed since it was found plain: probed again below. */
    }
    if (++walker.probes > walker.probe_budget) {
      return Verdict::Unreadable; /* A crafted chain or path: refused, never a hung thread. */
    }
    const Probe found = walker.probe(candidate);
    switch (found.kind) {
      case Probe::Kind::Missing:
        if (std::find(pending.begin(), pending.end(), L"..") == pending.end()) {
          return Verdict::Safe; /* Nothing below exists, so no link is followed. */
        }
        current = candidate; /* A `..` from a link's target may come back up past it, by its text: go on. */
        continue;
      case Probe::Kind::Refuse:
        return Verdict::Unreadable;
      case Probe::Kind::Plain:
        current = candidate;
        if (found.folder && walker.plain_folders != nullptr) {
          walker.plain_folders->insert(candidate);
        }
        continue;
      case Probe::Kind::Link:
        break;
    }
    if (++links > walker.link_limit) {
      return Verdict::Unreadable;
    }
    Rooted to = split_target(found.target, found.relative);
    if (to.type == Rooted::Type::Bad) {
      return Verdict::Unreadable;
    }
    if (const std::optional<Verdict> refused = reroot(to, walker)) {
      return *refused;
    }
    if (!to.root.empty()) {
      root = current = to.root; /* An absolute target: the walk goes on from its root. */
    }
    /* A relative target applies to the link's real folder (`current`, unchanged). */
    pending.insert(pending.begin(), to.rest.begin(), to.rest.end());
    /* Past a link, the rest is often plain (a project folder linked elsewhere, then many folders): one look says so
     * instead of a probe per component (Windows cost read: the link path was 2.3x the baseline, per link). */
    if (walker.rest_plain && walker.rest_plain(current, pending, follow_last)) {
      return Verdict::Safe;
    }
  }
  return Verdict::Safe;
}

}  // namespace blender::angelo_guard::links
