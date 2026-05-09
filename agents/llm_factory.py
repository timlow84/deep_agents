"""Shared LLM factory — patches langchain-anthropic's httpx clients to use the OS
certificate store. Required on corporate networks where an SSL-inspection proxy
presents a corporate CA that is not in certifi's bundle but IS in the Windows store.
"""

import logging
import os
import ssl

import httpx
import truststore
from langchain_anthropic import ChatAnthropic
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

log = logging.getLogger(__name__)

# Patch the global SSL context so all HTTP libraries (requests, httpx, urllib3)
# use the OS trust store. This covers ChatNVIDIA (requests) and any other client
# that doesn't accept an explicit ssl_context argument.
truststore.inject_into_ssl()

# Explicit truststore-aware httpx clients for ChatOpenAI, which creates its own
# httpx clients internally and ignores the global SSL patch.
_ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_sync_http_client = httpx.Client(verify=_ssl_ctx)
_async_http_client = httpx.AsyncClient(verify=_ssl_ctx)


def _patch_langchain_anthropic_ssl() -> None:
    """Replace langchain-anthropic's cached httpx client factories with truststore-aware ones.

    langchain-anthropic creates httpx clients without a custom verify= argument, so they
    use certifi's bundle. On corporate networks we need the OS trust store instead.
    We patch both the _client_utils module (source of truth) and the chat_models module
    (which imported the functions directly at import time).
    """
    import langchain_anthropic._client_utils as _cu  # type: ignore[import-untyped]
    import langchain_anthropic.chat_models as _cm

    ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _NOT_GIVEN = _cu._NOT_GIVEN  # type: ignore[attr-defined]
    _SyncWrapper = _cu._SyncHttpxClientWrapper  # type: ignore[attr-defined]
    _AsyncWrapper = _cu._AsyncHttpxClientWrapper  # type: ignore[attr-defined]

    def _sync_client(*, base_url, timeout=_NOT_GIVEN, anthropic_proxy=None):
        kw: dict = {
            "base_url": base_url
            or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://api.anthropic.com",
            "verify": ssl_ctx,
        }
        if timeout is not _NOT_GIVEN:
            kw["timeout"] = timeout
        if anthropic_proxy:
            kw["proxy"] = anthropic_proxy
        return _SyncWrapper(**kw)

    def _async_client(*, base_url, timeout=_NOT_GIVEN, anthropic_proxy=None):
        kw: dict = {
            "base_url": base_url
            or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://api.anthropic.com",
            "verify": ssl_ctx,
        }
        if timeout is not _NOT_GIVEN:
            kw["timeout"] = timeout
        if anthropic_proxy:
            kw["proxy"] = anthropic_proxy
        return _AsyncWrapper(**kw)

    # Patch both the originating module and the chat_models direct import
    _cu._get_default_httpx_client = _sync_client  # type: ignore[attr-defined]
    _cu._get_default_async_httpx_client = _async_client  # type: ignore[attr-defined]
    _cm._get_default_httpx_client = _sync_client  # type: ignore[attr-defined]
    _cm._get_default_async_httpx_client = _async_client  # type: ignore[attr-defined]


_patch_langchain_anthropic_ssl()


def get_llm(model_name: str | None = None) -> ChatAnthropic | ChatOpenAI | ChatNVIDIA | ChatOllama:
    """Return the LLM selected by LLM_PROVIDER_SELECTOR env var (ANTHROPIC, PORTKEY, NVIDIA, OLLAMA)."""
    provider = os.getenv("LLM_PROVIDER_SELECTOR", "ANTHROPIC").upper()
    log.info("LLM provider selected: %s", provider)
    if provider == "PORTKEY":
        return get_openai_llm(model_name)
    if provider == "NVIDIA":
        return get_nvidia_llm(model_name)
    if provider == "OLLAMA":
        return get_local_ollama(model_name)
    return get_anthropic_llm(model_name)


def get_anthropic_llm(model_name: str | None = None) -> ChatAnthropic:
    if model_name is None:
        model_name = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    return ChatAnthropic(model=model_name)  # type: ignore[call-arg]


def get_openai_llm(model_name: str | None = None) -> ChatOpenAI:
    if model_name is None:
        model_name = os.getenv("PORTKEY_MODEL", "llama3.2:latest")
    return ChatOpenAI(
        model=model_name,
        base_url=os.getenv("PORTKEY_BASE_URL"),  # type: ignore[arg-type]
        api_key=os.getenv("PORTKEY_API_KEY"),  # type: ignore[arg-type]
        temperature=0,
        http_client=_sync_http_client,
        http_async_client=_async_http_client,
    )


def get_nvidia_llm(model_name: str | None = None) -> ChatNVIDIA:
    """Return a ChatNVIDIA client using langchain-nvidia-ai-endpoints.

    ChatNVIDIA reads NVIDIA_API_KEY from the environment automatically.
    The truststore-aware httpx clients handle corporate SSL proxies.
    """
    if model_name is None:
        model_name = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
    return ChatNVIDIA(  # type: ignore[call-arg]
        model=model_name,
        base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.getenv("NVIDIA_API_KEY"),  # type: ignore[arg-type]
        temperature=0,
    )


def get_local_ollama(model_name: str | None = None) -> ChatOllama:
    """Return a ChatOllama client pointed at a local Ollama instance.

    ChatOllama uses Ollama's native API rather than the OpenAI-compatible shim,
    which ensures tool/function-call messages are formatted correctly for local models.
    OLLAMA_BASE_URL and OLLAMA_MODEL are read from the environment.
    """
    if model_name is None:
        model_name = os.getenv("OLLAMA_MODEL", "mistral-nemo:latest")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    return ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=0,
    )
