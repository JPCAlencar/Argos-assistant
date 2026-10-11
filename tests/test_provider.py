import httpx
import pytest

from app.config import ProviderConfig
from app.providers import DeepSeekProvider, ProviderError, ProviderManager

pytestmark = pytest.mark.unit


async def test_tool_call_contract_cache_thinking_and_usage():
    async def transport(request):
        import json

        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "enabled"}
        assert "reasoning_content" in payload["messages"][0]
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "preserve-on-next-turn",
                            "tool_calls": [
                                {
                                    "id": "call1",
                                    "type": "function",
                                    "function": {"name": "get_system_status", "arguments": "{}"},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 40},
            },
        )

    config = ProviderConfig(thinking=True, input_price=1, cached_input_price=0.1, output_price=2)
    provider = DeepSeekProvider(config, "test-key", httpx.MockTransport(transport))
    result = await provider.complete(
        [{"role": "assistant", "content": "", "reasoning_content": "previous"}],
        [{"type": "function", "function": {"name": "get_system_status"}}],
    )
    assert result.message.tool_calls[0].function.name == "get_system_status"
    assert result.cached_tokens == 40
    assert result.estimated_cost == pytest.approx(0.000104)


async def test_provider_http_error_redacts_secret():
    provider = DeepSeekProvider(
        ProviderConfig(),
        "test-key",
        httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "secret-key-never-log-this"})),
    )
    with pytest.raises(ProviderError, match="HTTP 401") as error:
        await provider.complete([], [])
    assert "secret-key-never-log-this" not in str(error.value)


async def test_missing_key_and_truncated_response():
    with pytest.raises(ProviderError, match="Cadastre"):
        await ProviderManager.create(ProviderConfig(), "").complete([], [])
    provider = DeepSeekProvider(
        ProviderConfig(),
        "test-key",
        httpx.MockTransport(
            lambda _: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {}}]})
        ),
    )
    with pytest.raises(ProviderError, match="interrompida"):
        await provider.complete([], [])
