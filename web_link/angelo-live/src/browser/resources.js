// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// GPU resources the SDK made (from its own parses), disposed once no tracked copy uses them. What the developer
// loaded is never disposed here: their own code may still hold it.

function texturesOf(material, into) {
  for (const value of Object.values(material)) {
    if (value?.isTexture) into.add(value);
  }
  for (const uniform of Object.values(material.uniforms ?? {})) {
    const value = uniform?.value;
    if (value?.isTexture) into.add(value);
    else if (Array.isArray(value)) for (const item of value) if (item?.isTexture) into.add(item);
  }
}

/** Every geometry, material, texture and skeleton under `roots`. */
export function resourcesUnder(roots) {
  const found = new Set();
  for (const root of roots) {
    root.traverse((object) => {
      if (object.geometry) found.add(object.geometry);
      if (object.skeleton) found.add(object.skeleton);
      const materials = Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];
      for (const material of materials) {
        found.add(material);
        texturesOf(material, found);
      }
    });
  }
  return found;
}

export class Resources {
  constructor() {
    this.created = new Set();
  }

  /** Everything a parse made, so it can be disposed when nothing uses it. */
  adopt(root) {
    for (const item of resourcesUnder([root])) this.created.add(item);
  }

  /** Remember a skeleton the matcher made. */
  add(item) {
    this.created.add(item);
  }

  /**
   * Dispose what the SDK made that nothing under `roots` uses and that isn't in `keep` (resources a journal may put
   * back). `retain(item)` keeps an item tracked after disposing it (a texture kept for the link, so it is disposed
   * again whenever it goes unused). Returns the set disposed.
   */
  sweep(roots, keep = [], retain = () => false) {
    const used = resourcesUnder(roots);
    for (const item of keep) {
      for (const material of Array.isArray(item) ? item : [item]) {
        used.add(material);
        if (material?.isMaterial) texturesOf(material, used);
      }
    }
    const disposed = new Set();
    for (const item of this.created) {
      if (used.has(item)) continue;
      item.dispose?.(); // Frees GPU memory; a retained texture keeps its picture and uploads again if used.
      disposed.add(item);
      if (!retain(item)) this.created.delete(item);
    }
    return disposed;
  }
}
