// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The Vite plugin: `plugins: [angeloLive()]` in vite.config. Dev server only (`apply: "serve"`), so a production
// build never holds any of it. It connects the dev server to Angelo (link.js), serves the page's bytes under a
// per-start token (http.js), relays events over Vite's own token-checked HMR socket (bridge.js), and keeps Vite from
// reloading the page when Angelo writes a linked asset (VITE-FACTS.md §3).

import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";
import path from "node:path";
import { Bridge } from "./bridge.js";
import { ROOT, createMiddleware, isLoopbackPeer } from "./http.js";
import { LinkClient } from "./link.js";
import { projectFolder, projectName, relativeInside, urlToProjectPath } from "./paths.js";

const PACKAGE = "@call-me-sensei/angelo";
const ALIASES = ["angelo-studio"]; // Unscoped alias packages that re-export this one.
const VERSION = "0.1.1";
const CONFIG_ID = "virtual:angelo-live/config";
const RESOLVED_CONFIG = `\0${CONFIG_ID}`;
const ANGELO_FOLDER = /(^|[\\/])\.angelo([\\/]|$)/;
const TAB_EVENTS = ["angelo:hello", "angelo:track", "angelo:untrack", "angelo:edit", "angelo:ack",
  "angelo:capture_failed", "angelo:resync", "angelo:bye"];

function packageVersion(from, name) {
  try {
    return createRequire(path.join(from, "noop.js"))(`${name}/package.json`).version ?? "unknown";
  } catch {
    return "unknown";
  }
}

const STUB = fileURLToPath(new URL("../stub.js", import.meta.url));

/**
 * In a build, `@call-me-sensei/angelo` is the no-op stub whatever the export conditions say: a build run with
 * NODE_ENV other than "production" would otherwise pick the development SDK.
 */
function buildPlugin() {
  return {
    name: "angelo-live:build",
    apply: "build",
    enforce: "pre",
    resolveId(id) {
      return id === PACKAGE ? STUB : null;
    },
  };
}

/**
 * @param {object} [options]
 * @param {boolean} [options.capture=true]  Let Angelo's agent ask the page for a picture of its canvas.
 * @param {string} [options.record]  The endpoint record's path (default: Angelo's per-user folder, or
 *   ANGELO_LINK_RECORD).
 * @returns {import("vite").Plugin[]} The dev-server bridge, and for builds only the stub's resolution.
 */
export function angeloLive(options = {}) {
  return [devPlugin(options), buildPlugin()];
}

function devPlugin(options) {
  const captureAllowed = options.capture !== false;
  const token = randomBytes(16).toString("base64url"); // The page's path token: one per dev-server start.
  const routeBase = `${ROOT}${token}/`;
  let config = null;
  let bridge = null;
  let project = "";

  const ignoreAngeloFolder = (file) => ANGELO_FOLDER.test(file);

  return {
    name: "angelo-live",
    apply: "serve",
    enforce: "pre",
    // For tests and other plugins: the running bridge (null before the dev server starts).
    api: { bridge: () => bridge, routeBase },

    config() {
      // The SDK imports this plugin's virtual module, which esbuild's dependency pre-bundling can't resolve; served
      // as source it also shares the app's own copy of three.
      return { optimizeDeps: { exclude: [PACKAGE, ...ALIASES] }, server: { watch: { ignored: [ignoreAngeloFolder] } } };
    },

    configResolved(resolved) {
      config = resolved;
    },

    resolveId(id) {
      return id === CONFIG_ID ? RESOLVED_CONFIG : null;
    },

    load(id) {
      if (id !== RESOLVED_CONFIG) return null;
      return `export default ${JSON.stringify({ base: routeBase, capture: captureAllowed })};`;
    },

    configureServer(server) {
      project = projectFolder(config.root);
      const name = projectName(project);
      const viteVersion = packageVersion(config.root, "vite");
      const threeVersion = packageVersion(config.root, "three");
      const logger = config.logger;
      const say = (message) => logger.info(`[angelo] ${message}`, { timestamp: true });
      if (config.legacy?.skipWebSocketTokenCheck) {
        logger.warn("[angelo] legacy.skipWebSocketTokenCheck is on: other websites could read this dev server's live "
          + "link events. Turn it off to protect them.");
      }
      let look = false; // A tracked page asked for the look (reserved: Angelo 1.0.10 sends none yet).
      const link = new LinkClient({
        record: options.record,
        client: () => ({
          engine: "web",
          web: `vite ${viteVersion}; three ${threeVersion}`,
          plugin: `${PACKAGE} ${VERSION}`,
          capabilities: ["durable", "live", "acks", ...(captureAllowed ? ["capture"] : []), ...(look ? ["look"] : [])],
          project,
          project_name: name,
        }),
      });
      const publicDir = config.publicDir || "";
      const peers = new WeakMap(); // HMR socket -> its peer address, from the WebSocket server's "connection".
      const ws = server.ws;
      const hot = {
        broadcast: (event, data) => ws.send(event, data),
        sendTo: (client, event, data) => client.send(event, data),
      };
      bridge = new Bridge({
        link, hot, project, projectName: name, routeBase, capture: captureAllowed,
        toProject: (url) => urlToProjectPath(url, { root: config.root, publicDir, base: config.base, project }),
        log: say,
      });
      link.on("state", (state, message) => {
        if (state === "live" || state === "lost") say(message);
        bridge.linkState();
      });
      link.on("frame", (header, payload) => bridge.fromAngelo(header, payload));
      link.on("problem", (error) => logger.error(`[angelo] ${error?.message ?? error}`));

      ws.on("connection", (socket, request) => {
        peers.set(socket, request?.socket?.remoteAddress);
      });
      const local = (client) => isLoopbackPeer(peers.get(client?.socket) ?? client?.socket?._socket?.remoteAddress);
      const watched = new WeakSet();
      for (const event of TAB_EVENTS) {
        ws.on(event, (data, client) => {
          if (!client || !local(client)) return; // `vite --host`: a tab on another machine gets nothing.
          if (event === "angelo:hello") {
            if (data?.look === "apply" && !look) {
              look = true;
              if (!bridge.assetsLinked()) link.reconnect(); // Re-announce capabilities; never mid-link.
            }
            if (!watched.has(client.socket)) {
              watched.add(client.socket);
              client.socket?.on?.("close", () => bridge.tabClosed(client));
            }
          }
          bridge.fromTab(event, data, client);
        });
      }

      const middleware = createMiddleware({
        token,
        allowedHosts: () => config.server.allowedHosts,
        scene: (assetId, version) => bridge.sceneBytes(assetId, version),
        capture: (id, png, size) => bridge.captured(id, png, size),
        expectsCapture: (id) => bridge.expectsCapture(id),
      });
      // First in Vite's stack, so on Vite 7+ its cors can't add a header to (or answer a preflight for) these
      // routes; everything else passes straight through (VITE-FACTS.md §2).
      const stack = server.middlewares.stack;
      if (Array.isArray(stack)) stack.unshift({ route: "", handle: middleware });
      else server.middlewares.use(middleware);

      link.start();
      server.httpServer?.once("close", () => {
        bridge.close();
        link.stop();
      });
    },

    hotUpdate({ file }) {
      if (ignoreAngeloFolder(file)) return [];
      if (!bridge || !project) return undefined;
      const relative = relativeInside(project, file);
      if (relative && bridge.quietPaths().includes(relative)) return []; // The page re-fetches it itself.
      return undefined;
    },
  };
}

export default angeloLive;
