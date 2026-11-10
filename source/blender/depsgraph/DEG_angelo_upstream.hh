/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup depsgraph
 *
 * Angelo: the data-blocks an ID's evaluation reads, taken from the relations Blender has already
 * built for a view layer (doc/angelo/scene_intelligence.rst, "Upstream relations"). It is an
 * oracle the agent's revisions are checked against: callers refuse on every status but OK. It
 * only reads: it never builds, rebuilds, allocates, tags or evaluates a graph. Kept out of
 * DEG_depsgraph_query.hh, which upstream changes often, so a base move does not conflict here.
 */

#pragma once

#include <cstdint>

#include "BLI_function_ref.hh"
#include "BLI_sys_types.h"

namespace blender {

struct Depsgraph;
struct ID;
struct Main;
struct Scene;
struct ViewLayer;

enum class DEGUpstreamStatus : int8_t {
  /** Every component upstream of the ID was reported. */
  Ok = 0,
  /** The view layer has no dependency graph (nothing evaluated it yet, e.g. a headless file). */
  NoGraph = 1,
  /**
   * The graph's relations are tagged for rebuild or are being rebuilt inside a checkpoint
   * materialize scope, or its node for the ID was built for an earlier ID at the same address,
   * or a driver variable path upstream no longer names what it named when they were built
   * (`AngeloPathRead`: a write re-pointed data the path passes through without tagging them).
   */
  RelationsStale = 2,
  /**
   * The graph is evaluating (the call comes from inside its evaluation), or a job owned by the
   * scene runs or a window manager has its interface locked: bakes evaluate the view layer's own
   * graph on a job thread while they run.
   */
  Evaluating = 3,
  /** The ID has no node in the graph: this view layer does not evaluate it. */
  NotInGraph = 4,
  /** More than #DEG_UPSTREAM_MAX_OPERATIONS operations are upstream; the walk stopped. */
  TooLarge = 5,
  /** Called off the main thread. */
  NotMainThread = 6,
  /**
   * An operation upstream reads data its relations do not name: a Python driver expression that
   * is not a simple expression, or a driver variable whose target did not resolve when the
   * relations were built (#DEPSOP_FLAG_ANGELO_UNDECLARED_READS).
   */
  UndeclaredReads = 7,
};

/**
 * The most operations one walk visits: a bound on one call's main-thread time (the measured cost
 * per operation is in doc/angelo/scene_intelligence.rst). A walk that would visit more stops with
 * #DEGUpstreamStatus::TooLarge, so a caller refuses rather than trusting a partial set.
 */
inline constexpr int64_t DEG_UPSTREAM_MAX_OPERATIONS = int64_t(1) << 20;

/** One component of the evaluation's upstream. */
struct DEGUpstreamRecord {
  /**
   * The ID's original session UID as the graph node caches it (`ID.session_uid` in Python);
   * zero for a record that is not an ID (the time source).
   */
  uint session_uid;
  /** The ID's type code (`ID_Type`, as `GS()` returns it); zero for the time source. */
  int id_type;
  /**
   * The internal `deg::NodeType` value of the component (intern/node/deg_node.hh). A record of
   * type `COPY_ON_EVAL` means an operation reached waits for that other ID's evaluated copy (a
   * driver target, an IK target): Blender adds these for pointer readiness; they are reported one
   * hop deep, never followed. `TIMESOURCE` means something reached depends on the frame.
   */
  uint8_t node_type;
};

/**
 * Visit every distinct (ID, component type) whose operations the evaluation of `id` transitively
 * depends on in `depsgraph`, the root's own components included. Starts from all of the root's
 * components except `VISIBILITY` and `COPY_ON_EVAL`, and never enters an operation of those two
 * types: visibility relations order evaluation without carrying content (custom bone shapes are
 * linked that way), and copy relations carry pointers, not content. Every object reaches the
 * `HIERARCHY` of its collections, up through parent collections to the Scene's.
 *
 * A plain worklist over operations with a visited set; every inlink is examined, and one from a
 * node that is not an operation (the time source) is reported and never followed. Reads only the
 * graph's nodes and relations and the identity they cache: never dereferences an original or
 * evaluated ID, never writes the graph. Requires that nothing modifies the graph during the call.
 *
 * Returns Ok, NotInGraph (without calling `callback`), TooLarge once more than `max_operations`
 * operations are visited, or UndeclaredReads at the first such operation (after some calls).
 * Callers discard every record unless the result is Ok.
 */
DEGUpstreamStatus DEG_foreach_ancestor_component(
    const Depsgraph *depsgraph,
    const ID *id,
    FunctionRef<void(const DEGUpstreamRecord &)> callback,
    int64_t max_operations = DEG_UPSTREAM_MAX_OPERATIONS);

/**
 * `ViewLayer.angelo_upstream()`: #DEG_foreach_ancestor_component over the dependency graph that
 * already exists for `view_layer`, only while its relations are current. Never creates, builds,
 * rebuilds, tags, evaluates or activates a graph; a missing, stale or busy graph is a status.
 * Main-thread only. `id` must be an original ID; `bmain` is the Main that owns `scene`.
 */
DEGUpstreamStatus DEG_angelo_foreach_upstream(
    const Main *bmain,
    const Scene *scene,
    const ViewLayer *view_layer,
    const ID *id,
    FunctionRef<void(const DEGUpstreamRecord &)> callback);

}  // namespace blender
