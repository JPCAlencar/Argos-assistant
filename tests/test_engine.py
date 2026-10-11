import asyncio
import json

import pytest
from sqlalchemy import select

from app.config import ProviderConfig, RuntimeConfig
from app.database import Approval, Execution, Message, Task, ToolPermission, Usage, Workflow, WorkflowRun
from app.executor import ExecutionContext, LimitReached, PolicyDenied
from app.providers import Completion, ModelMessage, ProviderManager, ToolCall
from app.tools.registry import NoArguments, Tool, ToolError
from app.workflows import DEFAULT_WORKFLOW, WorkflowDefinition, WorkflowStep
from tests.conftest import pending_approval, wait_terminal

pytestmark = pytest.mark.integration


class ScriptedProvider:
    def __init__(self, messages):
        self.config = ProviderConfig()
        self.messages = iter(messages)
        self.seen = []

    async def complete(self, messages, tools):
        self.seen.append((list(messages), tools))
        return Completion(next(self.messages), 100, 10, 0, 1, 0.0001)


def call(tool, args, call_id="call1"):
    return ModelMessage(
        tool_calls=[ToolCall(id=call_id, function={"name": tool, "arguments": json.dumps(args)})]
    )


async def test_multi_step_agent_persists_real_tool_observations(services, monkeypatch):
    root = services.settings.allowed_roots[0]
    (root / "note.txt").write_text("Olá, ARGOS", encoding="utf-8")
    provider = ScriptedProvider(
        [
            call("file_exists", {"path": "note.txt"}),
            call("read_file", {"path": "note.txt"}, "call2"),
            ModelMessage(content="Li o arquivo autorizado."),
        ]
    )
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    task = await services.tasks.create(services.test_user_id, "Leia note.txt.")
    final = await wait_terminal(services, task.id)
    assert final.status == "COMPLETED"
    assert final.result == "Li o arquivo autorizado."
    assert final.estimated_cost == pytest.approx(0.0003)
    async with services.db.sessions() as session:
        assert len((await session.scalars(select(Execution).where(Execution.task_id == task.id))).all()) == 2
        assert len((await session.scalars(select(Usage).where(Usage.task_id == task.id))).all()) == 3
        assert (
            len(
                (
                    await session.scalars(
                        select(Message).where(Message.conversation_id == task.conversation_id)
                    )
                ).all()
            )
            == 2
        )
    assert any(m.get("role") == "tool" and "Olá, ARGOS" in m.get("content", "") for m in provider.seen[-1][0])


async def test_approval_blocks_until_exact_decision_then_consumes_once(services):
    context = ExecutionContext("approval-task", services.test_user_id, RuntimeConfig())
    async with services.db.sessions() as session:
        session.add(
            Task(id=context.task_id, user_id=services.test_user_id, objective="write", status="RUNNING")
        )
        await session.commit()
    worker = asyncio.create_task(
        services.executor.execute(context, "write_file", {"path": "approved.txt", "content": "OK"})
    )
    approval = await pending_approval(services, context.task_id)
    assert not (services.settings.allowed_roots[0] / "approved.txt").exists()
    assert not worker.done()
    async with services.db.sessions() as session:
        row = await session.get(Approval, approval.id)
        row.status = "APPROVED"
        await session.commit()
    services.executor.approval_events[approval.id].set()
    assert (await worker)["confirmed"]
    async with services.db.sessions() as session:
        assert (await session.get(Approval, approval.id)).status == "CONSUMED"
    assert (services.settings.allowed_roots[0] / "approved.txt").read_text() == "OK"


async def test_tampered_or_denied_approval_never_executes(services):
    context = ExecutionContext("denied-task", services.test_user_id, RuntimeConfig())
    async with services.db.sessions() as session:
        session.add(
            Task(id=context.task_id, user_id=services.test_user_id, objective="denied", status="RUNNING")
        )
        await session.commit()
    worker = asyncio.create_task(
        services.executor.execute(context, "write_file", {"path": "denied.txt", "content": "OK"})
    )
    approval = await pending_approval(services, context.task_id)
    async with services.db.sessions() as session:
        row = await session.get(Approval, approval.id)
        row.status, row.fingerprint = "APPROVED", "tampered"
        await session.commit()
    services.executor.approval_events[approval.id].set()
    with pytest.raises(PolicyDenied):
        await worker
    assert not (services.settings.allowed_roots[0] / "denied.txt").exists()


async def test_policy_disabled_and_budget_and_steps(services):
    context = ExecutionContext("irrelevant", services.test_user_id, RuntimeConfig(max_steps=1))
    async with services.db.sessions() as session:
        session.add(ToolPermission(name="get_system_status", enabled=False))
        await session.commit()
    with pytest.raises(PolicyDenied):
        await services.executor.execute(context, "get_system_status", {})
    with pytest.raises(LimitReached):
        await services.executor.execute(context, "file_exists", {"path": "a"})
    assert all(t["function"]["name"] != "get_system_status" for t in await services.executor.available())


async def test_workflow_is_deterministic_and_logged(services, monkeypatch):
    def never(*_):
        raise AssertionError("Workflow não deve usar IA")

    monkeypatch.setattr(ProviderManager, "create", never)
    async with services.db.sessions() as session:
        workflow = Workflow(**DEFAULT_WORKFLOW)
        session.add(workflow)
        await session.commit()
    task = await services.tasks.create(services.test_user_id, "diagnóstico", workflow_id=workflow.id)
    final = await wait_terminal(services, task.id)
    assert final.status == "COMPLETED"
    assert final.estimated_cost == 0
    async with services.db.sessions() as session:
        run = await session.scalar(select(WorkflowRun).where(WorkflowRun.task_id == task.id))
        assert run.status == "COMPLETED"
        assert len((await session.scalars(select(Execution).where(Execution.task_id == task.id))).all()) == 3


async def test_retry_only_safe_tools_and_failed_step(services):
    count = 0

    async def flaky(_):
        nonlocal count
        count += 1
        if count == 1:
            raise ToolError("temporary")
        return {"confirmed": True}

    services.registry.register(Tool("flaky", "test", NoArguments, flaky))
    definition = WorkflowDefinition(steps=[WorkflowStep(tool="flaky", retries=1)])
    async with services.db.sessions() as session:
        workflow = Workflow(name="flaky", description="test", definition=definition.model_dump())
        session.add(workflow)
        await session.commit()
    task = await services.tasks.create(services.test_user_id, "retry", workflow_id=workflow.id)
    assert (await wait_terminal(services, task.id)).status == "COMPLETED"
    assert count == 2
    with pytest.raises(ToolError):
        services.workflows.validate_tools(
            WorkflowDefinition(steps=[WorkflowStep(tool="write_file", retries=1)])
        )


async def test_cooperative_cancel_interrupts_tool_and_approval(services, monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def slow(_):
        try:
            started.set()
            await asyncio.sleep(10)
        finally:
            stopped.set()

    services.registry.register(Tool("slow", "slow test", NoArguments, slow))
    monkeypatch.setattr(ProviderManager, "create", lambda *_: ScriptedProvider([call("slow", {})]))
    task = await services.tasks.create(services.test_user_id, "cancel")
    await asyncio.wait_for(started.wait(), timeout=5)
    await services.tasks.cancel(task.id)
    assert stopped.is_set()
    assert (await wait_terminal(services, task.id)).status == "CANCELLED"
    monkeypatch.setattr(
        ProviderManager,
        "create",
        lambda *_: ScriptedProvider([call("write_file", {"path": "cancel.txt", "content": "never"})]),
    )
    task = await services.tasks.create(services.test_user_id, "cancel approval")
    approval = await pending_approval(services, task.id)
    await services.tasks.cancel(task.id)
    assert (await wait_terminal(services, task.id)).status == "CANCELLED"
    async with services.db.sessions() as session:
        assert (await session.get(Approval, approval.id)).status == "EXPIRED"
    assert not (services.settings.allowed_roots[0] / "cancel.txt").exists()


async def test_tool_timeout_and_max_calls_stop_agent(services, monkeypatch):
    async def slow(_):
        await asyncio.sleep(10)

    services.registry.register(Tool("slow", "timeout", NoArguments, slow, timeout=1))
    await services.store.set_config("runtime", RuntimeConfig(max_calls=1, max_attempts=1).model_dump())
    provider = ScriptedProvider([call("slow", {})])
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    task = await services.tasks.create(services.test_user_id, "timeout")
    final = await wait_terminal(services, task.id)
    assert final.status == "FAILED"
    assert "chamadas" in final.error
    async with services.db.sessions() as session:
        row = await session.scalar(select(Execution).where(Execution.task_id == task.id))
        assert "Timeout" in row.result["error"]


async def test_restart_retains_history_and_expires_incomplete_actions(services):
    async with services.db.sessions() as session:
        session.add(
            Task(id="crash-task", user_id=services.test_user_id, objective="interrupted", status="RUNNING")
        )
        session.add(
            Task(
                id="completed-task",
                user_id=services.test_user_id,
                objective="done",
                status="COMPLETED",
                result="OK",
            )
        )
        await session.flush()
        session.add(
            Approval(task_id="crash-task", tool="write_file", arguments={}, risk="HIGH", fingerprint="x")
        )
        session.add(Execution(task_id="crash-task", tool="write_file", arguments={}, status="RUNNING"))
        await session.commit()
    await services.store.set_config("runtime", RuntimeConfig(name="Minha preferência").model_dump())
    await services.store.recover()
    async with services.db.sessions() as session:
        assert (await session.get(Task, "crash-task")).status == "FAILED"
        assert (await session.get(Task, "completed-task")).result == "OK"
        assert (await session.scalar(select(Approval))).status == "EXPIRED"
        assert (await session.scalar(select(Execution))).status == "INTERRUPTED"
    assert (await services.store.runtime()).name == "Minha preferência"
