// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

import type { Plugin } from "vite";

export interface AngeloLiveOptions {
  /** Let Angelo's agent ask the page for a picture of its WebGL canvas (default true). */
  capture?: boolean;
  /** Path of Angelo's endpoint record. Default: Angelo's per-user data folder, or ANGELO_LINK_RECORD. */
  record?: string;
}

/**
 * The dev-server bridge between Angelo and this project's pages (`apply: "serve"`), plus, for builds only, a
 * resolver that makes `@call-me-sensei/angelo` the no-op stub.
 */
export declare function angeloLive(options?: AngeloLiveOptions): Plugin[];
export default angeloLive;
