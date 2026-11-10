# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mac browser-managed Fab sign-in profile ownership; no cookie/account/credential reads.

The existing private_roots registry remains the sole privacy registry. A private binding in the existing
connection directory identifies this feature's one profile. Binding/Done/retention never prove authentication.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

from . import private_roots, storage
from .fab_browser_ownership import snapshot, marker_and_profile, identity, browser_bundle, OwnershipUnavailable

BINDING='fab-browser-profile.json'
PROFILE_LOCK='.angelo-fab-profile.lock'

class ProfileUnavailable(RuntimeError):
    def __init__(self,code,owner=None):
        self.code,self.owner=code,owner
        super().__init__(code)

def _regular(fd,*,private=True):
    info=os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
            or (stat.S_IMODE(info.st_mode)!=0o600 if private else info.st_mode&0o022)):
        raise ProfileUnavailable('PROFILE_METADATA_UNSAFE')
    return info

def _read(path,limit=4096):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=_regular(fd)
        if info.st_size>limit:raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        data=os.read(fd,limit+1)
        if len(data)>limit:raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        return data
    finally:os.close(fd)

class ProfileStore:
    """Private connection metadata plus the existing account-store lock, never a new account/session engine."""
    def __init__(self,directory=None,*,scratch=None):
        if sys.platform!='darwin':raise ProfileUnavailable('PROFILE_PLATFORM_UNVERIFIED')
        self.directory=Path(directory) if directory is not None else storage.connection_directory()
        if not self.directory.is_absolute() or any(p.is_symlink() for p in (self.directory,*self.directory.parents)):
            raise ProfileUnavailable('PROFILE_METADATA_UNSAFE')
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        info=self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
            raise ProfileUnavailable('PROFILE_METADATA_UNSAFE')
        self._metadata_uncertain=False
        self._metadata_partial_owner=None
        self.directory_identity=info.st_dev,info.st_ino
        self.scratch=Path(scratch) if scratch is not None else private_roots.scratch_root()
        if not self.scratch.is_absolute() or any(p.is_symlink() for p in (self.scratch,*self.scratch.parents)):
            raise ProfileUnavailable('PROFILE_ROOT_UNSAFE')
        self.scratch.mkdir(parents=True,exist_ok=True,mode=0o700)
        info=self.scratch.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
            raise ProfileUnavailable('PROFILE_ROOT_UNSAFE')
        self.scratch_identity=info.st_dev,info.st_ino
    def _current(self):
        info=self.directory.lstat()
        if (info.st_dev,info.st_ino)!=self.directory_identity or not stat.S_ISDIR(info.st_mode):
            raise ProfileUnavailable('PROFILE_METADATA_CHANGED')
    @contextmanager
    def _transaction(self):
        if self._metadata_uncertain:raise ProfileUnavailable('PROFILE_METADATA_CLOSE_UNPROVED',self._metadata_partial_owner or self)
        self._current()
        # Same lock file/protocol as connections.metadata_lock, but fail immediately instead of blocking a job.
        fd=os.open(self.directory/'connections.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            _regular(fd,private=False)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ProfileUnavailable('PROFILE_BUSY') from None
            self._current()
            yield
        finally:
            try:os.close(fd)
            except OSError:
                self._metadata_uncertain=True
                partial=self._metadata_partial_owner
                owner=partial if partial is not None and not getattr(partial,'_released',False) else self
                raise ProfileUnavailable('PROFILE_METADATA_CLOSE_UNPROVED',owner) from None
    def close(self):
        if self._metadata_uncertain:raise ProfileUnavailable('PROFILE_METADATA_CLOSE_UNPROVED',self)
    def _binding(self):
        try:data=json.loads(_read(self.directory/BINDING))
        except FileNotFoundError:return None
        except (ValueError,UnicodeError):raise ProfileUnavailable('PROFILE_METADATA_SHAPE') from None
        keys={'version','uid','path','device','inode','token','state'}
        if (type(data) is not dict or set(data)!=keys or type(data['version']) is not int or data['version']!=1
                or type(data['uid']) is not int or data['uid']!=os.getuid()
                or any(type(data[k]) is not int or data[k]<=0 for k in ('device','inode'))
                or type(data['path']) is not str or type(data['token']) is not str
                or not re.fullmatch('[0-9a-f]{32}',data['token'])
                or data['state'] not in ('pending','retained','cleanup_required')):
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        return data
    def _write(self,row):
        self._current()
        fd,name=tempfile.mkstemp(prefix='.fab-profile-',dir=self.directory)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as file:
                json.dump(row,file);file.flush();os.fsync(file.fileno())
            self._current();os.replace(name,self.directory/BINDING)
        finally:
            if os.path.lexists(name):os.unlink(name)
    def create(self):
        with self._transaction():
            if self._binding() is not None:raise ProfileUnavailable('PROFILE_EXISTS')
            path=private_roots.allocate(private_roots.FAB_BROWSER_PREFIX,parent=self.scratch)
            info=path.lstat();token=private_roots.owner_token((info.st_dev,info.st_ino))
            row=dict(version=1,uid=os.getuid(),path=str(path),device=info.st_dev,inode=info.st_ino,token=token,state='pending')
            lease=ProfileLease.__new__(ProfileLease)
            self._metadata_partial_owner=lease
            try:
                ProfileLease.__init__(lease,self,row)
                if not private_roots.remember_profiles(self.directory,[{'native_profile':str(path)}],locked=True):
                    raise ProfileUnavailable('PROFILE_PRIVACY_REGISTRATION_FAILED',lease)
                self._write(row)
                return lease
            except BaseException:
                # Caller receives an explicit owner; no untracked deletion or loss of a possibly private profile.
                raise ProfileUnavailable('PROFILE_CREATION_INCOMPLETE',lease) from None
    def resume(self):
        return self._resume()
    def resume_expected(self,expected):
        """Acquire only this exact retained binding under the existing transaction; never create a substitute."""
        if (type(expected)is not dict or set(expected)!={'version','uid','path','device','inode','token','state'}
                or any(type(expected[k])is not int for k in ('version','uid','device','inode'))
                or any(type(expected[k])is not str for k in ('path','token','state'))):
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        return self._resume(dict(expected))
    def _resume(self,expected=None):
        with self._transaction():
            row=self._binding()
            if expected is not None and row!=expected:raise ProfileUnavailable('PROFILE_METADATA_CHANGED')
            if row is None:raise ProfileUnavailable('PROFILE_MISSING')
            if row['state']!='retained':raise ProfileUnavailable('PROFILE_RECOVERY_REQUIRED')
            lease=ProfileLease.__new__(ProfileLease)
            self._metadata_partial_owner=lease
            try:ProfileLease.__init__(lease,self,row)
            except BaseException:raise ProfileUnavailable('PROFILE_REOPEN_INCOMPLETE',lease) from None
            return lease

class ProfileLease:
    """An exclusive profile lifetime. Its fields/opaque token are private, never model/UI data."""
    def __init__(self,store,row):
        self.store,self._row=store,dict(row)
        self.path=Path(row['path']);self.identity=row['device'],row['inode']
        self._fd=None;self._fd_uncertain=False;self._lock_identity=None;self._browser=None;self._released=False
        self.validate()
        fd=os.open(self.path/PROFILE_LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            _regular(fd)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ProfileUnavailable('PROFILE_BUSY') from None
            self.validate();self._fd=fd;info=os.fstat(fd);self._lock_identity=info.st_dev,info.st_ino
        except BaseException:os.close(fd);raise
    def validate(self):
        try:self._validate()
        except ProfileUnavailable:raise
        except (OSError,ValueError,UnicodeError):raise ProfileUnavailable('PROFILE_CHANGED',self) from None
    def _validate(self):
        if self._fd_uncertain:raise ProfileUnavailable('PROFILE_LOCK_CLOSE_UNPROVED',self)
        if (self._released or not self.path.is_absolute() or self.path.parent!=self.store.scratch
                or not self.path.name.startswith(private_roots.FAB_BROWSER_PREFIX)
                or any(p.is_symlink() for p in (self.path,*self.path.parents))):
            raise ProfileUnavailable('PROFILE_CHANGED',self)
        root=self.store.scratch.lstat()
        if (root.st_dev,root.st_ino)!=self.store.scratch_identity or not stat.S_ISDIR(root.st_mode):
            raise ProfileUnavailable('PROFILE_ROOT_CHANGED',self)
        if self._fd is not None:
            held=_regular(self._fd);named=(self.path/PROFILE_LOCK).lstat()
            if ((held.st_dev,held.st_ino)!=self._lock_identity or (named.st_dev,named.st_ino)!=self._lock_identity
                    or not stat.S_ISREG(named.st_mode)):
                raise ProfileUnavailable('PROFILE_LOCK_CHANGED',self)
        info=self.path.lstat()
        if ((info.st_dev,info.st_ino)!=self.identity or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.getuid()
                or _read(self.path/private_roots.OWNER,64).decode('ascii')!=self._row['token']):
            raise ProfileUnavailable('PROFILE_CHANGED',self)
        if private_roots.hold(self.path)!=self.identity:raise ProfileUnavailable('PROFILE_CHANGED',self)
    def _inactive(self,program):
        try:self._inactive_checked(program)
        except ProfileUnavailable:raise
        except (OwnershipUnavailable,OSError,ValueError):raise ProfileUnavailable('PROFILE_INSPECTION_UNPROVED',self) from None
    def _inactive_checked(self,program):
        if any(os.path.lexists(self.path/name) for name in ('SingletonLock','SingletonSocket','SingletonCookie')):
            raise ProfileUnavailable('PROFILE_IN_USE',self)
        bundle=browser_bundle(program);deadline=time.monotonic()+3;rows=snapshot(deadline)
        selected={pid:row for pid,row in rows.items() if row['program'].startswith(bundle) and not row['zombie']}
        if len(selected)>128:raise ProfileUnavailable('PROFILE_INSPECTION_LIMIT',self)
        for pid,row in selected.items():
            marked,named=marker_and_profile(pid,'0'*32,str(self.path),deadline)
            fresh=snapshot(deadline).get(pid)
            if fresh is None or fresh['zombie']:continue
            if identity(fresh)!=identity(row) or marked is None:raise ProfileUnavailable('PROFILE_INSPECTION_UNPROVED',self)
            if named:raise ProfileUnavailable('PROFILE_IN_USE',self)
        final=snapshot(deadline)
        if {identity(row) for row in final.values() if row['program'].startswith(bundle) and not row['zombie']}!={identity(row) for row in selected.values()}:
            raise ProfileUnavailable('PROFILE_INSPECTION_UNPROVED',self)
    def claim(self,browser,program):
        self.validate()
        if self._fd is None or self._browser is not None:raise ProfileUnavailable('PROFILE_BUSY',self)
        self._inactive(program)
        self._browser=browser
        return self.path
    def finish_drained(self,browser,*,retain=False):
        # Only the same internal BrowserProcess calls this after its positive helper+leader drain proof.
        if type(retain) is not bool:raise ProfileUnavailable('PROFILE_RETENTION_POLICY',self)
        if self._browser is not browser or getattr(browser,'_pid',None) is not None or getattr(browser,'_closed',False) is not True:
            raise ProfileUnavailable('PROFILE_DRAIN_UNPROVED',self)
        self.validate()
        with self.store._transaction():
            current=self.store._binding()
            if current!=self._row:raise ProfileUnavailable('PROFILE_METADATA_CHANGED',self)
            if retain:
                updated=dict(self._row,state='retained');self.store._write(updated);self._row=updated
            else:
                import shutil
                shutil.rmtree(self.path)
                self.store._current();(self.store.directory/BINDING).unlink()
                if not private_roots.remember_profiles(self.store.directory,[],locked=True):
                    raise ProfileUnavailable('PROFILE_PRIVACY_REGISTRATION_FAILED',self)
                private_roots.prune()
        self._browser=None
        self._release_lock()

    def cancel_pending(self,program):
        """Cancel an allocated but never claimed profile; prove it inactive, never signal by pathname."""
        if self._browser is not None or self._fd is None:raise ProfileUnavailable('PROFILE_BUSY',self)
        self.validate();self._inactive(program)
        with self.store._transaction():
            current=self.store._binding()
            if current is not None and current!=self._row:raise ProfileUnavailable('PROFILE_METADATA_CHANGED',self)
            import shutil
            shutil.rmtree(self.path)
            if current is not None:(self.store.directory/BINDING).unlink()
            if not private_roots.remember_profiles(self.store.directory,[],locked=True):
                raise ProfileUnavailable('PROFILE_PRIVACY_REGISTRATION_FAILED',self)
            private_roots.prune()
        self._release_lock()

    def _release_lock(self):
        if self._fd_uncertain:raise ProfileUnavailable('PROFILE_LOCK_CLOSE_UNPROVED',self)
        if self._fd is None:raise ProfileUnavailable('PROFILE_LOCK_CLOSE_UNPROVED',self)
        try:info=os.fstat(self._fd)
        except OSError:
            self._fd_uncertain=True
            raise ProfileUnavailable('PROFILE_LOCK_CLOSE_UNPROVED',self) from None
        if (info.st_dev,info.st_ino)!=self._lock_identity or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
            raise ProfileUnavailable('PROFILE_LOCK_CHANGED',self)
        try:os.close(self._fd)
        except OSError:
            # Close may have succeeded. Never inspect or retry this numeric descriptor after uncertainty.
            self._fd_uncertain=True
            raise ProfileUnavailable('PROFILE_LOCK_CLOSE_UNPROVED',self) from None
        self._fd=None;self._released=True

    def close(self):
        """Uniform internal cleanup handle; never an account sign-out certificate until this succeeds."""
        if self._released:return
        if self._fd_uncertain:raise ProfileUnavailable('PROFILE_LOCK_CLOSE_UNPROVED',self)
        if self._browser is not None:
            self._browser.close()
            return
        if self._fd is None:
            # A partial constructor may have failed before acquiring the lock. Revalidate before another attempt.
            self.__init__(self.store,self._row)
        from .fab_browser_process import chrome_program
        self.cancel_pending(chrome_program())
