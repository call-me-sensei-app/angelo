# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One Windows Fab profile: atomic user-only DACL, pinned non-reparse path, identity-checked deletion.

No profile from another browser is opened. Windows APIs are loaded only on use.
A cleanup failure retains this exact owner; callers must never delete by an unverified pathname.
Source preparation only: real Windows ACL/filesystem/Chrome validation remains required.
"""
import ctypes
import os
from pathlib import Path
import threading
import time
import uuid

from . import private_roots

REPARSE = 0x400
DIRECTORY = 0x10


class ProfileUnavailable(RuntimeError):
    def __init__(self, code, *, owner=None):
        self.code, self.owner = code, owner
        super().__init__('Fab private profile: ' + code)


def _native_structures():
    class StatusUnion(ctypes.Union):
        _fields_=[('status',ctypes.c_int32),('pointer',ctypes.c_void_p)]
    class IOStatus(ctypes.Structure):
        _fields_=[('value',StatusUnion),('information',ctypes.c_size_t)]
    class Unicode(ctypes.Structure):
        _fields_=[('length',ctypes.c_uint16),('maximum',ctypes.c_uint16),('buffer',ctypes.c_void_p)]
    class Attributes(ctypes.Structure):
        _fields_=[('length',ctypes.c_uint32),('root',ctypes.c_void_p),('name',ctypes.POINTER(Unicode)),
                  ('flags',ctypes.c_uint32),('security',ctypes.c_void_p),('quality',ctypes.c_void_p)]
    return IOStatus,Unicode,Attributes


class _WindowsProfileAPI:
    """Small Win32 adapter; all HANDLE/pointer signatures are explicit on 32/64-bit Windows."""
    def __init__(self):
        if os.name != 'nt':
            raise ProfileUnavailable('PLATFORM_UNVERIFIED')
        from ctypes import wintypes as w
        self.w = w
        self._pending_open = self._pending_query = None
        self._temporary = [None, None, None, None]  # token, SID string, built SD, returned SD
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.a = ctypes.WinDLL('advapi32', use_last_error=True)
        self.n = ctypes.WinDLL('ntdll', use_last_error=True)
        specs = (
            (self.k, 'GetCurrentProcess', (), w.HANDLE),
            (self.k, 'CloseHandle', (w.HANDLE,), w.BOOL),
            (self.k, 'WaitForSingleObject', (w.HANDLE,w.DWORD), w.DWORD),
            (self.k, 'LocalFree', (ctypes.c_void_p,), ctypes.c_void_p),
            (self.k, 'CreateDirectoryW', (w.LPCWSTR, ctypes.c_void_p), w.BOOL),
            (self.k, 'GetFileInformationByHandle', (w.HANDLE,ctypes.c_void_p), w.BOOL),
            (self.k, 'GetFileInformationByHandleEx', (w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD), w.BOOL),
            (self.k, 'SetFileInformationByHandle', (w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD), w.BOOL),
            (self.a, 'OpenProcessToken', (w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)), w.BOOL),
            (self.a, 'GetTokenInformation', (w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)), w.BOOL),
            (self.a, 'ConvertSidToStringSidW', (ctypes.c_void_p,ctypes.POINTER(w.LPWSTR)), w.BOOL),
            (self.a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
             (w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p), w.BOOL),
            (self.a, 'GetSecurityInfo', (w.HANDLE,ctypes.c_int,w.DWORD,ctypes.POINTER(ctypes.c_void_p),
             ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)), w.DWORD),
            (self.a, 'GetSecurityDescriptorControl', (ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint16),ctypes.POINTER(w.DWORD)), w.BOOL),
            (self.a, 'GetAclInformation', (ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.c_int), w.BOOL),
            (self.a, 'GetAce', (ctypes.c_void_p,w.DWORD,ctypes.POINTER(ctypes.c_void_p)), w.BOOL),
            (self.a, 'IsValidSid', (ctypes.c_void_p,), w.BOOL),
        )
        for dll,name,args,result in specs:
            function=getattr(dll,name);function.argtypes=args;function.restype=result
        self.IOStatus,self.Unicode,self.Attributes=_native_structures()
        IOStatus,Unicode,Attributes=self.IOStatus,self.Unicode,self.Attributes
        self.n.NtOpenFile.argtypes=(ctypes.POINTER(w.HANDLE),w.DWORD,ctypes.POINTER(Attributes),
                                   ctypes.POINTER(IOStatus),w.DWORD,w.DWORD)
        self.n.NtOpenFile.restype=ctypes.c_int32
        self.n.NtQueryDirectoryFile.argtypes=(w.HANDLE,w.HANDLE,ctypes.c_void_p,ctypes.c_void_p,
            ctypes.POINTER(IOStatus),ctypes.c_void_p,w.DWORD,ctypes.c_int,ctypes.c_ubyte,ctypes.c_void_p,ctypes.c_ubyte)
        self.n.NtQueryDirectoryFile.restype=ctypes.c_int32

    def _check(self, result, code):
        if not result:
            raise ProfileUnavailable(code)
        return result

    def _release_temporary(self, slot):
        value=self._temporary[slot]
        if value is None:
            return
        pointer=ctypes.cast(value,ctypes.c_void_p).value
        if pointer:
            if slot==0:
                self._check(self.k.CloseHandle(value), 'TOKEN_CLOSE')
            elif self.k.LocalFree(pointer):
                raise ProfileUnavailable('SECURITY_MEMORY_RELEASE')
        self._temporary[slot]=None

    def _temporary_slot(self, slot, value):
        if self._temporary[slot] is not None:
            raise ProfileUnavailable('SECURITY_CLEANUP_UNPROVED')
        self._temporary[slot]=value
        return value

    def cleanup(self):
        # Unexpected pending native I/O retains all call buffers AND directory
        # owners. Never free a buffer still writable by the kernel or close its parent.
        for kind in ('_pending_open','_pending_query'):
            arena=getattr(self,kind)
            if arena is not None:
                handle,ios,*_keepalive=arena
                value=getattr(handle,'value',handle)
                if not value or self.k.WaitForSingleObject(handle,0)!=0 or ios.value.status==0x103:
                    raise ProfileUnavailable('DIRECTORY_IO_PENDING')
                if kind=='_pending_open':self.close(handle)
                setattr(self,kind,None)
        failure=None
        for slot in range(4):
            try:self._release_temporary(slot)
            except Exception as error:failure=failure or error
        if failure is not None:raise failure

    def sid_text(self, sid):
        self._check(self.a.IsValidSid(sid), 'SID_INVALID')
        text=self._temporary_slot(1,self.w.LPWSTR())
        self._check(self.a.ConvertSidToStringSidW(sid,ctypes.byref(text)), 'SID_READ')
        try:
            value=text.value
            if not isinstance(value,str) or not value.startswith('S-') or any(c not in 'S-0123456789' for c in value):
                raise ProfileUnavailable('SID_INVALID')
            return value
        finally:
            self._release_temporary(1)

    def current_sid(self):
        token=self._temporary_slot(0,self.w.HANDLE())
        self._check(self.a.OpenProcessToken(self.k.GetCurrentProcess(),0x8,ctypes.byref(token)), 'TOKEN_OPEN')
        try:
            size=self.w.DWORD()
            self.a.GetTokenInformation(token,1,None,0,ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise ProfileUnavailable('TOKEN_SIZE')
            buffer=ctypes.create_string_buffer(size.value)
            self._check(self.a.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)), 'TOKEN_READ')
            return self.sid_text(ctypes.c_void_p.from_buffer(buffer).value)
        finally:
            self._release_temporary(0)

    def create(self, path, sid):
        # OI|CI propagates privacy to Chrome-created cache/credential descendants.
        sddl='O:'+sid+'D:P(A;OICI;FA;;;'+sid+')'
        descriptor=self._temporary_slot(2,ctypes.c_void_p())
        self._check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None),
                    'DACL_BUILD')
        class Attributes(ctypes.Structure):
            _fields_=[('size',ctypes.c_uint32),('descriptor',ctypes.c_void_p),('inherit',ctypes.c_int32)]
        attributes=Attributes(ctypes.sizeof(Attributes),descriptor,0)
        try:
            if not self.k.CreateDirectoryW(str(path),ctypes.byref(attributes)):
                if ctypes.get_last_error()==183:
                    return False  # No adoption of a collision or preexisting directory.
                raise ProfileUnavailable('PROFILE_CREATE')
            return True
        finally:
            self._release_temporary(2)

    def _open(self, root, name, output, *, directory, delete, no_reparse):
        if self._pending_open is not None or self._pending_query is not None:
            raise ProfileUnavailable('DIRECTORY_IO_PENDING')
        raw=name.encode('utf-16-le','strict')
        if not raw or len(raw)>65532:
            raise ProfileUnavailable('DIRECTORY_NAME')
        buffer=ctypes.create_string_buffer(raw+b'\0\0')
        text=self.Unicode(len(raw),len(raw)+2,ctypes.cast(buffer,ctypes.c_void_p))
        attributes=self.Attributes(ctypes.sizeof(self.Attributes),getattr(root,'value',root),ctypes.pointer(text),
                                   0x40|(0x1000 if no_reparse else 0),None,None)
        ios=self.IOStatus();ios.value.status=0x103
        # Output is caller-published before NtOpenFile can acquire a native handle.
        self._pending_open=(output,ios,buffer,text,attributes)
        access=0x00020000|0x80|0x00100000|0x1|(0x00010000 if delete else 0)
        options=0x00200000|0x20|(0x1 if directory else 0)  # OPEN_REPARSE_POINT, synchronous, optional directory.
        status=self.n.NtOpenFile(ctypes.byref(output),access,ctypes.byref(attributes),ctypes.byref(ios),1,options)
        if status==0x103:
            raise ProfileUnavailable('DIRECTORY_IO_PENDING')
        self._pending_open=None  # Call completed; output remains caller-owned even if later Python fails.
        if status!=0 or not output.value:
            raise ProfileUnavailable('DIRECTORY_OPEN')

    def open_directory_into(self, path, output, *, delete=False):
        value=str(path)
        if len(value)<3 or value[1:3] not in (':\\',':/') or value.startswith('\\\\'):
            raise ProfileUnavailable('PROFILE_ROOT')
        # Native absolute name rejects every reparse in the initial path atomically.
        self._open(None,'\\??\\'+value.replace('/','\\'),output,directory=True,delete=delete,no_reparse=True)

    def open_relative_into(self, parent, name, output):
        if (type(name) is not str or not name or name in ('.','..') or
                any(c in name for c in ('\\','/',':','\0'))):
            raise ProfileUnavailable('DIRECTORY_NAME')
        # Exactly ONE leaf relative to the pinned object, with OBJ_DONT_REPARSE.
        # Unexpected reparse leaves refuse; no ancestor pathname is reopened.
        self._open(parent,name,output,directory=False,delete=True,no_reparse=True)

    def entries(self, handle, deadline):
        if self._pending_open is not None or self._pending_query is not None:
            raise ProfileUnavailable('DIRECTORY_IO_PENDING')
        buffer=(ctypes.c_uint64*8192)()  # Explicit 8-byte alignment required by FILE_DIRECTORY_INFORMATION.
        capacity=ctypes.sizeof(buffer)
        restart=True
        while True:
            if time.monotonic()>=deadline:
                raise ProfileUnavailable('PROFILE_CLEANUP_BOUND')
            ios=self.IOStatus();ios.value.status=0x103
            self._pending_query=(handle,ios,buffer)
            status=self.n.NtQueryDirectoryFile(handle,None,None,None,ctypes.byref(ios),buffer,
                                               capacity,1,0,None,int(restart))
            if status==0x103:
                raise ProfileUnavailable('DIRECTORY_IO_PENDING')
            self._pending_query=None
            code=status&0xffffffff
            if code==0x80000006:return  # STATUS_NO_MORE_FILES.
            used=ios.information
            if status!=0 or not 64<=used<=capacity:
                raise ProfileUnavailable('DIRECTORY_ENUMERATION')
            raw=ctypes.string_at(buffer,used)
            offset=0
            while True:
                if offset+64>used:raise ProfileUnavailable('DIRECTORY_RECORD')
                following=int.from_bytes(raw[offset:offset+4],'little')
                size=int.from_bytes(raw[offset+60:offset+64],'little')
                end=offset+64+size
                if size%2 or size==0 or end>used or (following and (following%8 or following<64+size or offset+following>=used)):
                    raise ProfileUnavailable('DIRECTORY_RECORD')
                try:name=raw[offset+64:end].decode('utf-16-le','strict')
                except UnicodeError as error:raise ProfileUnavailable('DIRECTORY_NAME') from error
                if name not in ('.','..'):
                    if any(c in name for c in ('\\','/',':','\0')):
                        raise ProfileUnavailable('DIRECTORY_NAME')
                    yield name
                if not following:break
                offset+=following
            restart=False

    def information(self, handle):
        identity,directory,reparse=self.entry_information(handle)
        if reparse or not directory:
            raise ProfileUnavailable('REPARSE_OR_NOT_DIRECTORY')
        return identity

    def entry_information(self, handle):
        # FILETIME alignment is 4, not the host ABI's default uint64 alignment.
        # Explicit DWORD pairs preserve the actual 52-byte BY_HANDLE_FILE_INFORMATION layout.
        class WireInfo(ctypes.Structure):
            _fields_=[('attributes',ctypes.c_uint32),('times',ctypes.c_uint32*6),
                      ('volume',ctypes.c_uint32),('size_hi',ctypes.c_uint32),('size_lo',ctypes.c_uint32),
                      ('links',ctypes.c_uint32),('index_hi',ctypes.c_uint32),('index_lo',ctypes.c_uint32)]
        info=WireInfo()
        self._check(self.k.GetFileInformationByHandle(handle,ctypes.byref(info)), 'DIRECTORY_IDENTITY')
        class FileId(ctypes.Structure):
            _fields_=[('volume',ctypes.c_uint64),('identifier',ctypes.c_ubyte*16)]
        full=FileId()
        # FileIdInfo(18) preserves ReFS's full 128-bit ID; no truncated identity fallback.
        self._check(self.k.GetFileInformationByHandleEx(handle,18,ctypes.byref(full),ctypes.sizeof(full)),
                    'DIRECTORY_FULL_IDENTITY')
        return (full.volume,bytes(full.identifier)),bool(info.attributes&DIRECTORY),bool(info.attributes&REPARSE)

    def verify_acl(self, handle, sid):
        owner,dacl=ctypes.c_void_p(),ctypes.c_void_p()
        descriptor=self._temporary_slot(3,ctypes.c_void_p())
        result=self.a.GetSecurityInfo(handle,1,0x1|0x4,ctypes.byref(owner),None,ctypes.byref(dacl),None,ctypes.byref(descriptor))
        if result:
            raise ProfileUnavailable('DACL_READ')
        try:
            control,revision=ctypes.c_uint16(),self.w.DWORD()
            self._check(self.a.GetSecurityDescriptorControl(descriptor,ctypes.byref(control),ctypes.byref(revision)), 'DACL_CONTROL')
            if control.value & 0x1004 != 0x1004 or self.sid_text(owner)!=sid or not dacl.value:
                raise ProfileUnavailable('DACL_NOT_PRIVATE')
            class AclSize(ctypes.Structure):
                _fields_=[('count',ctypes.c_uint32),('used',ctypes.c_uint32),('free',ctypes.c_uint32)]
            size=AclSize()
            self._check(self.a.GetAclInformation(dacl,ctypes.byref(size),ctypes.sizeof(size),2), 'DACL_SIZE')
            if size.count!=1:
                raise ProfileUnavailable('DACL_NOT_PRIVATE')
            ace=ctypes.c_void_p()
            self._check(self.a.GetAce(dacl,0,ctypes.byref(ace)), 'DACL_ACE')
            header=ctypes.string_at(ace,4)
            ace_type,flags=header[0],header[1]
            ace_size=int.from_bytes(header[2:4],'little')
            if ace_type!=0 or flags!=0x3 or ace_size<20:
                raise ProfileUnavailable('DACL_NOT_PRIVATE')
            prefix=ctypes.string_at(ace,10)
            sid_size=8+4*prefix[9]  # SID.SubAuthorityCount, bounded by this ACE before native SID access.
            if prefix[9]>15 or 8+sid_size>ace_size:
                raise ProfileUnavailable('DACL_NOT_PRIVATE')
            mask=int.from_bytes(prefix[4:8],'little')
            if ace_type!=0 or flags!=0x3 or mask!=0x001f01ff or self.sid_text(ace.value+8)!=sid:
                raise ProfileUnavailable('DACL_NOT_PRIVATE')
        finally:
            self._release_temporary(3)

    def mark_delete(self, handle):
        flag=ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN.
        self._check(self.k.SetFileInformationByHandle(handle,4,ctypes.byref(flag),ctypes.sizeof(flag)), 'PROFILE_DELETE')

    def close(self, handle):
        if hasattr(handle,'value') and handle.value is None:return
        self._check(self.k.CloseHandle(handle), 'DIRECTORY_CLOSE')
        if hasattr(handle,'value'):handle.value=None


class PrivateWindowsProfile:
    """Identity/ACL authority retained independently from browser and pipe owners."""
    def __init__(self, root=None, *, _api=None):
        self._lock=threading.RLock()
        self._api=_WindowsProfileAPI() if _api is None else _api
        self.path=None
        self.identity=None
        self._deleted=False
        self._marked=False
        self._sid=None
        self._paths=()
        self._handles=[]
        self._cleanup_handles=[None]*65  # One guarded entry per admitted recursion level, allocated up front.
        scratch=Path(private_roots.scratch_root()).absolute()
        if root is not None and Path(root).absolute()!=scratch:
            raise ProfileUnavailable('PROFILE_ROOT')
        # Refuse UNC/network profiles; local drive paths have a bounded ancestor chain.
        if not scratch.drive or str(scratch.drive).startswith('\\\\') or len(scratch.parents)>64:
            raise ProfileUnavailable('PROFILE_ROOT')
        ancestors=tuple(reversed(scratch.parents))+(scratch,)
        self._paths=ancestors
        self._handles=[None]*(len(ancestors)+1)  # Allocate slots before native resource acquisition.
        try:
            self._sid=self._api.current_sid()
            for index,path in enumerate(ancestors):
                self._handles[index]=ctypes.c_void_p()  # Publish mutable native output before acquisition.
                self._api.open_directory_into(path,self._handles[index])
                self._api.information(self._handles[index])
            # Generate before CreateDirectoryW; never adopt a collision.
            self.path=scratch/(private_roots.FAB_BROWSER_PREFIX+uuid.uuid4().hex)
            if not self._api.create(self.path,self._sid):
                self.path=None
                raise ProfileUnavailable('PROFILE_COLLISION')
            self._handles[-1]=ctypes.c_void_p()
            self._api.open_directory_into(self.path,self._handles[-1],delete=True)
            self.identity=self._api.information(self._handles[-1])
            self._api.verify_acl(self._handles[-1],self._sid)
            private_roots._own(self.path)  # Existing file-tool privacy registration; no new profile family.
        except BaseException as error:
            # Never drop a created directory/partial handle owner into an exception string.
            raise ProfileUnavailable('PROFILE_PREPARE_FAILED',owner=self) from error

    def verify(self):
        with self._lock:
            if self._deleted or self._handles[-1] is None or self.identity is None:
                raise ProfileUnavailable('PROFILE_UNAVAILABLE',owner=self)
            for handle in self._handles[:-1]:
                if handle is None:
                    raise ProfileUnavailable('PROFILE_ANCESTOR_UNPROVED',owner=self)
                self._api.information(handle)  # Recheck reparse attributes at every launch/cleanup gate.
            if self._api.information(self._handles[-1])!=self.identity:
                raise ProfileUnavailable('PROFILE_CHANGED',owner=self)
            self._api.verify_acl(self._handles[-1],self._sid)
            return True

    def _release_cleanup_handles(self):
        # Release deepest-first. If one close fails, retain every ancestor guard
        # above it: moving its parent must not move a pending deletion outside root.
        for slot in reversed(range(len(self._cleanup_handles))):
            handle=self._cleanup_handles[slot]
            if handle is not None:
                self._api.close(handle)
                self._cleanup_handles[slot]=None

    def _empty(self, directory, deadline, depth=0, count=None):
        if depth>64:
            raise ProfileUnavailable('PROFILE_CLEANUP_BOUND',owner=self)
        if count is None:count=[0]
        for name in self._api.entries(directory,deadline):
            count[0]+=1
            if count[0]>100000 or time.monotonic()>=deadline:
                raise ProfileUnavailable('PROFILE_CLEANUP_BOUND',owner=self)
            if self._cleanup_handles[depth] is not None:
                raise ProfileUnavailable('PROFILE_CLEANUP_UNPROVED',owner=self)
            output=ctypes.c_void_p()
            self._cleanup_handles[depth]=output  # Prepublish before native open/return allocation.
            self._api.open_relative_into(directory,name,output)
            _identity,is_directory,reparse=self._api.entry_information(output)
            if is_directory and not reparse:
                self._empty(output,deadline,depth+1,count)
            # Enumeration, child opening, recursion and deletion all use held
            # objects. Share flags alone do NOT exclude FILE_WRITE_ATTRIBUTES;
            # safety comes from object-relative resolution, not a pathname check.
            self._api.mark_delete(output)
            self._api.close(output)
            self._cleanup_handles[depth]=None

    def remove(self, deadline):
        """Only after caller proves browser job empty. Delete root by its held handle, never pathname."""
        with self._lock:
            try:
                if time.monotonic()>=deadline:
                    raise ProfileUnavailable('PROFILE_CLEANUP_BOUND',owner=self)
                self._api.cleanup()  # Retire any failed constructor/security query's exact temporary owners first.
                self._release_cleanup_handles()
                if not self._deleted and self.path is not None:
                    if not self._marked:
                        self.verify()
                        self._empty(self._handles[-1],deadline)
                        self._api.mark_delete(self._handles[-1])
                        self._marked=True
                    self._api.close(self._handles[-1])
                    self._handles[-1]=None
                    self._deleted=True
                # No browser/profile remains; release pinned ancestors independently.
                failure=None
                for index in reversed(range(len(self._handles))):
                    if self._handles[index] is not None:
                        try:
                            self._api.close(self._handles[index]);self._handles[index]=None
                        except Exception as error:
                            failure=failure or error
                try:self._api.cleanup()
                except Exception as error:failure=failure or error
                if failure is not None:
                    raise failure
            except BaseException as error:
                raise ProfileUnavailable('PROFILE_CLEANUP_UNPROVED',owner=self) from error
