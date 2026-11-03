/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The meshoptimizer library the application bundles, opened on first use (never at startup) for the
 * simplifier and vertex welding of `_bpy._angelo_reduce`. Blender links meshoptimizer only into the glTF
 * add-on's encode/decode bridge, which exports no simplifier, and calling the bundled shared library
 * through ctypes with a hand-written signature crashed the application once (an older `simplifySloppy`
 * signature). Here every entry point's type comes from the meshoptimizer header this file is compiled
 * against (`decltype`), so a call can only use that header's signature, and the library is checked
 * before any call:
 *
 * - At compile time, the header must be a 1.x release from 1.1 on. Upstream promises from 1.0 that later
 *   1.x releases keep every 1.0 function API, ABI and behaviour compatible (permissive simplification
 *   excepted, which is never used here), so a newer 1.x library keeps these signatures.
 * - At run time, the library loaded must export every entry point used here AND at least one of the
 *   functions first added in 1.1 (`meshopt_decodeMeshlet`, `meshopt_opacityMapMeasure`). The library
 *   carries no version number (its install name is 0.0.0), so those exports are the version evidence: a
 *   library older than 1.1, where `meshopt_simplifySloppy` may still have its pre-0.25 signature, is refused
 *   with a reason and nothing is called. Both are experimental upstream (1.1 added no stable function), so a
 *   later 1.x could rename them; that fails safe (the simplifier is refused with the reason) and the native
 *   suite's availability check catches it. A 2.x library cannot be told apart this way; the compile-time
 *   check covers the bundled one, and a system library of another major version is the residual risk.
 */

#pragma once

#include <string>

#ifdef WITH_MESHOPTIMIZER
/* Only the C declarations: the header's C++ template wrappers overload every name, so `decltype(&name)`
 * would not name one function. */
#  ifndef MESHOPTIMIZER_NO_WRAPPERS
#    define MESHOPTIMIZER_NO_WRAPPERS
#  endif
#  include <meshoptimizer.h>
/* The simplifier is written against meshoptimizer 1.x from 1.1 (see above). A build against another header (a
 * system library on Linux, or a bump of the bundled one) builds without it, and `status()` says why: the native
 * suite's availability check then fails, so a bump is reviewed rather than silently compiled against. */
#  if MESHOPTIMIZER_VERSION >= 1010 && MESHOPTIMIZER_VERSION < 2000
#    define ANGELO_MESHOPT_SIMPLIFY
#  endif
#endif

namespace blender::angelo_meshopt {

#ifdef ANGELO_MESHOPT_SIMPLIFY
/** The entry points used, each typed from the compiled header's own declaration. */
struct Api {
  decltype(&::meshopt_simplify) simplify;
  decltype(&::meshopt_simplifyWithAttributes) simplify_with_attributes;
  decltype(&::meshopt_simplifyWithUpdate) simplify_with_update;
  decltype(&::meshopt_simplifySloppy) simplify_sloppy;
  decltype(&::meshopt_simplifyScale) simplify_scale;
  decltype(&::meshopt_generateVertexRemapMulti) generate_vertex_remap_multi;
};
#else
struct Api;
#endif

struct Status {
  bool available = false;
  /** Why the library is not available, in English (model-visible text is never translated). */
  std::string reason;
  /** The file the loader opened, when one was found. */
  std::string library_path;
  /** `MESHOPTIMIZER_VERSION` of the compiled header (major * 1000 + minor * 10 + patch), 0 without one. */
  int header_version = 0;
};

/**
 * The loaded library's entry points, or nullptr when it cannot be used (`status().reason` says why).
 * Opens the library on the first call only; thread-safe.
 */
const Api *api();

/** Whether the library is usable and where it came from. Opens it on the first call, like `api()`. */
Status status();

/** Whether anything has asked for the library yet; never opens it (the proof that unused tools load nothing). */
bool attempted();

}  // namespace blender::angelo_meshopt
