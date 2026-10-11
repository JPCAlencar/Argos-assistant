"""Uma janela própria; nunca termina processos ou documentos anteriores ao teste."""

import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4


class OwnedNotepad:
    def __init__(self, directory: Path):
        self.directory = directory
        self.file = directory / f"ARGOS-DESCARTAVEL-{uuid4().hex}.txt"
        self.handle = self.pid = self.document_id = None
        self.verified_blank = False
        self.events = []
        self.before = set()
        self.previous_focus = ctypes.windll.user32.GetForegroundWindow()

    def log(self, action, **details):
        self.events.append({"time": time.monotonic(), "action": action, **details})
        output = Path("test-results/windows-integration.json")
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")
        (output.parent / (self.file.stem + ".json")).write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def windows(self):
        from pywinauto import Desktop

        return Desktop(backend="uia").windows(visible_only=True)

    def editor(self, win):
        documents = win.descendants(control_type="Document") or win.descendants(control_type="Edit")
        if len(documents) != 1:
            raise RuntimeError("Editor próprio não identificado inequivocamente.")
        return documents[0]

    def owned(self):
        from pywinauto import Desktop

        if not self.handle or self.handle in self.before:
            raise RuntimeError("Não há janela própria comprovada.")
        win = Desktop(backend="uia").window(handle=self.handle).wrapper_object()
        if win.process_id() != self.pid or len(win.descendants(control_type="TabItem")) > 1:
            raise RuntimeError("Janela contém outro processo/documento; nenhuma ação permitida.")
        if self.editor(win).element_info.runtime_id != self.document_id:
            raise RuntimeError("Documento mudou; nenhuma ação permitida.")
        return win

    def close_seed_tab(self):
        matches = [
            (w, t)
            for w in self.windows()
            if w.class_name() == "Notepad"
            for t in w.descendants(control_type="TabItem")
            if self.file.stem in t.window_text()
        ]
        if not matches:
            return
        if len(matches) != 1:
            raise RuntimeError("Aba descartável ambígua; nenhuma aba será fechada.")
        _, tab = matches[0]
        buttons = [
            b for b in tab.descendants(control_type="Button") if b.element_info.automation_id == "CloseButton"
        ]
        if len(buttons) != 1:
            raise RuntimeError("Botão da aba descartável não identificado.")
        buttons[0].invoke()
        self.log("seed_tab_closed")

    def __enter__(self):
        from pywinauto import keyboard

        self.directory.mkdir(parents=True, exist_ok=False)
        with self.file.open("x", encoding="utf-8"):
            pass
        self.before = {w.handle for w in self.windows()}
        self.log("before_launch", existing_window_count=len(self.before), launch_attempts=1)
        try:
            subprocess.Popen(
                [str(Path(os.environ["SystemRoot"]) / "System32/notepad.exe"), str(self.file.resolve())],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 10
            seed = None
            while time.monotonic() < deadline:
                matches = [
                    w
                    for w in self.windows()
                    if w.class_name() == "Notepad" and self.file.stem in w.window_text()
                ]
                if len(matches) == 1:
                    seed = matches[0]
                    break
                time.sleep(0.1)
            if seed is None:
                raise RuntimeError("Aba descartável não identificada; nenhum relançamento.")
            baseline = {w.handle for w in self.windows()}
            seed.set_focus()
            if ctypes.windll.user32.GetForegroundWindow() != seed.handle:
                raise RuntimeError("Sem foco na aba descartável para criar janela exclusiva.")
            # Atalho documentado do Notepad. Uma solicitação, não modifica documento anterior.
            keyboard.send_keys("^+n", pause=0.05)
            self.log("new_window_requested_once")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                new = [w for w in self.windows() if w.class_name() == "Notepad" and w.handle not in baseline]
                if len(new) == 1:
                    win = new[0]
                    editor = self.editor(win)
                    self.handle, self.pid = win.handle, win.process_id()
                    self.document_id = editor.element_info.runtime_id
                    self.owned()
                    if editor.iface_text.DocumentRange.GetText(-1).strip():
                        raise RuntimeError("Documento novo não está vazio; nenhuma escrita permitida.")
                    self.verified_blank = True
                    self.close_seed_tab()
                    self.file.unlink(missing_ok=True)
                    self.log("owned_window", handle=self.handle, process_id=self.pid)
                    return self
                time.sleep(0.1)
            raise RuntimeError("Ctrl+Shift+N não criou janela exclusiva no prazo; sem repetição.")
        except Exception as exc:
            self.log("setup_failed", exception_type=type(exc).__name__, error=str(exc))
            self.close_seed_tab()
            self.file.unlink(missing_ok=True)
            if self.handle and self.verified_blank:
                self.cleanup()
            raise

    def cleanup(self):
        from app.tools.desktop_worker import set_native_text

        if not self.verified_blank:
            raise RuntimeError("Documento descartável não comprovado; limpeza recusada.")
        win = self.owned()
        editor = self.editor(win)
        editor_handle = editor.element_info.handle
        if not editor_handle or not ctypes.windll.user32.IsChild(self.handle, editor_handle):
            raise RuntimeError("Editor próprio sem HWND para descarte seguro.")
        set_native_text(editor_handle, "")
        if editor.iface_text.DocumentRange.GetText(-1).strip():
            raise RuntimeError("Descarte não confirmado; não fechar à força.")
        self.log("own_text_discarded")
        self.owned().close()
        deadline = time.monotonic() + 3
        while ctypes.windll.user32.IsWindow(self.handle) and time.monotonic() < deadline:
            time.sleep(0.1)
        if ctypes.windll.user32.IsWindow(self.handle):
            raise RuntimeError("Janela de teste não encerrou no prazo; intervenção necessária.")
        self.log("cleanup_confirmed", closed_handle=self.handle)
        current = {w.handle for w in self.windows()}
        if not self.before.issubset(current):
            raise RuntimeError("Uma janela anterior desapareceu durante o teste; investigar log.")
        self.log("previous_windows_preserved", count=len(self.before))

    def __exit__(self, exc_type, exc, traceback):
        self.log("cleanup_start", test_failed=exc_type is not None)
        try:
            self.cleanup()
        except Exception as cleanup_error:
            self.log("cleanup_failed", exception_type=type(cleanup_error).__name__, error=str(cleanup_error))
            raise
        finally:
            if ctypes.windll.user32.IsWindow(self.previous_focus):
                ctypes.windll.user32.SetForegroundWindow(self.previous_focus)
        return False
