"""Thin ctypes layer over Win32: hotkey, clipboard, SendInput, foreground window, mutex.

Standard library only. Every function sets argtypes/restype so handles survive on 64-bit Windows.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- constants --------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
VK_SHIFT, VK_CONTROL, VK_MENU, VK_SPACE, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x20, 0x5B, 0x5C
VK_C, VK_V = 0x43, 0x56
VK_UNASSIGNED = 0xE8  # reserved by Windows; used only to cancel Alt/Win menu activation
KEYEVENTF_KEYUP = 0x0002
MAPVK_VK_TO_VSC = 0
INPUT_KEYBOARD = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183

HOTKEY_ID = 0x5C01
CLIPBOARD_OPEN_TRIES = 20
CLIPBOARD_RETRY_S = 0.025
COPY_WAIT_S = 0.6            # how long to wait for an app to answer Ctrl+C
COPY_POLL_S = 0.01
KEY_RELEASE_WAIT_S = 1.5
FOCUS_WAIT_S = 1.0
PASTE_SETTLE_S = 0.08

_NAMED_KEYS = {"space": VK_SPACE, "enter": 0x0D, "tab": 0x09, "esc": 0x1B}
_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (modifier flags, virtual-key code). Raises ValueError."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"hotkey needs at least one modifier and a key: {spec!r}")
    *mods, key = parts
    flags = 0
    for m in mods:
        if m not in _MODS:
            raise ValueError(f"unknown modifier {m!r} in {spec!r}")
        flags |= _MODS[m]
    if key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"unknown key {key!r} in {spec!r}")
    return flags, vk


# ---- signatures -------------------------------------------------------------
user32.OpenClipboard.argtypes, user32.OpenClipboard.restype = [wt.HWND], wt.BOOL
user32.CloseClipboard.argtypes, user32.CloseClipboard.restype = [], wt.BOOL
user32.EmptyClipboard.argtypes, user32.EmptyClipboard.restype = [], wt.BOOL
user32.GetClipboardData.argtypes, user32.GetClipboardData.restype = [wt.UINT], wt.HANDLE
user32.SetClipboardData.argtypes, user32.SetClipboardData.restype = [wt.UINT, wt.HANDLE], wt.HANDLE
user32.IsClipboardFormatAvailable.argtypes, user32.IsClipboardFormatAvailable.restype = [wt.UINT], wt.BOOL
user32.GetClipboardSequenceNumber.argtypes, user32.GetClipboardSequenceNumber.restype = [], wt.DWORD
kernel32.GlobalAlloc.argtypes, kernel32.GlobalAlloc.restype = [wt.UINT, ctypes.c_size_t], wt.HGLOBAL
kernel32.GlobalLock.argtypes, kernel32.GlobalLock.restype = [wt.HGLOBAL], ctypes.c_void_p
kernel32.GlobalUnlock.argtypes, kernel32.GlobalUnlock.restype = [wt.HGLOBAL], wt.BOOL
kernel32.GlobalFree.argtypes, kernel32.GlobalFree.restype = [wt.HGLOBAL], wt.HGLOBAL
user32.GetForegroundWindow.argtypes, user32.GetForegroundWindow.restype = [], wt.HWND
user32.SetForegroundWindow.argtypes, user32.SetForegroundWindow.restype = [wt.HWND], wt.BOOL
user32.GetParent.argtypes, user32.GetParent.restype = [wt.HWND], wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetAsyncKeyState.argtypes, user32.GetAsyncKeyState.restype = [ctypes.c_int], ctypes.c_short
user32.RegisterHotKey.argtypes, user32.RegisterHotKey.restype = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT], wt.BOOL
user32.UnregisterHotKey.argtypes, user32.UnregisterHotKey.restype = [wt.HWND, ctypes.c_int], wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
kernel32.OpenProcess.argtypes, kernel32.OpenProcess.restype = [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE
kernel32.CloseHandle.argtypes, kernel32.CloseHandle.restype = [wt.HANDLE], wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.CreateMutexW.argtypes, kernel32.CreateMutexW.restype = [wt.LPVOID, wt.BOOL, wt.LPCWSTR], wt.HANDLE


# ---- SendInput --------------------------------------------------------------
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # present only so INPUT has the right size
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]


user32.MapVirtualKeyW.argtypes, user32.MapVirtualKeyW.restype = [wt.UINT, wt.UINT], wt.UINT
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT


def _key_event(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC), dwFlags=KEYEVENTF_KEYUP if up else 0, time=0, dwExtraInfo=0)
    return inp


def send_keys(events: list[tuple[int, bool]]) -> None:
    """Send (virtual-key, is_key_up) events in one atomic SendInput call."""
    arr = (INPUT * len(events))(*[_key_event(vk, up) for vk, up in events])
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput sent {sent}/{len(events)} events (blocked by UIPI?)")


def send_chord(modifier_vk: int, key_vk: int) -> None:
    send_keys([(modifier_vk, False), (key_vk, False), (key_vk, True), (modifier_vk, True)])


def keys_down(vks: tuple[int, ...] = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_SPACE)) -> list[int]:
    return [vk for vk in vks if user32.GetAsyncKeyState(vk) & 0x8000]


def mask_menu_activation() -> bool:
    """Tap an unassigned key while Alt/Win is still held.

    RegisterHotKey swallows the key press, so the focused app sees only "Alt down, Alt up", which
    opens its menu bar (Notepad shows key-tips) and sends our Ctrl+C to the menu instead of the
    text. A key between Alt-down and Alt-up cancels that. Returns True if a tap was sent.
    """
    if not keys_down((VK_MENU, VK_LWIN, VK_RWIN)):
        return False
    send_keys([(VK_UNASSIGNED, False), (VK_UNASSIGNED, True)])
    return True


def wait_keys_released(timeout: float = KEY_RELEASE_WAIT_S) -> bool:
    """The hotkey fires while the user still holds Ctrl+Alt; a synthetic Ctrl+C would arrive as
    Ctrl+Alt+C. Wait for physical release first."""
    deadline = time.monotonic() + timeout
    while keys_down():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


# ---- clipboard --------------------------------------------------------------
def _open_clipboard() -> None:
    for _ in range(CLIPBOARD_OPEN_TRIES):
        if user32.OpenClipboard(None):
            return
        time.sleep(CLIPBOARD_RETRY_S)
    raise OSError("clipboard is locked by another program")


def clipboard_sequence() -> int:
    return user32.GetClipboardSequenceNumber()


def get_clipboard_text() -> str | None:
    _open_clipboard()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    data = (text + "\0").encode("utf-16-le")
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise OSError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            raise OSError("GlobalLock failed")
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)  # ownership passes to the system only on success
            raise OSError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


# ---- foreground window ------------------------------------------------------
def process_name_of(hwnd) -> str:
    """Lowercase exe name of the window's process, or '' if it cannot be opened (e.g. elevated)."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)


def foreground() -> tuple[int | None, str]:
    hwnd = user32.GetForegroundWindow()
    return hwnd, (process_name_of(hwnd) if hwnd else "")


def is_terminal(process_name: str, terminals: tuple[str, ...] | frozenset[str]) -> bool:
    """True for terminal hosts, and for an unknown process: when we cannot tell, never send Ctrl+C."""
    name = process_name.lower()
    return name == "" or name in terminals


def focus_window(hwnd) -> bool:
    user32.SetForegroundWindow(hwnd)
    deadline = time.monotonic() + FOCUS_WAIT_S
    while time.monotonic() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)
    return False


# ---- grab / paste -----------------------------------------------------------
def grab_selection() -> str | None:
    """Ctrl+C the selection. None if nothing was copied; the clipboard is left untouched then.

    Never call this for a terminal window: Ctrl+C there is SIGINT.
    """
    wait_keys_released()
    before = clipboard_sequence()
    send_chord(VK_CONTROL, VK_C)
    deadline = time.monotonic() + COPY_WAIT_S
    while clipboard_sequence() == before:
        if time.monotonic() > deadline:
            return None  # nothing selected (the sequence number moves on every copy, even of equal text)
        time.sleep(COPY_POLL_S)
    return get_clipboard_text()


def paste_text(text: str) -> None:
    """Put text on the clipboard (left there on purpose) and press Ctrl+V."""
    set_clipboard_text(text)
    wait_keys_released()
    time.sleep(PASTE_SETTLE_S)
    send_chord(VK_CONTROL, VK_V)


# ---- hotkey loop and single instance ---------------------------------------
def acquire_single_instance(name: str = "Local\\PromptCleaner.SingleInstance"):
    """Returns the mutex handle, or None if another instance already holds it."""
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(f"CreateMutexW failed ({ctypes.get_last_error()})")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def run_hotkey_loop(spec: str, on_hotkey) -> None:
    """Register the hotkey and run the message loop until WM_QUIT. Raises OSError if the key is taken."""
    mods, vk = parse_hotkey(spec)
    if not user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
        raise OSError(f"RegisterHotKey({spec}) failed ({ctypes.get_last_error()}): is it used by another program?")
    try:
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                on_hotkey()
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
