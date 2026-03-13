"""
LLM 프로바이더 추상 클래스 + OpenAI / Ollama 구현체
어떤 LLM을 쓰든 같은 인터페이스로 호출
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from openai import AsyncOpenAI


class LlmProvider(ABC):
    """LLM 프로바이더 추상 인터페이스"""

    @abstractmethod
    async def complete(self, messages: list[dict], **kwargs) -> str:
        """동기 응답 — 전체 텍스트 반환"""

    @abstractmethod
    async def stream(self, messages: list[dict], **kwargs) -> AsyncGenerator[str, None]:
        """스트리밍 응답 — 토큰 단위 yield"""


class OpenAiProvider(LlmProvider):
    """OpenAI GPT 프로바이더"""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 temperature: float = 0.2, max_tokens: int = 4096):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def complete(self, messages: list[dict], **kwargs) -> str:
        resp = await self._client.chat.completions.create(
            model=kwargs.get("model", self._model),
            messages=messages,
            temperature=kwargs.get("temperature", self._temperature),
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
        )
        return resp.choices[0].message.content or ""

    async def stream(self, messages: list[dict], **kwargs) -> AsyncGenerator[str, None]:
        resp = await self._client.chat.completions.create(
            model=kwargs.get("model", self._model),
            messages=messages,
            temperature=kwargs.get("temperature", self._temperature),
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
            stream=True,
        )
        async for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


class OllamaProvider(LlmProvider):
    """Ollama 로컬 LLM 프로바이더"""

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5-coder:7b",
                 temperature: float = 0.2, max_tokens: int = 4096):
        import httpx
        self._base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=600.0)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def complete(self, messages: list[dict], **kwargs) -> str:
        resp = await self._client.post("/api/chat", json={
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", self._temperature),
                "num_predict": kwargs.get("max_tokens", self._max_tokens),
                "num_ctx": 32768,
            },
        })
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message")
        if isinstance(msg, dict):
            return msg.get("content", "") or ""
        return ""

    async def list_models(self) -> list[dict]:
        """Ollama에 설치된 모델 목록 조회"""
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [
                {
                    "id": m["name"],
                    "name": m["name"],
                    "size_gb": round(m.get("size", 0) / 1e9, 1),
                    "type": "local",
                }
                for m in models
            ]
        except Exception:
            return []

    async def stream(self, messages: list[dict], **kwargs) -> AsyncGenerator[str, None]:
        import httpx
        import json
        import logging
        _log = logging.getLogger("nori-server")

        total_chars = sum(len(m.get("content", "")) for m in messages)
        _log.info("Ollama stream 요청: model=%s, messages=%d개, 총 %d자",
                  kwargs.get("model", self._model), len(messages), total_chars)

        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=600.0) as client:
                async with client.stream("POST", "/api/chat", json={
                    "model": kwargs.get("model", self._model),
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": kwargs.get("temperature", self._temperature),
                        "num_predict": kwargs.get("max_tokens", self._max_tokens),
                        "num_ctx": 32768,
                    },
                }) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.strip():
                            data = json.loads(line)
                            msg = data.get("message")
                            content = (msg.get("content", "") if isinstance(msg, dict) else "") or ""
                            if content:
                                yield content
        except httpx.ReadTimeout:
            _log.error("Ollama 읽기 타임아웃 (600s) — 컨텍스트 %d자", total_chars)
            raise
        except httpx.HTTPStatusError as e:
            _log.error("Ollama HTTP 에러: %s — 컨텍스트 %d자", e.response.status_code, total_chars)
            raise
        except Exception as e:
            _log.error("Ollama stream 예외: %s — 컨텍스트 %d자", e, total_chars)
            raise
