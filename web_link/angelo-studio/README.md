<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: MIT -->

# angelo-studio

An alias for [`@call-me-sensei/angelo`](https://www.npmjs.com/package/@call-me-sensei/angelo): Angelo's live link
into your own Three.js page. It re-exports that package, subpath for subpath, and keeps its version.

```bash
npm i -D angelo-studio
```

```js
import { angeloLive } from "angelo-studio/vite";        // vite.config.js
import { createAngeloLive } from "angelo-studio";        // your page
```

Everything else, including how production builds get a no-op stub, is in the scoped package's README.

MIT © 2026 Hyperbond Studio Pte. Ltd.
