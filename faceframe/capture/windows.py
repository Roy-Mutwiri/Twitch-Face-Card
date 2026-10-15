"""Win32 window enumeration and state queries (ctypes, no extra packages)."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
GW_OWNER = 4
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    exe: str
    rect: tuple[int, int, int, int]     # x, y, w, h (DWM extended frame bounds, physical px)
    client_size: tuple[int, int]
    minimized: bool
    dpi: int

    def label(self) -> str:
        t = self.title if len(self.title) <= 90 else self.title[:87] + "..."
        state = " [minimized]" if self.minimized else ""
        return f"{t}  —  {self.rect[2]}x{self.rect[3]} px  (hwnd {self.hwnd:#x}){state}"


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_exe(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        kernel32.CloseHandle(h)


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_cloaked(hwnd: int) -> bool:
    v = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return v.value != 0


def frame_bounds(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def client_size(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def window_dpi(hwnd: int) -> int:
    try:
        return int(user32.GetDpiForWindow(hwnd)) or 96
    except Exception:
        return 96


def restore_window(hwnd: int) -> None:
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)


def info(hwnd: int) -> WindowInfo:
    pid = window_pid(hwnd)
    return WindowInfo(hwnd=int(hwnd), title=window_title(hwnd), class_name=window_class(hwnd), pid=pid,
                      exe=process_exe(pid), rect=frame_bounds(hwnd), client_size=client_size(hwnd),
                      minimized=is_minimized(hwnd), dpi=window_dpi(hwnd))


def list_top_windows(exe_filter: set[str] | None = None, exclude_own: bool = True) -> list[WindowInfo]:
    """Visible, uncloaked, unowned, non-tool top-level windows (what a capture picker shows)."""
    own = os.getpid()
    out: list[WindowInfo] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0 or is_cloaked(hwnd):
            return True
        pid = window_pid(hwnd)
        if exclude_own and pid == own:
            return True
        exe = process_exe(pid)
        if exe_filter and exe.lower() not in exe_filter:
            return True
        try:
            out.append(info(hwnd))
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def list_edge_windows() -> list[WindowInfo]:
    return list_top_windows({"msedge.exe"})


def find_window_by_title(title: str, pid: int | None = None) -> int | None:
    """Exact-title lookup (used for the app's own output window in tests)."""
    found: list[int] = []

    def cb(hwnd, _):
        if window_title(hwnd) == title and (pid is None or window_pid(hwnd) == pid):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


def ensure_dpi_awareness() -> None:
    """Per-monitor-v2 DPI awareness so all rects are physical pixels.
    (Qt sets this itself for GUI processes; CLI tools call this first.)"""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
