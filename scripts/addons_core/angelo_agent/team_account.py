# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed-in Angelo team account: tokens, renewal, sign-out and the console API.

Contract: doc/angelo/ai_providers.rst, "Team account and team connections".

* **Tokens.** The refresh token is in the OS store (``team_token_store``;
  memory only without one); the access token and grant key are in memory, each
  shared by every Angelo process here (``team_access_share``, ``team_grants``).
  A process refreshes only with no access token over ``ACCESS_MARGIN`` left
  (or a refused one), adopting a newer shared one first: about once per
  token lifetime per computer, however many processes run.
* **Refresh is strict single-flight.** Refresh tokens rotate and a reused one
  ends the whole sign-in, so ``_refresh_lock`` admits one refresh at a time in
  this process, and ``process_lock`` (``team_lock``) one across Angelo
  processes, which re-read the stored token first: another may have rotated
  it. A token is presented at most once. When the request certainly never
  reached the console (``NotSent``, or the lock timed out), or the console
  answered without using it (429, 503 or a non-grant OAuth error), it is kept;
  when the answer was lost or ambiguous, the sign-in ends and the person signs
  in again, rather than risk presenting a used token.
* **Policy.** ``policy()`` is the managed policy (``managed_policy``). A
  policy that stops team sign-in refuses to start one; a completed sign-in for
  an organization or account it refuses is revoked at once.
* **Threads.** Main-thread methods (``view``, ``begin_sign_in``, ``sign_out``,
  ``tick``, ``grant_key``) do no network or store I/O; they queue work on one
  worker thread. ``access_token`` and ``api_get`` may block on the network and
  run only on worker threads. ``drain`` runs at exit.
* **Epochs.** Signing out or starting a new sign-in increments ``_epoch``; a
  late result of the old one is discarded and its refresh token revoked.
* **Usage reports** (``team_usage_reports``): agent tasks on team connections
  are counted in memory, saved by the worker and reported every 15 minutes
  and, within a second, by ``close`` at quit.

Standard library only; no bpy. ``api_get`` is the hook for the team library.
"""

import atexit
import contextlib
import queue
import threading
import time
from dataclasses import replace

from . import managed_policy, team_signin
from .display_text import Text
from .team_errors import (BROWSER_ANSWERS, BUSY, DROPPED, INVALID_ANSWER, LOCKED,
                          NOT_ACCEPTED, REFUSED_BROWSER, SIGN_IN, SIGNED_OUT_NOTE, UNEXPECTED, NotSent,
                          SignInRequired, TeamError, Unanswered, api_error, oauth_error, unreachable)
from .team_access_share import SharedAccess
from .team_account_keys import GrantKeyTasks
from .team_account_reads import RETRY_EVERY, TeamReadTasks
from .team_grants import GrantKeys
from .team_http import form_body, http_transport, json_object
from .team_lock import LockTimeout
from .team_token_store import StoredSignIn
from .team_usage_reports import REPORT_EVERY, UsageReportTasks
from .team_view import OFFLINE, RESTORING, SIGNED_IN, SIGNED_OUT, SIGNING_IN, AccountView, parse_tokens

ACCESS_MARGIN = 60               # seconds: refresh an access token with less left
PROVIDERS_EVERY = 15 * 60        # seconds between providers/available checks

class TeamAccount(GrantKeyTasks, TeamReadTasks, UsageReportTasks):
    def __init__(self, console_url, *, vault=None, grant_vault=None, access_vault=None, storage_reason="",
                 transport=http_transport, process_lock=contextlib.nullcontext, clock=time.time,
                 monotonic=time.monotonic, sign_in_timeout=team_signin.TIMEOUT, policy=managed_policy.current,
                 usage_buffer=None):
        self.console_url = console_url
        self._stored, self._transport, self._process_lock = StoredSignIn(vault), transport, process_lock
        self._shared = SharedAccess(access_vault, clock)  # The access token every process here uses.
        self._clock, self._monotonic, self._sign_in_timeout = clock, monotonic, sign_in_timeout
        self._policy = policy
        self.storage_reason = storage_reason
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._view = AccountView(stored="" if vault else "session")
        self._access, self._access_expiry, self._refresh_token = "", 0.0, ""
        self._grants = GrantKeys(grant_vault, clock)
        self._epoch, self._listener, self._closed = 0, None, False
        self._login_hint = ""  # The email of a sign-in the console ended, for the next allow screen.
        self._pending, self._last = set(), {}
        # team_usage_buffer.UsageBuffer, the counts not saved yet, one save at a time (a quit waits for the worker's).
        self._usage_buffer, self._usage_pending, self._usage_saving = usage_buffer, {}, threading.Lock()
        self._tasks, self._worker = queue.Queue(), None
        atexit.register(self.drain)

    # Main thread: no network or store I/O ---------------------------------

    def view(self):
        return self._view

    def _update(self, **changes):
        with self._lock:
            self._view = replace(self._view, version=self._view.version + 1, **changes)

    def _update_if(self, epoch, **changes):
        with self._lock:
            if epoch == self._epoch:
                self._update(**changes)

    def _enqueue(self, name, *args, unique=True):
        with self._lock:
            if self._closed or (unique and name in self._pending):
                return False
            self._pending.add(name)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, daemon=True, name="AngeloTeamAccount")
                self._worker.start()
        self._tasks.put((name, args))
        return True

    def restore(self):
        """Resume a stored sign-in at startup. False when there is nothing to do."""
        with self._lock:
            if self._stored.vault is None or self._view.status != SIGNED_OUT:
                return False
            self._update(status=RESTORING, message="")
            return self._enqueue("restore", self._epoch)

    def begin_sign_in(self, device=None, pages=None):
        """Start listening on 127.0.0.1 and return the console URL to open in the browser.

        ``pages``: the browser pages in the person's language (``team_pages.Pages``, built on the main thread).
        """
        problem = managed_policy.sign_in_problem(self._policy())
        if problem:
            raise RuntimeError(problem)
        with self._lock:
            if self._view.status in {SIGNING_IN, SIGNED_IN}:
                raise RuntimeError("Already signed in, or a sign-in is waiting for the browser")
        verifier, challenge = team_signin.pkce_pair()
        state = team_signin.new_state()
        listener = team_signin.LoopbackListener(state, timeout=self._sign_in_timeout, issuer=self.console_url,
                                                pages=pages).start()
        device = device or team_signin.device_name()
        url = team_signin.authorize_url(self.console_url, listener.redirect_uri, challenge, state, device,
                                        org_hint=self._policy().organization, login_hint=self._login_hint)
        with self._lock:
            self._epoch += 1
            self._clear_tokens()
            self._listener = listener
            self._update(status=SIGNING_IN, message="", sign_in_url=url)
            epoch = self._epoch
        if not self._enqueue("sign_in", listener, verifier, epoch, device, unique=False):
            listener.close()
            raise RuntimeError("The team account is closed")
        return url

    def cancel_sign_in(self):
        with self._lock:
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()

    def sign_out(self):
        """Forget the sign-in now; revoke it and erase the stored tokens in the background."""
        with self._lock:
            token, listener, self._listener = self._refresh_token, self._listener, None
            self._epoch += 1
            self._clear_tokens()
            self._login_hint = ""
            self._update(status=SIGNED_OUT, message=Text(SIGNED_OUT_NOTE), sign_in_url="", **self._no_org())
        if listener is not None:
            listener.close()
        self._enqueue("sign_out", token, unique=False)

    def tick(self, idle):
        """Queue background work that is due. ``idle``: no chat is running a turn."""
        now = self._monotonic()
        with self._lock:
            view, last, epoch = self._view, dict(self._last), self._epoch
        due = lambda name, every: now - last.get(name, float("-inf")) >= every  # noqa: E731
        if view.status == OFFLINE and due("refresh", RETRY_EVERY):
            self._enqueue("refresh", epoch)
        if view.status != SIGNED_IN:
            return
        if due("providers", RETRY_EVERY if view.providers is None else PROVIDERS_EVERY):
            self._enqueue("providers")
        if due("usage", PROVIDERS_EVERY):
            self._enqueue("usage")
        if self._usage_buffer is not None and due("usage_report", REPORT_EVERY):
            self._enqueue("usage_report")
        if self.grant_key_due(idle) and due("grant_key", RETRY_EVERY):
            self._enqueue("grant_key")

    def _owner(self, view=None):
        view = view or self._view
        return view.org_id, view.user_id

    def redact(self, text):
        with self._lock:
            secrets = [s for s in (self._access, self._refresh_token, self._grants.secret()) if s]
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def close(self):
        """Stop background work (unregister, and so quit). The stored sign-in stays for the next launch."""
        self.report_at_exit()  # At most a second; what doesn't go now goes at the next start.
        with self._lock:
            self._closed = True
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        self._tasks.put(None)
        atexit.unregister(self.drain)

    def drain(self, timeout=10.0):
        """At exit: let a refresh in flight store its rotated token, or the next launch would present a used one."""
        if self._refresh_lock.acquire(timeout=timeout):
            self._refresh_lock.release()

    def _clear_tokens(self):
        self._access, self._access_expiry, self._refresh_token = "", 0.0, ""
        self._grants.forget()

    @staticmethod
    def _no_org():
        return {"org_id": "", "org_name": "", "user_id": "", "email": "", "seat": False, "scopes": frozenset(),
                "providers": None, "providers_error": "", "grant_key_expires": None, "grant_error": "", "usage": None,
                "org_policy": None}

    # Worker threads --------------------------------------------------------

    def _run(self):
        while True:
            item = self._tasks.get()
            if item is None:
                return
            name, args = item
            try:
                getattr(self, "_task_" + name)(*args)
            except Exception:  # noqa: BLE001 - keep the worker alive; never show internals or tokens.
                self._update(message=Text(UNEXPECTED))
            finally:
                with self._lock:
                    self._pending.discard(name)
                    self._last[name] = self._monotonic()

    def access_token(self, stale=""):
        """A valid access token, refreshing at most once at a time. ``stale``: a token the server refused."""
        with self._lock:
            if self._access and self._access != stale and self._access_expiry - self._monotonic() > ACCESS_MARGIN:
                return self._access
            if self._view.status not in {SIGNED_IN, RESTORING, OFFLINE}:
                raise SignInRequired(Text(SIGN_IN))
        with self._refresh_lock:
            with self._lock:  # Another thread may have refreshed while this one waited.
                if self._access and self._access != stale and self._access_expiry - self._monotonic() > ACCESS_MARGIN:
                    return self._access
                epoch = self._epoch
            self._refresh(epoch, stale)
            with self._lock:
                if epoch == self._epoch and self._access:
                    return self._access
        raise SignInRequired(Text(SIGN_IN))

    def api_get(self, path):
        """GET a console API path (``/api/...``) as the signed-in person; the decoded JSON object."""
        if not isinstance(path, str) or not path.startswith("/api/") or "//" in path or ".." in path:
            raise ValueError("Use a console API path such as /api/orgs/<id>/library/stats")
        return self._authorized("GET", path)

    def _authorized(self, method, path, body=None, headers=None):
        token = self.access_token()
        for attempt in (0, 1):
            request = dict(headers or {}, Authorization="Bearer " + token, Accept="application/json")
            try:
                status, answer, data = self._transport(method, self.console_url + path, request, body)
            except (NotSent, Unanswered):
                raise unreachable(self.console_url) from None
            if status == 401 and attempt == 0:
                token = self.access_token(stale=token)  # UNAUTHENTICATED: refresh once.
                continue
            if 200 <= status < 300:
                value = json_object(data)
                if value is None:
                    raise TeamError(Text(INVALID_ANSWER))
                return value
            error = api_error(status, data, answer.get("retry-after"))
            if isinstance(error, SignInRequired):
                with self._lock:
                    epoch = self._epoch
                self._end(epoch, error.message)
            raise error

    def _token_call(self, form):
        body, headers = form_body(dict(form, client_id=team_signin.CLIENT_ID))
        status, _answer, data = self._transport("POST", self.console_url + "/api/oauth/token", headers, body)
        if 200 <= status < 300:
            tokens = parse_tokens(json_object(data))
            if tokens is None:
                raise Unanswered("The token answer was not usable")  # It may have rotated the token.
            return tokens
        if status in {400, 401}:
            raise oauth_error(status, data)
        if status in {429, 503}:  # Refused before processing: the token was not used.
            raise TeamError(Text(BUSY), status=status)
        raise Unanswered("HTTP %d" % status)

    def _refresh(self, epoch, stale=""):
        """Rotate the refresh token (caller holds ``_refresh_lock``). Raises SignInRequired or TeamError."""
        try:
            with self._process_lock():
                self._refresh_locked(epoch, stale)
        except LockTimeout:
            raise TeamError(Text(LOCKED)) from None  # Nothing was presented; the token is kept.

    def _refresh_locked(self, epoch, stale=""):
        stored = self._stored.read()[0]
        with self._lock:
            if epoch != self._epoch:
                raise SignInRequired(Text(SIGNED_OUT_NOTE))
            token = self._refresh_token
            if self._stored.renewed_elsewhere(stored, token):
                token = stored  # Another Angelo window renewed this sign-in; its token is the live one.
            if not token:
                raise SignInRequired(Text(SIGN_IN))
        # Another Angelo process here refreshed less than a token's lifetime ago: use its newer answer (never the
        # token this process holds or the server refused), rotate nothing.
        shared = self._shared.read(token)
        if shared is not None and shared["access"] not in {stale, self._access} and shared["expires_in"] > ACCESS_MARGIN:
            if not self._accept(shared, epoch):
                raise SignInRequired(self._view.message or Text(SIGNED_OUT_NOTE))
            self._update(stored=self._view.stored or "saved")
            self._enqueue("providers")  # Another process's refresh is still a refresh for this one.
            return
        with self._lock:
            if epoch != self._epoch:
                raise SignInRequired(Text(SIGNED_OUT_NOTE))
            self._refresh_token = ""  # Presented at most once.
        try:
            tokens = self._token_call({"grant_type": "refresh_token", "refresh_token": token})
        except SignInRequired as error:
            self._end(epoch, error.message)
            raise
        except (NotSent, TeamError) as error:
            with self._lock:  # Never reached the console, or refused unprocessed: still valid.
                if epoch == self._epoch and not self._refresh_token:
                    self._refresh_token = token
            raise error if isinstance(error, TeamError) else unreachable(self.console_url) from None
        except Unanswered:
            self._end(epoch, Text(DROPPED))
            raise SignInRequired(Text(DROPPED)) from None
        if not self._accept(tokens, epoch):
            raise SignInRequired(self._view.message or Text(SIGNED_OUT_NOTE))
        self._share(tokens, self._stored.write(tokens["refresh"]))
        self._enqueue("providers")  # Each token refresh re-reads which providers the team offers (seat changes too).

    def _share(self, tokens, stored):
        """Record where the rotated sign-in is kept; share its access token only when the store holds it."""
        self._update(stored=stored)
        if stored == "saved":
            self._shared.write(tokens)
        else:
            self._shared.erase()

    def _accept(self, tokens, epoch):
        """Adopt new tokens unless the sign-in is stale or the policy refuses its organization or account."""
        refusal = managed_policy.admit(self._policy(), tokens["org_id"], tokens["email"])
        with self._lock:
            current = epoch == self._epoch and not self._closed and refusal is None
            if current:
                self._access, self._refresh_token = tokens["access"], tokens["refresh"]
                self._access_expiry = self._monotonic() + tokens["expires_in"]
                fields = {key: tokens[key] for key in ("org_id", "org_name", "user_id", "email", "seat", "scopes",
                                                       "org_policy", "role")}
                if self._owner() != (tokens["org_id"], tokens["user_id"]):
                    self._grants.forget()
                    fields.update(providers=None, providers_error="", grant_key_expires=None, grant_error="", usage=None)
                self._update(status=SIGNED_IN, message="", sign_in_url="", **fields)
        if refusal is not None:
            self._end(epoch, refusal, refused=True)  # Also erases a stored token of this refused sign-in.
        if not current:
            self._enqueue("revoke", tokens["refresh"], unique=False)  # Refused, or a late result of an abandoned sign-in.
        return current

    def _end(self, epoch, message, refused=False):
        """The sign-in can't continue: forget it and erase the stored tokens.

        ``refused``: the managed policy refused its account, so its email is no hint for the next sign-in.
        """
        with self._lock:
            if epoch != self._epoch:
                return
            self._login_hint = "" if refused else self._view.email or self._login_hint
            self._epoch += 1
            self._clear_tokens()
            self._update(status=SIGNED_OUT, message=message, **self._no_org())
        self._stored.delete()
        self._shared.erase()
        self._grants.erase_stored()

    def _task_restore(self, epoch):
        stored, problem = self._stored.read(adopt=True)
        with self._lock:
            if epoch != self._epoch:
                return
            if not stored:
                self._update(status=SIGNED_OUT, message=problem)
                return
            self._refresh_token = stored
        self._task_refresh(epoch)

    def _task_refresh(self, epoch):
        try:
            with self._refresh_lock:
                with self._lock:  # A caller may have refreshed (access_token) while this task waited.
                    held = self._access and self._access_expiry - self._monotonic() > ACCESS_MARGIN
                if not held:
                    self._refresh(epoch)
        except SignInRequired:
            return
        except TeamError as error:
            self._update_if(epoch, status=OFFLINE, message=error.message)
            return

    def _task_sign_in(self, listener, verifier, epoch, device):
        """Exchange the browser's code, then tell the browser's result page what happened (``team_pages``)."""
        answer = listener.wait()
        with self._lock:
            if self._listener is listener:
                self._listener = None
            stale = epoch != self._epoch
        if stale or "code" not in answer:
            listener.finish("mismatch" if stale else "denied")
            if not stale:
                template = BROWSER_ANSWERS.get(answer.get("error", ""), REFUSED_BROWSER)
                self._update_if(epoch, status=SIGNED_OUT, message=Text(template), sign_in_url="")
            return
        try:
            tokens = self._token_call({"grant_type": "authorization_code", "code": answer["code"],
                                       "code_verifier": verifier, "redirect_uri": listener.redirect_uri})
        except TeamError as error:
            # ``member_removed``: the person left the organization between Allow and this exchange.
            expired = isinstance(error, SignInRequired) and getattr(error, "reason", "") != "member_removed"
            listener.finish("expired" if expired else "error", reason=error.message)
            self._update_if(epoch, status=SIGNED_OUT, message=Text(NOT_ACCEPTED) if expired else error.message,
                            sign_in_url="")
            return
        except (NotSent, Unanswered):
            listener.finish("unreachable")
            self._update_if(epoch, status=SIGNED_OUT, message=unreachable(self.console_url).message, sign_in_url="")
            return
        policy = self._policy()
        refusal = managed_policy.admit(policy, tokens["org_id"], tokens["email"])
        accepted = self._accept(tokens, epoch)
        listener.finish("refused" if refusal else ("signed_in" if tokens["seat"] else "no_seat") if accepted
                        else "mismatch", reason=refusal, device=device, org=tokens["org_name"],
                        contact=managed_policy.contact(policy))
        if accepted:
            try:
                with self._process_lock():
                    self._share(tokens, self._stored.write(tokens["refresh"]))
            except LockTimeout:
                self._update(stored="session")  # Kept for this session; the next launch asks again.
            self._task_providers()

    def _task_sign_out(self, token):
        if token:
            self._task_revoke(token)
        try:
            with self._process_lock():
                self._stored.delete()
                self._shared.erase()
        except LockTimeout:
            self._stored.delete()  # The grant is revoked: whatever another window writes is refused.
            self._shared.erase()
        self._grants.erase_stored()

    def _task_revoke(self, token):
        body, headers = form_body({"token": token, "client_id": team_signin.CLIENT_ID})
        try:
            self._transport("POST", self.console_url + "/api/oauth/revoke", headers, body)
        except (NotSent, Unanswered):
            pass  # Best effort; the person can also sign the device out in the console (Your devices).
