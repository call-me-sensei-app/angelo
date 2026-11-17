// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Changing one object of a tracked copy to match a node of a new parse, used by full frames (matcher.js) and deltas
// (delta.js). Every change is a diff: a field is written only when Angelo's value differs from what Angelo sent last
// time, and the journal records the object before its first change. So an unchanged node is never touched, and the
// developer's own changes to it (an animation, a physics pose, a material) are left alone.

import { Skeleton } from "three";
import { Journal } from "./journal.js";
import { sameGeometry, sameMaterial } from "./same.js";
import { TextureCache } from "./textures.js";
import { isDrawable, kindOf, nameOf } from "./source.js";

// What the SDK last did to an object; WeakMaps so the developer's objects carry nothing extra.
export const sdkMaterial = new WeakMap(); // drawable -> the material the SDK (or the loader) put there
export const sdkParent = new WeakMap(); // node -> the parent the SDK put it under
export const sdkRest = new WeakMap(); // node -> the local transform Angelo last gave it: [position, quaternion, scale]
export const sdkDigest = new WeakMap(); // node -> Angelo's digest of what the page shows for it (PROTOCOL.md §1.2)
// drawable -> a copy of the material values Angelo last sent. Angelo's next material is compared with this, not with
// the live object: a developer who changes the colour of the material in place (a configurator's variant, a hover
// highlight) keeps it until Angelo itself changes that material.
export const sdkMaterialValues = new WeakMap();

/** A copy of a material (or array) to compare later; textures are shared, not copied. */
export function materialValues(material) {
  return Array.isArray(material) ? material.map((item) => item?.clone()) : material?.clone();
}
export const DEVELOPER_PLACED = Object.freeze({}); // An sdkParent no object has: the developer placed that node.

export const owner = {
  save: (object) => ({ material: sdkMaterial.get(object), parent: sdkParent.get(object), rest: sdkRest.get(object),
    digest: sdkDigest.get(object), values: sdkMaterialValues.get(object) }),
  load(object, saved) {
    for (const [map, value] of [[sdkMaterial, saved.material], [sdkParent, saved.parent], [sdkRest, saved.rest],
      [sdkDigest, saved.digest], [sdkMaterialValues, saved.values]]) {
      if (value === undefined) map.delete(object);
      else map.set(object, value);
    }
  },
};

export function newJournal() {
  return new Journal(owner);
}

/** Record `object`'s transform as the one Angelo gave it (a first load, or after an update). */
export function rememberRest(object) {
  sdkRest.set(object, [object.position.clone(), object.quaternion.clone(), object.scale.clone()]);
}

export function emptyContent() {
  return { primitives: [], camera: null, light: null };
}

export function contentObjects(content) {
  return [...content.primitives, ...(content.camera ? [content.camera] : []), ...(content.light ? [content.light] : [])];
}

/** The developer's children of an SDK node: neither nodes nor the node's content (nor a light's own target). */
export function developerChildren(copy, node) {
  const info = copy.nodes.get(node);
  const content = info ? contentObjects(info.content) : [];
  return node.children.filter((child) => !copy.nodes.has(child) && !content.includes(child) && child !== node.target);
}

export function compatible(object, from) {
  const kind = kindOf(object);
  if (kind !== kindOf(from)) return false;
  return kind !== "InstancedMesh" || object.count === from.count;
}

/** The update of one copy: {copy, journal, cache, report}. */
export class Update {
  constructor(copy, journal, cache) {
    this.copy = copy;
    this.journal = journal;
    this.cache = cache ?? new TextureCache();
    this.report = { renamed: [], removed: [], added: [], replaced: [], skeletons: [], changed: new Set() };
  }

  /** Record `object` before the SDK changes `aspects` of it (journal.js ASPECTS). */
  touch(object, ...aspects) {
    for (const aspect of aspects) this.journal.touch(object, this.copy, aspect);
    this.report.changed.add(object);
  }

  /** Angelo's local transform, written only when it differs from the last one Angelo gave. */
  transform(object, from) {
    const rest = sdkRest.get(object);
    if (rest && rest[0].equals(from.position) && rest[1].equals(from.quaternion) && rest[2].equals(from.scale)) return;
    this.touch(object, "transform");
    object.position.copy(from.position);
    object.quaternion.copy(from.quaternion);
    object.scale.copy(from.scale);
    if (from.pivot !== undefined) object.pivot = from.pivot?.clone?.() ?? from.pivot;
    if (!object.matrixAutoUpdate) object.updateMatrix();
    rememberRest(object);
  }

  rename(object, from) {
    const before = nameOf(object);
    const to = nameOf(from);
    if (before === to && object.name === from.name) return;
    this.touch(object, "name");
    object.name = from.name;
    if (from.userData.name !== undefined) object.userData.name = from.userData.name;
    if (before !== to) this.report.renamed.push({ object, from: before, to });
  }

  drawable(object, from) {
    if (!sameGeometry(object.geometry, from.geometry)) {
      this.touch(object, "geometry");
      object.geometry = from.geometry;
      object.updateMorphTargets?.();
    }
    const influences = from.morphTargetInfluences;
    if (influences && object.morphTargetInfluences?.length === influences.length
        && influences.some((value, i) => value !== object.morphTargetInfluences[i])) {
      this.touch(object, "leaf");
      object.morphTargetInfluences.splice(0, influences.length, ...influences);
    }
    // The developer's material stays: only one the SDK (or the loader) put there is replaced, and only when
    // Angelo's differs from it. Its textures are the page's own where the pictures are the same.
    if (sdkMaterial.get(object) === object.material) {
      this.cache.substitute(from.material);
      if (!sameMaterial(sdkMaterialValues.get(object) ?? object.material, from.material)) {
        this.touch(object, "material");
        object.material = from.material;
        sdkMaterial.set(object, from.material);
        sdkMaterialValues.set(object, materialValues(from.material));
      }
    }
    if (kindOf(object) === "InstancedMesh") {
      const same = object.instanceMatrix.array.every((value, i) => value === from.instanceMatrix.array[i]);
      if (!same) {
        this.touch(object, "leaf");
        object.instanceMatrix.array.set(from.instanceMatrix.array);
        object.instanceMatrix.needsUpdate = true;
      }
      if (object.instanceColor !== from.instanceColor
          && !(object.instanceColor && from.instanceColor
            && object.instanceColor.array.every((value, i) => value === from.instanceColor.array[i]))) {
        this.touch(object, "leaf");
        object.instanceColor = from.instanceColor;
      }
    }
  }

  leaf(object, from) {
    if (isDrawable(object)) return this.drawable(object, from);
    const fields = object.isLight ? ["intensity", "distance", "decay", "angle", "penumbra", "width", "height"]
      : object.isCamera ? ["fov", "near", "far", "zoom", "left", "right", "top", "bottom"] : [];
    const differs = fields.some((field) => field in from && object[field] !== from[field])
      || (object.color && from.color && !object.color.equals(from.color))
      || (object.target && from.target && !object.target.position.equals(from.target.position));
    if (!differs) return;
    this.touch(object, "leaf");
    for (const field of fields) if (field in from) object[field] = from[field];
    if (object.color && from.color) object.color.copy(from.color);
    if (object.target && from.target) object.target.position.copy(from.target.position);
    object.updateProjectionMatrix?.();
  }

  /** A new object for a node or content item: shares the parse's geometry and material, carries no uid. */
  clone(from) {
    const object = from.clone(false);
    delete object.userData.angelo_uid;
    delete object.userData.angelo_role;
    delete object.userData.angelo_digest;
    if (from.target && from.target.parent === from && object.target) object.add(object.target);
    if (isDrawable(object)) {
      this.cache.substitute(object.material);
      sdkMaterial.set(object, object.material);
      sdkMaterialValues.set(object, materialValues(object.material));
    }
    rememberRest(object);
    this.journal.added(object);
    this.report.changed.add(object);
    return object;
  }

  /** The node's content (primitives, a camera or light beside a mesh), slot by slot. */
  content(object, info, fromContent) {
    const content = info.content;
    const removed = [];
    const slot = (current, next) => {
      if (current && next && compatible(current, next)) {
        this.leaf(current, next);
        this.transform(current, next);
        return current;
      }
      if (!current && !next) return null;
      this.touch(object, "node"); // Its content list changes.
      if (current) {
        this.touch(current, "parent");
        removed.push(current);
      }
      if (!next) return null;
      const made = this.clone(next);
      if (current) for (const flag of DISPLAY_FLAGS) made[flag] = current[flag];
      object.add(made);
      return made;
    };
    const count = Math.max(content.primitives.length, fromContent.primitives.length);
    const primitives = [];
    for (let i = 0; i < count; i++) {
      const made = slot(content.primitives[i], fromContent.primitives[i]);
      if (made) primitives.push(made);
    }
    const changed = primitives.length !== content.primitives.length
      || primitives.some((item, i) => item !== content.primitives[i]);
    const camera = slot(content.camera, fromContent.camera);
    const light = slot(content.light, fromContent.light);
    if (changed || camera !== content.camera || light !== content.light) {
      this.copy.nodes.set(object, { ...info, content: { primitives, camera, light } });
    }
    for (const item of removed) item.removeFromParent();
  }

  /** A new node object with its content, recorded in the copy. */
  createNode(node) {
    const object = this.clone(node.object);
    const content = emptyContent();
    for (const primitive of node.content.primitives) {
      const made = this.clone(primitive);
      object.add(made);
      content.primitives.push(made);
    }
    for (const key of ["camera", "light"]) {
      if (node.content[key]) {
        content[key] = this.clone(node.content[key]);
        object.add(content[key]);
      }
    }
    this.copy.nodes.set(object, { uid: node.uid, content });
    return object;
  }

  /** Put a node under `parent` when the SDK placed it (the developer may have moved it elsewhere). */
  place(object, parent) {
    if (object.parent === parent || object.parent !== sdkParent.get(object)) return;
    this.touch(object, "parent");
    parent.add(object);
    sdkParent.set(object, parent);
  }

  /** Update a matched node in place, or replace it when its kind changed. Returns the object that is the node now. */
  updateNode(object, node, parent) {
    if (node.digest && sdkDigest.get(object) === node.digest && compatible(object, node.object)) {
      // Angelo says nothing the page shows for it changed: no geometry, material, texture or GPU work, no event.
      // Only its place is checked (a parent's own digest covers which children it has).
      this.place(object, parent);
      this.learnUid(object, node);
      return object;
    }
    const result = this.updateNodeData(object, node, parent);
    if (node.digest) sdkDigest.set(result, node.digest);
    else sdkDigest.delete(result);
    return result;
  }

  learnUid(object, node) {
    const info = this.copy.nodes.get(object);
    if (info && node.uid !== null && info.uid !== node.uid) this.copy.nodes.set(object, { ...info, uid: node.uid });
  }

  updateNodeData(object, node, parent) {
    if (!compatible(object, node.object)) return this.replace(object, node, parent);
    this.place(object, parent);
    if (object.parent === sdkParent.get(object)) this.transform(object, node.object);
    this.rename(object, node.object);
    // Learning a node's uid (a key that changed after an undo, matched by name) changes nothing the page shows: it
    // is recorded, never reported. A saved file has no uids, so the live ones are kept.
    this.learnUid(object, node);
    this.leaf(object, node.object);
    this.content(object, this.copy.nodes.get(object), node.content);
    return object;
  }

  addNode(node, parent) {
    const object = this.createNode(node);
    if (node.digest) sdkDigest.set(object, node.digest);
    parent.add(object);
    sdkParent.set(object, parent);
    this.report.added.push(object);
    return object;
  }

  /** Another kind of object now (a mesh became skinned, an empty became a light): a new Object3D in its place. */
  replace(object, node, parent) {
    const made = this.createNode(node);
    this.touch(object, "parent", "node");
    for (const flag of DISPLAY_FLAGS) made[flag] = object[flag];
    for (const child of developerChildren(this.copy, object)) {
      this.touch(child, "parent");
      made.add(child);
    }
    const owned = object.parent === sdkParent.get(object);
    const holder = owned ? parent : object.parent ?? parent;
    const index = object.parent === holder ? holder.children.indexOf(object) : -1;
    holder.add(made);
    if (index >= 0) {
      holder.children.splice(holder.children.indexOf(made), 1);
      holder.children.splice(index, 0, made);
    }
    sdkParent.set(made, owned ? holder : DEVELOPER_PLACED);
    if (!owned) {
      made.position.copy(object.position);
      made.quaternion.copy(object.quaternion);
      made.scale.copy(object.scale);
    }
    object.removeFromParent();
    this.copy.nodes.delete(object);
    this.report.replaced.push({ from: object, to: made });
    return made;
  }

  /**
   * Remove deleted nodes. Their developer children go to the nearest surviving node (or the root), keeping their
   * world transform. `removed` holds every deleted node; `isRemoved` says whether an object is one.
   */
  removeNodes(removed) {
    const set = new Set(removed);
    this.copy.root.updateMatrixWorld(true);
    for (const node of removed) {
      const children = developerChildren(this.copy, node);
      let target = node.parent;
      while (target && set.has(target)) target = target.parent;
      target = target ?? this.copy.root;
      for (const child of children) {
        this.touch(child, "parent", "transform");
        target.attach(child);
      }
      this.report.removed.push({ name: nameOf(node), moved: children, to: target });
    }
    for (const node of removed) this.touch(node, "parent", "node");
    return () => {
      for (const node of removed) {
        node.removeFromParent();
        this.copy.nodes.delete(node);
      }
    };
  }

  /**
   * Bind a skinned mesh to this copy's bones (`bones`, matched objects, never replaced). With the same bones the
   * existing Skeleton is kept and only its rest data (inverse bind matrices, bind matrix) is written, so an
   * AnimationMixer driving the bones never notices.
   */
  skin(target, from, bones) {
    const inverses = from.skeleton.boneInverses;
    const skeleton = target.skeleton;
    const sameBones = skeleton && skeleton.bones.length === bones.length && skeleton.bones.every((b, i) => b === bones[i]);
    if (sameBones) {
      const restSame = skeleton.boneInverses.every((matrix, i) => matrix.equals(inverses[i]))
        && target.bindMatrix.equals(from.bindMatrix) && target.bindMode === from.bindMode;
      if (restSame) return;
      this.touch(target, "skin");
      skeleton.boneInverses.forEach((matrix, i) => matrix.copy(inverses[i]));
      target.bindMode = from.bindMode;
      target.bindMatrix.copy(from.bindMatrix);
      target.bindMatrixInverse.copy(from.bindMatrix).invert();
      return;
    }
    this.touch(target, "skin");
    const made = new Skeleton(bones, inverses.map((matrix) => matrix.clone()));
    target.bindMode = from.bindMode;
    target.bind(made, from.bindMatrix);
    this.report.skeletons.push(made);
  }
}

export const DISPLAY_FLAGS = ["castShadow", "receiveShadow", "visible", "renderOrder", "frustumCulled"];
