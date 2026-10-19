# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pages Angelo's sign-in callback shows in the browser, in the Angelo console's own design.

The console's pages outside an organization (``apps/console`` FullPage: the
wordmark above one panel on the "gallery after dark" wall, the serif title,
muted text) are the model, so the sign-in reads as one journey from the
console's allow screen back to the app. Dark is the console's palette; light
is the brand's sketchbook paper. Fonts fall back to the console's own system
stacks, because the page loads nothing.

Every page is self-contained: the wordmark is inline SVG (``team_page_art``),
the style is inline and allowed by its hash in a strict Content-Security-Policy
(no script, no fetch, no form, no framing), and no code, token or state ever
appears in it. Text comes from templates (``ui_text.n_``) that the app
translates on Blender's main thread before the listener starts
(``translations``); the listener thread only fills fields, escaped.

Standard library only; no bpy.
"""

import base64
import hashlib
import html
from dataclasses import dataclass

from . import rtl_text
from .team_page_art import WORDMARK
from .ui_text import n_

RTL_LANGUAGES = frozenset({"ar", "fa", "he", "ur", "ckb", "ps"})


@dataclass(frozen=True)
class State:
    status: int
    tone: str       # "ok", "warn" or "error": the eyebrow's colour
    eyebrow: str
    title: str
    body: str
    next: str
    action: str = ""   # A line before ``next``: what the person should do first (the admin contact).


SIGNED_IN = n_("Signed in")
NOT_SIGNED_IN = n_("Not signed in")
DONE = n_("Done")
STATES = {
    "signed_in": State(200, "ok", SIGNED_IN, n_("You're signed in"),
                       n_("Angelo on {device} can now use {org}."),
                       n_("Return to Angelo to continue. You can close this tab.")),
    "no_seat": State(200, "warn", SIGNED_IN, n_("You're signed in, without a seat"),
                     n_("Angelo on {device} is signed in to {org}, but you don't have a seat, so the team's AI "
                        "connections can't be used yet."),
                     n_("Then return to Angelo. You can close this tab."), n_("{contact}")),
    "refused": State(403, "error", NOT_SIGNED_IN, n_("This team isn't allowed here"),
                     n_("{reason}"),
                     n_("Return to Angelo and choose Sign in with the right account. Nothing was shared.")),
    "denied": State(200, "error", NOT_SIGNED_IN, n_("Sign-in cancelled"),
                    n_("Angelo wasn't given access to your team."),
                    n_("To try again, return to Angelo and choose Sign in. You can close this tab.")),
    "expired": State(400, "error", NOT_SIGNED_IN, n_("This sign-in expired"),
                     n_("It took too long to finish, or this sign-in was already used. Nothing was shared."),
                     n_("Return to Angelo and choose Sign in to start again.")),
    "unreachable": State(502, "error", NOT_SIGNED_IN, n_("Angelo couldn't finish signing in"),
                         n_("It couldn't reach the Angelo console ({console}) to finish. Nothing was shared."),
                         n_("Check your network, then return to Angelo and choose Sign in.")),
    "error": State(502, "error", NOT_SIGNED_IN, n_("Angelo couldn't finish signing in"),
                   n_("{reason}"),
                   n_("Return to Angelo and choose Sign in to try again.")),
    "mismatch": State(400, "error", NOT_SIGNED_IN, n_("This sign-in doesn't match"),
                      n_("This page came from an older sign-in or from another app, so Angelo ignored it. Nothing "
                         "was shared."),
                      n_("Return to Angelo. If a sign-in is waiting there, finish it in the newest tab; otherwise "
                         "choose Sign in again.")),
    "already_used": State(200, "ok", DONE, n_("This sign-in is already complete"),
                          n_("Angelo has already received this sign-in."),
                          n_("Return to Angelo. You can close this tab.")),
    "waiting": State(200, "warn", n_("Finishing", "Angelo"), n_("Finishing your sign-in"),
                     n_("Angelo is still finishing your sign-in."),
                     n_("Return to Angelo to see the result. You can close this tab.")),
    "not_found": State(404, "error", n_("Not found"), n_("Nothing here"),
                       n_("This address only completes an Angelo sign-in."),
                       n_("Return to Angelo. You can close this tab.")),
}
DEVICES = n_("Manage your devices")
FOOTNOTE = n_("Angelo · {console}")
THIS_COMPUTER = n_("this computer")
YOUR_TEAM = n_("your team")
CONTACT_ADMIN = n_("Contact your admin for access.")
WORDMARK_LABEL = n_("Angelo")

STYLE = """
:root{color-scheme:dark light;--bg:#0e0c0b;--panel-a:rgba(33,30,27,.78);--panel-b:rgba(20,18,16,.78);
--line:rgba(243,236,226,.08);--line-2:rgba(243,236,226,.14);--text:#f3ece2;--text-2:#bdb2a3;--text-3:#82786b;
--title:#fbf6ee;--spot:rgba(255,217,163,.08);--ok:#a1f4d6;--warn:#ffd9a3;--error:#ff9b80;--link:#a1f4d6;
--shadow:inset 0 1px 0 rgba(255,255,255,.04),0 24px 60px -28px rgba(0,0,0,.7);
--art:"Fraunces","Iowan Old Style",Georgia,serif;--sans:"Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
--mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:light){:root{--bg:#f4ede0;--panel-a:rgba(255,252,246,.92);--panel-b:rgba(250,245,236,.92);
--line:rgba(74,58,34,.14);--line-2:rgba(74,58,34,.22);--text:#26221d;--text-2:#574f44;--text-3:#7a7062;--title:#1d1a17;
--spot:rgba(240,180,41,.12);--ok:#087787;--warn:#8a5a00;--error:#b4472d;--link:#087787;
--shadow:inset 0 1px 0 rgba(255,255,255,.6),0 24px 60px -32px rgba(74,58,34,.35)}}
*{box-sizing:border-box}
html{background:var(--bg);-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;min-height:100vh;min-height:100svh;background:radial-gradient(46% 42% at 50% 12%,var(--spot),transparent 72%),var(--bg);
color:var(--text);font-family:var(--sans);font-size:1rem;line-height:1.55;-webkit-font-smoothing:antialiased}
main{display:grid;place-content:center;justify-items:center;gap:28px;min-height:100vh;min-height:100svh;padding:48px 16px}
.mark{width:140px;color:var(--title)}
.mark svg{display:block;width:100%;height:auto}
.panel{display:grid;gap:16px;width:min(440px,calc(100vw - 32px));padding:28px;border-radius:24px;
background:linear-gradient(180deg,var(--panel-a),var(--panel-b));box-shadow:var(--shadow),inset 0 0 0 1px var(--line)}
.eyebrow{display:flex;align-items:center;gap:10px;margin:0;font-family:var(--mono);font-size:.68rem;letter-spacing:.08em;
text-transform:uppercase;color:var(--text-3)}
.eyebrow::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor;flex:none}
.ok{color:var(--ok)}.warn{color:var(--warn)}.error{color:var(--error)}
h1{margin:0;font-family:var(--art);font-size:1.7rem;font-weight:560;line-height:1.08;letter-spacing:-.015em;
word-spacing:.04em;color:var(--title);text-wrap:balance}
p{margin:0}
.text{color:var(--text-2);text-wrap:pretty}
.text b{color:var(--text);font-weight:600;overflow-wrap:anywhere}
.next{display:grid;gap:12px;padding-top:16px;border-top:1px solid var(--line);font-size:.9rem;color:var(--text)}
.devices{font-size:.84rem}
.action{font-weight:600}.action b{display:inline-block;max-width:100%;overflow-wrap:anywhere;font-weight:600}
a{color:var(--link);text-decoration:underline;text-decoration-color:var(--line-2);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
a:focus-visible{outline:2px solid var(--link);outline-offset:3px;border-radius:6px}
.footnote{font-family:var(--mono);font-size:.64rem;letter-spacing:.06em;color:var(--text-3)}
@media (max-width:480px){main{gap:22px;padding:32px 12px}.mark{width:120px}.panel{padding:22px 18px;border-radius:20px}
h1{font-size:1.5rem}}
@media (prefers-reduced-motion:no-preference){.panel{animation:rise .6s cubic-bezier(.16,1,.3,1) both}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}}
""".strip()
STYLE_HASH = base64.b64encode(hashlib.sha256(STYLE.encode("utf-8")).digest()).decode("ascii")
CSP = ("default-src 'none'; style-src 'sha256-%s'; img-src 'none'; base-uri 'none'; form-action 'none'; "  # i18n: HTTP header value
       "frame-ancestors 'none'" % STYLE_HASH)
HEADERS = {"Content-Security-Policy": CSP, "Cache-Control": "no-store", "Pragma": "no-cache",
           "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
           "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin"}


def templates():
    """Every template a page may show, including the account's and policy's messages a page quotes."""
    from . import managed_policy, team_errors
    output = {DEVICES, FOOTNOTE, THIS_COMPUTER, YOUR_TEAM, CONTACT_ADMIN, WORDMARK_LABEL}
    for state in STATES.values():
        output.update(part for part in (state.eyebrow, state.title, state.body, state.next, state.action) if part)
    for module in (managed_policy, team_errors):
        output.update(value for name, value in vars(module).items() if name.isupper() and isinstance(value, str)
                      and not name.startswith(("_", "DOMAIN", "MAC_", "WINDOWS_", "LINUX_", "TEST_", "INVALID")))
    return output


def _angelo_first(translate, template):
    """As ``ui_text``: a template marked with the "Angelo" context keeps Angelo's meaning; a browser shapes Arabic itself."""
    translated = translate(template, "Angelo")
    translated = translated if translated != template else translate(template)
    return rtl_text.to_logical(translated) if rtl_text.is_visual(translated) else translated


def translations(translate, locale=""):
    """``(catalog, lang, direction)`` on the main thread: ``translate`` is ``bpy.app.translations.pgettext_iface``."""
    catalog = {template: _angelo_first(translate, template) for template in templates()}
    language = (locale or "en").replace("_", "-").split("@")[0] or "en"
    return catalog, language, "rtl" if language.split("-")[0].lower() in RTL_LANGUAGES else "ltr"


class Pages:
    """Renders the states with a fixed catalog; safe to call from the listener's threads."""

    def __init__(self, console_url, *, catalog=None, language="en", direction="ltr"):
        self.console_url = console_url
        self.catalog, self.language, self.direction = dict(catalog or {}), language, direction

    def _(self, template):
        return self.catalog.get(template, template)

    def text(self, value):
        """A ``display_text.Text`` (or plain text) in the page's language, from the catalog."""
        template = getattr(value, "template", None)
        if template is None:
            return str(value)  # User data (an organization or device name) is never translated.
        fields = {key: self.text(item) for key, item in value.fields.items()}
        return self._fill(template, fields)

    def _fill(self, template, fields):
        try:
            return self._(template).format(**fields)
        except (KeyError, IndexError, ValueError):
            return template.format(**fields)  # A translation with a wrong placeholder falls back to English.

    def render(self, kind, **fields):
        """``(status, headers, body bytes)`` of one state. Fields are escaped; ``reason``/``contact`` are text."""
        state = STATES.get(kind, STATES["not_found"])
        host = self.console_url.split("://", 1)[-1]
        values = {key: html.escape(self.text(value)) for key, value in fields.items() if value}
        contact = fields.get("contact")
        if getattr(contact, "fields", None):  # "Contact your admin for access: <b>it@…</b>", the address kept whole.
            values["contact"] = self._fill(contact.template, {key: "<b>%s</b>" % html.escape(self.text(item))
                                                              for key, item in contact.fields.items()})
        values.setdefault("device", html.escape(self._(THIS_COMPUTER)))
        values.setdefault("org", html.escape(self._(YOUR_TEAM)))
        values.setdefault("console", html.escape(host))
        values.setdefault("contact", html.escape(self._(CONTACT_ADMIN)))
        values.setdefault("reason", "")
        bold = dict(values, device="<b>%s</b>" % values["device"], org="<b>%s</b>" % values["org"],
                    console="<b>%s</b>" % values["console"])
        devices = ('<p class="devices"><a href="%s/account/devices" rel="noreferrer">%s</a></p>'  # i18n: HTML markup; the link text is translated
                   % (html.escape(self.console_url, quote=True), html.escape(self._(DEVICES)))
                   if kind in {"signed_in", "no_seat"} else "")
        title = html.escape(self._(state.title))
        page = (
            '<!doctype html><html lang="%s" dir="%s"><head><meta charset="utf-8">'  # i18n: HTML page markup; every text is translated through the catalog
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="color-scheme" content="dark light"><meta name="referrer" content="no-referrer">'
            '<meta name="robots" content="noindex"><title>%s · Angelo</title><style>%s</style></head>'
            '<body><main><div class="mark" role="img" aria-label="%s">%s</div>'
            '<section class="panel" aria-labelledby="title">'
            '<p class="eyebrow %s">%s</p><h1 id="title">%s</h1><p class="text" role="status">%s</p>'
            '<div class="next">%s<p>%s</p>%s</div></section>'
            '<p class="footnote">%s</p></main></body></html>'
        ) % (html.escape(self.language, quote=True), self.direction, title, STYLE, html.escape(self._(WORDMARK_LABEL)),
             WORDMARK, state.tone, html.escape(self._(state.eyebrow)), title, self._fill(state.body, bold),
             '<p class="action">%s</p>' % self._fill(state.action, values) if state.action else "",  # i18n: HTML markup around a translated text
             self._fill(state.next, values), devices, self._fill(FOOTNOTE, values))
        return state.status, dict(HEADERS), page.encode("utf-8")
