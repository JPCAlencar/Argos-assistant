"""Worker descartável: sem shell, kill ao timeout/cancelamento; usa só operações enumeradas."""

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from app.sessions import session_state

STAGE = "initialization"


def stage(value: str):
    global STAGE
    STAGE = value


class DesktopError(RuntimeError):
    """Mensagens controladas, sem conteúdo de janelas ou exceções de bibliotecas."""


def execute(name: str, args: dict, data_dir: Path) -> dict:
    if not session_state().interactive:
        raise RuntimeError("Sessão GUI indisponível. Autentique-se pelo Windows ou acesso remoto assistido.")
    import ctypes
    import os
    import subprocess

    import mss
    import pyautogui
    from PIL import Image
    from pywinauto import Desktop
    from pywinauto.uia_defines import NoPatternInterfaceError

    desktop = Desktop(backend="uia")
    pyautogui.FAILSAFE = True

    def info(window):
        return {
            "handle": window.handle,
            "title": window.window_text()[:300],
            "process_id": window.process_id(),
            "class_name": window.class_name(),
        }

    def window():
        stage("resolve_window")
        win = desktop.window(handle=args["handle"]).wrapper_object()
        if not win.is_visible() or not win.is_enabled():
            raise RuntimeError("Janela invisível ou desabilitada.")
        if args.get("expected_process_id") and win.process_id() != args["expected_process_id"]:
            raise DesktopError("Handle pertence a outro processo. Ação interrompida.")
        return win

    def focus(win):
        stage("focus_window")
        if ctypes.windll.user32.GetForegroundWindow() != win.handle:
            win.set_focus()
        if ctypes.windll.user32.GetForegroundWindow() != win.handle:
            raise RuntimeError("Não foi possível confirmar foco da janela. Ação interrompida.")

    if name == "get_screen_info":
        with mss.mss() as capture:
            return {"monitors": capture.monitors, "confirmed": True}
    if name == "list_windows":
        return {
            "windows": [info(w) for w in desktop.windows(visible_only=True)][:100],
            "confirmed": True,
            "untrusted": True,
        }
    if name == "get_active_window":
        handle = ctypes.windll.user32.GetForegroundWindow()
        win = desktop.window(handle=handle).wrapper_object()
        elements = []
        for child in win.descendants()[:100]:
            element = child.element_info
            elements.append(
                {
                    "name": element.name[:200],
                    "type": element.control_type,
                    "automation_id": element.automation_id,
                }
            )
        return {**info(win), "elements": elements, "confirmed": True, "untrusted": True}
    if name == "open_application":
        import psutil

        stage("observe_application_before_launch")
        executable = {"notepad": "notepad.exe", "calculator": "calc.exe"}[args["application"]]
        process_names = (
            {"notepad.exe"} if args["application"] == "notepad" else {"calculatorapp.exe", "calculator.exe"}
        )

        def candidates():
            found = []
            for candidate in desktop.windows(visible_only=True):
                try:
                    if psutil.Process(candidate.process_id()).name().lower() in process_names:
                        found.append(candidate)
                except (psutil.Error, RuntimeError):
                    continue
            return found

        existing = candidates()
        if args.get("reuse_handle"):
            existing = [w for w in existing if w.handle == args["reuse_handle"]]
            if len(existing) != 1:
                raise DesktopError("reuse_handle não identifica janela deste aplicativo.")
        if len(existing) == 1:
            return {**info(existing[0]), "action_executed": False, "already_open": True, "confirmed": True}
        if len(existing) > 1:
            raise DesktopError(
                "Há várias janelas do aplicativo. Informe reuse_handle; nenhum lançamento realizado."
            )
        old = {w.handle for w in desktop.windows(visible_only=True)}
        stage("launch_application_once")
        subprocess.Popen(
            [str(Path(os.environ["SystemRoot"]) / "System32" / executable)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        stage("observe_application_after_launch")
        while time.monotonic() < deadline:
            for win in candidates():
                if win.handle not in old:
                    return {**info(win), "action_executed": True, "confirmed": True}
            time.sleep(0.25)
        return {
            "action_executed": True,
            "confirmed": False,
            "message": "Comando enviado, mas nenhuma nova janela foi identificada. Consulte list_windows.",
        }
    if name == "mouse_move":
        width, height = pyautogui.size()
        if args["x"] >= width or args["y"] >= height:
            raise RuntimeError("Coordenadas fora do monitor principal.")
        target = (args["x"], args["y"])
        if tuple(pyautogui.position()) == target:
            return {"action_executed": False, "already_satisfied": True, "confirmed": True}
        stage("mouse_move_once")
        pyautogui.moveTo(*target, duration=0.15)
        return {"action_executed": True, "confirmed": tuple(pyautogui.position()) == (args["x"], args["y"])}
    win = window()
    if name == "focus_window":
        focus(win)
        return {**info(win), "confirmed": True}
    if name == "keyboard_type":
        import psutil

        if psutil.Process(win.process_id()).name().lower() != "notepad.exe":
            raise RuntimeError("Digitação está limitada ao Notepad nesta versão.")
        stage("resolve_edit_control")
        if args.get("automation_id"):
            control = (
                desktop.window(handle=win.handle).child_window(auto_id=args["automation_id"]).wrapper_object()
            )
        else:
            documents = win.descendants(control_type="Document")
            controls = documents if documents else win.descendants(control_type="Edit")
            controls = [item for item in controls if item.is_visible() and item.is_enabled()]
            if len(controls) != 1:
                raise RuntimeError("Informe automation_id: não há exatamente um campo editável na janela.")
            control = controls[0]

        def read_text():
            try:
                return control.iface_text.DocumentRange.GetText(-1)
            except NoPatternInterfaceError:
                try:
                    return control.iface_value.CurrentValue
                except NoPatternInterfaceError as exc:
                    raise DesktopError(
                        "Campo sem leitura UIA. Nenhuma escrita será realizada sem verificação."
                    ) from exc

        def normalize(text):
            return text.replace("\r\n", "\n").replace("\r", "\n")

        stage("read_text_before_write")
        previous = read_text()
        if normalize(previous) == normalize(args["text"]):
            return {
                "action_executed": False,
                "already_satisfied": True,
                "confirmed": True,
                "method": "state_check",
                "observed_characters": len(previous),
            }
        if normalize(previous) and not args.get("replace_existing"):
            raise DesktopError(
                "Documento contém texto. Crie documento em branco ou aprove substituição específica."
            )
        method = "uia_value_pattern"
        stage("uia_write")
        try:
            # ValuePattern não utiliza clipboard nem expressões de teclas.
            control.set_edit_text(args["text"].replace("\r\n", "\n").replace("\n", "\r\n"))
        except (NoPatternInterfaceError, AttributeError):
            stage("verify_before_fallback")
            if read_text() != previous:
                raise DesktopError("Conteúdo mudou durante falha UIA; fallback interrompido.")
            # WM_SETTEXT vai diretamente ao HWND do editor, sem clipboard nem foco global.
            native_handle = control.element_info.handle
            native = Desktop(backend="win32").window(handle=win.handle).descendants()
            editors = [
                item
                for item in native
                if item.class_name() in {"Edit", "RichEditD2DPT", "RICHEDIT50W"}
                and item.is_visible()
                and item.is_enabled()
            ]
            if native_handle:
                editors = [item for item in editors if item.handle == native_handle]
            if len(editors) == 1 and ctypes.windll.user32.IsChild(win.handle, editors[0].handle):
                stage("win32_text_fallback")
                method = "win32_wm_settext"
                set_native_text(editors[0].handle, args["text"])
            else:
                # Último recurso: somente documento vazio, texto curto e foco UIA comprovado.
                if normalize(previous) or len(args["text"]) > 256:
                    raise DesktopError("Sem editor Win32 inequívoco; fallback de teclado não é seguro.")
                focus(win)
                control.set_focus()
                if not control.has_keyboard_focus():
                    raise DesktopError("Foco do editor não confirmado; nenhuma tecla enviada.")
                stage("unicode_keyboard_fallback")
                method = "guarded_unicode_keyboard"
                send_unicode(args["text"], win.handle)
        stage("verify_text_after_write")
        deadline = time.monotonic() + 2
        observed = read_text()
        while normalize(observed) != normalize(args["text"]) and time.monotonic() < deadline:
            time.sleep(0.1)
            observed = read_text()
        return {
            "action_executed": True,
            "confirmed": normalize(observed) == normalize(args["text"]),
            "observed_characters": len(observed),
            "expected_characters": len(args["text"]),
            "observed_line_breaks": observed.count("\n") + observed.count("\r"),
            "method": method,
            "stage": STAGE,
        }
    if name == "keyboard_hotkey":
        before_tabs = len(win.descendants(control_type="TabItem"))
        focus(win)
        stage("send_hotkey_once")
        pyautogui.hotkey(*args["keys"].split("+"))
        stage("observe_after_hotkey")
        if args["keys"] == "ctrl+n":
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                documents = win.descendants(control_type="Document") or win.descendants(control_type="Edit")
                if len(win.descendants(control_type="TabItem")) == before_tabs + 1 and len(documents) == 1:
                    try:
                        blank = not documents[0].iface_text.DocumentRange.GetText(-1).strip()
                    except NoPatternInterfaceError:
                        blank = False
                    if blank:
                        return {
                            **info(win),
                            "action_executed": True,
                            "confirmed": True,
                            "new_blank_document": True,
                            "tabs_before": before_tabs,
                            "tabs_after": before_tabs + 1,
                        }
                time.sleep(0.1)
        return {
            "action_executed": True,
            "confirmed": False,
            "message": "Atalho enviado; observe o resultado.",
            "foreground_matches": ctypes.windll.user32.GetForegroundWindow() == win.handle,
            "tabs_before": before_tabs,
            "tabs_after": len(win.descendants(control_type="TabItem")),
        }
    if name == "mouse_click":
        control = (
            desktop.window(handle=win.handle).child_window(auto_id=args["automation_id"]).wrapper_object()
        )
        if not control.is_visible() or not control.is_enabled():
            raise DesktopError("Elemento invisível ou desabilitado; nenhum clique realizado.")
        focus(win)
        stage("invoke_control_once")
        try:
            control.invoke()
        except NoPatternInterfaceError:
            if not control.is_visible() or not control.is_enabled():
                raise DesktopError("Elemento indisponível; fallback de mouse interrompido.")
            from ctypes import wintypes

            rect = control.rectangle()
            point = wintypes.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            ctypes.windll.user32.WindowFromPoint.argtypes = [wintypes.POINT]
            ctypes.windll.user32.WindowFromPoint.restype = wintypes.HWND
            hit = ctypes.windll.user32.WindowFromPoint(point)
            if (
                ctypes.windll.user32.GetForegroundWindow() != win.handle
                or ctypes.windll.user32.GetAncestor(hit, 2) != win.handle
            ):
                raise DesktopError("Foco ou ponto de clique pertence a outra janela; mouse interrompido.")
            stage("mouse_fallback_once")
            control.click_input()
        return {
            "action_executed": True,
            "confirmed": False,
            "message": "Clique enviado; observe o resultado.",
            "foreground_matches": ctypes.windll.user32.GetForegroundWindow() == win.handle,
            "element_enabled_after": control.is_enabled(),
        }
    if name == "take_screenshot":
        rect = win.rectangle()
        if rect.width() <= 0 or rect.height() <= 0:
            raise RuntimeError("Janela sem área capturável.")
        with mss.mss() as capture:
            shot = capture.grab(
                {"left": rect.left, "top": rect.top, "width": rect.width(), "height": rect.height()}
            )
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        image.thumbnail((args["max_width"], args["max_width"]))
        folder = data_dir / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        asset = uuid4().hex + ".jpg"
        image.save(folder / asset, format="JPEG", quality=70, optimize=True)
        return {
            "asset": asset,
            "width": image.width,
            "height": image.height,
            "confirmed": True,
            "untrusted": True,
            "image_authorized": True,
        }
    raise RuntimeError("Operação não implementada.")


def set_native_text(handle: int, text: str):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    buffer = ctypes.create_unicode_buffer(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(
        handle, 0x000C, 0, ctypes.addressof(buffer), 0x0002 | 0x0020, 1500, ctypes.byref(result)
    ):
        raise DesktopError("Editor não respondeu ao fallback Win32 no prazo. Não repetir escrita.")
    if not result.value:
        raise DesktopError("Editor recusou WM_SETTEXT; não repetir escrita.")


def send_unicode(text: str, handle: int):
    import ctypes
    from ctypes import wintypes

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HardwareInput(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class InputUnion(ctypes.Union):
        _fields_ = [("ki", KeyboardInput), ("mi", MouseInput), ("hi", HardwareInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", wintypes.DWORD), ("data", InputUnion)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    units = text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-16-le")
    for start in range(0, len(units), 2):
        if user32.GetForegroundWindow() != handle:
            raise DesktopError("Janela perdeu foco; envio interrompido.")
        if any(user32.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise DesktopError("Modificador de teclado pressionado; envio interrompido.")
        unit = int.from_bytes(units[start : start + 2], "little")
        if unit == 13:
            events = [
                Input(type=1, data=InputUnion(ki=KeyboardInput(wVk=13))),
                Input(type=1, data=InputUnion(ki=KeyboardInput(wVk=13, dwFlags=2))),
            ]
        else:
            events = [
                Input(type=1, data=InputUnion(ki=KeyboardInput(wScan=unit, dwFlags=4))),
                Input(type=1, data=InputUnion(ki=KeyboardInput(wScan=unit, dwFlags=6))),
            ]
        array = (Input * len(events))(*events)
        if user32.SendInput(len(events), array, ctypes.sizeof(Input)) != len(events):
            raise DesktopError("Windows não confirmou o envio de todas as teclas.")
        # RichEdit pode consultar o estado VK_PACKET ao processar WM_KEYDOWN.
        # Dá tempo para consumir cada unidade antes de mudar o próximo pacote.
        time.sleep(0.05)


def main():
    payload = json.loads(sys.stdin.buffer.read(100000))
    started = time.monotonic()
    try:
        result = execute(payload["tool"], payload["arguments"], Path(payload["data_dir"]))
    except DesktopError as exc:
        result = {"error": str(exc), "diagnostic": {"stage": STAGE, "exception_type": type(exc).__name__}}
    except Exception as exc:
        # Exceções de UIA podem conter texto de janelas; não expor detalhes sensíveis.
        result = {
            "error": "Operação Windows falhou: verifique sessão, handle, elemento e permissões.",
            "diagnostic": {"stage": STAGE, "exception_type": type(exc).__name__},
        }
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
