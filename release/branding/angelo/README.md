<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Angelo — Curious Raven

The app is named **Angelo**. Its mark is the **teal raven robot head** on a
transparent background. `selection.json` records the active choice and
`design-brief.json` the design, palette and wordmark. `angelo-raven-teal-v2.png`
is the production, head-only transparent PNG; the `angelo-mark-*.svg` vectors
are path-based reductions of it. The dark visor belongs to the head and remains
present.

The Angelo name, the raven mark and the wordmark are trademarks. The GPL that
covers this repository grants no right to use them; see
[TRADEMARKS.md](../../../TRADEMARKS.md).

## Wordmark

The approved wordmark is **Option B**: the word *Angelo* whose triangular "A" is
faceted in the mark's teal palette (a low-poly nod), followed by "ngelo" set as in
the approved source: the project's variable Inter at weight 500 with -0.015 em
letter spacing (optical size 32, Chromium's automatic choice at this size).

**Deliberate glyph substitution:** the approved source used web Inter, whose
default "l" is a plain stem, but Blender's bundled Inter has only a tailed "l".
The wordmark's "l" is therefore a plain rectangle: the weight-500 stem's left
edge, width and ascender, ending on the baseline, with a right side bearing equal
to its left one. The "o" moves accordingly and the -0.015 em tracking still
applies. `plainL` in `angelo_letters.mjs` performs this on every regeneration.

- `angelo-wordmark.svg`: colour lockup for dark backgrounds (colour raven,
  faceted teal A, white letters).
- `angelo-wordmark-white.svg`: single-colour variant; the facets keep their
  light-to-dark order as opacity. It is also the compiled About wordmark
  (`release/datafiles/icons_svg/blender_logo_large.svg`, tinted by the theme).
- `angelo-splash.svg`: splash source, rendered to `release/datafiles/splash.png`
  (see "Splash").

Every wordmark is plain paths without fonts, images in the letters, or clip
paths, so NanoSVG renders it. The A's geometry and facet colours live in
`build_files/utils/angelo_wordmark.mjs`. The letter outlines are committed in
`angelo-wordmark-letters.svg`: neither the canvas converter nor Blender can
select a variable-font instance, so `build_files/utils/angelo_letters.mjs` lays
the text out in headless Chromium (Playwright) and copies the glyph paths from
its PDF output. It records the font's SHA-256; the exporter refuses a changed
font until the letters are regenerated.

## Splash

The splash keeps the lockup (the approved bitmap raven, then the coloured
wordmark, at their previous size and place) on **Faceted light**, a background
built from the wordmark's own teals: two planes meet on a seam that runs parallel
to the A's left leg, so the raven sits on the lit plane and the wordmark on the
calm one. A bloom behind the raven, a vignette, a soft shadow from the raven's
own silhouette and a fine grain give it depth; the grain also keeps the dark
gradients from banding. `build_files/utils/angelo_splash.mjs` holds the whole
composition and `angelo_branding.mjs` renders it.

What the composition has to respect, because Blender draws over and around it
(`wm_splash_screen.cc`):

- **Size.** The image is drawn 45 widget units wide and scaled to fit with a box
  filter: 495 px at interface scale 1, 990 px on a Retina display, more with a
  larger interface or display scale. The PNG is 2000 x 1000 (the source's
  1000 x 500 units at twice the density; Blender's own splash is 1000 x 500), so
  the lockup is scaled down, never up, until the interface scale passes 4.
- **Version label.** "Angelo 1.0 · Blender 5.2.2 LTS" is drawn in white at the
  top right, so that corner stays the darkest part: white on its lightest pixel
  measures 15.7:1.
- **Edges.** Blender rounds the top corners. The bottom 120 units settle into
  `#181818`, the default theme's menu colour, so the image and the Quick Setup
  or file list below read as one surface.

The splash is a PNG, never drawn by NanoSVG, so its source may use gradients and
filters. It stays font-free and self-contained, and its grain has a fixed seed:
the export is byte-for-byte reproducible. `coverage.json` records the PNG's size
and SHA-256; `test_native_branding.py` compares them with the committed file (or
its Git LFS pointer), so a changed source without a re-export fails.

## Window chrome

The operating system's title bar and the top bar (the application menu and the
workspace tabs) are `#076573`: the A's darkest facet, `#087787`, at 85% of its
brightness. The exact facet colour does not carry the theme's menu text or the
macOS title at WCAG AA; the tint does. The colour is theme data, set for the
top bar in the default theme and in Angelo Light; the rules, the platform
notes and the measured contrasts are in "Window chrome" in
`doc/angelo/app_identity.rst`.

## Installer artwork

The disk image's Finder window and the Windows installer are lit like the
splash. `build_files/utils/angelo_installer_scenes.mjs` composes `facetedLight`
of `angelo_splash.mjs` again for each canvas (the same wall, planes, bloom,
vignette and grain, with the seam placed for that picture), and every flat shape
is a facet colour from `FACETS` in `angelo_wordmark.mjs`, so a change to the
splash's light or to the A's palette changes the installers with it.
`angelo_installer_art.mjs` renders the files. Their sizes and formats are what
CPack and WiX consume and are not part of the design:

| File | Size and format | Consumer |
| --- | --- | --- |
| `release/darwin/background.tif` | 1080 x 700 at 144 dpi (540 x 350 Finder points), LZW TIFF | `CPACK_DMG_BACKGROUND_IMAGE`, with `ANGELO_DMG_STYLED=ON` |
| `release/darwin/buildbot/background.tif` | the same file | nothing in this repository (upstream's build bot read it) |
| `release/windows/installer_wix/WIX_UI_DIALOG.png` | 493 x 312, 8-bit RGB PNG | `CPACK_WIX_UI_DIALOG`: the welcome and finish pages |
| `release/windows/installer_wix/WIX_UI_BANNER.bmp` | 493 x 58, 24-bit BMP | `CPACK_WIX_UI_BANNER`: the top of every other page |

Each host draws its own text over the picture, in a colour the artwork does not
choose, so each picture is drawn around that text:

- **DMG window.** Finder draws an icon's name in black in a window that has a
  background picture, in the light and the dark appearance alike (what that
  rests on is in the document named below). Each of the two names sits on a
  plaque whose middle facet is the A's `#0b8a99`: black reads on it at 5.10:1,
  and white, should a Finder draw it, still at 4.10:1. Three chevrons with the
  A's apex angle point from the app to the Applications link. The picture
  carries the wordmark alone, because the app's own icon is the raven; the seam
  passes between the two icons and beside the A, as on the splash.
- **WiX dialog.** The left 164 pixels are the panel WiX's text leaves free: the
  raven on the lit plane above the seam, the wordmark below it. The rest is
  white for the black title and description, and settles into the dialog colour
  (`#f0f0f0`) where the finish page's check box, which paints that colour behind
  itself, and the button row follow.
- **WiX banner.** A thin strip cannot hold the splash. The text gets white
  paper, and the right end is a cap of the night wall with the raven, cut on the
  A's lean and edged with three of its facets.

The exporter measures the WCAG contrast of that text on the pixels it writes,
refuses to export below 4.5:1, and records the sizes, checksums and measured
contrasts under `installer_art` in `coverage.json`, which
`test_native_branding.py` compares with the committed files (or their Git LFS
pointers). The window layout, the text positions and the measurements are in
"Installer artwork" in `doc/angelo/app_identity.rst`.

## Regenerating

Packaging never redesigns or recolors the selected master.

Regenerate source-native branding and packaging on macOS, **in this order**:

```sh
# Only when the font, weight or spacing changes: refresh the letter outlines.
node build_files/utils/angelo_letters.mjs
# First refresh the selected app PNGs (the X11 export consumes them).
python3 build_files/utils/angelo_icons.py
# Refresh UI vectors, wordmarks, splash, document PNGs, MSIX and native window assets.
node build_files/utils/angelo_branding.mjs
# Package the now-current document icons and update the checksum manifest.
python3 build_files/utils/angelo_icons.py
# Last: the Windows installer banner/dialog and the DMG background, from the wordmark exported above.
node build_files/utils/angelo_installer_art.mjs
```

The Node scripts use bundled sharp (and Playwright with its Chromium for the
letters) or `ANGELO_NODE_MODULES` (or a deprecated spelling). Every step
is deterministic with the same dependency versions: unchanged artwork and letters
are rewritten byte-for-byte.
`exports/` contains the master, RGBA size variants, macOS app/document ICNS and
Windows app/document ICOs. Monochrome UI glyphs and the Wayland color mark are
native path-based reductions of the selected raven design, not embedded bitmap
stand-ins. The color mark retains its teal helmet, dark visor, mint eyes and coral
beak. The white UI reduction uses negative space for the visor.

The chat's waiting animation draws the colour mark as GPU triangles, not a
picture: `scripts/addons_core/angelo_agent/marble_mark.py` keeps a verbatim copy
of `angelo-mark-color.svg`'s paths and gradients (the installed app does not
ship `release/`) and triangulates them once per process. `test_native_marble_waiting`
fails when the two differ, so a change to the mark updates that copy with it.

`exports/manifest.json` records output checksums; `coverage.json` records source
bindings. The source-wired surfaces include splash, top-left menu, About wordmark,
file glyphs, macOS app/document icons, Windows installer/MSIX resources, Linux
desktop and symbolic icons, X11 and Wayland. Rebuild the fork to refresh compiled
branding.

The macOS bundle is `Angelo.app` (identifier from `scripts/modules/angelo_identity.json`, currently `studio.hyperbond.angelo`) and
references `Angelo.icns` without the obsolete Blender Liquid Glass catalog
override. The development bundle lives under `../build-angelo-native/bin/Angelo.app`;
installed system applications are not modified. Windows and Linux packaging
require platform acceptance testing. Upstream Blender attribution, APIs and file
formats are unchanged.
