// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Full frames: how a whole parse of an asset updates the copies of it in the developer's scene, by node identity
// (README, "How updates keep your objects"). Nothing here touches the network or the DOM, so it is tested in Node.
//
// - A glTF *node* is known by GLTFLoader's `parser.associations` (its node index). What the loader made under a node
//   for it (the primitives of a multi-primitive mesh, a camera or light beside a mesh) is the node's *content*.
//   Anything else under a node is the developer's.
// - Matching: by `extras.angelo_uid` (Angelo's per-process node identity) first, using the chain of uids from the
//   asset root only to tell repeated uids apart (the same Blender object instanced under several parents), then by
//   the chain of names from the root for what is left (a first load, a saved file, an Angelo restart).
// - A matched node keeps its Object3D, and is changed only where Angelo's data differs (update.js): an unchanged node
//   keeps its geometry, material and textures and costs nothing on the GPU. Bones are never replaced.

import { describe, isDrawable, kindOf, nameOf } from "./source.js";
import { Update, contentObjects, developerChildren, emptyContent, materialValues, newJournal, rememberRest, sdkDigest,
  sdkMaterial, sdkMaterialValues, sdkParent } from "./update.js";

export { describe, developerChildren };

export class Copy {
  constructor(root) {
    this.root = root;
    this.nodes = new Map(); // Object3D -> {uid: string|null, content: {primitives: [], camera, light}}
    this.journal = newJournal(); // What the SDK changed since the asset was last as saved.
  }
}



function ownsMaterial(mesh, isLoaderMaterial) {
  const material = mesh.material;
  const all = Array.isArray(material) ? material : [material];
  if (all.length && all.every((item) => item && isLoaderMaterial(item))) {
    sdkMaterial.set(mesh, material);
    sdkMaterialValues.set(mesh, materialValues(material));
  }
}

/** A copy made from a loaded glTF (its own scene, or `root` when it is that scene). */
export function adoptGltf(gltf, root = gltf.scene) {
  const source = describe(gltf);
  const copy = new Copy(root);
  const associations = gltf.parser?.associations;
  const isLoaderMaterial = (material) => !associations || associations.has(material);
  for (const node of source.nodes) {
    copy.nodes.set(node.object, { uid: node.uid, content: node.content });
    sdkParent.set(node.object, node.object.parent);
    for (const item of [node.object, ...node.content.primitives]) {
      rememberRest(item);
      if (isDrawable(item)) ownsMaterial(item, isLoaderMaterial);
    }
  }
  return copy;
}

/**
 * What the file itself says each loader material is, for comparing Angelo's materials with (update.js): a page
 * often changes a loaded material in place before it calls track() (a colour variant), and that change must not
 * look like Angelo's. GLTFLoader's `loadMaterial` builds a fresh material from the file (sharing its textures).
 */
export async function rememberFileMaterials(gltf, copy) {
  const parser = gltf.parser;
  if (typeof parser?.loadMaterial !== "function" || !parser.associations) return;
  for (const [object, info] of copy.nodes) {
    for (const item of [object, ...info.content.primitives]) {
      if (!isDrawable(item) || Array.isArray(item.material) || sdkMaterial.get(item) !== item.material) continue;
      const index = parser.associations.get(item.material)?.materials;
      if (index === undefined) continue;
      try {
        // The loader's final step for this mesh (normal-map handedness, vertex colours, points/lines variants),
        // done on a stand-in so the result is what Angelo's frames will hold for an unchanged material.
        const probe = { geometry: item.geometry, material: await parser.loadMaterial(index), isPoints: item.isPoints,
          isLine: item.isLine, isLineSegments: item.isLineSegments, isLineLoop: item.isLineLoop };
        if (typeof parser.assignFinalMaterial === "function") parser.assignFinalMaterial(probe);
        sdkMaterialValues.set(item, probe.material);
      } catch {
        // Keep the copy made at track(): the comparison is then against the material as it was then.
      }
    }
  }
}

/**
 * A copy for `root`, a clone of `template`'s root (Object3D.clone or SkeletonUtils.clone): the two trees are
 * walked side by side, children by position and then by name and kind, so the developer's own children are never
 * taken for nodes.
 */
export function adoptClone(template, root) {
  const copy = new Copy(root);
  const walk = (from, to) => {
    const taken = new Set();
    from.children.forEach((child, index) => {
      const info = template.nodes.get(child);
      const isContent = !info && template.nodes.has(from)
        && contentObjects(template.nodes.get(from).content).includes(child);
      if (!info && !isContent) return; // The developer's, in the template.
      let match = to.children[index];
      if (!match || taken.has(match) || match.name !== child.name || kindOf(match) !== kindOf(child)) {
        match = to.children.find((item) => !taken.has(item) && item.name === child.name && kindOf(item) === kindOf(child));
      }
      if (!match) return;
      taken.add(match);
      rememberRest(match);
      if (info) {
        copy.nodes.set(match, { uid: info.uid, content: emptyContent(), from: child });
        sdkParent.set(match, to);
        walk(child, match);
      }
      if (isDrawable(child) && isDrawable(match) && sdkMaterial.get(child) === child.material
          && match.material === child.material) {
        sdkMaterial.set(match, match.material);
        sdkMaterialValues.set(match, sdkMaterialValues.get(child) ?? materialValues(match.material));
      }
    });
  };
  walk(template.root, root);
  // Content: the clone's children at the template content's places.
  for (const [object, info] of copy.nodes) {
    const content = template.nodes.get(info.from).content;
    const mapped = (item) => (item ? object.children.find((child) => child.name === item.name
      && kindOf(child) === kindOf(item) && !copy.nodes.has(child)) ?? null : null);
    const used = new Set();
    for (const primitive of content.primitives) {
      const found = object.children.find((child) => !used.has(child) && !copy.nodes.has(child)
        && child.name === primitive.name && kindOf(child) === kindOf(primitive));
      if (found) {
        used.add(found);
        info.content.primitives.push(found);
      }
    }
    info.content.camera = mapped(content.camera);
    info.content.light = mapped(content.light);
    delete info.from;
  }
  return copy;
}


// Chains: a node's place from the asset root, each step told apart from same-named (or same-uid) siblings.

function copyChains(copy) {
  const chains = new Map();
  const visit = (parent, prefixName, prefixUid) => {
    const seenName = new Map();
    const seenUid = new Map();
    for (const child of parent.children) {
      const info = copy.nodes.get(child);
      if (!info) continue;
      const name = nameOf(child);
      const n = seenName.get(name) ?? 0;
      seenName.set(name, n + 1);
      const uid = info.uid ?? "";
      const u = seenUid.get(uid) ?? 0;
      seenUid.set(uid, u + 1);
      const names = `${prefixName}\u0000${name}#${n}`;
      const uids = `${prefixUid}\u0000${uid}#${u}`;
      chains.set(child, { names, uids });
      visit(child, names, uids);
    }
  };
  visit(copy.root, "", "");
  // Nodes the developer moved out of the asset start their own chains where they are.
  for (const [object, info] of copy.nodes) {
    if (!chains.has(object)) {
      const names = `\u0001${nameOf(object)}#0`;
      const uids = `\u0001${info.uid ?? ""}#0`;
      chains.set(object, { names, uids });
    }
  }
  return chains;
}

function groupBy(items, key) {
  const groups = new Map();
  for (const item of items) {
    const value = key(item);
    if (value === null || value === undefined) continue;
    if (!groups.has(value)) groups.set(value, []);
    groups.get(value).push(item);
  }
  return groups;
}

/** Pair the source's nodes with the copy's: Map<source node, Object3D>. */
export function matchNodes(copy, source) {
  const chains = copyChains(copy);
  const pairs = new Map();
  const used = new Set();
  const pairAll = (sources, objects) => {
    const free = (list) => list.filter((item) => !pairs.has(item) && !used.has(item));
    for (const field of ["uids", "names"]) {
      const byChain = groupBy(free(objects), (object) => chains.get(object)[field]);
      for (const node of free(sources)) {
        const candidates = byChain.get(field === "uids" ? node.uidChain : node.nameChain);
        const object = candidates?.find((item) => !used.has(item));
        if (object) {
          pairs.set(node, object);
          used.add(object);
        }
      }
    }
    const rest = free(objects);
    for (const node of free(sources)) {
      if (!rest.length) break;
      const object = rest.shift();
      pairs.set(node, object);
      used.add(object);
    }
  };
  // 1. By uid: a lone uid on each side pairs whatever moved or was renamed; repeated uids pair by chain, then order.
  const objectsByUid = groupBy([...copy.nodes.keys()], (object) => copy.nodes.get(object).uid);
  for (const [uid, sources] of groupBy(source.nodes, (node) => node.uid)) {
    const objects = objectsByUid.get(uid);
    if (objects) pairAll(sources, objects);
  }
  // 2. By name chain for the rest; nothing pairs by position alone across different names.
  const restObjects = [...copy.nodes.keys()].filter((object) => !used.has(object));
  const byName = groupBy(restObjects, (object) => chains.get(object).names);
  for (const node of source.nodes) {
    if (pairs.has(node)) continue;
    const object = byName.get(node.nameChain)?.find((item) => !used.has(item));
    if (object) {
      pairs.set(node, object);
      used.add(object);
    }
  }
  // 3. Bones are never replaced (an AnimationMixer holds them), but a bone's key holds its name, so a renamed bone
  //    arrives with a new key and a new name. Pair what is left of the bones under the same (already paired) parent
  //    when exactly one is left on each side.
  const parentObject = (node) => (node.parent ? pairs.get(node.parent) : copy.root);
  const leftBones = groupBy([...copy.nodes.keys()].filter((object) => object.isBone && !used.has(object)),
    (object) => object.parent);
  const newBones = groupBy(source.nodes.filter((node) => node.object.isBone && !pairs.has(node)), parentObject);
  for (const [parent, nodes] of newBones) {
    const objects = leftBones.get(parent);
    if (nodes.length === 1 && objects?.length === 1) {
      pairs.set(nodes[0], objects[0]);
      used.add(objects[0]);
    }
  }
  // 4. A node the developer moved out of the asset has no chain to compare: pair it by its name alone, when that
  //    name is unique among what is left on both sides (Blender's object names are unique in a file).
  const leftObjects = groupBy([...copy.nodes.keys()].filter((object) => !used.has(object)), (object) => nameOf(object));
  const leftSources = groupBy(source.nodes.filter((node) => !pairs.has(node)), (node) => node.name);
  for (const [name, nodes] of leftSources) {
    const objects = leftObjects.get(name);
    if (nodes.length === 1 && objects?.length === 1 && chains.get(objects[0]).names.startsWith("\u0001")) {
      pairs.set(nodes[0], objects[0]);
      used.add(objects[0]);
    }
  }
  return pairs;
}

// Updating objects ----------------------------------------------------------------------------------------------

/**
 * Update `copy` to `source` (one whole parse, shared by every copy) by diff. `cache` is the page's TextureCache.
 * Returns the report: {renamed, removed, added, replaced, skeletons, changed: Set<Object3D>}.
 */
export function applySource(copy, source, cache) {
  const update = new Update(copy, copy.journal, cache);
  const pairs = matchNodes(copy, source);
  const kept = new Set(pairs.values());
  const finish = update.removeNodes([...copy.nodes.keys()].filter((object) => !kept.has(object)));
  const objectOf = new Map();
  const unchanged = new Set();
  for (const node of source.nodes) { // Parents before children.
    const parent = node.parent ? objectOf.get(node.parent) : copy.root;
    const object = pairs.get(node);
    if (object && node.digest && sdkDigest.get(object) === node.digest) unchanged.add(node);
    objectOf.set(node, object ? update.updateNode(object, node, parent) : update.addNode(node, parent));
  }
  finish();
  const changed = new Set(source.nodes.filter((node) => !unchanged.has(node)));
  skins(update, source, (bone) => objectOf.get(source.byObject.get(bone)), objectOf, changed);
  return update.report;
}

/** Bind each skinned mesh of the source to this copy's bones; `boneOf` maps a source bone to the copy's object. */
export function skins(update, source, boneOf, objectOf, only = null) {
  for (const node of source.nodes) {
    if (only && !only.has(node)) continue;
    const object = objectOf.get(node);
    const info = object && update.copy.nodes.get(object);
    if (!info) continue;
    const pairs = [[object, node.object], ...info.content.primitives.map((item, i) => [item, node.content.primitives[i]])];
    for (const [target, from] of pairs) {
      if (!target?.isSkinnedMesh || !from?.isSkinnedMesh || !from.skeleton) continue;
      const bones = from.skeleton.bones.map(boneOf);
      if (bones.some((bone) => !bone)) continue;
      update.skin(target, from, bones);
    }
  }
}

/** Forget every uid (Angelo restarted: its uids start over and mean other objects). */
export function forgetUids(copy) {
  for (const [object, info] of copy.nodes) if (info.uid !== null) copy.nodes.set(object, { ...info, uid: null });
}
