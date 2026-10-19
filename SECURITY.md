<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Security Policy

## Supported versions

| Version | Security fixes |
| --- | --- |
| 1.0.x | Yes |
| Earlier development builds | No |

Fixes ship in a new 1.0.x release. Update to the latest 1.0.x release before
reporting, if you can.

## Reporting a vulnerability

Please report security problems **privately** by email to
[security@angelo.studio](mailto:security@angelo.studio). Do not open a public
issue, pull request or discussion for them.

Include what you can of:

- what the problem is and what an attacker could do with it;
- the Angelo version, operating system and, if it matters, the AI provider or
  connection involved;
- steps or a file that reproduce it;
- whether the problem is already public or known to others.

Do not include real credentials, API keys or other people's data. If you need
to show that a secret leaks, use one you created for the test and revoke it
afterwards.

## What happens next

1. We acknowledge your report, normally within three working days.
2. We check whether we can reproduce it, and tell you what we found and how
   serious we think it is.
3. We work on a fix and keep you updated on progress, including any delay.
4. We release the fix and publish an advisory that describes the problem. We
   credit you in it unless you ask us not to.

Please keep the problem confidential until the fix is released, or until we
agree on a disclosure date with you.

## Scope

In scope: Angelo's own code and releases in this repository, including the
built-in agent and its tools, provider connections and how they store
credentials, the external command-line transport, checkpoints, the update
check, crash reports and the installers.

Angelo is built on Blender. If a problem is in Blender code that Angelo uses
unchanged, report it to us and, if you can, also to the Blender project through
its own security process. Problems in an AI provider's service belong to that
provider.
