import asyncio
import json
import time
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select, update

from app.config import RuntimeConfig
from app.database import Approval, Execution, Task, ToolPermission, identifier, now
from app.security import SECRET_PATTERN, audit, fingerprint, redact, redact_data
from app.sessions import session_state
from app.tools.registry import ToolError


class PolicyDenied(ToolError):
    pass


class LimitReached(ToolError):
    pass


@dataclass
class ExecutionContext:
    task_id: str
    user_id: str
    config: RuntimeConfig
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    steps: int = 0
    tool_failures: dict[str, int] = field(default_factory=dict)
    uncertain_gui_actions: set[str] = field(default_factory=set)
    sent_gui_actions: set[str] = field(default_factory=set)


class ToolExecutor:
    def __init__(self, services):
        self.services = services
        self.gui_lock = asyncio.Lock()
        self.approval_events: dict[str, asyncio.Event] = {}

    async def available(self) -> list[dict]:
        async with self.services.db.sessions() as session:
            disabled = set(
                (
                    await session.scalars(
                        select(ToolPermission.name).where(ToolPermission.enabled.is_(False))
                    )
                ).all()
            )
        return [
            tool.schema()
            for name, tool in self.services.registry.tools.items()
            if tool.available and name not in disabled
        ]

    async def permission(self, name: str):
        async with self.services.db.sessions() as session:
            permission = await session.get(ToolPermission, name)
            if permission and not permission.enabled:
                raise PolicyDenied("Ferramenta desativada pela política administrativa.")

    async def approval(self, context: ExecutionContext, tool, arguments: dict, risk: str):
        approval_id = identifier()
        event = asyncio.Event()
        self.approval_events[approval_id] = event
        expected = fingerprint(tool.name, arguments)
        try:
            async with self.services.db.sessions() as session:
                session.add(
                    Approval(
                        id=approval_id,
                        task_id=context.task_id,
                        tool=tool.name,
                        arguments=arguments,
                        fingerprint=expected,
                        risk=risk,
                    )
                )
                await session.execute(
                    update(Task)
                    .where(Task.id == context.task_id)
                    .values(status="WAITING_APPROVAL", updated_at=now())
                )
                await session.commit()
            await self.services.store.emit(context.task_id, "status", {"status": "WAITING_APPROVAL"})
            await self.services.store.emit(
                context.task_id,
                "approval",
                {"id": approval_id, "tool": tool.name, "arguments": arguments, "risk": risk},
            )
            await event.wait()
            async with self.services.db.sessions() as session:
                approval = await session.get(Approval, approval_id, with_for_update=True)
                if not approval or approval.status != "APPROVED" or approval.fingerprint != expected:
                    raise PolicyDenied("Ação não aprovada pelo usuário.")
                approval.status = "CONSUMED"
                await session.commit()
            await self.services.store.state(context.task_id, "RUNNING")
        finally:
            self.approval_events.pop(approval_id, None)
            async with self.services.db.sessions() as session:
                await session.execute(
                    update(Approval)
                    .where(Approval.id == approval_id, Approval.status.in_(["PENDING", "APPROVED"]))
                    .values(status="EXPIRED")
                )
                await session.commit()

    async def execute(
        self,
        context: ExecutionContext,
        name: str,
        raw: dict,
        timeout: int | None = None,
        retries: int | None = None,
    ) -> dict:
        if context.cancel.is_set():
            raise asyncio.CancelledError
        context.steps += 1
        if context.steps > context.config.max_steps:
            raise LimitReached("Limite de passos da tarefa atingido.")
        tool = self.services.registry.get(name)
        if context.tool_failures.get(name, 0) >= context.config.max_attempts:
            raise LimitReached(f"Ferramenta {name} falhou repetidamente. Tarefa interrompida.")
        await self.permission(name)
        try:
            args = tool.arguments.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(".".join(str(v) for v in err["loc"]) for err in exc.errors())
            raise ToolError(f"Parâmetros inválidos: {fields}.") from exc
        arguments = args.model_dump()
        action_key = fingerprint(name, arguments)
        if name in {"keyboard_hotkey", "mouse_click"} and action_key in context.sent_gui_actions:
            raise LimitReached(f"Ação {name} já enviada nesta tarefa. Comando duplicado bloqueado.")
        if tool.gui and not tool.retry_safe and action_key in context.uncertain_gui_actions:
            raise LimitReached(
                f"Ação gráfica {name} já foi enviada sem confirmação. Repetição bloqueada; "
                "verifique o estado antes de iniciar outra tarefa."
            )
        if any(
            SECRET_PATTERN.search(str(arguments.get(field, ""))) or "sk-" in str(arguments.get(field, ""))
            for field in ("text", "content")
        ):
            raise PolicyDenied("Manipulação de credenciais não está disponível nesta versão.")
        risk = (
            "HIGH"
            if (
                name == "write_file"
                and arguments.get("overwrite")
                or name == "keyboard_type"
                and arguments.get("replace_existing")
            )
            else tool.risk
        )
        if name == "take_screenshot" and not context.config.screenshots_enabled:
            raise PolicyDenied("Capturas desativadas. Habilite nas Configurações antes de solicitar captura.")
        if tool.gui and not session_state().interactive:
            raise PolicyDenied("Desktop interativo indisponível. Faça autenticação assistida pelo Windows.")
        # Mesmo com auto aprovação moderada, risco alto sempre exige confirmação exata.
        if risk == "HIGH" or risk == "MODERATE" and not context.config.auto_approve_moderate:
            await self.approval(context, tool, arguments, risk)
        await self.permission(name)  # revalida após espera por aprovação
        if context.cancel.is_set():
            raise asyncio.CancelledError
        if tool.gui and not session_state().interactive:
            raise PolicyDenied("Sessão ficou indisponível após aprovação.")
        execution_id, started = identifier(), time.monotonic()
        # Texto de arquivo/digitação não entra no histórico de execução; fica só na aprovação concreta.
        logged_args = {
            key: "[conteúdo omitido]" if key in {"content", "text"} else value
            for key, value in arguments.items()
        }
        async with self.services.db.sessions() as session:
            session.add(
                Execution(
                    id=execution_id,
                    task_id=context.task_id,
                    tool=name,
                    arguments=logged_args,
                    status="RUNNING",
                )
            )
            await session.commit()
        await audit(
            self.services.db,
            "tool_execute",
            {"task_id": context.task_id, "tool": name, "risk": risk, "execution_id": execution_id},
            context.user_id,
        )
        await self.services.store.emit(
            context.task_id, "tool_start", {"tool": name, "execution_id": execution_id}
        )
        status, result = "FAILED", {"confirmed": False}
        attempts = (
            (retries + 1 if retries is not None else context.config.max_attempts) if tool.retry_safe else 1
        )
        attempts = min(attempts, context.config.max_attempts)
        attempts = min(attempts, context.config.max_attempts - context.tool_failures.get(name, 0))
        failure_recorded = False
        try:
            if name == "run_workflow":
                result = await self.services.workflows.run(context, args.workflow_id, args.parameters)
            else:

                async def call():
                    async with asyncio.timeout(min(tool.timeout, timeout or context.config.tool_timeout)):
                        return await tool.handler(args)

                for attempt in range(attempts):
                    try:
                        if name in {"keyboard_hotkey", "mouse_click"}:
                            context.sent_gui_actions.add(action_key)
                        if tool.gui:
                            async with self.gui_lock:
                                result = await call()
                        else:
                            result = await call()
                        break
                    except (ToolError, OSError, TimeoutError) as exc:
                        context.tool_failures[name] = context.tool_failures.get(name, 0) + 1
                        failure_recorded = True
                        if attempt + 1 == attempts:
                            if isinstance(exc, TimeoutError):
                                raise ToolError(
                                    "Timeout da ferramenta; ação pode não ter sido confirmada."
                                ) from exc
                            raise
                        await self.services.store.emit(
                            context.task_id, "retry", {"tool": name, "attempt": attempt + 2}
                        )
                        await asyncio.sleep(0.25)
            # Limita observações e higieniza strings; não armazena base64.
            serialized = json.dumps(result, ensure_ascii=False)
            if len(serialized) > 30000:
                result = {"truncated": True, "observation": redact(serialized[:24000]), "confirmed": False}
            else:
                result = redact_data(result)
            if tool.gui and not tool.retry_safe and not result.get("confirmed", False):
                context.uncertain_gui_actions.add(action_key)
                context.tool_failures[name] = context.tool_failures.get(name, 0) + 1
            else:
                context.tool_failures.pop(name, None)
            status = "COMPLETED"
            return result
        except asyncio.CancelledError:
            if tool.gui and not tool.retry_safe:
                context.uncertain_gui_actions.add(action_key)
            status = "CANCELLED"
            result = {"error": "Execução cancelada; verifique efeitos já realizados.", "confirmed": False}
            raise
        except Exception as exc:
            if not failure_recorded:
                context.tool_failures[name] = context.tool_failures.get(name, 0) + 1
            if tool.gui and not tool.retry_safe:
                context.uncertain_gui_actions.add(action_key)
            result = {"error": redact(str(exc))[:500], "confirmed": False}
            raise
        finally:
            async with self.services.db.sessions() as session:
                await session.execute(
                    update(Execution)
                    .where(Execution.id == execution_id)
                    .values(
                        status=status, result=result, duration_ms=int((time.monotonic() - started) * 1000)
                    )
                )
                await session.commit()
            await self.services.store.emit(
                context.task_id,
                "tool_end",
                {
                    "tool": name,
                    "execution_id": execution_id,
                    "status": status,
                    "confirmed": result.get("confirmed", False),
                    "error": result.get("error"),
                },
            )
