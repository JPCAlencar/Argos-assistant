from datetime import timedelta

from sqlalchemy import delete, select, update

from app.config import ProviderConfig, RuntimeConfig
from app.database import Approval, ConfigEntry, Event, Execution, Task, WorkflowRun, now

ACTIVE = {"PENDING", "PLANNING", "RUNNING", "WAITING_APPROVAL"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class Store:
    def __init__(self, db, secrets):
        self.db, self.secrets = db, secrets

    async def get_config(self, key: str) -> dict:
        async with self.db.sessions() as session:
            entry = await session.get(ConfigEntry, key)
            return entry.value if entry else {}

    async def set_config(self, key: str, value: dict):
        async with self.db.sessions() as session:
            entry = await session.get(ConfigEntry, key)
            if entry:
                entry.value = value
            else:
                session.add(ConfigEntry(key=key, value=value))
            await session.commit()

    async def runtime(self) -> RuntimeConfig:
        return RuntimeConfig.model_validate(await self.get_config("runtime"))

    async def provider(self) -> tuple[ProviderConfig, str]:
        config = await self.get_config("provider")
        encrypted = config.pop("encrypted_key", "")
        return ProviderConfig.model_validate(config), self.secrets.decrypt(encrypted) if encrypted else ""

    async def emit(self, task_id: str, kind: str, data: dict):
        async with self.db.sessions() as session:
            session.add(Event(task_id=task_id, kind=kind, data=data))
            await session.commit()

    async def state(self, task_id: str, status: str, **values):
        async with self.db.sessions() as session:
            await session.execute(
                update(Task).where(Task.id == task_id).values(status=status, updated_at=now(), **values)
            )
            await session.commit()
        await self.emit(task_id, "status", {"status": status, **values})

    async def recover(self):
        """Não reexecuta ações após crash; aprovações antigas expiram."""
        async with self.db.sessions() as session:
            tasks = list((await session.scalars(select(Task).where(Task.status.in_(ACTIVE)))).all())
            for task in tasks:
                task.status = "FAILED"
                task.error = (
                    "Execução interrompida pelo reinício. Revise as ações antes de criar nova tarefa."
                )
                task.updated_at = now()
                session.add(
                    Event(task_id=task.id, kind="status", data={"status": "FAILED", "error": task.error})
                )
            await session.execute(
                update(Approval).where(Approval.status == "PENDING").values(status="EXPIRED")
            )
            await session.execute(
                update(Execution)
                .where(Execution.status == "RUNNING")
                .values(status="INTERRUPTED", result={"confirmed": False, "error": "Processo interrompido."})
            )
            await session.execute(
                update(WorkflowRun).where(WorkflowRun.status == "RUNNING").values(status="FAILED")
            )
            await session.commit()

    async def cleanup_events(self, days: int):
        async with self.db.sessions() as session:
            # Mantém eventos de tarefas ativas e auditoria. Apenas o feed detalhado tem retenção.
            await session.execute(
                delete(Event).where(
                    Event.created_at < now() - timedelta(days=days),
                    Event.task_id.in_(select(Task.id).where(Task.status.in_(TERMINAL))),
                )
            )
            await session.commit()
