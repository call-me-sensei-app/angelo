/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * USD's primary asset resolver in Angelo: the default resolver, with every asset path judged by the agent-script
 * confinement while a confined script runs (usd_confined_resolver.hh, doc/angelo/script_confinement_pipeline.rst).
 *
 * Four entry points carry every asset USD reads or writes by path, whoever asks (`pxr`, Blender's importer and
 * exporter, Hydra's texture readers, usdz packaging):
 *
 * - `_Resolve` and `_ResolveForNewAsset`: a refused path does not resolve. Readers that open a resolved path
 *   themselves instead of through `OpenAsset` (usdMtlx reads a `.mtlx` on disk with MaterialX, the usdz writer maps
 *   a dependency's file) never see it;
 * - `_OpenAsset` and `_OpenAssetForWrite`: a resolved path built directly (`Ar.ResolvedPath(...)`) is judged too.
 *
 * A `.mtlx` file is read by MaterialX, which follows its own XIncludes outside Ar: resolving one judges every file
 * its XIncludes reach as well.
 *
 * The type is declared to USD by `plugInfo.json` (a resource plugin installed beside USD's own), and defined with
 * its factory here, so USD picks it as the only plugin primary resolver.
 */

#include "usd_confined_resolver.hh"

#include <pxr/base/tf/diagnostic.h>
#include <pxr/base/tf/stringUtils.h>
#include <pxr/usd/ar/asset.h>
#include <pxr/usd/ar/defaultResolver.h>
#include <pxr/usd/ar/defineResolver.h>
#include <pxr/usd/ar/packageUtils.h>
#include <pxr/usd/ar/resolver.h>
#include <pxr/usd/ar/writableAsset.h>

#include <atomic>
#include <cctype>
#include <cstring>
#include <fstream>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace blender::io::usd {

static std::atomic<ConfinedPathJudge> judge_function{nullptr};
static std::atomic<ConfinedUnavailable> unavailable_function{nullptr};

void confined_resolver_set_judge(ConfinedPathJudge judge, ConfinedUnavailable unavailable)
{
  judge_function.store(judge);
  unavailable_function.store(unavailable);
}

const char *confined_resolver_refusal()
{
  const ConfinedUnavailable unavailable = unavailable_function.load(std::memory_order_acquire);
  return unavailable == nullptr ? nullptr : unavailable();
}

/** The refusal for `path` (its outer file when it names a file inside a package), or null. */
static const char *refusal(const std::string &path, bool write)
{
  const ConfinedPathJudge judge = judge_function.load(std::memory_order_acquire);
  if (judge == nullptr || path.empty()) {
    return nullptr;
  }
  const std::string outer = pxr::ArIsPackageRelativePath(path) ?
                                pxr::ArSplitPackageRelativePathOuter(path).first :
                                path;
  return judge(outer.c_str(), write);
}

/* -------------------------------------------------------------------- */
/** \name MaterialX XIncludes
 *
 * MaterialX reads the files a document XIncludes with its own reader (an `href` relative to the including file's
 * folder, or absolute). The same files are found here and judged before USD resolves the document.
 * \{ */

static std::string decode_entities(std::string text)
{
  static const std::pair<const char *, const char *> entities[] = {
      {"&quot;", "\""}, {"&apos;", "'"}, {"&lt;", "<"}, {"&gt;", ">"}, {"&amp;", "&"}};
  for (const auto &[entity, plain] : entities) {
    for (size_t at = text.find(entity); at != std::string::npos; at = text.find(entity, at + 1)) {
      text.replace(at, strlen(entity), plain);
    }
  }
  return text;
}

/** The `href` of every `xi:include` element in an XML text. */
static std::vector<std::string> xinclude_hrefs(const std::string &text)
{
  std::vector<std::string> hrefs;
  for (size_t at = text.find("<xi:include"); at != std::string::npos; at = text.find("<xi:include", at + 1)) {
    const size_t end = text.find('>', at);
    const std::string element = text.substr(at, end == std::string::npos ? std::string::npos : end - at);
    const size_t key = element.find("href");
    if (key == std::string::npos) {
      continue;
    }
    size_t quote = element.find_first_of("\"'", key);
    if (quote == std::string::npos) {
      continue;
    }
    const size_t close = element.find(element[quote], quote + 1);
    if (close == std::string::npos) {
      continue;
    }
    hrefs.push_back(decode_entities(element.substr(quote + 1, close - quote - 1)));
  }
  return hrefs;
}

static bool is_absolute(const std::string &path)
{
  return !path.empty() && (path[0] == '/' || path[0] == '\\' ||
                           (path.size() > 1 && path[1] == ':' && std::isalpha((unsigned char)path[0])));
}

/** The refusal for any file the MaterialX document at `path` reaches through its XIncludes, or null. */
static const char *xinclude_refusal(const std::string &path, std::set<std::string> &seen, int level)
{
  if (level > 32 || !seen.insert(path).second) {
    return nullptr;
  }
  std::ifstream file(path, std::ios::binary);
  if (!file) {
    return nullptr; /* MaterialX can't read it either. */
  }
  std::stringstream text;
  text << file.rdbuf();
  const std::string folder = pxr::TfGetPathName(path);
  for (const std::string &href : xinclude_hrefs(text.str())) {
    const std::string target = is_absolute(href) ? href : pxr::TfStringCatPaths(folder, href);
    if (const char *refused = refusal(target, false)) {
      return refused;
    }
    if (const char *refused = xinclude_refusal(target, seen, level + 1)) {
      return refused;
    }
  }
  return nullptr;
}

static bool is_materialx(const std::string &path)
{
  const std::string lower = pxr::TfStringToLower(path);
  return lower.size() > 5 && lower.compare(lower.size() - 5, 5, ".mtlx") == 0;
}

/** \} */

}  // namespace blender::io::usd

PXR_NAMESPACE_OPEN_SCOPE

using blender::io::usd::refusal;

/** Every refusal is reported as a runtime error: the script sees it in the error USD raises, in the confinement's
 * own words. */
static void report(const char *reason, const std::string &path)
{
  TF_RUNTIME_ERROR("%s (%s)", reason, path.c_str());
}

class BlenderConfinedResolver final : public ArDefaultResolver {
 public:
  BlenderConfinedResolver() = default;

 protected:
  ArResolvedPath _Resolve(const std::string &assetPath) const override
  {
    ArResolvedPath resolved = ArDefaultResolver::_Resolve(assetPath);
    if (resolved.empty()) {
      return resolved;
    }
    const std::string &path = resolved.GetPathString();
    const char *refused = refusal(path, false);
    if (refused == nullptr && blender::io::usd::is_materialx(path) && !ArIsPackageRelativePath(path)) {
      std::set<std::string> seen;
      refused = blender::io::usd::xinclude_refusal(path, seen, 0);
    }
    if (refused != nullptr) {
      report(refused, path);
      return ArResolvedPath();
    }
    return resolved;
  }

  ArResolvedPath _ResolveForNewAsset(const std::string &assetPath) const override
  {
    ArResolvedPath resolved = ArDefaultResolver::_ResolveForNewAsset(assetPath);
    if (const char *refused = refusal(resolved.GetPathString(), true)) {
      report(refused, resolved.GetPathString());
      return ArResolvedPath();
    }
    return resolved;
  }

  std::shared_ptr<ArAsset> _OpenAsset(const ArResolvedPath &resolvedPath) const override
  {
    if (const char *refused = refusal(resolvedPath.GetPathString(), false)) {
      report(refused, resolvedPath.GetPathString());
      return nullptr;
    }
    return ArDefaultResolver::_OpenAsset(resolvedPath);
  }

  std::shared_ptr<ArWritableAsset> _OpenAssetForWrite(const ArResolvedPath &resolvedPath,
                                                      WriteMode writeMode) const override
  {
    if (const char *refused = refusal(resolvedPath.GetPathString(), true)) {
      report(refused, resolvedPath.GetPathString());
      return nullptr;
    }
    return ArDefaultResolver::_OpenAssetForWrite(resolvedPath, writeMode);
  }
};

AR_DEFINE_RESOLVER(BlenderConfinedResolver, ArDefaultResolver);

PXR_NAMESPACE_CLOSE_SCOPE

namespace blender::io::usd {

bool confined_resolver_active()
{
  /* Read only when USD makes its resolver: too late (ignored) when something made it already. */
  pxr::ArSetPreferredResolver("BlenderConfinedResolver");
  return dynamic_cast<pxr::BlenderConfinedResolver *>(&pxr::ArGetUnderlyingResolver()) != nullptr;
}

}  // namespace blender::io::usd
