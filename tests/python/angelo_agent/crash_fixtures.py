# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Crash logs for the report tests, in the three formats Angelo's handler writes.

The macOS log is a real one (a deliberate null read in an isolated run of the
shipping binary) with the Info-editor messages and paths a real session
adds. Every log plants **canaries** where private data really appears: the Info
messages (file names, object names, a prompt echoed by a report, an API key),
Python frames from the person's own scripts and Windows pdb/source paths. A
report that contains any ``CANARIES`` text has leaked. Linux and Windows logs
follow ``BLI_system_backtrace`` and ``system_win32.cc`` line for line.
"""

CANARIES = (
    "alice", "Secret Client", "Project X", "Prototype_Zeta", "unreleased", "sk-ant-api03", "ghp_", "hunter2",
    "/Users/", "C:\\Users", "/home/", "wallet.blend", "Documents", "Application Support", "build-machine",
)

HEADER = "# Blender 5.2.2, Commit date: 2026-10-09 07:39, Hash 311bbe4efa04\n"

REPORTS = (
    'Saved "/Users/alice/Documents/Secret Client/Project X.blend"  # INFO\n'
    "Prototype_Zeta: modifier 'Bevel' applied  # INFO\n"
    "Angelo: make a model of my client's unreleased sneaker, token sk-ant-api03-AbCdEf123456  # INFO\n"
    "Error: cannot read C:\\Users\\alice\\wallet.blend password hunter2 ghp_0123456789abcdef  # ERROR\n"
    "# backtrace\n"                                   # A message shaped like a marker (the real one comes later).
    "0   Angelo   0x0000000100000000 leaked_symbol_from_a_message + 1\n"
)

MAC = HEADER + REPORTS + """
# backtrace
0   Angelo                              0x00000001015a036c _ZN7blender33BLI_system_backtrace_with_os_infoEP7__sFILEPKv + 56
1   Angelo                              0x0000000100a8719c _ZNSt3__117__call_once_proxyB9nqe210106INS_5tupleIJRZN7blenderL19sig_handle_crash_fnEiE3$_0EEEEEvPv + 372
2   libc++.1.dylib                      0x000000019133a4e4 _ZNSt3__111__call_onceERVmPvPFvS2_E + 196
3   Angelo                              0x0000000100a86f68 _ZN7blenderL19sig_handle_crash_fnEi + 84
4   libsystem_platform.dylib            0x0000000191429764 _sigtramp + 56
5   Angelo                              0x0000000102a1b2c4 _ZN7blender2ed4draw13draw_something + 120
6   Angelo                              0x00000001067af608 _PyObject_MakeTpCall + 300
7   Angelo                              0x00000001068dece4 _PyEval_EvalFrameDefault + 7716
8   dyld                                0x0000000191055d54 start + 7184

# Python backtrace
  File "/Users/alice/Library/Application Support/Angelo/5.2/scripts/addons/secret_client_tools.py", line 40 in sync_prototype
  File "/Users/alice/Documents/Secret Client/make_prototype_zeta.py", line 4 in <module>
  File "/Applications/Angelo.app/Contents/Resources/5.2/scripts/addons_core/angelo_agent/chat_view.py", line 612 in draw_message
  File "/Applications/Angelo.app/Contents/Resources/5.2/python/lib/python3.13/json/__init__.py", line 231 in dumps
  File "<string>", line 1 in <module>
  File "/Applications/Angelo.app/Contents/Resources/5.2/scripts/modules/../../../../etc/passwd.py", line 1 in sneaky
"""

LINUX = HEADER + REPORTS.replace("/Users/alice", "/home/alice") + """
# backtrace
./angelo(+0x1e3a1c4) [0x55d1c7a3b1c4]
/home/alice/angelo-build-machine/bin/angelo(_ZN7blender3foo3barEv+0x38) [0x55d1c7a3b8c4]
/lib/x86_64-linux-gnu/libc.so.6(+0x3c050) [0x7f2b1c442050]
/usr/lib/x86_64-linux-gnu/dri/radeonsi_dri.so(gallivm_free+0x14a) [0x7f2b0e1a914a]
/lib/x86_64-linux-gnu/libc.so.6(__libc_start_main+0x80) [0x7f2b1c442e40]

# Python backtrace
  File "/opt/angelo/5.2/scripts/startup/bl_ui/space_view3d.py", line 88 in draw
"""

WINDOWS = HEADER + REPORTS.replace("/Users/alice", "C:\\Users\\alice") + """
# backtrace
Exception Record:

ExceptionCode         : EXCEPTION_ACCESS_VIOLATION (0xc0000005)
Exception Address     : 0x00007FF6A1B2C3D4
Exception Module      : C:\\Users\\alice\\AppData\\Local\\Programs\\Angelo\\Angelo.exe
Exception Flags       : 0x00000000
Exception Parameters  : 0x2
\tParameters[0] (action)  : 0x0000000000000000 (read)
\tParameters[1] (address) : 0x0000000000000008


Stack trace:
Angelo.exe          :0x00007FF6A1B2C3D4  blender::ed::draw::draw_something C:\\build-machine\\angelo\\source\\blender\\editors\\draw.cc:120
Angelo.exe          :0x00007FF6A1B2C500  blender::wm_draw_update C:\\build-machine\\angelo\\source\\blender\\windowmanager\\intern\\wm_draw.cc:1011
nvoglv64.dll        :0x00007FFA11223344  Symbols not available
KERNEL32.DLL        :0x00007FFA9A2C257D  BaseThreadInitThunk


Loaded Modules :
0x00007FF6A0000000 1.0.0.0              Angelo.exe C:\\build-machine\\angelo\\bin\\Angelo.pdb
0x00007FFA11000000 31.0.15.3623         nvoglv64.dll
0x00007FFA9A2B0000 10.0.26100.1        KERNEL32.DLL
Threads:
Thread : 000014b0
Angelo.exe          :0x00007FF6A1B00000  blender::worker_loop C:\\build-machine\\angelo\\source\\blender\\blenlib\\intern\\task.cc:55
Thread : 00001a2c
ntdll.dll           :0x00007FFA9B8E0000  NtWaitForSingleObject

# Python backtrace
  File "C:\\Program Files\\Angelo\\5.2\\scripts\\addons_core\\angelo_agent\\chat_view.py", line 612 in draw_message
  File "C:\\Users\\alice\\Documents\\Secret Client\\run.py", line 9 in go
"""

STOCK_BLENDER = HEADER + "\n# backtrace\n0   Blender   0x0000000100000000 _ZN7blender3fooEv + 4\n1   libsystem_platform.dylib 0x0000000191429764 _sigtramp + 56\n"

MAC_ROOTS = ("/Applications/Angelo.app/Contents/Resources/5.2",)
LINUX_ROOTS = ("/opt/angelo/5.2",)
WINDOWS_ROOTS = ("C:\\Program Files\\Angelo\\5.2",)
