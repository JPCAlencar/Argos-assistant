import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select, update

from app.database import Approval, Conversation, Message, Task, identifier, now
from app.executor import ExecutionContext
from app.providers import ProviderManager
from app.security import redact
from app.store import ACTIVE, TERMINAL
from app.workflows import WorkflowFailure

logger = logging.getLogger("argos")


class TaskManager:
    def __init__(self, services):
        self.services = services
        self.running: dict[str, asyncio.Task] = {}
        self.contexts: dict[str, ExecutionContext] = {}
        self.create_lock = asyncio.Lock()

    async def create(
        self,
        user_id: str,
        objective: str,
        conversation_id: str | None = None,
        workflow_id: str | None = None,
        parameters: dict | None = None,
    ) -> Task:
        config = await self.services.store.runtime()
        provider_config, _ = await self.services.store.provider()
        async with self.create_lock:
            async with self.services.db.sessions() as session:
                active = list((await session.scalars(select(Task.id).where(Task.status.in_(ACTIVE)))).all())
                if len(active) >= config.max_concurrent:
                    raise ValueError("Limite de tarefas simultâneas atingido. Aguarde ou cancele uma tarefa.")
                if conversation_id:
                    conversation = await session.get(Conversation, conversation_id)
                    if not conversation or conversation.user_id != user_id:
                        raise ValueError("Conversa não encontrada.")
                    if await session.scalar(
                        select(Task.id).where(
                            Task.conversation_id == conversation_id, Task.status.in_(ACTIVE)
                        )
                    ):
                        raise ValueError("Aguarde a tarefa atual desta conversa.")
                elif not workflow_id:
                    conversation_id = identifier()
                    session.add(
                        Conversation(id=conversation_id, user_id=user_id, title=redact(objective[:120]))
                    )
                    await session.flush()
                task = Task(
                    id=identifier(),
                    user_id=user_id,
                    conversation_id=conversation_id,
                    objective=redact(objective),
                    model="workflow" if workflow_id else provider_config.model,
                    status="PENDING",
                    estimated_cost=0,
                )
                session.add(task)
                if conversation_id:
                    session.add(
                        Message(conversation_id=conversation_id, role="user", content=redact(objective))
                    )
                await session.commit()
            execution = ExecutionContext(task.id, user_id, config)
            self.contexts[task.id] = execution
            self.running[task.id] = asyncio.create_task(
                self._run(task, execution, workflow_id, parameters or {})
            )
            return task

    async def _run(self, task: Task, execution: ExecutionContext, workflow_id: str | None, parameters: dict):
        try:
            async with asyncio.timeout(execution.config.task_timeout):
                await self.services.store.state(task.id, "PLANNING")
                await self.services.store.state(task.id, "RUNNING")
                if workflow_id:
                    try:
                        result_data = await self.services.executor.execute(
                            execution, "run_workflow", {"workflow_id": workflow_id, "parameters": parameters}
                        )
                        result = f"Rotina concluída: {result_data['workflow']}. {result_data['steps']} etapas confirmadas."
                    except WorkflowFailure as exc:
                        if not exc.recoverable:
                            raise
                        config, key = await self.services.store.provider()
                        result = await self.services.agent.run(
                            task, execution, ProviderManager.create(config, key), str(exc)
                        )
                else:
                    config, key = await self.services.store.provider()
                    result = await self.services.agent.run(
                        task, execution, ProviderManager.create(config, key)
                    )
                async with self.services.db.sessions() as session:
                    if task.conversation_id:
                        session.add(
                            Message(conversation_id=task.conversation_id, role="assistant", content=result)
                        )
                    await session.execute(
                        update(Task)
                        .where(Task.id == task.id)
                        .values(status="COMPLETED", result=result, updated_at=now())
                    )
                    await session.commit()
                await self.services.store.emit(task.id, "status", {"status": "COMPLETED", "result": result})
        except asyncio.CancelledError:
            await self.services.store.state(
                task.id, "CANCELLED", error="Tarefa cancelada. Ações já realizadas permanecem."
            )
        except TimeoutError:
            await self.services.store.state(task.id, "TIMED_OUT", error="Timeout geral da tarefa atingido.")
        except Exception as exc:
            # Não inclui payload do provedor, stacktrace com variáveis ou segredos.
            error = redact(str(exc))[:800] or type(exc).__name__
            logger.warning("Tarefa %s falhou (%s)", task.id, type(exc).__name__)
            await self.services.store.state(task.id, "FAILED", error=error)
        finally:
            async with self.services.db.sessions() as session:
                await session.execute(
                    update(Approval)
                    .where(Approval.task_id == task.id, Approval.status.in_(["PENDING", "APPROVED"]))
                    .values(status="EXPIRED")
                )
                await session.commit()
            self.contexts.pop(task.id, None)
            self.running.pop(task.id, None)

    async def cancel(self, task_id: str):
        execution = self.contexts.get(task_id)
        worker = self.running.get(task_id)
        if worker and execution:
            execution.cancel.set()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.contexts.pop(task_id, None)
        self.running.pop(task_id, None)
        # Cancelamento pode chegar antes da primeira instrução do worker.
        async with self.services.db.sessions() as session:
            task = await session.get(Task, task_id)
            active = task and task.status not in TERMINAL
            await session.execute(
                update(Approval)
                .where(Approval.task_id == task_id, Approval.status.in_(["PENDING", "APPROVED"]))
                .values(status="EXPIRED")
            )
            await session.commit()
        if active:
            await self.services.store.state(task_id, "CANCELLED", error="Tarefa cancelada antes de concluir.")

    async def shutdown(self):
        for execution in self.contexts.values():
            execution.cancel.set()
        for worker in list(self.running.values()):
            worker.cancel()
        await asyncio.gather(*list(self.running.values()), return_exceptions=True)


async def maintenance(services):
    """Retenção leve de binários, feed de eventos e sessões expiradas."""
    while True:
        try:
            config = await services.store.runtime()
            folder = services.settings.data_dir / "screenshots"
            cutoff = (now() - timedelta(hours=config.screenshot_retention_hours)).timestamp()

            def clean():
                if folder.is_dir():
                    for path in folder.glob("*.jpg"):
                        if path.is_file() and path.stat().st_mtime < cutoff:
                            path.unlink()

            await asyncio.to_thread(clean)
            await services.store.cleanup_events(config.event_retention_days)
            from app.database import AdminSession

            async with services.db.sessions() as session:
                await session.execute(delete(AdminSession).where(AdminSession.expires_at < now()))
                await session.commit()
        except Exception as exc:
            logger.warning("Retenção falhou (%s)", type(exc).__name__)
        await asyncio.sleep(3600)
