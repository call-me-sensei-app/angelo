// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Delta frames (PROTOCOL.md §1.2): only the objects Angelo changed, as `content` nodes, hung under their real
// ancestors, which come as `parent` nodes (locators the page finds by key and never changes), plus the keys that
// are gone. Only content nodes are touched, and one whose digest the page already has is skipped. A delta that
// doesn't fit what the page shows (a parent key it doesn't have) is not applied at all: the caller asks for a full
// frame instead.

import { skins } from "./matcher.js";
import { describe } from "./source.js";
import { Update, sdkDigest } from "./update.js";

/** The delta's nodes, parents first, each with `parentUid`: its glb parent's key, or "" under the asset root. */
export function describeDelta(gltf) {
  const whole = describe(gltf);
  const nodes = whole.nodes.map((node) => ({ ...node, parentUid: node.parent ? node.parent.uid : "" }));
  const byObject = new Map(nodes.map((node) => [node.object, node]));
  for (const node of nodes) node.parent = node.parent ? byObject.get(node.parent.object) : null;
  return { nodes, byObject };
}

/**
 * Apply a delta to one copy: {report} when applied, or {problem} (and nothing changed) when the page must ask
 * for a full frame. `removedKeys` is the header's `removed`.
 */
export function applyDelta(copy, delta, removedKeys, cache) {
  const byUid = new Map();
  for (const [object, info] of copy.nodes) if (info.uid !== null && !byUid.has(info.uid)) byUid.set(info.uid, object);

  // Resolve first, so a delta is applied whole or not at all.
  for (const node of delta.nodes) {
    if (node.uid === null) return { problem: "a node without its key" };
    if (node.role === "parent" && !byUid.has(node.uid)) return { problem: "a parent the page doesn't have" };
  }
  const removed = removedKeys.map((key) => byUid.get(key)).filter(Boolean);
  const removing = new Set(removed);
  for (const node of delta.nodes) {
    if (removing.has(byUid.get(node.uid))) return { problem: "a node both sent and removed" };
  }

  const update = new Update(copy, copy.journal, cache);
  const finish = update.removeNodes(removed);
  const objectOf = new Map();
  const changed = new Set();
  for (const node of delta.nodes) { // Parents before children.
    const existing = byUid.get(node.uid);
    if (node.role === "parent") {
      objectOf.set(node, existing);
      continue;
    }
    const parent = node.parent ? objectOf.get(node.parent) : copy.root;
    if (existing && node.digest && sdkDigest.get(existing) === node.digest) {
      objectOf.set(node, existing);
      continue; // What the page has already: untouched.
    }
    changed.add(node);
    objectOf.set(node, existing ? update.updateNode(existing, node, parent) : update.addNode(node, parent));
  }
  finish();
  // Joints by key: bones (sent as parents when only a skin needs them) are the page's own objects, never replaced.
  skins(update, delta, (bone) => objectOf.get(delta.byObject.get(bone)) ?? null, objectOf, changed);
  return { report: update.report };
}
