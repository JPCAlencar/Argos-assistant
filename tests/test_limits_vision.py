import asyncio

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import RuntimeConfig
from app.database import Approval, Execution, Task, Workflow, WorkflowRun
from app.executor import ExecutionContext, PolicyDenied
from app.providers import ModelMessage, ProviderManager
from app.tools.registry import NoArguments, Tool, ToolError
from app.workflows import WorkflowDefinition, WorkflowStep
from tests.conftest import pending_approval, wait_terminal
from tests.test_engine import ScriptedProvider, call

pytestmark = pytest.mark.integration


async def decide(services, approval_id, approved):
    async with services.db.sessions() as session:
        row = await session.get(Approval, approval_id)
        row.status = "APPROVED" if approved else "DENIED"
        await session.commit()
    services.executor.approval_events[approval_id].set()


async def test_immediate_cancel_before_worker_runs(services):
    task = await services.tasks.create(services.test_user_id, "Cancel before starting")
    await services.tasks.cancel(task.id)
    final = await wait_terminal(services, task.id)
    assert final.status == "CANCELLED"
    assert task.id not in services.tasks.contexts
    assert task.id not in services.tasks.running


async def test_estimated_budget_stops_before_paid_call(services, monkeypatch):
    await services.store.set_config("runtime", RuntimeConfig(max_task_cost=0.000001).model_dump())
    provider = ScriptedProvider([ModelMessage(content="must not call")])
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    task = await services.tasks.create(services.test_user_id, "Budget limited")
    final = await wait_terminal(services, task.id)
    assert final.status == "FAILED"
    assert "Orçamento" in final.error
    assert provider.seen == []


async def test_concurrency_and_total_timeout(services, monkeypatch):
    class SlowProvider(ScriptedProvider):
        async def complete(self, messages, tools):
            await asyncio.sleep(10)

    monkeypatch.setattr(ProviderManager, "create", lambda *_: SlowProvider([]))
    await services.store.set_config("runtime", RuntimeConfig(max_concurrent=1, task_timeout=5).model_dump())
    task = await services.tasks.create(services.test_user_id, "Long controlled task")
    with pytest.raises(ValueError, match="simultâneas"):
        await services.tasks.create(services.test_user_id, "Second task")
    assert (await wait_terminal(services, task.id, timeout=7)).status == "TIMED_OUT"


async def test_permission_rechecked_after_approval(services):
    from app.database import ToolPermission

    context = ExecutionContext("revalidate", services.test_user_id, RuntimeConfig())
    async with services.db.sessions() as session:
        session.add(
            Task(id=context.task_id, user_id=services.test_user_id, objective="revalidate", status="RUNNING")
        )
        await session.commit()
    worker = asyncio.create_task(
        services.executor.execute(context, "write_file", {"path": "blocked.txt", "content": "x"})
    )
    approval = await pending_approval(services, context.task_id)
    async with services.db.sessions() as session:
        session.add(ToolPermission(name="write_file", enabled=False))
        await session.commit()
    await decide(services, approval.id, True)
    with pytest.raises(PolicyDenied, match="desativada"):
        await worker
    assert not (services.settings.allowed_roots[0] / "blocked.txt").exists()


async def test_overwrite_remains_high_even_when_moderate_auto_approved(services):
    root = services.settings.allowed_roots[0]
    (root / "original.txt").write_text("original", encoding="utf-8")
    context = ExecutionContext("overwrite", services.test_user_id, RuntimeConfig(auto_approve_moderate=True))
    async with services.db.sessions() as session:
        session.add(
            Task(id=context.task_id, user_id=services.test_user_id, objective="overwrite", status="RUNNING")
        )
        await session.commit()
    worker = asyncio.create_task(
        services.executor.execute(
            context, "write_file", {"path": "original.txt", "content": "changed", "overwrite": True}
        )
    )
    approval = await pending_approval(services, context.task_id)
    assert approval.risk == "HIGH"
    assert (root / "original.txt").read_text() == "original"
    await decide(services, approval.id, False)
    with pytest.raises(PolicyDenied):
        await worker
    assert (root / "original.txt").read_text() == "original"


async def test_simulated_visual_observation_requires_approval_and_builds_image_payload(services, monkeypatch):
    folder = services.settings.data_dir / "screenshots"
    folder.mkdir(parents=True)
    asset = "a" * 32 + ".jpg"
    Image.new("RGB", (20, 20), "black").save(folder / asset)

    async def fake_capture(_):
        return {"asset": asset, "confirmed": True, "image_authorized": True}

    tool = services.registry.tools["take_screenshot"]
    tool.gui, tool.available, tool.handler = False, True, fake_capture
    provider = ScriptedProvider(
        [call("take_screenshot", {"handle": 123}), ModelMessage(content="Imagem de teste observada.")]
    )
    monkeypatch.setattr(ProviderManager, "create", lambda *_: provider)
    await services.store.set_config("runtime", RuntimeConfig(screenshots_enabled=True).model_dump())
    task = await services.tasks.create(services.test_user_id, "Observe simulated screen")
    approval = await pending_approval(services, task.id)
    assert len(provider.seen) == 1
    await decide(services, approval.id, True)
    assert (await wait_terminal(services, task.id)).status == "COMPLETED"
    content = provider.seen[-1][0][-1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    async with services.db.sessions() as session:
        execution = await session.scalar(select(Execution).where(Execution.task_id == task.id))
        assert "base64" not in str(execution.result)


async def test_workflow_failure_logs_step_without_unsafe_recovery(services, monkeypatch):
    async def broken(_):
        raise ToolError("controlled error")

    services.registry.register(Tool("broken", "controlled", NoArguments, broken))
    async with services.db.sessions() as session:
        workflow = Workflow(
            name="Broken",
            description="test",
            definition=WorkflowDefinition(steps=[WorkflowStep(tool="broken")], recovery="stop").model_dump(),
        )
        session.add(workflow)
        await session.commit()

    def never(*_):
        raise AssertionError("No unauthorized recovery")

    monkeypatch.setattr(ProviderManager, "create", never)
    task = await services.tasks.create(services.test_user_id, "broken", workflow_id=workflow.id)
    assert (await wait_terminal(services, task.id)).status == "FAILED"
    async with services.db.sessions() as session:
        run = await session.scalar(select(WorkflowRun).where(WorkflowRun.task_id == task.id))
        assert run.status == "FAILED"
        assert run.failed_step == 0
