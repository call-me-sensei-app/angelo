// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

import type { Camera, Object3D, Scene, WebGLRenderer } from "three";

/** What a GLTFLoader (three's, or a compatible one) gives: `loadAsync(url)` and `parseAsync(data, path)`. */
export interface LoaderLike {
  loadAsync(url: string): Promise<GltfLike>;
  parseAsync(data: ArrayBuffer, path: string): Promise<GltfLike>;
}

/** A GLTFLoader result: its `scene` and, for node identity, its `parser` (associations and json). */
export interface GltfLike {
  scene: Object3D;
  parser?: { associations?: Map<unknown, { nodes?: number }>; json?: unknown };
  [key: string]: unknown;
}

export interface AngeloLiveOptions {
  /** Your renderer: Alt-click picking and captures use its canvas. */
  renderer?: WebGLRenderer;
  /** Your scene (optional; kept for reference). */
  scene?: Scene;
  /** Your GLTFLoader, with your decoders (Draco, meshopt, KTX2). Required. */
  loader: LoaderLike;
  /** The camera Alt-click picks with. Default: the last camera rendered to the canvas. */
  camera?: Camera;
  /** Reserved: Angelo 1.0.10 sends no look yet, so nothing is applied either way. Default "ignore". */
  look?: "ignore" | "apply";
  /** Answer Angelo's requests for a picture of the canvas (only while Angelo asks). Default true. */
  capture?: boolean;
  /** Show the corner status dot. Default true. */
  overlay?: boolean;
}

export type LinkState = "off" | "waiting" | "live" | "lost";

export interface AngeloLiveEvents {
  state: { link: LinkState; message: string; session: number };
  pending: { asset: string };
  opened: { asset: string };
  refused: { asset: string; code: string; message: string };
  updated: { asset: string; version: number; instances: number };
  saved: { asset: string; save: number; instances: number };
  restored: { asset: string; instances: number };
  ended: { asset: string; reason: string };
  status: { asset: string; code: string; message: string };
  renamed: { asset: string; object: Object3D; from: string; to: string };
  removed: { asset: string; name: string; moved: Object3D[]; to: Object3D };
  added: { asset: string; objects: Object3D[] };
  replaced: { asset: string; from: Object3D; to: Object3D };
}

export interface TrackHandle {
  untrack(): void;
}

export interface AngeloLive {
  /** Follow Angelo's edits of the asset at `url` in this copy (a GLTFLoader result, its scene, or a clone). */
  track(target: GltfLike | Object3D, url: string): TrackHandle;
  /** Stop following one copy, or every copy of a URL. */
  untrack(target: Object3D | string): void;
  /** Ask Angelo to open the asset; `node` names the object shown on Angelo's card. */
  edit(url: string, node?: string): void;
  /** Listen to an event; the "angelo:" prefix is optional. Returns a function that removes the listener. */
  on<K extends keyof AngeloLiveEvents>(event: K | `angelo:${K}`, listener: (detail: AngeloLiveEvents[K]) => void): () => void;
  /** Put every tracked copy back as last saved, then stop. */
  stop(): Promise<void>;
  readonly state: { link: LinkState; message: string; session: number;
    assets: { url: string; copies: number; opened: boolean; live: boolean; version: number }[] };
}

/** Start the live link on this page. In a production build this is a no-op with the same API. */
export declare function createAngeloLive(options: AngeloLiveOptions): AngeloLive;
