import platform
import socket
from dataclasses import asdict, dataclass


@dataclass
class SessionState:
    state: str
    interactive: bool
    remote: bool
    message: str


def session_state() -> SessionState:
    if platform.system() != "Windows":
        return SessionState("UNSUPPORTED", False, False, "Ferramentas GUI disponíveis somente no Windows.")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    remote = bool(user32.GetSystemMetrics(0x1000))
    desktop = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS; sem SwitchDesktop
    if not desktop:
        return SessionState(
            "LOCKED_OR_SECURE",
            False,
            remote,
            "Desktop indisponível: sessão bloqueada, login, UAC ou agente sem sessão interativa.",
        )
    try:
        name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        success = user32.GetUserObjectInformationW(
            desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
        )
        interactive = bool(success and name.value.lower() == "default" and user32.GetForegroundWindow())
        return SessionState(
            "REMOTE_INTERACTIVE"
            if interactive and remote
            else "INTERACTIVE"
            if interactive
            else "SECURE_OR_UNKNOWN",
            interactive,
            remote,
            "Desktop interativo disponível." if interactive else "Desktop seguro ou desconhecido.",
        )
    finally:
        user32.CloseDesktop(desktop)


def local_device() -> dict:
    return {
        "id": "local",
        "name": socket.gethostname(),
        "os": platform.platform(),
        "connected": True,
        "power": "ON",
        "session": asdict(session_state()),
    }
