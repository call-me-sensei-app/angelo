<!--
Keep this document short & concise,
linking to external resources instead of including content in-line.
See 'release/text/readme.html' for the end user read-me.
The product name, vendor and links are defined once in
'scripts/modules/angelo_identity.json' (see doc/angelo/app_identity.rst).
-->

Angelo by Call Me Sensei
========================

Angelo is a native AI-assisted 3D creation app, based on [Blender](https://www.blender.org),
the free and open source 3D creation suite. It keeps Blender's modeling, rigging, animation,
simulation, rendering, compositing, motion tracking and video editing tools, and adds a
built-in agent that works directly in the scene.

Angelo is a separate application: it installs and runs side by side with a stock Blender
install and keeps its own settings, caches and file associations.

Project Pages
-------------

- [Angelo Website](https://angelo.studio/)
- [Blender Reference Manual](https://docs.blender.org/manual/en/latest/index.html) (for the Blender features)

Releases
--------

Release notes for every version are at [angelo.studio/releases](https://angelo.studio/releases),
one anchor per version (for example [1.0.2](https://angelo.studio/releases#1.0.2)), and in
[CHANGELOG.md](CHANGELOG.md).

Development
-----------

- [Agent and contributor instructions](AGENTS.md)
- [Contributing](CONTRIBUTING.md) (build, validation and engineering rules)
- [Subsystem documents](doc/angelo/)

License
-------

Angelo is based on Blender. Like Blender, the program as a whole is licensed under the
[GNU General Public License, version 3 or later](LICENSE). Each file carries its own SPDX
identifier: Angelo's own files are mostly `GPL-2.0-or-later` (some tests `Apache-2.0`), all
compatible with the whole. `release/license/` holds every license text, and
[blender.org/about/license](https://www.blender.org/about/license) explains Blender's licensing.

- [Trademarks](TRADEMARKS.md): the Angelo name and logos are not licensed under the GPL;
  distributions of modified builds use their own name and logo.
- [Security](SECURITY.md): how to report a vulnerability privately.
- [Code of conduct](CODE_OF_CONDUCT.md).
- Contributions are accepted under the [Developer Certificate of Origin](DCO): sign off every
  commit with `git commit -s`.
