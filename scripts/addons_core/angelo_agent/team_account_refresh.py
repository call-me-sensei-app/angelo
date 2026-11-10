# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account's token refresh, a part of ``team_account.TeamAccount`` kept in its own module.

Contract: doc/angelo/ai_providers.rst, "Single flight" and "A rotated token the store refuses".

A refresh holds ``_refresh_lock`` (this process) and ``_process_lock``
(``team_lock``, every Angelo process here). Under them it re-reads the stored
token and the shared record (``team_refresh_marks``) and presents only the
stored token or this process's own one, never a marked one; it marks the
token before presenting it. A rotated token the store refuses stays in this
process's memory (``_unsaved``, with the holder lock taken) and its save is
retried; the others wait for it instead of presenting the used token. A new
sign-in whose generation couldn't be recorded yet (``_pending_gen``) records it
at the next retry. ``close`` waits for a token being presented, and hands an
unsaved one to the next account in this process (an add-on reload).

``TeamAccount`` provides ``_lock``, ``_refresh_lock``, ``_process_lock``,
``_epoch``, ``_view``, ``_stored``, ``_record``, ``_shared``, ``_transport``,
``_accept``, ``_end``, ``_enqueue``, ``_update``, ``_update_if`` and the token
fields. Standard library only; no bpy.
"""

import contextlib
import threading
import time

from . import team_http, team_signin
from .display_text import Text
from .team_errors import (BAD_RECORD, BUSY, DROPPED, HELD_ELSEWHERE, LOCKED, LOST, NO_RECORD, READ_LATER, SIGN_IN,
                          SIGNED_OUT_NOTE, NotSent, SignInRequired, TeamError, Unanswered, oauth_error, unreachable)
from .team_http import form_body, json_object
from .team_lock import LockTimeout
from .team_refresh_marks import RecordError, marked
from .team_token_store import TIMEOUT as STORE_TIMEOUT, UNREADABLE
from .team_view import OFFLINE, SIGNED_IN, RESTORING, parse_tokens

ACCESS_MARGIN = 60   # seconds: refresh an access token with less left
HOLDER_TRIES = 50     # 10 ms apart
QUIT_LOCK_WAIT = 3.0  # seconds ``close`` waits for the team lock to save an unsaved token one last time
# Seconds ``close`` waits while a token is being presented: the transport's connect and answer timeouts (each an idle
# timeout per socket operation, so a trickling answer can take longer), then one store call, so a rotated token the
# console answered is stored (or kept unsaved) before the process ends. At quit a slower answer is lost and its token
# stays marked spent (the others end the sign-in without presenting it); at an add-on reload it is still stored or
# handed off.
QUIT_REFRESH_WAIT = 2 * team_http.TIMEOUT + STORE_TIMEOUT  # Connecting, the answer, then the store.
_handoff, _handoff_lock = {}, threading.Lock()  # An unsaved token for the next account in this process (memory only).


def record_message(error):
    return Text(NO_RECORD if error.kind == "io" else BAD_RECORD)


class RefreshTasks:
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
        if self._unsaved or self._pending_gen:
            self._settle(epoch)
        try:
            token = self._choose(epoch)
        except SignInRequired as error:
            self._end(epoch, error.message)
            raise
        except TeamError as error:  # Waiting, never presenting: shown, and retried every minute (``tick``).
            self._update_if(epoch, status=OFFLINE, message=error.message)
            raise
        try:
            # Another Angelo process here refreshed less than a token's lifetime ago: use its newer answer (never
            # the token this process holds or the server refused), rotate nothing.
            shared = self._shared.read(token)
            if (shared is not None and shared["access"] not in {stale, self._access}
                    and shared["expires_in"] > ACCESS_MARGIN):
                if not self._accept(shared, epoch):
                    raise SignInRequired(self._view.message or Text(SIGNED_OUT_NOTE))
                self._update(stored=self._view.stored or "saved")
                self._enqueue("providers")  # Another process's refresh is still a refresh for this one.
                return
            self._present(token, epoch)
        finally:
            with self._lock:
                self._presenting = False

    def _choose(self, epoch):
        """The token to present, never a marked one: this process's own where no other process can hold it, else
        the stored one, else its own when the store holds none or a used one. Raises TeamError to wait (nothing
        presented) or SignInRequired when the sign-in is gone."""
        stored, problem = self._stored.read()
        try:
            generation, spent = self._record.state()
        except RecordError as error:
            raise TeamError(record_message(error)) from None
        dropped, chosen, wait = "", "", None
        with self._lock:
            if epoch != self._epoch:
                raise SignInRequired(Text(SIGNED_OUT_NOTE))
            if self._closed:  # Quitting: nothing new is presented (``close`` saves what is in flight).
                raise TeamError(Text(LOCKED))
            mine, shares = self._refresh_token, self._stored.shares
            kept = bool(mine) and mine == self._kept_marked
            # A token only this process can hold: unsaved, kept, a new sign-in not recorded yet, or saved where
            # no other process can read it.
            own = (self._unsaved or kept or self._pending_gen or not shares
                   or self._view.stored == "unreadable")
            current = not shares or self._pending_gen or generation == self._generation  # No sign-in/out since.
            if mine and own and not current:  # Of an ended or replaced sign-in: given up (``_settle`` too).
                dropped, mine, self._refresh_token, self._kept_marked, self._unsaved = mine, "", "", "", False
            usable = bool(mine) and current and (kept or not marked(mine, spent))
            if usable and own:
                chosen = mine
            elif problem and shares:
                wait = Text(READ_LATER, problem=problem)
            elif stored and not marked(stored, spent):
                self._generation, self._shared_sign_in = generation, True  # The sign-in every window shares.
                chosen = stored
            elif usable:  # The store holds no token, or a used one; this one was never presented.
                chosen = mine
        if dropped:
            self._release_holder_if_free()
            if not problem and stored != dropped:  # Still the stored one: the others may renew it (as ``_commit``).
                self._enqueue("revoke", dropped, unique=False)
        if chosen:
            return chosen
        if wait is not None:
            raise TeamError(wait)
        if self._record.holder_elsewhere():
            raise TeamError(Text(HELD_ELSEWHERE))  # The live token is in another window's memory: wait for it.
        if not stored and not mine:
            raise SignInRequired(Text(SIGN_IN))
        raise SignInRequired(Text(LOST))  # Used, and nobody holds the token that replaced it.

    def _present(self, token, epoch):
        with self._lock:
            if epoch != self._epoch:
                raise SignInRequired(Text(SIGNED_OUT_NOTE))
            if self._closed:  # Quitting: nothing new is presented; the token is kept.
                raise TeamError(Text(LOCKED))
            self._refresh_token = ""  # Presented at most once.
            self._presenting = True  # From here until the answer is stored, ``close`` waits (``_refresh_locked``).
        try:
            if token != self._kept_marked:
                self._record.mark(token)  # Before presenting: no other process presents it after this.
        except RecordError as error:
            self._keep(token, epoch)
            self._update_if(epoch, status=OFFLINE, message=record_message(error))
            raise TeamError(record_message(error)) from None
        try:
            tokens = self._token_call({"grant_type": "refresh_token", "refresh_token": token})
        except SignInRequired as error:
            self._end(epoch, error.message)
            raise
        except (NotSent, TeamError) as error:
            self._keep(token, epoch)  # Never reached the console, or refused unprocessed: still valid.
            self._unmark(token, epoch)
            raise error if isinstance(error, TeamError) else unreachable(self.console_url) from None
        except Unanswered:
            self._end(epoch, Text(DROPPED))
            raise SignInRequired(Text(DROPPED)) from None
        self._kept_marked = ""
        if not self._accept(tokens, epoch):
            with self._lock:  # Quitting: still the live token, for the next launch (or the next account here).
                closing = self._closed and epoch == self._epoch
                if closing:
                    self._refresh_token = tokens["refresh"]
            if closing:
                self._save_rotated(tokens, epoch)
                if self._handed_off:  # ``close`` stopped waiting and handed off already: this one goes too.
                    self._hand_off()
            raise SignInRequired(self._view.message or Text(SIGNED_OUT_NOTE))
        self._save_rotated(tokens, epoch)
        self._enqueue("providers")  # Each token refresh re-reads which providers the team offers (seat changes too).

    def _keep(self, token, epoch):
        with self._lock:
            if epoch == self._epoch and not self._refresh_token:
                self._refresh_token = token

    def _unmark(self, token, epoch):
        """The console didn't use ``token``. If the record can't say so, this process keeps it as the one token it
        may still present, and holds the holder lock so the other windows wait rather than end the sign-in."""
        try:
            self._record.unmark(token)
        except RecordError:
            with self._lock:
                if epoch != self._epoch:  # Signed out meanwhile: nothing of it is kept.
                    return
                self._kept_marked = token
            self._take_holder()
            return
        if self._kept_marked == token:
            self._kept_marked = ""
        self._release_holder_if_free()

    def _save_rotated(self, tokens, epoch):
        """Store a token the console just issued (caller holds the team lock); share its access token when saved.
        A sign-in whose generation isn't recorded yet keeps it unsaved for ``_settle`` to commit in one step."""
        with self._lock:
            pending = epoch == self._epoch and self._pending_gen and self._stored.shares
            if pending:
                self._unsaved, self._rotated = True, tokens
        if pending:
            self._take_holder()
            self._update_if(epoch, stored="session")
            return
        stored, problem = self._stored.write(tokens["refresh"])
        with self._lock:
            if epoch != self._epoch:  # Signed out or ended meanwhile: nothing of it is kept.
                return
            # The live token is only in this process: the others wait for it (team_refresh_marks). A sign-in no
            # other process ever read from the store ("session" from the start) is this window's alone, as before.
            unsaved = self._stored.vault is not None and (
                stored == "failed" or (stored == "session" and self._shared_sign_in))
            self._unsaved = unsaved
            if stored in {"saved", "unreadable"}:
                self._shared_sign_in = True
        if unsaved:
            self._take_holder()
            if self._stored.failure_kind != "timeout":  # A timed-out call may still land later, over a newer token.
                self._delete_if_used()
        self._release_holder_if_free()  # Also when a sign-out between ``_lock`` and the take cleared ``_unsaved``.
        self._update_if(epoch, stored=stored, save_problem=problem if unsaved else "")
        if stored == "saved":
            self._shared.write(tokens)
        else:
            self._shared.erase()

    def _delete_if_used(self):
        """Best effort, under the team lock: delete the stored token only when it is a used one (marked), so an
        older build launched meanwhile finds no used token; never another sign-in's live, unmarked token."""
        stored, problem = self._stored.read()
        if not stored or problem:
            return
        try:
            if marked(stored, self._record.state()[1]):
                self._stored.delete()
        except RecordError:
            pass

    def _give_up(self, token, epoch, revoke):
        """Stop keeping an unsaved ``token``: it was used elsewhere, or belongs to an ended or replaced sign-in."""
        with self._lock:
            if epoch != self._epoch:  # Of an ended sign-in: the current one's state stays.
                return
            if self._refresh_token == token:
                self._refresh_token = ""
            self._unsaved = False
        self._release_holder_if_free()
        self._update(save_problem="")
        if revoke:
            self._enqueue("revoke", token, unique=False)

    def _settle(self, epoch, quitting=False):
        """``_commit``, with ``_committing`` cleared afterwards (``close`` waits while it is set)."""
        try:
            self._commit(epoch, quitting)
        finally:
            with self._lock:
                self._committing = False

    def _commit(self, epoch, quitting):
        """Commit a new sign-in, or retry saving this process's unsaved token (caller holds the team lock).

        A new sign-in (``_pending_gen``) records its generation, then is stored. Nothing else of the older sign-in
        changes: windows on it keep its stored token (adopting it under the new generation) until this one is
        stored, so this window's failure to store it, or a quit, ends nothing of theirs. After ``close`` only
        close's own last try commits. A sign-in or sign-out in another window since an unsaved token was issued
        wins (the token is revoked and given up); so does a process that presented it after a store write
        reported as failed landed after all (given up: it is used)."""
        if epoch != self._epoch:  # Of a sign-in that has ended: nothing of the current one is touched.
            return
        with self._lock:
            token = self._refresh_token
            if self._pending_gen and not token:  # The sign-in never got its token (refused, or closed meanwhile).
                self._pending_gen = self._unsaved = False
            committing = self._pending_gen and not (self._closed and not quitting)
            if committing:
                self._committing = True  # From the record write until the store write: ``close`` waits.
        try:
            if self._pending_gen:  # Newer than any sign-in the record names (it also replaces an unusable one).
                if not committing:
                    return
                generation = self._record.bump()
                with self._lock:
                    if epoch != self._epoch:
                        return
                    self._generation, self._pending_gen = generation, False
            generation, spent = self._record.state()
        except RecordError:
            if self._pending_gen:
                self._update_if(epoch, stored="session")  # This window's until it can be recorded and saved.
            return  # Kept, and tried again; nothing is presented meanwhile (``_choose``).
        if not self._unsaved:
            return
        if not token or (marked(token, spent) and token != self._kept_marked):
            self._give_up(token, epoch, revoke=False)
            return
        if generation != self._generation:
            # Replaced by a sign-in or sign-out elsewhere. If a timed-out write of it landed after all, the others
            # may be renewing it from the store: given up, not revoked (revoking would end their sign-in too). A
            # store that can't be read can't tell, so nothing is revoked then (the grant lapses unused).
            in_store, unreadable = self._stored.read()
            self._give_up(token, epoch, revoke=not unreadable and in_store != token)
            return
        self._take_holder()  # Again, in case an earlier take met another process's probe.
        stored, problem = self._stored.read() if not quitting and not committing else (None, "")
        if not problem and stored == token:  # A write that timed out landed after all.
            stored, problem = "saved", ""
        else:
            stored, problem = self._stored.write(token)
        saved = stored in {"saved", "unreadable"}
        with self._lock:
            if epoch != self._epoch:
                return
            if saved:
                self._unsaved, self._shared_sign_in = False, True
            rotated = self._rotated if committing and stored == "saved" else None
        if rotated is not None and rotated["refresh"] == token:  # A sign-in just issued: its access too.
            self._shared.write(rotated)
        self._release_holder_if_free()
        self._update_if(epoch, stored=stored, save_problem="" if saved else problem)

    def _take_holder(self):
        """Hold ``team-unsaved.lock``; another process's ``held_elsewhere`` probe holds it for a moment, so retry."""
        holder = self._record.holder
        for _attempt in range(HOLDER_TRIES):
            try:
                if holder is None or holder.take():
                    break
            except OSError:
                return  # The others then can't tell this window is alive; they still never present a marked token.
            time.sleep(0.01)
        self._release_holder_if_free()  # A sign-out or a save meanwhile cleared what it was taken for.

    def _release_holder_if_free(self):
        with self._lock:  # Checked and released together: a token unsaved meanwhile on another thread keeps it.
            holder = self._record.holder  # ``_hand_off`` may detach it.
            if not self._unsaved and not self._kept_marked and holder is not None:
                try:
                    holder.release()
                except OSError:
                    pass  # The OS releases it when this process ends.

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

    def _task_save(self, epoch):
        """The minute's retry of an unsaved token, or of a new sign-in's generation."""
        with self._refresh_lock:
            if not (self._unsaved or self._pending_gen):
                self._release_holder_if_free()  # Nothing is held for the others any more.
                return
            try:
                with self._process_lock():
                    self._settle(epoch)
            except LockTimeout:
                return

    def _save_at_quit(self, timeout):
        """At quit: wait up to ``timeout`` s while a token is being presented, then try once more to save an unsaved
        token (``QUIT_LOCK_WAIT`` for the locks). Never raises."""
        start = time.monotonic()
        while not self._refresh_lock.acquire(timeout=0.05):
            held = self._unsaved or self._pending_gen or self._kept_marked
            limit = timeout if self._presenting or self._committing else QUIT_LOCK_WAIT if held else 0
            if time.monotonic() - start >= limit:
                return
        try:
            if not (self._unsaved or self._pending_gen or self._kept_marked):
                return
            with self._lock:
                epoch, kept = self._epoch, self._kept_marked
            with self._process_lock(QUIT_LOCK_WAIT):
                if kept:  # The console never used it: the others may present it after this process is gone.
                    with contextlib.suppress(RecordError):
                        self._record.unmark(kept)
                        with self._lock:
                            if self._kept_marked == kept:
                                self._kept_marked = ""
                        self._release_holder_if_free()
                if self._unsaved or self._pending_gen:
                    self._settle(epoch, quitting=True)
        except Exception:  # noqa: BLE001 - quitting never fails here; the hand-off still runs (``close``).
            return
        finally:
            self._refresh_lock.release()

    def _hand_off(self):
        """After ``close``: a token only this process holds goes to the next account here (an add-on reload), with
        the holder lock, instead of being lost; at quit it ends with the process. Memory only, never stored."""
        with self._lock:
            token = self._refresh_token
            keep = bool(token) and (self._unsaved or self._kept_marked == token or self._pending_gen)
            entry = {"token": token, "generation": self._generation, "unsaved": self._unsaved,
                     "kept": self._kept_marked == token, "pending": self._pending_gen,
                     "shared": self._shared_sign_in, "holder": self._record.holder} if keep else None
            if keep:  # First: the next account owns it now; nothing this closed one does releases it.
                self._record.holder = None
                self._unsaved, self._kept_marked, self._pending_gen = False, "", False
            # Else a token being presented keeps its flags: its late answer is stored, or handed off, by their rules.
            self._handed_off = True  # An answer that arrives after this hands its unsaved token off too.
        if entry is None:
            self._release_holder_if_free()
            return
        with _handoff_lock:
            _handoff[(self.console_url, str(self._record.path))] = entry

    def _take_handoff(self, epoch):
        """At restore: adopt a token the previous account in this process handed off. True when one was adopted."""
        with _handoff_lock:
            entry = _handoff.pop((self.console_url, str(self._record.path)), None)
            stale = [_handoff.pop(key) for key in list(_handoff)]  # One account per process: the others are gone.
        for other in stale:
            with contextlib.suppress(OSError):
                if other["holder"] is not None:
                    other["holder"].release()
        if entry is None:
            return False
        if entry["holder"] is not None:
            self._record.holder = entry["holder"]  # Still held by this process.
        with self._lock:
            stale = epoch != self._epoch
            if not stale:
                self._refresh_token, self._generation = entry["token"], entry["generation"]
                self._unsaved, self._pending_gen = entry["unsaved"], entry["pending"]
                self._shared_sign_in, self._kept_marked = entry["shared"], entry["token"] if entry["kept"] else ""
        self._take_holder()  # Taken again: the old account may have handed it off before it took it.
        return not stale

    def _restore_message(self, problem):
        """A restore found no stored token: wait for a window holding an unsaved one, else say why it is signed out."""
        if not problem and self._record.holder_elsewhere():
            return OFFLINE, Text(HELD_ELSEWHERE)
        return None, Text(UNREADABLE, problem=problem) if problem else ""
