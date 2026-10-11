"""Opt-in: uma janela própria, documento novo e descarte verificado, sem salvar texto."""

import asyncio
import json
import os
import platform

import pytest
from sqlalchemy import select

from app.config import RuntimeConfig
from app.database import Execution
from app.providers import ModelMessage, ProviderManager
from app.tools.desktop import ApplicationArguments, TypeArguments, desktop_call
from app.tools.registry import ToolError
from tests.conftest import wait_terminal
from tests.test_engine import ScriptedProvider, call

pytestmark = [
    pytest.mark.integration,
    pytest.mark.desktop,
    pytest.mark.skipif(
        platform.system() != "Windows" or os.environ.get("ARGOS_RUN_DESKTOP_TESTS") != "1",
        reason="GUI real exige Windows e ARGOS_RUN_DESKTOP_TESTS=1; não roda na suíte padrão.",
    ),
]


async def test_owned_notepad_real_write_idempotence_and_cleanup(services, monkeypatch):
    from uuid import uuid4

    from app.config import ROOT
    from app.sessions import session_state
    from tests.windows_harness import OwnedNotepad

    if not session_state().interactive:
        pytest.skip("Sessão interativa indisponível.")
    content = "ARGOS — teste controlado.\nDocumento novo e descartável."
    with OwnedNotepad(ROOT / "test-results" / ("owned-notepad-" + uuid4().hex)) as owned:
        arguments = {"handle": owned.handle, "expected_process_id": owned.pid, "text": content}

        class GUIProvider(ScriptedProvider):
            async def complete(self, messages, tools):
                if messages[-1]["role"] == "tool":
                    observation = json.loads(messages[-1]["content"])
                    owned.log(
                        "tool_verified",
                        confirmed=observation.get("confirmed"),
                        method=observation.get("method"),
                    )
                    if not observation.get("confirmed"):
                        raise ToolError("Escrita não confirmada. Teste interrompido, sem repetir.")
                return await super().complete(messages, tools)

        monkeypatch.setattr(
            ProviderManager,
            "create",
            lambda *_: GUIProvider(
                [
                    call("keyboard_type", arguments),
                    ModelMessage(content="Texto confirmado no documento descartável."),
                ]
            ),
        )
        await services.store.set_config(
            "runtime",
            RuntimeConfig(
                auto_approve_moderate=True,
                task_timeout=60,
                max_attempts=1,
                max_calls=3,
            ).model_dump(),
        )
        task = await services.tasks.create(services.test_user_id, "Teste de escrita em janela exclusiva.")
        final = await wait_terminal(services, task.id, timeout=55)
        assert final.status == "COMPLETED", final.error
        async with services.db.sessions() as session:
            result = await session.scalar(select(Execution).where(Execution.task_id == task.id))
        assert result.result["confirmed"]
        owned.log("write_confirmed", **result.result)

        repeated = await asyncio.wait_for(
            desktop_call(
                "keyboard_type",
                TypeArguments(**arguments),
                services.settings.data_dir,
            ),
            20,
        )
        assert repeated["confirmed"] and not repeated["action_executed"]
        owned.log("idempotence_confirmed", **repeated)
        reused = await asyncio.wait_for(
            desktop_call(
                "open_application",
                ApplicationArguments(application="notepad", reuse_handle=owned.handle),
                services.settings.data_dir,
            ),
            20,
        )
        assert reused["handle"] == owned.handle and not reused["action_executed"]
        owned.log("own_window_reused", handle=reused["handle"])
        with pytest.raises(ToolError, match="Documento contém texto"):
            await asyncio.wait_for(
                desktop_call(
                    "keyboard_type",
                    TypeArguments(**{**arguments, "text": "Não substituir"}),
                    services.settings.data_dir,
                ),
                20,
            )
        owned.log("existing_text_protected")
