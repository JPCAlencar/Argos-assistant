import asyncio
import copy
from typing import Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update

from app.database import Workflow, WorkflowRun, identifier
from app.tools.registry import Arguments, Tool, ToolError


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(pattern=r"^[a-zA-Z0-9_]{1,100}$")
    arguments: dict = Field(default_factory=dict)
    retries: int = Field(default=0, ge=0, le=2)
    timeout: int = Field(default=20, ge=1, le=120)
    success: dict = Field(default_factory=lambda: {"confirmed": True})


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    steps: list[WorkflowStep] = Field(min_length=1, max_length=30)
    timeout: int = Field(default=120, ge=1, le=1800)
    risk_policy: Literal["per_step"] = "per_step"
    recovery: Literal["stop", "agent_for_low_risk"] = "stop"

    @field_validator("parameters")
    @classmethod
    def valid_schema(cls, value):
        def check_refs(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in {"$ref", "$dynamicRef"} and (
                        not isinstance(child, str) or not child.startswith("#")
                    ):
                        raise ValueError("Referências externas não são permitidas em schemas.")
                    check_refs(child)
            elif isinstance(item, list):
                for child in item:
                    check_refs(child)

        check_refs(value)
        jsonschema.Draft202012Validator.check_schema(value)
        if value.get("type") != "object":
            raise ValueError("Parâmetros devem usar schema de objeto.")
        return value


class RunWorkflowArguments(Arguments):
    workflow_id: str = Field(min_length=1, max_length=36)
    parameters: dict = Field(default_factory=dict)


class WorkflowFailure(ToolError):
    def __init__(self, message: str, recoverable: bool = False):
        super().__init__(message)
        self.recoverable = recoverable


def substitute(value, parameters: dict):
    if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
        name = value[2:-2].strip()
        if name not in parameters:
            raise ToolError(f"Parâmetro de workflow ausente: {name}")
        return copy.deepcopy(parameters[name])
    if isinstance(value, dict):
        return {key: substitute(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, parameters) for item in value]
    return value


class WorkflowEngine:
    def __init__(self, services):
        self.services = services

    def validate_tools(self, definition: WorkflowDefinition):
        for step in definition.steps:
            if step.tool == "run_workflow":
                raise ToolError("Workflows aninhados não são permitidos na v0.1.")
            tool = self.services.registry.get(step.tool)
            if step.retries and not tool.retry_safe:
                raise ToolError("Retry permitido somente para ferramentas sem efeitos colaterais.")

    async def run(self, context, workflow_id: str, parameters: dict) -> dict:
        async with self.services.db.sessions() as session:
            workflow = await session.get(Workflow, workflow_id)
            if not workflow:
                raise ToolError("Workflow não encontrado.")
            definition = WorkflowDefinition.model_validate(workflow.definition)
            name = workflow.name
        self.validate_tools(definition)
        jsonschema.validate(parameters, definition.parameters)
        run_id = identifier()
        async with self.services.db.sessions() as session:
            session.add(
                WorkflowRun(
                    id=run_id, workflow_id=workflow_id, task_id=context.task_id, version=definition.version
                )
            )
            await session.commit()
        step_index = 0
        result = None
        try:
            async with asyncio.timeout(definition.timeout):
                for step_index, step in enumerate(definition.steps):
                    await self.services.store.emit(
                        context.task_id,
                        "workflow_step",
                        {"workflow": name, "step": step_index + 1, "tool": step.tool, "run_id": run_id},
                    )
                    args = substitute(step.arguments, parameters)
                    result = await self.services.executor.execute(
                        context, step.tool, args, timeout=step.timeout, retries=step.retries
                    )
                    if any(result.get(key) != value for key, value in step.success.items()):
                        raise ToolError("Critério de sucesso da etapa não confirmado.")
            status = "COMPLETED"
            return {
                "workflow": name,
                "run_id": run_id,
                "steps": len(definition.steps),
                "result": result,
                "confirmed": True,
            }
        except asyncio.CancelledError:
            status = "CANCELLED"
            raise
        except Exception as exc:
            status = "FAILED"
            from app.executor import PolicyDenied

            safe = (
                definition.recovery == "agent_for_low_risk"
                and self.services.registry.get(definition.steps[step_index].tool).risk == "LOW"
                and isinstance(exc, (ToolError, TimeoutError))
                and not isinstance(exc, PolicyDenied)
            )
            raise WorkflowFailure(f"Workflow {name}: falha na etapa {step_index + 1}: {exc}", safe) from exc
        finally:
            async with self.services.db.sessions() as session:
                await session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id)
                    .values(status=status, failed_step=step_index if status != "COMPLETED" else None)
                )
                await session.commit()

    async def summaries(self) -> list[dict]:
        async with self.services.db.sessions() as session:
            rows = list((await session.scalars(select(Workflow).limit(50))).all())
        return [
            {
                "id": w.id,
                "name": w.name,
                "description": w.description[:300],
                "parameters": w.definition.get("parameters", {}),
            }
            for w in rows
        ]

    def register(self):
        async def handler(args):
            # Contexto é obrigatório e encaminhado pelo executor, nunca pelo modelo.
            raise ToolError("Execução de workflow exige contexto de tarefa.")

        self.services.registry.register(
            Tool(
                "run_workflow",
                "Executa workflow cadastrado pelo ID, sem IA por etapa.",
                RunWorkflowArguments,
                handler,
                permission="workflows.run",
                timeout=1800,
                retry_safe=False,
            )
        )


DEFAULT_WORKFLOW = {
    "name": "Diagnóstico local",
    "description": "Consulta recursos e processos do computador sem usar IA.",
    "definition": WorkflowDefinition(
        steps=[WorkflowStep(tool="get_system_status"), WorkflowStep(tool="list_processes")]
    ).model_dump(),
}
