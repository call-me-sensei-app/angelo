# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private completed-file descriptor lease; browser event filePath/URL are never file authority.

Only the selected GUID's predetermined name under a held private landing directory is opened. This is a local
content seal, not a publisher signature, download permission, GLB parser certificate or native import authority.
"""
import hashlib
import os
from pathlib import Path
import re
import stat

from . import private_roots
from .fab_browser_process import BrowserUnavailable
from .team_library_files import MAX_BYTES
from .fab_browser_profile import _read

class CompletedFile:
 """A synchronous private descriptor lease; uncertain close is latched and never retried by FD number."""
 def __init__(self,landing,guid,observation,check):
  self._fd=None;self._uncertain=False;self._directory_uncertain=False;self._closed=False;self._check=check;self._consumed=False;self._retired=False
  if (type(guid)is not str or not re.fullmatch('[A-Za-z0-9_-]{1,128}',guid)
      or observation.guid!=guid or not observation.complete
      or type(observation.received_bytes)is not int or not 0<observation.received_bytes<=MAX_BYTES['model']):
   raise BrowserUnavailable('DOWNLOAD_FILE_UNPROVED')
  if type(landing)is not Landing or landing.path is None:raise BrowserUnavailable('DOWNLOAD_FILE_UNPROVED')
  self._landing,self._guid=landing,guid
  expected_identity,expected_token=landing.identity,landing.token
  landing=Path(landing.path);before=os.lstat(landing)
  held=private_roots.hold(landing)
  if (held!=expected_identity or held!=(before.st_dev,before.st_ino)or not stat.S_ISDIR(before.st_mode)
      or before.st_uid!=os.getuid()or stat.S_IMODE(before.st_mode)!=0o700
      or expected_token is None or private_roots.owner_token(held)!=expected_token
      or _read(landing/private_roots.OWNER,64).decode('ascii')!=expected_token):raise BrowserUnavailable('DOWNLOAD_FILE_UNPROVED')
  folder=None
  try:
   folder=os.open(landing,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   opened=os.fstat(folder)
   if (opened.st_dev,opened.st_ino)!=held:raise BrowserUnavailable('DOWNLOAD_FILE_UNPROVED')
   fd=os.open(guid,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=folder);self._fd=fd
   info=os.fstat(fd)
   if (not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or info.st_nlink!=1
       or info.st_size!=observation.received_bytes):raise BrowserUnavailable('DOWNLOAD_FILE_UNPROVED')
   self.identity=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
   digest=hashlib.sha256();remaining=info.st_size
   while remaining:
    check();chunk=os.read(fd,min(1024*1024,remaining))
    if not chunk:raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
    digest.update(chunk);remaining-=len(chunk)
   if os.read(fd,1):raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
   final=os.fstat(fd)
   if self._identity(final)!=self.identity:raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
   check();self.size=info.st_size;self.sha256=digest.hexdigest();os.lseek(fd,0,os.SEEK_SET)
  except BaseException:
   try:self.close()
   except BaseException as cleanup:
    cleanup.owner=self;raise cleanup from None
   raise
  finally:
   if folder is not None:
    try:os.close(folder)
    except BaseException:
     # Never retry an uncertain numeric directory FD. Retain the file owner for independent cleanup.
     self._directory_uncertain=True
     try:self.close()
     except BaseException:pass
     error=BrowserUnavailable('DOWNLOAD_DIRECTORY_CLOSE_UNPROVED');error.owner=self;raise error from None
 def _identity(self,info):
  return(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
 def chunks(self):
  if self._closed or self._uncertain:raise BrowserUnavailable('DOWNLOAD_FILE_CLOSED')
  digest=hashlib.sha256();remaining=self.size
  while remaining:
   self._check()
   if self._identity(os.fstat(self._fd))!=self.identity:raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
   chunk=os.read(self._fd,min(1024*1024,remaining))
   if not chunk:raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
   digest.update(chunk);remaining-=len(chunk);yield chunk
  self._check()
  if (os.read(self._fd,1)or digest.hexdigest()!=self.sha256
      or self._identity(os.fstat(self._fd))!=self.identity):raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
  self._consumed=True
 def retire(self):
  if self._retired:return
  if self._closed or self._uncertain or self._directory_uncertain or not self._consumed:
   raise BrowserUnavailable('DOWNLOAD_FILE_UNCONSUMED')
  self._check();landing=self._landing;folder=None
  try:
   if private_roots.owner_token(landing.identity)!=landing.token or _read(landing.path/private_roots.OWNER,64).decode('ascii')!=landing.token:raise BrowserUnavailable('DOWNLOAD_LANDING_CHANGED')
   folder=os.open(landing.path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   info=os.fstat(folder)
   if (info.st_dev,info.st_ino)!=landing.identity:raise BrowserUnavailable('DOWNLOAD_LANDING_CHANGED')
   named=os.stat(self._guid,dir_fd=folder,follow_symlinks=False)
   if self._identity(named)!=self.identity or self._identity(os.fstat(self._fd))!=self.identity:
    raise BrowserUnavailable('DOWNLOAD_FILE_CHANGED')
   os.unlink(self._guid,dir_fd=folder)
   self.close()
  finally:
   if folder is not None:
    try:os.close(folder)
    except BaseException:
     self._directory_uncertain=True
     error=BrowserUnavailable('DOWNLOAD_DIRECTORY_CLOSE_UNPROVED');error.owner=self;raise error from None
  self._retired=True
 def close(self):
  if self._uncertain:
   error=BrowserUnavailable('DOWNLOAD_FILE_CLOSE_UNPROVED');error.owner=self;raise error
  if self._closed:
   if self._directory_uncertain:
    error=BrowserUnavailable('DOWNLOAD_DIRECTORY_CLOSE_UNPROVED');error.owner=self;raise error
   return
  if self._fd is not None:
   fd=self._fd;self._fd=None
   try:os.close(fd)
   except BaseException:
    self._uncertain=True;error=BrowserUnavailable('DOWNLOAD_FILE_CLOSE_UNPROVED');error.owner=self;raise error from None
  self._closed=True
  if self._directory_uncertain:
   error=BrowserUnavailable('DOWNLOAD_DIRECTORY_CLOSE_UNPROVED');error.owner=self;raise error


class Landing:
 """Existing private_roots allocation/identity owner, kept until exact native download/file drain is proved."""
 def __init__(self):
  self.path=None;self.identity=None;self.token=None
  try:
   self.path=private_roots.allocate(private_roots.FAB_BROWSER_PREFIX)
   os.chmod(self.path,0o700)
   self.identity=private_roots.hold(self.path);self.token=private_roots.owner_token(self.identity)
   if self.identity is None or self.token is None:raise BrowserUnavailable('DOWNLOAD_LANDING_UNPROVED')
  except BaseException:
   error=BrowserUnavailable('DOWNLOAD_LANDING_UNPROVED');error.owner=self;raise error from None
 def remove(self):
  if self.path is None:return
  import shutil
  info=os.lstat(self.path)
  if (not stat.S_ISDIR(info.st_mode)or (info.st_dev,info.st_ino)!=self.identity
      or info.st_uid!=os.getuid()or private_roots.owner_token(self.identity)!=self.token
      or _read(self.path/private_roots.OWNER,64).decode('ascii')!=self.token):
   error=BrowserUnavailable('DOWNLOAD_LANDING_CHANGED');error.owner=self;raise error
  shutil.rmtree(self.path);self.path=None;private_roots.prune()
