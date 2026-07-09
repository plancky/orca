"""Hermetic QA for the Modal/Qwen adapter + provider factory.

No real Modal call: `openai.AsyncOpenAI` is replaced by a recording fake, so
this proves the adapter builds the SDK client with the Modal auth headers and
maps our `chat(...)` contract (model, JSON mode, reasoning toggle) onto the
OpenAI Chat Completions call entirely offline. It mirrors the Gemini client's
`test_llm_qa.py`, which mocks the httpx transport for the same guarantees.
"""

from types import SimpleNamespace

import openai
import pytest

from backend.config import settings
from backend.llm.base import LLMProvider
from backend.llm.client import LLMClient, _build_llm_client
from backend.llm.modal_qwen import ModalQwenAdapter


class _RecordingAsyncOpenAI:
    init_kwargs: dict = {}
    create_kwargs: dict = {}
    reply: str = '{"ok": true}'

    def __init__(self, **kwargs):
        _RecordingAsyncOpenAI.init_kwargs = kwargs
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        _RecordingAsyncOpenAI.create_kwargs = kwargs
        message = SimpleNamespace(content=_RecordingAsyncOpenAI.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    async def close(self):
        pass


@pytest.fixture
def modal_env(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://app.modal.direct/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "unused")
    monkeypatch.setattr(settings, "LLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 2048)
    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "none")
    monkeypatch.setattr(settings, "MODAL_PROXY_TOKEN_ID", "tid")
    monkeypatch.setattr(settings, "MODAL_PROXY_TOKEN_SECRET", "tsecret")
    monkeypatch.setattr(openai, "AsyncOpenAI", _RecordingAsyncOpenAI)
    _RecordingAsyncOpenAI.init_kwargs = {}
    _RecordingAsyncOpenAI.create_kwargs = {}


# --- HAPPY -----------------------------------------------------------------


async def test_adapter_builds_client_with_modal_auth_headers(modal_env):
    adapter = ModalQwenAdapter()
    out = await adapter.chat(
        [{"role": "user", "content": "hi"}], response_format="json_object"
    )

    assert out == '{"ok": true}'
    init = _RecordingAsyncOpenAI.init_kwargs
    assert init["base_url"] == "https://app.modal.direct/v1"
    assert init["api_key"] == "unused"
    assert init["default_headers"] == {"Modal-Key": "tid", "Modal-Secret": "tsecret"}


async def test_chat_maps_contract_onto_openai_call(modal_env):
    adapter = ModalQwenAdapter()
    await adapter.chat(
        [{"role": "user", "content": "hi"}],
        response_format="json_object",
        temperature=0.3,
    )

    kw = _RecordingAsyncOpenAI.create_kwargs
    assert kw["model"] == "Qwen/Qwen3.6-35B-A3B"
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert kw["temperature"] == 0.3
    assert kw["max_tokens"] == 2048
    assert kw["response_format"] == {"type": "json_object"}
    assert kw["extra_body"] == {"reasoning_effort": "none"}


async def test_chat_omits_response_format_when_plain(modal_env):
    adapter = ModalQwenAdapter()
    await adapter.chat([{"role": "user", "content": "hi"}])

    assert "response_format" not in _RecordingAsyncOpenAI.create_kwargs


async def test_reasoning_effort_omitted_when_blank(modal_env, monkeypatch):
    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "")
    adapter = ModalQwenAdapter()
    await adapter.chat([{"role": "user", "content": "hi"}])

    assert "extra_body" not in _RecordingAsyncOpenAI.create_kwargs


async def test_missing_base_url_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    adapter = ModalQwenAdapter()
    with pytest.raises(RuntimeError, match="LLM_BASE_URL required"):
        await adapter.chat([{"role": "user", "content": "hi"}])


# --- FACTORY ----------------------------------------------------------------


def test_factory_routes_to_modal_when_base_url_set(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://app.modal.direct/v1")
    client = _build_llm_client()
    assert isinstance(client, ModalQwenAdapter)
    assert isinstance(client, LLMProvider)


def test_factory_falls_back_to_gemini_without_base_url(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    assert isinstance(_build_llm_client(), LLMClient)


def test_factory_honors_explicit_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "modal_qwen")
    assert isinstance(_build_llm_client(), ModalQwenAdapter)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    assert isinstance(_build_llm_client(), LLMClient)


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "bogus")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        _build_llm_client()
