"""Regressões simuladas de ferramentas; persistência/executor usam PostgreSQL real."""

import pytest
from sqlalchemy import select

from app.config import RuntimeConfig
from app.database import Execution
from app.providers import ModelMessage, ProviderManager
from app.tools.registry import NoArguments, Tool, ToolError
from tests.conftest import wait_terminal
from tests.test_engine import ScriptedProvider, call

pytestmark = pytest.mark.integration


async def test_gui_failure_stops_agent_without_second_action(services, monkeypatch):
    from app import executor

    monkeypatch.setattr(executor, "session_state", lambda: type("State", (), {"interactive": True})())
    attempts = []

    async def broken(args):
        attempts.append(1)
        raise ToolError("UIA indisponível [stage=resolve_edit_control]")

    services.registry.register(
        Tool("controlled_gui", "Teste", NoArguments, broken, gui=True, retry_safe=False)
    )
    provider = ScriptedProvider(
        [call("controlled_gui", {}), call("controlled_gui", {}), ModelMessage(content="Fim")]
    )
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    task = await services.tasks.create(services.test_user_id, "Falha gráfica simulada")
    final = await wait_terminal(services, task.id)
    assert final.status == "FAILED" and "resolve_edit_control" in final.error
    assert len(attempts) == 1 and len(provider.seen) == 1


async def test_uncertain_gui_action_cannot_be_sent_twice(services, monkeypatch):
    from app import executor

    monkeypatch.setattr(executor, "session_state", lambda: type("State", (), {"interactive": True})())
    attempts = []

    async def uncertain(args):
        attempts.append(1)
        return {"confirmed": False, "action_executed": True}

    services.registry.register(
        Tool("controlled_gui", "Teste", NoArguments, uncertain, gui=True, retry_safe=False)
    )
    provider = ScriptedProvider([call("controlled_gui", {}), call("controlled_gui", {}, "again")])
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    task = await services.tasks.create(services.test_user_id, "Ação sem confirmação")
    final = await wait_terminal(services, task.id)
    assert final.status == "FAILED" and "Repetição bloqueada" in final.error
    assert len(attempts) == 1
    async with services.db.sessions() as session:
        assert len((await session.scalars(select(Execution).where(Execution.task_id == task.id))).all()) == 1


async def test_repeated_read_failure_stops_model_loop_at_operation_limit(services, monkeypatch):
    attempts = []

    async def broken(args):
        attempts.append(1)
        raise ToolError("Leitura temporariamente indisponível")

    services.registry.register(Tool("broken_read", "Teste", NoArguments, broken))
    await services.store.set_config("runtime", RuntimeConfig(max_attempts=2).model_dump())
    provider = ScriptedProvider([call("broken_read", {}, str(i)) for i in range(8)])
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    task = await services.tasks.create(services.test_user_id, "Falha repetida")
    final = await wait_terminal(services, task.id)
    assert final.status == "FAILED" and "falhou repetidamente" in final.error
    assert len(attempts) == 2 and len(provider.seen) == 2
