<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: MIT -->

# Vite facts the bridge depends on

Checked on 2026-11-04 against Vite's published packages, not from memory, on the Linux VM `angelo-weblink-linux`:
the `dist/` of vite 5.4.11, 5.4.12, 6.0.8, 6.0.9, 6.1.0, 6.2.0, 6.3.0, 6.4.5, 7.0.0, 7.3.7 (latest 7.x) and 8.3.5
(npm `latest` that day), read with `grep`/`sed`, plus a probe (a real dev server per version, a WebSocket HMR client
and real file edits) run on 6.0.9, 7.3.7 and 8.3.5. Line numbers below are in those published bundles; the upstream
sources are `packages/vite/src/node/server/{index,ws,hmr}.ts` and `server/middlewares/hostCheck.ts`.

## 1. The HMR WebSocket token: added in 6.0.9 (and 5.4.12); minimum supported Vite is 6.0.9

- `webSocketToken` is absent from every file of 6.0.8 and 5.4.11 and present in 6.0.9 and 5.4.12 (the fix was
  backported to the 5.4 line). It is the published fix for CVE-2025-24010 / GHSA-vg6x-rcgg-rjx6 (any website could
  open the dev server's HMR socket and read its messages); that advisory id is from memory, the version boundary is
  verified.
- How it works (6.0.9 `dep-BdTvomPN.js` ~37782-37846; 7.3.7 `chunks/config.js` ~20589-20632):
  - the resolved config gets `webSocketToken`: 9 random bytes, base64url, made once per config
    (`crypto.getRandomValues(new Uint8Array(9))`);
  - `/@vite/client` is served with the token baked in (`__WS_TOKEN__`) and opens `ws://host?token=<token>`;
  - the server's `shouldHandle(req)` accepts the upgrade when the `Host` passes the host check and, **when the
    request has an `Origin` header** (every browser sends one on a WebSocket), the `token` query parameter equals the
    token (`crypto.timingSafeEqual`). A request without `Origin` (a non-browser client) needs no token, and
    `legacy.skipWebSocketTokenCheck: true` turns the check off.
  - So the token protects the channel from other web origins only, as long as `/@vite/client` cannot be read
    cross-origin. Vite's default `server.cors` (since the same fix) allows only loopback origins.
- Minimum: **6.0.9**. 5.4.12 has the token too, but the bridge also uses Vite 6's `hotUpdate` hook and
  `server.environments`, so 5.x is not supported. The plugin warns when `legacy.skipWebSocketTokenCheck` is on.

## 2. Plugin middlewares and Vite's host check: the order changed in 7.0.0, so the bridge checks everything itself

- **5.4.12 to 6.4.x**: `configureServer` hooks run first; middlewares a hook adds directly with
  `server.middlewares.use` therefore run **before** Vite's `cors` and `hostCheckMiddleware`. A returned post-hook
  runs after the host check, cors, transforms and public/static serving, just before `indexHtmlMiddleware`
  (6.0.9 ~44023-44076, 6.4.5 ~39000-39013).
- **7.0.0 and later** (7.3.7 ~25843-25853, 8.3.5 ~25054-25064): `rejectInvalidRequest`, `cors` and
  `hostValidationMiddleware` are installed **before** the `configureServer` hooks, so even direct middlewares run
  after them.
- What Vite's own host check does not cover, in every version:
  - it is skipped entirely when `server.https` is set or `server.allowedHosts` is `true`;
  - any IPv4 literal and any bracketed IPv6 literal passes, as do `localhost` and `*.localhost`, whatever the port;
  - from 7.0.0 a request with no `Host` header passes (`isHostAllowed(undefined) === true`);
  - it never looks at the socket's peer, so `vite --host` serves the LAN;
  - on 7.0.0+ the `cors` middleware has already added `Access-Control-Allow-Origin` for loopback origins, and it
    answers an `OPTIONS` preflight itself, before any plugin middleware.
- Therefore the bridge (`src/vite/http.js`) does every check of PROTOCOL.md §2 itself: loopback peer, its own
  `Host` list with the request's own port, `Sec-Fetch-Site`/`Origin`, the path token; and it puts its middleware at
  the **front** of Vite's connect stack (`server.middlewares.stack.unshift`, falling back to `use`), so on every
  version it answers `/@angelo-live/...` before Vite's cors can add a header or answer a preflight. It also removes
  any `Access-Control-*` header already set, as a second guard. Every other request goes to `next()` untouched.
- HMR messages: `server.ws.on(event, (data, client))` passes a client with `socket` (the `ws` socket) on 6.0.9,
  7.3.7 and 8.3.5, and `server.ws.on("connection", (socket, request))` gives the peer address; the bridge ignores tab
  messages from non-loopback peers. `vite:client:connect`/`vite:client:disconnect` exist only from 7.x, so the bridge
  listens to the socket's own `close` instead.

## 3. Changed asset files in dev

Probe: a page whose module does `import b from "./assets/b.glb"` and `new URL("./assets/c.glb", import.meta.url)`,
plus `public/models/a.glb`; each file rewritten while an HMR client listens.

| File | 6.0.9 | 7.3.7 | 8.3.5 |
|---|---|---|---|
| `public/models/a.glb` (no module) | nothing | nothing | nothing |
| `src/assets/b.glb`, imported (`?import` module) | `full-reload` | `full-reload` | `full-reload` |
| `src/assets/c.glb` via `new URL(…, import.meta.url)` | nothing | `full-reload` | `full-reload` |

- A public file is not in the module graph; `handleHMRUpdate` finds no module and reloads only for `.html`.
- An asset in the graph has importers that don't accept HMR, so the update reaches the root: a full page reload,
  which would lose the developer's page state on every Angelo save.
- `hotUpdate` (Vite 6+) is called per environment with the changed file's modules; **returning `[]` suppresses the
  update**, verified on all three versions (no message reached the client). The plugin returns `[]` for linked
  assets and for anything under `.angelo/`, which it also adds to `server.watch.ignored` (as a function, which
  chokidar 3 and 4 both accept). `handleHotUpdate` is deprecated in favour of `hotUpdate` from Vite 6 and is not
  used.
- Serving a changed file: the page re-fetches it with `?angelo=<save>`; Vite's public and static middlewares strip
  the query (`cleanUrl`) and serve the current bytes.
