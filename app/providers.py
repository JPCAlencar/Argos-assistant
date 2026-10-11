import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from app.config import ProviderConfig


class ProviderError(RuntimeError):
    pass


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


class ModelMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


@dataclass
class Completion:
    message: ModelMessage
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    duration_ms: int
    estimated_cost: float


class Provider(Protocol):
    config: ProviderConfig

    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion: ...


class DeepSeekProvider:
    def __init__(self, config: ProviderConfig, api_key: str, transport=None):
        self.config, self.api_key, self.transport = config, api_key, transport

    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        if not self.api_key:
            raise ProviderError("Cadastre sua API key na página Modelos.")
        if tools and not self.config.tool_calling:
            raise ProviderError("Tool calling desativado para o modelo.")
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "thinking": {"type": "enabled" if self.config.thinking else "disabled"},
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout, transport=self.transport, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.post(
                    self.config.base_url + "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if response.status_code != 200:
                # Nunca devolve corpos de erros que podem conter mensagens/chaves.
                raise ProviderError(
                    f"DeepSeek retornou HTTP {response.status_code}. Confira modelo, chave e saldo."
                )
            body = response.json()
            choice = body["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise ProviderError("Resposta interrompida pelo limite de tokens ou filtro do provedor.")
            message = ModelMessage.model_validate(choice["message"])
            usage = body.get("usage", {})
            inputs, outputs = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            cached = min(inputs, int(usage.get("prompt_cache_hit_tokens", 0)))
            cost = (
                (inputs - cached) * self.config.input_price
                + cached * self.config.cached_input_price
                + outputs * self.config.output_price
            ) / 1_000_000
            return Completion(
                message, inputs, outputs, cached, int((time.monotonic() - started) * 1000), cost
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("Timeout ao consultar o DeepSeek.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Não foi possível conectar ao DeepSeek.") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Resposta inválida do DeepSeek.") from exc


class ProviderManager:
    """Ponto de substituição do provedor; capacidades pertencem ao adaptador."""

    @staticmethod
    def create(config: ProviderConfig, api_key: str) -> Provider:
        if config.provider != "deepseek":
            raise ProviderError("Provedor ainda não implementado.")
        return DeepSeekProvider(config, api_key)
