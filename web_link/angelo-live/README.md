<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: MIT -->

# @call-me-sensei/angelo

Angelo's live link for the web: edits you make in [Angelo](https://angelo.studio) show up live in **your own**
Three.js page, inside your lights, shaders, physics and game state. Saving in Angelo rewrites the `.glb` your page
loads, and the page picks the new file up without reloading.

It has two halves:

- a **Vite plugin** that runs in your dev server and talks to Angelo on this computer;
- a **browser SDK** that updates the objects of a tracked glTF in place, by glTF node identity.

Your page never talks to Angelo. It talks only to its own dev server, over its own origin.

## Install

```bash
npm i -D @call-me-sensei/angelo
```

(`angelo-studio` is an unscoped alias of the same package.) Requirements: Vite 6.0.9 or newer, three.js r172 or
newer, Node 18.18 or newer.

## Use

```js
// vite.config.js
import { angeloLive } from "@call-me-sensei/angelo/vite";
export default { plugins: [angeloLive()] };
```

```js
// your page
import { createAngeloLive } from "@call-me-sensei/angelo";

const loader = new GLTFLoader(); // yours, with your Draco / meshopt / KTX2 decoders
const live = createAngeloLive({ renderer, scene, loader });
const gltf = await loader.loadAsync("/models/chair.glb");
scene.add(gltf.scene);
live.track(gltf, "/models/chair.glb");
```

In Angelo, add the link from the chat bar's + menu (Game engine live link ▸ Add Angelo Link to a web project…).
Then **Alt-click** an object in your canvas and click **Edit chair.glb in Angelo**, or call
`live.edit("/models/chair.glb")`. Angelo asks you on a card, and from then on its edits show in your page.

### `createAngeloLive(options)`

| option | default | |
|---|---|---|
| `renderer` | | Your `WebGLRenderer`. Alt-click picking and captures use its canvas. |
| `loader` | required | Your `GLTFLoader` (three's or three-stdlib's). Live frames and saved files are decoded with it. |
| `scene` | | Your scene (optional). |
| `camera` | last camera rendered | The camera Alt-click picks with. |
| `capture` | `true` | Let Angelo's agent ask for a picture of the canvas. `false` answers "capture is off on this page". |
| `overlay` | `true` | The small status dot in the corner. `false` hides it. |
| `look` | `"ignore"` | Reserved: `"apply"` will take Angelo's lighting and environment. Angelo 1.0.10 sends no look yet, so nothing is applied either way. |

### `live.track(target, url)`

`target` is a GLTFLoader result (the best choice: its parser tells the SDK which object is which glTF node), its
scene, or a clone of that scene (`scene.clone()` or `SkeletonUtils.clone`). Track every copy you show: one parse of
each live frame serves them all. `track` returns `{ untrack() }`.

Other methods: `live.untrack(objectOrUrl)`, `live.edit(url, nodeName?)`, `live.on(event, listener)` (returns a function
that removes the listener), `live.stop()` (puts everything back as saved, then stops), and `live.state`.

### How updates keep your objects

- **Nothing is rebuilt and the page never reloads.** Every frame is applied as a diff. An object whose content didn't
  change keeps its geometry, material and textures, with no GPU upload and no event.
- **Your Object3Ds stay the same objects.** So do your references to them, their `userData`, the children you added,
  and the physics bodies and mixers that hold them. Bones are never replaced, so a playing `AnimationMixer` keeps
  running.
- **Angelo changes** an object's geometry, its local transform (only when Angelo moved it, so your own animation of an
  object Angelo didn't move continues) and its name.
- **Materials**: Angelo changes a material only when it changed it. A material you replaced stays yours. So does a
  change you made in place to a loaded material (a colour variant), until Angelo edits that material itself.
- **A deleted object** is removed. Children you had added to it move to the nearest remaining part of the asset,
  keeping their world position (event `removed`, plus one console warning).
- **New objects** arrive as the loader makes them (event `added`): apply your per-mesh setup there, for example
  `castShadow`.
- **Textures** are kept by content, so a picture the page already has is never decoded or uploaded again.
- **When Angelo stops** (or `live.stop()`), the SDK puts back exactly what it changed, in place, without reloading.
  If the dev server loses Angelo it waits 15 s before that, so a reconnect doesn't flicker.

### Events

`live.on(name, listener)`; the `angelo:` prefix is optional.

| event | detail |
|---|---|
| `state` | `{link: "off" \| "waiting" \| "live" \| "lost", message, session}` |
| `pending`, `opened` | `{asset}`: Angelo is asking on a card / the asset is open |
| `refused` | `{asset, code, message}` |
| `updated` | `{asset, version, frame: "full" \| "delta", instances, changed, timing: {fetch, parse, apply}}` |
| `renamed` | `{asset, object, from, to}` |
| `removed` | `{asset, name, moved: [your objects], to}` |
| `added` | `{asset, objects}` |
| `replaced` | `{asset, from, to}`: an object changed kind (a mesh became skinned), so a new Object3D took its place |
| `saved` | `{asset, save, instances, load()}`: `load()` gives the saved file through your loader (with animations) |
| `restored`, `ended` | `{asset, ...}`: the asset shows as saved again / Angelo stopped editing it |
| `status`, `resync` | Angelo's notices; a frame the page asked Angelo to send again in full |

## Security

- The dev-server routes answer only this computer (loopback peers), whatever the `Host` says. A phone on your LAN
  (`vite --host`) gets nothing, and the SDK switches itself off on a non-local page.
- Each route also checks the `Host` (a loopback name or one of your `server.allowedHosts`, with the dev server's
  port), that the request comes from the page's own origin (`Sec-Fetch-Site`/`Origin`), and a random path token made
  for each dev-server start. The routes never send CORS headers.
- Events travel over Vite's own HMR socket, which Vite protects with a token from 6.0.9 on. Don't turn on
  `legacy.skipWebSocketTokenCheck`; the plugin warns if you do.
- Angelo's endpoint record is read only when it is a small file owned by you, with no group or other permissions,
  and never through a link. Its token never leaves the dev server's process, and the token itself never crosses the
  socket.
- A capture is the WebGL canvas only, never the page's DOM. It is sent only while Angelo asks, at most one at a time,
  within 8 s, and only as a PNG of at most 8 MiB. Turn it off with `capture: false` here or `angeloLive({ capture:
  false })`.
- The package has no runtime dependencies and fetches nothing from the network.

## Production

The plugin runs on the dev server only. In a build, `@call-me-sensei/angelo` resolves to a no-op stub with the same
API (the `production` export condition, and the plugin enforces it in builds whatever `NODE_ENV` says). A production
build holds no Angelo route, socket or string. You can leave `createAngeloLive` and `track` in your code.

## Protocol

The wire contract between Angelo, the plugin and the page is [PROTOCOL.md](PROTOCOL.md).

## License

MIT © 2026 Hyperbond Studio Pte. Ltd.
