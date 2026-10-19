Windows Store (MSIX) Packaging
==============================

`AppxManifest.xml.template.in` is configured by CMake from the application identity
(`scripts/modules/angelo_identity.json`, see `doc/angelo/app_identity.rst`) into
`<build>/release/windows/msix/AppxManifest.xml.template`. The packaging step then fills in
the remaining `[BRACKETED]` values (`[PACKAGETYPE]`, `[PUBLISHER]`, `[VERSION]`,
`[ARCHITECTURE]`, `[LTSORNOT]`) and bundles `Assets/`.

The package identity name and publisher must match what is reserved for Angelo in the
Microsoft Store (Partner Center); until then the identity file holds placeholders.

The manifest starts `<windows.install_folder>\<launcher>` inside the package, so the packaging
step must place the install tree (the one the MSI installs, with `Angelo.exe` and
`Angelo-launcher.exe`) in a folder of that name. The `ANGELO` application id, the `angelo://`
protocol and the `Angelo-launcher.exe` execution alias are claimed by this package only; the
`.blend` file type association is an "Open with" choice and does not displace another app's.
