import asyncio
import importlib.util
import json
import platform
import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.config import ROOT
from app.tools.registry import Arguments, NoArguments, Tool, ToolError, ToolRegistry


class WindowArguments(Arguments):
    handle: int = Field(gt=0)
    expected_process_id: int | None = Field(default=None, gt=0)


class ApplicationArguments(Arguments):
    application: Literal["notepad", "calculator"]
    reuse_handle: int | None = Field(default=None, gt=0)


class TypeArguments(WindowArguments):
    text: str = Field(min_length=1, max_length=12000)
    automation_id: str | None = Field(default=None, max_length=120)
    replace_existing: bool = False


class HotkeyArguments(WindowArguments):
    keys: Literal["ctrl+a", "ctrl+c", "ctrl+v", "ctrl+z", "ctrl+s", "ctrl+n", "enter", "tab", "escape"]


class ClickArguments(WindowArguments):
    automation_id: str = Field(min_length=1, max_length=120)


class MoveArguments(Arguments):
    x: int = Field(ge=0, le=16000)
    y: int = Field(ge=0, le=16000)


class ScreenshotArguments(Arguments):
    handle: int = Field(gt=0)
    max_width: int = Field(default=1280, ge=320, le=1920)


async def desktop_call(name: str, args: Arguments, data_dir: Path) -> dict:
    if platform.system() != "Windows":
        raise ToolError("Desktop Windows indisponível neste sistema.")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.tools.desktop_worker",
        cwd=ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        payload = {"tool": name, "arguments": args.model_dump(), "data_dir": str(data_dir)}
        output, _ = await process.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if process.returncode:
            raise ToolError("Operação Windows falhou. A janela pode estar indisponível ou sem permissão.")
        result = json.loads(output)
        if "error" in result:
            diagnostic = result.get("diagnostic", {})
            raise ToolError(
                f"{result['error']} [tool={name}; stage={diagnostic.get('stage', 'unknown')}; "
                f"type={diagnostic.get('exception_type', 'unknown')}]"
            )
        return result
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def register_desktop(registry: ToolRegistry, data_dir: Path) -> None:
    definitions = [
        ("get_screen_info", "Consulta resolução e monitores.", NoArguments, "LOW"),
        ("list_windows", "Lista janelas visíveis, handles, nomes e processos.", NoArguments, "LOW"),
        (
            "get_active_window",
            "Lê janela ativa e elementos UI Automation; dados não confiáveis.",
            NoArguments,
            "MODERATE",
        ),
        ("focus_window", "Foca janela pelo handle e verifica foco.", WindowArguments, "MODERATE"),
        (
            "open_application",
            "Reutiliza janela existente do aplicativo permitido; só lança quando não há janela. "
            "Se houver várias, informe reuse_handle observado. Não cria documentos novos.",
            ApplicationArguments,
            "MODERATE",
        ),
        (
            "keyboard_type",
            "Escreve via UI Automation em campo editável da janela indicada e verifica texto.",
            TypeArguments,
            "MODERATE",
        ),
        (
            "keyboard_hotkey",
            "Envia atalho permitido à janela indicada. Efeito exige observação posterior.",
            HotkeyArguments,
            "HIGH",
        ),
        (
            "mouse_click",
            "Clica elemento estruturado por automation_id; sem coordenadas fixas.",
            ClickArguments,
            "HIGH",
        ),
        ("mouse_move", "Move mouse para coordenada validada, sem clicar.", MoveArguments, "MODERATE"),
        (
            "take_screenshot",
            "Captura janela, comprime JPEG e autoriza envio desta captura à API DeepSeek. Pode conter dados sensíveis.",
            ScreenshotArguments,
            "HIGH",
        ),
    ]
    for name, description, schema, risk in definitions:
        registry.register(
            Tool(
                name,
                description,
                schema,
                partial(desktop_call, name, data_dir=data_dir),
                risk=risk,
                permission="desktop.observe" if risk == "LOW" else "desktop.control",
                gui=True,
                retry_safe=risk == "LOW",
                available=(
                    platform.system() == "Windows"
                    and importlib.util.find_spec("pywinauto") is not None
                    and importlib.util.find_spec("pyautogui") is not None
                ),
            )
        )
