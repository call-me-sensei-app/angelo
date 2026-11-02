Angelo's agent runtime: a modified build of OpenAI Codex
=======================================================

Angelo bundles a build of OpenAI Codex 0.163.0 (https://github.com/openai/codex, tag
rust-v0.163.0) and runs it as its agent runtime: the app-server behind ChatGPT and the other
Codex connections.

  macOS:    Angelo.app/Contents/Helpers/codex and codex-code-mode-host
  Windows:  agent-runtime\codex.exe and codex-code-mode-host.exe, with codex-command-runner.exe and
            codex-windows-sandbox-setup.exe where present

License
-------

OpenAI Codex is licensed under the Apache License, Version 2.0: LICENSE.txt in this folder. Its NOTICE
file is NOTICE.txt. The third-party software these programs contain, and the terms each comes under, are
listed in THIRD-PARTY-NOTICES.txt.

Modification
------------

The codex program (codex.exe on Windows) was modified for Angelo. codex-code-mode-host and, on Windows,
codex-command-runner.exe and codex-windows-sandbox-setup.exe are OpenAI's, unchanged, from the same
release; on Windows they keep OpenAI's own signature. The modification adds four optional settings, one
notification and more detail to one kind of error message:

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

Without the four settings, Codex behaves as released apart from that error detail and the notification
(which clients that do not read it ignore). Its Cargo.lock also takes
the root-store crates webpki-roots 1.0.10, webpki-root-certs 1.0.10 and rustls-webpki 0.103.16 (October
2026: emSign ECC Root CA - G3 removed and eight compromised eMudhra intermediate keys refused): upstream's
own change (openai/codex#55616) and the version upstream's release line carries. Everything else is
OpenAI's code, unchanged.

The modified source is the branch angelo/rust-v0.163.0-hold-images of
https://github.com/call-me-sensei-app/codex, a fork of https://github.com/openai/codex based on the tag
rust-v0.163.0: commit 76996e2e1290de41e93a0d0a6ddd122d95f18bf3, tag angelo-rust-v0.163.0-hold-images. Its tree
is identical to commit 26765206242546005a9ad364734d9a2618aed035, the one these programs were built from. ANGELO.md at the root of
that branch describes the changes and their tests.

Angelo returns to unmodified OpenAI Codex once Codex itself offers an equivalent
(https://github.com/openai/codex/issues/21796).
