// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// What the SDK changed in one tracked copy since the asset was last as saved, so stopping puts back exactly that, in
// place, without reloading anything. Before the SDK first changes an object it records how the object was; objects
// it made are listed to be removed. Restoring touches nothing else: the developer's camera, physics, mixers and
// everything they added stay as they are.

import { Matrix4 } from "three";

const LEAF_FIELDS = ["intensity", "distance", "decay", "angle", "penumbra", "width", "height", "fov", "near", "far",
  "zoom", "left", "right", "top", "bottom"];

// What "leaf" covers: a light's or camera's settings, morph influences, an instanced mesh's per-instance data.
function leafState(object) {
  return {
    fields: Object.fromEntries(LEAF_FIELDS.filter((field) => typeof object[field] === "number")
      .map((field) => [field, object[field]])),
    color: object.isLight ? object.color?.clone() : undefined,
    target: object.target?.position?.clone(),
    influences: object.morphTargetInfluences?.slice(),
    instanceMatrix: object.isInstancedMesh ? object.instanceMatrix.array.slice() : undefined,
    instanceColor: object.isInstancedMesh ? object.instanceColor : undefined,
  };
}

function restoreLeaf(object, leaf) {
  Object.assign(object, leaf.fields);
  if (leaf.color) object.color.copy(leaf.color);
  if (leaf.target && object.target) object.target.position.copy(leaf.target);
  if (leaf.influences && object.morphTargetInfluences) {
    object.morphTargetInfluences.splice(0, object.morphTargetInfluences.length, ...leaf.influences);
  }
  if (leaf.instanceMatrix) {
    object.instanceMatrix.array.set(leaf.instanceMatrix);
    object.instanceMatrix.needsUpdate = true;
    object.instanceColor = leaf.instanceColor;
  }
  object.updateProjectionMatrix?.();
}

function snapshot(object, copy, owner) {
  const info = copy.nodes.get(object);
  return {
    parent: object.parent,
    index: object.parent ? object.parent.children.indexOf(object) : -1,
    name: object.name,
    hasUserName: "name" in object.userData,
    userName: object.userData.name,
    position: object.position.clone(),
    quaternion: object.quaternion.clone(),
    scale: object.scale.clone(),
    geometry: object.geometry,
    material: object.material,
    skeleton: object.skeleton,
    boneInverses: object.skeleton?.boneInverses.map((matrix) => matrix.clone()),
    bindMatrix: object.bindMatrix?.clone(),
    bindMode: object.bindMode,
    info: info ? { uid: info.uid, content: { primitives: [...info.content.primitives], camera: info.content.camera,
      light: info.content.light } } : null,
    leaf: leafState(object),
    owner: owner.save(object),
    aspects: new Set(),
  };
}

export const ASPECTS = ["transform", "name", "geometry", "material", "skin", "leaf", "parent", "node"];

export class Journal {
  /** @param {{save(object): any, load(object, saved): void}} owner  The matcher's own per-object records. */
  constructor(owner) {
    this.owner = owner;
    this.before = new Map(); // Object3D -> how it was
    this.made = new Set(); // Objects the SDK added
  }

  get empty() {
    return this.before.size === 0 && this.made.size === 0;
  }

  /** Call before the SDK changes `aspect` (one of ASPECTS) of `object`. */
  touch(object, copy, aspect) {
    if (this.made.has(object)) return;
    let state = this.before.get(object);
    if (!state) {
      state = snapshot(object, copy, this.owner);
      this.before.set(object, state);
    }
    state.aspects.add(aspect);
  }

  added(object) {
    this.made.add(object);
  }

  /** The asset is as saved now (a save was shown): forget what came before. */
  reset() {
    this.before.clear();
    this.made.clear();
  }

  /** What the recorded state still refers to, so it is not disposed while it may come back. */
  resources() {
    const found = [];
    for (const state of this.before.values()) {
      if (state.aspects.has("geometry")) found.push(state.geometry);
      if (state.aspects.has("material")) found.push(state.material);
      if (state.aspects.has("skin")) found.push(state.skeleton);
    }
    return found.filter(Boolean);
  }

  /** Put the copy back as it was at the last reset. */
  restore(copy) {
    // Objects the SDK made go; anything else under them goes to the nearest object that stays.
    for (const object of this.made) {
      for (const child of [...object.children]) {
        if (this.made.has(child) || this.before.has(child)) continue;
        let target = object.parent;
        while (target && this.made.has(target)) target = target.parent;
        (target ?? copy.root).attach(child);
      }
    }
    for (const object of this.made) {
      object.removeFromParent();
      copy.nodes.delete(object);
    }
    const entries = [...this.before].sort((a, b) => a[1].index - b[1].index);
    for (const [object, state] of entries) {
      const has = (aspect) => state.aspects.has(aspect);
      if (has("name")) {
        object.name = state.name;
        if (state.hasUserName) object.userData.name = state.userName;
        else delete object.userData.name;
      }
      if (has("transform")) {
        object.position.copy(state.position);
        object.quaternion.copy(state.quaternion);
        object.scale.copy(state.scale);
        if (!object.matrixAutoUpdate) object.updateMatrix();
      }
      if (has("geometry") && object.geometry !== state.geometry) {
        object.geometry = state.geometry;
        object.updateMorphTargets?.();
      }
      if (has("material")) object.material = state.material;
      if (has("leaf")) restoreLeaf(object, state.leaf);
      if (has("skin") && state.skeleton) {
        state.skeleton.boneInverses.forEach((matrix, i) => matrix.copy(state.boneInverses[i] ?? new Matrix4()));
        object.bindMode = state.bindMode;
        object.bind(state.skeleton, state.bindMatrix);
      }
      if (has("node")) {
        if (state.info) copy.nodes.set(object, state.info);
        else copy.nodes.delete(object);
      }
      this.owner.load(object, state.owner);
    }
    for (const [object, state] of entries) {
      if (!state.aspects.has("parent")) continue;
      if (!state.parent) {
        object.removeFromParent();
        continue;
      }
      if (object.parent !== state.parent) state.parent.add(object);
      const children = state.parent.children;
      const at = children.indexOf(object);
      const index = Math.min(state.index, children.length - 1);
      if (at !== index) {
        children.splice(at, 1);
        children.splice(index, 0, object);
      }
    }
    this.reset();
  }
}
