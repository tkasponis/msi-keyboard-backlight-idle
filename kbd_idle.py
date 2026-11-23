"""Turn an MSI 26-zone keyboard backlight off after it sits idle.

MSI Center calls this keyboard "26 Zone". It shows up as USB 0DB0:1605,
the product id shared by that keyboard (tested on a Vector A18 HX).
LEDKeeper sets brightness with a HID feature report and leaves the current
Mystic Light effect alone:

    report id 1, command 176, level 0..4, then a 1
    report id 1, command 177 asks for the current level

Level 0 is off. Levels 1..4 are the four backlight steps; 1 is the dimmest.
Any keyboard key turns the previous level back on. Mouse movement does not.

From 18:00 until 08:00 a startup applies level 1. Mystic Light's own startup
pass often switches the backlight off once Windows is up; until the first key
this puts level 1 back whenever that happens. The idle timer starts only after
that first key, so the light is already on when you sit down in the dark.
"""

from __future__ import annotations

import ctypes
import json
import threading
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOG_PATH = HERE / "kbd_idle.log"
DEFAULT_TIMEOUT = 120.0

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
ERROR_ALREADY_EXISTS = 183
INVALID_HANDLE = wintypes.HANDLE(-1).value
GENERIC_READ_WRITE = 0xC0000000
FILE_SHARE_READ_WRITE = 0x3
OPEN_EXISTING = 3
DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10

hid = ctypes.WinDLL("hid")
setupapi = ctypes.WinDLL("setupapi")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
hid.HidD_SetFeature.restype = ctypes.c_ubyte
hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_ulong]
hid.HidD_GetFeature.restype = ctypes.c_ubyte
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_ulong]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 512)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL


def log(message: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {}


def timeout_seconds() -> float:
    try:
        value = float(load_config().get("timeout_seconds", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if value < 1:
        return DEFAULT_TIMEOUT
    return value


def evening_level() -> int:
    try:
        level = int(load_config().get("evening_level", 1))
    except (TypeError, ValueError):
        level = 1
    return max(1, min(4, level))


def in_evening() -> bool:
    data = load_config()
    try:
        start = int(data.get("evening_from_hour", 18))
        end = int(data.get("evening_until_hour", 8))
    except (TypeError, ValueError):
        start, end = 18, 8
    hour = time.localtime().tm_hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def keyboard_path() -> str | None:
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    infoset = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if infoset == INVALID_HANDLE:
        return None
    index = 0
    found = None
    try:
        while True:
            data = SP_DEVICE_INTERFACE_DATA()
            data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                infoset, None, ctypes.byref(guid), index, ctypes.byref(data)
            ):
                break
            index += 1
            detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
            detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            needed = wintypes.DWORD()
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                infoset, ctypes.byref(data), ctypes.byref(detail),
                ctypes.sizeof(detail), ctypes.byref(needed), None,
            ):
                continue
            path = detail.DevicePath
            if "vid_0db0&pid_1605" in path.lower():
                found = path
                break
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(infoset)
    return found


class Keyboard:
    def __init__(self) -> None:
        self._handle = INVALID_HANDLE
        self._path: str | None = None

    def close(self) -> None:
        if self._handle != INVALID_HANDLE:
            kernel32.CloseHandle(self._handle)
            self._handle = INVALID_HANDLE

    def _open(self) -> bool:
        if self._handle != INVALID_HANDLE:
            return True
        path = self._path or keyboard_path()
        if not path:
            return False
        handle = kernel32.CreateFileW(
            path, GENERIC_READ_WRITE, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
        )
        if handle == INVALID_HANDLE:
            return False
        self._path = path
        self._handle = handle
        return True

    def _feature(self, payload: bytes, write: bool) -> bytes | None:
        if not self._open():
            return None
        buf = (ctypes.c_ubyte * 64)()
        for index, value in enumerate(payload):
            buf[index] = value
        fn = hid.HidD_SetFeature if write else hid.HidD_GetFeature
        ok = fn(self._handle, buf, 64)
        if not ok:
            self.close()
            return None
        return bytes(buf)

    def get_brightness(self) -> int | None:
        try:
            if self._feature(bytes([1, 177]), True) is None:
                return None
            data = self._feature(bytes([1]), False)
            if data is None or data[0] != 1 or data[1] != 177 or data[2] > 4:
                return None
            return data[2]
        finally:
            self.close()

    def set_brightness(self, level: int) -> bool:
        level = max(0, min(4, int(level)))
        try:
            sent = self._feature(bytes([1, 176, level, 1]), True)
            return sent is not None
        finally:
            self.close()


class IdleLight:
    def __init__(self) -> None:
        self.keyboard = Keyboard()
        self.last_key = time.monotonic()
        self.asleep = False
        self.saved_level: int | None = None
        self.evening = in_evening()
        self.used = not self.evening
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._next_settle_check = 0.0

    def note_key(self) -> None:
        with self._lock:
            self.last_key = time.monotonic()
            self.used = True

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log("worker started")
        try:
            self._run()
        except Exception as exc:
            log(f"worker stopped: {exc}")

    def _run(self) -> None:
        if self.evening:
            wanted = evening_level()
            if self.keyboard.set_brightness(wanted):
                with self._lock:
                    self.saved_level = wanted
                log(f"evening on, brightness={wanted}, timeout={timeout_seconds():.0f}s")
            else:
                log(f"evening on failed, wanted {wanted}")
        else:
            level = self.keyboard.get_brightness()
            log(f"watching, brightness={level}, timeout={timeout_seconds():.0f}s")
        while not self._stop.is_set():
            time.sleep(0.2)
            with self._lock:
                used = self.used
            if self.evening and not used:
                now = time.monotonic()
                if now >= self._next_settle_check:
                    self._next_settle_check = now + 2.0
                    wanted = evening_level()
                    current = self.keyboard.get_brightness()
                    if current not in (0, None):
                        continue
                    if not self.keyboard.set_brightness(wanted):
                        continue
                    with self._lock:
                        already = self.saved_level == wanted
                        self.saved_level = wanted
                    if current == 0:
                        log(f"evening, restored {wanted} after startup turned it off")
                    elif not already:
                        log(f"evening on, brightness={wanted}, timeout={timeout_seconds():.0f}s")
                continue
            timeout = timeout_seconds()
            with self._lock:
                idle = time.monotonic() - self.last_key
                asleep = self.asleep
                saved = self.saved_level
            if asleep:
                if idle >= timeout:
                    continue
                if not saved:
                    with self._lock:
                        self.asleep = False
                    continue
                if self.keyboard.set_brightness(saved):
                    with self._lock:
                        self.asleep = False
                    log(f"on, brightness={saved}")
                continue
            if idle < timeout:
                continue
            current = self.keyboard.get_brightness()
            if current is None:
                continue
            with self._lock:
                if time.monotonic() - self.last_key < timeout:
                    continue
            if current == 0:
                with self._lock:
                    self.saved_level = 0
                    self.asleep = True
                log("already off")
                continue
            if not self.keyboard.set_brightness(0):
                log("off failed")
                continue
            with self._lock:
                if time.monotonic() - self.last_key < timeout:
                    pressed_during_off = True
                else:
                    pressed_during_off = False
                    self.saved_level = current
                    self.asleep = True
            if pressed_during_off:
                self.keyboard.set_brightness(current)
                log(f"key during off, kept {current}")
            else:
                log(f"off, will restore {current}")


def main() -> None:
    mutex = kernel32.CreateMutexW(None, False, "Local\\MsiKbdIdle")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return

    lights = IdleLight()
    worker = threading.Thread(target=lights.run, name="kbd-idle", daemon=True)
    worker.start()

    hook_holder: dict[str, ctypes.c_void_p] = {}

    def hook_proc(code, wparam, lparam):
        if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            lights.note_key()
        return user32.CallNextHookEx(hook_holder.get("hook"), code, wparam, lparam)

    callback = HOOKPROC(hook_proc)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, None, 0)
    if not hook:
        log(f"keyboard hook failed {ctypes.get_last_error()}")
        lights.stop()
        return
    hook_holder["hook"] = hook

    message = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
            if message.message == WM_QUIT:
                break
    finally:
        lights.stop()
        user32.UnhookWindowsHookEx(hook)
        if lights.asleep and lights.saved_level:
            lights.keyboard.set_brightness(lights.saved_level)
        lights.keyboard.close()
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
