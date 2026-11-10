<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: MIT -->

# Angelo web live link: wire contract

This is the contract between Angelo, the dev-server bridge (`@angelo-studio/live/vite`) and the browser SDK
(`@angelo-studio/live`). Angelo's side is `doc/angelo/web_link.rst`; the engine-neutral link is
`doc/angelo/engine_link.rst`. Both halves of this package follow this file. A change here changes both.

## 1. Angelo ⇄ bridge: `angelo-link/1`

The bridge is an engine editor plugin like Godot's. It runs in Node, inside the developer's dev server.

### Finding Angelo

- **The endpoint record.** The record is `angelo-link.json` in Angelo's per-user data folder:
  - macOS: `~/Library/Application Support/Angelo`
  - Windows: `%LOCALAPPDATA%\Angelo`
  - Linux and others: `$XDG_STATE_HOME/Angelo`, or `~/.local/state/Angelo`
- **The override.** `ANGELO_LINK_RECORD` names the record instead (tests use it).
- **Reading it.** Read the record only when it is a regular file of at most 4096 bytes. Where modes exist, it must be
  owned by this user and carry no group or other bits. Never follow a link.
  - JSON: `{protocol: "angelo-link/1", port, pid, token: <64 hex>, app, binary}`.
  - Any other content counts as no record.
- **The token.** The token is secret: never log it, print it, send it to the page or put it in an error.
- **Polling.** Watch the record about every 2 s while disconnected. A changed record means a new Angelo: reconnect.

### Frames

- Each frame is a 4-byte big-endian length `n` (1 ≤ n ≤ 65536), then `n` bytes of UTF-8 JSON (the header), then
  `header.bytes` bytes of payload when the header has `bytes`.
- Payloads from Angelo are at most 512 MiB.
- Non-finite numbers are invalid JSON here.
- A malformed frame ends the connection.

### Authentication (mutual HMAC-SHA256; the token never crosses the socket)

1. Connect to `127.0.0.1:<port>`. Angelo sends `{type: "hello", protocol: "angelo-link/1", nonce: <64 hex>, app}`.
2. The client sends `{type: "auth", nonce: <its own 64 hex>, proof, client}`.
   - `proof = hex(HMAC-SHA256(key = bytes.fromhex(token), msg = "angelo-link/1/client\n" + server nonce + "\n" + client nonce))`.
   - The message is ASCII, joined with `\n`.
3. Angelo answers `{type: "welcome", proof}`, where `proof` is the same HMAC over `"angelo-link/1/server\n…"`.
   - The client checks it, in constant time, before trusting anything.
   - `{type: "error", code: "auth"}` or a wrong proof means: not Angelo, or a stale token. Stop and wait for a new
     record.
4. Auth must finish within 10 s.

`client` in `auth` is what the editor says about itself. Angelo keeps at most 8 capabilities and 400 characters a
field:

```json
{"engine": "web", "web": "vite 7.1.3; three 0.185.1", "plugin": "@angelo-studio/live 0.1.0",
 "capabilities": ["durable", "live", "acks", "capture"],
 "project": "/abs/path/to/vite/root", "project_name": "<package.json name, else the folder name>"}
```

Add `"look"` to `capabilities` only when a tracked page asked for `look: "apply"`. It is reserved: Angelo 1.0.10
sends no look frames yet.

### Liveness

- Send `{type: "ping"}` every 10 s. Angelo answers `pong`.
- Angelo drops an editor that sends nothing for 45 s.
- The client counts Angelo lost after 60 s without any frame.

### Requests (client → Angelo; headers only, never a payload except §1.1)

- `{type: "open", id, project, asset, project_name, node}`
  - `asset` is the project-relative POSIX path of a `.glb` (`"public/models/chair.glb"`).
  - `node` is the clicked object's name, shown on the card.
  - `id` is unique per request, at most 64 characters.
- `{type: "close", id, asset}`
- `{type: "status", asset, code, ...}` acknowledgements:
  - `{code: "previewed", version, instances}`: `instances` is the number of tracked copies now showing that
    version, summed over every tab.
  - `{code: "preview_failed", version, message}`
  - `{code: "reimported", save}` after the page shows the saved file of a `saved` frame, echoing its `save`.
  - `{code: "reimport_failed", save, message}`
  - Messages are English, at most 200 characters, and never hold a path.

### Replies and pushes (Angelo → client)

- `pending {id, asset}`: the person decides on a card in Angelo.
- `opened {id?, asset}`: linked. Without an `id`, it is a broadcast to every editor of the project.
- `refused {id, code, message}`: show `message` (English).
  - Codes: `not_project`, `outside`, `kind`, `missing`, `engine`, `superseded`, `declined`, `failed`,
    `unreadable`.
- `closed {id, asset}`
- `scene {asset, version, export_ms, kind, base?, removed?}` plus a `.glb` payload: a live frame (§1.2). It is
  uncompressed, with images embedded and no animations.
- `saved {asset, files, scene_version, save}`: the asset file on disk was rewritten. The page re-fetches it.
- `status {asset, code, message}`: `edit_mode`, `export_failed`, `write_failed`, `baked_*`. Shown as notices.
- `ended {asset, reason}`: `engine`, `angelo`, `stopped`, `replaced`, `other_file`, `saved_elsewhere`. Every reason
  but `engine` gets a notice.
- `capture {id, asset, max}` (§1.1).
- `pong`

### 1.2 Live frames: full and delta

The page keeps every object it already has, and a live frame never rebuilds the developer's scene.

**Every node** in a frame's `.glb` carries, in `extras`:

- `angelo_uid`: the node's **key**. It is unique in the file and the same in every frame of one Angelo process.
  - an object's key is its Blender `session_uid`;
  - a bone's key is `"<armature uid>/<bone name>"`, and the exporter's extra leaf node of a bone adds `"#leaf"`;
  - an object inside a collection instance is keyed `"<instancer key>><uid>"`.
- `angelo_role`:
  - `"content"`: the page applies this node;
  - `"parent"`: present only so that a content node hangs where it belongs. The page never changes a parent node; it
    finds it by key.
- `angelo_digest`, on content nodes: the digest of everything the page shows for the node:
  - its name, transform and children's keys;
  - its mesh data and materials, with its images by digest;
  - its skin, camera and extensions.

  A content node whose digest equals the one the page already has is left untouched: no geometry, material, texture
  or GPU work, and no event.

**Images** carry `extras.angelo_digest`, the SHA-256 of their bytes. An image that every receiving page already has
is sent as a **stub**: a 1 × 1 PNG with `extras.angelo_stub: true`. The page uses its cached texture of that digest.
The page keeps its textures by digest for the whole link, even after the objects that used them go. A stub whose
digest the page doesn't have means the page resyncs (below).

**`kind: "full"`** holds every node, all `content`. It carries no stubs, except the full frame of a still-scene
check (Angelo's whole export after edits pause, sent only when a digest moved), which may stub images every
receiving page has. A page missing a stub's picture asks for a full frame, which comes with no stubs. The page
applies a full frame by diff:

- nodes it has with the same digest stay untouched;
- changed nodes update in place;
- new nodes are created;
- nodes it had under the tracked asset that the frame lacks are removed.

Angelo sends a full frame for a page's first frame, a resync, a page that fell behind (`LinkServer.lagging`), after
an undo, redo or file read, and when most of the scene changed.

**`kind: "delta"`** holds only the objects that changed (as `content`) plus their ancestors and the armatures their
skins need (as `parent`).

- `base` is the version it applies on.
- `removed` lists the keys that are gone.
- Angelo never drops a delta, and a client more than 8 deltas behind gets a full frame instead.
- The page applies a delta only when `base` equals the version it shows. Otherwise it applies nothing and asks for
  a full frame.

**The page asks for a full frame** (page → bridge `angelo:resync {asset}`, bridge → Angelo
`{type: "status", asset, code: "resync"}`) when a delta doesn't fit what it shows:

- the `base` differs;
- a parent key it doesn't have and the frame doesn't hold;
- a stub digest it doesn't have.

**Keys can change** across an undo or a reload: Blender may give objects new session uids. In a full frame, the
page matches a node whose key it doesn't know by its name path from the asset root before it creates anything.
When one matches, the existing Object3D adopts the new key, so identity survives.

**Skinned meshes**: the page keeps its bone Object3Ds, always. A bone's content updates only its rest data:

- its transform;
- the skin's inverse bind matrices.

A new or changed SkinnedMesh binds to the existing bones by key, so a playing AnimationMixer keeps driving them.

### 1.1 Captures: the only client payload

When Angelo's agent asks to see the page (`angelo_view` `image: "WEB"`), Angelo sends `capture {id, asset, max}` to
one live editor of the linked asset. The client answers once, within 8 s, with one of:

- `{type: "status", asset, code: "captured", id, bytes: <n>}` plus a PNG payload of at most 8 MiB, at most `max`
  pixels on its longer side, from the WebGL canvas only;
- `{type: "status", asset, code: "capture_failed", id, message}`: capture off in the config, a hidden tab, or no
  tracked copy on screen.

A payload is accepted only:

- in reply to that client's outstanding `id`;
- with `code: "captured"`;
- when it starts with the PNG signature and fits the bound.

Angelo ends the connection on any other client payload.

## 2. Bridge ⇄ page

The page never talks to Angelo. Events travel over Vite's HMR channel. Bytes travel over same-origin HTTP under
`/@angelo-live/<token>/`, where `<token>` is 16 random bytes (base64url) made once per dev-server start and given to
the page in the virtual module `virtual:angelo-live/config`.

### Server → page (`server.hot.send` / `server.ws.send`; event names)

- `angelo:state {link: "waiting" | "live" | "lost" | "off", message?}`
- `angelo:pending {asset}`, `angelo:opened {asset}`, `angelo:refused {asset, code, message}`
- `angelo:scene {asset, version, kind, base?, removed?, url}`: `url` is that frame's `.glb`
  (`scene/<asset-id>/<version>.glb`). The bridge keeps every delta since the last full frame, at most 8 (a delta is
  never skipped), and passes `kind`, `base` and `removed` through unchanged.
- `angelo:saved {asset, save, url}`: `url` is the asset's own dev-server URL with `?angelo=<save>`.
- `angelo:status {asset, code, message}`, `angelo:ended {asset, reason}`
- `angelo:capture {id, asset, max}`: sent to every tab. The first good answer wins; the bridge relays it.

`asset` on the page side is the URL the page loaded (`"/models/chair.glb"`). The bridge maps between that URL and the
project path (Vite `publicDir` and `root`).

### Page → server (`import.meta.hot.send`)

- `angelo:hello {tab, three, tracked: [url], capture: bool}`
- `angelo:track {tab, url}`, `angelo:untrack {tab, url}`
- `angelo:edit {tab, url, node}`
- `angelo:ack {tab, asset, code, version|save, instances, message?}`
- `angelo:capture_failed {tab, id, message}`
- `angelo:resync {tab, asset}`: the bridge sends Angelo `status {asset, code: "resync"}` (§1.2)

### HTTP, under `/@angelo-live/<token>/`

- `GET scene/<asset-id>/<version>.glb`: any frame the bridge still holds (the last full one and the deltas after
  it); 404 otherwise.
- `POST capture/<id>`: a PNG body of at most 8 MiB.

Every request must pass every check, or it gets 403 or 404 with no body detail:

- the socket peer is loopback (`127.0.0.1`, `::1`, `::ffff:127.0.0.1`), whatever the `Host` says;
- `Host` is `localhost`, `127.0.0.1` or `[::1]` with the dev server's port, or one of Vite's `allowedHosts`;
- `Sec-Fetch-Site` is `same-origin`, or `Origin` equals the dev server's own origin;
- the path token matches (constant-time).

Every response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`
and `Cross-Origin-Resource-Policy: same-origin`, and never a CORS header.

## 3. Production

- `@angelo-studio/live` under the `production` export condition (and its default) is a no-op stub with the same API.
- The Vite plugin is `apply: "serve"`.
- A production build holds no `angelo-live` route, socket or string.
