from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoArguments(Arguments):
    pass


class ToolError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[dict]]
    risk: Literal["LOW", "MODERATE", "HIGH"] = "LOW"
    permission: str = "system.read"
    timeout: int = 20
    gui: bool = False
    retry_safe: bool = True
    available: bool = True
    return_type: str = "object"
    device: str = "local"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments.model_json_schema(),
            },
        }

    def metadata(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "schema": self.arguments.model_json_schema(),
            "risk": self.risk,
            "permission": self.permission,
            "timeout": self.timeout,
            "gui": self.gui,
            "available": self.available,
            "return_type": self.return_type,
            "device": self.device,
            "retry_safe": self.retry_safe,
        }


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"Ferramenta já registrada: {tool.name}")
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self.tools or not self.tools[name].available:
            raise ToolError("Ferramenta inexistente ou indisponível.")
        return self.tools[name]
