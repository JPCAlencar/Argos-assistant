import asyncio
import json
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select

from app.config import ProviderConfig, RuntimeConfig
from app.database import (
    AdminSession,
    Approval,
    Audit,
    Conversation,
    Device,
    Event,
    Execution,
    Memory,
    Message,
    Task,
    ToolPermission,
    Usage,
    Workflow,
    WorkflowRun,
    now,
)
from app.providers import ProviderError, ProviderManager
from app.security import SECRET_PATTERN, audit, authenticate, login, redact
from app.sessions import local_device
from app.store import TERMINAL
from app.tools.registry import ToolError
from app.workflows import WorkflowDefinition

router = APIRouter(prefix="/api")
Admin = Annotated[AdminSession, Depends(authenticate)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


def serialize(row, exclude: set[str] | None = None) -> dict:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in (exclude or set())
    }


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def sign_in(payload: LoginInput, request: Request, response: Response):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Origem não autorizada.")
    services = request.app.state.services
    token, csrf = await login(
        services.db,
        services.settings,
        payload.username,
        payload.password,
        request.client.host if request.client else "unknown",
    )
    response.set_cookie(
        "argos_session",
        token,
        httponly=True,
        secure=services.settings.cookie_secure,
        samesite="strict",
        max_age=services.settings.session_hours * 3600,
        path="/",
    )
    return {"csrf_token": csrf}


@router.get("/me")
async def me(admin: Admin):
    return {"user_id": admin.user_id, "csrf_token": admin.csrf_token}


@router.post("/logout")
async def sign_out(request: Request, response: Response, admin: Admin):
    async with request.app.state.services.db.sessions() as session:
        await session.execute(delete(AdminSession).where(AdminSession.token_hash == admin.token_hash))
        await session.commit()
    response.delete_cookie("argos_session")
    return {"ok": True}


class PasswordInput(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.put("/password")
async def change_password(payload: PasswordInput, request: Request, admin: Admin):
    from argon2.exceptions import VerificationError

    from app.database import User
    from app.security import hasher

    async with request.app.state.services.db.sessions() as session:
        user = await session.get(User, admin.user_id)
        try:
            await asyncio.to_thread(hasher.verify, user.password_hash, payload.current_password)
        except VerificationError as exc:
            raise HTTPException(403, "Senha atual inválida.") from exc
        user.password_hash = await asyncio.to_thread(hasher.hash, payload.new_password)
        await session.execute(
            delete(AdminSession).where(
                AdminSession.user_id == admin.user_id, AdminSession.token_hash != admin.token_hash
            )
        )
        await session.commit()
    await audit(request.app.state.services.db, "password_changed", {}, admin.user_id)
    return {"ok": True}


@router.get("/dashboard")
async def dashboard(request: Request, admin: Admin):
    services = request.app.state.services
    provider, key = await services.store.provider()
    async with services.db.sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(Task)
                    .where(Task.user_id == admin.user_id)
                    .order_by(Task.created_at.desc())
                    .limit(8)
                )
            ).all()
        )
        total = await session.scalar(select(func.coalesce(func.sum(Usage.estimated_cost), 0)))
        calls = await session.scalar(select(func.count()).select_from(Usage))
        tokens = await session.scalar(
            select(func.coalesce(func.sum(Usage.input_tokens + Usage.output_tokens), 0))
        )
    device = await asyncio.to_thread(local_device)
    alerts = []
    if not key:
        alerts.append("Cadastre sua chave DeepSeek em Modelos para conversar com o agente.")
    if not device["session"]["interactive"]:
        alerts.append(device["session"]["message"])
    if not provider.input_price or not provider.output_price:
        alerts.append("Preços não configurados: estimativa e limite financeiro estão incompletos.")
    return {
        "status": "ONLINE",
        "model": provider.model,
        "provider_configured": bool(key),
        "device": device,
        "tasks": [serialize(t) for t in rows],
        "running": len(services.tasks.running),
        "estimated_cost": total,
        "calls": calls,
        "tokens": tokens,
        "alerts": alerts,
    }


class TaskInput(Input):
    objective: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=36)


@router.post("/tasks", status_code=201)
async def create_task(payload: TaskInput, request: Request, admin: Admin):
    try:
        task = await request.app.state.services.tasks.create(
            admin.user_id, payload.objective, payload.conversation_id
        )
        await audit(request.app.state.services.db, "task_created", {"task_id": task.id}, admin.user_id)
        return serialize(task)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/tasks")
async def tasks(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(
                select(Task)
                .where(Task.user_id == admin.user_id)
                .order_by(Task.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [serialize(row) for row in rows]


async def owned_task(request: Request, admin: AdminSession, task_id: str) -> Task:
    async with request.app.state.services.db.sessions() as session:
        task = await session.get(Task, task_id)
    if not task or task.user_id != admin.user_id:
        raise HTTPException(404, "Tarefa não encontrada.")
    return task


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str, request: Request, admin: Admin):
    task = await owned_task(request, admin, task_id)
    async with request.app.state.services.db.sessions() as session:
        executions = (
            await session.scalars(
                select(Execution)
                .where(Execution.task_id == task_id)
                .order_by(Execution.created_at)
                .limit(100)
            )
        ).all()
        approvals = (
            await session.scalars(
                select(Approval).where(Approval.task_id == task_id).order_by(Approval.created_at).limit(100)
            )
        ).all()
        usage = (await session.scalars(select(Usage).where(Usage.task_id == task_id).limit(100))).all()
    return {
        **serialize(task),
        "executions": [serialize(e) for e in executions],
        "approvals": [serialize(a, {"fingerprint"}) for a in approvals],
        "usage": [serialize(u) for u in usage],
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, admin: Admin):
    await owned_task(request, admin, task_id)
    await request.app.state.services.tasks.cancel(task_id)
    await audit(request.app.state.services.db, "task_cancelled", {"task_id": task_id}, admin.user_id)
    return {"ok": True}


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request, admin: Admin, after: int = Query(default=0, ge=0)):
    await owned_task(request, admin, task_id)
    try:
        cursor = max(after, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        cursor = after

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            # Starlette cancela o stream repetidamente após desconexão. Protege
            # somente as transações curtas para garantir devolução da conexão.
            with anyio.CancelScope(shield=True):
                try:
                    await authenticate(request)
                except HTTPException:
                    return
                async with request.app.state.services.db.sessions() as session:
                    events = (
                        await session.scalars(
                            select(Event)
                            .where(Event.task_id == task_id, Event.id > cursor)
                            .order_by(Event.id)
                            .limit(100)
                        )
                    ).all()
                    task = await session.get(Task, task_id)
            for event in events:
                cursor = event.id
                yield f"id: {event.id}\nevent: update\ndata: {json.dumps({'kind': event.kind, 'data': event.data}, ensure_ascii=False)}\n\n"
            if task.status in TERMINAL and len(events) < 100:
                yield "event: done\ndata: {}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
async def conversations(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(
                select(Conversation)
                .where(Conversation.user_id == admin.user_id)
                .order_by(Conversation.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [serialize(row) for row in rows]


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: str, request: Request, admin: Admin, limit: Limit = 50, offset: Offset = 0
):
    async with request.app.state.services.db.sessions() as session:
        conversation = await session.get(Conversation, conversation_id)
        if not conversation or conversation.user_id != admin.user_id:
            raise HTTPException(404, "Conversa não encontrada.")
        rows = (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [serialize(row) for row in reversed(rows)]


class ProviderInput(ProviderConfig):
    api_key: str | None = Field(default=None, min_length=8, max_length=256)
    clear_key: bool = False


@router.get("/provider")
async def provider_settings(request: Request, admin: Admin):
    config, key = await request.app.state.services.store.provider()
    return {**config.model_dump(), "key_configured": bool(key)}


@router.put("/provider")
async def save_provider(payload: ProviderInput, request: Request, admin: Admin):
    services = request.app.state.services
    old = await services.store.get_config("provider")
    value = payload.model_dump(exclude={"api_key", "clear_key"})
    encrypted = "" if payload.clear_key else old.get("encrypted_key", "")
    if payload.api_key:
        encrypted = services.store.secrets.encrypt(payload.api_key)
    value["encrypted_key"] = encrypted
    await services.store.set_config("provider", value)
    await audit(
        services.db,
        "provider_updated",
        {"model": payload.model, "key_configured": bool(encrypted)},
        admin.user_id,
    )
    return {"ok": True}


@router.post("/provider/test")
async def test_provider(request: Request, admin: Admin):
    services = request.app.state.services
    config, key = await services.store.provider()
    config = config.model_copy(update={"max_tokens": 128})
    try:
        completion = await ProviderManager.create(config, key).complete(
            [{"role": "user", "content": "Responda apenas: conexão OK."}], []
        )
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    async with services.db.sessions() as session:
        session.add(
            Usage(
                model=config.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cached_tokens=completion.cached_tokens,
                duration_ms=completion.duration_ms,
                estimated_cost=completion.estimated_cost,
                prices_configured=config.input_price > 0 and config.output_price > 0,
            )
        )
        await session.commit()
    await audit(services.db, "provider_test", {"model": config.model}, admin.user_id)
    return {
        "ok": True,
        "response": redact(completion.message.content or ""),
        "estimated_cost": completion.estimated_cost,
    }


@router.get("/settings")
async def settings(request: Request, admin: Admin):
    services = request.app.state.services
    return {
        "runtime": (await services.store.runtime()).model_dump(),
        "allowed_roots": [str(p) for p in services.settings.allowed_roots],
        "voice": {"status": "planned", "version": "0.2"},
    }


@router.put("/settings")
async def save_settings(payload: RuntimeConfig, request: Request, admin: Admin):
    await request.app.state.services.store.set_config("runtime", payload.model_dump())
    await audit(request.app.state.services.db, "runtime_updated", payload.model_dump(), admin.user_id)
    return {"ok": True, "message": "Limites serão aplicados às novas tarefas."}


@router.get("/tools")
async def tools(request: Request, admin: Admin):
    services = request.app.state.services
    async with services.db.sessions() as session:
        disabled = set(
            (
                await session.scalars(select(ToolPermission.name).where(ToolPermission.enabled.is_(False)))
            ).all()
        )
    return [
        {**tool.metadata(), "enabled": name not in disabled} for name, tool in services.registry.tools.items()
    ]


class PermissionInput(Input):
    enabled: bool


@router.put("/tools/{name}")
async def save_permission(name: str, payload: PermissionInput, request: Request, admin: Admin):
    services = request.app.state.services
    if name not in services.registry.tools:
        raise HTTPException(404, "Ferramenta não encontrada.")
    async with services.db.sessions() as session:
        permission = await session.get(ToolPermission, name)
        if not permission:
            session.add(ToolPermission(name=name, enabled=payload.enabled))
        else:
            permission.enabled = payload.enabled
        await session.commit()
    await audit(services.db, "tool_permission", {"tool": name, "enabled": payload.enabled}, admin.user_id)
    return {"ok": True}


class WorkflowInput(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=1000)
    definition: WorkflowDefinition


@router.get("/workflows")
async def workflows(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(select(Workflow).order_by(Workflow.name).offset(offset).limit(limit))
        ).all()
        return [serialize(row) for row in rows]


@router.post("/workflows", status_code=201)
async def save_workflow(payload: WorkflowInput, request: Request, admin: Admin):
    services = request.app.state.services
    try:
        services.workflows.validate_tools(payload.definition)
    except ToolError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with services.db.sessions() as session:
        if await session.scalar(select(Workflow.id).where(Workflow.name == payload.name)):
            raise HTTPException(409, "Já existe uma rotina com este nome.")
        workflow = Workflow(
            name=payload.name, description=payload.description, definition=payload.definition.model_dump()
        )
        session.add(workflow)
        await session.commit()
    await audit(services.db, "workflow_created", {"workflow_id": workflow.id}, admin.user_id)
    return serialize(workflow)


class WorkflowRunInput(Input):
    parameters: dict = Field(default_factory=dict)


@router.post("/workflows/{workflow_id}/run", status_code=201)
async def run_workflow(workflow_id: str, payload: WorkflowRunInput, request: Request, admin: Admin):
    services = request.app.state.services
    async with services.db.sessions() as session:
        workflow = await session.get(Workflow, workflow_id)
        if not workflow:
            raise HTTPException(404, "Workflow não encontrado.")
    try:
        task = await services.tasks.create(
            admin.user_id,
            "Executar rotina: " + workflow.name,
            workflow_id=workflow_id,
            parameters=payload.parameters,
        )
        return serialize(task)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/workflow-runs")
async def workflow_runs(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(
                select(WorkflowRun)
                .join(Task)
                .where(Task.user_id == admin.user_id)
                .order_by(WorkflowRun.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [serialize(row) for row in rows]


@router.get("/devices")
async def devices(request: Request, admin: Admin):
    device = await asyncio.to_thread(local_device)
    async with request.app.state.services.db.sessions() as session:
        row = await session.get(Device, "local")
        if row:
            row.last_seen = now()
            await session.commit()
    return [
        {
            **device,
            "last_seen": now(),
            "available_tools": [
                t.name for t in request.app.state.services.registry.tools.values() if t.available
            ],
        }
    ]


@router.get("/memories")
async def memories(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(
                select(Memory)
                .where(Memory.user_id == admin.user_id)
                .order_by(Memory.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [serialize(row) for row in rows]


class MemoryInput(Input):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)

    def check_sensitive(self):
        if SECRET_PATTERN.search(self.key + "=" + self.value) or "sk-" in self.value:
            raise HTTPException(422, "Memória não aceita credenciais ou segredos.")


@router.post("/memories", status_code=201)
async def add_memory(payload: MemoryInput, request: Request, admin: Admin):
    payload.check_sensitive()
    async with request.app.state.services.db.sessions() as session:
        memory = Memory(user_id=admin.user_id, **payload.model_dump())
        session.add(memory)
        await session.commit()
    await audit(request.app.state.services.db, "memory_added", {"memory_id": memory.id}, admin.user_id)
    return serialize(memory)


@router.put("/memories/{memory_id}")
async def edit_memory(memory_id: str, payload: MemoryInput, request: Request, admin: Admin):
    payload.check_sensitive()
    async with request.app.state.services.db.sessions() as session:
        memory = await session.get(Memory, memory_id)
        if not memory or memory.user_id != admin.user_id:
            raise HTTPException(404, "Memória não encontrada.")
        memory.key, memory.value = payload.key, payload.value
        await session.commit()
    await audit(request.app.state.services.db, "memory_edited", {"memory_id": memory_id}, admin.user_id)
    return serialize(memory)


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request, admin: Admin):
    async with request.app.state.services.db.sessions() as session:
        result = await session.execute(
            delete(Memory).where(Memory.id == memory_id, Memory.user_id == admin.user_id)
        )
        if not result.rowcount:
            raise HTTPException(404, "Memória não encontrada.")
        await session.commit()
    await audit(request.app.state.services.db, "memory_deleted", {"memory_id": memory_id}, admin.user_id)
    return {"ok": True}


@router.get("/approvals")
async def approvals(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(
                select(Approval)
                .join(Task)
                .where(Task.user_id == admin.user_id)
                .order_by(Approval.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [serialize(row, {"fingerprint"}) for row in rows]


class DecisionInput(Input):
    approved: bool


@router.post("/approvals/{approval_id}")
async def decide(approval_id: str, payload: DecisionInput, request: Request, admin: Admin):
    services = request.app.state.services
    async with services.db.sessions() as session:
        approval = await session.get(Approval, approval_id, with_for_update=True)
        if not approval:
            raise HTTPException(404, "Aprovação não encontrada.")
        task = await session.get(Task, approval.task_id)
        if task.user_id != admin.user_id:
            raise HTTPException(404, "Aprovação não encontrada.")
        if (
            approval.status != "PENDING"
            or task.status != "WAITING_APPROVAL"
            or approval_id not in services.executor.approval_events
        ):
            raise HTTPException(409, "Esta aprovação não está mais pendente.")
        approval.status = "APPROVED" if payload.approved else "DENIED"
        approval.decided_at = now()
        session.add(
            Audit(
                user_id=admin.user_id,
                action="approval_decided",
                data={"approval_id": approval_id, "approved": payload.approved, "tool": approval.tool},
            )
        )
        await session.commit()
    services.executor.approval_events[approval_id].set()
    return {"ok": True}


@router.get("/audit")
async def audit_log(request: Request, admin: Admin, limit: Limit = 30, offset: Offset = 0):
    async with request.app.state.services.db.sessions() as session:
        rows = (
            await session.scalars(select(Audit).order_by(Audit.id.desc()).offset(offset).limit(limit))
        ).all()
        return [serialize(row) for row in rows]


@router.post("/sessions/revoke")
async def revoke_sessions(request: Request, admin: Admin):
    async with request.app.state.services.db.sessions() as session:
        result = await session.execute(
            delete(AdminSession).where(
                AdminSession.user_id == admin.user_id, AdminSession.token_hash != admin.token_hash
            )
        )
        await session.commit()
    await audit(request.app.state.services.db, "sessions_revoked", {"count": result.rowcount}, admin.user_id)
    return {"ok": True, "count": result.rowcount}


@router.get("/screenshots/{asset}")
async def screenshot(asset: str, request: Request, admin: Admin):
    import re

    if not re.fullmatch(r"[a-f0-9]{32}\.jpg", asset):
        raise HTTPException(404)
    async with request.app.state.services.db.sessions() as session:
        execution = await session.scalar(
            select(Execution)
            .join(Task)
            .where(Task.user_id == admin.user_id, Execution.result["asset"].astext == asset)
        )
    path = request.app.state.services.settings.data_dir / "screenshots" / asset
    if not execution or not path.is_file():
        raise HTTPException(404, "Captura indisponível ou expirada.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
