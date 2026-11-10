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
  processes, which re-read the stored token and the shared record first
  (``team_account_refresh``, ``team_refresh_marks``): another may have rotated
  it, or rotated it and failed to save it. A token is presented at most once.
  When the request certainly never reached the console (``NotSent``, or the
  lock timed out), or the console answered without using it (429, 503 or a
  non-grant OAuth error), it is kept;
  when the answer was lost or ambiguous, the sign-in ends and the person signs
  in again, rather than risk presenting a used token.
* **Policy.** ``policy()`` is the managed policy (``managed_policy``). A
  policy that stops team sign-in refuses to start one; a completed sign-in for
  an organization or account it refuses is revoked at once.
* **Threads.** Main-thread methods (``view``, ``begin_sign_in``, ``sign_out``,
  ``tick``, ``grant_key``) do no network or store I/O; they queue work on one
  worker thread. ``access_token`` and ``api_get`` may block on the network and
  run only on worker threads. ``close`` (or ``drain`` at exit) lets a refresh
  in flight finish and saves an unsaved token once more.
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

from . import managed_policy, product_env, team_signin
from .display_text import Text
from .team_errors import (BROWSER_ANSWERS, INVALID_ANSWER, NOT_ACCEPTED, REFUSED_BROWSER, SIGNED_OUT_NOTE,
                          UNEXPECTED, NotSent, SignInRequired, TeamError, Unanswered, api_error, unreachable)
from .team_access_share import SharedAccess
from .team_account_keys import GrantKeyTasks
from .team_account_refresh import ACCESS_MARGIN, QUIT_REFRESH_WAIT, RefreshTasks
from .team_account_reads import RETRY_EVERY, TeamReadTasks
from .team_grants import GrantKeys
from .team_http import form_body, http_transport, json_object
from .team_lock import LockTimeout
from .team_refresh_marks import RecordError, RefreshRecord
from .team_token_store import StoredSignIn
from .team_usage_reports import REPORT_EVERY, UsageReportTasks
from .team_view import OFFLINE, RESTORING, SIGNED_IN, SIGNED_OUT, SIGNING_IN, AccountView

__all__ = ["ACCESS_MARGIN", "PROVIDERS_EVERY", "TeamAccount"]
PROVIDERS_EVERY = 15 * 60        # seconds between providers/available checks

class TeamAccount(RefreshTasks, GrantKeyTasks, TeamReadTasks, UsageReportTasks):
    def __init__(self, console_url, *, vault=None, grant_vault=None, access_vault=None, storage_reason="",
                 transport=http_transport, process_lock=contextlib.nullcontext, clock=time.time,
                 monotonic=time.monotonic, sign_in_timeout=team_signin.TIMEOUT, policy=managed_policy.current,
                 usage_buffer=None, record_directory=None):
        self.console_url = console_url
        self._stored, self._transport, self._process_lock = StoredSignIn(vault), transport, process_lock
        self._shared = SharedAccess(access_vault, clock)  # The access token every process here uses.
        # Generation and spent marks every process here shares (team_refresh_marks); in memory without a folder.
        self._record = RefreshRecord(record_directory, console_url, clock=clock)
        self._generation = None  # The record's generation this process's token belongs to.
        self._unsaved, self._kept_marked = False, ""  # A rotated token the store refused; a marked token kept.
        # A new sign-in whose generation isn't recorded yet; this sign-in was read from or saved in the store (other
        # processes may hold it); a token is being presented (``close`` waits for its answer to be stored).
        self._pending_gen, self._shared_sign_in, self._presenting = False, False, False
        self._committing = False  # A new sign-in's generation is recorded but its token not stored yet.
        self._handed_off = False  # ``close`` ran ``_hand_off`` (``team_account_refresh``).
        self._rotated = None  # The token answer of a new sign-in not stored yet, in memory only (its access is shared).
        self._clock, self._monotonic, self._sign_in_timeout = clock, monotonic, sign_in_timeout
        self._policy, self.storage_reason = policy, storage_reason
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._view = AccountView(stored="" if vault else "session")
        self._access, self._access_expiry, self._refresh_token = "", 0.0, ""
        self._grants = GrantKeys(grant_vault, clock)
        self._affinity = None  # (org_id, user_id, cache-affinity secret) from the last providers read.
        self._epoch, self._listener, self._closed = 0, None, False
        self._login_hint = ""  # The email of a sign-in the console ended, for the next allow screen.
        self._pending, self._last = set(), {}
        # team_usage_buffer.UsageBuffer, the counts not saved yet, one save at a time (a quit waits for the worker's).
        self._usage_buffer, self._usage_pending, self._usage_saving = usage_buffer, {}, threading.Lock()
        self._tasks, self._worker, self._exit_report = queue.Queue(), None, None  # Threads ``join`` waits for.
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
                try:
                    self._worker.start()
                except RuntimeError:   # No thread to run it (``thread_start``): not queued, said; a later ask retries.
                    self._worker = None
                    self._pending.discard(name)
                    self._update(message=Text(UNEXPECTED))
                    return False
        self._tasks.put((name, args))
        return True

    def restore(self):
        """Resume a stored sign-in at startup. False when there is nothing to do."""
        with self._lock:
            if self._stored.vault is None or self._view.status != SIGNED_OUT:
                return False
            self._update(status=RESTORING, message="")
            if self._enqueue("restore", self._epoch):
                return True
            self._update(status=SIGNED_OUT)   # Not restoring after all: shown as before, with why (``_enqueue``).
            return False

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
            self._update_if(epoch, status=SIGNED_OUT, sign_in_url="")   # Nothing waits for the browser.
            raise RuntimeError("The team account is closed" if self._closed else "The sign-in couldn't start; retry")
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
        if (self._unsaved or self._pending_gen) and due("save", RETRY_EVERY):  # Not saved yet (team_refresh_marks).
            self._enqueue("save", epoch)
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
            affinity = self._affinity[2].hex() if self._affinity else ""
            override = (product_env.current("TEAM_CACHE_KEY_SECRET") or "").strip()  # codex_team_keys' override.
            secrets = [s for s in (self._access, self._refresh_token, self._grants.secret(), affinity, override,
                                   override.lower()) if s]
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def close(self):
        """Stop background work (unregister, and so quit). The stored sign-in stays for the next launch."""
        self.report_at_exit()  # At most a second; what doesn't go now goes at the next start.
        with self._lock:
            self._closed, listener, self._listener = True, self._listener, None
        if listener is not None:
            listener.close()
        self._tasks.put(None)
        atexit.unregister(self.drain)
        self._save_at_quit(QUIT_REFRESH_WAIT)  # A refresh in flight stores its token; an unsaved one gets one more try.
        self._hand_off()  # Still unsaved: an add-on reload's next account in this process takes it over.

    def join(self, timeout=10.0):
        """After ``close``, for a caller that keeps running (a test removing the folder): wait ``timeout`` s in all for
        the worker and a quit's report still sending, which open files there. True when none is left."""
        deadline, threads = time.monotonic() + timeout, [t for t in (self._worker, self._exit_report) if t is not None]
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads)

    def drain(self, timeout=QUIT_REFRESH_WAIT):
        """At exit, for a process that never ran ``close``: let a refresh in flight store its rotated token. (A save
        started here can't run: the interpreter starts no new threads at exit, so the store call fails safely.)"""
        with self._lock:
            self._closed = True  # Nothing new is presented after this (``close`` never ran).
        self._save_at_quit(timeout)

    def _clear_tokens(self):
        self._access, self._access_expiry, self._refresh_token = "", 0.0, ""
        self._unsaved, self._kept_marked = False, ""  # The holder lock is released by the worker.
        self._pending_gen, self._shared_sign_in, self._rotated = False, False, None
        self._grants.forget()
        self._affinity = None

    @staticmethod
    def _no_org():
        return {"org_id": "", "org_name": "", "user_id": "", "email": "", "seat": False, "scopes": frozenset(),
                "providers": None, "providers_error": "", "grant_key_expires": None, "grant_error": "", "usage": None,
                "org_policy": None, "save_problem": ""}

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
                with self._lock:  # What was read under a refused token (a removed member's) goes, however the refresh
                    self._affinity = None  # ends; a live token's next providers read brings it back.
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
                    self._affinity = None
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
            # Its live token was never the stored one (unsaved, or a new sign-in not stored yet): the store may
            # hold another sign-in's live token the other windows renew from, so it is left to them.
            elsewhere = self._unsaved or self._pending_gen
            self._epoch += 1
            self._clear_tokens()
            self._update(status=SIGNED_OUT, message=message, **self._no_org())
        self._release_holder_if_free()
        if not elsewhere:  # Else the stored token and its shared access item may be the others' (kept for them).
            self._stored.delete()
            self._shared.erase()
        self._grants.erase_stored()

    def _task_restore(self, epoch):
        if self._take_handoff(epoch):  # This process's previous account (an add-on reload) held an unsaved token.
            self._task_refresh(epoch)
            return
        stored, problem = self._stored.read()
        status, message = (None, "") if stored else self._restore_message(problem)
        with self._lock:
            if epoch != self._epoch:
                return
            if not stored:  # Signed out, or waiting for another window that holds an unsaved token (OFFLINE).
                self._update(status=status or SIGNED_OUT, message=message)
                return
            self._refresh_token, self._shared_sign_in = stored, True
        self._task_refresh(epoch)

    def _task_sign_in(self, listener, verifier, epoch, device):
        """Exchange the browser's code, then tell the browser's result page what happened (``team_pages``)."""
        self._release_holder_if_free()  # A token this process held before (``begin_sign_in`` dropped it) is gone.
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
        shared = self._stored.shares  # Only a sign-in the windows share is recorded (team_refresh_marks).
        with self._lock:  # Before it is adopted: no refresh of this process may take it for the older sign-in's.
            if epoch == self._epoch and shared:
                self._unsaved = self._pending_gen = True  # Stored once its generation is recorded (``_settle``).
                self._rotated = tokens
        accepted = self._accept(tokens, epoch)
        listener.finish("refused" if refusal else ("signed_in" if tokens["seat"] else "no_seat") if accepted
                        else "mismatch", reason=refusal, device=device, org=tokens["org_name"],
                        contact=managed_policy.contact(policy))
        if accepted:
            try:
                with self._refresh_lock, self._process_lock():  # No refresh here meanwhile (it would rotate it).
                    if not shared:
                        self._save_rotated(tokens, epoch)  # This window's alone, as before.
                    else:
                        self._settle(epoch)  # Recorded, then stored (``close`` waits); retried until it is.
            except LockTimeout:  # Kept for this session; recorded and saved by the minute's retry (``_settle``).
                self._update_if(epoch, stored="session")
            self._task_providers()

    def _task_sign_out(self, token):
        if token:
            self._task_revoke(token)
        self._release_holder_if_free()
        try:
            with self._process_lock():
                try:  # A window holding an unsaved token of this sign-in gives it up (team_refresh_marks).
                    if self._stored.shares:  # A sign-in no other window shares changes nothing of theirs.
                        self._record.bump()  # Also replaces a record this build can't use.
                except RecordError:
                    pass  # It then writes a revoked token, which every window's next refresh ends.
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
