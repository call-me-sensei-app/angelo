.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Shared UI components (ui_kit)
*****************************

Angelo draws its own surfaces (cards, the chat, Preferences pages) in one visual language: card_kit's
navy-charcoal and brand teal, amber for "needs you" and paid work, coral for errors and destructive actions.
Every colour, size and radius comes from ``ui_tokens.py``. The shared controls are drawn once, in the
``ui_kit*`` modules, so every surface gets the same states, scaling and right-to-left behaviour.

Components
==========

========================  ===================================================================================
Module                    Components
========================  ===================================================================================
``ui_kit.py``             State model (``State``, ``state_for``), focus ring, outline, check mark, text that
                          keeps blending on, the plan dispatcher and ``register_hits`` for the chat canvas.
``ui_kit_status.py``      Status chip (running, done, failed, needs you, preview, paid), inline notice (info,
                          warn, error, success; actions; a collapsed "Details" for technical text), code block.
``ui_kit_controls.py``    Toggle switch (and settings row), segmented control (optional colour swatches): one
                          selection mark, the chosen segment filled flush in the track whose border is the
                          only one; every swatch the same rounded square with the same shadow rim, so a
                          dark and a light sample differ only in colour (the setup card's Look and the
                          Providers sheet use it through ``b.segmented``).
``ui_kit_field.py``       Text field, plain and secret: placeholder, caret, error line, mask, reveal, paste.
``ui_kit_select.py``      Dropdown: the closed control and an anchored, keyboard-navigable popover list.
``ui_kit_rows.py``        List row, letter tile, provider tile, the auto-hiding draggable scrollbar.
``ui_kit_builder.py``     ``card_kit.Builder`` methods for all of the above (a mixin).
``card_draw.py``          Button styles, including ``destructive`` (coral).
``canvas_controls.py``    The edge-clamped tooltip (``tip_rect``): flush with its control near an edge, inside
                          the region, below the control when there is no room above.
========================  ===================================================================================

Each module starts with its API reference. Every component is a stateless draw function taking a view (the
chat canvas module or ``card_host``'s view), a rectangle in region pixels, the UI scale and the pointer and
focus state. Hit testing stays with the host.

Using them
==========

* **In a card** (``card_host`` cards, Preferences pages built with ``card_kit.Builder``): call the Builder
  methods (``b.notice(...)``, ``b.toggle(...)``, ``b.text_field(...)``, ...). They lay the component out,
  register its hits and Tab stops, and the plan draws it with the frame's hover, pressed and focus state. An
  open dropdown is an overlay: drawn last and hit first. A card that edits text keeps a ``FieldState`` and
  implements ``key_event(event, host)``; ``card_host`` hands it each key press before its own Tab, Return and
  Escape handling.
* **On the chat canvas**: draw with the component's function and ``ui_kit.state_for(state, action, value)``,
  and add its hits with ``ui_kit.register_hits(state, hits)``: ``canvas_controls`` still tracks hover, press
  and Tab focus for them but leaves their look to the component.
* Strings reach the kit already translated (``ui_text.iface_``); the kit translates its own few labels
  (chip names, Copy, Copied, Details, Paste) at display time.

Right to left
=============

Layout is written left to right and mirrored by ``canvas_rtl``. Code, typed text and secrets read left to
right in every language (their frame and buttons still mirror). Check marks are symbols and never mirror.

Testing
=======

Pure logic (masking, editing keys, layout maths, hits, the scrollbar and the tooltip placement) is host-tested
in ``test_native_ui_kit.py``. ``test_blender_ui_kit_gallery`` shows every component in every state in a real
``card_host`` card, drives it with real events and screenshots it in English, Arabic and a narrow card.
