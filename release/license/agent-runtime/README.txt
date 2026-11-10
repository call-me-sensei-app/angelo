Angelo's agent runtime: a modified build of OpenAI Codex
=======================================================

Angelo bundles a build of OpenAI Codex 0.166.0 (https://github.com/openai/codex, tag
rust-v0.166.0) and runs it as its agent runtime: the app-server behind ChatGPT and the other
Codex connections.

  macOS:    Angelo.app/Contents/Helpers/codex and codex-code-mode-host
  Windows:  agent-runtime\codex.exe and codex-code-mode-host.exe, with codex-command-runner.exe and
            codex-windows-sandbox-setup.exe where present

License
-------

OpenAI Codex is licensed under the Apache License, Version 2.0: LICENSE.txt in this folder. Its NOTICE
file is NOTICE.txt. The third-party software these programs contain, and the terms each comes under, are
listed in THIRD-PARTY-NOTICES.txt. That generated list conservatively covers normal source-package
dependencies; it does not assert that every dependency survives linking in every named binary. The retained
command runner remains the exact upstream release executable, with the origin and signature stated below.

Modification
------------

The codex program (codex.exe on Windows) was modified for Angelo. It, codex-code-mode-host and the
Windows sandbox setup helper are built from Angelo's fork. On Windows these rebuilt executables require
Angelo's signature. Only the exact upstream codex-command-runner.exe is retained unchanged with
OpenAI's own signature, verified valid. The fork refreshes webpki-roots to 1.0.11; rustls-webpki remains
0.103.16. Its application changes add five optional settings,
two notifications, one request field and more detail to one kind of error message:

  * cacheAffinityKey, an experimental parameter of Codex's app-server (1 to 64 visible ASCII characters)
    on thread/start, thread/resume and thread/fork. When it is set, the thread's model requests send it
    as the prompt cache key and the session-id header in place of the thread's own ID, so new
    conversations with the same opening can reuse a cached prompt, and durable threads keep it in their
    saved session.
  * capabilities.request_compression on a custom model provider ("zstd" or "unsupported"). With "zstd",
    request bodies to that provider are compressed exactly as Codex compresses them for OpenAI's own
    backend; Angelo sets it only for a team gateway that says it decodes them.
  * In code mode, when the JavaScript a model runs fails, the error the model reads also names the line
    and column of the failure, shows that source line and marks the column. The position comes from the
    error's stack or, for a syntax error, from parsing the code with tree-sitter's JavaScript grammar. The
    original error text and a successful run's output are unchanged.
  * code_mode_hold_dynamic_tools, a setting listing app tools whose calls wait for something (Angelo lists
    every one of its tools: any call can wait for the person's approval, its scene or a job, such as
    angelo_workers_status for a parallel-edit worker). In code mode, when the JavaScript a model
    runs is still waiting for the answer to a listed tool as its yield time ends, Codex keeps waiting for it
    instead of handing the model a "still running" result to poll with wait. It hands over the result once
    the code finishes, new output arrives, the code yields with no such call outstanding, the turn is
    interrupted or steered, or after 35 minutes. Unlisted tools, Codex's own tools and a yield the code asks
    for itself (yield_control()) are unchanged.
  * code_mode_forward_dynamic_tool_images, a setting listing app tools whose results can carry pictures. In
    code mode, a listed tool's pictures reach the model even when the JavaScript the model runs does not
    pass them on with image(): each is added to the code's next result, once per turn; a picture the code
    already passed on is not sent again, and in the text the code sees, a delivered picture's data URL
    reads [image delivered]. A history rewrite (compaction, rollback) lets a picture be sent again, and a
    model without image input is unaffected.
  * turn/modelInputImages, an app-server notification. Once the model provider shows it consumed a model
    request (its first output, or the request's completion), Codex names each picture that request newly
    carried by the SHA-256 of its bytes (and of the bytes the tool returned, when Codex re-encoded it), so
    Angelo can tell by content which pictures the model received. It carries hashes only.
  * modelStep, a field of the app-server's item/tool/call request. A turn numbers its model requests from
    0, and each app tool call names the request that wrote it (for a call made by JavaScript the model
    runs in code mode, the request that wrote that code, even after it handed control back to the model),
    the turn's latest request and the code's cell. Angelo uses it to tell whether the model had seen a
    result before it wrote a call.
  * turn/modelRequestStarted, an app-server notification naming each model request of a turn by that
    number as the turn sends it, before the request's input is built.
  * serial_dynamic_tools, a setting listing app tools whose calls run one at a time, as every app tool's
    did before Codex 0.164 (Angelo lists every one of its tools, which it answers in order).
  * On every new input, app-server checks that the conversation's model provider is still the one the
    configuration selects. It now builds the whole configuration for that check only when the configuration
    has changed since the last check for the same folder, instead of on every input; the outcome is the same.

Without the five settings, Codex behaves as released apart from that error detail, the notifications and
the request field (which clients that do not read them ignore). Everything else is OpenAI's code,
unchanged apart from the root-store update described above. Rebuilding the host and sandbox setup
also refreshes their compiled-in Mozilla trust anchors; their source-level program behavior is unchanged.

The modified source is the branch angelo/rust-v0.166.0-call-steps of
https://github.com/call-me-sensei-app/codex, a fork of https://github.com/openai/codex based on the tag
rust-v0.166.0: commit 0ffcb4dbb1e2e310dd375d50911ea667c22f2210. Rebuilt executables use that source plus a reviewed
Cargo.lock overlay that normalizes only local workspace versions to the already-declared 0.166.0.
No external dependency is changed by that overlay. ANGELO.md at the root of
that branch describes the changes and their tests.

Angelo returns to unmodified OpenAI Codex once Codex itself offers an equivalent
(https://github.com/openai/codex/issues/21796).
